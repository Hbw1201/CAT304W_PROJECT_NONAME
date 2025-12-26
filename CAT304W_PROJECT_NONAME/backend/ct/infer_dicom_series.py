import os
from typing import Optional
import numpy as np
import pydicom
import torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image


def _build_model(weights_path: str, device: str):
    model = models.resnet18(weights=None)
    # binary classifier head (1 logit)
    model.fc = nn.Linear(model.fc.in_features, 1)
    sd = torch.load(weights_path, map_location=device)
    # tolerate checkpoints that may store under different keys
    if isinstance(sd, dict) and "state_dict" in sd:
        sd = sd["state_dict"]
    # remove possible "module." prefixes
    new_sd = {}
    for k, v in sd.items():
        nk = k.replace("module.", "")
        new_sd[nk] = v
    model.load_state_dict(new_sd, strict=False)
    model.to(device)
    model.eval()
    return model


def load_model(weights_path: str, device: str = "cpu"):
    return _build_model(weights_path, device)


def _read_dicom_pixels(path: str) -> np.ndarray:
    ds = pydicom.dcmread(path, force=True)
    arr = ds.pixel_array.astype(np.float32)
    # apply rescale if present
    slope = float(getattr(ds, "RescaleSlope", 1.0))
    intercept = float(getattr(ds, "RescaleIntercept", 0.0))
    arr = arr * slope + intercept
    return arr


def _window_and_normalize(arr: np.ndarray, center: float = 40.0, width: float = 400.0) -> np.ndarray:
    # typical lung/soft tissue-ish window; adjust later if needed
    lo = center - width / 2.0
    hi = center + width / 2.0
    arr = np.clip(arr, lo, hi)
    arr = (arr - lo) / (hi - lo + 1e-6)
    arr = (arr * 255.0).clip(0, 255).astype(np.uint8)
    return arr


def _to_224_center(img_u8: np.ndarray) -> Image.Image:
    # center crop square then resize to 224
    h, w = img_u8.shape
    m = min(h, w)
    y0 = (h - m) // 2
    x0 = (w - m) // 2
    crop = img_u8[y0:y0 + m, x0:x0 + m]
    pil = Image.fromarray(crop).convert("L")
    pil = pil.resize((224, 224))
    return pil


@torch.no_grad()
def infer_dicom_dir(
    dicom_dir: str,
    weights_path: Optional[str] = None,
    device: str = "cpu",
    max_slices: int = 160,
    model=None,
):
    files = [f for f in os.listdir(dicom_dir) if f.lower().endswith(".dcm")]
    files.sort()  # assumes 1-001.dcm style naming
    if not files:
        raise RuntimeError(f"No .dcm files found in {dicom_dir}")

    # optional downsample if too many slices (keep evenly spaced)
    if max_slices and len(files) > max_slices:
        idx = np.linspace(0, len(files) - 1, max_slices).astype(int).tolist()
        files = [files[i] for i in idx]

    if model is None:
        if not weights_path:
            raise ValueError("weights_path is required when model is not provided.")
        model = _build_model(weights_path, device)
    else:
        model = model.to(device)

    tfm = transforms.Compose([
        transforms.ToTensor(),  # (1,H,W) in [0,1]
        transforms.Normalize(mean=[0.5], std=[0.5]),
    ])

    slice_scores = []
    for i, fn in enumerate(files):
        path = os.path.join(dicom_dir, fn)
        arr = _read_dicom_pixels(path)
        u8 = _window_and_normalize(arr)
        pil = _to_224_center(u8)
        x = tfm(pil).unsqueeze(0).to(device)  # (1,1,224,224)
        # expand to 3 channels because resnet expects 3
        x = x.repeat(1, 3, 1, 1)
        logit = model(x).squeeze().float().item()
        prob = 1.0 / (1.0 + np.exp(-logit))
        slice_scores.append({
            "index": i,
            "file": fn,
            "prob_malignant": float(prob),
        })

    probs = np.array([s["prob_malignant"] for s in slice_scores], dtype=np.float32)
    agg = {
        "num_slices_scored": int(len(slice_scores)),
        "mean_prob_malignant": float(probs.mean()) if len(probs) else 0.0,
        "max_prob_malignant": float(probs.max()) if len(probs) else 0.0,
    }
    # simple label for UI
    p = agg["max_prob_malignant"]
    if p >= 0.70:
        label = "high"
    elif p >= 0.40:
        label = "intermediate"
    else:
        label = "low"

    return {
        "label": label,
        "aggregate": agg,
        "slice_scores": slice_scores,
        "note": "Prototype slice-level scoring from center-cropped slices. Replace with nodule detection+patch pipeline for clinical use.",
    }
