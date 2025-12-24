from __future__ import annotations

import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from PIL import Image
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, Subset
import torchvision.transforms as T
import torchvision.models as models

# Paths
PATCH_ROOT = Path(r"E:\肺部ct影像\nodule_patches")
LABEL_CSV = Path(r"E:\肺部ct影像\patient_summaries\nodule_labels.csv")
BEST_MODEL_PATH = Path(r"E:\肺部ct影像\code\best_resnet_nodule.pt")

# Hyperparameters
BATCH_SIZE = 64
NUM_EPOCHS = 20
LR = 1e-4
SEED = 42
NUM_WORKERS = 0


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def parse_patient_nodule(fname: str) -> Optional[Tuple[str, str]]:
    """
    Expected pattern: prefix_patientid_noduleid_sliceXXX.png
    Example: avg5_LIDC-IDRI-0751_16464_slice074.png
    Returns (patient_id, nodule_id) or None.
    """
    stem = Path(fname).stem
    parts = stem.split("_")
    if len(parts) < 3:
        return None
    patient_id = parts[1] if parts[1].startswith("LIDC-IDRI-") else None
    nodule_id = parts[2] if len(parts[2]) > 0 else None
    if patient_id and nodule_id:
        return patient_id, nodule_id
    return None


def load_labels(csv_path: Path) -> Dict[Tuple[str, str], int]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Label CSV not found: {csv_path}")
    df = pd.read_csv(csv_path)
    required = {"patient_id", "nodule_id", "is_malignant"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Label CSV missing columns: {missing}")
    df["patient_id"] = df["patient_id"].astype(str)
    df["nodule_id"] = df["nodule_id"].astype(str)
    df["label"] = df["is_malignant"].astype(int)
    label_map = {(row.patient_id, row.nodule_id): row.label for row in df.itertuples()}
    return label_map


class NodulePatchDataset(Dataset):
    def __init__(self, patch_root: Path, label_map: Dict[Tuple[str, str], int], transform=None):
        super().__init__()
        self.transform = transform
        self.samples: List[Tuple[Path, int]] = []
        skipped = 0
        for png_path in patch_root.rglob("*.png"):
            parsed = parse_patient_nodule(png_path.name)
            if parsed is None:
                skipped += 1
                continue
            key = (parsed[0], parsed[1])
            label = label_map.get(key)
            if label is None:
                print(f"[WARN] Label not found for {key}, skipping {png_path.name}")
                skipped += 1
                continue
            self.samples.append((png_path, int(label)))
        print(f"[INFO] Loaded {len(self.samples)} patches, skipped {skipped} without labels")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        path, label = self.samples[idx]
        img = Image.open(path).convert("L")
        # convert to 3-channel
        img = img.convert("RGB")
        if self.transform:
            img = self.transform(img)
        label = torch.tensor(label, dtype=torch.long)
        return img, label


def split_indices(n: int, seed: int, train_ratio=0.7, val_ratio=0.15):
    indices = list(range(n))
    random.Random(seed).shuffle(indices)
    train_end = int(train_ratio * n)
    val_end = train_end + int(val_ratio * n)
    train_idx = indices[:train_end]
    val_idx = indices[train_end:val_end]
    test_idx = indices[val_end:]
    return train_idx, val_idx, test_idx


def compute_metrics(logits: torch.Tensor, targets: torch.Tensor):
    preds = torch.argmax(logits, dim=1)
    correct = (preds == targets).sum().item()
    total = targets.numel()
    acc = correct / total if total > 0 else 0.0

    tp = ((preds == 1) & (targets == 1)).sum().item()
    tn = ((preds == 0) & (targets == 0)).sum().item()
    fp = ((preds == 1) & (targets == 0)).sum().item()
    fn = ((preds == 0) & (targets == 1)).sum().item()

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return acc, precision, recall, f1


def train_one_epoch(model, loader, device, criterion, optimizer):
    model.train()
    total_loss = 0.0
    total_samples = 0
    for imgs, labels in loader:
        imgs = imgs.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True)
        optimizer.zero_grad()
        logits = model(imgs)
        loss = criterion(logits, labels)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * labels.size(0)
        total_samples += labels.size(0)
    avg_loss = total_loss / total_samples if total_samples > 0 else 0.0
    return avg_loss


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
        acc, precision, recall, f1 = compute_metrics(logits_cat, targets_cat)
    else:
        acc = precision = recall = f1 = 0.0
    return avg_loss, acc, precision, recall, f1


def main():
    set_seed(SEED)

    label_map = load_labels(LABEL_CSV)
    transform = T.Compose(
        [
            T.Resize((64, 64)),
            T.ToTensor(),
            T.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ]
    )

    dataset = NodulePatchDataset(PATCH_ROOT, label_map, transform=transform)

    train_idx, val_idx, test_idx = split_indices(len(dataset), SEED, train_ratio=0.7, val_ratio=0.15)
    train_ds = Subset(dataset, train_idx)
    val_ds = Subset(dataset, val_idx)
    test_ds = Subset(dataset, test_idx)

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")
    pin_mem = use_cuda
    print(f"[INFO] Using device: {device}")

    train_loader = DataLoader(
        train_ds, batch_size=BATCH_SIZE, shuffle=True, num_workers=NUM_WORKERS, pin_memory=pin_mem
    )
    val_loader = DataLoader(
        val_ds, batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS, pin_memory=pin_mem
    )
    test_loader = DataLoader(
        test_ds, batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS, pin_memory=pin_mem
    )

    model = models.resnet18(weights=None)
    # ensure first conv accepts 3-channel (default is 3, so fine) -> already 3 channels
    num_ftrs = model.fc.in_features
    model.fc = nn.Linear(num_ftrs, 2)
    model = model.to(device)

    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)

    best_val_acc = 0.0
    best_state = None

    for epoch in range(1, NUM_EPOCHS + 1):
        train_loss = train_one_epoch(model, train_loader, device, criterion, optimizer)
        val_loss, val_acc, val_prec, val_rec, val_f1 = evaluate(model, val_loader, device, criterion)

        print(
            f"Epoch {epoch:02d} | "
            f"Train Loss: {train_loss:.4f} | "
            f"Val Loss: {val_loss:.4f} | "
            f"Val Acc: {val_acc:.4f} | "
            f"Val F1: {val_f1:.4f}"
        )

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state = model.state_dict()
            torch.save(best_state, BEST_MODEL_PATH)
            print(f"[INFO] Saved best model to {BEST_MODEL_PATH} (val_acc={val_acc:.4f})")

    if best_state is None:
        print("[WARN] No best model saved; using last epoch weights for testing.")
    else:
        model.load_state_dict(best_state)

    test_loss, test_acc, test_prec, test_rec, test_f1 = evaluate(model, test_loader, device, criterion)
    print(
        "Test Results -> "
        f"Loss: {test_loss:.4f}, "
        f"Acc: {test_acc:.4f}, "
        f"Precision: {test_prec:.4f}, "
        f"Recall: {test_rec:.4f}, "
        f"F1: {test_f1:.4f}"
    )


if __name__ == "__main__":
    main()
