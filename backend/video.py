"""Video upload processing for Sana.

Runs MediaPipe Pose (legacy solutions API) over an uploaded video and returns
joint frames compatible with GaitProcessor (6 joints, y flipped so up is
positive — same convention the frontend uses for live camera input).
"""

from __future__ import annotations

import math

import cv2
import mediapipe as mp

# pose landmark indices -> joint names (same mapping as the frontend)
_LANDMARK_JOINTS = {
    23: "left_hip",
    24: "right_hip",
    25: "left_knee",
    26: "right_knee",
    27: "left_ankle",
    28: "right_ankle",
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
                    for k in range(3)
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


def extract_frames(
    path: str, max_frames: int = 300
) -> tuple[list[Frame], float, int]:
    """Returns (joint_frames, effective_fps, total_frames_read)."""
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
    total_raw = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    total = int(total_raw) if total_raw and math.isfinite(total_raw) and total_raw > 0 else 0
    step = max(1, math.ceil(total / max_frames)) if total > 0 else 1
    # Decimating a long clip to fit max_frames can drop the sample rate below
    # what gait timing needs (~4 steps/s), which aliases stride and cadence.
    # Keep the rate above the floor and analyse a bounded window instead.
    if fps / step < _MIN_ANALYSIS_FPS:
        step = max(1, int(fps // _MIN_ANALYSIS_FPS))
    effective_fps = fps / step

    detections: list[tuple[int, Frame]] = []
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
    return frames, effective_fps, read
