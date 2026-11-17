"""Orquestador de la etapa de transcripción.

Correcciones de idempotencia y recuperación:

- La reutilización de una transcripción persistida exige task succeeded,
  resultado cargable y validado, y huella (audio + config + motor + ids)
  coincidente con la ejecución actual. Los artefactos legacy sin huella
  nunca se aceptan por inferencia: se regeneran.
- Una tarea en estado running se reconcilia: con lock activo (pid vivo en
  este host) falla explícitamente; sin lock se marca fallida (ejecución
  abandonada) y se reintenta.
- Un lock file (transcribe.lock) con pid + host + token excluye ejecuciones
  concurrentes de la misma etapa en el mismo proyecto.
- El orden de persistencia es: transcription.json primero, task succeeded
  después; nunca se marca succeeded sin artefacto escrito.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from schemas.contracts import Project, Task, TaskStatus, Stage
from schemas.validation import ProjectContext
from services.ingestion.errors import (
    ProjectDirectoryConflictError,
    SourceNotFoundError,
)
from services.ingestion.ingest import (
    PROJECT_JSON,
)

from .config import TranscriptionConfig
from .errors import (
    TranscriptionError,
    TranscriptionInferenceError,
    TranscriptionInvalidSegmentsError,
    TranscriptionNotFoundError,
    TranscriptionPersistenceError,
    TranscriptionTaskStateError,
)
from .fingerprint import (
    build_fingerprint,
    compute_audio_sha256,
    fingerprint_diff,
    fingerprint_matches,
)
from .models import TranscriptionResult
from .store import TranscriptionStore, TRANSCRIPTION_JSON, TASK_JSON
from .transcriber import Transcriber


logger = logging.getLogger(__name__)

LOCK_FILENAME = "transcribe.lock"

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pid_alive(pid: int) -> Optional[bool]:
    """True si el pid está vivo, False si no, None si es indeterminable.

    En Windows no se usa os.kill(pid, 0) (podría terminar el proceso);
    se consulta el estado con OpenProcess/GetExitCodeProcess.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_INVALID_PARAMETER = 87
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if handle:
            code = ctypes.c_ulong(0)
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            if not ok:
                return None
            return code.value == STILL_ACTIVE
        err = kernel32.GetLastError()
        if err == ERROR_INVALID_PARAMETER:
            return False
        # Acceso denegado u otro motivo: no se puede determinar.
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _load_project(project_dir: Path) -> Project:
    project_json = project_dir / PROJECT_JSON
    try:
        data = json.loads(project_json.read_text(encoding="utf-8"))
        return Project.from_dict(data)
    except Exception as exc:
        raise ProjectDirectoryConflictError(
            f"No se pudo cargar project.json: {exc}", path=str(project_dir)
        ) from exc


def _extract_audio_if_needed(
    source_path: Path,
    project_dir: Path,
) -> Path:
    """Extrae audio a WAV mono 16kHz para transcripción si es necesario.

    Si el origen ya es audio y válido, lo devuelve. Usa FFmpeg con argumentos
    estructurados (sin shell). Artefacto persistente en project_dir/audio/.
    """
    suffix = source_path.suffix.lower()
    if suffix in _AUDIO_EXTS:
        try:
            if source_path.exists() and source_path.stat().st_size > 0:
                return source_path
        except Exception:
            pass

    audio_dir = project_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "audio.wav"

    import subprocess

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(audio_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
            text=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
            raise TranscriptionError(f"FFmpeg falló: {err}")
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError(f"Timeout extrayendo audio: {exc}") from exc
    except FileNotFoundError as exc:
        raise TranscriptionError(f"FFmpeg no encontrado: {exc}") from exc
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError(f"Error extrayendo audio: {exc}") from exc

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise TranscriptionError(f"Audio extraído inválido: {audio_path}")
    return audio_path


class TranscriptionOrchestrator:
    """Orquestador de la etapa de transcripción."""

    def __init__(
        self,
        project_root: Optional[Path] = None,
        config: Optional[TranscriptionConfig] = None,
        transcriber: Optional[Any] = None,
    ) -> None:
        self.store = TranscriptionStore(project_root)
        self.config = config or TranscriptionConfig()
        self.transcriber = transcriber or Transcriber(self.config)

    # ------------------------------------------------------------------
    # Tarea y lock
    # ------------------------------------------------------------------

    def _get_task(self, project_id: str) -> Task:
        task = self.store.load_task(project_id)
        if task is None:
            task = Task(
                task_id=str(uuid.uuid4()),
                project_id=project_id,
                stage=Stage.TRANSCRIPTION,
                status=TaskStatus.PENDING,
                attempt_count=0,
            )
        return task

    def _lock_path(self, project_id: str) -> Path:
        return self.store.transcription_dir(project_id) / LOCK_FILENAME

    def _read_lock(self, lock_path: Path) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _acquire_lock(self, project_id: str) -> Dict[str, Any]:
        """Adquiere el lock exclusivo de la etapa para este proyecto.

        - Si existe un lock con un pid vivo en este host, falla explícitamente.
        - Si el pid está muerto (ejecución abandonada), retira el lock y lo
          adquiere.
        - Si el lock no se puede interpretar o verificar, falla explícitamente
          (nunca se asume que una ejecución está muerta sin evidencia).
        """
        lock_path = self._lock_path(project_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "token": uuid.uuid4().hex,
            "acquired_at": _now_iso(),
            "project_id": project_id,
        }
        for _ in range(2):
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                existing = self._read_lock(lock_path)
                if existing is None:
                    raise TranscriptionTaskStateError(
                        f"Lock de transcripción presente pero ilegible; no se puede "
                        f"determinar si hay una ejecución activa. Revisar manualmente: "
                        f"{lock_path}"
                    )
                existing_host = existing.get("hostname")
                if existing_host != socket.gethostname():
                    raise TranscriptionTaskStateError(
                        f"Lock de transcripción creado por otro host "
                        f"({existing_host!r}); no se puede verificar su vigencia: "
                        f"{lock_path}"
                    )
                try:
                    existing_pid = int(existing.get("pid", -1))
                except (TypeError, ValueError):
                    existing_pid = -1
                alive = _pid_alive(existing_pid)
                if alive is True:
                    raise TranscriptionTaskStateError(
                        f"Otra ejecución de transcripción está activa para el proyecto "
                        f"{project_id} (pid={existing_pid}). Lock: {lock_path}"
                    )
                if alive is None:
                    raise TranscriptionTaskStateError(
                        f"No se pudo determinar si la ejecución anterior (pid="
                        f"{existing_pid}) sigue activa; revisión manual requerida: "
                        f"{lock_path}"
                    )
                # Pid muerto: lock obsoleto de una ejecución abandonada.
                logger.warning(
                    "Retirando lock obsoleto de ejecución abandonada (pid=%s): %s",
                    existing_pid,
                    lock_path,
                )
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise TranscriptionTaskStateError(
                        f"No se pudo retirar el lock obsoleto {lock_path}: {exc}"
                    ) from exc
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            return payload
        raise TranscriptionTaskStateError(
            f"No se pudo adquirir el lock de transcripción: {lock_path}"
        )

    def _release_lock(self, lock_path: Path, token: str) -> None:
        existing = self._read_lock(lock_path)
        if existing is None:
            return
        if existing.get("token") != token:
            logger.warning(
                "Lock %s pertenece a otro token; no se retira.", lock_path
            )
            return
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("No se pudo retirar el lock %s: %s", lock_path, exc)

    # ------------------------------------------------------------------
    # Reutilización y reconciliación
    # ------------------------------------------------------------------

    def _load_for_reuse(self, project_id: str) -> Optional[TranscriptionResult]:
        try:
            return self.store.load_transcription(project_id)
        except TranscriptionPersistenceError as exc:
            logger.warning(
                "Resultado persistido no reutilizable (se regenera): %s", exc
            )
            return None

    def _reconcile_running_task(self, task: Task) -> Task:
        """Reconcilia una tarea running cuando este proceso ya tiene el lock.

        Con el lock en manos no puede existir otra ejecución viva: un estado
        running es una ejecución abandonada (crash). Se marca failed de forma
        visible antes de reintentar.
        """
        if task.status != TaskStatus.RUNNING:
            return task
        logger.warning(
            "Tarea %s en running sin ejecución activa (lock adquirido); "
            "se marca como fallida (ejecución abandonada).",
            task.task_id,
        )
        task.status = TaskStatus.FAILED
        task.finished_at = _now_iso()
        task.error_code = "AbandonedRunningTask"
        task.error_message = (
            "Tarea en estado running sin ejecución activa; se marca como "
            "fallida para permitir el reintento."
        )
        self.store.save_task(task)
        return task

    def _decide_reuse(
        self,
        task: Task,
        project: Project,
        current_fingerprint: Dict[str, Any],
    ) -> Optional[TranscriptionResult]:
        """Devuelve el resultado a reutilizar o None si hay que regenerar."""
        if task.status != TaskStatus.SUCCEEDED:
            return None
        existing = self._load_for_reuse(project.project_id)
        if existing is None:
            return None
        if existing.project_id != project.project_id:
            logger.warning(
                "Regenerando: project_id del resultado (%s) no coincide con el "
                "proyecto (%s).",
                existing.project_id,
                project.project_id,
            )
            return None
        if existing.source_asset_id != project.source_asset_id:
            logger.warning(
                "Regenerando: source_asset_id del resultado (%s) no coincide con "
                "el proyecto (%s).",
                existing.source_asset_id,
                project.source_asset_id,
            )
            return None
        if not fingerprint_matches(existing.fingerprint, current_fingerprint):
            diffs = fingerprint_diff(existing.fingerprint, current_fingerprint)
            logger.warning(
                "Regenerando: huella no coincide (%d diferencias): %s",
                len(diffs),
                "; ".join(diffs[:5]),
            )
            return None
        logger.info(
            "Reutilizando transcripción existente de %s (huella coincidente).",
            project.project_id,
        )
        return existing

    # ------------------------------------------------------------------
    # Flujo principal
    # ------------------------------------------------------------------

    def _resolve_audio_for_transcription(
        self, source_path: Path, project_dir: Path
    ) -> Path:
        """Resuelve el archivo de audio real que se va a transcribir.

        Prefiere el WAV ya extraído en el proyecto; luego el original si es
        audio; si no, extrae con FFmpeg.
        """
        extracted_wav = project_dir / "audio" / "audio.wav"
        try:
            if extracted_wav.exists() and extracted_wav.stat().st_size > 0:
                return extracted_wav
        except OSError:
            pass
        return _extract_audio_if_needed(source_path, project_dir)

    def transcribe_project(self, project_dir: Path) -> TranscriptionResult:
        project_dir = Path(project_dir)
        project = _load_project(project_dir)

        # Lock exclusivo antes de decidir cualquier cosa: evita que dos
        # procesos decidan regenerar a la vez.
        lock = self._acquire_lock(project.project_id)
        lock_path = self._lock_path(project.project_id)
        try:
            task = self._get_task(project.project_id)
            task = self._reconcile_running_task(task)

            audio_path = self._resolve_audio_for_transcription(
                Path(project.source_path), project_dir
            )
            audio_hash = compute_audio_sha256(audio_path)
            current_fingerprint = build_fingerprint(
                audio_sha256=audio_hash,
                config=self.config,
                project_id=project.project_id,
                source_asset_id=project.source_asset_id,
                duration_seconds=project.duration_seconds,
            )

            reused = self._decide_reuse(task, project, current_fingerprint)
            if reused is not None:
                return reused

            # Marcar running (tras la decisión de reutilización).
            task.attempt_count = task.attempt_count + 1
            task.status = TaskStatus.RUNNING
            task.started_at = _now_iso()
            task.finished_at = None
            task.error_code = None
            task.error_message = None
            self.store.save_task(task)

            try:
                result = self.transcriber.transcribe_audio(
                    audio_path,
                    project_id=project.project_id,
                    source_asset_id=project.source_asset_id,
                    duration_seconds=project.duration_seconds,
                )

                # Diferenciación explícita:
                # - inferencia completa con 0 segmentos: válido (audio sin voz).
                # - inferencia no completada: resultado parcial -> error visible.
                if not getattr(result, "inference_completed", False):
                    raise TranscriptionInferenceError(
                        "La inferencia de transcripción no se completó; no se "
                        "persiste un resultado parcial."
                    )

                # Validar contra contexto (clamp ligero por redondeo)
                dur_src = float(project.duration_seconds)
                adjusted = []
                for seg in result.segments:
                    if seg.end_seconds > dur_src + 0.01:  # tolerancia 10ms
                        seg.end_seconds = dur_src
                    if seg.start_seconds < 0:
                        seg.start_seconds = 0.0
                    if seg.end_seconds <= seg.start_seconds:
                        seg.end_seconds = min(seg.start_seconds + 0.01, dur_src)
                    adjusted.append(seg)
                result.segments = adjusted
                # revalidar
                ctx2 = ProjectContext.from_project(project, result.segments)
                for seg in result.segments:
                    ctx2.validate_segment(seg)

                # Adjuntar huella de identidad de esta ejecución.
                result.fingerprint = current_fingerprint

                # Orden de persistencia: artefacto primero, succeeded después.
                out_path = self.store.save_transcription(result)
                task.status = TaskStatus.SUCCEEDED
                task.finished_at = _now_iso()
                task.error_code = None
                task.error_message = None
                task.artifact_paths = [
                    str(out_path),
                    str(self.store.task_json_path(project.project_id)),
                ]
                self.store.save_task(task)
                return result
            except Exception as exc:
                task.status = TaskStatus.FAILED
                task.finished_at = _now_iso()
                task.error_code = type(exc).__name__
                task.error_message = str(exc)[:500]
                try:
                    self.store.save_task(task)
                except Exception as save_exc:
                    # No enmascarar el error original con un fallo de guardado.
                    logger.error(
                        "No se pudo persistir el estado fallido de la tarea: %s",
                        save_exc,
                    )
                raise
        finally:
            self._release_lock(lock_path, lock["token"])
