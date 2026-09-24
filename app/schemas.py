"""Request/response schemas for the beat-grid API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .grid import parse_rational


class EventIn(BaseModel):
    """One sound or rest event; duration is a positive rational string."""

    kind: Literal["sound", "rest"]
    duration: str = Field(examples=["1/4", "2", "3/8"])

    @field_validator("duration")
    @classmethod
    def duration_must_be_positive_rational(cls, value: str) -> str:
        try:
            parse_rational(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value


class VoiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    events: list[EventIn] = Field(min_length=1)


class MeasureIn(BaseModel):
    """A measure: target duration plus at least two voices."""

    target_duration: str = Field(examples=["4/4", "1", "3/2"])
    voices: list[VoiceIn] = Field(min_length=2)

    @field_validator("target_duration")
    @classmethod
    def target_must_be_positive_rational(cls, value: str) -> str:
        try:
            parse_rational(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("voices")
    @classmethod
    def voice_names_must_be_unique(cls, voices: list[VoiceIn]) -> list[VoiceIn]:
        names = [voice.name for voice in voices]
        if len(names) != len(set(names)):
            raise ValueError("voice names must be unique within a measure")
        return voices


class Mismatch(BaseModel):
    """Per-voice duration mismatch; difference = actual - expected."""

    voice: str
    expected: str
    actual: str
    difference: str


class EventOut(BaseModel):
    id: str
    kind: Literal["sound", "rest"]
    duration: str
    start: str
    end: str


class VoiceOut(BaseModel):
    name: str
    events: list[EventOut]


class MeasureOut(BaseModel):
    id: int
    target_duration: str
    created_at: str
    voices: list[VoiceOut]


class SegmentOut(BaseModel):
    """Half-open segment [start, end); null means the voice rests."""

    start: str
    end: str
    events: dict[str, str | None]


class GridOut(BaseModel):
    measure_id: int
    target_duration: str
    voices: list[str]
    segments: list[SegmentOut]


class LoopAlignmentIn(BaseModel):
    """Two saved measures aligned as repeating loops (may be identical)."""

    measure_a_id: int = Field(gt=0, examples=[1])
    measure_b_id: int = Field(gt=0, examples=[2])


class LoopVoiceOut(BaseModel):
    """One loop side: measure id, loop length, voices, repetition count."""

    measure_id: int
    target_duration: str
    voices: list[str]
    loop_count: int


class LoopAlignmentOut(BaseModel):
    """Grid over [0, alignment_time) for two repeating loops.

    Each segment maps each loop side ("a"/"b") to a per-voice map:
    [original event id, 1-based loop index] for a sounding event,
    or null when that voice rests. Voice names are namespaced under
    "a"/"b" because the same measure may be aligned with itself.
    """

    measure_a_id: int
    measure_b_id: int
    alignment_time: str
    loops: dict[str, LoopVoiceOut]
    total_event_count: int
    segments: list["LoopSegmentOut"]


class LoopSegmentOut(BaseModel):
    """Half-open segment [start, end) of the aligned loop timeline."""

    start: str
    end: str
    events: dict[str, dict[str, list[str | int] | None]]


LoopAlignmentOut.model_rebuild()
