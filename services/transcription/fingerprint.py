"""Huella de identidad de una transcripción (corrección de idempotencia).

Una transcripción persistida solo puede reutilizarse si su huella coincide
exactamente con la huella calculada en la ejecución actual. La huella cubre:

- el audio realmente transcrito (SHA-256 del archivo de audio),
- la identidad del proyecto y del activo fuente,
- el motor y la versión del contrato de resultados,
- la configuración efectiva del transcriptor (modelo, device, compute
  type, idioma, VAD, beam size, device_index, hilos, workers, etc.),
- la duración declarada de la fuente.

La serialización de la huella es canónica (JSON con claves ordenadas y
separadores fijos) para que la misma información produzca siempre el mismo
SHA-256. No se incluyen fechas, rutas absolutas ni otros valores variables
entre ejecuciones.

Política de artefactos legacy: si un resultado persistido no tiene huella o
le faltan campos, NUNCA se acepta por inferencia; se regenera.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .config import TranscriptionConfig

FINGERPRINT_VERSION = "1.0"
RESULT_SCHEMA_VERSION = "1.1"
ENGINE_NAME = "faster-whisper"
TRANSCRIPTION_SERVICE_VERSION = "1.0"

_HASH_CHUNK = 1024 * 1024

# Campos de identidad que deben existir y coincidir para reutilizar.
IDENTITY_FIELDS = (
    "fingerprint_version",
    "result_schema_version",
    "service_version",
    "engine",
    "audio_hash",
    "config_hash",
    "model_size_or_path",
    "device",
    "device_index",
    "compute_type",
    "cpu_threads",
    "num_workers",
    "local_files_only",
    "beam_size",
    "vad_filter",
    "language",
    "download_root",
    "project_id",
    "source_asset_id",
    "duration_seconds",
)


def compute_audio_sha256(audio_path: Path, chunk_size: int = _HASH_CHUNK) -> str:
    """SHA-256 del contenido del archivo de audio real transcrito."""
    path = Path(audio_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Audio no existe o no es archivo: {path}")
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def compute_config_hash(config: TranscriptionConfig) -> str:
    """SHA-256 de la configuración efectiva del transcriptor."""
    effective = {
        "model_size_or_path": config.model_size_or_path,
        "device": config.device,
        "device_index": int(config.device_index),
        "compute_type": config.compute_type,
        "cpu_threads": int(config.cpu_threads),
        "num_workers": int(config.num_workers),
        "local_files_only": bool(config.local_files_only),
        "beam_size": int(config.beam_size),
        "vad_filter": bool(config.vad_filter),
        "language": config.language,
        "download_root": config.download_root,
    }
    return hashlib.sha256(_canonical_json(effective).encode("utf-8")).hexdigest()


def build_fingerprint(
    *,
    audio_sha256: str,
    config: TranscriptionConfig,
    project_id: str,
    source_asset_id: str,
    duration_seconds: float,
) -> Dict[str, Any]:
    """Construye la huella de identidad de una transcripción.

    `audio_sha256` debe ser el hash del archivo de audio REALMENTE transcrito
    (no se infiere desde el identificador del activo).
    """
    if not audio_sha256 or not isinstance(audio_sha256, str):
        raise ValueError("audio_sha256 requerido (hash del audio transcrito)")
    if not project_id:
        raise ValueError("project_id requerido para la huella")
    if not source_asset_id:
        raise ValueError("source_asset_id requerido para la huella")
    duration = float(duration_seconds)
    if duration <= 0:
        raise ValueError(f"duration_seconds debe ser > 0, recibido {duration_seconds}")
    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "service_version": TRANSCRIPTION_SERVICE_VERSION,
        "engine": ENGINE_NAME,
        "audio_hash": audio_sha256,
        "config_hash": compute_config_hash(config),
        "model_size_or_path": config.model_size_or_path,
        "device": config.device,
        "device_index": int(config.device_index),
        "compute_type": config.compute_type,
        "cpu_threads": int(config.cpu_threads),
        "num_workers": int(config.num_workers),
        "local_files_only": bool(config.local_files_only),
        "beam_size": int(config.beam_size),
        "vad_filter": bool(config.vad_filter),
        "language": config.language,
        "download_root": config.download_root,
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
    """Compara la huella persistida con la de la ejecución actual.

    Devuelve False si falta cualquiera de las dos, si `stored` no es un
    dict, o si falta o difiere algún campo de identidad (incluidos los
    artefactos legacy sin huella: nunca se aceptan por inferencia).
    """
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
    """Lista de campos de identidad que faltan o difieren (para diagnóstico)."""
    diffs: list = []
    if not isinstance(stored, dict):
        return ["<huella ausente o inválida>"]
    if not isinstance(current, dict):
        return ["<huella actual inválida>"]
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
