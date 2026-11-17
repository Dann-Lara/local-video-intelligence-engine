"""API pública del servicio de transcripción."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import (
    DEFAULT_COMPUTE_TYPE,
    DEFAULT_DEVICE,
    DEFAULT_MODEL_SIZE,
    TranscriptionConfig,
    get_default_model_dir,
    resolve_model_path,
)
from .errors import (
    TranscriptionError,
    TranscriptionInferenceError,
    TranscriptionInvalidAudioError,
    TranscriptionInvalidSegmentsError,
    TranscriptionModelError,
    TranscriptionNotFoundError,
    TranscriptionPersistenceError,
    TranscriptionTaskStateError,
)
from .fingerprint import (
    ENGINE_NAME,
    FINGERPRINT_VERSION,
    RESULT_SCHEMA_VERSION,
    build_fingerprint,
    compute_audio_sha256,
    compute_config_hash,
    fingerprint_diff,
    fingerprint_matches,
)
from .models import TranscriptionResult
from .orchestrator import TranscriptionOrchestrator, _extract_audio_if_needed, _load_project
from .store import (
    TASK_JSON,
    TRANSCRIPTION_DIR,
    TRANSCRIPTION_JSON,
    TranscriptionStore,
)
from .transcriber import Transcriber

__all__ = [
    "DEFAULT_COMPUTE_TYPE",
    "DEFAULT_DEVICE",
    "DEFAULT_MODEL_SIZE",
    "ENGINE_NAME",
    "FINGERPRINT_VERSION",
    "RESULT_SCHEMA_VERSION",
    "TRANSCRIPTION_DIR",
    "TRANSCRIPTION_JSON",
    "TASK_JSON",
    "TranscriptionConfig",
    "TranscriptionError",
    "TranscriptionInferenceError",
    "TranscriptionInvalidAudioError",
    "TranscriptionInvalidSegmentsError",
    "TranscriptionModelError",
    "TranscriptionNotFoundError",
    "TranscriptionOrchestrator",
    "TranscriptionPersistenceError",
    "TranscriptionResult",
    "TranscriptionStore",
    "TranscriptionTaskStateError",
    "Transcriber",
    "build_fingerprint",
    "compute_audio_sha256",
    "compute_config_hash",
    "fingerprint_diff",
    "fingerprint_matches",
    "get_default_model_dir",
    "resolve_model_path",
    "transcribe_project",
]


def transcribe_project(project_dir: Path, config: Optional[TranscriptionConfig] = None) -> TranscriptionResult:
    """Función de conveniencia para transcribir un proyecto."""
    orch = TranscriptionOrchestrator(project_root=None, config=config)
    return orch.transcribe_project(project_dir)
