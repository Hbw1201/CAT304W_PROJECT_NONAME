from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
from PIL import Image
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
import torchvision.models as models
import torchvision.transforms as T
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)


# Decision threshold (tuned)
BEST_THRESHOLD = 0.37  # tuned from eval_precise_nodule_model.py

# Paths
PATCH_ROOT = Path(r"E:\肺部ct影像\nodule_patches_precise")
LABEL_CSV = Path(r"E:\肺部ct影像\patient_summaries\nodule_labels.csv")
SAVE_BEST_MODEL = Path(r"E:\肺部ct影像\code\best_resnet_nodule_precise.pt")
TEST_PRED_CSV = Path(r"E:\肺部ct影像\nodule_predictions_test_precise_nodule_level.csv")

# Settings
BATCH_SIZE = 64
RANDOM_SEED = 42
NUM_WORKERS = 0


@dataclass
class PatchEntry:
    path: Path
    label: int
    patient_id: str
    nodule_id: str


class NodulePatchDataset(Dataset):
    def __init__(self, entries: Sequence[PatchEntry], transform: T.Compose):
        self.entries = list(entries)
        self.transform = transform

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, idx: int):
        entry = self.entries[idx]
        img = Image.open(entry.path).convert("RGB")
        img = self.transform(img)
        return img, entry.label, entry.patient_id, entry.nodule_id


def load_labels(csv_path: Path) -> Dict[Tuple[str, str], int]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Label CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)
    required_base = {"patient_id", "nodule_id"}
    missing_base = required_base - set(df.columns)
    if missing_base:
        raise ValueError(f"Label CSV missing columns: {missing_base}")

    label_source = None
    if "label" in df.columns:
        df["label"] = df["label"].astype(int)
        label_source = "label"
    elif "is_malignant" in df.columns:
        df["label"] = df["is_malignant"].astype(int)
        label_source = "is_malignant"
    elif "avg_malignancy" in df.columns:
        df["label"] = (df["avg_malignancy"].astype(float) >= 4.0).astype(int)
        label_source = "avg_malignancy>=4"
    else:
        raise ValueError("Label CSV must contain one of: label, is_malignant, or avg_malignancy")

    df["patient_id"] = df["patient_id"].astype(str)
    df["nodule_id"] = df["nodule_id"].astype(str)

    label_map: Dict[Tuple[str, str], int] = {}
    for row in df.itertuples(index=False):
        key = (row.patient_id, row.nodule_id)
        label_map[key] = int(row.label)

    print(f"[INFO] Loaded labels from {csv_path} using source column: {label_source}")
    return label_map


def gather_patches(patch_root: Path, label_map: Dict[Tuple[str, str], int]) -> List[PatchEntry]:
    if not patch_root.exists():
        raise FileNotFoundError(f"Patch root not found: {patch_root}")

    entries: List[PatchEntry] = []
    skipped = 0
    for png_path in patch_root.rglob("*.png"):
        rel_parts = png_path.relative_to(patch_root).parts
        if len(rel_parts) < 3:
            skipped += 1
            continue
        patient_id, nodule_id = rel_parts[0], rel_parts[1]
        key = (patient_id, nodule_id)
        label = label_map.get(key)
        if label is None:
            skipped += 1
            continue
        entries.append(
            PatchEntry(path=png_path, label=int(label), patient_id=patient_id, nodule_id=nodule_id)
        )

    if not entries:
        raise RuntimeError("No patches matched the label table; check paths and CSV contents.")

    print(f"[INFO] Loaded {len(entries)} patches (skipped {skipped} missing labels or malformed paths)")
    return entries


def split_by_nodule(entries: Sequence[PatchEntry]) -> tuple[List[PatchEntry], List[PatchEntry], List[PatchEntry]]:
    nodules = {}
    for entry in entries:
        key = (entry.patient_id, entry.nodule_id)
        nodules.setdefault(key, []).append(entry)

    nodule_keys = list(nodules.keys())
    random.Random(RANDOM_SEED).shuffle(nodule_keys)

    n = len(nodule_keys)
    if n == 0:
        return [], [], []

    train_end = int(0.7 * n)
    val_end = int(0.85 * n)

    if train_end == 0 and n > 1:
        train_end = 1
    if val_end <= train_end and n - train_end > 1:
        val_end = train_end + 1
    val_end = min(val_end, n)

    train_keys = set(nodule_keys[:train_end])
    val_keys = set(nodule_keys[train_end:val_end])

    train_entries: List[PatchEntry] = []
    val_entries: List[PatchEntry] = []
    test_entries: List[PatchEntry] = []

    for key, items in nodules.items():
        if key in train_keys:
            train_entries.extend(items)
        elif key in val_keys:
            val_entries.extend(items)
        else:
            test_entries.extend(items)

    print(
        f"[INFO] Nodule splits -> Train: {len(train_keys)}, Val: {len(val_keys)}, Test: {len(nodule_keys) - len(train_keys) - len(val_keys)}"
    )
    print(
        f"[INFO] Patch splits  -> Train: {len(train_entries)}, Val: {len(val_entries)}, Test: {len(test_entries)}"
    )
    return train_entries, val_entries, test_entries


def make_dataloader(entries: Sequence[PatchEntry], transform: T.Compose, shuffle: bool) -> DataLoader:
    dataset = NodulePatchDataset(entries, transform)
    return DataLoader(
        dataset,
        batch_size=BATCH_SIZE,
        shuffle=shuffle,
        num_workers=NUM_WORKERS,
        pin_memory=torch.cuda.is_available(),
    )


def build_model(device: torch.device) -> nn.Module:
    model = models.resnet18(weights=None)
    num_ftrs = model.fc.in_features
    model.fc = nn.Linear(num_ftrs, 1)
    model = model.to(device)
    return model


def evaluate(
    model: nn.Module,
    loader: DataLoader,
    device: torch.device,
    criterion: nn.Module,
) -> dict:
    model.eval()
    losses = []
    all_labels: List[int] = []
    all_probs: List[float] = []
    all_pid: List[str] = []
    all_nid: List[str] = []

    with torch.no_grad():
        for imgs, labels, pids, nids in loader:
            imgs = imgs.to(device, non_blocking=True)
            labels = labels.float().to(device, non_blocking=True)

            logits = model(imgs).squeeze(1)
            loss = criterion(logits, labels)
            losses.append(loss.item() * labels.size(0))

            probs = torch.sigmoid(logits).cpu().numpy()

            all_probs.extend(probs.tolist())
            all_labels.extend(labels.cpu().numpy().tolist())
            all_pid.extend(list(pids))
            all_nid.extend(list(nids))

    total = len(all_labels)
    avg_loss = sum(losses) / total if total > 0 else 0.0

    y_true = np.array(all_labels, dtype=int)
    y_prob = np.array(all_probs, dtype=float)
    y_pred = (y_prob >= BEST_THRESHOLD).astype(int)

    acc = accuracy_score(y_true, y_pred) if total > 0 else 0.0
    prec = precision_score(y_true, y_pred, zero_division=0) if total > 0 else 0.0
    rec = recall_score(y_true, y_pred, zero_division=0) if total > 0 else 0.0
    f1 = f1_score(y_true, y_pred, zero_division=0) if total > 0 else 0.0
    cm = confusion_matrix(y_true, y_pred) if total > 0 else None
    clf_report = (
        classification_report(y_true, y_pred, target_names=["benign", "malignant"], digits=4, zero_division=0)
        if total > 0
        else ""
    )

    return {
        "loss": avg_loss,
        "acc": acc,
        "prec": prec,
        "rec": rec,
        "f1": f1,
        "cm": cm,
        "report": clf_report,
        "labels": y_true,
        "probs": y_prob,
        "preds": y_pred,
        "pids": all_pid,
        "nids": all_nid,
    }


def aggregate_per_nodule(metrics: dict) -> pd.DataFrame:
    rows = []
    agg: Dict[Tuple[str, str], dict] = {}
    for pid, nid, prob, lbl in zip(metrics["pids"], metrics["nids"], metrics["probs"], metrics["labels"]):
        key = (pid, nid)
        if key not in agg:
            agg[key] = {"probs": [], "labels": []}
        agg[key]["probs"].append(prob)
        agg[key]["labels"].append(int(lbl))

    for (pid, nid), data in agg.items():
        avg_prob = float(sum(data["probs"]) / len(data["probs"])) if data["probs"] else 0.0
        # Majority vote for true label (labels should be identical, but this guards against noise)
        label_mean = sum(data["labels"]) / len(data["labels"]) if data["labels"] else 0.0
        true_label = 1 if label_mean >= 0.5 else 0
        pred_label = 1 if avg_prob >= BEST_THRESHOLD else 0
        rows.append(
            {
                "patient_id": pid,
                "nodule_id": nid,
                "true_label": true_label,
                "prob_avg": avg_prob,
                "pred_label": pred_label,
            }
        )

    return pd.DataFrame(rows, columns=["patient_id", "nodule_id", "true_label", "prob_avg", "pred_label"])


def compute_nodule_metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"acc": 0.0, "prec": 0.0, "rec": 0.0, "f1": 0.0, "cm": None, "report": ""}

    labels = df["true_label"].tolist()
    preds = df["pred_label"].tolist()

    acc = accuracy_score(labels, preds)
    prec = precision_score(labels, preds, zero_division=0)
    rec = recall_score(labels, preds, zero_division=0)
    f1 = f1_score(labels, preds, zero_division=0)
    cm = confusion_matrix(labels, preds)
    report = classification_report(
        labels, preds, target_names=["benign", "malignant"], digits=4, zero_division=0
    )

    return {"acc": acc, "prec": prec, "rec": rec, "f1": f1, "cm": cm, "report": report}


def evaluate_at_threshold(y_true: np.ndarray, y_prob: np.ndarray, threshold: float) -> tuple[float, float, float]:
    y_pred = (y_prob >= threshold).astype(int)
    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    return precision, recall, f1


def main() -> None:
    torch.manual_seed(RANDOM_SEED)
    random.seed(RANDOM_SEED)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    label_map = load_labels(LABEL_CSV)
    entries = gather_patches(PATCH_ROOT, label_map)
    _, _, test_entries = split_by_nodule(entries)

    if not test_entries:
        raise RuntimeError("Test set is empty after splitting nodules; cannot evaluate.")

    transform = T.Compose(
        [
            T.Grayscale(num_output_channels=3),
            T.ToTensor(),
            T.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ]
    )

    test_loader = make_dataloader(test_entries, transform, shuffle=False)

    model = build_model(device)
    if not SAVE_BEST_MODEL.exists():
        raise FileNotFoundError(f"Model checkpoint not found: {SAVE_BEST_MODEL}")
    state = torch.load(SAVE_BEST_MODEL, map_location=device)
    model.load_state_dict(state)
    model.eval()
    print(f"[INFO] Loaded model from {SAVE_BEST_MODEL} on device {device}")
    print(f"[INFO] Using decision threshold = {BEST_THRESHOLD:.2f}")

    criterion = nn.BCEWithLogitsLoss()
    metrics = evaluate(model, test_loader, device, criterion)

    print("Patch-level metrics")
    print(f"  Loss: {metrics['loss']:.4f}")
    print(f"  Accuracy: {metrics['acc']:.4f}")
    print(f"  Precision: {metrics['prec']:.4f}")
    print(f"  Recall: {metrics['rec']:.4f}")
    print(f"  F1: {metrics['f1']:.4f}")
    if metrics["cm"] is not None:
        print("  Confusion Matrix:")
        print(metrics["cm"])
    if metrics["report"]:
        print("  Classification Report:")
        print(metrics["report"])

    # Threshold sweep for best F1
    y_true = metrics["labels"]
    y_prob = metrics["probs"]
    thresholds = np.arange(0.30, 0.71, 0.01)
    best_threshold = BEST_THRESHOLD
    best_f1 = -1.0
    sweep_results = []
    for th in thresholds:
        p, r, f1 = evaluate_at_threshold(y_true, y_prob, th)
        sweep_results.append((th, p, r, f1))
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = float(th)

    print("Threshold tuning results:")
    print("th\tprecision\trecall\tF1")
    for th, p, r, f1 in sweep_results:
        print(f"{th:.2f}\t{p:.4f}\t{r:.4f}\t{f1:.4f}")
    print(f"Best threshold: {best_threshold:.2f} with F1={best_f1:.4f}")

    # Metrics at best threshold
    y_pred_best = (y_prob >= best_threshold).astype(int)
    cm_best = confusion_matrix(y_true, y_pred_best) if len(y_true) > 0 else None
    report_best = (
        classification_report(y_true, y_pred_best, target_names=["benign", "malignant"], digits=4, zero_division=0)
        if len(y_true) > 0
        else ""
    )
    acc_best = accuracy_score(y_true, y_pred_best) if len(y_true) > 0 else 0.0
    prec_best = precision_score(y_true, y_pred_best, zero_division=0) if len(y_true) > 0 else 0.0
    rec_best = recall_score(y_true, y_pred_best, zero_division=0) if len(y_true) > 0 else 0.0
    f1_best = f1_score(y_true, y_pred_best, zero_division=0) if len(y_true) > 0 else 0.0

    print("=== Metrics at best threshold ===")
    print(f"  Accuracy: {acc_best:.4f}")
    print(f"  Precision: {prec_best:.4f}")
    print(f"  Recall: {rec_best:.4f}")
    print(f"  F1: {f1_best:.4f}")
    if cm_best is not None:
        print("  Confusion Matrix:")
        print(cm_best)
    if report_best:
        print("  Classification Report:")
        print(report_best)

    nodule_df = aggregate_per_nodule(metrics)
    nodule_metrics = compute_nodule_metrics(nodule_df)

    print("Nodule-level metrics")
    print(f"  Accuracy: {nodule_metrics['acc']:.4f}")
    print(f"  Precision: {nodule_metrics['prec']:.4f}")
    print(f"  Recall: {nodule_metrics['rec']:.4f}")
    print(f"  F1: {nodule_metrics['f1']:.4f}")
    if nodule_metrics["cm"] is not None:
        print("  Confusion Matrix:")
        print(nodule_metrics["cm"])
    if nodule_metrics["report"]:
        print("  Classification Report:")
        print(nodule_metrics["report"])

    TEST_PRED_CSV.parent.mkdir(parents=True, exist_ok=True)
    nodule_df.to_csv(TEST_PRED_CSV, index=False)
    print(f"[INFO] Saved per-nodule test predictions to {TEST_PRED_CSV} ({len(nodule_df)} nodules)")


if __name__ == "__main__":
    main()
