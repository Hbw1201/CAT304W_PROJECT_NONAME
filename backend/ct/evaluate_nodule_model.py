from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score, roc_curve
from torch.utils.data import DataLoader, Dataset, Subset
import torchvision.transforms as T
import torchvision.models as models

# Decision threshold (tuned)
BEST_THRESHOLD = 0.37  # tuned from eval_precise_nodule_model.py

# 路径常量（与训练脚本保持一致）
PATCH_ROOT = r"E:\肺部ct影像\nodule_patches"
LABEL_CSV = r"E:\肺部ct影像\patient_summaries\labels.csv"
MODEL_PATH = r"E:\肺部ct影像\code\best_resnet_nodule_precise.pt"

# 评估配置
BATCH_SIZE = 64
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
        img = Image.open(path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        else:
            img = T.ToTensor()(img)
        if isinstance(img, torch.Tensor) and img.dim() == 3 and img.shape[0] == 1:
            img = img.repeat(3, 1, 1)
        return img, torch.tensor(label, dtype=torch.float32)


class TransformingSubset(Subset):
    def __init__(self, dataset, indices, transform):
        super().__init__(dataset, indices)
        self.transform = transform

    def __getitem__(self, idx):
        path, lbl = self.dataset.samples[self.indices[idx]]
        img = Image.open(path).convert("RGB")
        img = self.transform(img)
        if isinstance(img, torch.Tensor):
            if img.dim() == 2:
                img = img.unsqueeze(0)
            if img.dim() == 3 and img.shape[0] == 1:
                img = img.repeat(3, 1, 1)
            if img.shape[-2:] != (64, 64):
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
        return x.squeeze(1)


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
        if img.dim() == 3 and img.shape[0] == 1:
            img = img.repeat(3, 1, 1)
        if img.shape[-2:] != (64, 64):
            img = F.interpolate(
                img.unsqueeze(0), size=(64, 64), mode="bilinear", align_corners=False
            ).squeeze(0)
        imgs.append(img)
        labels.append(label)
    imgs = torch.stack(imgs, dim=0)
    labels = torch.stack(labels, dim=0)
    return imgs, labels


def build_model(device: torch.device) -> nn.Module:
    model = models.resnet18(weights=None)
    num_ftrs = model.fc.in_features
    model.fc = nn.Linear(num_ftrs, 1)
    model = model.to(device)
    return model


def main():
    set_seed(SEED)

    patch_root = Path(PATCH_ROOT)
    label_csv = Path(LABEL_CSV)
    if not patch_root.exists():
        raise FileNotFoundError(f"Patch root not found: {patch_root}")
    if not label_csv.exists():
        raise FileNotFoundError(f"Label CSV not found: {label_csv}")
    if not Path(MODEL_PATH).exists():
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")

    labels_df = pd.read_csv(label_csv)
    required_cols = {"patient_id", "nodule_id", "is_malignant"}
    missing = required_cols - set(labels_df.columns)
    if missing:
        print(
            f"[WARN] Label CSV missing columns {missing}; will rely on filename-derived labels (avgX_ prefix)."
        )

    eval_tfms = T.Compose(
        [
            T.Resize((64, 64)),
            T.ToTensor(),
        ]
    )

    full_dataset = NodulePatchDataset(patch_root, labels_df, transform=None)
    _, _, test_idx = split_indices(len(full_dataset), SEED)
    test_ds = TransformingSubset(full_dataset, test_idx, eval_tfms)

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")
    pin_mem = use_cuda
    print(f"[INFO] Using device: {device}")
    print(f"[INFO] Using decision threshold = {BEST_THRESHOLD:.2f}")

    test_loader = DataLoader(
        test_ds,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=NUM_WORKERS,
        pin_memory=pin_mem,
        collate_fn=collate_batch,
    )

    model = build_model(device)
    state = torch.load(MODEL_PATH, map_location=device)
    model.load_state_dict(state)
    model.eval()

    all_logits = []
    all_labels = []
    with torch.no_grad():
        for imgs, labels in test_loader:
            imgs = imgs.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            logits = model(imgs)
            all_logits.append(logits.cpu())
            all_labels.append(labels.cpu())

    if not all_logits:
        print("[WARN] No test samples to evaluate.")
        return

    logits_cat = torch.cat(all_logits)
    labels_cat = torch.cat(all_labels)
    probs = torch.sigmoid(logits_cat).numpy()
    preds = (probs >= BEST_THRESHOLD).astype(np.int64).flatten()
    y_true = labels_cat.numpy().astype(np.int64).flatten()

    report = classification_report(
        y_true,
        preds,
        target_names=["benign/normal", "malignant/high-risk"],
        digits=4,
    )
    print(report)

    # Confusion matrix
    cm = confusion_matrix(y_true, preds, labels=[0, 1])
    fig, ax = plt.subplots(figsize=(4, 4))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(["Normal", "High-risk"])
    ax.set_yticklabels(["Normal", "High-risk"])
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, str(v), ha="center", va="center", color="black")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    plt.tight_layout()
    plt.savefig("confusion_matrix.png", dpi=200)
    plt.show()
    plt.close(fig)

    # ROC curve
    try:
        fpr, tpr, _ = roc_curve(y_true, probs)
        auc = roc_auc_score(y_true, probs)
        plt.figure(figsize=(5, 5))
        plt.plot(fpr, tpr, label=f"ROC AUC = {auc:.4f}")
        plt.plot([0, 1], [0, 1], "k--", label="Random")
        plt.xlabel("False Positive Rate")
        plt.ylabel("True Positive Rate")
        plt.title("ROC Curve")
        plt.legend(loc="lower right")
        plt.tight_layout()
        plt.savefig("roc_curve.png", dpi=200)
        plt.show()
        plt.close()
    except ValueError:
        print("[WARN] ROC/AUC cannot be computed (single-class predictions).")


if __name__ == "__main__":
    main()
