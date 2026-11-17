"""Pruebas de los subtitulos de la Fase 5C (servicio de renderizado).

Cubren, como minimo:
- Conversion de tiempos absolutos a relativos.
- Segmentos anteriores, posteriores y parcialmente contenidos en el clip.
- Segmentos con intervalos invalidos.
- Texto multilinea y caracteres especiales.
- Ausencia de timestamps por palabra.
- Archivo de subtitulos vacio o transcripcion sin segmentos utilizables.

Ejecutar con:
    python -B -m unittest tests.test_rendering_subtitles -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.rendering.errors import RenderingValidationError  # noqa: E402
from services.rendering.subtitles import (  # noqa: E402
    CALIBRATION_FACTOR,
    Cue,
    build_ass,
    build_cues,
    segment_bounds,
    segment_to_relative,
    split_text_into_blocks,
    wrap_text_metric,
)


def _avail_units(width=1080, margin=70):
    return (width - 2 * margin) / CALIBRATION_FACTOR


def seg(start, end, text="hola", segment_id="seg_x"):
    return {
        "start_seconds": start,
        "end_seconds": end,
        "text": text,
        "segment_id": segment_id,
        "words": [],
    }


class TestSegmentBounds(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(segment_bounds(seg(1.0, 2.0)), (1.0, 2.0))

    def test_missing_times(self):
        self.assertIsNone(segment_bounds({"text": "x"}))
        self.assertIsNone(segment_bounds({"start_seconds": None, "end_seconds": 2}))

    def test_non_numeric(self):
        self.assertIsNone(segment_bounds({"start_seconds": "a", "end_seconds": 2}))

    def test_negative_start(self):
        self.assertIsNone(segment_bounds(seg(-1.0, 2.0)))

    def test_zero_or_negative_duration(self):
        self.assertIsNone(segment_bounds(seg(2.0, 2.0)))
        self.assertIsNone(segment_bounds(seg(3.0, 2.0)))


class TestSegmentToRelative(unittest.TestCase):
    def test_fully_inside(self):
        self.assertEqual(
            segment_to_relative(
                seg(1102.0, 1105.0),
                absolute_start_seconds=1100.0,
                clip_duration_seconds=30.0,
            ),
            (2.0, 5.0),
        )

    def test_before_clip_is_dropped(self):
        self.assertIsNone(
            segment_to_relative(
                seg(1000.0, 1005.0),
                absolute_start_seconds=1100.0,
                clip_duration_seconds=30.0,
            )
        )

    def test_after_clip_is_dropped(self):
        self.assertIsNone(
            segment_to_relative(
                seg(1200.0, 1210.0),
                absolute_start_seconds=1100.0,
                clip_duration_seconds=30.0,
            )
        )

    def test_partial_at_start_is_clipped(self):
        # El segmento empieza antes del clip pero lo atraviesa.
        self.assertEqual(
            segment_to_relative(
                seg(1098.0, 1102.0),
                absolute_start_seconds=1100.0,
                clip_duration_seconds=30.0,
            ),
            (0.0, 2.0),
        )

    def test_partial_at_end_is_clipped(self):
        self.assertEqual(
            segment_to_relative(
                seg(1125.0, 1135.0),
                absolute_start_seconds=1100.0,
                clip_duration_seconds=30.0,
            ),
            (25.0, 30.0),
        )

    def test_invalid_interval_dropped(self):
        self.assertIsNone(
            segment_to_relative(
                seg(1105.0, 1105.0),
                absolute_start_seconds=1100.0,
                clip_duration_seconds=30.0,
            )
        )


class TestBuildCues(unittest.TestCase):
    def test_ordering_and_relative_times(self):
        segs = [
            seg(1105.0, 1108.0, "dos", "s2"),
            seg(1100.5, 1103.0, "uno", "s1"),
        ]
        cues = build_cues(segs, absolute_start_seconds=1100.0, clip_duration_seconds=30.0)
        self.assertEqual([c.source_segment_id for c in cues], ["s1", "s2"])
        self.assertAlmostEqual(cues[0].start_seconds, 0.5)
        self.assertAlmostEqual(cues[0].end_seconds, 3.0)
        self.assertAlmostEqual(cues[1].start_seconds, 5.0)

    def test_negative_times_never_emitted(self):
        segs = [seg(1099.0, 1101.0, "borde", "s1")]
        cues = build_cues(segs, absolute_start_seconds=1100.0, clip_duration_seconds=30.0)
        self.assertTrue(cues)
        self.assertGreaterEqual(cues[0].start_seconds, 0.0)

    def test_none_beyond_clip_duration(self):
        segs = [seg(1100.0, 1200.0, "largo", "s1")]
        cues = build_cues(segs, absolute_start_seconds=1100.0, clip_duration_seconds=5.0)
        self.assertTrue(cues)
        self.assertLessEqual(cues[0].end_seconds, 5.0)

    def test_zero_duration_clip_raises(self):
        with self.assertRaises(RenderingValidationError):
            build_cues(
                [seg(1100.0, 1101.0)],
                absolute_start_seconds=1100.0,
                clip_duration_seconds=0.0,
            )

    def test_empty_segments_returns_empty(self):
        self.assertEqual(
            build_cues([], absolute_start_seconds=0.0, clip_duration_seconds=10.0), []
        )

    def test_segments_without_usable_text_dropped(self):
        segs = [seg(1100.0, 1101.0, "   ", "s1")]
        self.assertEqual(
            build_cues(segs, absolute_start_seconds=1100.0, clip_duration_seconds=10.0),
            [],
        )

    def test_overlap_is_resolved(self):
        segs = [
            seg(1100.0, 1104.0, "uno", "s1"),
            seg(1103.0, 1106.0, "dos", "s2"),
        ]
        cues = build_cues(segs, absolute_start_seconds=1100.0, clip_duration_seconds=30.0)
        self.assertEqual(len(cues), 2)
        self.assertGreaterEqual(cues[1].start_seconds, cues[0].end_seconds)

    def test_sub_minimum_cue_dropped(self):
        # Segmento que apenas atraviesa el fin del clip (13 ms) se descarta.
        segs = [seg(1109.987, 1110.0, "flash", "s1")]
        cues = build_cues(
            segs,
            absolute_start_seconds=1100.0,
            clip_duration_seconds=10.0,
            min_cue_seconds=0.3,
        )
        self.assertEqual(cues, [])

    def test_minimum_cue_kept_when_long_enough(self):
        segs = [seg(1109.0, 1110.0, "ok", "s1")]
        cues = build_cues(
            segs,
            absolute_start_seconds=1100.0,
            clip_duration_seconds=10.0,
            min_cue_seconds=0.3,
        )
        self.assertEqual(len(cues), 1)

    def test_no_word_timestamps_does_not_crash(self):
        # La transcripcion real tiene words=[]; los cues usan solo el segmento.
        segs = [seg(1100.0, 1103.0, "sin palabras por token", "s1")]
        cues = build_cues(segs, absolute_start_seconds=1100.0, clip_duration_seconds=10.0)
        self.assertEqual(len(cues), 1)


class TestWrappingAndEscaping(unittest.TestCase):
    def test_multiline_wrapping(self):
        text = "palabra " * 20
        cues = build_cues(
            [seg(1100.0, 1103.0, text.strip(), "s1")],
            absolute_start_seconds=1100.0,
            clip_duration_seconds=10.0,
        )
        self.assertIn(r"\N", cues[0].text)

    def test_special_characters_escaped(self):
        cues = build_cues(
            [seg(1100.0, 1103.0, "código {no} <tag> & 'x'", "s1")],
            absolute_start_seconds=1100.0,
            clip_duration_seconds=10.0,
        )
        self.assertIn(r"\{no\}", cues[0].text)
        self.assertIn("código", cues[0].text)

    def test_whitespace_normalized(self):
        cues = build_cues(
            [seg(1100.0, 1103.0, "mucho    espacio\t\ny saltos", "s1")],
            absolute_start_seconds=1100.0,
            clip_duration_seconds=10.0,
        )
        self.assertNotIn("  ", cues[0].text)
        self.assertNotIn("\n", cues[0].text)


class TestBuildAss(unittest.TestCase):
    def test_empty_cues_produces_valid_header_only(self):
        content = build_ass([], width=1080, height=1920)
        self.assertIn("[Script Info]", content)
        self.assertIn("[V4+ Styles]", content)
        self.assertIn("[Events]", content)
        self.assertIn("PlayResX: 1080", content)
        self.assertIn("PlayResY: 1920", content)
        self.assertNotIn("Dialogue:", content)

    def test_dialogue_times_formatted(self):
        content = build_ass(
            [Cue(0.0, 2.5, "hola", "s1")], width=1080, height=1920
        )
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.50,Default", content)

    def test_margin_bottom_applied(self):
        content = build_ass(
            [Cue(0.0, 2.0, "x", "s1")],
            width=1080,
            height=1920,
            margin_bottom=320,
        )
        self.assertIn(",320,1", content)


class TestWrappingMetric(unittest.TestCase):
    def test_short_text_single_line(self):
        out = wrap_text_metric("hola mundo", available_units=_avail_units())
        self.assertNotIn(r"\N", out)

    def test_long_text_multiple_lines(self):
        text = "esta es una frase bastante larga que debe partirse en varias lineas"
        out = wrap_text_metric(text, available_units=_avail_units())
        self.assertIn(r"\N", out)

    def test_no_content_lost(self):
        text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        out = wrap_text_metric(text, available_units=_avail_units())
        self.assertEqual(
            out.replace(r"\N", " ").split(), text.split()
        )

    def test_lines_respect_available_width(self):
        # Con metrica real y un ancho amplio, dos lineas deben caber.
        from services.rendering.subtitles import _load_font, _measure_width

        font, ok = _load_font(r"C:\Windows\Fonts\ariblk.ttf", 96)
        if not ok:
            self.skipTest("Fuente de prueba no disponible")
        avail = _avail_units()
        text = "uno dos tres cuatro cinco seis siete ocho nueve diez"
        out = wrap_text_metric(text, available_units=avail)
        for line in out.split(r"\N"):
            self.assertLessEqual(
                _measure_width(line, font), avail,
                f"Linea excede ancho util: {line!r}",
            )

    def test_available_width_wider_needs_fewer_lines(self):
        text = "palabra " * 12
        narrow = wrap_text_metric(text.strip(), available_units=_avail_units(1080, 200))
        wide = wrap_text_metric(text.strip(), available_units=_avail_units(1080, 40))
        self.assertGreaterEqual(
            narrow.count(r"\N"), wide.count(r"\N")
        )

    def test_accented_and_special_chars_preserved(self):
        text = "camión código ¿qué? ¡sí! [test] {x}"
        out = wrap_text_metric(text, available_units=_avail_units())
        self.assertIn("camión", out)
        self.assertIn("código", out)


class TestSplitTextIntoBlocks(unittest.TestCase):
    def test_short_text_single_block(self):
        blocks = split_text_into_blocks("hola mundo", available_units=_avail_units())
        self.assertEqual(len(blocks), 1)

    def test_long_text_multiple_blocks(self):
        text = " ".join(["palabra%d" % i for i in range(80)])
        blocks = split_text_into_blocks(text, available_units=_avail_units())
        self.assertGreater(len(blocks), 1)
        # Cada bloque tiene como maximo dos lineas.
        for b in blocks:
            self.assertLessEqual(b.count(r"\N"), 1)

    def test_no_orphan_short_trailing_block(self):
        text = " ".join(["palabra%d" % i for i in range(50)])
        blocks = split_text_into_blocks(text, available_units=_avail_units())
        # El ultimo bloque no debe ser una sola palabra suelta.
        self.assertGreater(len(blocks[-1].split()), 1)

    def test_all_words_preserved_in_order(self):
        text = " ".join(["w%d" % i for i in range(60)])
        blocks = split_text_into_blocks(text, available_units=_avail_units())
        joined = " ".join(b.replace(r"\N", " ") for b in blocks).split()
        self.assertEqual(joined, text.split())

    def test_empty_text_returns_empty(self):
        self.assertEqual(
            split_text_into_blocks("   ", available_units=_avail_units()), []
        )


class TestBuildCuesLongSegment(unittest.TestCase):
    def test_long_segment_split_into_sequential_cues_within_interval(self):
        text = " ".join(["palabra%d" % i for i in range(80)])
        segs = [seg(1100.0, 1110.0, text, "s1")]
        cues = build_cues(
            segs, absolute_start_seconds=1100.0, clip_duration_seconds=10.0
        )
        self.assertGreater(len(cues), 1)
        # Todos los cues quedan dentro del intervalo del segmento.
        for c in cues:
            self.assertGreaterEqual(c.start_seconds, 0.0)
            self.assertLessEqual(c.end_seconds, 10.0)
        # Ordenados y sin huecos negativos.
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a.end_seconds, b.end_seconds + 1e-9)

    def test_all_words_preserved_across_cues(self):
        text = " ".join(["w%d" % i for i in range(60)])
        segs = [seg(1100.0, 1112.0, text, "s1")]
        cues = build_cues(
            segs, absolute_start_seconds=1100.0, clip_duration_seconds=12.0
        )
        joined = " ".join(c.text.replace(r"\N", " ") for c in cues).split()
        self.assertEqual(joined, text.split())

    def test_style_uses_thick_font_and_outline(self):
        content = build_ass(
            [Cue(0.0, 2.0, "hola", "s1")], width=1080, height=1920
        )
        self.assertIn("Arial Black", content)
        self.assertIn("WrapStyle: 0", content)


if __name__ == "__main__":
    unittest.main()
