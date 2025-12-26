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

# Update these paths and IDs to point at the desired patient/series.
PATIENT_ID = "LIDC-IDRI-0984"  # the patient this series/xml belongs to
OUTPUT_ROOT = r"E:\肺部ct影像\output_marked_slices"
SERIES_DIR = r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI\LIDC-IDRI-0001\01-01-2000-NA-NA-30178\3000566.000000-NA-03192"
XML_PATH = r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI\LIDC-IDRI-0984\01-01-2000-NA-CT LUNG SCREEN-67378\NA-76953\121.xml"
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
        for nodule_node in nodule_nodes:
            nodule_id = nodule_node.get("noduleID")
            if not nodule_id:
                nodule_id_elem = (
                    nodule_node.find("ns:noduleID", ns)
                    if ns
                    else nodule_node.find("noduleID")
                )
                if nodule_id_elem is not None and nodule_id_elem.text:
                    nodule_id = nodule_id_elem.text.strip()
            if not nodule_id:
                nodule_id = "unknown"

            entry = nodules.setdefault(
                nodule_id,
                {"z": [], "x": [], "y": [], "malignancies": []},
            )

            characteristics = (
                nodule_node.find("ns:characteristics", ns)
                if ns
                else nodule_node.find("characteristics")
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
                nodule_node.findall("ns:roi", ns)
                if ns
                else nodule_node.findall("roi")
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


def closest_slice_index(slice_z_positions: List[float], target_z: float) -> int:
    return min(
        range(len(slice_z_positions)),
        key=lambda i: abs(slice_z_positions[i] - target_z),
    )


def main() -> None:
    series_dir = Path(SERIES_DIR)
    xml_path = Path(XML_PATH)
    output_dir = Path(OUTPUT_DIR)

    if not series_dir.exists():
        raise FileNotFoundError(f"Series directory not found: {series_dir}")
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    volume, slice_z_positions = load_dicom_series(series_dir)
    nodules_raw = parse_nodules(xml_path)

    nodule_summaries = []
    for nodule_id, data in nodules_raw.items():
        z_vals: List[float] = data["z"]  # type: ignore[assignment]
        x_vals: List[float] = data["x"]  # type: ignore[assignment]
        y_vals: List[float] = data["y"]  # type: ignore[assignment]
        malignancies: List[int] = data["malignancies"]  # type: ignore[assignment]

        center_z = mean(z_vals) if z_vals else None
        center_x = mean(x_vals) if x_vals else None
        center_y = mean(y_vals) if y_vals else None

        slice_index = (
            closest_slice_index(slice_z_positions, center_z)
            if center_z is not None
            else None
        )

        avg_mal = mean(malignancies) if malignancies else None

        nodule_summaries.append(
            {
                "nodule_id": nodule_id,
                "slice_index": slice_index,
                "center": (center_z, center_y, center_x),
                "malignancies": malignancies,
                "avg_malignancy": avg_mal,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    half_size = 20
    slices_to_draw: Dict[int, List[dict]] = {}
    for summary in nodule_summaries:
        idx = summary["slice_index"]
        if idx is None:
            continue
        slices_to_draw.setdefault(idx, []).append(summary)

    saved_indices = []
    for idx in sorted(slices_to_draw):
        fig, ax = plt.subplots(figsize=(6, 6))
        slice_img = volume[idx]
        ax.imshow(slice_img, cmap="gray")
        height, width = slice_img.shape

        for nodule in slices_to_draw[idx]:
            _, center_y, center_x = nodule["center"]
            if center_x is None or center_y is None:
                continue
            left = center_x - half_size
            top = center_y - half_size
            rect = Rectangle(
                (left, top),
                2 * half_size,
                2 * half_size,
                linewidth=1.5,
                edgecolor="red",
                facecolor="none",
            )
            ax.add_patch(rect)
            

        ax.axis("off")
        plt.tight_layout()
        out_path = output_dir / f"slice_{idx:03d}.png"
        plt.savefig(out_path, dpi=200)
        plt.close(fig)
        saved_indices.append(idx)

    print(f"Total unique nodules: {len(nodule_summaries)}")
    for summary in sorted(nodule_summaries, key=lambda s: s["nodule_id"]):
        cz, cy, cx = summary["center"]
        center_str = (
            f"({cz:.3f}, {cy:.3f}, {cx:.3f})"
            if cz is not None and cy is not None and cx is not None
            else "N/A"
        )
        mal_scores = summary["malignancies"]
        mal_str = ", ".join(str(m) for m in mal_scores) if mal_scores else "none"
        avg_mal = summary["avg_malignancy"]
        avg_str = f"{avg_mal:.2f}" if avg_mal is not None else "N/A"
        print(
            f"Nodule {summary['nodule_id']}: slice {summary['slice_index']}, "
            f"center {center_str}, malignancy [{mal_str}], average {avg_str}"
        )

    if saved_indices:
        saved_list = ", ".join(str(i) for i in sorted(saved_indices))
        print(f"Saved slice images for indices: {saved_list}")
    else:
        print("No slice images were saved (no matching nodules with slice indices).")


if __name__ == "__main__":
    main()
