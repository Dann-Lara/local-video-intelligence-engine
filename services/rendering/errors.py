"""Errores de la etapa de renderizado (Fase 5, minimo).

Siguen el patron de `services/editorial/errors.py`: una jerarquia simple con
contexto, sin propagar errores crudos de `subprocess`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class RenderingError(Exception):
    """Error base de la etapa de renderizado."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class RenderingSourceError(RenderingError):
    """El video fuente no existe, no es legible o no tiene stream esperado."""


class RenderingToolError(RenderingError):
    """FFmpeg o FFprobe no estan disponibles o fallaron."""


class RenderingOutputError(RenderingError):
    """La salida generada no existe, esta vacia o no supera la verificacion."""


class RenderingValidationError(RenderingError):
    """El clip solicitado no es valido contra la fuente (tiempos fuera de rango)."""
