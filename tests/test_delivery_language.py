"""Pruebas de la regla de idioma del SEO (transcripcion -> SEO).

Casos exigidos:

1. Transcripcion en ingles -> SEO en ingles.
2. Transcripcion en espanol -> SEO en espanol.
3. Otro idioma con confianza suficiente (italiano) -> SEO en ese idioma.
4. Transcripcion mixta/ambigua -> `needs_review`, sin paquete SEO.
5. Subtitulos en un idioma distinto -> el SEO sigue a la transcripcion.
6. Un campo copiable en otro idioma invalida el paquete.
7. Short 6: SEO corregido sin volver a renderizar ni tocar el MP4.
8. Un SEO fallido se reanuda sin re-render y conserva el MP4.
9. COD Mobile y el libro no pasan el filtro; una mencion incidental si.
10. Los entregables previos conservan sus hashes.

No se llama a la red, ni a LM Studio, ni se renderiza video.

Ejecutar con:
    python -m unittest tests.test_delivery_language -v
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.delivery import baselines, pipeline  # noqa: E402
from services.delivery.language import (  # noqa: E402
    SeoLanguageError,
    build_language_context,
    detect_language,
    require_seo_language,
)
from services.delivery.pipeline import (  # noqa: E402
    DeliveryError,
    validate_candidate,
)
from services.delivery.seo import build_seo, declared_language, validate_seo  # noqa: E402
from services.editorial.parse import detect_promotional_evidence  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures de transcripcion (texto literal, sin inventar hechos)
# ---------------------------------------------------------------------------

EN_SEGMENTS = [
    {
        "segment_id": "seg_0016",
        "start_seconds": 26.0,
        "end_seconds": 30.0,
        "text": "Me and my four friends just escaped from 100 real cops,",
    },
    {
        "segment_id": "seg_0019",
        "start_seconds": 32.0,
        "end_seconds": 34.0,
        "text": "They have until the end of the day to arrest us",
    },
    {
        "segment_id": "seg_0021",
        "start_seconds": 36.0,
        "end_seconds": 38.0,
        "text": "I spent months setting up hundreds of cameras,",
    },
    {
        "segment_id": "seg_0022",
        "start_seconds": 38.0,
        "end_seconds": 40.0,
        "text": "booby traps, and secret rooms.",
    },
    {
        "segment_id": "seg_0023",
        "start_seconds": 40.0,
        "end_seconds": 43.0,
        "text": "Let's just hope it's enough to keep us from getting caught.",
    },
]

ES_SEGMENTS = [
    {
        "segment_id": "seg_es1",
        "start_seconds": 0.0,
        "end_seconds": 8.0,
        "text": (
            "El equipo acaba de escapar de 100 policías reales y ahora tiene "
            "hasta el final del día para que no los arresten."
        ),
    },
    {
        "segment_id": "seg_es2",
        "start_seconds": 8.0,
        "end_seconds": 16.0,
        "text": (
            "Su plan depende de cámaras, trampas y habitaciones secretas, "
            "pero la persecución no ha terminado."
        ),
    },
]

IT_SEGMENTS = [
    {
        "segment_id": "seg_it1",
        "start_seconds": 0.0,
        "end_seconds": 8.0,
        "text": (
            "La squadra è appena scappata da 100 poliziotti veri e ora ha fino "
            "alla fine della giornata per non essere arrestata."
        ),
    },
    {
        "segment_id": "seg_it2",
        "start_seconds": 8.0,
        "end_seconds": 16.0,
        "text": (
            "Il piano dipende da telecamere, trappole e stanze segrete, ma "
            "l'inseguimento non è ancora finito perché i poliziotti sono "
            "molto vicini."
        ),
    },
]

MIXED_SEGMENTS = [
    {
        "segment_id": "seg_mix1",
        "start_seconds": 0.0,
        "end_seconds": 5.0,
        "text": (
            "The team escaped from 100 policias reales y ahora tiene hasta el "
            "final del dia."
        ),
    },
    {
        "segment_id": "seg_mix2",
        "start_seconds": 5.0,
        "end_seconds": 10.0,
        "text": (
            "We have cameras, trampas y habitaciones secretas, but the chase "
            "is not over because they are very close."
        ),
    },
]

SPANISH_SUBTITLES = (
    "El equipo acaba de escapar de 100 policías reales y ahora tiene hasta "
    "el final del día para que no los arresten."
)

DELIVERABLE = {
    "path": "output/clip_0006_shorts_mvp_20261029.mp4",
    "file_name": "clip_0006_shorts_mvp_20261029.mp4",
    "bytes": 19031096,
    "sha256": "84BC8E1C9D728DEAF1A23A626C101FBB261E6B92DBD665CA7402C3BD4D44BDBE",
    "duration_seconds": 16.950267,
}

SOURCES = {
    "en": [
        {
            "classification": "official_requirement",
            "url": "https://support.google.com/youtube/answer/10059070",
            "checked_on": "2026-10-29",
            "claim": "Shorts can be up to 3 minutes long.",
        },
        {
            "classification": "official_requirement",
            "url": "https://support.google.com/youtube/answer/57407",
            "checked_on": "2026-10-29",
            "claim": "The title allows up to 100 characters and the description up to 5,000.",
        },
        {
            "classification": "editorial_recommendation",
            "url": "https://support.google.com/youtube/answer/6390658",
            "checked_on": "2026-10-29",
            "claim": "Up to three hashtags can be shown next to the title.",
        },
        {
            "classification": "hypothesis",
            "url": "https://support.google.com/youtube/answer/6390658",
            "checked_on": "2026-10-29",
            "claim": "Own hypothesis, not verified, about topic classification.",
        },
    ],
    "es": [
        {
            "classification": "official_requirement",
            "url": "https://support.google.com/youtube/answer/10059070",
            "checked_on": "2026-10-29",
            "claim": "Los Shorts pueden durar hasta 3 minutos.",
        },
        {
            "classification": "official_requirement",
            "url": "https://support.google.com/youtube/answer/57407",
            "checked_on": "2026-10-29",
            "claim": "El titulo admite hasta 100 caracteres.",
        },
        {
            "classification": "editorial_recommendation",
            "url": "https://support.google.com/youtube/answer/6390658",
            "checked_on": "2026-10-29",
            "claim": "Hasta tres hashtags pueden mostrarse junto al titulo.",
        },
        {
            "classification": "hypothesis",
            "url": "https://support.google.com/youtube/answer/6390658",
            "checked_on": "2026-10-29",
            "claim": "Hipotesis propia, no verificada, sobre clasificacion tematica.",
        },
    ],
    "it": [
        {
            "classification": "official_requirement",
            "url": "https://support.google.com/youtube/answer/10059070",
            "checked_on": "2026-10-29",
            "claim": "Gli Short possono durare fino a 3 minuti.",
        },
        {
            "classification": "official_requirement",
            "url": "https://support.google.com/youtube/answer/57407",
            "checked_on": "2026-10-29",
            "claim": "Il titolo ammette fino a 100 caratteri.",
        },
        {
            "classification": "editorial_recommendation",
            "url": "https://support.google.com/youtube/answer/6390658",
            "checked_on": "2026-10-29",
            "claim": "Fino a tre hashtag possono apparire accanto al titolo.",
        },
        {
            "classification": "hypothesis",
            "url": "https://support.google.com/youtube/answer/6390658",
            "checked_on": "2026-10-29",
            "claim": "Ipotesi propria, non verificata, sulla classificazione tematica.",
        },
    ],
}


def make_job(segments, *, language, seo, clip_id="clip_9999"):
    return {
        "job_id": f"test_lang_{language}",
        "project_id": "test_project",
        "source_asset_id": "test_asset",
        "output_stem": f"{clip_id}_shorts_lang_test",
        "source": {
            "path": str(ROOT / "input" / "no_usado_en_estas_pruebas.mp4"),
            "bytes": 10,
            "sha256": "0" * 64,
        },
        "transcription_path": str(ROOT / "projects" / "test" / "transcription.json"),
        "candidate": {
            "clip_id": clip_id,
            "title": seo["title"],
            "start_seconds": 26.06,
            "end_seconds": 43.0,
            "requested_start_seconds": 26.0,
            "requested_end_seconds": 43.0,
            "evidence_refs": [s["segment_id"] for s in segments],
            "evidence_text": " ".join(s["text"] for s in segments),
            "ad_classification": "none",
            "ad_evidence": "",
            "editorial_decision": "accept",
            "boundary_evaluation": {"start_ok": True, "end_ok": True},
            "confidence": 0.86,
        },
        "seo": seo,
    }


EN_JOB = make_job(
    EN_SEGMENTS,
    language="en",
    seo={
        "title": "They Escaped 100 Real Cops",
        "alt_titles": [
            "100 Real Cops Are Chasing Them: Can They Escape?",
            "Cameras, Traps and Secret Rooms to Avoid Arrest",
        ],
        "description": (
            "Me and four friends just escaped from 100 real cops, at least for "
            "now. They have until the end of the day to arrest them, and the "
            "plan depends on hundreds of cameras, booby traps and secret "
            "rooms.\n\n"
            "Editorial excerpt from the original video. It is not a guide and "
            "it is not a promise of results."
        ),
        "hashtags": ["#Shorts", "#Challenge", "#Escape"],
        "tags": ["escape", "100 real cops", "challenge", "cameras", "shorts"],
        "pinned_comment": (
            "How would you try to avoid being caught? Tell us in the comments."
        ),
        "thumbnail_text": "100 REAL COPS",
        "language": "en",
        "audience": "not_made_for_kids",
        "facts": [
            {
                "claim": "me and my four friends just escaped from 100 real cops",
                "segment_id": "seg_0016",
            },
            {
                "claim": "they have until the end of the day to arrest us",
                "segment_id": "seg_0019",
            },
            {
                "claim": "booby traps, and secret rooms",
                "segment_id": "seg_0022",
            },
        ],
        "sources": SOURCES["en"],
        "notes": "Generated from the real transcript of the fragment.",
    },
)

ES_JOB = make_job(
    ES_SEGMENTS,
    language="es",
    seo={
        "title": "Escaparon de 100 policías reales",
        "alt_titles": [
            "100 policías reales los persiguen: ¿podrán escapar?",
            "Cámaras, trampas y cuartos secretos para no ser atrapados",
        ],
        "description": (
            "El equipo acaba de escapar de 100 policías reales y ahora tiene "
            "hasta el final del día para que no los arresten. Su plan depende "
            "de cámaras, trampas y habitaciones secretas.\n\n"
            "Fragmento editorial del video original. No es una guía ni una "
            "promesa de resultados."
        ),
        "hashtags": ["#Shorts", "#Reto", "#Persecución"],
        "tags": ["reto", "escape", "policías", "shorts"],
        "pinned_comment": "¿Qué harías tú para no ser atrapado?",
        "thumbnail_text": "100 POLICÍAS",
        "language": "es",
        "audience": "not_made_for_kids",
        "facts": [
            {
                "claim": "el equipo acaba de escapar de 100 policías reales",
                "segment_id": "seg_es1",
            },
            {
                "claim": "cámaras, trampas y habitaciones secretas",
                "segment_id": "seg_es2",
            },
        ],
        "sources": SOURCES["es"],
        "notes": "Generado a partir de la transcripcion real del fragmento.",
    },
)

IT_JOB = make_job(
    IT_SEGMENTS,
    language="it",
    seo={
        "title": "Sono scappati da 100 poliziotti veri",
        "alt_titles": [
            "100 poliziotti veri li stanno cercando: riusciranno a scappare?",
            "Telecamere, trappole e stanze segrete per non essere arrestati",
        ],
        "description": (
            "La squadra è appena scappata da 100 poliziotti veri e ora ha fino "
            "alla fine della giornata per non essere arrestata. Il piano "
            "dipende da telecamere, trappole e stanze segrete, ma "
            "l'inseguimento non è ancora finito.\n\n"
            "Estratto editoriale del video originale. Non è una guida e non è "
            "una promessa di risultati."
        ),
        "hashtags": ["#Shorts", "#Sfida", "#Inseguimento"],
        "tags": ["sfida", "fuga", "poliziotti", "shorts"],
        "pinned_comment": (
            "Cosa faresti per non essere arrestato? Raccontacelo nei commenti."
        ),
        "thumbnail_text": "100 POLIZIOTTI VERI",
        "language": "it",
        "audience": "not_made_for_kids",
        "facts": [
            {
                "claim": "la squadra è appena scappata da 100 poliziotti veri",
                "segment_id": "seg_it1",
            },
            {
                "claim": "il piano dipende da telecamere, trappole e stanze segrete",
                "segment_id": "seg_it2",
            },
        ],
        "sources": SOURCES["it"],
        "notes": "Generato dalla trascrizione reale del frammento.",
    },
)


def validate(markdown, job, segments, context):
    return validate_seo(
        markdown,
        segments=segments,
        deliverable=DELIVERABLE,
        verified_sources=[s["url"] for s in job["seo"]["sources"]],
        language_context=context,
    )


class LanguageSelectionTest(unittest.TestCase):
    """El idioma del SEO sale de la transcripcion, no de una preferencia fija."""

    def test_ingles_a_ingles(self):
        context = build_language_context(EN_SEGMENTS)
        self.assertEqual(context["transcript_language"], "en")
        self.assertEqual(context["seo_language"], "en")
        self.assertEqual(context["seo_language_decision"], "auto_from_audio_transcript")
        self.assertEqual(require_seo_language(context), "en")

        markdown = build_seo(EN_JOB, DELIVERABLE, language_context=context)
        self.assertEqual(declared_language(markdown), "en")
        self.assertIn("## 2. Title", markdown)
        self.assertNotIn("## 2. Titulo", markdown)
        result = validate(markdown, EN_JOB, EN_SEGMENTS, context)
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(result["checks"]["language_fields_consistent"])
        self.assertTrue(result["checks"]["seo_language_matches_transcript"])
        self.assertTrue(result["checks"]["seo_language_decision_recorded"])

    def test_espanol_a_espanol(self):
        context = build_language_context(ES_SEGMENTS)
        self.assertEqual(context["transcript_language"], "es")
        self.assertEqual(context["seo_language"], "es")
        self.assertEqual(context["seo_language_decision"], "auto_from_audio_transcript")

        markdown = build_seo(ES_JOB, DELIVERABLE, language_context=context)
        self.assertEqual(declared_language(markdown), "es")
        self.assertIn("## 2. Titulo", markdown)
        result = validate(markdown, ES_JOB, ES_SEGMENTS, context)
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(result["checks"]["language_fields_consistent"])

    def test_italiano_con_confianza_suficiente(self):
        context = build_language_context(IT_SEGMENTS)
        self.assertEqual(context["transcript_language"], "it")
        self.assertEqual(context["seo_language"], "it")
        self.assertEqual(context["seo_language_decision"], "auto_from_audio_transcript")
        self.assertGreaterEqual(context["transcript_language_confidence"], 0.60)

        markdown = build_seo(IT_JOB, DELIVERABLE, language_context=context)
        self.assertEqual(declared_language(markdown), "it")
        self.assertIn("## 2. Titolo", markdown)
        result = validate(markdown, IT_JOB, IT_SEGMENTS, context)
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(result["checks"]["language_fields_consistent"])

    def test_mixto_o_ambiguo_requiere_revision(self):
        detection = detect_language(" ".join(s["text"] for s in MIXED_SEGMENTS))
        self.assertEqual(detection["decision"], "needs_review")
        self.assertIsNone(detection["language"])

        context = build_language_context(MIXED_SEGMENTS)
        self.assertIsNone(context["seo_language"])
        self.assertEqual(context["seo_language_decision"], "needs_review")
        with self.assertRaises(SeoLanguageError):
            require_seo_language(context)

        # No se genera ningun paquete SEO sin idioma decidido.
        with self.assertRaises(SeoLanguageError):
            build_seo(EN_JOB, DELIVERABLE, language_context=context)

    def test_subtitulos_en_otro_idioma_no_cambian_el_seo(self):
        context = build_language_context(
            EN_SEGMENTS, subtitle_text=SPANISH_SUBTITLES
        )
        self.assertEqual(context["transcript_language"], "en")
        self.assertEqual(context["subtitle_language"], "es")
        self.assertEqual(context["seo_language"], "en")
        self.assertEqual(context["seo_language_decision"], "auto_from_audio_transcript")
        self.assertNotEqual(
            context["transcript_language"], context["subtitle_language"]
        )

        markdown = build_seo(EN_JOB, DELIVERABLE, language_context=context)
        result = validate(markdown, EN_JOB, EN_SEGMENTS, context)
        self.assertTrue(result["valid"], result["errors"])
        self.assertIn("**Subtitle language:** Spanish (es)", markdown)
        self.assertIn("**SEO language:** English (en)", markdown)

    def test_un_campo_en_otro_idioma_invalida_el_paquete(self):
        context = build_language_context(EN_SEGMENTS)
        markdown = build_seo(EN_JOB, DELIVERABLE, language_context=context)
        tampered = markdown.replace(
            EN_JOB["seo"]["description"], ES_JOB["seo"]["description"]
        )
        result = validate(tampered, EN_JOB, EN_SEGMENTS, context)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["language_fields_consistent"])
        self.assertTrue(
            any("Idioma inconsistente" in error for error in result["errors"]),
            result["errors"],
        )


class ResumeLanguageSeoTest(unittest.TestCase):
    """Regenerar el SEO no re-renderiza ni toca el MP4 ya entregado."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lang_resume_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.output = self.tmp / "output"
        self.output.mkdir()
        self.mp4 = self.tmp / "clip_0006_shorts_mvp_20261029.mp4"
        self.mp4.write_bytes(b"mp4-de-prueba-sin-render")
        self.source = self.tmp / "source.mp4"
        self.source.write_bytes(b"fuente-de-prueba")

    def _write_job(self, segments, *, output_stem=None, seo_output_name=None):
        job = json.loads(json.dumps(EN_JOB))
        job["job_id"] = "test_short6_seo_only"
        job["output_stem"] = output_stem or "clip_0006_shorts_mvp_20261029"
        job["source"] = {
            "path": str(self.source),
            "bytes": self.source.stat().st_size,
            "sha256": pipeline.sha256_file(self.source),
        }
        job["transcription_path"] = str(self.tmp / "transcription.json")
        job["seo_output_name"] = (
            seo_output_name or "clip_0006_shorts_mvp_20261029.seo_en_v2.md"
        )
        job["expected_mp4_sha256"] = pipeline.sha256_file(self.mp4)
        job["candidate"]["evidence_refs"] = [s["segment_id"] for s in segments]
        job["candidate"]["evidence_text"] = " ".join(s["text"] for s in segments)
        (self.tmp / "transcription.json").write_text(
            json.dumps({"language": "en", "segments": segments}, ensure_ascii=False),
            encoding="utf-8",
        )
        path = self.tmp / "job.json"
        path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        return job, path

    def _seed_state(self):
        sha = pipeline.sha256_file(self.mp4)
        state = {
            "job_id": "test_short6_seo_only",
            "created_at": "2026-10-29T00:00:00+00:00",
            "stages": {
                "render": {
                    "status": "succeeded",
                    "started_at": "2026-10-29T00:00:00+00:00",
                    "finished_at": "2026-10-29T00:00:10+00:00",
                },
                "validate_mp4": {
                    "status": "succeeded",
                    "started_at": "2026-10-29T00:00:10+00:00",
                    "finished_at": "2026-10-29T00:00:12+00:00",
                },
            },
            "deliverable": {
                "path": str(self.mp4),
                "file_name": self.mp4.name,
                "bytes": self.mp4.stat().st_size,
                "sha256": sha,
                "duration_seconds": 16.950267,
            },
        }
        (self.work / "state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        return state

    def _forbidden_render(self):
        def forbidden(ctx):
            raise AssertionError("no se debe re-renderizar ni revalidar el MP4")

        return {"render": forbidden, "validate_mp4": forbidden}

    def test_short6_regenera_seo_sin_render(self):
        _, job_path = self._write_job(EN_SEGMENTS)
        self._seed_state()
        before = self.mp4.read_bytes()

        patched = dict(pipeline.STAGE_FUNCS)
        patched.update(self._forbidden_render())
        with mock.patch.dict(pipeline.STAGE_FUNCS, patched, clear=True):
            result = pipeline.run_job(
                job_path,
                stages="render,validate_mp4,seo,validate_seo",
                work_dir=self.work,
                output_dir=self.output,
            )

        statuses = [item["status"] for item in result["stages"]]
        self.assertEqual(
            statuses,
            ["skipped_succeeded", "skipped_succeeded", "succeeded", "succeeded"],
        )
        self.assertEqual(self.mp4.read_bytes(), before)
        published = self.output / "clip_0006_shorts_mvp_20261029.seo_en_v2.md"
        self.assertTrue(published.exists())
        markdown = published.read_text(encoding="utf-8")
        self.assertEqual(declared_language(markdown), "en")
        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["language"]["seo_language"], "en")
        self.assertIn("## 4. Hashtags", markdown)
        self.assertIn("**Status:** pending_user_review", markdown)

    def test_fallo_de_idioma_no_publica_y_conserva_el_mp4(self):
        _, job_path = self._write_job(MIXED_SEGMENTS)
        self._seed_state()
        before = self.mp4.read_bytes()

        patched = dict(pipeline.STAGE_FUNCS)
        patched.update(self._forbidden_render())
        with mock.patch.dict(pipeline.STAGE_FUNCS, patched, clear=True):
            with self.assertRaises(DeliveryError) as raised:
                pipeline.run_job(
                    job_path,
                    stages="render,validate_mp4,seo",
                    work_dir=self.work,
                    output_dir=self.output,
                )

        details = raised.exception.details
        self.assertTrue(details.get("needs_review"))
        self.assertTrue(details.get("render_preserved"))
        self.assertEqual(self.mp4.read_bytes(), before)
        self.assertEqual(list(self.output.iterdir()), [])
        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stages"]["seo"]["status"], "failed")
        self.assertEqual(state["stages"]["render"]["status"], "succeeded")
        self.assertEqual(state["stages"]["validate_mp4"]["status"], "succeeded")

    def test_seo_fallido_se_reanuda_sin_render(self):
        job, job_path = self._write_job(EN_SEGMENTS)
        state = self._seed_state()
        render_started = state["stages"]["render"]["started_at"]
        before = self.mp4.read_bytes()

        # 1) Un SEO con una cifra no respaldada falla en `validate_seo`.
        job["seo"]["description"] = job["seo"]["description"].replace(
            "100 real cops", "500 real cops"
        )
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        patched = dict(pipeline.STAGE_FUNCS)
        patched.update(self._forbidden_render())
        with mock.patch.dict(pipeline.STAGE_FUNCS, patched, clear=True):
            with self.assertRaises(DeliveryError):
                pipeline.run_job(
                    job_path,
                    stages="render,validate_mp4,seo,validate_seo",
                    work_dir=self.work,
                    output_dir=self.output,
                )
        self.assertFalse(
            (self.output / "clip_0006_shorts_mvp_20261029.seo_en_v2.md").exists()
        )
        self.assertEqual(self.mp4.read_bytes(), before)
        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stages"]["seo"]["status"], "succeeded")
        self.assertEqual(state["stages"]["validate_seo"]["status"], "failed")

        # 2) Se corrige el SEO y se reanuda: solo se regeneran SEO y validacion.
        job, _ = self._write_job(EN_SEGMENTS)
        with mock.patch.dict(pipeline.STAGE_FUNCS, patched, clear=True):
            pipeline.run_job(
                job_path,
                stages="seo",
                force=True,
                work_dir=self.work,
                output_dir=self.output,
            )
            result = pipeline.run_job(
                job_path,
                stages="render,validate_mp4,validate_seo",
                work_dir=self.work,
                output_dir=self.output,
            )

        self.assertEqual(
            [item["status"] for item in result["stages"]],
            ["skipped_succeeded", "skipped_succeeded", "succeeded"],
        )
        self.assertEqual(self.mp4.read_bytes(), before)
        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stages"]["render"]["started_at"], render_started)
        self.assertEqual(state["stages"]["validate_seo"]["status"], "succeeded")
        published = Path(state["seo"]["output_path"])
        self.assertTrue(published.exists())
        self.assertEqual(
            pipeline.sha256_file(published), state["seo"]["sha256"]
        )


class AdGateAndBaselinesTest(unittest.TestCase):
    """Los anuncios conocidos siguen bloqueados y lo previo no cambia."""

    COD_RANGE = (526.791111, 584.949211)
    BOOK_RANGE = (1178.691338, 1228.382041)
    SHORT7_RANGE = (399.666, 456.760)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lang_ad_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "source.mp4"
        self.source.write_bytes(b"fuente-de-prueba")
        self.output = self.tmp / "output"
        self.output.mkdir()

    def _candidate_job(self, ad_classification):
        job = json.loads(json.dumps(EN_JOB))
        job["output_stem"] = "clip_0007_shorts_mvp_20261029"
        job["source"] = {
            "path": str(self.source),
            "bytes": self.source.stat().st_size,
            "sha256": pipeline.sha256_file(self.source),
        }
        job["candidate"]["start_seconds"] = self.SHORT7_RANGE[0]
        job["candidate"]["end_seconds"] = self.SHORT7_RANGE[1]
        job["candidate"]["ad_classification"] = ad_classification
        return job

    def test_cod_y_libro_no_pasan_el_filtro(self):
        for classification in ("promotional", "sponsored_or_integrated"):
            job = self._candidate_job(classification)
            errors = validate_candidate(
                job, source_duration=1300.0, output_dir=self.output
            )
            self.assertTrue(
                any("ad_classification" in error for error in errors), errors
            )

        self.assertNotIn(
            "promotional", pipeline.RENDER_SAFE_AD_CLASSIFICATIONS
        )
        cod_overlap = baselines.overlap_seconds(*self.COD_RANGE)
        book_overlap = baselines.overlap_seconds(*self.BOOK_RANGE)
        self.assertGreater(cod_overlap["clip_0001"], 0.0)
        self.assertGreater(book_overlap["clip_0005"], 0.0)

    def test_mencion_incidental_es_aceptable(self):
        job = self._candidate_job("incidental_mention")
        errors = validate_candidate(
            job, source_duration=1300.0, output_dir=self.output
        )
        self.assertEqual(errors, [])

        incidental = (
            "we escaped from one hundred real cops and then we played a call "
            "of duty lobby with my friends"
        )
        self.assertEqual(detect_promotional_evidence(incidental), [])

    def test_fixtures_reales_detectan_cta(self):
        cod_cta = (
            "hop into cod mobile right after this video. scan this qr code or "
            "click the link in the description to download call of duty mobile today"
        )
        book_cta = (
            "buy the book and enter for a chance to win one million dollars. "
            "go wherever you buy books and check it out"
        )
        cod = " ".join(detect_promotional_evidence(cod_cta)).lower()
        book = " ".join(detect_promotional_evidence(book_cta)).lower()
        self.assertIn("download call of duty mobile today", cod)
        self.assertIn("scan this qr code", cod)
        self.assertIn("buy the book", book)

    def test_short7_no_solapa_y_queda_antes_del_anuncio(self):
        overlaps = baselines.overlap_seconds(*self.SHORT7_RANGE)
        self.assertTrue(all(value == 0.0 for value in overlaps.values()), overlaps)
        # El patrocinio mas cercano (seg_0202) empieza en 484.68 s.
        self.assertLess(self.SHORT7_RANGE[1], 484.68)
        # Los rangos publicitarios quedan fuera del fragmento.
        self.assertFalse(self.COD_RANGE[0] < self.SHORT7_RANGE[1])
        self.assertFalse(self.BOOK_RANGE[0] < self.SHORT7_RANGE[1])

    @unittest.skipUnless((ROOT / "output").is_dir(), "no existe output/")
    def test_entregables_previos_conservan_hash(self):
        result = baselines.check_protected(ROOT / "output")
        failed = [name for name, info in result["files"].items() if not info["ok"]]
        self.assertTrue(result["all_ok"], failed)
        self.assertEqual(len(result["files"]), 10)

        expected_extra = {
            "clip_0006_shorts_mvp_20261029.mp4":
                "84BC8E1C9D728DEAF1A23A626C101FBB261E6B92DBD665CA7402C3BD4D44BDBE",
            "clip_0006_shorts_mvp_20261029.seo.md":
                "FCEE5CA786643EE2137A54840AC20AB6D4D63C95480E3CBED8572C3858D1B7EA",
            "clip_0006_shorts_mvp_20261029.manifest.json":
                "FEA95924EF6102E6951B58E63EA926639AB41871518B52F12890AE941FB6A294",
            "clip_0007_shorts_mvp_20261029.mp4":
                "1A6D5C6CDBFA8BBA000F49652483022FB08782D9D01D49A24FFA6A8448851A96",
        }
        for name, expected in expected_extra.items():
            path = ROOT / "output" / name
            if path.exists():
                self.assertEqual(baselines.sha256_file(path), expected, name)


if __name__ == "__main__":
    unittest.main()
