"""Ensemble beat-grid API.

Submit a measure (target duration + >= 2 voices of sound/rest events
with positive rational durations). Durations are accumulated as exact
fractions; a voice that does not fill the target exactly is reported
with its difference and nothing is saved. Valid measures are stored in
SQLite and their beat grid — half-open segments over the union of all
event boundaries — can be queried by id.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from fractions import Fraction

from fastapi import FastAPI, HTTPException

from . import db
from .grid import (
    build_voice_events,
    compute_grid,
    format_rational,
    parse_rational,
    voice_total,
)
from .schemas import GridOut, MeasureIn, MeasureOut, Mismatch


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

    # Rebuild exact Fraction offsets from the stored canonical strings.
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
