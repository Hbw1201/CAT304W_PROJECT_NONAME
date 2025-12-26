from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pydicom
from PIL import Image
import torch
import torchvision.transforms as T

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

# 常量配置
SUMMARY_ROOT = r"E:\肺部ct影像\patient_summaries"
PATCH_ROOT = r"E:\肺部ct影像\nodule_patches_multislice"
PATCH_SIZE = 64
MAX_SLICES_PER_NODULE = 5
AUG_PER_PATCH = 2  # 在原 patch 基础上再生成多少个增强版本


def extract_namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[0][1:]
    return ""


def to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def slice_sort_key(ds) -> float:
    pos = getattr(ds, "ImagePositionPatient", None)
    if pos and len(pos) >= 3:
        try:
            return float(pos[2])
        except (TypeError, ValueError):
            pass
    try:
        return float(getattr(ds, "InstanceNumber", 0))
    except (TypeError, ValueError):
        return 0.0


def load_dicom_series(series_dir: Path) -> Tuple[np.ndarray, List[float]]:
    files = sorted([p for p in series_dir.glob("*.dcm") if p.is_file()])
    if not files:
        raise FileNotFoundError(f"No DICOM files found in {series_dir}")

    datasets = [pydicom.dcmread(p) for p in files]
    datasets.sort(key=slice_sort_key)

    volume_slices = []
    z_positions: List[float] = []
    for ds in datasets:
        volume_slices.append(ds.pixel_array.astype(np.float32))
        pos = getattr(ds, "ImagePositionPatient", None)
        if pos and len(pos) >= 3:
            try:
                z_positions.append(float(pos[2]))
                continue
            except (TypeError, ValueError):
                pass
        try:
            z_positions.append(float(getattr(ds, "InstanceNumber", 0)))
        except (TypeError, ValueError):
            z_positions.append(0.0)

    volume = np.stack(volume_slices, axis=0)
    return volume, z_positions


def closest_slice_index(slice_z_positions: List[float], target_z: float) -> int:
    return min(
        range(len(slice_z_positions)),
        key=lambda i: abs(slice_z_positions[i] - target_z),
    )


def parse_nodule_rois(xml_path: Path, slice_z_positions: List[float]) -> Dict[str, Dict[int, Dict[str, List[float]]]]:
    import xml.etree.ElementTree as ET

    tree = ET.parse(xml_path)
    root = tree.getroot()
    namespace = extract_namespace(root.tag)
    ns = {"ns": namespace} if namespace else {}

    nodules: Dict[str, Dict[int, Dict[str, List[float]]]] = {}

    reading_sessions = (
        root.findall(".//ns:readingSession", ns)
        if ns
        else root.findall(".//readingSession")
    )

    for session in reading_sessions:
        nodule_nodes = (
            session.findall("ns:unblindedReadNodule", ns)
            if ns
            else session.findall("unblindedReadNodule")
        )
        for nodule in nodule_nodes:
            nodule_id = nodule.get("noduleID")
            if not nodule_id:
                nid_elem = (
                    nodule.find("ns:noduleID", ns)
                    if ns
                    else nodule.find("noduleID")
                )
                if nid_elem is not None and nid_elem.text:
                    nodule_id = nid_elem.text.strip()
            if not nodule_id:
                nodule_id = "unknown"

            rois = (
                nodule.findall("ns:roi", ns)
                if ns
                else nodule.findall("roi")
            )
            for roi in rois:
                inclusion_elem = (
                    roi.find("ns:inclusion", ns)
                    if ns
                    else roi.find("inclusion")
                )
                if inclusion_elem is not None and isinstance(inclusion_elem.text, str):
                    if inclusion_elem.text.strip().lower() == "false":
                        continue

                z_elem = (
                    roi.find("ns:imageZposition", ns)
                    if ns
                    else roi.find("imageZposition")
                )
                roi_z = to_float(z_elem.text) if z_elem is not None else None
                if roi_z is None:
                    continue

                edge_maps = (
                    roi.findall("ns:edgeMap", ns)
                    if ns
                    else roi.findall("edgeMap")
                )
                xs: List[float] = []
                ys: List[float] = []
                for edge in edge_maps:
                    x_elem = (
                        edge.find("ns:xCoord", ns)
                        if ns
                        else edge.find("xCoord")
                    )
                    y_elem = (
                        edge.find("ns:yCoord", ns)
                        if ns
                        else edge.find("yCoord")
                    )
                    x_val = to_float(x_elem.text) if x_elem is not None else None
                    y_val = to_float(y_elem.text) if y_elem is not None else None
                    if x_val is not None and y_val is not None:
                        xs.append(x_val)
                        ys.append(y_val)

                if len(xs) == 0 or len(ys) == 0:
                    continue

                slice_idx = closest_slice_index(slice_z_positions, roi_z)
                entry = nodules.setdefault(nodule_id, {}).setdefault(slice_idx, {"xs": [], "ys": []})
                entry["xs"].extend(xs)
                entry["ys"].extend(ys)

    return nodules


def select_slices(slice_indices: List[int], max_slices: int) -> List[int]:
    unique = sorted(set(slice_indices))
    if len(unique) <= max_slices:
        return unique
    center_val = sum(unique) / len(unique)
    center_idx = min(unique, key=lambda x: abs(x - center_val))
    selected = [center_idx]
    remaining = [s for s in unique if s != center_idx]
    offset = 1
    while len(selected) < max_slices and remaining:
        higher = [s for s in remaining if s > center_idx]
        lower = [s for s in remaining if s < center_idx]
        pick = None
        if lower:
            pick = max(lower)
            selected.append(pick)
            remaining.remove(pick)
        if len(selected) >= max_slices or not remaining:
            break
        if higher:
            pick = min(higher)
            selected.append(pick)
            remaining.remove(pick)
        offset += 1
    return sorted(selected[:max_slices])


def crop_patch(slice_img: np.ndarray, center_x: float, center_y: float, size: int) -> np.ndarray:
    h, w = slice_img.shape
    half = size // 2
    cx = int(round(center_x))
    cy = int(round(center_y))
    left = max(0, cx - half)
    top = max(0, cy - half)
    if left + size > w:
        left = max(0, w - size)
    if top + size > h:
        top = max(0, h - size)
    patch = slice_img[top : top + size, left : left + size]
    if patch.shape != (size, size):
        pad_h = size - patch.shape[0]
        pad_w = size - patch.shape[1]
        patch = np.pad(patch, ((0, pad_h), (0, pad_w)), mode="edge")
    return patch.astype(np.float32)


def augment_patch(patch: np.ndarray, aug_per_patch: int) -> List[np.ndarray]:
    patches = [patch]
    pil = Image.fromarray(normalize_to_uint8(patch))
    aug_ops = [
        lambda img: img.transpose(Image.FLIP_LEFT_RIGHT),
        lambda img: T.functional.rotate(img, angle=10, fill=0),
        lambda img: T.functional.rotate(img, angle=-10, fill=0),
    ]
    random.shuffle(aug_ops)
    for i in range(min(aug_per_patch, len(aug_ops))):
        aug_img = aug_ops[i](pil)
        aug_arr = np.array(aug_img, dtype=np.float32)
        patches.append(aug_arr)
    return patches


def normalize_to_uint8(patch: np.ndarray) -> np.ndarray:
    p = patch.astype(np.float32)
    p = p - p.min()
    vmax = p.max()
    if vmax > 0:
        p = p / vmax
    p = (p * 255).clip(0, 255).astype(np.uint8)
    return p


def main():
    random.seed(42)

    summary_root = Path(SUMMARY_ROOT)
    patch_root = Path(PATCH_ROOT)
    patch_root.mkdir(parents=True, exist_ok=True)
    benign_dir = patch_root / "benign"
    malignant_dir = patch_root / "malignant"
    benign_dir.mkdir(parents=True, exist_ok=True)
    malignant_dir.mkdir(parents=True, exist_ok=True)

    labels_path = summary_root / "labels.csv"
    if not labels_path.exists():
        raise FileNotFoundError(f"labels.csv not found at {labels_path}")
    import pandas as pd

    labels_df = pd.read_csv(labels_path)
    required_cols = {"patient_id", "nodule_id", "is_malignant"}
    missing = required_cols - set(labels_df.columns)
    if missing:
        print(f"[WARN] labels.csv missing columns: {missing}, will fall back to summary avg_malignancy.")
        labels_df = pd.DataFrame(columns=list(required_cols))

    label_map = {
        (str(row.patient_id), str(row.nodule_id)): int(row.is_malignant)
        for row in labels_df.itertuples()
    }

    patients = sorted({str(row.patient_id) for row in labels_df.itertuples()}) if not labels_df.empty else None
    if not patients:
        # fallback: scan summaries present
        patients = sorted([p.stem.replace("_summary", "") for p in Path(SUMMARY_ROOT).glob("*_summary.json")])
    pbar = tqdm(patients, desc="Patients") if tqdm else patients

    stats_patients = 0
    stats_nodules = 0
    total_patches = 0
    benign_count = 0
    malignant_count = 0

    for patient_id in pbar:
        summary_path = summary_root / f"{patient_id}_summary.json"
        if not summary_path.exists():
            print(f"[WARN] Summary not found for {patient_id}, skipping.")
            continue
        with summary_path.open("r", encoding="utf-8") as f:
            summary = json.load(f)

        series_dir = Path(summary.get("series_dir", ""))
        xml_path = Path(summary.get("xml_path", ""))
        if not series_dir.exists() or not xml_path.exists():
            print(f"[WARN] Missing series or xml for {patient_id}, skipping.")
            continue

        try:
            volume, slice_z_positions = load_dicom_series(series_dir)
        except FileNotFoundError as exc:
            print(f"[WARN] {exc}")
            continue

        # 用 XML 重新构建每个结节的多切片 ROI
        nodule_rois = parse_nodule_rois(xml_path, slice_z_positions)

        summary_nod_map = {str(n["nodule_id"]): n for n in summary.get("nodules", [])}

        # 获取该患者的所有标注结节
        if not labels_df.empty:
            patient_nodules = [row for row in labels_df.itertuples() if str(row.patient_id) == patient_id]
        else:
            patient_nodules = [type("obj", (), {"nodule_id": nid, "is_malignant": None}) for nid in nodule_rois.keys()]
        if not patient_nodules:
            continue

        stats_patients += 1

        for row in patient_nodules:
            nid = str(row.nodule_id)
            label = None
            if (patient_id, nid) in label_map:
                label = label_map[(patient_id, nid)]
            else:
                node = summary_nod_map.get(nid)
                if node and node.get("avg_malignancy") is not None:
                    label = 1 if float(node["avg_malignancy"]) >= 4 else 0
            if label is None:
                print(f"[WARN] No label for {patient_id} nodule {nid}, skipping.")
                continue
            if nid not in nodule_rois:
                print(f"[WARN] Nodule {nid} not found in XML for {patient_id}, skipping.")
                continue

            slice_dict = nodule_rois[nid]
            slice_indices = list(slice_dict.keys())
            if not slice_indices:
                continue

            selected_slices = select_slices(slice_indices, MAX_SLICES_PER_NODULE)

            for s_idx in selected_slices:
                roi_data = slice_dict[s_idx]
                xs = roi_data["xs"]
                ys = roi_data["ys"]
                if not xs or not ys:
                    continue
                center_x = sum(xs) / len(xs)
                center_y = sum(ys) / len(ys)

                slice_img = volume[s_idx]
                patch = crop_patch(slice_img, center_x, center_y, PATCH_SIZE)
                patches = augment_patch(patch, AUG_PER_PATCH)

                for aug_idx, p_arr in enumerate(patches):
                    out_dir = malignant_dir if label == 1 else benign_dir
                    fname = f"{patient_id}_{nid}_s{s_idx:03d}_v{aug_idx}.png"
                    out_path = out_dir / fname
                    Image.fromarray(normalize_to_uint8(p_arr)).save(out_path)
                    total_patches += 1
                    if label == 1:
                        malignant_count += 1
                    else:
                        benign_count += 1

            stats_nodules += 1

    print("==== Summary ====")
    print(f"Patients processed: {stats_patients}")
    print(f"Nodules processed: {stats_nodules}")
    print(f"Total patches (with aug): {total_patches}")
    print(f"Benign patches: {benign_count}")
    print(f"Malignant patches: {malignant_count}")


if __name__ == "__main__":
    main()
