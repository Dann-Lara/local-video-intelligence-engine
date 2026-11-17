"""Errores del servicio de transcripción (Fase 2)."""

from __future__ import annotations

from services.ingestion.errors import IngestionError


class TranscriptionError(IngestionError):
    """Error base para la etapa de transcripción."""


class TranscriptionModelError(TranscriptionError):
    """No se pudo cargar o utilizar el modelo de transcripción."""


class TranscriptionInferenceError(TranscriptionError):
    """Falló la inferencia con faster-whisper."""


class TranscriptionInvalidAudioError(TranscriptionError):
    """El audio proporcionado no es válido para transcribir."""


class TranscriptionInvalidSegmentsError(TranscriptionError):
    """Los segmentos generados no superan la validación de contratos."""


class TranscriptionPersistenceError(TranscriptionError):
    """No se pudieron escribir los artefactos de transcripción."""


class TranscriptionTaskStateError(TranscriptionError):
    """Estado inconsistente de la tarea de transcripción."""


class TranscriptionNotFoundError(TranscriptionError):
    """No existe una transcripción válida para el proyecto solicitado."""
