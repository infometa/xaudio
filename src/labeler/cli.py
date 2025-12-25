from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from .pipeline import LabelPipeline, draw_predictions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exploded-view label detection + OCR")
    parser.add_argument("--image", required=True, help="Path to input image")
    parser.add_argument("--detector", required=True, help="Path to YOLO detector weights")
    parser.add_argument("--output", default="predictions.json", help="Output JSON path")
    parser.add_argument("--device", default="cpu", help="Device for inference (cpu/cuda)")
    parser.add_argument("--conf", type=float, default=0.25, help="Detection confidence")
    parser.add_argument("--iou", type=float, default=0.45, help="Detection IOU threshold")
    parser.add_argument("--viz", default=None, help="Optional visualization output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(image_path)

    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Failed to read image: {image_path}")

    pipeline = LabelPipeline(
        detector_path=args.detector,
        device=args.device,
        det_conf=args.conf,
        det_iou=args.iou,
    )
    predictions = pipeline.predict(image)

    output_data = [
        {
            "label": pred.label,
            "confidence": round(pred.confidence, 4),
            "bbox": pred.bbox,
        }
        for pred in predictions
    ]
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(output_data, handle, ensure_ascii=False, indent=2)

    if args.viz:
        viz = draw_predictions(image, predictions)
        cv2.imwrite(args.viz, viz)


if __name__ == "__main__":
    main()
