"""Configuración mínima para la etapa de transcripción."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_MODEL_SIZE = "large-v3-turbo"
DEFAULT_COMPUTE_TYPE = "float16"
DEFAULT_DEVICE = "cuda"
DEFAULT_CPU_THREADS = 0
DEFAULT_NUM_WORKERS = 1
DEFAULT_LOCAL_FILES_ONLY = True
DEFAULT_BEAM_SIZE = 5
DEFAULT_VAD_FILTER = False
DEFAULT_LANGUAGE = None


@dataclass(frozen=True)
class TranscriptionConfig:
    """Configuración mínima del transcriptor (no destructiva)."""

    model_size_or_path: str = DEFAULT_MODEL_SIZE
    device: str = DEFAULT_DEVICE
    device_index: int = 0
    compute_type: str = DEFAULT_COMPUTE_TYPE
    cpu_threads: int = DEFAULT_CPU_THREADS
    num_workers: int = DEFAULT_NUM_WORKERS
    local_files_only: bool = DEFAULT_LOCAL_FILES_ONLY
    beam_size: int = DEFAULT_BEAM_SIZE
    vad_filter: bool = DEFAULT_VAD_FILTER
    language: Optional[str] = DEFAULT_LANGUAGE
    download_root: Optional[str] = None

    def __post_init__(self) -> None:
        # Solo validación básica para evitar valores claramente inválidos.
        if not self.model_size_or_path:
            raise ValueError("model_size_or_path no puede estar vacío")
        if self.beam_size < 1:
            raise ValueError("beam_size debe ser >= 1")


def resolve_model_path(
    model_size_or_path: str,
    *,
    cache_dir: Optional[str] = None,
) -> str:
    """Resuelve la ruta al modelo. Por defecto usa el snapshot local ya validado."""
    if model_size_or_path and Path(model_size_or_path).is_dir():
        return str(model_size_or_path)
    # Si es un nombre corto, devolvemos tal cual (faster-whisper resolverá
    # desde caché si local_files_only=True). No forzamos descarga.
    return model_size_or_path


def get_default_model_dir() -> str:
    """Ruta por defecto del snapshot large-v3-turbo en caché local."""
    home = os.path.expanduser("~")
    return os.path.join(
        home,
        ".cache",
        "huggingface",
        "hub",
        "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
        "snapshots",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    )
