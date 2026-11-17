"""Pruebas focalizadas de la persistencia diagnostica de rechazos (Fase 4B).

Verifican que la instrumentacion es puramente observacional: enriquece los
registros de rechazo y persiste `editorial/diagnostics.json` SIN cambiar el
conjunto de candidatos aceptados ni los motivos de rechazo.

No se ejecuta LM Studio: se reusa el cliente falso de
`test_editorial_pipeline`. No se lanza Whisper ni FFmpeg.

Ejecutar con:
    python -m unittest tests.test_editorial_diagnostics -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.contracts import Project, TranscriptSegment  # noqa: E402
from schemas.editorial import WindowRecord  # noqa: E402
from services.editorial import (  # noqa: E402
    DIAGNOSTIC_SCHEMA_VERSION,
    MAX_RAW_RESPONSE_CHARS,
    EditorialConfig,
    EditorialOrchestrator,
    EditorialStore,
)
from services.editorial.parse import parse_model_response  # noqa: E402
from services.transcription import TranscriptionResult, TranscriptionStore  # noqa: E402
from tests.test_editorial_pipeline import FakeModelClient, valid_response  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seg(sid, start, end, text):
    return TranscriptSegment(
        segment_id=sid, start_seconds=start, end_seconds=end, text=text
    )


def _parse(raw, *, body_ids, segments, config=None):
    by_id = {s.segment_id: s for s in segments}
    state = {"n": 0}

    def factory():
        state["n"] += 1
        return f"clip_{state['n']:04d}"

    return parse_model_response(
        raw,
        source_asset_id="asset-1",
        window_id="win_0001",
        body_ids=body_ids,
        segments_by_id=by_id,
        config=config or EditorialConfig(model_id="test-model"),
        duration_seconds=120.0,
        clip_id_factory=factory,
    )


class ParseDiagnosticsTests(unittest.TestCase):
    """Casos 1-4 y 11: enriquecido del registro de rechazo."""

    def setUp(self):
        self.segments = [
            seg("seg_0001", 0.0, 30.0, "texto uno comun"),
            seg("seg_0002", 30.0, 60.0, "texto dos comun"),
            seg("seg_short", 55.0, 60.0, "corto texto"),
        ]

    def test_evidence_not_found_keeps_evidence_and_segment_ids(self):  # caso 1
        raw = json.dumps({"candidates": [{
            "segment_ids": ["seg_0001"],
            "title": "T",
            "evidence_text": "frase inventada",
        }]})
        _, rejected = _parse(raw, body_ids=["seg_0001", "seg_0002"], segments=self.segments)
        entry = rejected[0]
        self.assertEqual(entry["reason"], "evidence_not_found")
        self.assertEqual(entry["segment_ids"], ["seg_0001"])
        self.assertEqual(entry["evidence_text"], "frase inventada")
        self.assertEqual(entry["title"], "T")
        self.assertEqual(entry["proposed_index"], 0)
        self.assertEqual(entry["diagnostic_schema_version"], DIAGNOSTIC_SCHEMA_VERSION)
        self.assertIn("detail", entry)

    def test_duration_out_of_range_keeps_computed(self):  # caso 2
        raw = json.dumps({"candidates": [{
            "segment_ids": ["seg_short"],
            "title": "T",
            "evidence_text": "corto texto",
        }]})
        _, rejected = _parse(raw, body_ids=["seg_short"], segments=self.segments)
        entry = rejected[0]
        self.assertEqual(entry["reason"], "duration_out_of_range")
        computed = entry["computed"]
        self.assertEqual(computed["duration_seconds"], 5.0)
        self.assertEqual(computed["clip_min"], 15.0)
        self.assertEqual(computed["clip_target"], 45.0)
        self.assertEqual(computed["clip_max"], 90.0)
        self.assertEqual(computed["referenced_segment_count"], 1)

    def test_segment_out_of_body_keeps_id_and_body_bounds(self):  # caso 3
        raw = json.dumps({"candidates": [{
            "segment_ids": ["seg_9999"],
            "title": "T",
            "evidence_text": "x",
        }]})
        _, rejected = _parse(raw, body_ids=["seg_0001", "seg_0002"], segments=self.segments)
        entry = rejected[0]
        self.assertEqual(entry["reason"], "segment_out_of_body")
        self.assertEqual(entry["segment_ids"], ["seg_9999"])
        self.assertEqual(entry["body_min"], "seg_0001")
        self.assertEqual(entry["body_max"], "seg_0002")
        self.assertIn("seg_9999", entry["detail"])

    def test_rejection_without_optional_fields_still_valid(self):  # caso 4
        w = WindowRecord(
            window_id="win_0001",
            first_segment_id="seg_0001",
            last_segment_id="seg_0002",
            segment_ids=["seg_0001", "seg_0002"],
            rejected=[{"reason": "evidence_not_found", "detail": "x"}],
        )
        self.assertEqual(w.rejected[0]["reason"], "evidence_not_found")

    def test_no_credentials_in_diagnostics(self):  # caso 11
        raw = json.dumps({"candidates": [
            {"segment_ids": ["seg_0001"], "title": "t", "evidence_text": "inventado"}
        ]})
        _, rejected = _parse(raw, body_ids=["seg_0001", "seg_0002"], segments=self.segments)
        blob = json.dumps(rejected).lower()
        for needle in ("authorization", "bearer", "api_key", "token", "password"):
            self.assertNotIn(needle, blob)


class OrchestratorDiagnosticsTests(unittest.TestCase):
    """Casos 5-10 y 12: persistencia end-to-end sin red."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tdiag-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.root = self.tmp / "projects"
        self.root.mkdir(parents=True, exist_ok=True)

    def make_project(self, *, duration_seconds=60.0):
        project_id = str(uuid.uuid4())
        project_dir = self.root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        source = self.tmp / "src.mp4"
        source.write_bytes(b"contenido fuente")
        project = Project(
            project_id=project_id,
            source_asset_id=f"asset-{project_id[:8]}",
            status="ingested",
            created_at=_now_iso(),
            source_path=str(source),
            duration_seconds=duration_seconds,
        )
        (project_dir / "project.json").write_text(
            json.dumps(project.to_dict()), encoding="utf-8"
        )
        return project_dir, project

    def write_transcription(self, project, segments=None):
        result = TranscriptionResult(
            project_id=project.project_id,
            source_asset_id=project.source_asset_id,
            model_size="fake-model",
            device="cpu",
            compute_type="int8",
            language="es",
            duration_seconds=60.0,
            segments=segments if segments is not None else [
                seg("seg_0001", 0.0, 30.0, "texto uno comun"),
                seg("seg_0002", 30.0, 60.0, "texto dos comun"),
            ],
            inference_completed=True,
        )
        TranscriptionStore(self.root).save_transcription(result)
        return result

    def orch(self, client, *, config=None):
        return EditorialOrchestrator(
            project_root=self.root,
            config=config or EditorialConfig(model_id="test-model"),
            client=client,
        )

    def test_invalid_json_persisted_in_diagnostics(self):  # caso 5
        project_dir, project = self.make_project()
        self.write_transcription(project)
        # Dos respuestas invalidas: agota los 2 intentos (max_repair_attempts=1).
        fake = FakeModelClient(responses=["no hay json aqui", "tampoco aqui"])
        with self.assertRaises(Exception):
            self.orch(fake).analyze_project(project_dir)
        diag = EditorialStore(self.root).load_diagnostics(project.project_id)
        self.assertIsNotNone(diag)
        self.assertEqual(diag["windows"][0]["parse_ok"], False)
        self.assertEqual(diag["windows"][0]["raw_response"], "no hay json aqui")
        self.assertEqual(len(diag["windows"]), 2)  # un registro por intento

    def test_multiple_proposals_one_raw_per_window(self):  # caso 6
        project_dir, project = self.make_project()
        self.write_transcription(project)
        multi = json.dumps({"candidates": [
            {"segment_ids": ["seg_0001"], "title": "t1", "evidence_text": "inventado uno"},
            {"segment_ids": ["seg_0002"], "title": "t2", "evidence_text": "inventado dos"},
        ]})
        fake = FakeModelClient(responses=[multi])
        run = self.orch(fake).analyze_project(project_dir)
        self.assertEqual(len(run.selection["rejected"]), 2)
        diag = EditorialStore(self.root).load_diagnostics(project.project_id)
        self.assertEqual(len(diag["windows"]), 1)
        self.assertEqual(diag["windows"][0]["rejected_count"], 2)
        self.assertEqual(diag["windows"][0]["raw_response"], multi)
        self.assertEqual(diag["windows"][0]["rejection_reasons"].get("evidence_not_found"), 2)

    def test_truncated_response_is_marked_incomplete(self):  # caso 7
        project_dir, project = self.make_project()
        self.write_transcription(project)
        big = "x" * (MAX_RAW_RESPONSE_CHARS + 500)
        fake = FakeModelClient(responses=[big, big])
        with self.assertRaises(Exception):
            self.orch(fake).analyze_project(project_dir)
        diag = EditorialStore(self.root).load_diagnostics(project.project_id)
        w = diag["windows"][0]
        self.assertTrue(w["raw_response_truncated"])
        self.assertEqual(len(w["raw_response"]), MAX_RAW_RESPONSE_CHARS)
        self.assertEqual(w["raw_response_chars"], MAX_RAW_RESPONSE_CHARS + 500)

    def test_old_artifacts_readable_without_diagnostics(self):  # caso 8
        project_dir, project = self.make_project()
        self.write_transcription(project)
        fake = FakeModelClient(responses=[valid_response()])
        self.orch(fake).analyze_project(project_dir)
        store = EditorialStore(self.root)
        diag_path = store.diagnostics_json_path(project.project_id)
        if diag_path.exists():
            diag_path.unlink()
        self.assertIsNone(store.load_diagnostics(project.project_id))
        run = store.load_run(project.project_id)
        self.assertEqual(run.status, "succeeded")

    def test_diagnostics_preserve_history(self):  # caso 9
        project_dir, project = self.make_project()
        store = EditorialStore(self.root)
        store.save_diagnostics(project.project_id, {
            "project_id": project.project_id,
            "run_created_at": "2026-01-01T00:00:00+00:00",
            "windows": [{"window_id": "win_0001", "raw_response": "previo"}],
        })
        store.save_diagnostics(project.project_id, {
            "project_id": project.project_id,
            "run_created_at": "2026-01-02T00:00:00+00:00",
            "windows": [{"window_id": "win_0001", "raw_response": "nuevo"}],
        })
        diag = store.load_diagnostics(project.project_id)
        self.assertEqual(diag["windows"][0]["raw_response"], "nuevo")
        self.assertEqual(diag["run_created_at"], "2026-01-02T00:00:00+00:00")
        self.assertEqual(len(diag["history"]), 1)
        self.assertEqual(diag["history"][0]["run_created_at"], "2026-01-01T00:00:00+00:00")

    def test_instrumentation_does_not_change_acceptance(self):  # caso 10
        project_dir, project = self.make_project()
        self.write_transcription(project)
        fake = FakeModelClient(responses=[valid_response()])
        run = self.orch(fake).analyze_project(project_dir)
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(len(run.candidates), 1)
        self.assertEqual(run.selection["rejected_count"], 0)

    def test_persistence_error_does_not_mark_success(self):  # caso 12
        project_dir, project = self.make_project()
        self.write_transcription(project)
        store = EditorialStore(self.root)
        original = EditorialStore._atomic_write_json

        def failing(self_store, target, data):
            if getattr(target, "name", "") == "candidates.json":
                from services.editorial.errors import EditorialPersistenceError
                raise EditorialPersistenceError("fallo simulado de escritura")
            return original(self_store, target, data)

        EditorialStore._atomic_write_json = failing  # type: ignore
        self.addCleanup(
            setattr, EditorialStore, "_atomic_write_json", original
        )
        fake = FakeModelClient(responses=[valid_response()])
        with self.assertRaises(Exception):
            self.orch(fake).analyze_project(project_dir)
        self.assertEqual(store.load_task(project.project_id).status.value, "failed")


if __name__ == "__main__":
    unittest.main()
