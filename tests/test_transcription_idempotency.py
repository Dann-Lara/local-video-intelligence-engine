"""Pruebas de idempotencia y recuperación de la etapa de transcripción.

Cubren la corrección de los defectos de Fase 2:

- reutilización solo con huella (audio + config + motor + ids) coincidente,
- regeneración ante audio/config/modelo cambiados o artefactos legacy,
- rechazo de resultados corruptos, vacíos o inexistentes,
- reconciliación de tareas running abandonadas y exclusión de ejecuciones
  activas mediante lock con comprobación de pid,
- orden de persistencia (artefacto antes de succeeded),
- rutas Unicode.

Se usa un transcriptor falso: no se carga el modelo real ni se depende de
GPU/FFmpeg.

Ejecutar con:
    python -m unittest tests.test_transcription_idempotency -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.contracts import (  # noqa: E402
    Project,
    Stage,
    Task,
    TaskStatus,
    TranscriptSegment,
)
from services.transcription import (  # noqa: E402
    TranscriptionConfig,
    TranscriptionInferenceError,
    TranscriptionOrchestrator,
    TranscriptionPersistenceError,
    TranscriptionResult,
    TranscriptionStore,
    TranscriptionTaskStateError,
)
from services.transcription.orchestrator import LOCK_FILENAME  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FakeTranscriber:
    """Transcriptor determinista para pruebas (sin modelo real)."""

    def __init__(
        self, *, fail: bool = False, empty: bool = False, completed: bool = True
    ) -> None:
        self.calls = 0
        self.fail = fail
        self.empty = empty
        self.completed = completed

    def transcribe_audio(
        self,
        audio_path,
        *,
        project_id="unknown",
        source_asset_id="unknown",
        duration_seconds=None,
    ) -> TranscriptionResult:
        self.calls += 1
        if self.fail:
            raise TranscriptionInferenceError("fallo simulado de inferencia")
        dur = float(duration_seconds) if duration_seconds else 5.0
        segments = []
        if not self.empty:
            segments = [
                TranscriptSegment(
                    segment_id="seg_0001",
                    start_seconds=0.0,
                    end_seconds=min(1.0, dur),
                    text="hola mundo",
                ),
                TranscriptSegment(
                    segment_id="seg_0002",
                    start_seconds=min(1.0, dur),
                    end_seconds=min(2.0, dur),
                    text="prueba de idempotencia",
                ),
            ]
        return TranscriptionResult(
            project_id=project_id,
            source_asset_id=source_asset_id,
            model_size="fake-model",
            device="cpu",
            compute_type="int8",
            language="es",
            duration_seconds=dur,
            segments=segments,
            inference_completed=self.completed,
        )


class TranscriptionIdempotencyBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tvit-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.root = self.tmp / "projects"
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def make_project(
        self,
        *,
        duration_seconds: float = 10.0,
        audio_bytes: bytes = b"fake-audio-v1",
        source_filename: str = "audio.mp3",
        base: Path = None,
    ):
        base = base or self.root
        project_id = str(uuid.uuid4())
        project_dir = base / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        source_dir = self.tmp / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_path = source_dir / source_filename
        source_path.write_bytes(audio_bytes)
        project = Project(
            project_id=project_id,
            source_asset_id=f"asset-{project_id[:8]}",
            status="ingested",
            created_at=_now_iso(),
            source_path=str(source_path),
            duration_seconds=duration_seconds,
        )
        (project_dir / "project.json").write_text(
            json.dumps(project.to_dict()), encoding="utf-8"
        )
        return project_dir, project_id, source_path

    def make_orch(self, transcriber, *, root=None, config=None):
        return TranscriptionOrchestrator(
            project_root=root or self.root,
            config=config or TranscriptionConfig(),
            transcriber=transcriber,
        )

    def write_task(self, project_id, status, *, attempt_count=0, root=None):
        store = TranscriptionStore(root or self.root)
        task = Task(
            task_id=str(uuid.uuid4()),
            project_id=project_id,
            stage=Stage.TRANSCRIPTION,
            status=status,
            attempt_count=attempt_count,
        )
        store.save_task(task)
        return task

    def write_lock(self, project_id, *, pid, hostname, token="tok", root=None):
        store = TranscriptionStore(root or self.root)
        lock_path = store.transcription_dir(project_id) / LOCK_FILENAME
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "hostname": hostname,
                    "token": token,
                    "acquired_at": _now_iso(),
                    "project_id": project_id,
                }
            ),
            encoding="utf-8",
        )
        return lock_path

    def dead_pid(self) -> int:
        proc = subprocess.Popen(["cmd", "/c", "exit", "0"])
        proc.wait()
        return proc.pid

    # ------------------------------------------------------------------
    # 1. Misma entrada: no re-inferir
    # ------------------------------------------------------------------

    def test_01_same_input_no_reinference(self):
        project_dir, project_id, _ = self.make_project()
        fake1 = FakeTranscriber()
        r1 = self.make_orch(fake1).transcribe_project(project_dir)
        self.assertEqual(fake1.calls, 1)
        self.assertIsNotNone(r1.fingerprint)

        fake2 = FakeTranscriber()
        r2 = self.make_orch(fake2).transcribe_project(project_dir)
        self.assertEqual(fake2.calls, 0, "no debe re-inferir con huella coincidente")
        self.assertEqual(r1.created_at, r2.created_at)
        task = self.make_orch(FakeTranscriber()).store.load_task(project_id)
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)

    # ------------------------------------------------------------------
    # 2. Audio modificado: regenerar
    # ------------------------------------------------------------------

    def test_02_audio_modified_regenerates(self):
        project_dir, project_id, source_path = self.make_project()
        fake1 = FakeTranscriber()
        self.make_orch(fake1).transcribe_project(project_dir)
        self.assertEqual(fake1.calls, 1)

        source_path.write_bytes(b"fake-audio-v2-different-bytes")
        fake2 = FakeTranscriber()
        self.make_orch(fake2).transcribe_project(project_dir)
        self.assertEqual(fake2.calls, 1, "audio distinto debe regenerar")

    # ------------------------------------------------------------------
    # 3. Configuración cambiada: regenerar
    # ------------------------------------------------------------------

    def test_03_config_changed_regenerates(self):
        project_dir, _, _ = self.make_project()
        self.make_orch(
            FakeTranscriber(), config=TranscriptionConfig(beam_size=5)
        ).transcribe_project(project_dir)

        fake2 = FakeTranscriber()
        self.make_orch(
            fake2, config=TranscriptionConfig(beam_size=9)
        ).transcribe_project(project_dir)
        self.assertEqual(fake2.calls, 1, "config distinta debe regenerar")

    # ------------------------------------------------------------------
    # 4. Modelo/versión incompatible: regenerar
    # ------------------------------------------------------------------

    def test_04_model_version_incompatible_regenerates(self):
        project_dir, project_id, _ = self.make_project()
        self.make_orch(FakeTranscriber()).transcribe_project(project_dir)

        # Modelo distinto en la configuración.
        fake2 = FakeTranscriber()
        self.make_orch(
            fake2, config=TranscriptionConfig(model_size_or_path="otro-modelo")
        ).transcribe_project(project_dir)
        self.assertEqual(fake2.calls, 1)

        # Versión de contrato de resultado distinta en la huella persistida.
        store = TranscriptionStore(self.root)
        path = store.transcription_json_path(project_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["fingerprint"]["result_schema_version"] = "0.0-legacy"
        path.write_text(json.dumps(data), encoding="utf-8")

        fake3 = FakeTranscriber()
        self.make_orch(fake3).transcribe_project(project_dir)
        self.assertEqual(fake3.calls, 1, "versión de huella distinta debe regenerar")

    def test_04b_legacy_without_fingerprint_regenerates(self):
        project_dir, project_id, _ = self.make_project()
        self.make_orch(FakeTranscriber()).transcribe_project(project_dir)

        store = TranscriptionStore(self.root)
        path = store.transcription_json_path(project_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("fingerprint", None)
        path.write_text(json.dumps(data), encoding="utf-8")

        fake = FakeTranscriber()
        self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(
            fake.calls, 1, "artefacto legacy sin huella nunca debe aceptarse"
        )

    # ------------------------------------------------------------------
    # 5. Resultado corrupto: regenerar
    # ------------------------------------------------------------------

    def test_05_corrupt_result_regenerates(self):
        project_dir, project_id, _ = self.make_project()
        self.make_orch(FakeTranscriber()).transcribe_project(project_dir)

        store = TranscriptionStore(self.root)
        path = store.transcription_json_path(project_id)
        path.write_text("{ esto no es json valido", encoding="utf-8")
        self.assertFalse(store.has_valid_transcription(project_id))

        fake = FakeTranscriber()
        self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 1, "resultado corrupto debe regenerar")
        self.assertTrue(store.has_valid_transcription(project_id))

    # ------------------------------------------------------------------
    # 6. Succeeded sin resultado: regenerar
    # ------------------------------------------------------------------

    def test_06_succeeded_without_result_regenerates(self):
        project_dir, project_id, _ = self.make_project()
        self.make_orch(FakeTranscriber()).transcribe_project(project_dir)

        store = TranscriptionStore(self.root)
        store.transcription_json_path(project_id).unlink()
        self.assertFalse(store.has_valid_transcription(project_id))
        self.assertEqual(store.load_task(project_id).status, TaskStatus.SUCCEEDED)

        fake = FakeTranscriber()
        self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 1, "succeeded sin artefacto debe regenerar")

    # ------------------------------------------------------------------
    # 7. Running abandonado: reconciliar y reintentar
    # ------------------------------------------------------------------

    def test_07_abandoned_running_recovered(self):
        project_dir, project_id, _ = self.make_project()
        self.write_task(project_id, TaskStatus.RUNNING, attempt_count=1)

        fake = FakeTranscriber()
        self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 1)
        task = TranscriptionStore(self.root).load_task(project_id)
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(task.attempt_count, 2)
        self.assertIsNone(task.error_code)

    def test_07b_stale_lock_dead_pid_recovered(self):
        project_dir, project_id, _ = self.make_project()
        dead = self.dead_pid()
        import socket

        self.write_lock(project_id, pid=dead, hostname=socket.gethostname())
        self.write_task(project_id, TaskStatus.RUNNING, attempt_count=1)

        from services.transcription.orchestrator import _pid_alive

        if _pid_alive(dead) is not False:
            self.skipTest("no se pudo obtener un pid muerto verificado")

        fake = FakeTranscriber()
        self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(
            TranscriptionStore(self.root).load_task(project_id).status,
            TaskStatus.SUCCEEDED,
        )
        lock_path = TranscriptionStore(self.root).transcription_dir(
            project_id
        ) / LOCK_FILENAME
        self.assertFalse(lock_path.exists(), "el lock debe liberarse al terminar")

    # ------------------------------------------------------------------
    # 8. Ejecución activa: bloquear
    # ------------------------------------------------------------------

    def test_08_active_execution_blocks(self):
        project_dir, project_id, _ = self.make_project()
        import socket

        self.write_lock(
            project_id, pid=os.getpid(), hostname=socket.gethostname()
        )

        fake = FakeTranscriber()
        with self.assertRaises(TranscriptionTaskStateError):
            self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 0, "no debe inferir con ejecución activa")
        self.assertFalse(
            TranscriptionStore(self.root).transcription_json_path(project_id).exists()
        )

    # ------------------------------------------------------------------
    # 9. Interrupción en inferencia: marcar failed
    # ------------------------------------------------------------------

    def test_09_inference_interruption_marks_failed(self):
        project_dir, project_id, _ = self.make_project()
        with self.assertRaises(TranscriptionInferenceError):
            self.make_orch(FakeTranscriber(fail=True)).transcribe_project(project_dir)

        store = TranscriptionStore(self.root)
        task = store.load_task(project_id)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.error_code, "TranscriptionInferenceError")
        self.assertFalse(store.transcription_json_path(project_id).exists())

        # Reintento posterior funciona.
        fake = FakeTranscriber()
        self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(store.load_task(project_id).status, TaskStatus.SUCCEEDED)

    # ------------------------------------------------------------------
    # 10. Interrupción en persistencia: marcar failed
    # ------------------------------------------------------------------

    def test_10_persistence_interruption_marks_failed(self):
        project_dir, project_id, _ = self.make_project()
        orch = self.make_orch(FakeTranscriber())
        with mock.patch.object(
            orch.store,
            "save_transcription",
            side_effect=TranscriptionPersistenceError("disco simulado lleno"),
        ):
            with self.assertRaises(TranscriptionPersistenceError):
                orch.transcribe_project(project_dir)

        store = TranscriptionStore(self.root)
        self.assertEqual(store.load_task(project_id).status, TaskStatus.FAILED)
        self.assertFalse(store.transcription_json_path(project_id).exists())

    # ------------------------------------------------------------------
    # 11. Guardado del artefacto antes de succeeded
    # ------------------------------------------------------------------

    def test_11_artifact_written_before_succeeded(self):
        project_dir, project_id, _ = self.make_project()
        orch = self.make_orch(FakeTranscriber())
        original_save_task = orch.store.save_task
        state = {"raised": False}

        def failing_on_succeeded(task):
            if task.status == TaskStatus.SUCCEEDED and not state["raised"]:
                state["raised"] = True
                raise TranscriptionPersistenceError(
                    "fallo simulado al marcar succeeded"
                )
            return original_save_task(task)

        with mock.patch.object(
            orch.store, "save_task", side_effect=failing_on_succeeded
        ):
            with self.assertRaises(TranscriptionPersistenceError):
                orch.transcribe_project(project_dir)

        store = TranscriptionStore(self.root)
        # El artefacto quedó escrito (nunca hay succeeded sin resultado)...
        self.assertTrue(store.transcription_json_path(project_id).exists())
        data = json.loads(store.transcription_json_path(project_id).read_text("utf-8"))
        self.assertIsNotNone(data.get("fingerprint"))
        # ...pero el fallo es visible: la tarea no quedó succeeded.
        task = store.load_task(project_id)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.error_code, "TranscriptionPersistenceError")

    # ------------------------------------------------------------------
    # 12. Resultado sin segmentos: completado (válido) vs incompleto (error)
    # ------------------------------------------------------------------

    def test_12_empty_completed_is_valid(self):
        """Audio sin voz: inferencia completa con 0 segmentos es legítima."""
        project_dir, project_id, _ = self.make_project()
        fake = FakeTranscriber(empty=True, completed=True)
        result = self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 1)
        self.assertTrue(result.inference_completed)
        self.assertEqual(result.segments, [])

        store = TranscriptionStore(self.root)
        self.assertEqual(store.load_task(project_id).status, TaskStatus.SUCCEEDED)
        self.assertTrue(store.transcription_json_path(project_id).exists())
        self.assertTrue(store.has_valid_transcription(project_id))

        # Y es reutilizable sin re-inferir.
        fake2 = FakeTranscriber(empty=True, completed=True)
        r2 = self.make_orch(fake2).transcribe_project(project_dir)
        self.assertEqual(fake2.calls, 0)
        self.assertEqual(r2.segments, [])

    def test_12b_incomplete_result_rejected(self):
        """Resultado parcial (inferencia no completada) no se persiste."""
        project_dir, project_id, _ = self.make_project()
        fake = FakeTranscriber(completed=False)
        with self.assertRaises(TranscriptionInferenceError):
            self.make_orch(fake).transcribe_project(project_dir)

        store = TranscriptionStore(self.root)
        self.assertEqual(store.load_task(project_id).status, TaskStatus.FAILED)
        self.assertFalse(store.transcription_json_path(project_id).exists())

    def test_12c_persisted_incomplete_is_invalid(self):
        """Un artefacto con segmentos pero sin finalización no es reutilizable."""
        project_dir, project_id, source_path = self.make_project()
        store = TranscriptionStore(self.root)
        incomplete = TranscriptionResult(
            project_id=project_id,
            source_asset_id=f"asset-{project_id[:8]}",
            model_size="fake-model",
            device="cpu",
            compute_type="int8",
            language="es",
            duration_seconds=10.0,
            segments=[
                TranscriptSegment(
                    segment_id="seg_0001",
                    start_seconds=0.0,
                    end_seconds=1.0,
                    text="hola",
                )
            ],
            inference_completed=False,
        )
        store.save_transcription(incomplete)

        self.assertFalse(store.has_valid_transcription(project_id))
        with self.assertRaises(TranscriptionPersistenceError):
            store.load_transcription(project_id)

        # El orquestador lo regenera en lugar de aceptarlo.
        fake = FakeTranscriber()
        self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 1)
        self.assertTrue(store.has_valid_transcription(project_id))

    # ------------------------------------------------------------------
    # 13. Rutas Unicode
    # ------------------------------------------------------------------

    def test_13_unicode_paths(self):
        unicode_root = self.tmp / "proyectos-español-ñ-测试-日本語"
        unicode_root.mkdir(parents=True, exist_ok=True)
        project_dir, project_id, _ = self.make_project(
            source_filename="vídeo-ñ-猫.mp3", base=unicode_root
        )

        fake1 = FakeTranscriber()
        r1 = self.make_orch(fake1, root=unicode_root).transcribe_project(project_dir)
        self.assertEqual(fake1.calls, 1)
        self.assertTrue(
            TranscriptionStore(unicode_root).has_valid_transcription(project_id)
        )

        fake2 = FakeTranscriber()
        r2 = self.make_orch(fake2, root=unicode_root).transcribe_project(project_dir)
        self.assertEqual(fake2.calls, 0, "la reutilización debe funcionar con Unicode")
        self.assertEqual(r1.created_at, r2.created_at)

    # ------------------------------------------------------------------
    # Extras: limpieza del lock y no confiar en la antigüedad del timestamp
    # ------------------------------------------------------------------

    def test_14_lock_released_after_success(self):
        project_dir, project_id, _ = self.make_project()
        self.make_orch(FakeTranscriber()).transcribe_project(project_dir)
        lock_path = (
            TranscriptionStore(self.root).transcription_dir(project_id)
            / LOCK_FILENAME
        )
        self.assertFalse(lock_path.exists())

    def test_15_corrupt_lock_fails_explicitly(self):
        project_dir, project_id, _ = self.make_project()
        store = TranscriptionStore(self.root)
        lock_path = store.transcription_dir(project_id) / LOCK_FILENAME
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("no-json", encoding="utf-8")

        fake = FakeTranscriber()
        with self.assertRaises(TranscriptionTaskStateError):
            self.make_orch(fake).transcribe_project(project_dir)
        self.assertEqual(fake.calls, 0)


if __name__ == "__main__":
    unittest.main()
