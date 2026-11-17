"""API publica del servicio editorial (Fase 3)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .client import LMStudioClient, ModelClient
from .config import (
    DEFAULT_BASE_URL,
    DEFAULT_CLIP_MAX_SECONDS,
    DEFAULT_CLIP_MIN_SECONDS,
    DEFAULT_CLIP_TARGET_SECONDS,
    DEFAULT_MODEL,
    DEFAULT_OVERLAP_SEGMENTS,
    DEFAULT_WINDOW_HARD_MAX_CHARS,
    DEFAULT_WINDOW_TARGET_CHARS,
    EditorialConfig,
)
from .errors import (
    EditorialConfigError,
    EditorialError,
    EditorialInferenceError,
    EditorialInvalidResponseError,
    EditorialModelUnavailableError,
    EditorialNotFoundError,
    EditorialPersistenceError,
    EditorialTaskStateError,
    EditorialTranscriptionError,
)
from .fingerprint import (
    EDITORIAL_FINGERPRINT_VERSION,
    EDITORIAL_SCHEMA_VERSION,
    ENGINE_NAME,
    build_fingerprint,
    compute_config_hash,
    compute_transcription_digest,
    fingerprint_diff,
    fingerprint_matches,
)
from .orchestrator import EditorialOrchestrator, LOCK_FILENAME
from .prompt import PROMPT_VERSION
from .store import (
    CANDIDATES_JSON,
    DIAGNOSTICS_JSON,
    DIAGNOSTIC_SCHEMA_VERSION,
    EDITORIAL_DIR,
    MAX_RAW_RESPONSE_CHARS,
    TASK_JSON,
    EditorialStore,
)
from .windows import Window, build_windows

__all__ = [
    "CANDIDATES_JSON",
    "DIAGNOSTICS_JSON",
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DEFAULT_BASE_URL",
    "DEFAULT_CLIP_MAX_SECONDS",
    "DEFAULT_CLIP_MIN_SECONDS",
    "DEFAULT_CLIP_TARGET_SECONDS",
    "DEFAULT_MODEL",
    "DEFAULT_OVERLAP_SEGMENTS",
    "DEFAULT_WINDOW_HARD_MAX_CHARS",
    "DEFAULT_WINDOW_TARGET_CHARS",
    "EDITORIAL_DIR",
    "EDITORIAL_FINGERPRINT_VERSION",
    "EDITORIAL_SCHEMA_VERSION",
    "ENGINE_NAME",
    "LOCK_FILENAME",
    "MAX_RAW_RESPONSE_CHARS",
    "PROMPT_VERSION",
    "TASK_JSON",
    "EditorialConfig",
    "EditorialConfigError",
    "EditorialError",
    "EditorialInferenceError",
    "EditorialInvalidResponseError",
    "EditorialModelUnavailableError",
    "EditorialNotFoundError",
    "EditorialOrchestrator",
    "EditorialPersistenceError",
    "EditorialStore",
    "EditorialTaskStateError",
    "EditorialTranscriptionError",
    "LMStudioClient",
    "ModelClient",
    "Window",
    "analyze_project",
    "build_fingerprint",
    "build_windows",
    "compute_config_hash",
    "compute_transcription_digest",
    "fingerprint_diff",
    "fingerprint_matches",
]


def analyze_project(
    project_dir: Path,
    config: Optional[EditorialConfig] = None,
    client: Optional[object] = None,
):
    """Funcion de conveniencia para analizar editorialmente un proyecto."""
    orch = EditorialOrchestrator(project_root=None, config=config, client=client)
    return orch.analyze_project(project_dir)
