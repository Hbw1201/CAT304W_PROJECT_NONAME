from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from PIL import Image
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, Subset
import torchvision.transforms as T

# Paths to edit as needed
PATCH_ROOT = r"E:\肺部ct影像\nodule_patches"
LABEL_CSV = r"E:\肺部ct影像\patient_summaries\labels.csv"

# Decision threshold (tuned)
BEST_THRESHOLD = 0.37  # tuned from eval_precise_nodule_model.py

# Training hyperparameters
BATCH_SIZE = 64
NUM_EPOCHS = 20
LEARNING_RATE = 1e-3
NUM_WORKERS = 0
SEED = 42


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def parse_filename(fname: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Expected pattern: prefix_patientid_noduleid_sliceXXX.png
    Example: avg5_LIDC-IDRI-0751_16464_slice074.png
    Returns (patient_id, nodule_id) or (None, None) if cannot parse.
    """
    stem = Path(fname).stem
    parts = stem.split("_")
    patient_id = None
    nodule_id = None
    for i, part in enumerate(parts):
        if part.startswith("LIDC-IDRI-"):
            patient_id = part
            if i + 1 < len(parts):
                nodule_id = parts[i + 1]
            break
    return patient_id, nodule_id


def parse_malignancy_from_fname(fname: str) -> Optional[int]:
    """
    Attempts to parse malignancy from prefix like 'avg5_...'.
    Returns an int or None if unavailable.
    """
    stem = Path(fname).stem
    parts = stem.split("_")
    if parts and parts[0].lower().startswith("avg"):
        try:
            return int(round(float(parts[0][3:])))
        except ValueError:
            return None
    return None


class NodulePatchDataset(Dataset):
    def __init__(self, patch_root: Path, label_df: pd.DataFrame, transform=None):
        super().__init__()
        self.transform = transform

        self.use_label_map = False
        self.label_map: Dict[Tuple[str, str], int] = {}

        if {"patient_id", "nodule_id", "is_malignant"}.issubset(label_df.columns):
            label_df = label_df.copy()
            label_df["patient_id"] = label_df["patient_id"].astype(str)
            label_df["nodule_id"] = label_df["nodule_id"].astype(str)
            label_df["is_malignant"] = label_df["is_malignant"].astype(int)
            self.label_map = {
                (row.patient_id, row.nodule_id): row.is_malignant
                for row in label_df.itertuples()
            }
            self.use_label_map = True
            print("[INFO] Using labels from CSV (patient_id + nodule_id join).")
        else:
            print(
                "[WARN] CSV missing required columns (patient_id, nodule_id, is_malignant). "
                "Will fall back to malignancy parsed from filename prefix 'avgX_'."
            )

        self.samples: List[Tuple[Path, int]] = []
        skipped = 0
        for png_path in patch_root.rglob("*.png"):
            patient_id, nodule_id = parse_filename(png_path.name)
            if patient_id is None or nodule_id is None:
                skipped += 1
                continue
            if self.use_label_map:
                key = (patient_id, nodule_id)
                label = self.label_map.get(key)
                if label is None:
                    skipped += 1
                    continue
            else:
                mal = parse_malignancy_from_fname(png_path.name)
                if mal is None:
                    skipped += 1
                    continue
                label = 1 if mal >= 4 else 0
            self.samples.append((png_path, int(label)))

        print(f"[INFO] Loaded {len(self.samples)} patches, skipped {skipped} without labels")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        path, label = self.samples[idx]
        img = Image.open(path).convert("L")
        if self.transform:
            img = self.transform(img)
        else:
            img = T.ToTensor()(img)  # [1, H, W], float32 in [0,1]
        return img, torch.tensor(label, dtype=torch.float32)


class TransformingSubset(Subset):
    def __init__(self, dataset, indices, transform):
        super().__init__(dataset, indices)
        self.transform = transform

    def __getitem__(self, idx):
        path, lbl = self.dataset.samples[self.indices[idx]]
        img = Image.open(path).convert("L")
        img = self.transform(img)
        if isinstance(img, torch.Tensor):
            if img.dim() == 2:
                img = img.unsqueeze(0)
            if img.shape[1] != 64 or img.shape[2] != 64:
                img = F.interpolate(
                    img.unsqueeze(0), size=(64, 64), mode="bilinear", align_corners=False
                ).squeeze(0)
        return img, torch.tensor(lbl, dtype=torch.float32)


class SimpleCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 4 * 4, 128),
            nn.ReLU(inplace=True),
            nn.Linear(128, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.classifier(x)
        return x.squeeze(1)  # [B]


def compute_metrics(logits: torch.Tensor, targets: torch.Tensor) -> Dict[str, float]:
    probs = torch.sigmoid(logits)
    preds = (probs >= BEST_THRESHOLD).float()
    targets = targets.float()
    correct = (preds == targets).sum().item()
    total = targets.numel()
    acc = correct / total if total > 0 else 0.0

    tp = ((preds == 1) & (targets == 1)).sum().item()
    fp = ((preds == 1) & (targets == 0)).sum().item()
    fn = ((preds == 0) & (targets == 1)).sum().item()

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0

    return {
        "acc": acc,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def split_indices(n: int, seed: int, train_ratio=0.8, val_ratio=0.1):
    indices = list(range(n))
    random.Random(seed).shuffle(indices)
    train_end = int(train_ratio * n)
    val_end = train_end + int(val_ratio * n)
    train_idx = indices[:train_end]
    val_idx = indices[train_end:val_end]
    test_idx = indices[val_end:]
    return train_idx, val_idx, test_idx


def collate_batch(batch):
    imgs = []
    labels = []
    for img, label in batch:
        if img.dim() == 2:
            img = img.unsqueeze(0)
        if img.shape[-2:] != (64, 64):
            img = F.interpolate(
                img.unsqueeze(0), size=(64, 64), mode="bilinear", align_corners=False
            ).squeeze(0)
        imgs.append(img)
        labels.append(label)
    imgs = torch.stack(imgs, dim=0)
    labels = torch.stack(labels, dim=0)
    return imgs, labels


def evaluate(model, loader, device, criterion):
    model.eval()
    total_loss = 0.0
    total_samples = 0
    all_logits = []
    all_targets = []
    with torch.no_grad():
        for imgs, labels in loader:
            imgs = imgs.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            logits = model(imgs)
            loss = criterion(logits, labels)
            total_loss += loss.item() * labels.size(0)
            total_samples += labels.size(0)
            all_logits.append(logits.cpu())
            all_targets.append(labels.cpu())
    avg_loss = total_loss / total_samples if total_samples > 0 else 0.0
    if all_logits:
        logits_cat = torch.cat(all_logits)
        targets_cat = torch.cat(all_targets)
        metrics = compute_metrics(logits_cat, targets_cat)
    else:
        metrics = {"acc": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0}
    return avg_loss, metrics


def main():
    set_seed(SEED)

    patch_root = Path(PATCH_ROOT)
    label_csv = Path(LABEL_CSV)
    if not patch_root.exists():
        raise FileNotFoundError(f"Patch root not found: {patch_root}")
    if not label_csv.exists():
        raise FileNotFoundError(f"Label CSV not found: {label_csv}")

    labels_df = pd.read_csv(label_csv)
    required_cols = {"patient_id", "nodule_id", "is_malignant"}
    missing = required_cols - set(labels_df.columns)
    if missing:
        print(
            f"[WARN] Label CSV missing columns {missing}; will rely on filename-derived labels (avgX_ prefix)."
        )

    # Transforms
    train_tfms = T.Compose(
        [
            T.RandomHorizontalFlip(),
            T.RandomRotation(degrees=10, fill=0),
            T.Resize((64, 64)),
            T.ToTensor(),
        ]
    )
    eval_tfms = T.Compose(
        [
            T.Resize((64, 64)),
            T.ToTensor(),
        ]
    )

    full_dataset = NodulePatchDataset(patch_root, labels_df, transform=None)

    # We apply transforms via wrappers on Subset using lambdas
    train_idx, val_idx, test_idx = split_indices(len(full_dataset), SEED)

    train_ds = TransformingSubset(full_dataset, train_idx, train_tfms)
    val_ds = TransformingSubset(full_dataset, val_idx, eval_tfms)
    test_ds = TransformingSubset(full_dataset, test_idx, eval_tfms)

    use_cuda = torch.cuda.is_available()
    if not use_cuda:
        raise RuntimeError("未检测到可用的GPU，请安装CUDA版PyTorch或检查驱动后再试。")
    device = torch.device("cuda")
    pin_mem = True
    print(f"[INFO] Using device: {device} ({torch.cuda.get_device_name(0)})")
    print(f"[INFO] Using decision threshold = {BEST_THRESHOLD:.2f} for metrics")

    train_loader = DataLoader(
        train_ds,
        batch_size=BATCH_SIZE,
        shuffle=True,
        num_workers=NUM_WORKERS,
        pin_memory=pin_mem,
        collate_fn=collate_batch,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=NUM_WORKERS,
        pin_memory=pin_mem,
        collate_fn=collate_batch,
    )
    test_loader = DataLoader(
        test_ds,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=NUM_WORKERS,
        pin_memory=pin_mem,
        collate_fn=collate_batch,
    )

    model = SimpleCNN().to(device)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    for epoch in range(1, NUM_EPOCHS + 1):
        model.train()
        total_loss = 0.0
        total_samples = 0
        for imgs, labels in train_loader:
            imgs = imgs.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            optimizer.zero_grad()
            logits = model(imgs)
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * labels.size(0)
            total_samples += labels.size(0)
        train_loss = total_loss / total_samples if total_samples > 0 else 0.0

        val_loss, val_metrics = evaluate(model, val_loader, device, criterion)
        print(
            f"Epoch {epoch:02d} | "
            f"Train Loss: {train_loss:.4f} | "
            f"Val Loss: {val_loss:.4f} | "
            f"Val Acc: {val_metrics['acc']:.4f} | "
            f"Val F1: {val_metrics['f1']:.4f}"
        )

    test_loss, test_metrics = evaluate(model, test_loader, device, criterion)
    print(
        "Test Results -> "
        f"Loss: {test_loss:.4f}, "
        f"Acc: {test_metrics['acc']:.4f}, "
        f"Precision: {test_metrics['precision']:.4f}, "
        f"Recall: {test_metrics['recall']:.4f}, "
        f"F1: {test_metrics['f1']:.4f}"
    )


if __name__ == "__main__":
    main()
