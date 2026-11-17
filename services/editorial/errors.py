"""Errores del servicio editorial (Fase 3).

Misma jerarquia que el resto del proyecto: heredan de `IngestionError` para
que el tratamiento visible de errores (AGENTS.md, principio 10) sea uniforme.
"""

from __future__ import annotations

from services.ingestion.errors import IngestionError


class EditorialError(IngestionError):
    """Error base para la etapa editorial."""


class EditorialConfigError(EditorialError):
    """Configuracion editorial invalida."""


class EditorialTranscriptionError(EditorialError):
    """No existe una transcripcion valida para analizar."""


class EditorialModelUnavailableError(EditorialError):
    """LM Studio no responde o el modelo local no esta disponible."""


class EditorialInvalidResponseError(EditorialError):
    """La respuesta del modelo no es JSON valido o incumple el contrato."""


class EditorialInferenceError(EditorialError):
    """Fallo al procesar una ventana (respuesta incompleta o error del modelo)."""


class EditorialPersistenceError(EditorialError):
    """No se pudieron escribir o leer los artefactos editoriales."""


class EditorialTaskStateError(EditorialError):
    """Estado inconsistente de la tarea editorial."""


class EditorialNotFoundError(EditorialError):
    """No existe un analisis editorial valido para el proyecto solicitado."""
