from __future__ import annotations

import csv
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Tuple

import numpy as np
import pydicom

# User-editable roots
LIDC_ROOT = r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI"
OUTPUT_ROOT = r"E:\肺部ct影像\patient_summaries"


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


def parse_patient_nodules(xml_path: Path, slice_z_positions: List[float]) -> List[dict]:
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

    nodule_list = []
    for nodule_id in sorted(nodules):
        data = nodules[nodule_id]
        z_vals: List[float] = data["z"]  # type: ignore[assignment]
        x_vals: List[float] = data["x"]  # type: ignore[assignment]
        y_vals: List[float] = data["y"]  # type: ignore[assignment]
        malignancies: List[int] = data["malignancies"]  # type: ignore[assignment]

        center_z = mean(z_vals) if z_vals else None
        center_x = mean(x_vals) if x_vals else None
        center_y = mean(y_vals) if y_vals else None
        avg_mal = mean(malignancies) if malignancies else None

        slice_idx = (
            closest_slice_index(slice_z_positions, center_z)
            if center_z is not None
            else None
        )

        nodule_list.append(
            {
                "nodule_id": nodule_id,
                "slice_index": slice_idx,
                "center": {"z": center_z, "y": center_y, "x": center_x},
                "malignancy_scores": malignancies,
                "avg_malignancy": avg_mal,
            }
        )

    return nodule_list


def find_series_dir(patient_dir: Path, min_dcm: int = 10) -> Optional[Path]:
    candidates = []
    for sub in patient_dir.rglob("*"):
        if not sub.is_dir():
            continue
        dcm_files = list(sub.glob("*.dcm"))
        if len(dcm_files) >= min_dcm:
            candidates.append((sub, len(dcm_files)))

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            "ct" not in str(item[0]).lower(),  # False preferred
            -item[1],  # more files preferred
        )
    )
    return candidates[0][0]


def find_xml_file(patient_dir: Path) -> Optional[Path]:
    for xml_path in patient_dir.rglob("*.xml"):
        if xml_path.is_file():
            return xml_path
    return None


def build_patient_summary(patient_id: str, patient_dir: Path) -> Optional[dict]:
    xml_path = find_xml_file(patient_dir)
    if not xml_path:
        print(f"[WARN] No XML found for {patient_id}")
        return None

    series_dir = find_series_dir(patient_dir)
    if not series_dir:
        print(f"[WARN] No DICOM series found for {patient_id}")
        return None

    try:
        _, slice_z_positions = load_dicom_series(series_dir)
    except FileNotFoundError as exc:
        print(f"[WARN] {exc}")
        return None

    nodule_list = parse_patient_nodules(xml_path, slice_z_positions)

    num_nodules = len(nodule_list)
    max_malig_candidates = [
        n["avg_malignancy"] for n in nodule_list if n["avg_malignancy"] is not None
    ]
    max_malignancy = max(max_malig_candidates) if max_malig_candidates else 0

    if num_nodules == 0:
        risk_level = "normal"
    elif max_malignancy >= 4:
        risk_level = "high_risk"
    elif max_malignancy == 3:
        risk_level = "medium_risk"
    else:
        risk_level = "low_risk"

    return {
        "patient_id": patient_id,
        "series_dir": str(series_dir),
        "xml_path": str(xml_path),
        "num_nodules": num_nodules,
        "max_malignancy": max_malignancy,
        "risk_level": risk_level,
        "nodules": nodule_list,
    }


def main() -> None:
    lidc_root = Path(LIDC_ROOT)
    output_root = Path(OUTPUT_ROOT)

    if not lidc_root.exists():
        raise FileNotFoundError(f"LIDC root not found: {lidc_root}")

    output_root.mkdir(parents=True, exist_ok=True)

    patient_dirs = [
        d for d in lidc_root.iterdir() if d.is_dir() and d.name.startswith("LIDC-IDRI-")
    ]

    labels_rows = []
    summaries_created = 0

    for patient_dir in sorted(patient_dirs):
        patient_id = patient_dir.name
        summary = build_patient_summary(patient_id, patient_dir)
        if summary is None:
            continue

        summary_path = output_root / f"{patient_id}_summary.json"
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        labels_rows.append(
            [
                patient_id,
                summary["num_nodules"],
                summary["max_malignancy"],
                summary["risk_level"],
            ]
        )
        summaries_created += 1
        print(f"[INFO] Saved summary for {patient_id} -> {summary_path}")

    labels_path = output_root / "labels.csv"
    with labels_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["patient_id", "num_nodules", "max_malignancy", "risk_level"])
        writer.writerows(labels_rows)

    print(f"Total patients found: {len(patient_dirs)}")
    print(f"Summaries created: {summaries_created}")
    print(f"labels.csv saved to: {labels_path}")


if __name__ == "__main__":
    main()
