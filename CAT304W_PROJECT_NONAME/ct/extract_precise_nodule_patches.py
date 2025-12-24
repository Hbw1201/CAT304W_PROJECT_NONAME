from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pydicom
from PIL import Image
from skimage.draw import polygon


# Fixed paths
LIDC_ROOT = Path(r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI")
OUTPUT_ROOT = Path(r"E:\肺部ct影像\nodule_patches_precise")

PATCH_SIZE: Tuple[int, int] = (64, 64)
MIN_POLY_POINTS = 5


def extract_namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[0][1:]
    return ""


def to_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def slice_sort_key(z: float) -> float:
    try:
        return float(z)
    except (TypeError, ValueError):
        return 0.0


def find_series_and_xml(patient_dir: Path) -> tuple[Optional[Path], Optional[Path]]:
    xml_path = None
    for candidate in patient_dir.rglob("*.xml"):
        if candidate.is_file():
            xml_path = candidate
            break

    candidates: List[tuple[Path, int]] = []
    for sub in patient_dir.rglob("*"):
        if not sub.is_dir():
            continue
        dcm_files = list(sub.glob("*.dcm"))
        if dcm_files:
            candidates.append((sub, len(dcm_files)))

    if not candidates:
        return None, xml_path

    candidates.sort(
        key=lambda item: (
            "ct" not in str(item[0]).lower(),
            -item[1],
        )
    )
    series_dir = candidates[0][0]
    return series_dir, xml_path


def extract_slice_z(ds) -> float:
    pos = getattr(ds, "ImagePositionPatient", None)
    if pos and len(pos) >= 3:
        z_val = to_float(pos[2])
        if z_val is not None:
            return z_val

    loc = to_float(getattr(ds, "SliceLocation", None))
    if loc is not None:
        return loc

    inst = to_float(getattr(ds, "InstanceNumber", None))
    return inst if inst is not None else 0.0


def load_dicom_series(series_dir: Path) -> tuple[np.ndarray, List[float]]:
    dicom_paths = sorted([p for p in series_dir.glob("*.dcm") if p.is_file()])
    if not dicom_paths:
        raise FileNotFoundError(f"No DICOM files found in {series_dir}")

    slices: List[tuple[float, np.ndarray]] = []
    for path in dicom_paths:
        ds = pydicom.dcmread(path)
        z_pos = extract_slice_z(ds)
        arr = ds.pixel_array.astype(np.float32)
        slope = to_float(getattr(ds, "RescaleSlope", 1)) or 1.0
        intercept = to_float(getattr(ds, "RescaleIntercept", 0)) or 0.0
        arr = arr * slope + intercept
        slices.append((z_pos, arr))

    slices.sort(key=lambda item: slice_sort_key(item[0]))
    z_positions = [z for z, _ in slices]
    volume = np.stack([img for _, img in slices], axis=0)
    return volume, z_positions


def closest_slice_index(z_positions: List[float], target_z: float) -> int:
    return min(range(len(z_positions)), key=lambda idx: abs(z_positions[idx] - target_z))


def parse_nodule_rois(xml_path: Path) -> Dict[str, List[dict]]:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    namespace = extract_namespace(root.tag)
    ns = {"ns": namespace} if namespace else {}

    nodules: Dict[str, List[dict]] = defaultdict(list)

    reading_sessions = (
        root.findall(".//ns:readingSession", ns) if ns else root.findall(".//readingSession")
    )

    for session in reading_sessions:
        nodule_nodes = (
            session.findall("ns:unblindedReadNodule", ns)
            if ns
            else session.findall("unblindedReadNodule")
        )
        for nodule in nodule_nodes:
            nodule_id = nodule.get("noduleID") or ""
            if not nodule_id:
                nid_elem = nodule.find("ns:noduleID", ns) if ns else nodule.find("noduleID")
                if nid_elem is not None and nid_elem.text:
                    nodule_id = nid_elem.text.strip()
            if not nodule_id:
                nodule_id = "unknown"

            rois = nodule.findall("ns:roi", ns) if ns else nodule.findall("roi")
            for roi in rois:
                z_elem = roi.find("ns:imageZposition", ns) if ns else roi.find("imageZposition")
                z_val = to_float(z_elem.text) if z_elem is not None else None

                edge_maps = roi.findall("ns:edgeMap", ns) if ns else roi.findall("edgeMap")
                points: List[Tuple[float, float]] = []
                for edge in edge_maps:
                    x_elem = edge.find("ns:xCoord", ns) if ns else edge.find("xCoord")
                    y_elem = edge.find("ns:yCoord", ns) if ns else edge.find("yCoord")
                    x_val = to_float(x_elem.text) if x_elem is not None else None
                    y_val = to_float(y_elem.text) if y_elem is not None else None
                    if x_val is None or y_val is None:
                        continue
                    points.append((x_val, y_val))

                if z_val is None or len(points) < MIN_POLY_POINTS:
                    continue

                nodules[nodule_id].append({"z": z_val, "points": points})

    return nodules


def build_masked_patch(slice_img: np.ndarray, points: List[Tuple[float, float]]) -> Optional[np.ndarray]:
    if len(points) < MIN_POLY_POINTS:
        return None

    height, width = slice_img.shape
    xs = np.clip(np.round([p[0] for p in points]).astype(int), 0, width - 1)
    ys = np.clip(np.round([p[1] for p in points]).astype(int), 0, height - 1)

    rr, cc = polygon(ys, xs, shape=slice_img.shape)
    if rr.size == 0 or cc.size == 0:
        return None

    mask = np.zeros_like(slice_img, dtype=bool)
    mask[rr, cc] = True

    y_indices, x_indices = np.nonzero(mask)
    if y_indices.size == 0 or x_indices.size == 0:
        return None

    y_min, y_max = y_indices.min(), y_indices.max()
    x_min, x_max = x_indices.min(), x_indices.max()

    patch = slice_img[y_min : y_max + 1, x_min : x_max + 1]
    mask_crop = mask[y_min : y_max + 1, x_min : x_max + 1]

    roi_values = patch[mask_crop]
    if roi_values.size == 0:
        return None

    roi_min = float(roi_values.min())
    roi_max = float(roi_values.max())

    if roi_max == roi_min:
        scaled = np.zeros_like(patch, dtype=np.uint8)
        scaled[mask_crop] = 255
    else:
        norm = (patch - roi_min) / (roi_max - roi_min)
        scaled = np.clip(norm * 255.0, 0, 255).astype(np.uint8)
        scaled[~mask_crop] = 0

    return scaled


def resize_patch(patch: np.ndarray) -> np.ndarray:
    img = Image.fromarray(patch.astype(np.uint8), mode="L")
    img = img.resize(PATCH_SIZE, resample=Image.BILINEAR)
    return np.array(img, dtype=np.uint8)


def save_patch(patch: np.ndarray, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(patch, mode="L").save(out_path)


def process_patient(patient_dir: Path, output_root: Path) -> tuple[int, int]:
    patient_id = patient_dir.name
    series_dir, xml_path = find_series_and_xml(patient_dir)

    if xml_path is None or series_dir is None:
        print(f"[WARN] Skipping {patient_id}: missing series or XML")
        return 0, 0

    try:
        volume, z_positions = load_dicom_series(series_dir)
    except FileNotFoundError as exc:
        print(f"[WARN] Skipping {patient_id}: {exc}")
        return 0, 0

    nodules = parse_nodule_rois(xml_path)

    patches_saved = 0
    nodules_with_patches = 0

    for nodule_id, rois in nodules.items():
        nodule_patch_count = 0
        for roi in rois:
            slice_idx = closest_slice_index(z_positions, roi["z"])
            slice_img = volume[slice_idx]

            patch = build_masked_patch(slice_img, roi["points"])
            if patch is None:
                continue

            patch_resized = resize_patch(patch)
            out_dir = output_root / patient_id / nodule_id
            base_name = f"slice{slice_idx:03d}.png"
            out_path = out_dir / base_name

            suffix = 1
            while out_path.exists():
                out_path = out_dir / f"slice{slice_idx:03d}_{suffix}.png"
                suffix += 1

            save_patch(patch_resized, out_path)
            patches_saved += 1
            nodule_patch_count += 1

        if nodule_patch_count > 0:
            nodules_with_patches += 1

    if patches_saved > 0:
        print(f"[INFO] {patient_id}: saved {patches_saved} patches across {nodules_with_patches} nodules")
    else:
        print(f"[INFO] {patient_id}: no valid polygon patches")

    return patches_saved, nodules_with_patches


def main() -> None:
    lidc_root = Path(LIDC_ROOT)
    output_root = Path(OUTPUT_ROOT)

    if not lidc_root.exists():
        raise FileNotFoundError(f"LIDC root not found: {lidc_root}")

    output_root.mkdir(parents=True, exist_ok=True)

    total_patients = 0
    total_nodules = 0
    total_patches = 0

    patient_dirs = [d for d in lidc_root.iterdir() if d.is_dir() and d.name.startswith("LIDC-IDRI-")]

    for patient_dir in sorted(patient_dirs):
        total_patients += 1
        patches, nodules = process_patient(patient_dir, output_root)
        total_patches += patches
        total_nodules += nodules

    print(f"Total patients processed: {total_patients}")
    print(f"Total nodules extracted: {total_nodules}")
    print(f"Total patches extracted: {total_patches}")
    print(f"Output saved to: {output_root}")


if __name__ == "__main__":
    main()
