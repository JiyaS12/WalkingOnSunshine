import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import app
from video import _fill_gaps, extract_frames

client = TestClient(app)

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


def _blank_mp4(path: Path, n: int = 20) -> Path:
    out = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 64)
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
