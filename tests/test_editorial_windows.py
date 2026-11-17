"""Pruebas de ventanas e identidad de la etapa editorial (Fase 3).

No ejecutan LM Studio ni cargan modelos: prueban la logica determinista de
ventanas, el digest de transcripcion y la huella.

Ejecutar con:
    python -m unittest tests.test_editorial_windows -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.contracts import TranscriptSegment  # noqa: E402
from services.editorial.config import EditorialConfig  # noqa: E402
from services.editorial.fingerprint import (  # noqa: E402
    build_fingerprint,
    compute_config_hash,
    compute_transcription_digest,
    fingerprint_diff,
    fingerprint_matches,
)
from services.editorial.windows import build_windows, index_segments  # noqa: E402


def seg(sid: str, start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=sid, start_seconds=start, end_seconds=end, text=text
    )


def make_segments(count: int, *, chars: int = 10, span: float = 10.0):
    segments = []
    for i in range(count):
        start = i * span
        # text with exactly `chars` characters
        text = ("x" * max(1, chars - len(str(i)))) + str(i)
        segments.append(seg(f"seg_{i+1:04d}", start, start + span, text))
    return segments


class WindowTests(unittest.TestCase):
    def test_empty_returns_no_windows(self):
        self.assertEqual(build_windows([], target_chars=100, hard_max_chars=200,
                                       overlap_segments=2, context_segments=2), [])

    def test_small_input_single_window(self):
        segments = make_segments(3, chars=5)
        windows = build_windows(segments, target_chars=1000, hard_max_chars=2000,
                                overlap_segments=2, context_segments=1)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].body_ids, ["seg_0001", "seg_0002", "seg_0003"])
        self.assertEqual(windows[0].context_before_ids, [])
        self.assertEqual(windows[0].context_after_ids, [])

    def test_window_id_sequence(self):
        segments = make_segments(3, chars=10)
        windows = build_windows(segments, target_chars=10, hard_max_chars=1000,
                                overlap_segments=0, context_segments=0)
        self.assertEqual([w.window_id for w in windows], ["win_0001", "win_0002", "win_0003"])

    def test_split_respects_target_and_never_splits_segment(self):
        segments = make_segments(4, chars=10)
        windows = build_windows(segments, target_chars=10, hard_max_chars=1000,
                                overlap_segments=0, context_segments=0)
        for w in windows:
            self.assertGreaterEqual(len(w.body_ids), 1)
        covered = [sid for w in windows for sid in w.body_ids]
        self.assertEqual(covered, ["seg_0001", "seg_0002", "seg_0003", "seg_0004"])

    def test_overlap_produces_shared_segments(self):
        segments = make_segments(4, chars=10)
        windows = build_windows(segments, target_chars=20, hard_max_chars=1000,
                                overlap_segments=1, context_segments=0)
        self.assertEqual(windows[0].body_ids, ["seg_0001", "seg_0002"])
        self.assertEqual(windows[1].body_ids, ["seg_0002", "seg_0003"])
        # El segmento compartido aparece en ambas ventanas.
        self.assertIn("seg_0002", windows[0].body_ids)
        self.assertIn("seg_0002", windows[1].body_ids)

    def test_single_segment_window_disables_overlap_to_advance(self):
        segments = make_segments(3, chars=10)
        windows = build_windows(segments, target_chars=10, hard_max_chars=1000,
                                overlap_segments=3, context_segments=0)
        covered = [sid for w in windows for sid in w.body_ids]
        self.assertEqual(covered, ["seg_0001", "seg_0002", "seg_0003"])

    def test_giant_segment_not_truncated(self):
        segments = make_segments(3, chars=10)
        segments[1] = seg("seg_big", 10.0, 100.0, "y" * 50)
        windows = build_windows(segments, target_chars=10, hard_max_chars=20,
                                overlap_segments=0, context_segments=0)
        all_ids = [sid for w in windows for sid in w.body_ids]
        self.assertIn("seg_big", all_ids)
        # El segmento gigante forma su propia ventana (no se parte).
        big_window = next(w for w in windows if "seg_big" in w.body_ids)
        self.assertEqual(big_window.body_ids, ["seg_big"])

    def test_context_ids_have_correct_neighbors(self):
        segments = make_segments(5, chars=10)
        windows = build_windows(segments, target_chars=10, hard_max_chars=1000,
                                overlap_segments=0, context_segments=1)
        second = windows[1]
        self.assertEqual(second.body_ids, ["seg_0002"])
        self.assertEqual(second.context_before_ids, ["seg_0001"])
        self.assertEqual(second.context_after_ids, ["seg_0003"])

    def test_determinism(self):
        segments = make_segments(7, chars=9)
        kwargs = dict(target_chars=20, hard_max_chars=1000, overlap_segments=1,
                      context_segments=2)
        first = build_windows(segments, **kwargs)
        second = build_windows(segments, **kwargs)
        self.assertEqual([w.body_ids for w in first], [w.body_ids for w in second])
        self.assertEqual(
            [w.context_before_ids for w in first],
            [w.context_before_ids for w in second],
        )

    def test_index_segments(self):
        segments = make_segments(2, chars=3)
        mapping = index_segments(segments)
        self.assertEqual(set(mapping), {"seg_0001", "seg_0002"})

    def test_invalid_config_rejected(self):
        segments = make_segments(1, chars=3)
        with self.assertRaises(ValueError):
            build_windows(segments, target_chars=0, hard_max_chars=10,
                          overlap_segments=0, context_segments=0)
        with self.assertRaises(ValueError):
            build_windows(segments, target_chars=10, hard_max_chars=5,
                          overlap_segments=0, context_segments=0)


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.config = EditorialConfig(model_id="test-model")
        self.segments = make_segments(3, chars=8)

    def _fp(self, segments=None, config=None):
        return build_fingerprint(
            transcription_digest=compute_transcription_digest(segments or self.segments),
            config=config or self.config,
            prompt_version="editorial_v1",
            project_id="p1",
            source_asset_id="a1",
            duration_seconds=30.0,
        )

    def test_same_input_matches(self):
        self.assertTrue(fingerprint_matches(self._fp(), self._fp()))

    def test_transcription_change_invalidates(self):
        other = make_segments(3, chars=8)
        other[0] = seg("seg_0001", 0.0, 10.0, "contenido distinto")
        self.assertFalse(fingerprint_matches(self._fp(), self._fp(segments=other)))
        diffs = fingerprint_diff(self._fp(), self._fp(segments=other))
        self.assertTrue(any("transcription_digest" in d for d in diffs))

    def test_config_hash_changes(self):
        other = EditorialConfig(model_id="test-model", temperature=0.9)
        self.assertNotEqual(compute_config_hash(self.config), compute_config_hash(other))
        self.assertFalse(fingerprint_matches(self._fp(), self._fp(config=other)))

    def test_model_change_invalidates(self):
        other = EditorialConfig(model_id="otro-modelo")
        self.assertFalse(fingerprint_matches(self._fp(), self._fp(config=other)))

    def test_missing_fingerprint_never_matches(self):
        self.assertFalse(fingerprint_matches(None, self._fp()))
        self.assertFalse(fingerprint_matches(self._fp(), None))

    def test_digest_is_stable_and_normalized(self):
        self.assertEqual(
            compute_transcription_digest(self.segments),
            compute_transcription_digest(list(self.segments)),
        )


if __name__ == "__main__":
    unittest.main()
