"""Huella de identidad de una ejecucion editorial (Fase 3).

Replica el patron de `services/transcription/fingerprint.py`: una ejecucion
editorial persistida solo puede reutilizarse si su huella coincide con la
calculada ahora. La huella cubre:

- la transcripcion realmente analizada (digest canonico de sus segmentos),
- la identidad del proyecto y del activo fuente,
- el motor y version del contrato editorial,
- la configuracion efectiva del analisis,
- la version del prompt.

`model_id` es parte de la identidad, pero su valor por defecto es un
placeholder (`modelo-local-de-ejemplo`): en tanto no se ejecute LM Studio de
forma autorizada no puede confirmarse el modelo real. Cambiarlo invalida.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Optional

from schemas.contracts import TranscriptSegment

from .config import EditorialConfig

EDITORIAL_FINGERPRINT_VERSION = "1.0"
EDITORIAL_SCHEMA_VERSION = "1.0"
EDITORIAL_SERVICE_VERSION = "1.0"
ENGINE_NAME = "lmstudio"

IDENTITY_FIELDS = (
    "editorial_fingerprint_version",
    "editorial_schema_version",
    "service_version",
    "engine",
    "prompt_version",
    "transcription_digest",
    "config_hash",
    "model_id",
    "project_id",
    "source_asset_id",
    "duration_seconds",
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_transcription_digest(segments: Iterable[TranscriptSegment]) -> str:
    """SHA-256 canonico del contenido de la transcripcion analizada."""
    payload = [
        {
            "segment_id": seg.segment_id,
            "start_seconds": round(float(seg.start_seconds), 6),
            "end_seconds": round(float(seg.end_seconds), 6),
            "text": seg.text,
        }
        for seg in segments
    ]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_config_hash(config: EditorialConfig) -> str:
    """SHA-256 de la configuracion efectiva del analisis editorial."""
    return hashlib.sha256(
        _canonical_json(config.to_effective_dict()).encode("utf-8")
    ).hexdigest()


def build_fingerprint(
    *,
    transcription_digest: str,
    config: EditorialConfig,
    prompt_version: str,
    project_id: str,
    source_asset_id: str,
    duration_seconds: float,
) -> Dict[str, Any]:
    if not transcription_digest or not isinstance(transcription_digest, str):
        raise ValueError("transcription_digest requerido")
    if not prompt_version:
        raise ValueError("prompt_version requerido")
    if not project_id:
        raise ValueError("project_id requerido para la huella")
    if not source_asset_id:
        raise ValueError("source_asset_id requerido para la huella")
    duration = float(duration_seconds)
    if duration <= 0:
        raise ValueError(f"duration_seconds debe ser > 0, recibido {duration_seconds}")
    return {
        "editorial_fingerprint_version": EDITORIAL_FINGERPRINT_VERSION,
        "editorial_schema_version": EDITORIAL_SCHEMA_VERSION,
        "service_version": EDITORIAL_SERVICE_VERSION,
        "engine": ENGINE_NAME,
        "prompt_version": prompt_version,
        "transcription_digest": transcription_digest,
        "config_hash": compute_config_hash(config),
        "model_id": config.model_id,
        "project_id": project_id,
        "source_asset_id": source_asset_id,
        "duration_seconds": round(duration, 6),
    }


def _values_match(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


def fingerprint_matches(
    stored: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]]
) -> bool:
    if not isinstance(stored, dict) or not isinstance(current, dict):
        return False
    for field_name in IDENTITY_FIELDS:
        if field_name not in stored or field_name not in current:
            return False
        if not _values_match(stored[field_name], current[field_name]):
            return False
    return True


def fingerprint_diff(
    stored: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]]
) -> list:
    diffs: list = []
    if not isinstance(stored, dict):
        return ["<huella ausente o invalida>"]
    if not isinstance(current, dict):
        return ["<huella actual invalida>"]
    for field_name in IDENTITY_FIELDS:
        if field_name not in stored:
            diffs.append(f"{field_name}: ausente en resultado persistido")
        elif field_name not in current:
            diffs.append(f"{field_name}: ausente en huella actual")
        elif not _values_match(stored[field_name], current[field_name]):
            diffs.append(
                f"{field_name}: {stored[field_name]!r} != {current[field_name]!r}"
            )
    return diffs
