import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import video
from main import app
from video import _fill_gaps, extract_frames

client = TestClient(app)


class _FakeWorld:
    """Stand-in for MediaPipe's pose_world_landmarks."""

    def __init__(self):
        self.landmark = [
            type("L", (), {"x": i * 0.01, "y": i * 0.01, "z": 0.0})()
            for i in range(33)
        ]


class _FakePose:
    """Detects a pose in every frame, so decimation can be tested directly."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def process(self, image):
        return type("R", (), {"pose_world_landmarks": _FakeWorld()})()

_JOINTS = [
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
]


def _frame(v: float) -> dict:
    return {j: [v, v + 1, v + 2] for j in _JOINTS}


def test_fill_gaps_interpolates_middle():
    frames = _fill_gaps([(0, _frame(0.0)), (2, _frame(2.0))])
    assert len(frames) == 3
    assert frames[1]["left_hip"] == [1.0, 2.0, 3.0]
    assert frames[0]["left_hip"] == [0.0, 1.0, 2.0]
    assert frames[2]["left_hip"] == [2.0, 3.0, 4.0]


def test_fill_gaps_trims_edges():
    frames = _fill_gaps([(1, _frame(0.0)), (3, _frame(2.0))])
    assert len(frames) == 3


def test_fill_gaps_low_coverage_raises():
    # slots 0..9, only 4 detections -> 40% coverage
    detections = [(s, _frame(float(s))) for s in (0, 3, 6, 9)]
    with pytest.raises(ValueError, match="only detected in"):
        _fill_gaps(detections)


def _blank_mp4(path: Path, n: int = 20, fps: int = 30) -> Path:
    out = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 64)
    )
    for _ in range(n):
        out.write(np.zeros((64, 64, 3), dtype=np.uint8))
    out.release()
    return path


def test_extract_frames_no_pose(tmp_path):
    path = _blank_mp4(tmp_path / "blank.mp4")
    with pytest.raises(ValueError, match="no pose detected"):
        extract_frames(str(path))


def test_extract_frames_bad_file(tmp_path):
    path = tmp_path / "garbage.mp4"
    path.write_bytes(b"not a video")
    with pytest.raises(ValueError):
        extract_frames(str(path))


def test_process_video_corrupt_file_returns_422():
    resp = client.post(
        "/api/process-video",
        files={"file": ("garbage.mp4", b"\x00\x01not a video", "video/mp4")},
    )
    assert resp.status_code == 422
    assert "corrupt" in resp.json()["detail"].lower()


def test_process_video_empty_upload_returns_422():
    resp = client.post(
        "/api/process-video",
        files={"file": ("empty.mp4", b"", "video/mp4")},
    )
    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"].lower()


def test_process_video_processing_crash_returns_422(tmp_path, monkeypatch):
    def boom(path, max_frames=300):
        raise IndexError("list index out of range")

    monkeypatch.setattr(video, "extract_frames", boom)
    path = _blank_mp4(tmp_path / "blank.mp4")
    resp = client.post(
        "/api/process-video",
        files={"file": ("blank.mp4", path.read_bytes(), "video/mp4")},
    )
    assert resp.status_code == 422


def test_frame_from_landmarks_rejects_nan_and_missing():
    world = _FakeWorld()
    assert video._frame_from_landmarks(world) is not None
    world.landmark[25].x = float("nan")
    assert video._frame_from_landmarks(world) is None
    short = _FakeWorld()
    short.landmark = short.landmark[:20]
    assert video._frame_from_landmarks(short) is None
    assert video._frame_from_landmarks(None) is None


def test_process_video_bad_suffix():
    resp = client.post(
        "/api/process-video",
        files={"file": ("clip.avi", b"data", "video/avi")},
    )
    assert resp.status_code == 415


def test_process_video_no_pose(tmp_path):
    path = _blank_mp4(tmp_path / "blank.mp4")
    resp = client.post(
        "/api/process-video",
        files={"file": ("blank.mp4", path.read_bytes(), "video/mp4")},
    )
    assert resp.status_code == 422
    assert "pose" in resp.json()["detail"].lower()


def test_max_frames_enforced_without_frame_count(tmp_path, monkeypatch):
    """Containers that report no frame count must still respect max_frames."""
    path = _blank_mp4(tmp_path / "long.mp4", n=900)
    real_get = cv2.VideoCapture.get

    def no_frame_count(self, prop):
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return 0.0
        return real_get(self, prop)

    monkeypatch.setattr(cv2.VideoCapture, "get", no_frame_count)
    monkeypatch.setattr(video.mp.solutions.pose, "Pose", _FakePose)
    frames, _, _ = extract_frames(str(path), max_frames=300)
    assert len(frames) == 300


def test_decimation_keeps_a_usable_sample_rate(tmp_path, monkeypatch):
    """A long clip must not be sampled below the gait-timing floor."""
    path = _blank_mp4(tmp_path / "long.mp4", n=900, fps=30)
    monkeypatch.setattr(video.mp.solutions.pose, "Pose", _FakePose)
    frames, effective_fps, _ = extract_frames(str(path), max_frames=100)
    assert effective_fps >= video._MIN_ANALYSIS_FPS
    assert len(frames) <= 100


def test_process_video_unavailable_returns_503(monkeypatch):
    """A broken cv2/mediapipe install must not answer as a client error."""
    import main

    monkeypatch.setattr(main, "video", None)
    monkeypatch.setattr(main, "_VIDEO_IMPORT_ERROR", "libGL.so.1 missing")
    resp = client.post(
        "/api/process-video",
        files={"file": ("clip.mp4", b"data", "video/mp4")},
    )
    assert resp.status_code == 503
    assert "libGL" in resp.json()["detail"]


def test_health_survives_missing_video_deps(monkeypatch):
    import main

    monkeypatch.setattr(main, "video", None)
    assert client.get("/api/health").status_code == 200
