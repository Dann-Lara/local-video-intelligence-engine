"""Pruebas de la pipeline editorial completa (Fase 3).

Usan un cliente de modelo FALSO: no se ejecuta LM Studio, no se carga ningun
modelo y no se abre conexion de red. Se verifica el flujo, la idempotencia,
la recuperacion, el lock y la diferenciacion entre fallo y cero resultados.

Ejecutar con:
    python -m unittest tests.test_editorial_pipeline -v
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.contracts import (  # noqa: E402
    Project,
    Stage,
    Task,
    TaskStatus,
    TranscriptSegment,
)
from services.editorial import (  # noqa: E402
    EditorialConfig,
    EditorialInferenceError,
    EditorialOrchestrator,
    EditorialStore,
    EditorialTaskStateError,
    EditorialTranscriptionError,
)
from services.editorial.errors import EditorialModelUnavailableError  # noqa: E402
from services.transcription import TranscriptionResult, TranscriptionStore  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seg(sid, start, end, text):
    return TranscriptSegment(
        segment_id=sid, start_seconds=start, end_seconds=end, text=text
    )


def default_segments():
    return [
        seg("seg_0001", 0.0, 30.0, "texto uno comun"),
        seg("seg_0002", 30.0, 60.0, "texto dos comun"),
    ]


def valid_response(refs=("seg_0001", "seg_0002"), evidence="texto uno comun texto dos comun",
                   title="Titulo editorial", confidence=0.85):
    return json.dumps({
        "candidates": [{
            "segment_ids": list(refs),
            "title": title,
            "hook": "gancho",
            "main_idea": "idea",
            "ending": "cierre",
            "editorial_reason": "razon",
            "evidence_text": evidence,
            "context_dependency": "low",
            "confidence": confidence,
            "signals": [{"name": "hook", "status": "inferred", "evidence": "", "note": ""}],
        }]
    })


class FakeModelClient:
    """Cliente de modelo deterministico (sin red, sin modelo)."""

    def __init__(self, responses=None, fail_after=None):
        self.responses = list(responses or [])
        self.fail_after = fail_after
        self.calls = 0
        self.prompts = []

    def complete(self, *, system_prompt, user_prompt):
        self.calls += 1
        self.prompts.append(user_prompt)
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise EditorialModelUnavailableError("fallo simulado de modelo")
        if self.responses:
            return self.responses.pop(0)
        return json.dumps({"candidates": []})


class EditorialPipelineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tef-"))
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

    def write_transcription(self, project, segments=None, *, duration_seconds=60.0,
                            completed=True):
        result = TranscriptionResult(
            project_id=project.project_id,
            source_asset_id=project.source_asset_id,
            model_size="fake-model",
            device="cpu",
            compute_type="int8",
            language="es",
            duration_seconds=duration_seconds,
            segments=segments if segments is not None else default_segments(),
            inference_completed=completed,
        )
        TranscriptionStore(self.root).save_transcription(result)
        return result

    def orch(self, client, *, config=None, root=None):
        return EditorialOrchestrator(
            project_root=root or self.root,
            config=config or EditorialConfig(model_id="test-model"),
            client=client,
        )

    # ------------------------------------------------------------------

    def test_success_writes_candidates_and_pending_review(self):
        project_dir, project = self.make_project()
        self.write_transcription(project)
        fake = FakeModelClient(responses=[valid_response()])
        run = self.orch(fake).analyze_project(project_dir)

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(len(run.candidates), 1)
        cand = run.candidates[0]
        self.assertEqual(cand.proposal.review_status.value, "pending")
        self.assertTrue(cand.evidence_text)
        self.assertEqual(cand.proposal.start_seconds, 0.0)
        self.assertEqual(cand.proposal.end_seconds, 60.0)

        store = EditorialStore(self.root)
        self.assertTrue(store.candidates_json_path(project.project_id).exists())
        task = store.load_task(project.project_id)
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(task.stage, Stage.EDITORIAL)
        self.assertEqual(task.attempt_count, 1)

    def test_zero_candidates_is_success_not_failure(self):
        project_dir, project = self.make_project()
        self.write_transcription(project)
        fake = FakeModelClient()  # devuelve lista vacia
        run = self.orch(fake).analyze_project(project_dir)
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.candidates, [])
        store = EditorialStore(self.root)
        self.assertTrue(store.candidates_json_path(project.project_id).exists())
        self.assertEqual(store.load_task(project.project_id).status, TaskStatus.SUCCEEDED)

    def test_window_failure_marks_task_failed_and_no_artifact(self):
        project_dir, project = self.make_project()
        self.write_transcription(project)
        fake = FakeModelClient(fail_after=1)
        with self.assertRaises(EditorialInferenceError):
            self.orch(fake).analyze_project(project_dir)
        store = EditorialStore(self.root)
        self.assertFalse(store.candidates_json_path(project.project_id).exists())
        self.assertEqual(store.load_task(project.project_id).status, TaskStatus.FAILED)

    def test_partial_window_failure_no_artifact(self):
        project_dir, project = self.make_project(duration_seconds=60.0)
        self.write_transcription(project)
        # target_chars=1 -> dos ventanas (una por segmento).
        config = EditorialConfig(model_id="test-model", window_target_chars=1)
        fake = FakeModelClient(
            responses=[valid_response(refs=["seg_0001"], evidence="texto uno comun")],
            fail_after=2,
        )
        with self.assertRaises(EditorialInferenceError):
            self.orch(fake, config=config).analyze_project(project_dir)
        self.assertGreaterEqual(fake.calls, 2)
        store = EditorialStore(self.root)
        self.assertFalse(store.candidates_json_path(project.project_id).exists())
        self.assertEqual(store.load_task(project.project_id).status, TaskStatus.FAILED)

    def test_reuse_skips_model(self):
        project_dir, project = self.make_project()
        self.write_transcription(project)
        fake1 = FakeModelClient(responses=[valid_response()])
        r1 = self.orch(fake1).analyze_project(project_dir)
        self.assertGreaterEqual(fake1.calls, 1)

        fake2 = FakeModelClient(responses=[valid_response(title="otro")])
        r2 = self.orch(fake2).analyze_project(project_dir)
        self.assertEqual(fake2.calls, 0, "no debe re-ejecutar con huella coincidente")
        self.assertEqual(r1.created_at, r2.created_at)

    def test_transcription_change_regenerates(self):
        project_dir, project = self.make_project()
        self.write_transcription(project)
        fake1 = FakeModelClient(responses=[valid_response()])
        self.orch(fake1).analyze_project(project_dir)

        changed = [
            seg("seg_0001", 0.0, 30.0, "contenido totalmente distinto"),
            seg("seg_0002", 30.0, 60.0, "texto dos comun"),
        ]
        self.write_transcription(project, segments=changed)
        fake2 = FakeModelClient(responses=[json.dumps({"candidates": []})])
        self.orch(fake2).analyze_project(project_dir)
        self.assertEqual(fake2.calls, 1, "transcripcion distinta debe regenerar")

    def test_config_change_regenerates(self):
        project_dir, project = self.make_project()
        self.write_transcription(project)
        fake1 = FakeModelClient(responses=[valid_response()])
        self.orch(fake1, config=EditorialConfig(model_id="test-model")).analyze_project(
            project_dir
        )
        fake2 = FakeModelClient(responses=[valid_response()])
        self.orch(fake2, config=EditorialConfig(model_id="otro-modelo")).analyze_project(
            project_dir
        )
        self.assertEqual(fake2.calls, 1, "modelo distinto debe regenerar")

    def test_missing_transcription_raises(self):
        project_dir, _ = self.make_project()
        fake = FakeModelClient(responses=[valid_response()])
        with self.assertRaises(EditorialTranscriptionError):
            self.orch(fake).analyze_project(project_dir)
        self.assertEqual(fake.calls, 0)

    def test_lock_conflict_raises(self):
        project_dir, project = self.make_project()
        self.write_transcription(project)
        store = EditorialStore(self.root)
        lock_path = store.editorial_dir(project.project_id) / "editorial.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "token": "otro",
                "acquired_at": _now_iso(),
                "project_id": project.project_id,
            }),
            encoding="utf-8",
        )
        fake = FakeModelClient(responses=[valid_response()])
        with self.assertRaises(EditorialTaskStateError):
            self.orch(fake).analyze_project(project_dir)

    def test_reconciles_abandoned_running_task(self):
        project_dir, project = self.make_project()
        self.write_transcription(project)
        store = EditorialStore(self.root)
        store.save_task(Task(
            task_id=str(uuid.uuid4()),
            project_id=project.project_id,
            stage=Stage.EDITORIAL,
            status=TaskStatus.RUNNING,
            attempt_count=1,
        ))
        fake = FakeModelClient(responses=[valid_response()])
        run = self.orch(fake).analyze_project(project_dir)
        self.assertEqual(run.status, "succeeded")
        task = store.load_task(project.project_id)
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(task.attempt_count, 2)


if __name__ == "__main__":
    unittest.main()
