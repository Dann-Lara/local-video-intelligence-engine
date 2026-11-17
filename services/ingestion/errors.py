"""Errores de la etapa de ingesta (Fase 1).

Todos heredan de `IngestionError` para que la CLI, el registro de log y
las pruebas puedan tratarlos de forma uniforme (AGENTS.md 9: capturar
errores de procesos externos, codigos de salida, stderr y rutas).

Nunca deben propagarse `TypeError` ni errores crudos de `subprocess`
desde la capa de servicio: siempre se envuelven con contexto util.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class IngestionError(Exception):
    """Error base de la ingesta. Siempre lleva ruta y contexto."""

    def __init__(
        self,
        message: str,
        *,
        path: Optional[Any] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.message = message
        self.path = str(path) if path is not None else None
        self.details: Dict[str, Any] = dict(details or {})
        full = message
        if self.path:
            full = f"{message} [ruta: {self.path}]"
        super().__init__(full)


class SourceNotFoundError(IngestionError):
    """El archivo fuente no existe."""


class InvalidSourceError(IngestionError):
    """El archivo existe pero no es utilizable.

    Cubre: no es un archivo regular, vacio, ilegible, sin pista de video,
    sin duracion utilizable, duracion no finita o duracion <= 0
    (salvaguarda 6 del plan de la Fase 1).
    """


class FFprobeNotFoundError(IngestionError):
    """El ejecutable de FFprobe no esta disponible."""


class FFprobeTimeoutError(IngestionError):
    """FFprobe supero el tiempo de espera configurado."""


class FFprobeFailedError(IngestionError):
    """FFprobe termino con un codigo de salida distinto de cero."""


class MetadataInvalidError(IngestionError):
    """FFprobe devolvio una estructura que no se puede interpretar."""


class InsufficientSpaceError(IngestionError):
    """No hay espacio libre suficiente para crear los artefactos."""


class ProjectDirectoryConflictError(IngestionError):
    """Se hallo un directorio de proyecto existente o incompleto cuya
    procedencia no se puede demostrar.

    Salvaguarda 4: se detiene, se conservan sus archivos y no se
    elimina ni se sobrescribe nada.
    """


class ProjectWriteError(IngestionError):
    """Fallo al escribir o verificar los artefactos del proyecto.

    Salvaguarda 3: nunca queda un registro incompleto bajo un nombre de
    proyecto valido.
    """
