# CT Module Overview

## What it does
- Processes LIDC-IDRI CT DICOM series + XML annotations to build patient-level summaries and risk labels (JSON + CSV). (evidence: build_patient_summary.py:14-17, 40, 256-258; batch_build_summaries.py:15-16, 306-308, 321-325; generate_labels.py:9-10, 92-94)
- Extracts CT nodule image patches (PNG) for training and evaluation. (evidence: make_nodule_patches.py:17-20, 57, 307-313; extract_precise_nodule_patches.py:15-18, 247-255; extract_patches_multislice.py:22-23, 369-371)
- Trains ResNet18-based classifiers and exports weights, then runs per-nodule malignancy predictions from patch datasets. (evidence: train_resnet_nodule_classifier.py:17-19, 213, 240-241; train_precise_nodule_classifier.py:17-19, 297-299; export_nodule_predictions.py:19-22, 93, 155, 203-205)
- Provides visualization utilities that save marked CT slices as PNGs. (evidence: mark_nodules_on_ct.py:16-19, 266; batch_mark_slices.py:15-16, 304-305)

## Folder structure
ct/
- batch_build_summaries.py
- batch_mark_slices.py
- best_resnet_nodule.pt
- best_resnet_nodule_precise.pt
- build_nodule_label_table.py
- build_patient_summary.py
- confusion_matrix.png
- CT_MODULE_DOC.md
- eval_precise_nodule_model.py
- evaluate_nodule_model.py
- export_nodule_predictions.py
- extract_nodules_from_xml.py
- extract_patches_multislice.py
- extract_precise_nodule_patches.py
- generate_labels.py
- labels.csv
- load_dicom_series.py
- make_nodule_patches.py
- mark_nodules_on_ct.py
- mark_nodules_on_ct_v2.py
- parse_lidc_xml.py
- roc_curve.png
- train_nodule_classifier.py
- train_precise_nodule_classifier.py
- train_resnet_nodule_classifier.py

## Main entrypoints
- Inference (patch-level to nodule-level CSV): export_nodule_predictions.py (MODEL_PATH/PATCH_ROOT/LABEL_CSV/OUTPUT_CSV are fixed constants; outputs CSV). (evidence: export_nodule_predictions.py:19-22, 66, 155, 203-205)
- Evaluation (precise patches): eval_precise_nodule_model.py (loads SAVE_BEST_MODEL and writes TEST_PRED_CSV). (evidence: eval_precise_nodule_model.py:32-33, 337, 419-421)
- Evaluation (patches + metrics plots): evaluate_nodule_model.py (loads MODEL_PATH and writes confusion_matrix.png/roc_curve.png). (evidence: evaluate_nodule_model.py:26, 267, 313, 329)
- Training (ResNet18, coarse patches): train_resnet_nodule_classifier.py (writes BEST_MODEL_PATH). (evidence: train_resnet_nodule_classifier.py:17-19, 213, 240-241)
- Training (ResNet18, precise patches): train_precise_nodule_classifier.py (writes SAVE_BEST_MODEL). (evidence: train_precise_nodule_classifier.py:17-19, 297-299)
- Data prep (single patient summary): build_patient_summary.py (writes *_summary.json). (evidence: build_patient_summary.py:14-17, 256-258)
- Data prep (batch summaries + labels): batch_build_summaries.py (writes *_summary.json and labels.csv). (evidence: batch_build_summaries.py:15-16, 306-308, 321-325)
- Data prep (nodule label table): build_nodule_label_table.py (writes nodule_labels.csv). (evidence: build_nodule_label_table.py:8-9, 52-53)
- Patch extraction: make_nodule_patches.py / extract_precise_nodule_patches.py / extract_patches_multislice.py. (evidence: make_nodule_patches.py:17-20, 307-313; extract_precise_nodule_patches.py:15-18, 247-255; extract_patches_multislice.py:22-23, 369-371)
- Visualization: mark_nodules_on_ct.py / mark_nodules_on_ct_v2.py / batch_mark_slices.py. (evidence: mark_nodules_on_ct.py:16-19, 266; mark_nodules_on_ct_v2.py:16-19, 267; batch_mark_slices.py:15-16, 304-305)
- Utility: load_dicom_series.py (reads *.dcm and shows a slice; installs missing packages). (evidence: load_dicom_series.py:7-8, 35, 45-47)

## Input formats & parameters
- DICOM series directory of *.dcm files.
  - Constants: SERIES_DIR/DICOM_DIR/LIDC_ROOT (script-level constants, not argparse). (evidence: build_patient_summary.py:15; load_dicom_series.py:8; make_nodule_patches.py:17)
  - DICOM read uses *.dcm glob. (evidence: build_patient_summary.py:40; load_dicom_series.py:45; make_nodule_patches.py:57)
- LIDC XML annotations.
  - Single-file XML_PATH or batch *.xml scan. (evidence: build_patient_summary.py:16; batch_build_summaries.py:15-16; generate_labels.py:80)
- Patch datasets (PNG).
  - Patch roots from PATCH_ROOT constants; scripts glob *.png. (evidence: export_nodule_predictions.py:20, 66; train_resnet_nodule_classifier.py:17, 77)
- Label tables (CSV).
  - LABEL_CSV or nodule_labels.csv used for training/inference. (evidence: export_nodule_predictions.py:21; train_resnet_nodule_classifier.py:18; train_precise_nodule_classifier.py:18)
- Summary JSON input for multislice patches.
  - Uses *_summary.json under SUMMARY_ROOT. (evidence: extract_patches_multislice.py:22, 287)

## Output formats & files
- Patient summary JSON: *_summary.json under OUTPUT_ROOT. (evidence: build_patient_summary.py:17, 256-258; batch_build_summaries.py:306-308)
- Patient-level labels CSV: labels.csv under OUTPUT_ROOT. (evidence: batch_build_summaries.py:321-325; generate_labels.py:92-101)
- Nodule label table: nodule_labels.csv under SUMMARY_ROOT. (evidence: build_nodule_label_table.py:8-9, 52-53)
- Patch images: PNG patches under OUTPUT_ROOT.
  - Coarse patches named like avg{score}_{patient}_{nodule}_sliceXXX.png. (evidence: make_nodule_patches.py:307-313)
  - Precise patches saved per slice with suffixes. (evidence: extract_precise_nodule_patches.py:247-255)
  - Multislice patches named {patient}_{nodule}_sXXX_vY.png. (evidence: extract_patches_multislice.py:369-371)
- Prediction CSVs:
  - nodule_predictions.csv (columns include patient_id, nodule_id, num_patches, avg_prob_malignant, max_prob_malignant, predicted_label, true_label). (evidence: export_nodule_predictions.py:185-187, 121-122, 203-205)
  - nodule_predictions_test_precise_nodule_level.csv from eval_precise_nodule_model.py. (evidence: eval_precise_nodule_model.py:33, 419-421)
- Evaluation plots: confusion_matrix.png and roc_curve.png. (evidence: evaluate_nodule_model.py:313, 329)
- Marked slice PNGs: written under OUTPUT_ROOT/OUTPUT_DIR. (evidence: mark_nodules_on_ct.py:16-19, 266; batch_mark_slices.py:15-16, 304-305)

## Model weights & configuration
- Weights are loaded from fixed MODEL_PATH/SAVE_BEST_MODEL constants (no CLI flags). (evidence: export_nodule_predictions.py:19, 155; eval_precise_nodule_model.py:32, 337)
- Training scripts save .pt checkpoints to BEST_MODEL_PATH / SAVE_BEST_MODEL. (evidence: train_resnet_nodule_classifier.py:17-19, 240-241; train_precise_nodule_classifier.py:17-19, 297-299)
- Architecture used in training/inference is ResNet18. (evidence: train_resnet_nodule_classifier.py:213; export_nodule_predictions.py:93)

## How to run (Windows)
Note: These scripts rely on hard-coded paths (MODEL_PATH/PATCH_ROOT/LABEL_CSV/LIDC_ROOT). Adjust the constants if your data is elsewhere. (evidence: export_nodule_predictions.py:19-22; batch_build_summaries.py:15-16)

```powershell
cd .\ct
python export_nodule_predictions.py
```

```powershell
cd .\ct
python batch_build_summaries.py
```

Dependencies inferred from imports:
- torch, torchvision, numpy, pandas, scikit-learn, Pillow (PIL), pydicom, matplotlib. (evidence: export_nodule_predictions.py:7-15; train_resnet_nodule_classifier.py:7-14; evaluate_nodule_model.py:7-16; build_patient_summary.py:9-10)
- skimage and tqdm for some patch extraction workflows. (evidence: extract_precise_nodule_patches.py:11; extract_patches_multislice.py:17-19)
- load_dicom_series.py can auto-install pydicom/SimpleITK/matplotlib/numpy. (evidence: load_dicom_series.py:7, 35)

## Notes / gaps / next steps
- There is no CLI/argparse-based inference entrypoint; paths are module-level constants, so production integration needs a wrapper that accepts runtime inputs. (evidence: export_nodule_predictions.py:19-22; train_resnet_nodule_classifier.py:17-19)
- No script directly consumes a DICOM series and outputs predictions in one step; the current flow is DICOM/XML -> patch extraction -> model inference -> CSV. (evidence: make_nodule_patches.py:57; export_nodule_predictions.py:66, 203-205)
- Suggested minimal wrapper (not implemented here): add an infer_ct.py that takes a DICOM folder + XML or summary JSON, runs patch extraction, then calls the prediction export, returning a JSON response derived from the CSV columns.

If integrating with doctor/ct.html "Run AI analysis", the closest entrypoint to wrap is export_nodule_predictions.py; a reasonable JSON schema mirrors its CSV columns: {studyId, predictions:[{patient_id, nodule_id, num_patches, avg_prob_malignant, max_prob_malignant, predicted_label, true_label}]}. (evidence: export_nodule_predictions.py:185-187, 121-122, 203-205)
