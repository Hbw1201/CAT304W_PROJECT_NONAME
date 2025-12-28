from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import pandas as pd
from PIL import Image
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
import torchvision.models as models
import torchvision.transforms as T
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score


# Paths
PATCH_ROOT = Path(r"E:\肺部ct影像\nodule_patches_precise")
LABEL_CSV = Path(r"E:\肺部ct影像\patient_summaries\nodule_labels.csv")
SAVE_BEST_MODEL = Path(r"E:\肺部ct影像\code\best_resnet_nodule_precise.pt")

# Decision threshold (tuned)
BEST_THRESHOLD = 0.37  # tuned from eval_precise_nodule_model.py

# Hyperparameters
BATCH_SIZE = 64
EPOCHS = 20
LEARNING_RATE = 1e-4
WEIGHT_DECAY = 1e-4
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
        return img, entry.label


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
        entries.append(PatchEntry(path=png_path, label=int(label), patient_id=patient_id, nodule_id=nodule_id))

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

    train_keys = nodule_keys[:train_end]
    val_keys = nodule_keys[train_end:val_end]
    test_keys = nodule_keys[val_end:]

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
        f"[INFO] Nodule splits -> Train: {len(train_keys)}, Val: {len(val_keys)}, Test: {len(test_keys)}"
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


def compute_pos_weight(train_entries: Sequence[PatchEntry]) -> float:
    pos = sum(e.label for e in train_entries)
    neg = len(train_entries) - pos
    if pos == 0:
        return 1.0
    return float(neg / pos) if pos > 0 else 1.0


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

    with torch.no_grad():
        for imgs, labels in loader:
            imgs = imgs.to(device, non_blocking=True)
            labels = labels.float().to(device, non_blocking=True)

            logits = model(imgs).squeeze(1)
            loss = criterion(logits, labels)
            losses.append(loss.item() * labels.size(0))

            probs = torch.sigmoid(logits).cpu().numpy()
            all_probs.extend(probs.tolist())
            all_labels.extend(labels.cpu().numpy().tolist())

    total = len(all_labels)
    avg_loss = sum(losses) / total if total > 0 else 0.0
    preds = [1 if p >= BEST_THRESHOLD else 0 for p in all_probs]

    acc = accuracy_score(all_labels, preds) if total > 0 else 0.0
    prec = precision_score(all_labels, preds, zero_division=0) if total > 0 else 0.0
    rec = recall_score(all_labels, preds, zero_division=0) if total > 0 else 0.0
    f1 = f1_score(all_labels, preds, zero_division=0) if total > 0 else 0.0

    return {"loss": avg_loss, "acc": acc, "prec": prec, "rec": rec, "f1": f1}


def train() -> None:
    torch.manual_seed(RANDOM_SEED)
    random.seed(RANDOM_SEED)

    label_map = load_labels(LABEL_CSV)
    entries = gather_patches(PATCH_ROOT, label_map)
    train_entries, val_entries, test_entries = split_by_nodule(entries)

    if not train_entries:
        raise RuntimeError("Training set is empty after splitting nodules.")
    if not val_entries:
        print("[WARN] Validation set is empty; metrics will be unavailable during training.")
    if not test_entries:
        print("[WARN] Test set is empty; final evaluation will be skipped.")

    transform = T.Compose(
        [
            T.Grayscale(num_output_channels=3),
            T.ToTensor(),
            T.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ]
    )

    train_loader = make_dataloader(train_entries, transform, shuffle=True)
    val_loader = make_dataloader(val_entries, transform, shuffle=False) if val_entries else None
    test_loader = make_dataloader(test_entries, transform, shuffle=False) if test_entries else None

    pos_weight_val = compute_pos_weight(train_entries)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
    num_ftrs = model.fc.in_features
    model.fc = nn.Linear(num_ftrs, 1)
    model = model.to(device)

    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pos_weight_val], device=device))
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)

    print(
        f"[INFO] Device: {device}, Train positives: {sum(e.label for e in train_entries)}, "
        f"negatives: {len(train_entries) - sum(e.label for e in train_entries)}, "
        f"pos_weight: {pos_weight_val:.4f}"
    )
    print(f"[INFO] Using decision threshold = {BEST_THRESHOLD:.2f} for metrics")

    best_val_f1 = -1.0
    for epoch in range(1, EPOCHS + 1):
        model.train()
        epoch_losses = []
        for imgs, labels in train_loader:
            imgs = imgs.to(device, non_blocking=True)
            labels = labels.float().to(device, non_blocking=True)

            optimizer.zero_grad()
            logits = model(imgs).squeeze(1)
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()

            epoch_losses.append(loss.item() * labels.size(0))

        train_loss = sum(epoch_losses) / len(train_entries)

        if val_loader is not None:
            val_metrics = evaluate(model, val_loader, device, criterion)
            val_loss = val_metrics["loss"]
            val_acc = val_metrics["acc"]
            val_f1 = val_metrics["f1"]

            print(
                f"Epoch {epoch:02d} | Train Loss: {train_loss:.4f} | "
                f"Val Loss: {val_loss:.4f} | Val Acc: {val_acc:.4f} | Val F1: {val_f1:.4f}"
            )

            if val_f1 > best_val_f1:
                best_val_f1 = val_f1
                SAVE_BEST_MODEL.parent.mkdir(parents=True, exist_ok=True)
                torch.save(model.state_dict(), SAVE_BEST_MODEL)
                print(f"[INFO] Saved best model to {SAVE_BEST_MODEL} (val_f1={val_f1:.4f})")
        else:
            print(f"Epoch {epoch:02d} | Train Loss: {train_loss:.4f} | Val set not available")

    if test_loader is not None:
        test_metrics = evaluate(model, test_loader, device, criterion)
        print(
            "Test Results -> "
            f"Loss: {test_metrics['loss']:.4f}, "
            f"Acc: {test_metrics['acc']:.4f}, "
            f"Precision: {test_metrics['prec']:.4f}, "
            f"Recall: {test_metrics['rec']:.4f}, "
            f"F1: {test_metrics['f1']:.4f}"
        )
    else:
        print("[WARN] Test set empty; skipping final evaluation.")


def main() -> None:
    train()


if __name__ == "__main__":
    main()
