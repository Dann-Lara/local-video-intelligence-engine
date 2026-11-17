"""Orquestador de la etapa editorial (Fase 3).

Flujo: transcripcion valida -> ventanas -> modelo (LM Studio) por ventana ->
parseo/validacion -> deduplicacion/ordenamiento -> candidates.json + task.

Garantias (mismas que Fase 2):

- Reutilizacion: una ejecucion editorial persistida solo se reutiliza si la
  tarea esta succeeded, el artefacto es valido y la huella coincide.
- Lock exclusivo (`editorial.lock`) con pid + host + token.
- Reconciliacion de tareas `running` abandonadas.
- Orden de persistencia: candidates.json primero, task succeeded despues.
- Falla de CUALQUIER ventana => fallo del run completo; no se escribe
  candidatos y la tarea queda `failed` (nunca se presenta un resultado
  parcial como completo).
- Cero candidatos tras analisis exitoso es valido (`succeeded`, lista vacia).
- `review_status` siempre `pending`: la etapa nunca aprueba nada.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas.contracts import Project, Task, TaskStatus, Stage
from schemas.validation import ProjectContext
from services.ingestion.errors import ProjectDirectoryConflictError
from services.transcription.orchestrator import _load_project, _pid_alive
from services.transcription.store import TranscriptionStore

from .client import LMStudioClient
from .config import EditorialConfig
from .errors import (
    EditorialInferenceError,
    EditorialPersistenceError,
    EditorialTaskStateError,
    EditorialTranscriptionError,
)
from .fingerprint import (
    build_fingerprint,
    compute_transcription_digest,
    fingerprint_diff,
    fingerprint_matches,
)
from .parse import parse_model_response
from .prompt import (
    PROMPT_VERSION,
    build_repair_user_prompt,
    build_system_prompt,
    build_window_user_prompt,
)
from .selection import (
    RENDER_SAFE_AD_CLASSIFICATIONS,
    apply_total_limit,
    content_render_blockers,
    deduplicate,
    is_content_eligible,
    is_render_eligible,
    rank_candidates,
)
from .store import EditorialStore
from .windows import build_windows, index_segments

logger = logging.getLogger(__name__)

LOCK_FILENAME = "editorial.lock"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EditorialOrchestrator:
    """Orquestador de la etapa editorial."""

    def __init__(
        self,
        project_root: Optional[Path] = None,
        config: Optional[EditorialConfig] = None,
        client: Optional[Any] = None,
    ) -> None:
        self.store = EditorialStore(project_root)
        self.transcription_store = TranscriptionStore(project_root)
        self.config = config or EditorialConfig()
        self.client = client or LMStudioClient(
            base_url=self.config.base_url,
            model_id=self.config.model_id,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.request_timeout,
        )

    # ------------------------------------------------------------------
    # Tarea y lock
    # ------------------------------------------------------------------

    def _get_task(self, project_id: str) -> Task:
        task = self.store.load_task(project_id)
        if task is None:
            task = Task(
                task_id=str(uuid.uuid4()),
                project_id=project_id,
                stage=Stage.EDITORIAL,
                status=TaskStatus.PENDING,
                attempt_count=0,
            )
        return task

    def _lock_path(self, project_id: str) -> Path:
        return self.store.editorial_dir(project_id) / LOCK_FILENAME

    def _read_lock(self, lock_path: Path) -> Optional[Dict[str, Any]]:
        import json

        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _acquire_lock(self, project_id: str) -> Dict[str, Any]:
        import json
        import os
        import socket

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
                    raise EditorialTaskStateError(
                        "Lock editorial presente pero ilegible; no se puede "
                        f"determinar si hay una ejecucion activa: {lock_path}"
                    )
                if existing.get("hostname") != socket.gethostname():
                    raise EditorialTaskStateError(
                        f"Lock editorial creado por otro host "
                        f"({existing.get('hostname')!r}): {lock_path}"
                    )
                try:
                    existing_pid = int(existing.get("pid", -1))
                except (TypeError, ValueError):
                    existing_pid = -1
                alive = _pid_alive(existing_pid)
                if alive is True:
                    raise EditorialTaskStateError(
                        f"Otra ejecucion editorial esta activa para el proyecto "
                        f"{project_id} (pid={existing_pid}). Lock: {lock_path}"
                    )
                if alive is None:
                    raise EditorialTaskStateError(
                        f"No se pudo determinar si la ejecucion anterior "
                        f"(pid={existing_pid}) sigue activa; revision manual: {lock_path}"
                    )
                logger.warning(
                    "Retirando lock editorial obsoleto (pid=%s): %s",
                    existing_pid,
                    lock_path,
                )
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise EditorialTaskStateError(
                        f"No se pudo retirar el lock obsoleto {lock_path}: {exc}"
                    ) from exc
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            return payload
        raise EditorialTaskStateError(
            f"No se pudo adquirir el lock editorial: {lock_path}"
        )

    def _release_lock(self, lock_path: Path, token: str) -> None:
        existing = self._read_lock(lock_path)
        if existing is None:
            return
        if existing.get("token") != token:
            logger.warning("Lock %s pertenece a otro token; no se retira.", lock_path)
            return
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("No se pudo retirar el lock %s: %s", lock_path, exc)

    # ------------------------------------------------------------------
    # Reutilizacion y reconciliacion
    # ------------------------------------------------------------------

    def _reconcile_running_task(self, task: Task) -> Task:
        if task.status != TaskStatus.RUNNING:
            return task
        logger.warning(
            "Tarea editorial %s en running sin ejecucion activa (lock adquirido); "
            "se marca como fallida.",
            task.task_id,
        )
        task.status = TaskStatus.FAILED
        task.finished_at = _now_iso()
        task.error_code = "AbandonedRunningTask"
        task.error_message = (
            "Tarea editorial en running sin ejecucion activa; se marca como "
            "fallida para permitir el reintento."
        )
        self.store.save_task(task)
        return task

    def _load_run_for_reuse(self, project_id: str):
        try:
            return self.store.load_run(project_id)
        except EditorialPersistenceError as exc:
            logger.warning("Run editorial no reutilizable (se repite): %s", exc)
            return None

    def _decide_reuse(
        self, task: Task, project: Project, current_fingerprint: Dict[str, Any]
    ):
        if task.status != TaskStatus.SUCCEEDED:
            return None
        existing = self._load_run_for_reuse(project.project_id)
        if existing is None:
            return None
        if existing.project_id != project.project_id:
            return None
        if existing.source_asset_id != project.source_asset_id:
            return None
        if not fingerprint_matches(existing.identity, current_fingerprint):
            diffs = fingerprint_diff(existing.identity, current_fingerprint)
            logger.warning(
                "Run editorial no reutilizable: huella distinta (%d diferencias): %s",
                len(diffs),
                "; ".join(diffs[:5]),
            )
            return None
        logger.info(
            "Reutilizando analisis editorial existente de %s (huella coincidente).",
            project.project_id,
        )
        return existing

    # ------------------------------------------------------------------
    # Analisis
    # ------------------------------------------------------------------

    def _resolve_transcription(self, project: Project):
        transcription = self.transcription_store.load_transcription(project.project_id)
        if transcription is None:
            raise EditorialTranscriptionError(
                "No existe transcripcion valida para analizar; ejecute primero "
                "la etapa de transcripcion.",
                details={"project_id": project.project_id},
            )
        return transcription

    def _analyze_window(
        self,
        *,
        window,
        segments_by_id,
        project: Project,
        system_prompt: str,
        clip_id_factory,
        diagnostics_sink: Optional[List[Dict[str, Any]]] = None,
    ):
        body_ids = list(window.body_ids)
        user_prompt = build_window_user_prompt(
            window_id=window.window_id,
            body_ids=body_ids,
            context_before_ids=list(window.context_before_ids),
            context_after_ids=list(window.context_after_ids),
            segments_by_id=segments_by_id,
            duration_seconds=float(project.duration_seconds),
            max_candidates=self.config.max_candidates_per_window,
            clip_min_seconds=self.config.clip_min_seconds,
            clip_target_seconds=self.config.clip_target_seconds,
            clip_max_seconds=self.config.clip_max_seconds,
        )

        attempts = 0
        last_error: Optional[Exception] = None
        diagnostics: List[Dict[str, Any]] = []
        while attempts <= self.config.max_repair_attempts:
            attempts += 1
            raw: Optional[str] = None
            try:
                raw = self.client.complete(
                    system_prompt=system_prompt, user_prompt=user_prompt
                )
                candidates, rejected = parse_model_response(
                    raw,
                    source_asset_id=project.source_asset_id,
                    window_id=window.window_id,
                    body_ids=body_ids,
                    segments_by_id=segments_by_id,
                    config=self.config,
                    duration_seconds=float(project.duration_seconds),
                    clip_id_factory=clip_id_factory,
                )
                diag = self._window_diagnostic(
                    window=window,
                    attempt=attempts,
                    raw=raw,
                    parse_ok=True,
                    parse_error=None,
                    rejected=rejected,
                )
                diagnostics.append(diag)
                if diagnostics_sink is not None:
                    diagnostics_sink.append(diag)
                return candidates, rejected, attempts, diagnostics
            except EditorialInferenceError as exc:
                raise
            except Exception as exc:
                last_error = exc
                diag = self._window_diagnostic(
                    window=window,
                    attempt=attempts,
                    raw=raw,
                    parse_ok=False,
                    parse_error=str(exc),
                    rejected=[],
                )
                diagnostics.append(diag)
                if diagnostics_sink is not None:
                    diagnostics_sink.append(diag)
                if attempts > self.config.max_repair_attempts:
                    break
                logger.warning(
                    "Ventana %s: respuesta invalida (intento %d/%d): %s",
                    window.window_id,
                    attempts,
                    self.config.max_repair_attempts + 1,
                    exc,
                )
                user_prompt = build_repair_user_prompt(user_prompt, str(exc))
        raise EditorialInferenceError(
            f"La ventana {window.window_id} no produjo una respuesta valida en "
            f"{attempts} intento(s): {last_error}"
        )

    def _window_diagnostic(
        self,
        *,
        window,
        attempt: int,
        raw: Optional[str],
        parse_ok: bool,
        parse_error: Optional[str],
        rejected: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Registro diagnostico de una ventana/intento (observacional).

        Guarda la respuesta cruda (con limite de tamano) y el resumen de
        rechazos. Nunca se ejecuta una llamada adicional al modelo por esto.
        """
        raw_info: Dict[str, Any] = {
            "raw_response": None,
            "raw_response_truncated": False,
            "raw_response_chars": 0,
        }
        if raw is not None:
            raw_info = self.store.truncate_raw_response(raw)
        reasons: Dict[str, int] = {}
        for item in rejected:
            reason = str(item.get("reason", ""))
            reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "window_id": window.window_id,
            "attempt": int(attempt),
            "prompt_version": PROMPT_VERSION,
            "model_id": self.config.model_id,
            "parse_ok": bool(parse_ok),
            "parse_error": parse_error,
            "rejected_count": len(rejected),
            "rejection_reasons": reasons,
            "rejected": rejected,
            "raw_response": raw_info["raw_response"],
            "raw_response_truncated": raw_info["raw_response_truncated"],
            "raw_response_chars": raw_info["raw_response_chars"],
        }

    def analyze_project(self, project_dir: Path):
        project_dir = Path(project_dir)
        project = _load_project(project_dir)

        lock = self._acquire_lock(project.project_id)
        lock_path = self._lock_path(project.project_id)
        try:
            task = self._get_task(project.project_id)
            task = self._reconcile_running_task(task)

            transcription = self._resolve_transcription(project)
            segments = list(transcription.segments)
            segments_by_id = index_segments(segments)

            transcription_digest = compute_transcription_digest(segments)
            current_fingerprint = build_fingerprint(
                transcription_digest=transcription_digest,
                config=self.config,
                prompt_version=PROMPT_VERSION,
                project_id=project.project_id,
                source_asset_id=project.source_asset_id,
                duration_seconds=project.duration_seconds,
            )

            reused = self._decide_reuse(task, project, current_fingerprint)
            if reused is not None:
                return reused

            task.attempt_count = task.attempt_count + 1
            task.status = TaskStatus.RUNNING
            task.started_at = _now_iso()
            task.finished_at = None
            task.error_code = None
            task.error_message = None
            self.store.save_task(task)

            try:
                run = self._run_analysis(
                    project=project,
                    segments=segments,
                    segments_by_id=segments_by_id,
                    current_fingerprint=current_fingerprint,
                )
                out_path = self.store.save_run(run)
                task.status = TaskStatus.SUCCEEDED
                task.finished_at = _now_iso()
                task.error_code = None
                task.error_message = None
                task.artifact_paths = [
                    str(out_path),
                    str(self.store.task_json_path(project.project_id)),
                ]
                self.store.save_task(task)
                return run
            except Exception as exc:
                task.status = TaskStatus.FAILED
                task.finished_at = _now_iso()
                task.error_code = type(exc).__name__
                task.error_message = str(exc)[:500]
                try:
                    self.store.save_task(task)
                except Exception as save_exc:
                    logger.error(
                        "No se pudo persistir el estado fallido de la tarea editorial: %s",
                        save_exc,
                    )
                raise
        finally:
            self._release_lock(lock_path, lock["token"])

    def _ad_filter_summary(self, candidates: List[Any]) -> Dict[str, Any]:
        """Resumen auditable del filtro anti-anuncios.

        No elimina candidatos: los marca y expone quien puede pasar a render.
        `render_eligible_ids` exige aprobacion humana, por lo que en esta
        etapa (todo `pending`) siempre esta vacio.
        """
        counts: Dict[str, int] = {}
        for candidate in candidates:
            key = candidate.ad_classification
            counts[key] = counts.get(key, 0) + 1
        return {
            "policy": (
                "accept + ad_classification in (none, incidental_mention) + "
                "limites validados; promotional/sponsored_or_integrated/"
                "uncertain no pasan automaticamente a render"
            ),
            "allowed_ad_classifications": list(RENDER_SAFE_AD_CLASSIFICATIONS),
            "classification_counts": counts,
            "content_eligible_ids": [
                c.clip_id for c in candidates if is_content_eligible(c)
            ],
            "render_eligible_ids": [
                c.clip_id for c in candidates if is_render_eligible(c)
            ],
            "blocked": [
                {
                    "clip_id": c.clip_id,
                    "ad_classification": c.ad_classification,
                    "editorial_decision": c.editorial_decision,
                    "review_status": c.proposal.review_status.value,
                    "blockers": content_render_blockers(c),
                }
                for c in candidates
                if content_render_blockers(c)
            ],
        }

    def _run_analysis(
        self,
        *,
        project: Project,
        segments,
        segments_by_id,
        current_fingerprint: Dict[str, Any],
    ):
        from schemas.editorial import EditorialRun, WindowRecord

        windows = build_windows(
            segments,
            target_chars=self.config.window_target_chars,
            hard_max_chars=self.config.window_hard_max_chars,
            overlap_segments=self.config.overlap_segments,
            context_segments=self.config.context_segments,
        )
        system_prompt = build_system_prompt()

        counter = {"n": 0}

        def clip_id_factory() -> str:
            counter["n"] += 1
            return f"clip_{counter['n']:04d}"

        all_candidates: List[Any] = []
        window_records: List[WindowRecord] = []
        total_rejected: List[Dict[str, Any]] = []
        # Sink compartido: se llena ventana a ventana e intento a intento para
        # que el diagnostico quede disponible aunque un run posterior falle.
        window_diagnostics: List[Dict[str, Any]] = []
        run_created_at = _now_iso()

        try:
            for window in windows:
                candidates, rejected, attempts, _ = self._analyze_window(
                    window=window,
                    segments_by_id=segments_by_id,
                    project=project,
                    system_prompt=system_prompt,
                    clip_id_factory=clip_id_factory,
                    diagnostics_sink=window_diagnostics,
                )
                for item in rejected:
                    item.setdefault("window_id", window.window_id)
                    total_rejected.append(item)
                all_candidates.extend(candidates)
                window_records.append(
                    WindowRecord(
                        window_id=window.window_id,
                        first_segment_id=window.first_segment_id,
                        last_segment_id=window.last_segment_id,
                        segment_ids=list(window.body_ids),
                        status="succeeded",
                        attempts=attempts,
                        candidate_count=len(candidates),
                        rejected=rejected,
                    )
                )
        finally:
            # El diagnostico se persiste tanto en exito como en fallo; es
            # observacional y nunca debe alterar el resultado del run.
            self._persist_diagnostics(
                project=project,
                run_created_at=run_created_at,
                window_diagnostics=window_diagnostics,
            )

        # Deduplicacion, ordenamiento y acotado (deterministas).
        deduped, superseded = deduplicate(
            all_candidates,
            overlap_ratio_threshold=self.config.dedupe_overlap_ratio,
            text_similarity_threshold=self.config.dedupe_text_similarity,
        )
        ranked = rank_candidates(deduped)
        limited, over_limit = apply_total_limit(
            ranked, max_total=self.config.max_candidates_total
        )

        # Guardia de contrato: los candidatos deben validar contra el proyecto.
        ctx = ProjectContext.from_project(project, segments)
        for candidate in limited:
            errors = ctx.clip_errors(candidate.proposal)
            if errors:
                raise EditorialInferenceError(
                    "Candidato invalido tras el parseo (invariante rota): "
                    + "; ".join(errors)
                )

        selection = {
            "dedupe": {
                "overlap_ratio_threshold": self.config.dedupe_overlap_ratio,
                "text_similarity_threshold": self.config.dedupe_text_similarity,
            },
            "total_candidates": len(limited),
            "superseded_count": len(superseded),
            "rejected_count": len(total_rejected),
            "max_candidates_total": self.config.max_candidates_total,
            "superseded": superseded,
            "over_limit": over_limit,
            "rejected": total_rejected,
            "ad_filter": self._ad_filter_summary(limited),
        }

        from .fingerprint import ENGINE_NAME

        run = EditorialRun(
            project_id=project.project_id,
            source_asset_id=project.source_asset_id,
            duration_seconds=float(project.duration_seconds),
            identity=current_fingerprint,
            engine={
                "name": ENGINE_NAME,
                "model_id": self.config.model_id,
                "prompt_version": PROMPT_VERSION,
                "base_url": self.config.base_url,
            },
            effective_config=self.config.to_effective_dict(),
            windows=window_records,
            candidates=limited,
            selection=selection,
            status="succeeded",
            created_at=run_created_at,
        )
        return run

    def _persist_diagnostics(
        self,
        *,
        project: Project,
        run_created_at: str,
        window_diagnostics: List[Dict[str, Any]],
    ) -> None:
        """Persiste el diagnostico observacional (nunca lanza)."""
        diagnostics_payload = {
            "project_id": project.project_id,
            "run_created_at": run_created_at,
            "prompt_version": PROMPT_VERSION,
            "model_id": self.config.model_id,
            "windows": window_diagnostics,
        }
        try:
            self.store.save_diagnostics(project.project_id, diagnostics_payload)
        except EditorialPersistenceError as exc:
            logger.error(
                "No se pudo persistir diagnostics.json para %s: %s",
                project.project_id,
                exc,
            )
