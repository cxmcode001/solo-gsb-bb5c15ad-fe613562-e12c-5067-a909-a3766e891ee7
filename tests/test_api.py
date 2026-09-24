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


def _create_measure(target, voices):
    response = client.post(
        "/measures",
        json={"target_duration": target, "voices": voices},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_loop_alignment_of_different_length_measures():
    # A: length 1 (from VALID_PAYLOAD); B: length 3/2.
    measure_a = client.post("/measures", json=VALID_PAYLOAD).json()["id"]
    measure_b = _create_measure(
        "3/2",
        [
            {
                "name": "drum",
                "events": [
                    {"kind": "sound", "duration": "1/4"},
                    {"kind": "rest", "duration": "1/2"},
                    {"kind": "sound", "duration": "3/4"},
                ],
            },
            {
                "name": "pad",
                "events": [{"kind": "sound", "duration": "3/2"}],
            },
        ],
    )

    response = client.post(
        "/loop-alignments",
        json={"measure_a_id": measure_a, "measure_b_id": measure_b},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # lcm(1, 3/2) = 3: A loops three times, B twice.
    assert body["alignment_time"] == "3"
    assert body["loops"] == {
        "a": {
            "measure_id": measure_a,
            "target_duration": "1",
            "voices": ["melody", "bass"],
            "loop_count": 3,
        },
        "b": {
            "measure_id": measure_b,
            "target_duration": "3/2",
            "voices": ["drum", "pad"],
            "loop_count": 2,
        },
    }
    # A stores 5 events/loop x 3, B stores 4/loop x 2 = 23.
    assert body["total_event_count"] == 23

    segments = body["segments"]
    assert segments[0]["start"] == "0"
    assert segments[-1]["end"] == "3"
    for left, right in zip(segments, segments[1:]):
        assert left["end"] == right["start"]

    by_start = {s["start"]: s for s in segments}
    # A restarts at t=1 (third A copy would be loop 3 at t=2).
    assert by_start["1"]["events"]["a"]["melody"] == ["v1e1", 2]
    assert by_start["1"]["events"]["b"]["pad"] == ["v2e1", 1]
    # B restarts at 3/2: half-open boundary -> B's loop-2 event; A is at
    # offset 1/2 of its own loop 2, where melody rests (null).
    assert by_start["3/2"]["events"]["b"]["drum"] == ["v1e1", 2]
    assert by_start["3/2"]["events"]["a"]["melody"] is None
    # At t=2 A enters loop 3 (bass rests the first 1/4 of the loop).
    assert by_start["2"]["events"]["a"]["bass"] is None
    assert by_start["9/4"]["events"]["a"]["bass"] == ["v2e2", 3]
    # Rest voices report null.
    assert by_start["1/2"]["events"]["a"]["melody"] is None
    assert by_start["0"]["events"]["a"]["bass"] is None


def test_loop_alignment_allows_same_measure_for_both_sides():
    measure_id = client.post("/measures", json=VALID_PAYLOAD).json()["id"]
    response = client.post(
        "/loop-alignments",
        json={"measure_a_id": measure_id, "measure_b_id": measure_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["alignment_time"] == "1"
    assert body["loops"]["a"]["loop_count"] == 1
    assert body["loops"]["b"]["loop_count"] == 1
    first = body["segments"][0]["events"]
    assert first["a"] == {"melody": ["v1e1", 1], "bass": None}
    assert first["a"] == first["b"]


def test_loop_alignment_unknown_measure_returns_404():
    response = client.post(
        "/loop-alignments",
        json={"measure_a_id": 1, "measure_b_id": 999999},
    )
    assert response.status_code == 404, response.text

    response = client.post(
        "/loop-alignments",
        json={"measure_a_id": 999998, "measure_b_id": 999999},
    )
    assert response.status_code == 404
    assert "999998" in response.json()["detail"]
    assert "999999" in response.json()["detail"]


def test_loop_alignment_rejects_expansion_over_2000_without_timeline():
    # One measure with two single-event voices and a huge target length:
    # aligned against the length-1 measure it must repeat 2001 times,
    # giving 2 * 2001 + small = 4000+ > 2000 expanded events.
    small = client.post("/measures", json=VALID_PAYLOAD).json()["id"]
    huge = _create_measure(
        "2001",
        [
            {"name": "x", "events": [{"kind": "sound", "duration": "2001"}]},
            {"name": "y", "events": [{"kind": "rest", "duration": "2001"}]},
        ],
    )
    response = client.post(
        "/loop-alignments",
        json={"measure_a_id": small, "measure_b_id": huge},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["limit"] == 2000
    assert detail["total_event_count"] > 2000
    # No truncated timeline is produced.
    assert "segments" not in detail
    assert "alignment_time" in detail


def test_loop_alignment_accepts_exactly_2000_expanded_events():
    # A length 1 with two single-event voices (2 events/loop); B length
    # 999 likewise. lcm(1, 999) = 999: A tiles 999*2 = 1998, B 2, total
    # exactly 2000 — the limit is a strict >, so the request succeeds.
    short = _create_measure(
        "1",
        [
            {"name": "x", "events": [{"kind": "sound", "duration": "1"}]},
            {"name": "y", "events": [{"kind": "rest", "duration": "1"}]},
        ],
    )
    long_measure = _create_measure(
        "999",
        [
            {"name": "x", "events": [{"kind": "sound", "duration": "999"}]},
            {"name": "y", "events": [{"kind": "rest", "duration": "999"}]},
        ],
    )
    response = client.post(
        "/loop-alignments",
        json={"measure_a_id": short, "measure_b_id": long_measure},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_event_count"] == 2000
    assert body["segments"]


def test_loop_alignment_rejects_non_positive_ids():
    response = client.post(
        "/loop-alignments",
        json={"measure_a_id": 0, "measure_b_id": 1},
    )
    assert response.status_code == 422
