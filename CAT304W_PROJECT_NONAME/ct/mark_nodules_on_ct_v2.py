from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pydicom

# User-editable constants
PATIENT_ID = "LIDC-IDRI-0984"
SERIES_DIR = r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI\LIDC-IDRI-0984\01-01-2000-NA-CT LUNG SCREEN-67378\3000566.000000-NA-xxxx"
XML_PATH = r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI\LIDC-IDRI-0984\01-01-2000-NA-CT LUNG SCREEN-67378\NA-76953\121.xml"
OUTPUT_ROOT = r"E:\肺部ct影像\output_marked_slices"
OUTPUT_DIR = os.path.join(OUTPUT_ROOT, PATIENT_ID)


def extract_namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[0][1:]
    return ""


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


def closest_slice_index(slice_z_positions: List[float], target_z: float) -> int:
    return min(
        range(len(slice_z_positions)),
        key=lambda i: abs(slice_z_positions[i] - target_z),
    )


def parse_annotations(xml_path: Path, slice_z_positions: List[float]) -> List[dict]:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    namespace = extract_namespace(root.tag)
    ns = {"ns": namespace} if namespace else {}

    malignancy_map: Dict[str, List[int]] = {}
    roi_entries: List[dict] = []

    reading_sessions = (
        root.findall(".//ns:readingSession", ns)
        if ns
        else root.findall(".//readingSession")
    )

    for session in reading_sessions:
        nodules = (
            session.findall("ns:unblindedReadNodule", ns)
            if ns
            else session.findall("unblindedReadNodule")
        )
        for nodule in nodules:
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
                continue

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
                    malignancy_map.setdefault(nodule_id, []).append(mal_val)

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

                if len(xs) < 3 or len(ys) < 3:
                    continue

                margin = 3.0
                x_min = min(xs) - margin
                x_max = max(xs) + margin
                y_min = min(ys) - margin
                y_max = max(ys) + margin

                slice_idx = closest_slice_index(slice_z_positions, roi_z)
                mal_list = malignancy_map.get(nodule_id, [])
                avg_malig = mean(mal_list) if mal_list else None

                roi_entries.append(
                    {
                        "nodule_id": nodule_id,
                        "slice_index": slice_idx,
                        "x_min": x_min,
                        "x_max": x_max,
                        "y_min": y_min,
                        "y_max": y_max,
                        "malignancies": mal_list,
                        "avg_malig": avg_malig,
                    }
                )

    return roi_entries


def draw_box(ax, entry: dict, width: int, height: int):
    x_min = max(0.0, min(entry["x_min"], width - 1))
    x_max = max(0.0, min(entry["x_max"], width - 1))
    y_min = max(0.0, min(entry["y_min"], height - 1))
    y_max = max(0.0, min(entry["y_max"], height - 1))

    rect = Rectangle(
        (x_min, y_min),
        x_max - x_min,
        y_max - y_min,
        linewidth=2,
        edgecolor="red",
        facecolor="none",
    )
    ax.add_patch(rect)


def main() -> None:
    series_dir = Path(SERIES_DIR)
    xml_path = Path(XML_PATH)
    output_dir = Path(OUTPUT_DIR)

    if not series_dir.exists():
        raise FileNotFoundError(f"Series directory not found: {series_dir}")
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    volume, slice_z_positions = load_dicom_series(series_dir)
    roi_entries = parse_annotations(xml_path, slice_z_positions)

    slices_to_draw: Dict[int, List[dict]] = {}
    for entry in roi_entries:
        slices_to_draw.setdefault(entry["slice_index"], []).append(entry)

    saved_indices: List[int] = []
    for slice_idx in sorted(slices_to_draw):
        slice_img = volume[slice_idx]
        height, width = slice_img.shape

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.imshow(slice_img, cmap="gray")

        for entry in slices_to_draw[slice_idx]:
            draw_box(ax, entry, width, height)

        ax.axis("off")
        plt.tight_layout()
        out_path = output_dir / f"slice_{slice_idx:03d}.png"
        plt.savefig(out_path, dpi=200)
        plt.close(fig)
        saved_indices.append(slice_idx)

    unique_nodules = {}
    for entry in roi_entries:
        nid = entry["nodule_id"]
        unique_nodules.setdefault(nid, {"slices": set(), "mal": entry["malignancies"]})
        unique_nodules[nid]["slices"].add(entry["slice_index"])

    print(f"Total unique nodules: {len(unique_nodules)}")
    print(f"Total ROI entries (boxes drawn): {len(roi_entries)}")

    for nid in sorted(unique_nodules):
        slices = sorted(unique_nodules[nid]["slices"])
        mal_scores = unique_nodules[nid]["mal"]
        avg_mal = mean(mal_scores) if mal_scores else None
        mal_str = ", ".join(str(m) for m in mal_scores) if mal_scores else "none"
        avg_str = f"{avg_mal:.2f}" if avg_mal is not None else "N/A"
        slice_list_str = ", ".join(str(s) for s in slices)
        print(
            f"Nodule {nid}: slices [{slice_list_str}], malignancy [{mal_str}], average {avg_str}"
        )

    if saved_indices:
        saved_list = ", ".join(str(i) for i in sorted(set(saved_indices)))
        print(f"Saved slice images for indices: {saved_list}")
    else:
        print("No slice images were saved.")


if __name__ == "__main__":
    main()
