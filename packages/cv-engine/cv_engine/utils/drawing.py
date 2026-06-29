"""Non-mutating OpenCV overlay functions."""

from __future__ import annotations

from collections.abc import Mapping

import cv2
import numpy as np

from ..pipeline.zone_analyzer import ZoneAnalyzer
from ..types import Detection, FaceMatch, OverlayData, TrackedObject, Zone

Color = tuple[int, int, int]
DEFAULT_COLORS: dict[int, Color] = {0: (40, 210, 80), 1: (0, 215, 255), 2: (0, 215, 255), 3: (0, 165, 255)}


def draw_detections(
    frame: np.ndarray,
    detections: list[Detection] | tuple[Detection, ...],
    colors: Mapping[int, Color] | None = None,
) -> np.ndarray:
    output = frame.copy()
    palette = colors or DEFAULT_COLORS
    for detection in detections:
        color = palette.get(detection.class_id, (220, 220, 220))
        _draw_box(output, detection.bbox, f"{detection.class_name} {detection.confidence:.2f}", color)
    return output


def draw_tracks(frame: np.ndarray, tracks: list[TrackedObject] | tuple[TrackedObject, ...]) -> np.ndarray:
    output = frame.copy()
    for item in tracks:
        color = DEFAULT_COLORS.get(item.class_id, (220, 220, 220))
        _draw_box(output, item.bbox, f"{item.class_name} #{item.track_id}", color)
    return output


def draw_zones(frame: np.ndarray, zones: list[Zone] | tuple[Zone, ...]) -> np.ndarray:
    output = frame.copy()
    for zone in zones:
        points = ZoneAnalyzer.pixel_vertices(zone, output.shape).astype(np.int32)
        cv2.polylines(output, [points], True, zone.color, 2, cv2.LINE_AA)
        anchor = tuple(int(value) for value in points[0])
        cv2.putText(output, zone.name, anchor, cv2.FONT_HERSHEY_SIMPLEX, 0.55, zone.color, 2, cv2.LINE_AA)
    return output


def draw_people_count(frame: np.ndarray, count_in: int, count_out: int) -> np.ndarray:
    output = frame.copy()
    text = f"IN {count_in} | OUT {count_out}"
    cv2.rectangle(output, (8, 8), (225, 42), (15, 15, 15), -1)
    cv2.putText(output, text, (16, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (245, 245, 245), 2, cv2.LINE_AA)
    return output


def draw_face_labels(frame: np.ndarray, matches: list[FaceMatch] | tuple[FaceMatch, ...]) -> np.ndarray:
    output = frame.copy()
    for match in matches:
        color = (40, 210, 80) if match.person_id else (0, 165, 255)
        _draw_box(output, match.bbox, f"{match.name} {match.confidence:.2f}", color)
    return output


def render_overlay(frame: np.ndarray, overlay: OverlayData) -> np.ndarray:
    output = draw_zones(frame, overlay.zones)
    output = draw_tracks(output, overlay.tracks)
    output = draw_face_labels(output, overlay.face_matches)
    return draw_people_count(output, overlay.count_in, overlay.count_out)


def _draw_box(frame: np.ndarray, bbox: tuple[float, float, float, float], label: str, color: Color) -> None:
    x1, y1, x2, y2 = (int(round(value)) for value in bbox)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    (width, height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    top = max(0, y1 - height - baseline - 4)
    cv2.rectangle(frame, (x1, top), (x1 + width + 6, y1), color, -1)
    cv2.putText(frame, label, (x1 + 3, y1 - baseline - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (10, 10, 10), 1, cv2.LINE_AA)
