"""Pruebas de la entrega verificable de un Short (render -> SEO -> manifiesto).

Cubren los casos exigidos que no dependen de renderizar video:

1. SEO fiel a la fuente y al MP4 -> valido.
2. SEO con cifra inventada / promesa / cita no literal / seccion faltante -> invalido.
3. Un fallo de SEO no vuelve a ejecutar `render` ni `validate_mp4`.
4. Un SEO invalido no se publica en `output/`.
5. Los 5 Shorts protegidos y sus manifiestos siguen intactos (hashes fijados).
6. Un candidato publicitario, un rango solapado o un nombre ya usado se rechazan.
7. `container_checks` acepta el contenedor correcto y detecta desviaciones.
8. El manifiesto queda `pending_user_review` con hashes y evidencia.
9. La evidencia de los anuncios conocidos exige citas literales reales.

No se llama a la red, ni a LM Studio, ni se renderiza video.

Ejecutar con:
    python -m unittest tests.test_delivery_pipeline -v
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
from services.delivery.manifest import SCHEMA, build_manifest  # noqa: E402
from services.delivery.pipeline import (  # noqa: E402
    DeliveryError,
    container_checks,
    stage_validate_seo,
    validate_candidate,
)
from services.delivery.seo import build_seo, validate_seo  # noqa: E402

# Segmentos reales del fragmento (transcripcion del proyecto).
SEGMENTS = [
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

SOURCES = [
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
        "claim": "Hasta tres hashtags de la descripcion pueden mostrarse.",
    },
    {
        "classification": "hypothesis",
        "url": "https://support.google.com/youtube/answer/6390658",
        "checked_on": "2026-10-29",
        "claim": "Hipotesis propia, no verificada, sobre clasificacion tematica.",
    },
]

DELIVERABLE = {
    "path": "output/clip_9999_shorts_test.mp4",
    "file_name": "clip_9999_shorts_test.mp4",
    "bytes": 19031096,
    "sha256": "84BC8E1C9D728DEAF1A23A626C101FBB261E6B92DBD665CA7402C3BD4D44BDBE",
    "duration_seconds": 16.950267,
}


def make_job(**candidate_overrides):
    candidate = {
        "clip_id": "clip_9999",
        "title": "Escaparon de 100 policías reales",
        "start_seconds": 26.06,
        "end_seconds": 43.0,
        "requested_start_seconds": 26.0,
        "requested_end_seconds": 43.0,
        "evidence_refs": [s["segment_id"] for s in SEGMENTS],
        "evidence_text": " ".join(s["text"] for s in SEGMENTS),
        "ad_classification": "none",
        "ad_evidence": "",
        "editorial_decision": "accept",
        "boundary_evaluation": {"start_ok": True, "end_ok": True},
        "confidence": 0.86,
    }
    candidate.update(candidate_overrides)
    return {
        "job_id": "test_job",
        "project_id": "test_project",
        "source_asset_id": "test_asset",
        "output_stem": "clip_9999_shorts_test",
        "source": {
            "path": str(ROOT / "input" / "no_usado_en_estas_pruebas.mp4"),
            "bytes": 10,
            "sha256": "0" * 64,
        },
        "transcription_path": str(ROOT / "projects" / "test" / "transcription.json"),
        "candidate": candidate,
        "seo": {
            "title": "Escaparon de 100 policías reales",
            "alt_titles": [
                "100 policías reales los persiguen: ¿podrán escapar?",
                "Cámaras, trampas y cuartos secretos para no ser atrapados",
            ],
            "description": (
                "El equipo acaba de escapar de 100 policías reales. Después tiene "
                "hasta el final del día para que no lo arresten, y su plan depende "
                "de cámaras, trampas y habitaciones secretas.\n\n"
                "Fragmento editorial del video original. No es una guía ni una "
                "promesa de resultados."
            ),
            "hashtags": ["#Shorts", "#Reto", "#Persecución"],
            "tags": ["reto", "escape", "shorts"],
            "pinned_comment": "¿Qué harías tú para no ser atrapado?",
            "thumbnail_text": "100 POLICÍAS",
            "language": "es",
            "audience": "not_made_for_kids",
            "facts": [
                {"claim": "hundreds of cameras", "segment_id": "seg_0021"},
                {"claim": "booby traps, and secret rooms", "segment_id": "seg_0022"},
            ],
            "sources": SOURCES,
            "notes": "Generado a partir de la transcripcion real.",
        },
    }


def validate(markdown, job=None, deliverable=None):
    return validate_seo(
        markdown,
        segments=SEGMENTS,
        deliverable=deliverable or DELIVERABLE,
        verified_sources=[s["url"] for s in (job or make_job())["seo"]["sources"]],
    )


class SeoFidelityTest(unittest.TestCase):
    """El SEO generado debe ser fiel; cualquier añadido no respaldado lo invalida."""

    def test_seo_fiel_es_valido(self):
        markdown = build_seo(make_job(), DELIVERABLE)
        result = validate(markdown)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["extracted"]["hashtags"], ["#Shorts", "#Reto", "#Persecución"])
        self.assertEqual(result["extracted"]["facts"], 2)
        self.assertTrue(result["checks"]["facts_literal"])
        self.assertTrue(result["checks"]["numbers_backed"])
        self.assertTrue(result["checks"]["pending_review_declared"])
        self.assertTrue(result["checks"]["mp4_sha_matches"])
        # Las tres clasificaciones de fuente quedan separadas.
        self.assertEqual(
            {s["classification"] for s in SOURCES},
            {"official_requirement", "editorial_recommendation", "hypothesis"},
        )

    def test_cifra_inventada_invalida(self):
        job = make_job()
        job["seo"]["description"] = job["seo"]["description"].replace(
            "100 policías", "500 policías"
        )
        result = validate(build_seo(job, DELIVERABLE), job=job)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["numbers_backed"])
        self.assertTrue(any("Cifras sin respaldo" in e for e in result["errors"]))

    def test_promesa_de_resultado_invalida(self):
        job = make_job()
        job["seo"]["description"] += "\n\nÉxito asegurado."
        result = validate(build_seo(job, DELIVERABLE), job=job)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["no_result_promises"])

    def test_cita_no_literal_invalida(self):
        job = make_job()
        job["seo"]["description"] += '\n\nComo dicen ellos: "somos imparables".'
        result = validate(build_seo(job, DELIVERABLE), job=job)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["quotes_literal"])
        self.assertTrue(any("no respaldada" in e for e in result["errors"]))

    def test_seccion_faltante_invalida(self):
        markdown = build_seo(make_job(), DELIVERABLE).replace("## 4. Hashtags", "## 4. Etiquetas2")
        result = validate(markdown)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["section:4. Hashtags"])

    def test_cuarto_hashtag_invalido(self):
        job = make_job()
        job["seo"]["hashtags"].append("#Extra")
        result = validate(build_seo(job, DELIVERABLE), job=job)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["hashtags_max_3"])

    def test_sha_del_mp4_manipulado_invalido(self):
        markdown = build_seo(make_job(), DELIVERABLE)
        tampered = markdown.replace(DELIVERABLE["sha256"], "0" * 64)
        result = validate(tampered)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["mp4_sha_matches"])

    def test_fuente_no_verificada_invalida(self):
        markdown = build_seo(make_job(), DELIVERABLE)
        result = validate_seo(
            markdown,
            segments=SEGMENTS,
            deliverable=DELIVERABLE,
            verified_sources=["https://support.google.com/youtube/answer/10059070"],
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["sources_well_formed"])

    def test_facto_inexistente_invalido(self):
        job = make_job()
        job["seo"]["facts"].append({"claim": "ganamos un millón", "segment_id": "seg_0016"})
        result = validate(build_seo(job, DELIVERABLE), job=job)
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["facts_literal"])


class ResumeWithoutRenderTest(unittest.TestCase):
    """Reanudar solo SEO no debe volver a renderizar ni tocar el MP4."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="delivery_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.output = self.tmp / "output"
        self.output.mkdir()
        self.job = make_job()
        self.job["transcription_path"] = str(self.tmp / "transcription.json")
        (self.tmp / "transcription.json").write_text(
            json.dumps({"segments": SEGMENTS}, ensure_ascii=False), encoding="utf-8"
        )
        self.mp4 = self.output / "clip_9999_shorts_test.mp4"
        self.mp4.write_bytes(b"mp4-de-prueba-sin-render")

        state = {
            "job_id": "test_job",
            "created_at": "2026-10-29T00:00:00+00:00",
            "stages": {
                "render": {"status": "succeeded"},
                "validate_mp4": {"status": "succeeded"},
            },
            "deliverable": {
                "path": str(self.mp4),
                "file_name": self.mp4.name,
                "bytes": self.mp4.stat().st_size,
                "sha256": pipeline.sha256_file(self.mp4),
                "duration_seconds": 16.95,
            },
        }
        (self.work / "state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

    def test_fallo_seo_no_reejecuta_render(self):
        called = []

        def boom(ctx):
            called.append("seo")
            raise DeliveryError("fallo SEO simulado")

        def forbidden(name):
            def inner(ctx):
                raise AssertionError(f"no debia ejecutarse {name}")
            return inner

        patched = dict(pipeline.STAGE_FUNCS)
        patched.update({"seo": boom, "render": forbidden("render"),
                        "validate_mp4": forbidden("validate_mp4")})
        before = self.mp4.read_bytes()
        with mock.patch.dict(pipeline.STAGE_FUNCS, patched, clear=True):
            with self.assertRaises(DeliveryError):
                pipeline.run_job(
                    self._job_path(),
                    stages="render,validate_mp4,seo",
                    work_dir=self.work,
                    output_dir=self.output,
                )
        self.assertEqual(called, ["seo"])
        self.assertEqual(self.mp4.read_bytes(), before)

        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stages"]["seo"]["status"], "failed")
        self.assertEqual(state["stages"]["render"]["status"], "succeeded")
        self.assertEqual(state["stages"]["validate_mp4"]["status"], "succeeded")

    def test_etapas_completadas_se_omiten(self):
        def forbidden(ctx):
            raise AssertionError("una etapa completada no debe reejecutarse")

        with mock.patch.dict(
            pipeline.STAGE_FUNCS,
            {"render": forbidden, "validate_mp4": forbidden},
            clear=True,
        ):
            result = pipeline.run_job(
                self._job_path(),
                stages="render,validate_mp4",
                work_dir=self.work,
                output_dir=self.output,
            )
        self.assertEqual(
            [s["status"] for s in result["stages"]],
            ["skipped_succeeded", "skipped_succeeded"],
        )

    def _job_path(self):
        path = self.tmp / "job.json"
        path.write_text(json.dumps(self.job, ensure_ascii=False), encoding="utf-8")
        return path

    def test_seo_invalido_no_se_publica(self):
        work_path = self.work / "seo.md"
        tampered = build_seo(self.job, json.loads(
            (self.work / "state.json").read_text(encoding="utf-8")
        )["deliverable"]).replace("100 policías", "500 policías")
        work_path.write_text(tampered, encoding="utf-8")

        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        state["seo"] = {"work_path": str(work_path), "sha256": "X", "bytes": 1}
        ctx = {
            "job": self.job,
            "work": self.work,
            "output_dir": self.output,
            "state": state,
        }
        with self.assertRaises(DeliveryError):
            stage_validate_seo(ctx)
        self.assertFalse((self.output / "clip_9999_shorts_test.seo.md").exists())
        self.assertNotEqual(
            (self.work / "state.json").read_text(encoding="utf-8"), ""
        )
        self.assertEqual(state["stages"]["render"]["status"], "succeeded")


class CandidateGateTest(unittest.TestCase):
    """El candidato se filtra antes de gastar tiempo de render."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="delivery_gate_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.output = self.tmp / "output"
        self.output.mkdir()
        self.source = self.tmp / "fuente.mp4"
        self.source.write_bytes(b"fuente-de-prueba")

    def _job(self, **overrides):
        job = make_job(**overrides)
        job["source"] = {
            "path": str(self.source),
            "bytes": self.source.stat().st_size,
            "sha256": pipeline.sha256_file(self.source),
        }
        return job

    def test_candidato_limpio_pasa(self):
        errors = validate_candidate(
            self._job(), source_duration=1228.38, output_dir=self.output
        )
        self.assertEqual(errors, [])

    def test_candidato_publicitario_no_pasa(self):
        for classification in ("promotional", "sponsored_or_integrated", "uncertain"):
            errors = validate_candidate(
                self._job(ad_classification=classification),
                source_duration=1228.38,
                output_dir=self.output,
            )
            self.assertTrue(
                any("ad_classification" in e for e in errors), (classification, errors)
            )

    def test_rango_solapado_con_protegido_no_pasa(self):
        errors = validate_candidate(
            self._job(start_seconds=1100.0, end_seconds=1110.0),
            source_duration=1228.38,
            output_dir=self.output,
        )
        self.assertTrue(any("se solapa" in e for e in errors), errors)

    def test_rango_fuera_de_la_fuente_no_pasa(self):
        errors = validate_candidate(
            self._job(start_seconds=1220.0, end_seconds=1240.0),
            source_duration=1228.38,
            output_dir=self.output,
        )
        self.assertTrue(any("Rango invalido" in e for e in errors), errors)

    def test_nombre_ya_usado_no_pasa(self):
        (self.output / "clip_9999_shorts_test.mp4").write_bytes(b"existente")
        errors = validate_candidate(
            self._job(), source_duration=1228.38, output_dir=self.output
        )
        self.assertTrue(any("Ya existe" in e for e in errors), errors)

    def test_hash_de_fuente_incorrecto_no_pasa(self):
        job = self._job()
        job["source"]["sha256"] = "0" * 64
        errors = validate_candidate(
            job, source_duration=1228.38, output_dir=self.output
        )
        self.assertTrue(any("SHA-256" in e for e in errors), errors)

    def test_limites_no_validados_no_pasan(self):
        errors = validate_candidate(
            self._job(boundary_evaluation={"start_ok": False, "end_ok": True}),
            source_duration=1228.38,
            output_dir=self.output,
        )
        self.assertTrue(any("limites" in e for e in errors), errors)


class ContainerChecksTest(unittest.TestCase):
    """Contrato tecnico del MP4 (FFprobe simulado, sin render)."""

    def _probe(self, **video):
        v = {
            "codec_type": "video",
            "codec_name": "h264",
            "profile": "High",
            "pix_fmt": "yuv420p",
            "width": 1080,
            "height": 1920,
            "r_frame_rate": "30000/1001",
            "nb_frames": "508",
            "duration": "16.950267",
        }
        v.update(video)
        return {
            "streams": [
                v,
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "profile": "LC",
                    "sample_rate": "44100",
                    "channels": 2,
                    "duration": "16.950000",
                },
            ],
            "format": {"duration": "16.950267", "format_name": "mov,mp4"},
        }

    def _checks(self, probe=None, **overrides):
        kwargs = {
            "expected_duration": 16.950267,
            "actual_start": 26.052789,
            "requested_start": 26.06,
            "requested_end": 43.0,
            "alignment_ncc": 0.99870347,
            "overlaps": {k: 0.0 for k in baselines.PROTECTED_RANGES},
        }
        kwargs.update(overrides)
        return container_checks(probe or self._probe(), **kwargs)

    def test_contenedor_correcto_pasa(self):
        checks = self._checks()
        self.assertTrue(all(checks.values()), [k for k, v in checks.items() if not v])

    def test_vertical_incorrecto_falla(self):
        checks = self._checks(self._probe(width=1920, height=1080))
        self.assertFalse(checks["width"])
        self.assertFalse(checks["height"])

    def test_codec_o_pix_fmt_incorrecto_falla(self):
        checks = self._checks(self._probe(codec_name="av1", pix_fmt="yuv420p10le"))
        self.assertFalse(checks["video_codec"])
        self.assertFalse(checks["pix_fmt"])

    def test_inicio_desviado_falla(self):
        checks = self._checks(actual_start=24.458)
        self.assertFalse(checks["start_matches_cut"])

    def test_solape_protegido_falla(self):
        overlaps = {k: 0.0 for k in baselines.PROTECTED_RANGES}
        overlaps["clip_0005"] = 2.5
        checks = self._checks(overlaps=overlaps)
        self.assertFalse(checks["no_protected_overlap"])

    def test_sincronia_baja_falla(self):
        checks = self._checks(alignment_ncc=0.42)
        self.assertFalse(checks["alignment_confident"])


class ProtectedBaselinesTest(unittest.TestCase):
    """Los 5 Shorts previos y sus manifiestos no cambian."""

    @unittest.skipUnless((ROOT / "output").is_dir(), "no existe output/")
    def test_protegidos_intactos(self):
        result = baselines.check_protected(ROOT / "output")
        failed = [name for name, info in result["files"].items() if not info["ok"]]
        self.assertTrue(result["all_ok"], failed)
        self.assertEqual(len(result["files"]), 10)

    def test_rangos_protegidos_sin_solape_con_el_nuevo(self):
        overlaps = baselines.overlap_seconds(26.052789, 43.003056)
        self.assertTrue(all(v == 0.0 for v in overlaps.values()), overlaps)

    def test_nombre_nuevo_no_colisiona(self):
        self.assertNotIn(
            "clip_0006_shorts_mvp_20261029.mp4", baselines.PROTECTED_SHA256
        )


class ManifestTest(unittest.TestCase):
    """El manifiesto declara revision humana pendiente y los hashes reales."""

    def _state(self):
        sha = DELIVERABLE["sha256"]
        return {
            "render": {
                "artifacts": {
                    "source_cut": {"output_path": "x", "sha256": sha},
                    "vertical": {"output_path": "y", "sha256": sha},
                    "subtitles": {"path": "subs.ass", "cues": 10, "segments": 8},
                    "emphasis": {"path": "motion.ass", "count": 3},
                    "neon": {"path": "neon.ass", "bytes": 100},
                    "notifications": [],
                    "cards_anim": [],
                    "click": {"sha256": sha, "gain_db": -2.0, "delay_s": 0.012},
                    "audio_mix": {"mode": "mix_clicks"},
                }
            },
            "validate_mp4": {
                "probe": {
                    "video": {
                        "codec_name": "h264", "profile": "High", "pix_fmt": "yuv420p",
                        "width": 1080, "height": 1920,
                        "r_frame_rate": "30000/1001", "nb_frames": "508",
                        "duration": "16.950267",
                    },
                    "audio": {
                        "codec_name": "aac", "profile": "LC", "sample_rate": "44100",
                        "channels": 2, "bit_rate": "128000", "duration": "16.950000",
                    },
                    "format": {"format_name": "mov,mp4"},
                },
                "checks": {"video_present": True},
                "all_ok": True,
                "decode_integrity": {"ok": True, "error_lines": []},
            },
            "seo": {"work_path": "w", "output_path": "o", "sha256": "S", "bytes": 10},
            "validate_seo": {"result": {"valid": True, "errors": [], "checks": {}}},
            "deliverable": dict(DELIVERABLE),
            "timing_alignment": {"clip_zero_maps_to_source_seconds": 26.052789},
            "overlap_seconds": {k: 0.0 for k in baselines.PROTECTED_RANGES},
        }

    def test_manifiesto_pendiente_de_revision(self):
        manifest = build_manifest(
            make_job(),
            self._state(),
            root=ROOT,
            protected={"all_ok": True, "files": {}},
            reproduction=["python -m services.delivery --job job.json"],
        )
        self.assertEqual(manifest["schema"], SCHEMA)
        self.assertEqual(manifest["review_status"], "pending_user_review")
        self.assertEqual(manifest["status"], "delivered_pending_human_review")
        self.assertFalse(manifest["human_review"]["approved"])
        self.assertEqual(manifest["deliverable"]["sha256"], DELIVERABLE["sha256"])
        self.assertEqual(
            manifest["deliverable"]["video"]["height"], 1920
        )
        self.assertTrue(manifest["protected_baselines"]["all_ok"])
        self.assertEqual(
            manifest["provenance"]["editorial_selection"]["ad_classification"], "none"
        )
        self.assertTrue(
            manifest["provenance"]["editorial_selection"]["independence"]["zero_overlap"]
        )


    def test_manifiesto_con_probe_crudo_rellena_codec(self):
        """El estado real guarda el JSON crudo de FFprobe (`streams`)."""
        state = self._state()
        state["validate_mp4"]["probe"] = {
            "streams": [
                {
                    "codec_type": "video", "codec_name": "h264", "profile": "High",
                    "pix_fmt": "yuv420p", "width": 1080, "height": 1920,
                    "r_frame_rate": "30000/1001", "nb_frames": "508",
                    "duration": "16.950267",
                },
                {
                    "codec_type": "audio", "codec_name": "aac", "profile": "LC",
                    "sample_rate": "44100", "channels": 2, "bit_rate": "128000",
                    "duration": "16.950000",
                },
            ],
            "format": {"duration": "16.950267", "format_name": "mov,mp4"},
        }
        manifest = build_manifest(
            make_job(),
            state,
            root=ROOT,
            protected={"all_ok": True, "files": {}},
            reproduction=[],
        )
        video = manifest["deliverable"]["video"]
        audio = manifest["deliverable"]["audio"]
        self.assertEqual(video["codec"], "h264")
        self.assertEqual(video["height"], 1920)
        self.assertEqual(audio["sample_rate"], "44100")
        self.assertEqual(manifest["deliverable"]["video_duration_seconds"], "16.950267")
        self.assertEqual(manifest["deliverable"]["container"], "mov,mp4")


class AdEvidenceTest(unittest.TestCase):
    """La evidencia de los anuncios conocidos debe ser literal."""

    def _job(self, quote):
        job = make_job()
        job["negative_fixtures"] = [
            {
                "clip_id": "clip_0001",
                "brand_or_topic": "Call of Duty: Mobile",
                "ad_classification": "promotional",
                "protected_file": "output/clip_0001_shorts_mvp_20261028.mp4",
                "evidence": [
                    {"segment_id": "seg_0016", "quote": quote},
                    {"segment_id": "seg_0023", "quote": "keep us from getting caught"},
                ],
            }
        ]
        return job

    def test_cita_literal_se_registra(self):
        evidence = pipeline._ad_evidence(
            self._job("escaped from 100 real cops"), {"segments": SEGMENTS}
        )
        entry = evidence["entries"][0]
        self.assertEqual(entry["editorial_decision"], "reject")
        self.assertEqual(len(entry["evidence"]), 2)
        self.assertEqual(entry["evidence"][0]["segment_id"], "seg_0016")

    def test_cita_inventada_falla(self):
        with self.assertRaises(DeliveryError):
            pipeline._ad_evidence(
                self._job("compra el juego hoy mismo"), {"segments": SEGMENTS}
            )

    def test_una_sola_cita_no_basta(self):
        job = self._job("escaped from 100 real cops")
        job["negative_fixtures"][0]["evidence"].pop()
        with self.assertRaises(DeliveryError):
            pipeline._ad_evidence(job, {"segments": SEGMENTS})

    def test_segmento_inexistente_falla(self):
        job = self._job("escaped from 100 real cops")
        job["negative_fixtures"][0]["evidence"][0]["segment_id"] = "seg_9999"
        with self.assertRaises(DeliveryError):
            pipeline._ad_evidence(job, {"segments": SEGMENTS})

    @unittest.skipUnless(
        (ROOT / "work/mvp_short_clean_20261029_01/evidence/ad_classification_evidence.json").exists(),
        "no existe la evidencia publicada del job real",
    )
    def test_evidencia_real_documenta_dos_anuncios(self):
        path = ROOT / "work/mvp_short_clean_20261029_01/evidence/ad_classification_evidence.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        clips = {e["clip_id"]: e for e in evidence["entries"]}
        self.assertEqual(set(clips), {"clip_0001", "clip_0005"})
        self.assertEqual(clips["clip_0001"]["ad_classification"], "promotional")
        self.assertEqual(clips["clip_0005"]["ad_classification"], "promotional")
        self.assertIn("call of duty", json.dumps(clips["clip_0001"]).lower())
        self.assertIn("book", json.dumps(clips["clip_0005"]).lower())
        for entry in clips.values():
            self.assertGreaterEqual(len(entry["evidence"]), 2)
            for item in entry["evidence"]:
                self.assertIn(
                    " ".join(item["quote"].lower().split()),
                    " ".join(item["text"].lower().split()),
                )


if __name__ == "__main__":
    unittest.main()
