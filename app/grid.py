"""Core beat-grid logic.

Pure standard-library module: durations are exact ``fractions.Fraction``
values parsed from positive rational strings, and the grid is built from
the union of every voice's event boundaries.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from fractions import Fraction

# A positive rational: "3", "3/4", "12/8" ... (no sign, no decimals).
_RATIONAL_RE = re.compile(r"^(\d+)(?:/(\d+))?$")

KIND_SOUND = "sound"
KIND_REST = "rest"
KINDS = (KIND_SOUND, KIND_REST)


def parse_rational(text: str) -> Fraction:
    """Parse a positive rational string ("2", "3/4") into a Fraction.

    Raises ValueError on malformed input, zero denominators, or
    non-positive values.
    """
    if not isinstance(text, str):
        raise ValueError(f"rational must be a string, got {type(text).__name__}")
    match = _RATIONAL_RE.match(text.strip())
    if not match:
        raise ValueError(f"invalid rational number: {text!r}")
    numerator = int(match.group(1))
    denominator = int(match.group(2)) if match.group(2) else 1
    if denominator == 0:
        raise ValueError(f"denominator must not be zero: {text!r}")
    value = Fraction(numerator, denominator)
    if value <= 0:
        raise ValueError(f"duration must be positive, got {text!r}")
    return value


def format_rational(value: Fraction) -> str:
    """Canonical string form of a Fraction ("3/4", "2", "0")."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def event_id(voice_index: int, event_index: int) -> str:
    """Deterministic event identifier, 1-based: v1e1, v1e2, v2e1, ..."""
    return f"v{voice_index + 1}e{event_index + 1}"


def build_voice_events(voice_index: int, events: list[dict]) -> list[dict]:
    """Attach id/start/end offsets to a voice's events.

    ``events`` items must have ``kind`` and a ``Fraction`` ``duration``.
    Offsets are cumulative exact fractions; event i spans the half-open
    interval [start, end).
    """
    built = []
    cursor = Fraction(0)
    for position, event in enumerate(events):
        end = cursor + event["duration"]
        built.append(
            {
                "id": event_id(voice_index, position),
                "kind": event["kind"],
                "duration": event["duration"],
                "start": cursor,
                "end": end,
                "position": position,
            }
        )
        cursor = end
    return built


def voice_total(events: list[dict]) -> Fraction:
    """Exact accumulated duration of built (or raw) events."""
    total = Fraction(0)
    for event in events:
        total += event["duration"]
    return total


def compute_grid(voices: list[dict]) -> list[dict]:
    """Build half-open time segments from the union of all boundaries.

    ``voices`` is a list of {"name": str, "events": [built events]}.
    Every voice must span the same total duration. Returns one segment
    per adjacent boundary pair [start, end); each segment maps voice
    names to the sounding event id, or None when the voice rests.

    Because segment edges are exactly the union of event edges, each
    segment lies fully inside exactly one event per voice: boundaries
    neither drop nor duplicate sound (half-open intervals: an event is
    active for start <= t < end).
    """
    points: set[Fraction] = set()
    for voice in voices:
        for event in voice["events"]:
            points.add(event["start"])
            points.add(event["end"])
    ordered = sorted(points)

    # Per voice, event end offsets are strictly increasing (durations
    # are positive), so bisect finds the event covering any segment.
    ends_per_voice = [[event["end"] for event in voice["events"]] for voice in voices]

    segments = []
    for index in range(len(ordered) - 1):
        start, end = ordered[index], ordered[index + 1]
        active: dict[str, str | None] = {}
        for voice, ends in zip(voices, ends_per_voice):
            slot = bisect_right(ends, start)
            event = voice["events"][slot]
            # The segment starts inside this event by construction.
            assert event["start"] <= start < event["end"]
            active[voice["name"]] = (
                event["id"] if event["kind"] == KIND_SOUND else None
            )
        segments.append({"start": start, "end": end, "events": active})
    return segments
