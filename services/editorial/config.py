"""Configuracion de la etapa editorial (Fase 3).

Siguiendo el patron de `services/transcription/config.py`: un dataclass
inmutable con valores por defecto. Los parametros de ventana y duracion son
INICIALES, no calibrados para un modelo concreto: no se ejecuta LM Studio
para ajustarlos (limitacion documentada en README y en el informe).

Las variables de entorno `LVS_LMSTUDIO_BASE_URL` y `LVS_LMSTUDIO_MODEL` ya
existen en `.env.example` y se reutilizan; no se introduce un formato nuevo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "modelo-local-de-ejemplo"

# Valores iniciales (aproximaciones por caracteres, NO token calibrados).
DEFAULT_WINDOW_TARGET_CHARS = 8000
DEFAULT_WINDOW_HARD_MAX_CHARS = 16000
DEFAULT_OVERLAP_SEGMENTS = 2
DEFAULT_CONTEXT_SEGMENTS = 2

DEFAULT_CLIP_MIN_SECONDS = 15.0
DEFAULT_CLIP_TARGET_SECONDS = 45.0
DEFAULT_CLIP_MAX_SECONDS = 90.0

DEFAULT_MAX_CANDIDATES_PER_WINDOW = 2
DEFAULT_MAX_CANDIDATES_TOTAL = 20
DEFAULT_DEDUPE_OVERLAP_RATIO = 0.5
DEFAULT_DEDUPE_TEXT_SIMILARITY = 0.8
DEFAULT_MAX_REPAIR_ATTEMPTS = 1


def _default_base_url() -> str:
    value = os.environ.get("LVS_LMSTUDIO_BASE_URL")
    return value if value else DEFAULT_BASE_URL


def _default_model() -> str:
    value = os.environ.get("LVS_LMSTUDIO_MODEL")
    return value if value else DEFAULT_MODEL


@dataclass(frozen=True)
class EditorialConfig:
    """Configuracion efectiva de una ejecucion editorial."""

    # Conexion al servidor local (no se usa durante las pruebas).
    base_url: str = field(default_factory=_default_base_url)
    model_id: str = field(default_factory=_default_model)
    temperature: float = 0.2
    max_tokens: int = 2000
    request_timeout: float = 120.0
    max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS

    # Ventanas de analisis.
    window_target_chars: int = DEFAULT_WINDOW_TARGET_CHARS
    window_hard_max_chars: int = DEFAULT_WINDOW_HARD_MAX_CHARS
    overlap_segments: int = DEFAULT_OVERLAP_SEGMENTS
    context_segments: int = DEFAULT_CONTEXT_SEGMENTS

    # Duracion de clips.
    clip_min_seconds: float = DEFAULT_CLIP_MIN_SECONDS
    clip_target_seconds: float = DEFAULT_CLIP_TARGET_SECONDS
    clip_max_seconds: float = DEFAULT_CLIP_MAX_SECONDS
    # Por defecto, una duracion fuera de rango se rechaza; no se acepta en
    # silencio una excepcion.
    allow_duration_exception: bool = False

    # Limites de candidatos (evitan ruido; todo descarte es auditable).
    max_candidates_per_window: int = DEFAULT_MAX_CANDIDATES_PER_WINDOW
    max_candidates_total: int = DEFAULT_MAX_CANDIDATES_TOTAL

    # Deduplicacion.
    dedupe_overlap_ratio: float = DEFAULT_DEDUPE_OVERLAP_RATIO
    dedupe_text_similarity: float = DEFAULT_DEDUPE_TEXT_SIMILARITY

    def __post_init__(self) -> None:
        if not self.base_url or not str(self.base_url).strip():
            raise ValueError("base_url no puede estar vacio")
        if not self.model_id or not str(self.model_id).strip():
            raise ValueError("model_id no puede estar vacio")
        if not 0.0 <= float(self.temperature):
            raise ValueError("temperature debe ser >= 0")
        if int(self.max_tokens) < 1:
            raise ValueError("max_tokens debe ser >= 1")
        if float(self.request_timeout) <= 0:
            raise ValueError("request_timeout debe ser > 0")
        if int(self.max_repair_attempts) < 0:
            raise ValueError("max_repair_attempts no puede ser negativo")
        if int(self.window_target_chars) <= 0:
            raise ValueError("window_target_chars debe ser > 0")
        if int(self.window_hard_max_chars) < int(self.window_target_chars):
            raise ValueError("window_hard_max_chars debe ser >= window_target_chars")
        if int(self.overlap_segments) < 0:
            raise ValueError("overlap_segments no puede ser negativo")
        if int(self.context_segments) < 0:
            raise ValueError("context_segments no puede ser negativo")
        if float(self.clip_min_seconds) <= 0:
            raise ValueError("clip_min_seconds debe ser > 0")
        if float(self.clip_target_seconds) < float(self.clip_min_seconds):
            raise ValueError("clip_target_seconds debe ser >= clip_min_seconds")
        if float(self.clip_max_seconds) < float(self.clip_target_seconds):
            raise ValueError("clip_max_seconds debe ser >= clip_target_seconds")
        if int(self.max_candidates_per_window) < 0:
            raise ValueError("max_candidates_per_window no puede ser negativo")
        if int(self.max_candidates_total) < 0:
            raise ValueError("max_candidates_total no puede ser negativo")
        for name, value in (
            ("dedupe_overlap_ratio", self.dedupe_overlap_ratio),
            ("dedupe_text_similarity", self.dedupe_text_similarity),
        ):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} debe estar entre 0 y 1")

    def to_effective_dict(self) -> dict:
        """Configuracion efectiva serializable (alimenta la huella)."""
        return {
            "base_url": self.base_url,
            "model_id": self.model_id,
            "temperature": float(self.temperature),
            "max_tokens": int(self.max_tokens),
            "request_timeout": float(self.request_timeout),
            "max_repair_attempts": int(self.max_repair_attempts),
            "window_target_chars": int(self.window_target_chars),
            "window_hard_max_chars": int(self.window_hard_max_chars),
            "overlap_segments": int(self.overlap_segments),
            "context_segments": int(self.context_segments),
            "clip_min_seconds": float(self.clip_min_seconds),
            "clip_target_seconds": float(self.clip_target_seconds),
            "clip_max_seconds": float(self.clip_max_seconds),
            "allow_duration_exception": bool(self.allow_duration_exception),
            "max_candidates_per_window": int(self.max_candidates_per_window),
            "max_candidates_total": int(self.max_candidates_total),
            "dedupe_overlap_ratio": float(self.dedupe_overlap_ratio),
            "dedupe_text_similarity": float(self.dedupe_text_similarity),
        }
