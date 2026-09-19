"""Video upload processing for GaitGuard AI.

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


def extract_frames(
    path: str, max_frames: int = 300
) -> tuple[list[dict[str, list[float]]], float, int]:
    """Returns (joint_frames, effective_fps, total_frames_read)."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"could not open video file: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or math.isnan(fps) or fps <= 0:
        fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, math.ceil(total / max_frames)) if total > 0 else 1
    effective_fps = fps / step

    frames: list[dict[str, list[float]]] = []
    read = 0
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
                rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                results = pose.process(rgb)
                world = results.pose_world_landmarks
                if not world:
                    continue
                frame: dict[str, list[float]] = {}
                for lm_idx, joint in _LANDMARK_JOINTS.items():
                    lm = world.landmark[lm_idx]
                    # flip y so up is positive, matching processor convention
                    frame[joint] = [lm.x, -lm.y, lm.z]
                frames.append(frame)
    finally:
        cap.release()

    if len(frames) < 2:
        raise ValueError(
            f"no pose detected in enough frames (found {len(frames)} "
            f"of {read} processed) — upload a video with a clearly visible "
            "full body"
        )
    return frames, effective_fps, read
