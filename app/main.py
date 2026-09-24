"""Ensemble beat-grid API.

Submit a measure (target duration + >= 2 voices of sound/rest events
with positive rational durations). Durations are accumulated as exact
fractions; a voice that does not fill the target exactly is reported
with its difference and nothing is saved. Valid measures are stored in
SQLite and their beat grid — half-open segments over the union of all
event boundaries — can be queried by id.

POST /loop-alignments treats two saved measures as repeating loops of
(possibly) different lengths, returns the exact rational moment they
first realign at their start, and segments the repeated timeline into
the union of both loops' event boundaries with per-voice original event
ids and 1-based loop numbers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from fractions import Fraction

from fastapi import FastAPI, HTTPException

from . import db
from .grid import (
    build_voice_events,
    compute_grid,
    compute_loop_grid,
    format_rational,
    lcm_rational,
    parse_rational,
    repeat_events,
    voice_total,
)
from .schemas import (
    GridOut,
    LoopAlignmentIn,
    LoopAlignmentOut,
    MeasureIn,
    MeasureOut,
    Mismatch,
)

# A guard before the potentially large repetition expansion: if both
# loops had to be tiled to their first simultaneous restart and that
# would contain more than this many stored events in total, reject the
# request rather than returning a truncated timeline.
MAX_EXPANDED_EVENTS = 2000


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One command (docker compose up) therefore covers dependencies,
    # database initialization, and startup.
    db.init_db()
    yield


app = FastAPI(
    title="Ensemble Beat Grid API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict:
    return {
        "service": "ensemble-beat-grid",
        "docs": "/docs",
        "endpoints": {
            "create_measure": "POST /measures",
            "get_measure": "GET /measures/{id}",
            "get_grid": "GET /measures/{id}/grid",
            "align_loops": "POST /loop-alignments",
        },
    }


@app.post("/measures", response_model=MeasureOut, status_code=201)
def create_measure(payload: MeasureIn) -> dict:
    target = parse_rational(payload.target_duration)

    prepared_voices = []
    mismatches: list[Mismatch] = []
    for voice_index, voice in enumerate(payload.voices):
        raw_events = [
            {"kind": event.kind, "duration": parse_rational(event.duration)}
            for event in voice.events
        ]
        total = voice_total(raw_events)
        if total != target:
            mismatches.append(
                Mismatch(
                    voice=voice.name,
                    expected=format_rational(target),
                    actual=format_rational(total),
                    difference=format_rational(total - target),
                )
            )
        prepared_voices.append(
            {"name": voice.name, "events": build_voice_events(voice_index, raw_events)}
        )

    if mismatches:
        # Nothing is saved: the whole measure is rejected.
        raise HTTPException(
            status_code=422,
            detail={
                "message": "voice durations do not add up to the target "
                "duration; measure was not saved",
                "mismatches": [m.model_dump() for m in mismatches],
            },
        )

    measure_id = db.insert_measure(format_rational(target), prepared_voices)
    stored = db.load_measure(measure_id)
    assert stored is not None
    return stored


@app.get("/measures/{measure_id}", response_model=MeasureOut)
def get_measure(measure_id: int) -> dict:
    measure = db.load_measure(measure_id)
    if measure is None:
        raise HTTPException(status_code=404, detail="measure not found")
    return measure


@app.get("/measures/{measure_id}/grid", response_model=GridOut)
def get_grid(measure_id: int) -> dict:
    measure = db.load_measure(measure_id)
    if measure is None:
        raise HTTPException(status_code=404, detail="measure not found")

    _length, voices = _measure_voices(measure)

    segments = compute_grid(voices)
    return {
        "measure_id": measure["id"],
        "target_duration": measure["target_duration"],
        "voices": [voice["name"] for voice in measure["voices"]],
        "segments": [
            {
                "start": format_rational(segment["start"]),
                "end": format_rational(segment["end"]),
                "events": segment["events"],
            }
            for segment in segments
        ],
    }


def _measure_voices(measure: dict) -> tuple[Fraction, list[dict]]:
    """Rebuild exact Fraction offsets of a stored measure's events."""
    length = Fraction(measure["target_duration"])
    voices = []
    for voice in measure["voices"]:
        events = [
            {
                "id": event["id"],
                "kind": event["kind"],
                "duration": Fraction(event["duration"]),
                "start": Fraction(event["start"]),
                "end": Fraction(event["end"]),
            }
            for event in voice["events"]
        ]
        voices.append({"name": voice["name"], "events": events})
    return length, voices


@app.post("/loop-alignments", response_model=LoopAlignmentOut)
def align_loops(payload: LoopAlignmentIn) -> dict:
    # The same measure may serve as both loops; load it only once.
    requested_ids = {payload.measure_a_id, payload.measure_b_id}
    stored = {mid: db.load_measure(mid) for mid in requested_ids}
    missing = sorted(mid for mid, measure in stored.items() if measure is None)
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"measure not found: {', '.join(str(mid) for mid in missing)}",
        )

    length_a, voices_a = _measure_voices(stored[payload.measure_a_id])
    length_b, voices_b = _measure_voices(stored[payload.measure_b_id])

    # First time both loops are simultaneously back at their start,
    # computed exactly (LCM of two rational loop lengths).
    alignment_time = lcm_rational(length_a, length_b)
    cycles_a = alignment_time / length_a
    cycles_b = alignment_time / length_b
    assert cycles_a.denominator == 1 and cycles_b.denominator == 1
    cycles_a, cycles_b = int(cycles_a), int(cycles_b)

    events_per_loop_a = sum(len(voice["events"]) for voice in voices_a)
    events_per_loop_b = sum(len(voice["events"]) for voice in voices_b)
    total_event_count = (
        events_per_loop_a * cycles_a + events_per_loop_b * cycles_b
    )
    if total_event_count > MAX_EXPANDED_EVENTS:
        # Refuse outright: no truncated timeline is emitted.
        raise HTTPException(
            status_code=422,
            detail={
                "message": "expanded event count exceeds the limit; "
                "no timeline was produced",
                "limit": MAX_EXPANDED_EVENTS,
                "total_event_count": total_event_count,
                "alignment_time": format_rational(alignment_time),
                "loop_counts": {"a": cycles_a, "b": cycles_b},
            },
        )

    repeated_a = repeat_events(voices_a, length_a, cycles_a)
    repeated_b = repeat_events(voices_b, length_b, cycles_b)
    segments = compute_loop_grid(
        [("a", repeated_a), ("b", repeated_b)], alignment_time
    )

    def loop_summary(measure: dict, length: Fraction, cycles: int) -> dict:
        return {
            "measure_id": measure["id"],
            "target_duration": format_rational(length),
            "voices": [voice["name"] for voice in measure["voices"]],
            "loop_count": cycles,
        }

    return {
        "measure_a_id": payload.measure_a_id,
        "measure_b_id": payload.measure_b_id,
        "alignment_time": format_rational(alignment_time),
        "loops": {
            "a": loop_summary(
                stored[payload.measure_a_id], length_a, cycles_a
            ),
            "b": loop_summary(
                stored[payload.measure_b_id], length_b, cycles_b
            ),
        },
        "total_event_count": total_event_count,
        "segments": [
            {
                "start": format_rational(segment["start"]),
                "end": format_rational(segment["end"]),
                "events": segment["events"],
            }
            for segment in segments
        ],
    }
