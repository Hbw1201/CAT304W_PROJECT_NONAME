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


def to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def main() -> None:
    if not XML_PATH.exists():
        raise FileNotFoundError(f"XML file not found: {XML_PATH}")

    tree = ET.parse(XML_PATH)
    root = tree.getroot()

    namespace = extract_namespace(root.tag)
    ns = {"ns": namespace} if namespace else {}

    nodules = {}

    reading_sessions = root.findall(".//ns:readingSession", ns) if ns else root.findall(
        ".//readingSession"
    )

    for session in reading_sessions:
        nodule_nodes = (
            session.findall("ns:unblindedReadNodule", ns)
            if ns
            else session.findall("unblindedReadNodule")
        )
        for nodule_node in nodule_nodes:
            nodule_id_elem = (
                nodule_node.find("ns:noduleID", ns)
                if ns
                else nodule_node.find("noduleID")
            )
            nodule_id = (
                nodule_id_elem.text.strip()
                if nodule_id_elem is not None and nodule_id_elem.text
                else "unknown"
            )

            entry = nodules.setdefault(
                nodule_id,
                {"z": [], "x": [], "y": [], "roi_count": 0, "malignancies": []},
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
                    entry["malignancies"].append(mal_val)

            rois = (
                nodule_node.findall("ns:roi", ns)
                if ns
                else nodule_node.findall("roi")
            )
            for roi in rois:
                entry["roi_count"] += 1

                z_elem = (
                    roi.find("ns:imageZposition", ns)
                    if ns
                    else roi.find("imageZposition")
                )
                z_val = to_float(z_elem.text) if z_elem is not None else None
                if z_val is not None:
                    entry["z"].append(z_val)

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
                        entry["x"].append(x_val)
                    if y_val is not None:
                        entry["y"].append(y_val)

    print(f"Total unique nodules: {len(nodules)}")
    for nodule_id in sorted(nodules):
        data = nodules[nodule_id]

        center_z = mean(data["z"]) if data["z"] else None
        center_x = mean(data["x"]) if data["x"] else None
        center_y = mean(data["y"]) if data["y"] else None

        malignancies = data["malignancies"]
        avg_mal = mean(malignancies) if malignancies else None

        if center_z is not None and center_y is not None and center_x is not None:
            center_str = f"({center_z:.3f}, {center_y:.3f}, {center_x:.3f})"
        else:
            center_str = "N/A"

        mal_list = ", ".join(str(m) for m in malignancies) if malignancies else "none"
        avg_mal_str = f"{avg_mal:.2f}" if avg_mal is not None else "N/A"

        print(
            f"Nodule {nodule_id}: roi slices {data['roi_count']}, "
            f"center {center_str}, malignancy scores [{mal_list}], average {avg_mal_str}"
        )


if __name__ == "__main__":
    main()
