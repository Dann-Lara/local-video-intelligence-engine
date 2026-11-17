"""Etapa de renderizado (Fase 5, primera version minima).

Expone el renderizado de un clip individual a partir de un video fuente y un
rango verificado. No depende de otras etapas mas alla de leer los artefactos
editoriales.
"""

from __future__ import annotations

from .errors import (
    RenderingError,
    RenderingOutputError,
    RenderingSourceError,
    RenderingToolError,
    RenderingValidationError,
)
from .renderer import (
    DEFAULT_FFMPEG,
    DEFAULT_FFPROBE,
    DEFAULT_TIMEOUT,
    SHORTS_HEIGHT,
    SHORTS_WIDTH,
    probe,
    render_clip,
    render_vertical,
)
from .subtitles import (
    Cue,
    build_ass,
    build_cues,
    render_with_subtitles,
    segment_to_relative,
    write_ass,
)

__all__ = [
    "DEFAULT_FFMPEG",
    "DEFAULT_FFPROBE",
    "DEFAULT_TIMEOUT",
    "Cue",
    "RenderingError",
    "RenderingOutputError",
    "RenderingSourceError",
    "RenderingToolError",
    "RenderingValidationError",
    "SHORTS_HEIGHT",
    "SHORTS_WIDTH",
    "build_ass",
    "build_cues",
    "probe",
    "render_clip",
    "render_vertical",
    "render_with_subtitles",
    "segment_to_relative",
    "write_ass",
]
