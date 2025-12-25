from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

import cv2
import numpy as np
from paddleocr import PaddleOCR
from ultralytics import YOLO

from .postprocess import normalize_label
from .preprocess import enhance_for_ocr


@dataclass
class LabelPrediction:
    label: str
    confidence: float
    bbox: List[int]


class LabelPipeline:
    def __init__(
        self,
        detector_path: str,
        device: str = "cpu",
        ocr_lang: str = "en",
        det_conf: float = 0.25,
        det_iou: float = 0.45,
    ) -> None:
        self.detector = YOLO(detector_path)
        self.det_conf = det_conf
        self.det_iou = det_iou
        self.device = device

        self.ocr = PaddleOCR(
            lang=ocr_lang,
            use_angle_cls=False,
            show_log=False,
            rec_char_type="en",
        )

    def predict(self, image: np.ndarray) -> List[LabelPrediction]:
        if image is None:
            raise ValueError("Empty image input.")

        results = self.detector.predict(
            image,
            conf=self.det_conf,
            iou=self.det_iou,
            device=self.device,
            verbose=False,
        )

        predictions: List[LabelPrediction] = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
                conf = float(box.conf[0])

                crop = image[max(y1, 0): max(y2, 0), max(x1, 0): max(x2, 0)]
                if crop.size == 0:
                    continue

                processed = enhance_for_ocr(crop)
                ocr_result = self.ocr.ocr(processed, cls=False)
                text = ""
                score = 0.0

                if ocr_result:
                    lines = ocr_result[0]
                    if lines:
                        text, score = lines[0][1]

                label = normalize_label(text)
                predictions.append(
                    LabelPrediction(
                        label=label,
                        confidence=min(conf, float(score)),
                        bbox=[x1, y1, x2, y2],
                    )
                )

        return predictions


def draw_predictions(image: np.ndarray, predictions: Iterable[LabelPrediction]) -> np.ndarray:
    canvas = image.copy()
    for pred in predictions:
        x1, y1, x2, y2 = pred.bbox
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 153, 255), 2)
        label = f"{pred.label} ({pred.confidence:.2f})"
        cv2.putText(
            canvas,
            label,
            (x1, max(0, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 153, 255),
            2,
        )
    return canvas
