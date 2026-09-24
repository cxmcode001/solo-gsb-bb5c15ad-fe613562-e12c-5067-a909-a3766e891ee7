"""API tests using FastAPI's TestClient (requires fastapi + httpx)."""

import os
import tempfile

# Point the app at a throwaway database before it is imported.
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

# TestClient only runs the app's lifespan when used as a context
# manager, so initialize the schema explicitly here.
db.init_db()
client = TestClient(app)

VALID_PAYLOAD = {
    "target_duration": "4/4",
    "voices": [
        {
            "name": "melody",
            "events": [
                {"kind": "sound", "duration": "1/2"},
                {"kind": "rest", "duration": "1/4"},
                {"kind": "sound", "duration": "1/4"},
            ],
        },
        {
            "name": "bass",
            "events": [
                {"kind": "rest", "duration": "1/4"},
                {"kind": "sound", "duration": "3/4"},
            ],
        },
    ],
}


def test_create_measure_returns_201_and_stored_representation():
    response = client.post("/measures", json=VALID_PAYLOAD)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] >= 1
    assert body["target_duration"] == "1"  # 4/4 stored in canonical form
    assert [voice["name"] for voice in body["voices"]] == ["melody", "bass"]
    melody_events = body["voices"][0]["events"]
    assert [event["id"] for event in melody_events] == ["v1e1", "v1e2", "v1e3"]
    assert melody_events[0]["start"] == "0"
    assert melody_events[0]["end"] == "1/2"


def test_mismatched_voice_is_rejected_with_difference_and_not_saved():
    payload = {
        "target_duration": "1",
        "voices": [
            {
                "name": "too_short",
                "events": [{"kind": "sound", "duration": "3/4"}],
            },
            {
                "name": "too_long",
                "events": [{"kind": "sound", "duration": "5/4"}],
            },
        ],
    }
    response = client.post("/measures", json=payload)
    assert response.status_code == 422, response.text
    mismatches = response.json()["detail"]["mismatches"]
    by_voice = {entry["voice"]: entry for entry in mismatches}
    assert by_voice["too_short"] == {
        "voice": "too_short",
        "expected": "1",
        "actual": "3/4",
        "difference": "-1/4",
    }
    assert by_voice["too_long"]["difference"] == "1/4"

    # Nothing was saved: the next valid measure gets the very next id.
    ok = client.post("/measures", json=VALID_PAYLOAD)
    assert ok.status_code == 201
    assert client.get(f"/measures/{ok.json()['id']}").status_code == 200
    assert client.get(f"/measures/{ok.json()['id'] + 1}").status_code == 404


def test_grid_segments_cover_target_with_half_open_intervals():
    measure_id = client.post("/measures", json=VALID_PAYLOAD).json()["id"]
    response = client.get(f"/measures/{measure_id}/grid")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["voices"] == ["melody", "bass"]

    segments = body["segments"]
    assert [(s["start"], s["end"]) for s in segments] == [
        ("0", "1/4"),
        ("1/4", "1/2"),
        ("1/2", "3/4"),
        ("3/4", "1"),
    ]
    assert [s["events"] for s in segments] == [
        {"melody": "v1e1", "bass": None},
        {"melody": "v1e1", "bass": "v2e2"},
        {"melody": None, "bass": "v2e2"},
        {"melody": "v1e3", "bass": "v2e2"},
    ]
    # Segments tile [0, target) with no gaps or overlaps.
    assert segments[0]["start"] == "0"
    assert segments[-1]["end"] == body["target_duration"]
    for left, right in zip(segments, segments[1:]):
        assert left["end"] == right["start"]


def test_validation_errors():
    # Fewer than two voices.
    response = client.post(
        "/measures",
        json={
            "target_duration": "1",
            "voices": [{"name": "solo", "events": [{"kind": "sound", "duration": "1"}]}],
        },
    )
    assert response.status_code == 422

    # Non-positive / malformed durations.
    for bad_duration in ["0", "-1/2", "1.5", "abc", "1/0"]:
        response = client.post(
            "/measures",
            json={
                "target_duration": "1",
                "voices": [
                    {"name": "a", "events": [{"kind": "sound", "duration": bad_duration}]},
                    {"name": "b", "events": [{"kind": "rest", "duration": "1"}]},
                ],
            },
        )
        assert response.status_code == 422, bad_duration

    # Duplicate voice names.
    response = client.post(
        "/measures",
        json={
            "target_duration": "1",
            "voices": [
                {"name": "dup", "events": [{"kind": "sound", "duration": "1"}]},
                {"name": "dup", "events": [{"kind": "rest", "duration": "1"}]},
            ],
        },
    )
    assert response.status_code == 422


def test_unknown_measure_returns_404():
    assert client.get("/measures/999999").status_code == 404
    assert client.get("/measures/999999/grid").status_code == 404
