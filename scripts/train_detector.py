from __future__ import annotations

import argparse
from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train YOLO detector for label boxes")
    parser.add_argument("--data", required=True, help="Path to YOLO dataset yaml")
    parser.add_argument("--model", default="yolov8s.pt", help="Base model")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--project", default="runs/label-detector")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        device=args.device,
        project=args.project,
    )


if __name__ == "__main__":
    main()
