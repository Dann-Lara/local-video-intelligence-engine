"""Pruebas de la regla de idioma de overlays: audio original manda.

Matriz exigida:

1. Audio en + subtitulos en -> tarjetas/énfasis/SEO en ingles.
2. Audio en + subtitulos es -> tarjetas/énfasis/SEO siguen en ingles.
3. Audio es + subtitulos es -> overlays y SEO en espanol.
4. Audio es + subtitulos en -> overlays y SEO siguen en espanol.
5. Audio mixto/ambiguo o traduccion sin audio declarado -> `needs_review`.
6. Idioma soportado sin catalogo de tarjetas (pt) -> error controlado, sin
   fallback silencioso a espanol/ingles.
7. SEO y overlays usan el mismo idioma canonico.
8. La localizacion no altera geometria, tiempos ni el texto de los subtitulos;
   los textos caben en su contenedor.

No se llama a la red, ni a LM Studio, ni se renderiza video completo. Las
tarjetas se rasterizan localmente con Pillow y las fuentes del sistema.

Ejecutar con:
    python -m unittest tests.test_delivery_overlay_language -v
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.delivery import pipeline  # noqa: E402
from services.delivery.language import (  # noqa: E402
    OverlayLanguageError,
    SeoLanguageError,
    build_language_context,
    require_emphasis_language,
    require_overlay_language,
    require_seo_language,
)
from services.delivery.seo import build_seo, validate_seo  # noqa: E402
from services.rendering.card_text import (  # noqa: E402
    MissingCardTranslation,
    resolve_card_text,
)
from services.rendering.cards import (  # noqa: E402
    DEFAULT_FONTS_DIR,
    build_card_for_action,
)
from tests.test_delivery_language import (  # noqa: E402
    DELIVERABLE,
    EN_JOB,
    EN_SEGMENTS,
    ES_SEGMENTS,
    MIXED_SEGMENTS,
    SPANISH_SUBTITLES,
)

EN_SUBTITLES = (
    "The team just escaped from 100 real cops and now they have until the end "
    "of the day to avoid being arrested."
)
EN_OVERRIDE = "WOULD YOU FALL FOR IT?"

PT_SEGMENTS = [
    {
        "segment_id": "seg_pt1",
        "start_seconds": 0.0,
        "end_seconds": 8.0,
        "text": (
            "não você está muito longe porque quando eu vou para casa mas não "
            "tem nada aqui e o plano não funciona"
        ),
    },
    {
        "segment_id": "seg_pt2",
        "start_seconds": 8.0,
        "end_seconds": 16.0,
        "text": (
            "eles estão muito perto e não podem escapar porque o carro não "
            "está aqui, então tudo vai dar errado"
        ),
    },
]

FONT_AVAILABLE = (
    (Path(DEFAULT_FONTS_DIR) / "seguisb.ttf").exists()
    and (Path(DEFAULT_FONTS_DIR) / "segoeui.ttf").exists()
)


class OverlayLanguageMatrixTest(unittest.TestCase):
    """El idioma canonico sale del audio, nunca de los subtitulos."""

    def test_audio_en_subtitulos_en(self):
        context = build_language_context(
            EN_SEGMENTS,
            source_audio_language="en",
            source_audio_language_source="job",
            subtitle_text=EN_SUBTITLES,
        )
        self.assertEqual(context["source_audio_language"], "en")
        self.assertEqual(context["transcript_language"], "en")
        self.assertEqual(context["subtitle_language"], "en")
        self.assertEqual(context["notification_language"], "en")
        self.assertEqual(context["emphasis_language"], "en")
        self.assertEqual(context["overlay_language"], "en")
        self.assertEqual(context["seo_language"], "en")
        self.assertEqual(context["overlay_language_decision"], "auto_from_source_audio")
        self.assertEqual(require_overlay_language(context), "en")
        self.assertEqual(require_emphasis_language(context), "en")
        self.assertEqual(require_seo_language(context), "en")

        card = resolve_card_text("comment", context["overlay_language"])
        self.assertEqual(card["title"], "COMMENT")
        self.assertEqual(card["source"], "card_text_catalog")

    def test_audio_en_subtitulos_es_mantiene_overlays_en(self):
        context = build_language_context(
            EN_SEGMENTS,
            source_audio_language="en",
            source_audio_language_source="job",
            subtitle_text=SPANISH_SUBTITLES,
        )
        self.assertEqual(context["transcript_language"], "en")
        self.assertEqual(context["subtitle_language"], "es")
        self.assertEqual(context["overlay_language"], "en")
        self.assertEqual(context["notification_language"], "en")
        self.assertEqual(context["emphasis_language"], "en")
        self.assertEqual(context["seo_language"], "en")
        self.assertNotEqual(
            context["transcript_language"], context["subtitle_language"]
        )

        card = resolve_card_text("comment", context["overlay_language"])
        self.assertEqual(card["title"], "COMMENT")
        self.assertNotEqual(card["title"], "COMENTA")

    def test_audio_es_subtitulos_es(self):
        context = build_language_context(
            ES_SEGMENTS,
            source_audio_language="es",
            source_audio_language_source="job",
            subtitle_text=" ".join(s["text"] for s in ES_SEGMENTS),
        )
        self.assertEqual(context["subtitle_language"], "es")
        self.assertEqual(context["overlay_language"], "es")
        self.assertEqual(context["notification_language"], "es")
        self.assertEqual(context["emphasis_language"], "es")
        self.assertEqual(context["seo_language"], "es")
        self.assertEqual(context["overlay_language_decision"], "auto_from_source_audio")

        card = resolve_card_text("comment", context["overlay_language"])
        self.assertEqual(card["title"], "COMENTA")

    def test_audio_es_subtitulos_en_mantiene_overlays_es(self):
        context = build_language_context(
            ES_SEGMENTS,
            source_audio_language="es",
            source_audio_language_source="job",
            subtitle_text=EN_SUBTITLES,
        )
        self.assertEqual(context["subtitle_language"], "en")
        self.assertEqual(context["overlay_language"], "es")
        self.assertEqual(context["notification_language"], "es")
        self.assertEqual(context["emphasis_language"], "es")
        self.assertEqual(context["seo_language"], "es")

        card = resolve_card_text("comment", context["overlay_language"])
        self.assertEqual(card["title"], "COMENTA")

    def test_audio_mixto_o_ambiguo_requiere_revision(self):
        context = build_language_context(MIXED_SEGMENTS)
        self.assertIsNone(context["overlay_language"])
        self.assertIsNone(context["notification_language"])
        self.assertIsNone(context["emphasis_language"])
        self.assertIsNone(context["seo_language"])
        self.assertEqual(context["overlay_language_decision"], "needs_review")
        with self.assertRaises(OverlayLanguageError):
            require_overlay_language(context)
        with self.assertRaises(OverlayLanguageError):
            require_emphasis_language(context)
        with self.assertRaises(SeoLanguageError):
            require_seo_language(context)

    def test_traduccion_sin_audio_declarado_requiere_revision(self):
        context = build_language_context(
            ES_SEGMENTS, transcript_is_translation=True
        )
        self.assertIsNone(context["overlay_language"])
        self.assertEqual(context["overlay_language_decision"], "needs_review")
        self.assertIn("traduccion", context["overlay_language_reason"])
        with self.assertRaises(OverlayLanguageError):
            require_overlay_language(context)

    def test_traduccion_con_audio_declarado_usa_el_audio(self):
        context = build_language_context(
            ES_SEGMENTS,
            source_audio_language="en",
            source_audio_language_source="job",
            transcript_is_translation=True,
            subtitle_text=" ".join(s["text"] for s in ES_SEGMENTS),
        )
        self.assertEqual(context["transcript_language"], "es")
        self.assertEqual(context["overlay_language"], "en")
        self.assertEqual(context["seo_language"], "en")
        self.assertEqual(context["overlay_language_decision"], "auto_from_source_audio")
        # El enfasis literal no puede salir de una traduccion.
        self.assertIsNone(context["emphasis_language"])
        with self.assertRaises(OverlayLanguageError):
            require_emphasis_language(context)

    def test_idioma_sin_catalogo_es_error_controlado(self):
        context = build_language_context(
            PT_SEGMENTS,
            source_audio_language="pt",
            source_audio_language_source="job",
            subtitle_text=" ".join(s["text"] for s in PT_SEGMENTS),
        )
        self.assertEqual(context["overlay_language"], "pt")
        self.assertEqual(require_overlay_language(context), "pt")
        with self.assertRaises(MissingCardTranslation) as raised:
            resolve_card_text("comment", context["overlay_language"])
        details = raised.exception.details
        self.assertEqual(details["language"], "pt")
        self.assertEqual(details["supported_languages"], ["en", "es"])

    def test_override_en_otro_idioma_se_rechaza(self):
        with self.assertRaises(OverlayLanguageError):
            pipeline._assert_overlay_text_language(
                "notificacion 1 (subtitle_text)",
                "El equipo acaba de escapar de cien policias reales y ahora "
                "tiene hasta el final del dia, pero no saben si los policias "
                "estan muy cerca de ellos",
                "en",
            )
        # Un texto corto/ambiguo se acepta: el detector no puede decidir.
        pipeline._assert_overlay_text_language(
            "notificacion 1 (subtitle_text)", EN_OVERRIDE, "en"
        )

    def test_seo_y_overlays_comparten_idioma(self):
        context = build_language_context(
            EN_SEGMENTS,
            source_audio_language="en",
            source_audio_language_source="job",
            subtitle_text=SPANISH_SUBTITLES,
        )
        self.assertEqual(context["seo_language"], context["overlay_language"])
        markdown = build_seo(EN_JOB, DELIVERABLE, language_context=context)
        result = validate_seo(
            markdown,
            segments=EN_SEGMENTS,
            deliverable=DELIVERABLE,
            verified_sources=[s["url"] for s in EN_JOB["seo"]["sources"]],
            language_context=context,
        )
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(result["checks"]["seo_language_matches_overlay_language"])
        self.assertTrue(result["checks"]["seo_language_decision_recorded"])
        self.assertEqual(result["language"]["seo_language"], "en")


@unittest.skipUnless(FONT_AVAILABLE, "faltan fuentes del sistema para las tarjetas")
class LocalizationGeometryTest(unittest.TestCase):
    """Localizar textos no cambia geometria, tiempos ni subtitulos."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="overlay_lang_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_tarjeta_localizada_no_altera_geometria_ni_tiempos(self):
        notification = {
            "action": "comment",
            "subtitle_text": EN_OVERRIDE,
            "enter_start_seconds": 6.0,
            "hold_start_seconds": 6.52,
            "hold_end_seconds": 10.0,
            "exit_end_seconds": 10.42,
        }
        before = copy.deepcopy(notification)

        context = build_language_context(
            EN_SEGMENTS,
            source_audio_language="en",
            source_audio_language_source="job",
        )
        language = require_overlay_language(context)
        text = resolve_card_text(notification["action"], language)

        png_a = self.tmp / "a.png"
        png_b = self.tmp / "b.png"
        image_a, report_a = build_card_for_action(
            notification["action"],
            png_a,
            subtitle_text=notification["subtitle_text"],
            language=language,
        )
        image_b, report_b = build_card_for_action(
            notification["action"],
            png_b,
            subtitle_text=notification["subtitle_text"],
            language=language,
        )

        # La notificacion original no se modifica al localizar.
        self.assertEqual(notification, before)
        # Misma geometria y misma animacion (invariantes del MVP).
        self.assertEqual(list(image_a.size), [932, 312])
        self.assertEqual(list(image_b.size), [932, 312])
        self.assertEqual(report_a["icon_center_y"], 124.0)
        self.assertEqual(report_a["chevron_center_y"], 124.0)
        self.assertEqual(report_a["content_area_center"], 124.0)
        self.assertEqual(report_a["content_area_center"], report_b["content_area_center"])
        self.assertEqual(report_a["icon_center_y"], report_b["icon_center_y"])
        self.assertEqual(report_a["chevron_center_y"], report_b["chevron_center_y"])
        # Texto dentro del contenedor y render determinista.
        self.assertTrue(report_a["fits"], report_a)
        self.assertLessEqual(report_a["sub_bottom_ink_y"], 260)
        self.assertEqual(png_a.read_bytes(), png_b.read_bytes())

        self.assertEqual(report_a["title_text"], "COMMENT")
        self.assertEqual(report_a["subtitle_text"], EN_OVERRIDE)
        self.assertEqual(
            report_a["localization"]["subtitle_source"], "job_override"
        )

    def test_tarjeta_en_y_es_comparten_invariantes(self):
        languages = ["en", "es"]
        reports = {}
        sizes = {}
        for language in languages:
            png = self.tmp / f"{language}.png"
            image, report = build_card_for_action(
                "comment", png, language=language
            )
            reports[language] = report
            sizes[language] = list(image.size)

        for language, report in reports.items():
            self.assertTrue(report["fits"], (language, report))
            self.assertEqual(sizes[language], [932, 312])
            self.assertEqual(report["icon_center_y"], 124.0)
            self.assertEqual(report["chevron_center_y"], 124.0)
            self.assertEqual(report["language"], language)

        # La localizacion no cambia la geometria entre idiomas.
        self.assertEqual(
            reports["en"]["icon_center_y"], reports["es"]["icon_center_y"]
        )
        self.assertEqual(
            reports["en"]["chevron_center_y"], reports["es"]["chevron_center_y"]
        )
        self.assertEqual(
            reports["en"]["content_area_center"],
            reports["es"]["content_area_center"],
        )

        # El texto historico de `card_family.json` no se usa como fallback.
        self.assertEqual(reports["en"]["title_text"], "COMMENT")
        self.assertEqual(reports["es"]["title_text"], "COMENTA")
        self.assertNotEqual(
            reports["en"]["title_text"], reports["es"]["title_text"]
        )


if __name__ == "__main__":
    unittest.main()
