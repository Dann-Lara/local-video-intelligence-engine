"""Pruebas de la biblioteca de motion graphics (Fase 5E, pasos 1-2).

Cubren: catalogo de efectos, validacion temporal, zonas seguras (sin solapar
subtitulos), margenes horizontales, resolucion contextual desde segmentos
reales, opcionalidad de los CTA, y serializacion ASS coherente.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.rendering.motion import (  # noqa: E402
    ALL_EFFECT_TYPES,
    ARROW_GLYPH,
    CTA_DEFAULT_GLYPHS,
    CTA_DEFAULT_LABELS,
    CTA_EFFECT_TYPES,
    EffectType,
    MotionEvent,
    MotionPlan,
    MotionValidationError,
    SUBTITLE_BAND_PX,
    _asset_top_limit,
    _escape_ass_text,
    _fit_font_size,
    _zone_center_y,
    build_motion_ass,
    build_motion_cues,
    build_plan_from_spec,
    event_from_spec,
    find_segment,
    resolve_event_window,
)
from services.rendering.subtitles import FONT_FILE_DEFAULT  # noqa: E402


def seg(sid, start, end, text="hola"):
    return {
        "segment_id": sid,
        "start_seconds": start,
        "end_seconds": end,
        "text": text,
        "words": [],
    }


class TestCatalog(unittest.TestCase):
    def test_all_effect_types_unique(self):
        self.assertEqual(len(ALL_EFFECT_TYPES), len(set(ALL_EFFECT_TYPES)))

    def test_cta_types_subset(self):
        for t in CTA_EFFECT_TYPES:
            self.assertIn(t, ALL_EFFECT_TYPES)

    def test_cta_have_default_labels(self):
        for t in CTA_EFFECT_TYPES:
            self.assertIn(t, CTA_DEFAULT_LABELS)


class TestGlyphCompatibility(unittest.TestCase):
    """Evita reintroducir glifos incompatibles con la fuente configurada.

    Un glifo ausente en Arial Black provocaria fallback de fuente o un hueco en
    libass (esto ya ocurrio con U+27A4). Estas pruebas exigen que todo glifo
    por defecto exista realmente en la fuente configurada.
    """

    def _cmap_or_skip(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:  # pragma: no cover
            self.skipTest("fontTools no disponible")
        try:
            return TTFont(FONT_FILE_DEFAULT).getBestCmap()
        except Exception:  # pragma: no cover
            self.skipTest(f"no se pudo abrir la fuente {FONT_FILE_DEFAULT}")

    def test_arrow_glyph_present_in_font(self):
        cmap = self._cmap_or_skip()
        self.assertIn(
            ord(ARROW_GLYPH),
            cmap,
            f"El glifo de flecha U+{ord(ARROW_GLYPH):04X} no existe en la fuente",
        )

    def test_arrow_glyph_is_not_the_old_incompatible_one(self):
        cmap = self._cmap_or_skip()
        # U+27A4 (heavy round-tipped rightwards arrow) NO esta en Arial Black.
        self.assertNotEqual(ord(ARROW_GLYPH), 0x27A4)
        self.assertNotIn(0x27A4, cmap)

    def test_all_cta_glyphs_present_in_font(self):
        cmap = self._cmap_or_skip()
        for effect_type, glyph in CTA_DEFAULT_GLYPHS.items():
            self.assertIn(
                ord(glyph),
                cmap,
                f"El glifo {effect_type} U+{ord(glyph):04X} no existe en la fuente",
            )

    def test_arrow_default_uses_verified_glyph(self):
        cues = build_motion_cues(
            MotionPlan(events=[MotionEvent(EffectType.ARROW, 1.0, 3.0, "")]),
            clip_duration=10.0,
        )
        self.assertIn(ARROW_GLYPH, cues[0].text)
        self.assertNotIn("\u27A4", cues[0].text)


class TestEscapeAssText(unittest.TestCase):
    def test_braces_escaped(self):
        self.assertEqual(_escape_ass_text("{x}"), r"\{x\}")

    def test_backslash(self):
        self.assertEqual(_escape_ass_text("a\\b"), "a\\\\b")

    def test_newline_to_ass_break(self):
        self.assertIn(r"\N", _escape_ass_text("a\nb"))


class TestZones(unittest.TestCase):
    def test_zone_centers_known(self):
        for pos in ("top", "upper_mid", "indicator", "cta"):
            self.assertGreater(_zone_center_y(pos), 0)

    def test_zone_unknown_raises(self):
        with self.assertRaises(MotionValidationError):
            _zone_center_y("nope")

    def test_asset_top_limit_leaves_subtitle_band(self):
        self.assertEqual(_asset_top_limit(1920), 1920 - SUBTITLE_BAND_PX)

    def test_all_zone_centers_above_subtitle_band(self):
        limit = _asset_top_limit(1920)
        for pos in ("top", "upper_mid", "indicator", "cta"):
            self.assertLess(_zone_center_y(pos), limit)


class TestIntervalValidation(unittest.TestCase):
    def test_valid_window(self):
        cues_probe = build_motion_cues(
            MotionPlan(
                events=[MotionEvent(EffectType.TYPOGRAPHY, 1.0, 2.0, "x")],
            ),
            clip_duration=10.0,
        )
        self.assertEqual(len(cues_probe), 1)

    def test_end_before_start_raises(self):
        with self.assertRaises(MotionValidationError):
            build_motion_cues(
                MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, 2.0, 1.0, "x")]),
                clip_duration=10.0,
            )

    def test_negative_start_raises(self):
        with self.assertRaises(MotionValidationError):
            build_motion_cues(
                MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, -1.0, 1.0, "x")]),
                clip_duration=10.0,
            )

    def test_exceeds_clip_raises(self):
        with self.assertRaises(MotionValidationError):
            build_motion_cues(
                MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, 1.0, 11.0, "x")]),
                clip_duration=10.0,
            )

    def test_zero_duration_raises(self):
        with self.assertRaises(MotionValidationError):
            build_motion_cues(
                MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, 1.0, 1.0, "x")]),
                clip_duration=10.0,
            )

    def test_unknown_effect_raises(self):
        with self.assertRaises(MotionValidationError):
            build_motion_cues(
                MotionPlan(events=[MotionEvent("explosion", 1.0, 2.0, "x")]),
                clip_duration=10.0,
            )

    def test_bad_margin_raises(self):
        with self.assertRaises(MotionValidationError):
            build_motion_cues(
                MotionPlan(
                    events=[MotionEvent(EffectType.TYPOGRAPHY, 1.0, 2.0, "x")],
                    margin_lr=600,
                ),
                clip_duration=10.0,
            )


class TestCueBuilding(unittest.TestCase):
    def test_cue_layer_is_one(self):
        cues = build_motion_cues(
            MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, 1.0, 2.0, "x")]),
            clip_duration=10.0,
        )
        ass = build_motion_ass(cues)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        self.assertEqual(len(dialogue), 1)
        self.assertTrue(dialogue[0].startswith("Dialogue: 1,"))

    def test_typography_has_scale_animation(self):
        cues = build_motion_cues(
            MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, 1.0, 2.0, "x")]),
            clip_duration=10.0,
        )
        self.assertIn(r"\fscx", cues[0].text)
        self.assertIn(r"\fad", cues[0].text)

    def test_slide_uses_move(self):
        cues = build_motion_cues(
            MotionPlan(
                events=[
                    MotionEvent(EffectType.TYPOGRAPHY, 1.0, 2.0, "x", slide_px=100)
                ]
            ),
            clip_duration=10.0,
        )
        self.assertIn(r"\move(", cues[0].text)
        self.assertNotIn(r"\pos(", cues[0].text)

    def test_no_slide_uses_pos(self):
        cues = build_motion_cues(
            MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, 1.0, 2.0, "x")]),
            clip_duration=10.0,
        )
        self.assertIn(r"\pos(", cues[0].text)

    def test_cta_label_default_applied(self):
        cues = build_motion_cues(
            MotionPlan(events=[MotionEvent(EffectType.CTA_SUBSCRIBE, 1.0, 3.0, "")]),
            clip_duration=10.0,
        )
        self.assertIn(CTA_DEFAULT_LABELS[EffectType.CTA_SUBSCRIBE], cues[0].text)

    def test_arrow_default_glyph(self):
        cues = build_motion_cues(
            MotionPlan(events=[MotionEvent(EffectType.ARROW, 1.0, 3.0, "")]),
            clip_duration=10.0,
        )
        self.assertIn(ARROW_GLYPH, cues[0].text)

    def test_styles_distinct(self):
        cues = build_motion_cues(
            MotionPlan(
                events=[
                    MotionEvent(EffectType.TYPOGRAPHY, 1.0, 2.0, "a"),
                    MotionEvent(EffectType.ARROW, 3.0, 4.0, "b"),
                    MotionEvent(EffectType.CTA_LIKE, 5.0, 6.0, "c"),
                ]
            ),
            clip_duration=10.0,
        )
        styles = {c.style for c in cues}
        self.assertEqual(styles, {"MotionTypo", "MotionArrow", "MotionCTA"})


class TestSafeZones(unittest.TestCase):
    """Ningun efecto puede invadir la franja inferior de subtitulos."""

    def test_bottom_position_clamped_above_subtitles(self):
        # Evento en 'cta' con fuente enorme: la caja resultante debe seguir por
        # encima del bloque de subtitulos.
        cues = build_motion_cues(
            MotionPlan(
                events=[MotionEvent(EffectType.CTA_LIKE, 1.0, 3.0, "X" * 40)],
                font_size=200,
            ),
            clip_duration=10.0,
        )
        # Extraer la coordenada y de la animacion y comprobar que < limite.
        text = cues[0].text
        import re

        m = re.search(r"\\move\(\d+,(\d+),", text)
        self.assertIsNotNone(m)
        y = int(m.group(1))
        self.assertLess(y, _asset_top_limit(1920))

    def test_font_size_shrinks_for_long_text(self):
        max_units = (1080 - 2 * 70) / 0.70710678
        from services.rendering.subtitles import FONT_FILE_DEFAULT

        big = _fit_font_size(
            "A" * 60,
            font_file=FONT_FILE_DEFAULT,
            font_size=96,
            max_units=max_units,
        )
        small = _fit_font_size(
            "A", font_file=FONT_FILE_DEFAULT, font_size=96, max_units=max_units
        )
        self.assertLess(big, small)


class TestResolutionFromSegments(unittest.TestCase):
    SEGS = [
        seg("seg_0213", 541.44, 548.94),
        seg("seg_0217", 567.84, 574.38),
    ]

    def test_find_segment(self):
        self.assertIsNotNone(find_segment(self.SEGS, "seg_0217"))
        self.assertIsNone(find_segment(self.SEGS, "nope"))

    def test_resolve_relative_window(self):
        start, end = resolve_event_window(
            self.SEGS,
            anchor="seg_0213",
            absolute_start_seconds=526.791111,
            clip_duration_seconds=58.1581,
        )
        self.assertAlmostEqual(start, 541.44 - 526.791111, places=5)
        self.assertAlmostEqual(end, 548.94 - 526.791111, places=5)

    def test_hold_caps_window(self):
        _, end = resolve_event_window(
            self.SEGS,
            anchor="seg_0217",
            absolute_start_seconds=526.791111,
            clip_duration_seconds=58.1581,
            hold_seconds=2.0,
        )
        start = 567.84 - 526.791111
        self.assertAlmostEqual(end, start + 2.0, places=5)

    def test_explicit_window_wins(self):
        start, end = resolve_event_window(
            self.SEGS,
            anchor=None,
            absolute_start_seconds=526.791111,
            clip_duration_seconds=58.1581,
            start_seconds=1.0,
            end_seconds=2.0,
        )
        self.assertEqual((start, end), (1.0, 2.0))

    def test_no_anchor_no_window_raises(self):
        with self.assertRaises(MotionValidationError):
            resolve_event_window(
                self.SEGS,
                anchor=None,
                absolute_start_seconds=526.791111,
                clip_duration_seconds=58.1581,
            )

    def test_missing_anchor_raises(self):
        with self.assertRaises(MotionValidationError):
            resolve_event_window(
                self.SEGS,
                anchor="missing",
                absolute_start_seconds=526.791111,
                clip_duration_seconds=58.1581,
            )

    def test_anchor_outside_clip_raises(self):
        segs = [seg("seg_far", 100.0, 110.0)]
        with self.assertRaises(MotionValidationError):
            resolve_event_window(
                segs,
                anchor="seg_far",
                absolute_start_seconds=526.791111,
                clip_duration_seconds=58.1581,
            )

    def test_event_from_spec_uses_segment(self):
        ev = event_from_spec(
            {"effect_type": EffectType.TYPOGRAPHY, "anchor": "seg_0213"},
            segments=self.SEGS,
            absolute_start_seconds=526.791111,
            clip_duration_seconds=58.1581,
        )
        self.assertEqual(ev.source_segment_id, "seg_0213")
        self.assertAlmostEqual(ev.start_seconds, 541.44 - 526.791111, places=5)

    def test_event_from_spec_rejects_bad_type(self):
        with self.assertRaises(MotionValidationError):
            event_from_spec(
                {"effect_type": "boom", "anchor": "seg_0213"},
                segments=self.SEGS,
                absolute_start_seconds=526.791111,
                clip_duration_seconds=58.1581,
            )


class TestContextualPlan(unittest.TestCase):
    SEGS = [seg("seg_0213", 541.44, 548.94), seg("seg_0217", 567.84, 574.38)]

    def test_build_plan_generates_only_requested_ctas(self):
        plan = build_plan_from_spec(
            [
                {"effect_type": EffectType.TYPOGRAPHY, "anchor": "seg_0213",
                 "text": "100 COPS"},
                {"effect_type": EffectType.CTA_SUBSCRIBE, "anchor": "seg_0213",
                 "hold_seconds": 2.0},
            ],
            segments=self.SEGS,
            absolute_start_seconds=526.791111,
            clip_duration_seconds=58.1581,
        )
        types = [e.effect_type for e in plan.events]
        self.assertIn(EffectType.CTA_SUBSCRIBE, types)
        self.assertNotIn(EffectType.CTA_LIKE, types)
        self.assertNotIn(EffectType.CTA_SHARE, types)

    def test_build_plan_no_events_is_empty(self):
        plan = build_plan_from_spec(
            [],
            segments=self.SEGS,
            absolute_start_seconds=526.791111,
            clip_duration_seconds=58.1581,
        )
        cues = build_motion_cues(plan, clip_duration=58.1581)
        self.assertEqual(cues, [])


class TestAssOutput(unittest.TestCase):
    def test_ass_has_expected_sections(self):
        cues = build_motion_cues(
            MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, 1.0, 2.0, "x")]),
            clip_duration=10.0,
        )
        ass = build_motion_ass(cues)
        for token in (
            "[Script Info]",
            "[V4+ Styles]",
            "[Events]",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "Style: MotionTypo",
        ):
            self.assertIn(token, ass)

    def test_ass_times_formatted(self):
        cues = build_motion_cues(
            MotionPlan(events=[MotionEvent(EffectType.TYPOGRAPHY, 1.5, 3.0, "x")]),
            clip_duration=10.0,
        )
        ass = build_motion_ass(cues)
        self.assertIn("0:00:01.50", ass)
        self.assertIn("0:00:03.00", ass)

    def test_empty_plan_produces_valid_header(self):
        ass = build_motion_ass([])
        self.assertIn("[Events]", ass)
        self.assertNotIn("Dialogue:", ass)


if __name__ == "__main__":
    unittest.main()
