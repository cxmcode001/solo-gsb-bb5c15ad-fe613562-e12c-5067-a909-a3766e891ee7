"""Pure-logic tests: runnable with stdlib unittest or pytest."""

import unittest
from fractions import Fraction

from app.grid import (
    KIND_REST,
    KIND_SOUND,
    build_voice_events,
    compute_grid,
    compute_loop_grid,
    format_rational,
    lcm_rational,
    parse_rational,
    repeat_events,
    voice_total,
)


def make_voices(specs):
    """specs: list of (name, [(kind, duration_str), ...])."""
    voices = []
    for index, (name, events) in enumerate(specs):
        raw = [
            {"kind": kind, "duration": parse_rational(duration)}
            for kind, duration in events
        ]
        voices.append({"name": name, "events": build_voice_events(index, raw)})
    return voices


class ParseRationalTest(unittest.TestCase):
    def test_integer_and_fraction(self):
        self.assertEqual(parse_rational("2"), Fraction(2, 1))
        self.assertEqual(parse_rational("3/4"), Fraction(3, 4))
        self.assertEqual(parse_rational("6/8"), Fraction(3, 4))  # reduced

    def test_rejects_bad_input(self):
        for bad in ["", "abc", "1/0", "0", "0/4", "-1/2", "1.5", "1/2/3", "/2"]:
            with self.assertRaises(ValueError, msg=bad):
                parse_rational(bad)

    def test_format_roundtrip(self):
        self.assertEqual(format_rational(Fraction(3, 4)), "3/4")
        self.assertEqual(format_rational(Fraction(2, 1)), "2")
        self.assertEqual(format_rational(Fraction(0, 1)), "0")


class AccumulationTest(unittest.TestCase):
    def test_exact_fraction_sum(self):
        # 1/3 + 1/3 + 1/3 == 1 exactly (floats would not guarantee this).
        raw = [{"kind": KIND_SOUND, "duration": parse_rational("1/3")}] * 3
        self.assertEqual(voice_total(raw), Fraction(1))

    def test_event_offsets_are_half_open(self):
        (voice,) = make_voices([("a", [(KIND_SOUND, "1/2"), (KIND_REST, "1/4")])])
        first, second = voice["events"]
        self.assertEqual((first["start"], first["end"]), (Fraction(0), Fraction(1, 2)))
        self.assertEqual(
            (second["start"], second["end"]), (Fraction(1, 2), Fraction(3, 4))
        )
        self.assertEqual(first["id"], "v1e1")
        self.assertEqual(second["id"], "v1e2")


class GridTest(unittest.TestCase):
    def setUp(self):
        # melody: sound 1/2, rest 1/4, sound 1/4  (total 1)
        # bass:   rest 1/4, sound 3/4             (total 1)
        self.voices = make_voices(
            [
                (
                    "melody",
                    [(KIND_SOUND, "1/2"), (KIND_REST, "1/4"), (KIND_SOUND, "1/4")],
                ),
                ("bass", [(KIND_REST, "1/4"), (KIND_SOUND, "3/4")]),
            ]
        )
        self.segments = compute_grid(self.voices)

    def test_segment_boundaries_are_union_of_event_edges(self):
        edges = [(s["start"], s["end"]) for s in self.segments]
        self.assertEqual(
            edges,
            [
                (Fraction(0), Fraction(1, 4)),
                (Fraction(1, 4), Fraction(1, 2)),
                (Fraction(1, 2), Fraction(3, 4)),
                (Fraction(3, 4), Fraction(1)),
            ],
        )

    def test_active_events_per_segment(self):
        active = [s["events"] for s in self.segments]
        self.assertEqual(
            active,
            [
                {"melody": "v1e1", "bass": None},
                {"melody": "v1e1", "bass": "v2e2"},
                {"melody": None, "bass": "v2e2"},
                {"melody": "v1e3", "bass": "v2e2"},
            ],
        )

    def test_segments_tile_target_without_gaps_or_overlap(self):
        # No lost or duplicated time: segments chain exactly onto [0, 1).
        self.assertEqual(self.segments[0]["start"], Fraction(0))
        self.assertEqual(self.segments[-1]["end"], Fraction(1))
        for left, right in zip(self.segments, self.segments[1:]):
            self.assertEqual(left["end"], right["start"])

    def test_boundary_instant_belongs_to_exactly_one_event_per_voice(self):
        # At t = 1/2 the melody switches sound -> rest: the segment
        # starting at 1/2 must show the rest (null), the one ending at
        # 1/2 must show the sound. Half-open intervals => no double
        # counting, no gap.
        before = next(s for s in self.segments if s["end"] == Fraction(1, 2))
        after = next(s for s in self.segments if s["start"] == Fraction(1, 2))
        self.assertEqual(before["events"]["melody"], "v1e1")
        self.assertIsNone(after["events"]["melody"])

    def test_unaligned_voices_produce_finer_grid(self):
        voices = make_voices(
            [
                ("x", [(KIND_SOUND, "1/3"), (KIND_REST, "2/3")]),
                ("y", [(KIND_SOUND, "1/2"), (KIND_SOUND, "1/2")]),
            ]
        )
        segments = compute_grid(voices)
        edges = [(s["start"], s["end"]) for s in segments]
        # x has edges at 0, 1/3, 1; y at 0, 1/2, 1.
        self.assertEqual(
            edges,
            [
                (Fraction(0), Fraction(1, 3)),
                (Fraction(1, 3), Fraction(1, 2)),
                (Fraction(1, 2), Fraction(1)),
            ],
        )
        self.assertEqual(segments[0]["events"], {"x": "v1e1", "y": "v2e1"})
        self.assertEqual(segments[1]["events"], {"x": None, "y": "v2e1"})
        self.assertEqual(segments[2]["events"], {"x": None, "y": "v2e2"})


class RationalLcmTest(unittest.TestCase):
    def test_integer_and_fractional_loop_lengths(self):
        # First simultaneous restart of length-1 and length-3/2 loops.
        self.assertEqual(lcm_rational(Fraction(1), Fraction(3, 2)), Fraction(3))
        # lcm(1/2, 1/3) = lcm(1,1)/gcd(2,3) = 1.
        self.assertEqual(lcm_rational(Fraction(1, 2), Fraction(1, 3)), Fraction(1))
        # lcm(2/3, 4/5) = lcm(2,4)/gcd(3,5) = 4.
        self.assertEqual(lcm_rational(Fraction(2, 3), Fraction(4, 5)), Fraction(4))
        # Equal lengths => the measure itself.
        self.assertEqual(lcm_rational(Fraction(5, 7), Fraction(5, 7)), Fraction(5, 7))

    def test_result_is_common_multiple_with_integer_cycles(self):
        a, b = Fraction(3, 4), Fraction(5, 6)
        t = lcm_rational(a, b)
        self.assertEqual((t / a).denominator, 1)
        self.assertEqual((t / b).denominator, 1)


class RepeatEventsTest(unittest.TestCase):
    def test_copies_keep_id_and_carry_loop_index(self):
        voices = make_voices(
            [("a", [(KIND_SOUND, "1/2"), (KIND_REST, "1/4")])]
        )
        repeated = repeat_events(voices, Fraction(3, 4), 3)
        copies = repeated[0]["events"]
        self.assertEqual(len(copies), 6)
        self.assertEqual(
            [(c["id"], c["loop"]) for c in copies],
            [("v1e1", 1), ("v1e2", 1), ("v1e1", 2), ("v1e2", 2), ("v1e1", 3), ("v1e2", 3)],
        )
        # Half-open tiling: loop 2 starts exactly where loop 1 ended.
        self.assertEqual(copies[1]["end"], copies[2]["start"])
        self.assertEqual(copies[2]["start"], Fraction(3, 4))
        self.assertEqual(copies[-1]["end"], Fraction(9, 4))


class LoopGridTest(unittest.TestCase):
    def test_two_loops_realignment_grid(self):
        # A length 1:    sound 1/2, rest 1/2 (repeats 3 times)
        # B length 3/2:  rest 1/2, sound 1 (repeats 2 times)
        a = make_voices([("pulse", [(KIND_SOUND, "1/2"), (KIND_REST, "1/2")])])
        b = make_voices([("pad", [(KIND_REST, "1/2"), (KIND_SOUND, "1")])])
        horizon = lcm_rational(Fraction(1), Fraction(3, 2))
        self.assertEqual(horizon, Fraction(3))
        segments = compute_loop_grid(
            [("a", repeat_events(a, Fraction(1), 3)),
             ("b", repeat_events(b, Fraction(3, 2), 2))],
            horizon,
        )
        edges = [(s["start"], s["end"]) for s in segments]
        self.assertEqual(edges[0], (Fraction(0), Fraction(1, 2)))
        self.assertEqual(segments[0]["events"], {
            "a": {"pulse": ["v1e1", 1]},
            "b": {"pad": None},
        })
        # t in [1/2, 1): A loops the second time at t=1; B still in its
        # first long sound.
        at_half = next(s for s in segments if s["start"] == Fraction(1, 2))
        self.assertEqual(at_half["events"]["a"]["pulse"], None)
        self.assertEqual(at_half["events"]["b"]["pad"], ["v1e2", 1])
        at_one = next(s for s in segments if s["start"] == Fraction(1))
        self.assertEqual(at_one["events"]["a"]["pulse"], ["v1e1", 2])
        self.assertEqual(at_one["events"]["b"]["pad"], ["v1e2", 1])
        # B restarts at 3/2: half-open, segment starting there is its rest.
        at_restart = next(s for s in segments if s["start"] == Fraction(3, 2))
        self.assertEqual(at_restart["events"]["b"]["pad"], None)
        # Segments tile [0, 3).
        self.assertEqual(segments[0]["start"], Fraction(0))
        self.assertEqual(segments[-1]["end"], Fraction(3))
        for left, right in zip(segments, segments[1:]):
            self.assertEqual(left["end"], right["start"])

    def test_same_measure_as_both_sides_keeps_voice_namespaces(self):
        voices = make_voices([("solo", [(KIND_SOUND, "1")])])
        segments = compute_loop_grid(
            [("a", repeat_events(voices, Fraction(1), 2)),
             ("b", repeat_events(voices, Fraction(1), 2))],
            Fraction(2),
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["events"], {
            "a": {"solo": ["v1e1", 1]},
            "b": {"solo": ["v1e1", 1]},
        })
        self.assertEqual(segments[1]["events"]["a"]["solo"], ["v1e1", 2])


if __name__ == "__main__":
    unittest.main()
