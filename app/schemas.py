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
