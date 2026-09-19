import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import app
from video import extract_frames

client = TestClient(app)


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
