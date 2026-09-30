"""Logging estructurado (blueprint seccion 57).

Regla del blueprint: cada evento es un objeto JSON, no una linea de texto.
`print` queda prohibido como mecanismo principal.

Formato de una linea en `logs/pipeline.jsonl`:

    {"timestamp": "2026-09-28T10:00:00.000Z", "level": "INFO",
     "job_id": "job_001", "stage": "transcribing",
     "message": "Transcripcion completa", "duration_seconds": 12.4}

El modulo NO se llama `logging.py` a proposito: un fichero con ese nombre dentro
de un paquete es una trampa de sombreado para cualquiera que anada `src/core` al
path en lugar de importar `src.core.x`.

El contexto (`job_id`, `stage`, ...) se liga con `contextvars` y no se pasa a
mano en cada llamada. Es facil olvidarlo, y se pierde el rastro justo en el
mensaje que hacia falta para reconstruir un fallo.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

LOGGER_NAME = "lvi"

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "lvi_log_context", default={}
)

# Atributos que manages logging ya pone en el record y que no deben repetirse
# como campos propios del evento.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def current_context() -> dict[str, Any]:
    return dict(_context.get())


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Liga job_id/stage (u otros campos) a todos los logs dentro del bloque.

    El contexto se restaura al salir, incluso si hay excepcion, para que un
    fallo no deje el resto del proceso etiquetado con el stage equivocado.
    """
    merged = {**_context.get(), **{k: v for k, v in fields.items() if v is not None}}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


class JsonFormatter(logging.Formatter):
    """Serializa cada registro como una linea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "message": record.getMessage(),
        }

        payload.update(current_context())

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    """Para consola: un evento por linea, legible sin abrir un JSON."""

    def format(self, record: logging.LogRecord) -> str:
        context = current_context()
        parts = [
            f"{record.levelname:<7}",
            datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S"),
        ]
        for key in ("job_id", "stage"):
            if key in context:
                parts.append(f"{key}={context[key]}")
        parts.append(record.getMessage())
        return " ".join(parts)


def _log_file_path(config: dict[str, Any]) -> Path:
    from src.core import config as config_module

    return config_module.resolve_relative(
        config, "logging.file", "logs/pipeline.jsonl"
    )


def setup(
    config: dict[str, Any] | None = None,
    *,
    log_file: Path | None = None,
    level: str | None = None,
    console_level: str | None = None,
    to_file: bool = True,
) -> logging.Logger:
    """Configura el logger `lvi`. Es idempotente: no duplica handlers."""
    config = config or {}
    settings = config.get("logging") if isinstance(config.get("logging"), dict) else {}

    resolved_level = (level or settings.get("level", "INFO")).upper()
    resolved_console = (console_level or settings.get("console_level", "WARNING")).upper()
    as_json = settings.get("format", "json") == "json"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolved_level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(resolved_console)
    console.setFormatter(PlainFormatter())
    logger.addHandler(console)

    if to_file:
        if log_file is None:
            log_file = _log_file_path(config)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(JsonFormatter() if as_json else PlainFormatter())
        logger.addHandler(file_handler)

    return logger


def get_logger(suffix: str | None = None) -> logging.Logger:
    """`get_logger("media.ffmpeg")` devuelve `lvi.media.ffmpeg`."""
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)
