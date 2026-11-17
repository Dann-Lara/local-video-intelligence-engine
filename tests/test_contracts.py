"""Pruebas de los contratos iniciales (Fase 0).

Ejecutar con:
    python -m unittest discover -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import (  # noqa: E402
    ClipProposal,
    Project,
    ReviewStatus,
    Stage,
    Task,
    TaskStatus,
    TranscriptSegment,
    ValidationError,
    Word,
    validate_clip_against_source_duration,
    validate_clip_source_asset,
    validate_clip_transcript_refs,
    validate_segment_against_source_duration,
    validate_time_range,
)


def make_project(**overrides):
    data = {
        "project_id": "proj-1",
        "source_asset_id": "asset-1",
        "status": "ingested",
        "created_at": "2026-10-15T12:00:00",
        "source_path": "input/video con espacios y acentos ó.mp4",
        "duration_seconds": 100.0,
        "metadata": {"width": 1920, "height": 1080},
        "tool_versions": {},
    }
    data.update(overrides)
    return data


def make_segment(**overrides):
    data = {
        "segment_id": "seg-0001",
        "start_seconds": 10.0,
        "end_seconds": 15.5,
        "text": "Hola mundo",
        "words": [],
    }
    data.update(overrides)
    return data


def make_clip(**overrides):
    data = {
        "clip_id": "clip-0001",
        "source_asset_id": "asset-1",
        "start_seconds": 12.34,
        "end_seconds": 48.2,
        "title": "Título editorial",
        "hook": "Gancho",
        "main_idea": "Idea",
        "ending": "Cierre",
        "editorial_reason": "Unidad completa",
        "transcript_refs": ["seg-0001"],
        "confidence": 0.82,
        "warnings": [],
        "review_status": "pending",
    }
    data.update(overrides)
    return data


def make_task(**overrides):
    data = {
        "task_id": "task-1",
        "project_id": "proj-1",
        "stage": "transcription",
        "status": "pending",
        "attempt_count": 0,
        "started_at": None,
        "finished_at": None,
        "error_code": None,
        "error_message": None,
        "artifact_paths": [],
    }
    data.update(overrides)
    return data


class TestProject(unittest.TestCase):
    def test_valid_project(self):
        project = Project.from_dict(make_project())
        self.assertEqual(project.project_id, "proj-1")
        self.assertEqual(project.duration_seconds, 100.0)

    def test_source_path_with_spaces_and_accents(self):
        project = Project.from_dict(make_project())
        self.assertIn("acentos ó", project.source_path)

    def test_negative_duration_rejected(self):
        with self.assertRaises(ValidationError):
            Project.from_dict(make_project(duration_seconds=-1.0))

    def test_zero_duration_rejected(self):
        with self.assertRaises(ValidationError):
            Project.from_dict(make_project(duration_seconds=0))

    def test_empty_status_rejected(self):
        with self.assertRaises(ValidationError):
            Project.from_dict(make_project(status=""))

    def test_invalid_created_at_rejected(self):
        with self.assertRaises(ValidationError):
            Project.from_dict(make_project(created_at="no-es-fecha"))

    def test_serialization_roundtrip(self):
        project = Project.from_dict(make_project())
        payload = json.dumps(project.to_dict())
        restored = Project.from_dict(json.loads(payload))
        self.assertEqual(project.to_dict(), restored.to_dict())


class TestTranscriptSegment(unittest.TestCase):
    def test_valid_segment_without_words(self):
        seg = TranscriptSegment.from_dict(make_segment())
        self.assertEqual(seg.words, [])

    def test_valid_segment_with_words(self):
        seg = TranscriptSegment.from_dict(
            make_segment(
                words=[
                    {"start_seconds": 10.0, "end_seconds": 10.5, "text": "Hola"},
                    {"start_seconds": 10.5, "end_seconds": 11.0, "text": "mundo"},
                ]
            )
        )
        self.assertEqual(len(seg.words), 2)
        self.assertIsInstance(seg.words[0], Word)

    def test_end_before_or_equal_start_rejected(self):
        with self.assertRaises(ValidationError):
            TranscriptSegment.from_dict(make_segment(start_seconds=10.0, end_seconds=10.0))

    def test_negative_start_rejected(self):
        with self.assertRaises(ValidationError):
            TranscriptSegment.from_dict(make_segment(start_seconds=-1.0, end_seconds=5.0))

    def test_word_outside_segment_rejected(self):
        with self.assertRaises(ValidationError):
            TranscriptSegment.from_dict(
                make_segment(
                    words=[{"start_seconds": 5.0, "end_seconds": 11.0, "text": "fuera"}]
                )
            )

    def test_word_ends_after_segment_rejected(self):
        with self.assertRaises(ValidationError):
            TranscriptSegment.from_dict(
                make_segment(
                    start_seconds=10.0,
                    end_seconds=15.0,
                    words=[{"start_seconds": 14.0, "end_seconds": 16.0, "text": "exceso"}],
                )
            )

    def test_word_at_exact_segment_boundaries_accepted(self):
        seg = TranscriptSegment.from_dict(
            make_segment(
                start_seconds=10.0,
                end_seconds=15.0,
                words=[{"start_seconds": 10.0, "end_seconds": 15.0, "text": "ajustado"}],
            )
        )
        self.assertEqual(len(seg.words), 1)

    def test_words_none_rejected(self):
        with self.assertRaises(ValidationError):
            TranscriptSegment.from_dict(make_segment(words=None))

    def test_words_as_string_rejected(self):
        with self.assertRaises(ValidationError):
            TranscriptSegment.from_dict(make_segment(words="hola"))

    def test_words_item_of_wrong_type_rejected(self):
        with self.assertRaises(ValidationError):
            TranscriptSegment.from_dict(make_segment(words=["hola"]))

    def test_segment_against_source_duration(self):
        seg = TranscriptSegment.from_dict(make_segment(start_seconds=10.0, end_seconds=15.5))
        validate_segment_against_source_duration(seg, 20.0)
        with self.assertRaises(ValidationError):
            validate_segment_against_source_duration(seg, 12.0)

    def test_serialization_roundtrip(self):
        seg = TranscriptSegment.from_dict(
            make_segment(words=[{"start_seconds": 10.0, "end_seconds": 10.5, "text": "Hola"}])
        )
        restored = TranscriptSegment.from_dict(json.loads(json.dumps(seg.to_dict())))
        self.assertEqual(seg.to_dict(), restored.to_dict())


class TestClipProposal(unittest.TestCase):
    def test_valid_clip(self):
        clip = ClipProposal.from_dict(make_clip())
        self.assertEqual(clip.review_status, ReviewStatus.PENDING)

    def test_end_before_start_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(start_seconds=50.0, end_seconds=40.0))

    def test_negative_start_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(start_seconds=-0.1))

    def test_confidence_out_of_range_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(confidence=1.5))

    def test_invalid_review_status_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(review_status="quizas"))

    def test_clip_within_source_duration(self):
        clip = ClipProposal.from_dict(make_clip(end_seconds=48.2))
        validate_clip_against_source_duration(clip, 100.0)
        with self.assertRaises(ValidationError):
            validate_clip_against_source_duration(clip, 40.0)

    def test_overlapping_clips_are_allowed(self):
        clip_a = ClipProposal.from_dict(make_clip(clip_id="clip-a", start_seconds=10.0, end_seconds=30.0))
        clip_b = ClipProposal.from_dict(make_clip(clip_id="clip-b", start_seconds=20.0, end_seconds=40.0))
        validate_clip_against_source_duration(clip_a, 100.0)
        validate_clip_against_source_duration(clip_b, 100.0)
        self.assertLess(clip_a.start_seconds, clip_b.end_seconds)
        self.assertTrue(clip_a.end_seconds > clip_b.start_seconds)

    def test_transcript_refs_must_exist(self):
        clip = ClipProposal.from_dict(make_clip(transcript_refs=["seg-0001"]))
        segment = TranscriptSegment.from_dict(make_segment(segment_id="seg-0001"))
        validate_clip_transcript_refs(clip, [segment])
        with self.assertRaises(ValidationError):
            validate_clip_transcript_refs(clip, [])

    def test_source_asset_must_match_project(self):
        clip = ClipProposal.from_dict(make_clip(source_asset_id="asset-1"))
        project = Project.from_dict(make_project(source_asset_id="asset-1"))
        validate_clip_source_asset(clip, project)
        other = Project.from_dict(make_project(source_asset_id="asset-2"))
        with self.assertRaises(ValidationError):
            validate_clip_source_asset(clip, other)

    def test_serialization_roundtrip(self):
        clip = ClipProposal.from_dict(make_clip())
        restored = ClipProposal.from_dict(json.loads(json.dumps(clip.to_dict())))
        self.assertEqual(clip.to_dict(), restored.to_dict())


class TestTask(unittest.TestCase):
    def test_valid_task(self):
        task = Task.from_dict(make_task())
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertEqual(task.stage, Stage.TRANSCRIPTION)

    def test_all_documented_statuses_accepted(self):
        for status in ("pending", "running", "succeeded", "failed", "cancelled"):
            task = Task.from_dict(make_task(status=status))
            self.assertEqual(task.status.value, status)

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValidationError):
            Task.from_dict(make_task(status="terminado"))

    def test_invalid_stage_rejected(self):
        with self.assertRaises(ValidationError):
            Task.from_dict(make_task(stage="etapa_inventada"))

    def test_negative_attempt_count_rejected(self):
        with self.assertRaises(ValidationError):
            Task.from_dict(make_task(attempt_count=-1))

    def test_invalid_timestamp_rejected(self):
        with self.assertRaises(ValidationError):
            Task.from_dict(make_task(finished_at="ayer"))

    def test_serialization_roundtrip(self):
        task = Task.from_dict(make_task())
        restored = Task.from_dict(json.loads(json.dumps(task.to_dict())))
        self.assertEqual(task.to_dict(), restored.to_dict())


class TestNonFiniteNumbers(unittest.TestCase):
    """Regresión: NaN e infinitos deben rechazarse en todo campo numérico."""

    NON_FINITE = ("nan", "inf", "-inf")

    def _value(self, name):
        return {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}[name]

    def test_validate_time_range_rejects_non_finite(self):
        for name in self.NON_FINITE:
            with self.subTest(field="start", value=name):
                with self.assertRaises(ValidationError):
                    validate_time_range(self._value(name), 10.0, label="x")
            with self.subTest(field="end", value=name):
                with self.assertRaises(ValidationError):
                    validate_time_range(0.0, self._value(name), label="x")

    def test_clip_rejects_non_finite_times(self):
        for name in self.NON_FINITE:
            with self.subTest(field="start", value=name):
                with self.assertRaises(ValidationError):
                    ClipProposal.from_dict(
                        make_clip(start_seconds=self._value(name), end_seconds=48.2)
                    )
            with self.subTest(field="end", value=name):
                with self.assertRaises(ValidationError):
                    ClipProposal.from_dict(
                        make_clip(start_seconds=1.0, end_seconds=self._value(name))
                    )

    def test_segment_rejects_non_finite_times(self):
        for name in self.NON_FINITE:
            with self.subTest(field="start", value=name):
                with self.assertRaises(ValidationError):
                    TranscriptSegment.from_dict(
                        make_segment(start_seconds=self._value(name), end_seconds=15.5)
                    )
            with self.subTest(field="end", value=name):
                with self.assertRaises(ValidationError):
                    TranscriptSegment.from_dict(
                        make_segment(start_seconds=1.0, end_seconds=self._value(name))
                    )

    def test_word_rejects_non_finite_times(self):
        for name in self.NON_FINITE:
            with self.subTest(field="start", value=name):
                with self.assertRaises(ValidationError):
                    Word(self._value(name), 2.0, "hola")
            with self.subTest(field="end", value=name):
                with self.assertRaises(ValidationError):
                    Word(1.0, self._value(name), "hola")

    def test_project_duration_rejects_non_finite(self):
        for name in self.NON_FINITE:
            with self.subTest(value=name):
                with self.assertRaises(ValidationError):
                    Project.from_dict(
                        make_project(duration_seconds=self._value(name))
                    )

    def test_confidence_rejects_non_finite(self):
        for name in self.NON_FINITE:
            with self.subTest(value=name):
                with self.assertRaises(ValidationError):
                    ClipProposal.from_dict(
                        make_clip(confidence=self._value(name))
                    )

    def test_segment_duration_check_rejects_non_finite_upper_bound(self):
        seg = TranscriptSegment.from_dict(make_segment())
        with self.assertRaises(ValidationError):
            validate_segment_against_source_duration(seg, float("nan"))
        with self.assertRaises(ValidationError):
            validate_clip_against_source_duration(
                ClipProposal.from_dict(make_clip()), float("inf")
            )

    def test_project_duration_and_confidence_still_accept_valid_values(self):
        project = Project.from_dict(make_project(duration_seconds=1234.56))
        self.assertEqual(project.duration_seconds, 1234.56)
        clip = ClipProposal.from_dict(make_clip(confidence=0.82))
        self.assertEqual(clip.confidence, 0.82)


class TestCreatedAtAndOptionalTimestamps(unittest.TestCase):
    """created_at obligatorio; started_at/finished_at opcionales."""

    def test_created_at_none_rejected(self):
        with self.assertRaises(ValidationError):
            Project.from_dict(make_project(created_at=None))

    def test_created_at_empty_string_rejected(self):
        with self.assertRaises(ValidationError):
            Project.from_dict(make_project(created_at="   "))

    def test_created_at_valid_iso_with_z_accepted(self):
        project = Project.from_dict(make_project(created_at="2026-10-15T12:00:00Z"))
        self.assertEqual(project.created_at, "2026-10-15T12:00:00Z")

    def test_created_at_invalid_iso_rejected(self):
        with self.assertRaises(ValidationError):
            Project.from_dict(make_project(created_at="ayer"))

    def test_task_optional_timestamps_accept_none(self):
        task = Task.from_dict(make_task(started_at=None, finished_at=None))
        self.assertIsNone(task.started_at)
        self.assertIsNone(task.finished_at)

    def test_task_optional_timestamps_accept_iso_values(self):
        task = Task.from_dict(
            make_task(started_at="2026-10-15T12:00:00", finished_at="2026-10-15T12:05:00")
        )
        self.assertEqual(task.started_at, "2026-10-15T12:00:00")

    def test_task_optional_timestamp_empty_string_rejected(self):
        with self.assertRaises(ValidationError):
            Task.from_dict(make_task(started_at=""))

    def test_task_optional_timestamp_invalid_iso_rejected(self):
        with self.assertRaises(ValidationError):
            Task.from_dict(make_task(finished_at="mañana"))


class TestCollectionValidation(unittest.TestCase):
    """Las colecciones deben fallar con ValidationError, nunca con TypeError
    ni con una conversión silenciosa a lista de caracteres."""

    def test_transcript_refs_none_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(transcript_refs=None))

    def test_transcript_refs_as_string_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(transcript_refs="seg-0001"))

    def test_transcript_refs_with_non_string_item_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(transcript_refs=[1, 2]))

    def test_transcript_refs_with_empty_item_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(transcript_refs=["  "]))

    def test_warnings_none_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(warnings=None))

    def test_warnings_as_string_rejected(self):
        with self.assertRaises(ValidationError):
            ClipProposal.from_dict(make_clip(warnings="cuidado"))

    def test_artifact_paths_none_rejected(self):
        with self.assertRaises(ValidationError):
            Task.from_dict(make_task(artifact_paths=None))

    def test_artifact_paths_as_string_rejected(self):
        with self.assertRaises(ValidationError):
            Task.from_dict(make_task(artifact_paths="salida.mp4"))

    def test_error_type_is_validation_error_not_typeerror(self):
        for kwargs, ctor in (
            ({"transcript_refs": None}, lambda **kw: ClipProposal.from_dict(make_clip(**kw))),
            ({"warnings": None}, lambda **kw: ClipProposal.from_dict(make_clip(**kw))),
            ({"artifact_paths": None}, lambda **kw: Task.from_dict(make_task(**kw))),
        ):
            with self.subTest(kwargs=kwargs):
                try:
                    ctor(**kwargs)
                except Exception as exc:
                    self.assertNotIsInstance(exc, TypeError)
                    self.assertIsInstance(exc, ValidationError)
                else:
                    self.fail("se esperaba ValidationError")

    def test_valid_collections_still_accepted(self):
        clip = ClipProposal.from_dict(
            make_clip(transcript_refs=["seg-0001", "seg-0002"], warnings=["nota"])
        )
        self.assertEqual(clip.transcript_refs, ["seg-0001", "seg-0002"])
        task = Task.from_dict(make_task(artifact_paths=["work/transcript.json"]))
        self.assertEqual(task.artifact_paths, ["work/transcript.json"])


class TestSerializationStillWorks(unittest.TestCase):
    """La corrección no debe romper la serialización JSON ni los casos válidos."""

    def test_all_contracts_serialize_to_json(self):
        payload = {
            "project": Project.from_dict(make_project()).to_dict(),
            "segment": TranscriptSegment.from_dict(make_segment()).to_dict(),
            "clip": ClipProposal.from_dict(make_clip()).to_dict(),
            "task": Task.from_dict(make_task()).to_dict(),
        }
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn("Título editorial", encoded)

    def test_review_status_roundtrip_to_non_default_value(self):
        clip = ClipProposal.from_dict(make_clip(review_status="approved"))
        restored = ClipProposal.from_dict(json.loads(json.dumps(clip.to_dict())))
        self.assertEqual(restored.review_status, ReviewStatus.APPROVED)
        self.assertEqual(clip.to_dict(), restored.to_dict())

    def test_task_roundtrip_with_timestamps_and_errors(self):
        task = Task.from_dict(
            make_task(
                status="failed",
                started_at="2026-10-15T12:00:00",
                finished_at="2026-10-15T12:05:00",
                error_code="WHISPER_FAILED",
                error_message="modelo no disponible",
            )
        )
        restored = Task.from_dict(json.loads(json.dumps(task.to_dict())))
        self.assertEqual(task.to_dict(), restored.to_dict())
        self.assertEqual(restored.status, TaskStatus.FAILED)

    def test_segment_roundtrip_with_words_and_accents(self):
        seg = TranscriptSegment.from_dict(
            make_segment(
                text="Texto con acentuación ó",
                words=[{"start_seconds": 10.0, "end_seconds": 11.0, "text": "ó"}],
            )
        )
        restored = TranscriptSegment.from_dict(json.loads(json.dumps(seg.to_dict())))
        self.assertEqual(seg.to_dict(), restored.to_dict())

    def test_clip_roundtrip_with_full_optionals(self):
        clip = ClipProposal.from_dict(
            make_clip(
                transcript_refs=["seg-0001"],
                warnings=["revisar borde"],
                confidence=0.5,
            )
        )
        restored = ClipProposal.from_dict(json.loads(json.dumps(clip.to_dict())))
        self.assertEqual(clip.to_dict(), restored.to_dict())


if __name__ == "__main__":
    unittest.main()

