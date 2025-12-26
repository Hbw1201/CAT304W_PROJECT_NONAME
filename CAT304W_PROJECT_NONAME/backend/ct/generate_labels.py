from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROOT_DIR = Path(r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI")
OUTPUT_CSV = Path(__file__).with_name("labels.csv")


def extract_namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[0][1:]
    return ""


def find_patient_id(path: Path) -> Optional[str]:
    for part in reversed(path.parts):
        if part.startswith("LIDC-IDRI-"):
            return part
    return None


def parse_xml_file(xml_path: Path) -> Optional[Tuple[str, int, List[int]]]:
    patient_id = find_patient_id(xml_path.parent)
    if not patient_id:
        return None

    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return None

    root = tree.getroot()
    namespace = extract_namespace(root.tag)
    ns = {"ns": namespace} if namespace else {}

    nodules = root.findall(".//ns:unblindedReadNodule", ns)
    num_nodules = len(nodules)
    malignancies: List[int] = []

    for nodule in nodules:
        characteristics = nodule.find("ns:characteristics", ns)
        if characteristics is None:
            continue
        mal = characteristics.find("ns:malignancy", ns)
        if mal is None or mal.text is None:
            continue
        try:
            malignancies.append(int(mal.text))
        except ValueError:
            continue

    if not malignancies and num_nodules == 0:
        # Skip XMLs without usable data.
        return None

    if not malignancies and num_nodules > 0:
        # XML with nodules but no malignancy scores: skip as requested.
        return None

    return patient_id, num_nodules, malignancies


def label_from_scores(num_nodules: int, max_malignancy: int) -> str:
    if num_nodules == 0:
        return "normal"
    if max_malignancy >= 4:
        return "high_risk"
    if max_malignancy == 3:
        return "medium_risk"
    return "low_risk"


def main() -> None:
    patient_stats: Dict[str, Dict[str, int | List[int]]] = {}

    for xml_path in ROOT_DIR.rglob("*.xml"):
        parsed = parse_xml_file(xml_path)
        if not parsed:
            continue
        patient_id, num_nodules, malignancies = parsed

        stats = patient_stats.setdefault(
            patient_id, {"num_nodules": 0, "malignancies": []}
        )
        stats["num_nodules"] += num_nodules
        stats["malignancies"].extend(malignancies)

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["patient_id", "num_nodules", "max_malignancy", "label"])

        for patient_id in sorted(patient_stats):
            stats = patient_stats[patient_id]
            num_nodules = int(stats["num_nodules"])
            max_mal = max(stats["malignancies"], default=0)  # type: ignore[arg-type]
            label = label_from_scores(num_nodules, max_mal)
            writer.writerow([patient_id, num_nodules, max_mal, label])

    print("Done")


if __name__ == "__main__":
    main()
