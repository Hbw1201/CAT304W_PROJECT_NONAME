from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean


XML_PATH = Path(
    r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI\LIDC-IDRI-0984\01-01-2000-NA-CT LUNG SCREEN-67378\NA-76953\121.xml"
)


def extract_namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[0][1:]
    return ""


def get_malignancy_scores(nodule, ns: dict[str, str]) -> list[float]:
    scores = []
    for m in nodule.findall(".//ns:malignancy", ns):
        try:
            scores.append(float(m.text))
        except (TypeError, ValueError):
            continue
    return scores


def main() -> None:
    if not XML_PATH.exists():
        raise FileNotFoundError(f"XML file not found: {XML_PATH}")

    tree = ET.parse(XML_PATH)
    root = tree.getroot()

    namespace = extract_namespace(root.tag)
    ns = {"ns": namespace} if namespace else {}

    nodules = root.findall(".//ns:readingSession/ns:unblindedReadNodule", ns)
    non_nodules = root.findall(".//ns:readingSession/ns:nonNodule", ns)

    print(f"Number of nodules: {len(nodules)}")
    print(f"Number of non-nodules: {len(non_nodules)}")

    for idx, nodule in enumerate(nodules, start=1):
        nodule_id_elem = nodule.find("ns:noduleID", ns)
        nodule_id = nodule_id_elem.text if nodule_id_elem is not None else f"#{idx}"

        scores = get_malignancy_scores(nodule, ns)
        avg_score = mean(scores) if scores else None

        if scores:
            score_str = ", ".join(f"{s:g}" for s in scores)
        else:
            score_str = "none"

        avg_str = f"{avg_score:g}" if avg_score is not None else "N/A"
        print(f"Nodule {nodule_id}: malignancy scores [{score_str}], average {avg_str}")


if __name__ == "__main__":
    main()
