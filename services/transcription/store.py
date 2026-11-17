"""Persistencia y ciclo de vida de transcripciones."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from schemas.contracts import Task, TaskStatus, Stage, ValidationError
from schemas.validation import ProjectContext
from services.ingestion.ingest import (
    PROJECT_JSON,
    default_project_root,
    _ensure_free_space,
    _looks_like_uuid,
)
from services.ingestion.errors import (
    ProjectDirectoryConflictError,
    ProjectWriteError,
)

from .errors import (
    TranscriptionError,
    TranscriptionNotFoundError,
    TranscriptionPersistenceError,
    TranscriptionTaskStateError,
)
from .fingerprint import IDENTITY_FIELDS
from .models import TranscriptionResult


TRANSCRIPTION_DIR = "transcription"
TRANSCRIPTION_JSON = "transcription.json"
TASK_JSON = "task_transcribe.json"

# Tolerancia para límites de segmentos (redondeos de Whisper).
_TIME_TOLERANCE = 0.01


def _fingerprint_wellformed(fingerprint: Any) -> bool:
    """True si la huella es un dict con todos los campos de identidad.

    `language` y `download_root` pueden ser None legítimamente; el resto de
    campos deben existir y no ser None.
    """
    if not isinstance(fingerprint, dict):
        return False
    optional_none = {"language", "download_root"}
    for field_name in IDENTITY_FIELDS:
        if field_name not in fingerprint:
            return False
        if fingerprint[field_name] is None and field_name not in optional_none:
            return False
    return True


def _structural_issues(result: TranscriptionResult) -> list:
    """Problemas estructurales de un resultado ya parseado.

    Lista vacía significa resultado estructuralmente válido. La finalización
    de la inferencia (`inference_completed`) es obligatoria: un resultado sin
    ese marcador es incompleto. Cero segmentos es válido SOLO si la inferencia
    se completó (audio sin voz): no todo lo que no tiene segmentos es un error.
    """
    issues = []
    if not result.project_id:
        issues.append("project_id vacío")
    if not result.source_asset_id:
        issues.append("source_asset_id vacío")
    try:
        duration = float(result.duration_seconds)
    except (TypeError, ValueError):
        issues.append("duration_seconds no numérico")
        duration = 0.0
    if duration <= 0:
        issues.append(f"duration_seconds debe ser > 0, recibido {result.duration_seconds}")
    if not getattr(result, "inference_completed", False):
        issues.append("inferencia no completada (resultado parcial o interrumpido)")
    if not result.segments:
        # Válido si la inferencia se completó (audio sin voz). Si no se completó,
        # ya está registrado arriba como resultado incompleto.
        return issues
    for i, seg in enumerate(result.segments):
        prefix = f"segment[{i}]"
        try:
            start = float(seg.start_seconds)
            end = float(seg.end_seconds)
        except (TypeError, ValueError):
            issues.append(f"{prefix}: tiempos no numéricos")
            continue
        if start < 0:
            issues.append(f"{prefix}: start_seconds negativo ({start})")
        if end <= start:
            issues.append(f"{prefix}: end_seconds ({end}) <= start_seconds ({start})")
        if duration > 0 and end > duration + _TIME_TOLERANCE:
            issues.append(f"{prefix}: end_seconds ({end}) excede duración ({duration})")
        if not getattr(seg, "segment_id", ""):
            issues.append(f"{prefix}: segment_id vacío")
        if not isinstance(getattr(seg, "text", None), str):
            issues.append(f"{prefix}: text no es string")
        for j, w in enumerate(getattr(seg, "words", []) or []):
            wprefix = f"{prefix}.word[{j}]"
            try:
                wstart = float(w.start_seconds)
                wend = float(w.end_seconds)
            except (TypeError, ValueError):
                issues.append(f"{wprefix}: tiempos no numéricos")
                continue
            if wstart < 0 or wend < wstart:
                issues.append(f"{wprefix}: rango inválido ({wstart}, {wend})")
            if duration > 0 and wend > duration + _TIME_TOLERANCE:
                issues.append(f"{wprefix}: end excede duración")
    return issues


class TranscriptionStore:
    """Gestor de persistencia para resultados de transcripción."""

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = Path(project_root) if project_root else default_project_root()

    def _ensure_project_dir(self, project_id: str) -> Path:
        if not _looks_like_uuid(project_id):
            raise ProjectWriteError(
                f"project_id inválido: {project_id}", path=str(self.project_root)
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

    def transcription_dir(self, project_id: str) -> Path:
        return self._ensure_project_dir(project_id) / TRANSCRIPTION_DIR

    def transcription_json_path(self, project_id: str) -> Path:
        return self.transcription_dir(project_id) / TRANSCRIPTION_JSON

    def task_json_path(self, project_id: str) -> Path:
        return self.transcription_dir(project_id) / TASK_JSON

    def _atomic_write_json(self, target: Path, data: Dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = target.parent / f".tmp-{os.urandom(4).hex()}"
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / target.name
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            # Mover atómicamente
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
            raise TranscriptionPersistenceError(
                f"No se pudo escribir {target}: {exc}", path=str(target)
            ) from exc

    def save_transcription(self, result: TranscriptionResult) -> Path:
        path = self.transcription_json_path(result.project_id)
        try:
            self._atomic_write_json(path, result.to_dict())
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionPersistenceError(
                f"Error al guardar transcripción: {exc}", path=str(path)
            ) from exc
        return path

    def load_transcription(self, project_id: str) -> Optional[TranscriptionResult]:
        """Carga y valida la transcripción persistida.

        - Devuelve None si no existe el archivo.
        - Lanza TranscriptionPersistenceError si el JSON está corrupto o el
          resultado no supera la validación estructural (para que un artefacto
          corrupto o vacío nunca se confunda con uno reutilizable).
        """
        path = self.transcription_json_path(project_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TranscriptionPersistenceError(
                f"transcription.json corrupto (JSON inválido): {exc}", path=str(path)
            ) from exc
        try:
            result = TranscriptionResult.from_dict(data)
        except Exception as exc:
            raise TranscriptionPersistenceError(
                f"transcription.json corrupto (estructura inválida): {exc}", path=str(path)
            ) from exc
        issues = _structural_issues(result)
        if issues:
            raise TranscriptionPersistenceError(
                "transcription.json no supera validación estructural: "
                + "; ".join(issues[:10]),
                path=str(path),
            )
        return result

    def has_valid_transcription(self, project_id: str) -> bool:
        """True solo si existe una transcripción reutilizable.

        Requiere: cargable, estructuralmente válida (incluida la finalización
        de la inferencia) y con huella de identidad bien formada. Un artefacto
        legacy sin huella no es válido para reutilización.
        """
        try:
            res = self.load_transcription(project_id)
        except TranscriptionPersistenceError:
            return False
        if res is None:
            return False
        if _structural_issues(res):
            return False
        if not _fingerprint_wellformed(res.fingerprint):
            return False
        return True

    def save_task(self, task: Task) -> Path:
        path = self.task_json_path(task.project_id)
        try:
            self._atomic_write_json(path, task.to_dict())
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionPersistenceError(
                f"Error al guardar tarea: {exc}", path=str(path)
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
            raise TranscriptionPersistenceError(
                f"No se pudo cargar tarea: {exc}", path=str(path)
            ) from exc
