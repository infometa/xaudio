# xaudio - Exploded Diagram Labeling

This project detects and recognizes part labels (numbers, circled numbers, and alphanumeric labels like `4-A`) in exploded-view diagrams.

## Features
- **Two-stage pipeline**: YOLO detector for label bounding boxes + OCR for text.
- Handles **varying image resolutions**.
- Supports **circled labels** and **plain labels**.
- Normalizes OCR output for labels such as `4-A`, `12`, `A-7`.

## Quick Start

### 1) Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src
```

### 2) Train detector (bounding boxes only)
Prepare a YOLO dataset with a single class: `label`.

```yaml
# dataset.yaml
path: /path/to/dataset
train: images/train
val: images/val
names:
  0: label
```

Train:
```bash
python scripts/train_detector.py --data dataset.yaml --epochs 100 --imgsz 1024 --device cuda
```

### 3) Run inference
```bash
python -m labeler.cli \
  --image /path/to/image.png \
  --detector /path/to/best.pt \
  --output predictions.json \
  --viz predictions.png
```

Output format:
```json
[
  {"label": "12", "confidence": 0.92, "bbox": [x1, y1, x2, y2]},
  {"label": "4-A", "confidence": 0.87, "bbox": [x1, y1, x2, y2]}
]
```

## Labeling Guidelines
- Annotate **each complete label** as one box (e.g., `12`, `4-A`).
- Include the circle around numbers if present.
- Keep tight boxes around text but ensure the label is fully contained.

## Notes
- If OCR is inaccurate, refine the detector and expand the training set.
- You can swap OCR models later if you already have a better recognizer.

## Project Structure
```
src/labeler/
  cli.py        # CLI entrypoint
  pipeline.py   # Detection + OCR pipeline
  preprocess.py # Image enhancement for OCR
  postprocess.py# OCR label normalization
scripts/
  train_detector.py
```
