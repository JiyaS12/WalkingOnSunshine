"""Video upload processing for Sana.

Runs MediaPipe Pose (legacy solutions API) over an uploaded video and returns
joint frames compatible with GaitProcessor (6 joints, y flipped so up is
positive — same convention the frontend uses for live camera input).
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import cv2
import mediapipe as mp
from mediapipe.framework.formats.landmark_pb2 import LandmarkList

# pose landmark indices -> joint names (same mapping as the frontend)
_LANDMARK_JOINTS = {
    11: "left_shoulder",
    12: "right_shoulder",
    23: "left_hip",
    24: "right_hip",
    25: "left_knee",
    26: "right_knee",
    27: "left_ankle",
    28: "right_ankle",
    29: "left_heel",
    30: "right_heel",
    31: "left_foot_index",
    32: "right_foot_index",
}

Frame = dict[str, list[float]]


class VideoDecodeError(ValueError):
    """The uploaded file could not be decoded as a video."""

# lowest sample rate the gait metrics stay meaningful at (steps reach ~4/s)
_MIN_ANALYSIS_FPS = 15.0


def _fill_gaps(detections: list[tuple[int, Frame]]) -> list[Frame]:
    """Drop leading/trailing undetected slots and linearly interpolate
    interior gaps so the result has exactly one frame per sampled slot
    between the first and last detection."""
    first, last = detections[0][0], detections[-1][0]
    by_slot = dict(detections)
    coverage = len(detections) / (last - first + 1)
    if coverage < 0.5:
        raise ValueError(
            f"pose was only detected in {round(coverage * 100)}% of frames "
            "— keep the full body in view"
        )
    frames: list[Frame] = []
    i = 0  # index into detections
    for slot in range(first, last + 1):
        if slot in by_slot:
            frames.append(by_slot[slot])
            while i < len(detections) and detections[i][0] <= slot:
                i += 1
            continue
        prev_slot, prev_frame = detections[i - 1]
        next_slot, next_frame = detections[i]
        t = (slot - prev_slot) / (next_slot - prev_slot)
        frames.append(
            {
                joint: [
                    prev_frame[joint][k] + t * (next_frame[joint][k] - prev_frame[joint][k])
                    for k in range(len(prev_frame[joint]))
                ]
                for joint in prev_frame
            }
        )
    return frames


def _frame_from_landmarks(world) -> Frame | None:
    """Build a joint frame from MediaPipe world landmarks; None when the
    body was not detected or any required joint is missing/non-finite."""
    if not world or not world.landmark:
        return None
    landmarks = world.landmark
    frame: Frame = {}
    for lm_idx, joint in _LANDMARK_JOINTS.items():
        if lm_idx >= len(landmarks):
            return None
        lm = landmarks[lm_idx]
        coords = [lm.x, -lm.y, lm.z]  # flip y so up is positive
        if not all(math.isfinite(c) for c in coords):
            return None
        frame[joint] = coords
    return frame


def _visibility_from_landmarks(world: LandmarkList) -> Frame:
    return {
        joint: [float(world.landmark[index].visibility)]
        for index, joint in _LANDMARK_JOINTS.items()
    } if isinstance(world, LandmarkList) else {
        joint: [0.0] for joint in _LANDMARK_JOINTS.values()
    }


def extract_frames_with_visibility(
    path: str, max_frames: int = 300
) -> tuple[list[Frame], list[dict[str, float]], float, int, float]:
    """Return coordinates, visibility, FPS, frames read, and missing percentage."""
    try:
        cap = cv2.VideoCapture(path)
    except cv2.error as e:
        raise VideoDecodeError(f"could not open video: {e}") from e
    if not cap.isOpened():
        raise VideoDecodeError(
            "could not open the video — the file may be corrupt, truncated, "
            "or use an unsupported codec"
        )

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or math.isnan(fps) or math.isinf(fps) or fps <= 0:
        fps = 30.0
    step = max(1, math.ceil(fps / 30.0))
    effective_fps = fps / step
    max_frames = min(max_frames, int(round(10 * effective_fps)))

    detections: list[tuple[int, Frame]] = []
    visibility_detections: list[tuple[int, dict[str, list[float]]]] = []
    read = 0
    sampled = 0
    try:
        with mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as pose:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                idx = read
                read += 1
                if idx % step != 0:
                    continue
                if sampled >= max_frames:
                    break
                slot = sampled
                sampled += 1
                if image is None or image.size == 0 or image.ndim != 3:
                    continue
                try:
                    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                    results = pose.process(rgb)
                except (cv2.error, RuntimeError, ValueError):
                    continue
                frame = _frame_from_landmarks(results.pose_world_landmarks)
                if frame is not None:
                    detections.append((slot, frame))
                    visibility_detections.append(
                        (slot, _visibility_from_landmarks(results.pose_world_landmarks))
                    )
    except cv2.error as e:
        raise VideoDecodeError(f"video decoding failed: {e}") from e
    finally:
        cap.release()

    if read == 0:
        raise VideoDecodeError(
            "no frames could be read from the video — the file may be empty, "
            "corrupt, or use an unsupported codec"
        )
    if len(detections) < 2:
        raise ValueError(
            f"no pose detected in enough frames (found {len(detections)} "
            f"of {read} processed) — upload a video with a clearly visible "
            "full body"
        )
    frames = _fill_gaps(detections)
    visibility_vectors = _fill_gaps(visibility_detections)
    visibility_frames = [
        {joint: values[0] for joint, values in frame.items()}
        for frame in visibility_vectors
    ]
    missing_pct = 100.0 * (sampled - len(detections)) / sampled
    return frames, visibility_frames, effective_fps, read, missing_pct


def extract_frames(
    path: str, max_frames: int = 300
) -> tuple[list[Frame], float, int]:
    """Backward-compatible coordinate-only video extraction."""
    frames, _, effective_fps, read, _ = extract_frames_with_visibility(
        path, max_frames
    )
    return frames, effective_fps, read


def extract_frame_windows(
    path: str,
    *,
    window_seconds: float = 10.0,
    max_fps: float = 30.0,
) -> Iterator[tuple[list[Frame], list[dict[str, float]], float, float]]:
    """Yield bounded windows for offline training without retaining a full video."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"could not open video file: {path}")
    source_fps = cap.get(cv2.CAP_PROP_FPS)
    if not source_fps or not math.isfinite(source_fps) or source_fps <= 0:
        source_fps = 30.0
    step = max(1, math.ceil(source_fps / max_fps))
    effective_fps = source_fps / step
    slots_per_window = max(2, int(round(window_seconds * effective_fps)))
    read = 0
    sampled_slot = 0
    coords: list[tuple[int, Frame]] = []
    visibility: list[tuple[int, dict[str, list[float]]]] = []

    def finish_window(slot_count: int):
        nonlocal coords, visibility
        if len(coords) < 2:
            coords, visibility = [], []
            return None
        try:
            missing_pct = 100.0 * (slot_count - len(coords)) / slot_count
            filled = _fill_gaps(coords)
            filled_visibility = _fill_gaps(visibility)
        except ValueError:
            coords, visibility = [], []
            return None
        coords, visibility = [], []
        visibility_frames = [
            {joint: values[0] for joint, values in frame.items()}
            for frame in filled_visibility
        ]
        return filled, visibility_frames, effective_fps, missing_pct

    try:
        with mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as pose:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                source_index = read
                read += 1
                if source_index % step != 0:
                    continue
                local_slot = sampled_slot % slots_per_window
                sampled_slot += 1
                try:
                    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                    results = pose.process(rgb)
                    world = results.pose_world_landmarks
                    frame = _frame_from_landmarks(world)
                except (cv2.error, RuntimeError, ValueError):
                    frame = None
                if frame is not None:
                    coords.append((local_slot, frame))
                    visibility.append((local_slot, _visibility_from_landmarks(world)))
                if local_slot == slots_per_window - 1:
                    completed = finish_window(slots_per_window)
                    if completed is not None:
                        yield completed
            remainder = sampled_slot % slots_per_window
            if remainder:
                completed = finish_window(remainder)
                if completed is not None:
                    yield completed
    finally:
        cap.release()
