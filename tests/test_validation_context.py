"""Pruebas de la validacion contextual centralizada (Fase 1, objetivo 5).

Ejecutar con:
    python -m unittest discover -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import (  # noqa: E402
    ClipProposal,
    Project,
    ProjectContext,
    TranscriptSegment,
    ValidationError,
    validate_clip_for_project,
    validate_segment_for_project,
    validate_time_range,
)

DURACION = 100.0


def make_project(**overrides):
    data = {
        "project_id": "proj-1",
        "source_asset_id": "sha256-" + "a" * 64,
        "status": "ingested",
        "created_at": "2026-10-15T12:00:00+00:00",
        "source_path": "input/video con espacios y acentos ó.mp4",
        "duration_seconds": DURACION,
        "metadata": {},
        "tool_versions": {},
    }
    data.update(overrides)
    return Project.from_dict(data)


def make_segment(**overrides):
    data = {
        "segment_id": "seg-0001",
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "text": "Hola mundo",
        "words": [],
    }
    data.update(overrides)
    return TranscriptSegment.from_dict(data)


def make_clip(**overrides):
    data = {
        "clip_id": "clip-0001",
        "source_asset_id": "sha256-" + "a" * 64,
        "start_seconds": 10.0,
        "end_seconds": 30.0,
        "title": "Titulo",
        "transcript_refs": ["seg-0001"],
    }
    data.update(overrides)
    return ClipProposal.from_dict(data)


class ProjectContextTests(unittest.TestCase):
    def test_construido_desde_un_proyecto(self):
        segmento = make_segment()
        context = ProjectContext.from_project(make_project(), [segmento])
        self.assertEqual(context.duration_seconds, DURACION)
        self.assertEqual(context.source_asset_id, "sha256-" + "a" * 64)
        self.assertEqual(context.segment_ids, frozenset({"seg-0001"}))

    def test_no_duplica_campos_de_contratos(self):
        campos = set(ProjectContext.__dataclass_fields__)
        self.assertEqual(campos, {"source_asset_id", "duration_seconds", "segment_ids"})
        self.assertFalse(hasattr(ProjectContext, "to_dict"))

    def test_segmento_valido(self):
        context = ProjectContext.from_project(make_project())
        self.assertEqual(context.segment_errors(make_segment()), [])
        context.validate_segment(make_segment())

    def test_segmento_que_excede_la_duracion(self):
        context = ProjectContext.from_project(make_project())
        segmento = make_segment(start_seconds=95.0, end_seconds=120.0)
        errors = context.segment_errors(segmento)
        self.assertEqual(len(errors), 1)
        self.assertIn("excede la duracion", errors[0])

    def test_segmento_invertido_ni_siquiera_construye(self):
        with self.assertRaises(ValidationError):
            make_segment(start_seconds=50.0, end_seconds=50.0)

    def test_segmento_con_inicio_negativo_ni_siquiera_construye(self):
        with self.assertRaises(ValidationError):
            make_segment(start_seconds=-1.0, end_seconds=4.0)

    def test_validate_segment_lanza(self):
        context = ProjectContext.from_project(make_project())
        with self.assertRaises(ValidationError):
            context.validate_segment(make_segment(end_seconds=999.0))


class ClipValidationTests(unittest.TestCase):
    def test_clip_valido(self):
        context = ProjectContext.from_project(
            make_project(), [make_segment(segment_id="seg-0001")]
        )
        self.assertEqual(context.clip_errors(make_clip()), [])
        context.validate_clip(make_clip())

    def test_clip_fuera_de_la_duracion(self):
        context = ProjectContext.from_project(make_project(), [make_segment()])
        errors = context.clip_errors(make_clip(end_seconds=250.0))
        self.assertEqual(len(errors), 1)
        self.assertIn("excede la duracion", errors[0])

    def test_clip_de_otro_activo_fuente(self):
        context = ProjectContext.from_project(make_project(), [make_segment()])
        errors = context.clip_errors(make_clip(source_asset_id="sha256-" + "b" * 64))
        self.assertEqual(len(errors), 1)
        self.assertIn("pertenece al activo", errors[0])

    def test_clip_con_referencia_inexistente(self):
        context = ProjectContext.from_project(
            make_project(), [make_segment(segment_id="seg-0001")]
        )
        errors = context.clip_errors(make_clip(transcript_refs=["seg-0001", "seg-9999"]))
        self.assertEqual(len(errors), 1)
        self.assertIn("seg-9999", errors[0])

    def test_clip_sin_referencias_es_valido_si_los_limites_lo_son(self):
        context = ProjectContext.from_project(make_project(), [])
        self.assertEqual(context.clip_errors(make_clip(transcript_refs=[])), [])

    def test_todas_las_incidencias_acumuladas_en_una_sola_excepcion(self):
        context = ProjectContext.from_project(make_project(), [make_segment()])
        clip = make_clip(
            end_seconds=999.0,
            source_asset_id="sha256-" + "b" * 64,
            transcript_refs=["seg-9999"],
        )
        errors = context.clip_errors(clip)
        self.assertEqual(len(errors), 3)
        with self.assertRaises(ValidationError) as ctx:
            context.validate_clip(clip)
        message = str(ctx.exception)
        self.assertIn("excede la duracion", message)
        self.assertIn("pertenece al activo", message)
        self.assertIn("seg-9999", message)

    def test_clip_negativo_ni_siquiera_construye(self):
        with self.assertRaises(ValidationError):
            make_clip(start_seconds=-5.0, end_seconds=10.0)


class ConvenienceTests(unittest.TestCase):
    def test_validate_clip_for_project(self):
        project = make_project()
        segmento = make_segment()
        validate_clip_for_project(make_clip(), project, [segmento])
        with self.assertRaises(ValidationError):
            validate_clip_for_project(make_clip(end_seconds=1000.0), project, [segmento])

    def test_validate_segment_for_project(self):
        project = make_project()
        validate_segment_for_project(make_segment(), project)
        with self.assertRaises(ValidationError):
            validate_segment_for_project(make_segment(end_seconds=1000.0), project)

    def test_no_reimplementa_validate_time_range(self):
        with self.assertRaises(ValidationError):
            validate_time_range(10.0, 10.0)


if __name__ == "__main__":
    unittest.main()
