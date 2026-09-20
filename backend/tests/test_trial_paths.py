import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from mediapipe.framework.formats.landmark_pb2 import LandmarkList

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gait_gen
import video
from main import app
from processor import GaitProcessor


class ReplayPose:
    frames: list[dict[str, list[float]]] = []
    missing_slots: set[int] = set()

    def __init__(self, **kwargs):
        self.slot = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def process(self, image):
        world = None
        if self.slot not in self.missing_slots:
            world = LandmarkList()
            for _ in range(33):
                world.landmark.add()
            frame = self.frames[self.slot]
            for index, joint in video._LANDMARK_JOINTS.items():
                side = joint.split("_")[0]
                point = frame.get(joint, frame[f"{side}_ankle"])
                world.landmark[index].x = point[0]
                world.landmark[index].y = -point[1]
                world.landmark[index].z = point[2]
                world.landmark[index].visibility = 1.0
        self.slot += 1
        return PoseResult(world)


class PoseResult:
    def __init__(self, world):
        self.pose_world_landmarks = world


def make_clip(tmp_path, monkeypatch):
    frames = gait_gen.recovered_session()["frames"]
    monkeypatch.setattr(ReplayPose, "frames", frames)
    monkeypatch.setattr(video.mp.solutions.pose, "Pose", ReplayPose)
    path = tmp_path / "trial.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 64))
    for _ in frames:
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()
    return path


def test_live_upload_and_research_use_the_same_features(tmp_path, monkeypatch):
    path = make_clip(tmp_path, monkeypatch)
    frames, visibility, fps, _, missing = video.extract_frames_with_visibility(str(path))
    window_frames, window_visibility, window_fps, window_missing = next(video.extract_frame_windows(str(path)))
    upload = GaitProcessor(frames, fps=fps, visibility_frames=visibility, capture_missing_pct=missing)
    offline = GaitProcessor(window_frames, fps=window_fps, visibility_frames=window_visibility, capture_missing_pct=window_missing)
    assert upload.experimental_feature_result().features is not None
    assert upload.experimental_feature_result().features == pytest.approx(
        offline.experimental_feature_result().features, rel=1e-6
    )
    with TestClient(app) as client:
        live = client.post("/api/process-frame", json={
            "frames": frames, "fps": fps, "visibility_frames": visibility,
        })
        uploaded = client.post("/api/process-video", files={
            "file": ("trial.mp4", path.read_bytes(), "video/mp4"),
        })
    assert live.status_code == uploaded.status_code == 200
    assert live.json() == uploaded.json()["metrics"]
    assert "fall_risk_score" in live.json()


def test_missing_edges_count_toward_the_quality_gate(tmp_path, monkeypatch):
    path = make_clip(tmp_path, monkeypatch)
    monkeypatch.setattr(ReplayPose, "missing_slots", set(range(60)))
    frames, visibility, fps, _, missing = video.extract_frames_with_visibility(str(path))
    assert missing == pytest.approx(20)
    result = GaitProcessor(frames, fps=fps, visibility_frames=visibility, capture_missing_pct=missing).compute()
    assert result.experimental_cv_risk_status == "not_scorable"
    assert result.experimental_cv_risk_index is None


def test_live_reports_tracking_gaps_even_after_resampling():
    session = gait_gen.recovered_session()
    with TestClient(app) as client:
        response = client.post("/api/process-frame", json={**session, "capture_missing_pct": 20})
    assert response.status_code == 200
    assert response.json()["experimental_cv_risk_status"] == "not_scorable"
    assert response.json()["experimental_cv_risk_index"] is None


def test_disjoint_capture_and_landmark_loss_are_combined():
    session = gait_gen.recovered_session()
    for frame in session["frames"][60:78]:
        frame["left_ankle"] = [np.nan, np.nan, np.nan]
    result = GaitProcessor(session["frames"], capture_missing_pct=6).compute()
    assert result.dropped_frame_pct == pytest.approx(11.64)
    assert result.experimental_cv_risk_status == "not_scorable"
    assert result.experimental_cv_risk_index is None


@pytest.mark.parametrize("joint", ["left_heel", "right_foot_index", "left_shoulder"])
@pytest.mark.parametrize("malformed", [[0.1], [], [[0.1], [0.2], [0.3]]])
def test_optional_landmark_shapes_return_422(joint, malformed):
    session = gait_gen.recovered_session()
    for frame in session["frames"]:
        frame[joint] = malformed
    with TestClient(app) as client:
        response = client.post("/api/process-frame", json=session)
    assert response.status_code == 422


def test_inconsistent_optional_landmarks_return_422():
    session = gait_gen.recovered_session()
    session["frames"][0]["left_heel"] = [0, 0, 0]
    with TestClient(app) as client:
        response = client.post("/api/process-frame", json=session)
    assert response.status_code == 422
