from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from PIL import Image
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
import torchvision.transforms as T
import torchvision.models as models
from sklearn.metrics import accuracy_score, classification_report, precision_recall_fscore_support

# Fixed paths
MODEL_PATH = Path(r"E:\肺部ct影像\code\best_resnet_nodule.pt")
PATCH_ROOT = Path(r"E:\肺部ct影像\nodule_patches")
LABEL_CSV = Path(r"E:\肺部ct影像\patient_summaries\nodule_labels.csv")
OUTPUT_CSV = Path(r"E:\肺部ct影像\patient_summaries\nodule_predictions.csv")

# Decision threshold (tuned)
BEST_THRESHOLD = 0.37  # tuned from eval_precise_nodule_model.py

BATCH_SIZE = 64
NUM_WORKERS = 0


def parse_patient_nodule(fname: str) -> Optional[Tuple[str, str]]:
    stem = Path(fname).stem
    parts = stem.split("_")
    if len(parts) < 3:
        return None
    patient_id = parts[1]
    nodule_id = parts[2]
    if not patient_id or not nodule_id:
        return None
    return patient_id, nodule_id


def load_labels(csv_path: Optional[Path]) -> Optional[pd.DataFrame]:
    if not csv_path:
        return None
    if not csv_path.exists():
        print(f"[WARN] Label CSV not found: {csv_path}. Continuing without true labels.")
        return None
    df = pd.read_csv(csv_path)
    required = {"patient_id", "nodule_id", "is_malignant"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Label CSV missing columns: {missing}")
    df["patient_id"] = df["patient_id"].astype(str)
    df["nodule_id"] = df["nodule_id"].astype(str)
    df["true_label"] = df["is_malignant"].astype(int)
    return df


class NodulePatchDataset(Dataset):
    def __init__(self, patch_root: Path, label_df: Optional[pd.DataFrame], transform=None):
        super().__init__()
        self.transform = transform
        self.has_labels = label_df is not None
        label_map = {}
        if label_df is not None:
            label_map = {
                (row.patient_id, row.nodule_id): row.true_label for row in label_df.itertuples()
            }
        self.samples: List[Tuple[Path, str, str, int]] = []
        skipped = 0
        for png_path in patch_root.rglob("*.png"):
            parsed = parse_patient_nodule(png_path.name)
            if parsed is None:
                print(f"[WARN] Cannot parse {png_path.name}, skipped.")
                skipped += 1
                continue
            key = (parsed[0], parsed[1])
            if self.has_labels:
                label = label_map.get(key)
                if label is None:
                    print(f"[WARN] No label for {key}, skipped {png_path.name}")
                    skipped += 1
                    continue
                self.samples.append((png_path, parsed[0], parsed[1], int(label)))
            else:
                self.samples.append((png_path, parsed[0], parsed[1], 0))
        print(f"[INFO] Loaded {len(self.samples)} patches, skipped {skipped}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        path, pid, nid, label = self.samples[idx]
        img = Image.open(path).convert("L").convert("RGB")
        if self.transform:
            img = self.transform(img)
        return img, pid, nid, label


def build_model(device: torch.device):
    model = models.resnet18(weights=None)
    num_ftrs = model.fc.in_features
    model.fc = nn.Linear(num_ftrs, 2)
    model = model.to(device)
    return model


def aggregate_nodules(records: List[dict]) -> List[dict]:
    agg = defaultdict(lambda: {"probs": [], "true": None, "count": 0})
    for rec in records:
        key = (rec["patient_id"], rec["nodule_id"])
        agg[key]["probs"].append(rec["prob_malignant"])
        agg[key]["count"] += 1
        agg[key]["true"] = rec["true_label"]

    rows = []
    for (pid, nid), data in agg.items():
        probs = data["probs"]
        avg_prob = float(np.mean(probs)) if probs else 0.0
        max_prob = float(np.max(probs)) if probs else 0.0
        pred_label = 1 if avg_prob >= BEST_THRESHOLD else 0
        rows.append(
            {
                "patient_id": pid,
                "nodule_id": nid,
                "num_patches": data["count"],
                "avg_prob_malignant": avg_prob,
                "max_prob_malignant": max_prob,
                "predicted_label": pred_label,
                "true_label": data["true"],
            }
        )
    return rows


def run_export(
    model_path: Path,
    patch_root: Path,
    label_csv: Optional[Path],
    output_csv: Path,
) -> pd.DataFrame:
    if not model_path.exists():
        raise FileNotFoundError(f"Model checkpoint not found: {model_path}")
    if not patch_root.exists():
        raise FileNotFoundError(f"Patch root not found: {patch_root}")

    labels_df = load_labels(label_csv)

    transform = T.Compose(
        [
            T.Resize((64, 64)),
            T.ToTensor(),
            T.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ]
    )

    dataset = NodulePatchDataset(patch_root, labels_df, transform=transform)
    loader = DataLoader(
        dataset,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=NUM_WORKERS,
        pin_memory=torch.cuda.is_available(),
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model(device)
    state = torch.load(model_path, map_location=device)
    model.load_state_dict(state)
    model.eval()
    print(f"[INFO] Loaded model on device {device}")
    print(f"[INFO] Using decision threshold = {BEST_THRESHOLD:.2f}")

    patch_records = []
    softmax = nn.Softmax(dim=1)
    with torch.no_grad():
        for imgs, pids, nids, labels in loader:
            imgs = imgs.to(device, non_blocking=True)
            logits = model(imgs)
            probs = softmax(logits)[:, 1].cpu().numpy()
            for pid, nid, prob, true_lbl in zip(pids, nids, probs, labels):
                patch_records.append(
                    {
                        "patient_id": pid,
                        "nodule_id": nid,
                        "prob_malignant": float(prob),
                        "true_label": int(true_lbl) if dataset.has_labels else None,
                    }
                )

    nodule_rows = aggregate_nodules(patch_records)
    df = pd.DataFrame(
        nodule_rows,
        columns=[
            "patient_id",
            "nodule_id",
            "num_patches",
            "avg_prob_malignant",
            "max_prob_malignant",
            "predicted_label",
            "true_label",
        ],
    )

    # Metrics
    if df.empty:
        print("[WARN] No nodules aggregated; metrics skipped.")
    elif df["true_label"].notna().any():
        y_true = df["true_label"].to_numpy()
        y_pred = df["predicted_label"].to_numpy()
        acc = accuracy_score(y_true, y_pred)
        prec, rec, f1, _ = precision_recall_fscore_support(y_true, y_pred, average="binary", zero_division=0)
        print(f"[INFO] Nodule-level metrics -> Acc: {acc:.4f}, Precision: {prec:.4f}, Recall: {rec:.4f}, F1: {f1:.4f}")
        print(classification_report(y_true, y_pred, target_names=["benign", "malignant"], digits=4))
    else:
        print("[INFO] No true labels provided; metrics skipped.")

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    print(f"[INFO] Saved predictions to {output_csv}")
    print(f"[INFO] Nodules evaluated: {len(df)}")
    return df


def main():
    df = run_export(MODEL_PATH, PATCH_ROOT, LABEL_CSV, OUTPUT_CSV)
    if df.empty:
        print("[WARN] No predictions generated.")


if __name__ == "__main__":
    main()
