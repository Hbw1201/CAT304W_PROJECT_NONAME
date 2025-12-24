from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from export_nodule_predictions import run_export  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CT nodule inference from patch images.")
    parser.add_argument("--patch_root", required=True, help="Folder containing PNG patch images.")
    parser.add_argument(
        "--model_path",
        default=str(BASE_DIR / "best_resnet_nodule.pt"),
        help="Path to .pt model weights (default: ct/best_resnet_nodule.pt).",
    )
    parser.add_argument(
        "--label_csv",
        default="",
        help="Optional label CSV. If missing, true_label is null in output.",
    )
    parser.add_argument("--out_json", required=True, help="Output JSON path.")
    parser.add_argument(
        "--out_csv",
        default="",
        help="Optional output CSV path (default: temp file).",
    )
    parser.add_argument("--top_k", type=int, default=20, help="Top-K predictions to include.")
    return parser.parse_args()


def to_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_summary(df: pd.DataFrame) -> str:
    if df.empty:
        return "No predictions generated from patch dataset."
    max_prob = to_float(df["max_prob_malignant"].max(), 0.0)
    malignant_count = int((df["predicted_label"] == 1).sum())
    total = len(df)
    return (
        f"Max malignancy probability {max_prob:.3f} across {total} nodules; "
        f"predicted malignant: {malignant_count}."
    )


def normalize_predictions(df: pd.DataFrame, top_k: int) -> list[Dict[str, Any]]:
    if df.empty:
        return []
    sort_cols = ["max_prob_malignant", "avg_prob_malignant"]
    df_sorted = df.sort_values(by=sort_cols, ascending=False).head(max(top_k, 0))
    rows = []
    for row in df_sorted.itertuples(index=False):
        rows.append(
            {
                "patient_id": str(row.patient_id),
                "nodule_id": str(row.nodule_id),
                "num_patches": int(row.num_patches),
                "avg_prob_malignant": to_float(row.avg_prob_malignant),
                "max_prob_malignant": to_float(row.max_prob_malignant),
                "predicted_label": int(row.predicted_label),
                "true_label": to_optional_int(row.true_label),
            }
        )
    return rows


def main() -> None:
    args = parse_args()
    patch_root = Path(args.patch_root)
    model_path = Path(args.model_path)
    label_csv = Path(args.label_csv) if args.label_csv else None
    out_json = Path(args.out_json)

    if args.out_csv:
        out_csv = Path(args.out_csv)
    else:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        tmp.close()
        out_csv = Path(tmp.name)

    df = run_export(model_path, patch_root, label_csv, out_csv)
    result = {
        "model": "ResNet18_nodule_classifier",
        "model_path": str(model_path),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "patch_root": str(patch_root),
        "summary": build_summary(df),
        "predictions": normalize_predictions(df, args.top_k),
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] Saved JSON to {out_json}")


if __name__ == "__main__":
    main()
