from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Tuple

import numpy as np
import pydicom

# User-editable constants
PATIENT_ID = "LIDC-IDRI-0984"
SERIES_DIR = r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI\LIDC-IDRI-0984\01-01-2000-NA-CT LUNG SCREEN-67378\NA-76953"
XML_PATH = r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI\LIDC-IDRI-0984\01-01-2000-NA-CT LUNG SCREEN-67378\NA-76953\121.xml"
OUTPUT_ROOT = r"E:\肺部ct影像\patient_summaries"


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


def main() -> None:
    series_dir = Path(SERIES_DIR)
    xml_path = Path(XML_PATH)
    output_root = Path(OUTPUT_ROOT)

    if not series_dir.exists():
        raise FileNotFoundError(f"Series directory not found: {series_dir}")
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    output_root.mkdir(parents=True, exist_ok=True)

    _, slice_z_positions = load_dicom_series(series_dir)
    raw_nodules = parse_nodules(xml_path)

    nodule_summaries = []
    for nodule_id in sorted(raw_nodules):
        data = raw_nodules[nodule_id]
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

        nodule_summaries.append(
            {
                "nodule_id": nodule_id,
                "slice_index": slice_idx,
                "center": {
                    "z": center_z,
                    "y": center_y,
                    "x": center_x,
                },
                "malignancy_scores": malignancies,
                "avg_malignancy": avg_malig,
            }
        )

    num_nodules = len(nodule_summaries)
    max_malignancy_candidates = [
        n["avg_malignancy"] for n in nodule_summaries if n["avg_malignancy"] is not None
    ]
    max_malignancy = max(max_malignancy_candidates) if max_malignancy_candidates else 0

    if num_nodules == 0:
        risk_level = "normal"
    elif max_malignancy >= 4:
        risk_level = "high_risk"
    elif max_malignancy == 3:
        risk_level = "medium_risk"
    else:
        risk_level = "low_risk"

    summary = {
        "patient_id": PATIENT_ID,
        "series_dir": str(SERIES_DIR),
        "xml_path": str(XML_PATH),
        "num_nodules": num_nodules,
        "max_malignancy": max_malignancy,
        "risk_level": risk_level,
        "nodules": nodule_summaries,
    }

    summary_path = Path(OUTPUT_ROOT) / f"{PATIENT_ID}_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"Saved patient summary to: {summary_path}")
    print(f"patient_id: {PATIENT_ID}")
    print(f"num_nodules: {num_nodules}")
    print(f"max_malignancy: {max_malignancy}")
    print(f"risk_level: {risk_level}")


if __name__ == "__main__":
    main()
