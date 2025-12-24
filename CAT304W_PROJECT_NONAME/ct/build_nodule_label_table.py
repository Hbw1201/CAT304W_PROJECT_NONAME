from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SUMMARY_ROOT = Path(r"E:\肺部ct影像\patient_summaries")
OUTPUT_CSV = SUMMARY_ROOT / "nodule_labels.csv"


def main() -> None:
    if not SUMMARY_ROOT.exists():
        raise FileNotFoundError(f"Summary root not found: {SUMMARY_ROOT}")

    rows = []
    patient_count = 0
    malignant_count = 0
    benign_count = 0

    summary_files = sorted(SUMMARY_ROOT.glob("*_summary.json"))
    for fpath in summary_files:
        with fpath.open("r", encoding="utf-8") as f:
            data = json.load(f)

        patient_id = data.get("patient_id") or fpath.stem.replace("_summary", "")
        nodules = data.get("nodules", [])
        valid_found = False

        for nod in nodules:
            nodule_id = nod.get("nodule_id")
            avg = nod.get("avg_malignancy")
            if avg is None:
                continue
            label = 1 if float(avg) >= 4 else 0
            rows.append(
                {
                    "patient_id": patient_id,
                    "nodule_id": nodule_id,
                    "avg_malignancy": float(avg),
                    "is_malignant": label,
                }
            )
            malignant_count += 1 if label == 1 else 0
            benign_count += 1 if label == 0 else 0
            valid_found = True

        if valid_found:
            patient_count += 1

    df = pd.DataFrame(rows, columns=["patient_id", "nodule_id", "avg_malignancy", "is_malignant"])
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)

    print(f"Patients processed (with at least one labeled nodule): {patient_count}")
    print(f"Total nodules with valid labels: {len(rows)}")
    print(f"Malignant (>=4): {malignant_count}")
    print(f"Benign (<4): {benign_count}")
    print(f"Saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
