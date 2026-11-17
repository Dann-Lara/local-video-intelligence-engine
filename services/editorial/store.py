"""Persistencia y ciclo de vida de los artefactos editoriales (Fase 3).

Replica el patron de `services/transcription/store.py`:
- escritura atomica (tmp + os.replace),
- validacion estructural al leer (un artefacto corrupto nunca se confunde
  con uno reutilizable),
- el orden de persistencia lo controla el orquestador: candidates.json
  primero, task succeeded despues.

Artefactos: projects/<id>/editorial/candidates.json y task_editorial.json.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Optional

from schemas.contracts import Task
from schemas.editorial import EditorialRun
from services.ingestion.ingest import (
    default_project_root,
    _looks_like_uuid,
)
from services.ingestion.errors import (
    ProjectDirectoryConflictError,
    ProjectWriteError,
)

from .errors import EditorialPersistenceError
from .fingerprint import IDENTITY_FIELDS

EDITORIAL_DIR = "editorial"
CANDIDATES_JSON = "candidates.json"
TASK_JSON = "task_editorial.json"
DIAGNOSTICS_JSON = "diagnostics.json"

DIAGNOSTIC_SCHEMA_VERSION = "1.0"

# Limite de tamano para la respuesta cruda persistida por ventana. La
# transcripcion puede ser sensible (local-first): se guarda solo el contenido
# ya extraido por el cliente, nunca cabeceras ni credenciales. Si la respuesta
# supera el limite se trunca de forma determinista y se marca como truncada.
MAX_RAW_RESPONSE_CHARS = 20000


def _identity_wellformed(identity: Any) -> bool:
    if not isinstance(identity, dict):
        return False
    for field_name in IDENTITY_FIELDS:
        if field_name not in identity:
            return False
        if identity[field_name] is None:
            return False
    return True


def _structural_issues(run: EditorialRun) -> list:
    issues: list = []
    if not run.project_id:
        issues.append("project_id vacio")
    if not run.source_asset_id:
        issues.append("source_asset_id vacio")
    if not run.created_at:
        issues.append("created_at vacio")
    if run.status != "succeeded":
        issues.append(f"status inesperado en artefacto persistido: {run.status!r}")
    for index, window in enumerate(run.windows):
        if window.status != "succeeded":
            issues.append(f"ventana {index} no exitosa ({window.status!r})")
    for index, candidate in enumerate(run.candidates):
        prefix = f"candidate[{index}]"
        try:
            duration = float(candidate.duration_seconds)
        except (TypeError, ValueError):
            issues.append(f"{prefix}: duration_seconds no numerico")
            continue
        if not math.isfinite(duration) or duration <= 0:
            issues.append(f"{prefix}: duration_seconds invalido ({duration})")
        expected = float(candidate.proposal.end_seconds) - float(
            candidate.proposal.start_seconds
        )
        if abs(duration - expected) > 1e-3:
            issues.append(f"{prefix}: duracion incoherente")
        if not candidate.evidence_text:
            issues.append(f"{prefix}: evidence_text vacio")
        if candidate.proposal.end_seconds > run.duration_seconds + 0.01:
            issues.append(f"{prefix}: clip excede la duracion de la fuente")
    return issues


class EditorialStore:
    """Gestor de persistencia de la etapa editorial."""

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = Path(project_root) if project_root else default_project_root()

    def _ensure_project_dir(self, project_id: str) -> Path:
        if not _looks_like_uuid(project_id):
            raise ProjectWriteError(
                f"project_id invalido: {project_id}", path=str(self.project_root)
            )
        project_dir = self.project_root / project_id
        if not project_dir.exists():
            raise ProjectDirectoryConflictError(
                f"Directorio de proyecto no existe: {project_dir}", path=str(project_dir)
            )
        if not project_dir.is_dir():
            raise ProjectDirectoryConflictError(
                f"Ruta de proyecto no es directorio: {project_dir}", path=str(project_dir)
            )
        return project_dir

    def editorial_dir(self, project_id: str) -> Path:
        return self._ensure_project_dir(project_id) / EDITORIAL_DIR

    def candidates_json_path(self, project_id: str) -> Path:
        return self.editorial_dir(project_id) / CANDIDATES_JSON

    def task_json_path(self, project_id: str) -> Path:
        return self.editorial_dir(project_id) / TASK_JSON

    def diagnostics_json_path(self, project_id: str) -> Path:
        return self.editorial_dir(project_id) / DIAGNOSTICS_JSON

    def save_diagnostics(self, project_id: str, diagnostics: Dict[str, Any]) -> Path:
        """Persiste el diagnostico por ventana de una ejecucion editorial.

        Es un artefacto observacional separado de `candidates.json`: no altera
        ningun criterio de aceptacion. La escritura es atomica (mismo patron
        que el resto). Se conserva el `run_created_at` para poder distinguir
        ejecuciones; si ya existe un diagnostico de la MISMA ejecucion se
        reemplaza, y si es de una ejecucion distinta se preserva bajo una clave
        historica acotada (sin acumulacion indefinida).
        """
        path = self.diagnostics_json_path(project_id)
        existing: Dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                existing = {}
        payload = self._merge_diagnostics(existing, diagnostics)
        try:
            self._atomic_write_json(path, payload)
        except EditorialPersistenceError:
            raise
        except Exception as exc:
            raise EditorialPersistenceError(
                f"Error al guardar diagnostics.json: {exc}", path=str(path)
            ) from exc
        return path

    @staticmethod
    def _merge_diagnostics(
        existing: Dict[str, Any], incoming: Dict[str, Any]
    ) -> Dict[str, Any]:
        run_id = str(incoming.get("run_created_at", ""))
        history = existing.get("history", [])
        if not isinstance(history, list):
            history = []
        prev_run = str(existing.get("run_created_at", ""))
        if prev_run and prev_run != run_id:
            previous_windows = existing.get("windows", [])
            history = [
                {"run_created_at": prev_run, "windows": previous_windows}
            ] + history
            # Acotado: se conservan los diagnosticos mas recientes.
            history = history[:5]
        merged = dict(incoming)
        merged["diagnostic_schema_version"] = DIAGNOSTIC_SCHEMA_VERSION
        merged["history"] = history
        return merged

    def load_diagnostics(self, project_id: str) -> Optional[Dict[str, Any]]:
        path = self.diagnostics_json_path(project_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise EditorialPersistenceError(
                f"diagnostics.json corrupto (JSON invalido): {exc}", path=str(path)
            ) from exc
        if not isinstance(data, dict):
            raise EditorialPersistenceError(
                "diagnostics.json no es un objeto", path=str(path)
            )
        return data

    def truncate_raw_response(self, raw: str) -> Dict[str, Any]:
        """Empaqueta la respuesta cruda aplicando el limite de tamano.

        Devuelve `{raw_response, raw_response_truncated, raw_response_chars}`.
        Nunca marca como completa una respuesta truncada.
        """
        if not isinstance(raw, str):
            raw = "" if raw is None else str(raw)
        original_chars = len(raw)
        truncated = original_chars > MAX_RAW_RESPONSE_CHARS
        text = raw[:MAX_RAW_RESPONSE_CHARS] if truncated else raw
        return {
            "raw_response": text,
            "raw_response_truncated": truncated,
            "raw_response_chars": original_chars,
        }

    def _atomic_write_json(self, target: Path, data: Dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = target.parent / f".tmp-{os.urandom(4).hex()}"
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / target.name
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp_path, target)
        except Exception as exc:
            try:
                if tmp_dir.exists():
                    for f in tmp_dir.iterdir():
                        try:
                            f.unlink()
                        except Exception:
                            pass
                    tmp_dir.rmdir()
            except Exception:
                pass
            raise EditorialPersistenceError(
                f"No se pudo escribir {target}: {exc}", path=str(target)
            ) from exc

    def save_run(self, run: EditorialRun) -> Path:
        path = self.candidates_json_path(run.project_id)
        try:
            self._atomic_write_json(path, run.to_dict())
        except EditorialPersistenceError:
            raise
        except Exception as exc:
            raise EditorialPersistenceError(
                f"Error al guardar candidates.json: {exc}", path=str(path)
            ) from exc
        return path

    def load_run(self, project_id: str) -> Optional[EditorialRun]:
        path = self.candidates_json_path(project_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise EditorialPersistenceError(
                f"candidates.json corrupto (JSON invalido): {exc}", path=str(path)
            ) from exc
        try:
            run = EditorialRun.from_dict(data)
        except Exception as exc:
            raise EditorialPersistenceError(
                f"candidates.json corrupto (estructura invalida): {exc}", path=str(path)
            ) from exc
        issues = _structural_issues(run)
        if issues:
            raise EditorialPersistenceError(
                "candidates.json no supera validacion estructural: "
                + "; ".join(issues[:10]),
                path=str(path),
            )
        return run

    def has_valid_run(self, project_id: str) -> bool:
        try:
            run = self.load_run(project_id)
        except EditorialPersistenceError:
            return False
        if run is None:
            return False
        if _structural_issues(run):
            return False
        if not _identity_wellformed(run.identity):
            return False
        return True

    def save_task(self, task: Task) -> Path:
        path = self.task_json_path(task.project_id)
        try:
            self._atomic_write_json(path, task.to_dict())
        except EditorialPersistenceError:
            raise
        except Exception as exc:
            raise EditorialPersistenceError(
                f"Error al guardar tarea editorial: {exc}", path=str(path)
            ) from exc
        return path

    def load_task(self, project_id: str) -> Optional[Task]:
        path = self.task_json_path(project_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Task.from_dict(data)
        except Exception as exc:
            raise EditorialPersistenceError(
                f"No se pudo cargar la tarea editorial: {exc}", path=str(path)
            ) from exc
