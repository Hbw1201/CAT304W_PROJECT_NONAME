from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pydicom

# User-editable roots
LIDC_ROOT = r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI"
OUTPUT_ROOT = r"E:\肺部ct影像\nodule_patches"

PATCH_HALF_SIZE = 32  # final patch ~64x64


def extract_namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[0][1:]
    return ""


def to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value) -> Optional[int]:
    try:
        return int(float(value))
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
        volume_slices.append(ds.pixel_array)
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


def find_series_and_xml(patient_dir: Path) -> tuple[Optional[Path], Optional[Path]]:
    xml_path = None
    for candidate in patient_dir.rglob("*.xml"):
        if candidate.is_file():
            xml_path = candidate
            break

    candidates = []
    for sub in patient_dir.rglob("*"):
        if not sub.is_dir():
            continue
        dcm_files = list(sub.glob("*.dcm"))
        if len(dcm_files) >= 10:
            candidates.append((sub, len(dcm_files)))

    if candidates:
        candidates.sort(
            key=lambda item: (
                "ct" not in str(item[0]).lower(),  # prefer CT in path
                -item[1],  # prefer more files
            )
        )
        series_dir = candidates[0][0]
    else:
        series_dir = None

    if xml_path is None or series_dir is None:
        return None, None
    return series_dir, xml_path


def parse_nodules(xml_path: Path) -> Dict[str, Dict[str, List[float] | List[int]]]:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    namespace = extract_namespace(root.tag)
    ns = {"ns": namespace} if namespace else {}

    nodules: Dict[str, Dict[str, List[float] | List[int]]] = {}

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

            entry = nodules.setdefault(
                nodule_id,
                {"z": [], "x": [], "y": [], "malignancies": []},
            )

            characteristics = (
                nodule.find("ns:characteristics", ns)
                if ns
                else nodule.find("characteristics")
            )
            if characteristics is not None:
                mal_elem = (
                    characteristics.find("ns:malignancy", ns)
                    if ns
                    else characteristics.find("malignancy")
                )
                mal_val = to_int(mal_elem.text) if mal_elem is not None else None
                if mal_val is not None:
                    entry["malignancies"].append(mal_val)  # type: ignore[arg-type]

            rois = (
                nodule.findall("ns:roi", ns)
                if ns
                else nodule.findall("roi")
            )
            for roi in rois:
                z_elem = (
                    roi.find("ns:imageZposition", ns)
                    if ns
                    else roi.find("imageZposition")
                )
                z_val = to_float(z_elem.text) if z_elem is not None else None
                if z_val is not None:
                    entry["z"].append(z_val)  # type: ignore[arg-type]

                edge_maps = (
                    roi.findall("ns:edgeMap", ns)
                    if ns
                    else roi.findall("edgeMap")
                )
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
                    if x_val is not None:
                        entry["x"].append(x_val)  # type: ignore[arg-type]
                    if y_val is not None:
                        entry["y"].append(y_val)  # type: ignore[arg-type]

    return nodules


def build_nodule_summaries(
    nodules_raw: Dict[str, Dict[str, List[float] | List[int]]],
    slice_z_positions: List[float],
) -> List[dict]:
    summaries = []
    for nodule_id in sorted(nodules_raw):
        data = nodules_raw[nodule_id]
        z_vals: List[float] = data["z"]  # type: ignore[assignment]
        x_vals: List[float] = data["x"]  # type: ignore[assignment]
        y_vals: List[float] = data["y"]  # type: ignore[assignment]
        malignancies: List[int] = data["malignancies"]  # type: ignore[assignment]

        center_z = mean(z_vals) if z_vals else None
        center_x = mean(x_vals) if x_vals else None
        center_y = mean(y_vals) if y_vals else None
        avg_malig = mean(malignancies) if malignancies else None

        slice_idx = (
            closest_slice_index(slice_z_positions, center_z)
            if center_z is not None
            else None
        )

        summaries.append(
            {
                "nodule_id": nodule_id,
                "slice_index": slice_idx,
                "center": (center_z, center_y, center_x),
                "malignancies": malignancies,
                "avg_malignancy": avg_malig,
            }
        )
    return summaries


def save_patch(
    patch: np.ndarray,
    out_path: Path,
) -> None:
    plt.imsave(out_path, patch, cmap="gray")


def process_patient(patient_dir: Path, output_root: Path) -> int:
    patient_id = patient_dir.name
    series_dir, xml_path = find_series_and_xml(patient_dir)
    if series_dir is None or xml_path is None:
        print(f"[WARN] Skipping {patient_id}: missing series or XML")
        return 0

    try:
        volume, slice_z_positions = load_dicom_series(series_dir)
    except FileNotFoundError as exc:
        print(f"[WARN] Skipping {patient_id}: {exc}")
        return 0

    nodules_raw = parse_nodules(xml_path)
    summaries = build_nodule_summaries(nodules_raw, slice_z_positions)

    patches_saved = 0
    for nodule in summaries:
        slice_idx = nodule["slice_index"]
        avg_malig = nodule["avg_malignancy"]
        if slice_idx is None or avg_malig is None:
            continue
        avg_round = int(round(avg_malig))
        if avg_round < 1 or avg_round > 5:
            continue

        _, center_y, center_x = nodule["center"]
        if center_x is None or center_y is None:
            continue

        slice_img = volume[slice_idx]
        height, width = slice_img.shape

        cx = int(round(center_x))
        cy = int(round(center_y))
        y1 = max(0, cy - PATCH_HALF_SIZE)
        y2 = min(height, cy + PATCH_HALF_SIZE)
        x1 = max(0, cx - PATCH_HALF_SIZE)
        x2 = min(width, cx + PATCH_HALF_SIZE)

        if (y2 - y1) <= 0 or (x2 - x1) <= 0:
            continue

        patch = slice_img[y1:y2, x1:x2]

        class_dir = output_root / str(avg_round)
        class_dir.mkdir(parents=True, exist_ok=True)

        out_name = (
            f"avg{avg_round}_"
            f"{patient_id}_"
            f"{nodule['nodule_id']}_"
            f"slice{slice_idx:03d}.png"
        )
        out_path = class_dir / out_name
        save_patch(patch, out_path)
        patches_saved += 1

    return patches_saved


def main() -> None:
    lidc_root = Path(LIDC_ROOT)
    output_root = Path(OUTPUT_ROOT)

    if not lidc_root.exists():
        raise FileNotFoundError(f"LIDC root not found: {lidc_root}")

    output_root.mkdir(parents=True, exist_ok=True)

    total_patients = 0
    patients_with_patches = 0
    total_patches = 0

    patient_dirs = [
        d for d in lidc_root.iterdir() if d.is_dir() and d.name.startswith("LIDC-IDRI-")
    ]

    for patient_dir in sorted(patient_dirs):
        total_patients += 1
        patches_saved = process_patient(patient_dir, output_root)
        total_patches += patches_saved
        if patches_saved > 0:
            patients_with_patches += 1
            print(f"[INFO] Saved {patches_saved} patches for {patient_dir.name}")
        else:
            print(f"[INFO] No valid nodule patches for {patient_dir.name}")

    print(f"Total patients processed: {total_patients}")
    print(f"Patients with at least one patch: {patients_with_patches}")
    print(f"Total patches saved: {total_patches}")
    print(f"Output root: {output_root}")


if __name__ == "__main__":
    main()
