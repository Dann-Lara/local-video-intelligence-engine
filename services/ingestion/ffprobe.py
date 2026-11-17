"""Invocacion segura de FFprobe (Etapa A).

Decisiones de seguridad (AGENTS.md 9):

- `subprocess.run` con **lista de argumentos** y `shell=False`: los
  nombres de archivo con espacios, acentos o caracteres especiales se
  pasan como un unico elemento de la lista, sin interpolarlos en un
  comando de shell.
- `stdin=DEVNULL` para que el proceso no se quede esperando entrada.
- Tiempo de espera configurable; al expirar se lanza
  `FFprobeTimeoutError`.
- Se captura codigo de salida, stdout y stderr con contexto.

Este modulo solo ejecuta y devuelve el JSON crudo; la interpretacion de
los metadatos vive en `metadata.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, Sequence, Tuple

from .errors import (
    FFprobeFailedError,
    FFprobeNotFoundError,
    FFprobeTimeoutError,
    IngestionError,
    MetadataInvalidError,
)

DEFAULT_FFPROBE_EXECUTABLE = "ffprobe"
DEFAULT_TIMEOUT_SECONDS = 30.0

# Argumentos usados para leer un archivo completo de metadatos.
PROBE_ARGS = ("-v", "error", "-print_format", "json", "-show_format", "-show_streams")


def resolve_ffprobe_executable(explicit: Any = None) -> str:
    """Prioridad: argumento explicito > variable de entorno > PATH."""
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    from_env = os.environ.get("LVS_FFPROBE_PATH", "")
    if isinstance(from_env, str) and from_env.strip():
        return from_env.strip()
    return DEFAULT_FFPROBE_EXECUTABLE


def resolve_timeout(explicit: Any = None) -> float:
    """Prioridad: argumento explicito > variable de entorno > 30 s."""
    if explicit is not None:
        return _to_timeout(explicit, "argumento 'timeout'")
    from_env = os.environ.get("LVS_FFPROBE_TIMEOUT", "")
    if isinstance(from_env, str) and from_env.strip():
        return _to_timeout(from_env.strip(), "variable LVS_FFPROBE_TIMEOUT")
    return DEFAULT_TIMEOUT_SECONDS


def _to_timeout(value: Any, origin: str) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise IngestionError(
            f"Tiempo de espera invalido en {origin}: {value!r}; se esperaba un numero de segundos"
        ) from exc
    if not timeout > 0:
        raise IngestionError(
            f"Tiempo de espera invalido en {origin}: {timeout}; debe ser mayor que cero"
        )
    return timeout


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


class FFprobeRunner:
    """Ejecuta FFprobe como proceso externo.

    `run` devuelve (returncode, stdout, stderr) y solo lanza errores de
    entorno (ejecutable ausente, timeout, error del sistema operativo).
    El codigo de salida distinto de cero lo interpreta el llamador.
    """

    def __init__(self, executable: Any = None, timeout: Any = None) -> None:
        self.executable = resolve_ffprobe_executable(executable)
        self.timeout = resolve_timeout(timeout)

    def run(self, args: Sequence[str]) -> Tuple[int, str, str]:
        command = [self.executable, *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=self.timeout,
                shell=False,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise FFprobeNotFoundError(
                f"FFprobe no disponible: no se pudo ejecutar {self.executable!r}. "
                "Instale FFmpeg/FFprobe o defina LVS_FFPROBE_PATH.",
                details={"executable": self.executable, "command": list(command)},
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise FFprobeTimeoutError(
                f"FFprobe supero el tiempo de espera de {self.timeout} segundos",
                details={
                    "timeout_seconds": self.timeout,
                    "command": list(command),
                    "stdout_parcial": _decode(getattr(exc, "stdout", None))[:500],
                    "stderr_parcial": _decode(getattr(exc, "stderr", None))[:500],
                },
            ) from exc
        except OSError as exc:
            raise FFprobeFailedError(
                f"No se pudo ejecutar FFprobe: {exc}",
                details={"command": list(command)},
            ) from exc
        return completed.returncode, _decode(completed.stdout), _decode(completed.stderr)


def probe_media(runner: Any, source_path: Any) -> Dict[str, Any]:
    """Obtiene el JSON de metadatos de un archivo. Devuelve un dict."""
    args = [*PROBE_ARGS, str(source_path)]
    returncode, stdout, stderr = runner.run(args)
    if returncode != 0:
        raise FFprobeFailedError(
            f"FFprobe fallo al leer el archivo (codigo de salida {returncode}): "
            f"{stderr.strip()[:500] or 'sin mensaje en stderr'}",
            path=source_path,
            details={"returncode": returncode, "stderr": stderr.strip()},
        )
    if not stdout.strip():
        raise MetadataInvalidError(
            "FFprobe no devolvio ningun dato de metadatos", path=source_path
        )
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MetadataInvalidError(
            f"FFprobe devolvio JSON invalido: {exc}", path=source_path,
            details={"vista_previa": stdout[:500]},
        ) from exc
    if not isinstance(data, dict):
        raise MetadataInvalidError(
            f"FFprobe devolvio {type(data).__name__} en vez de un objeto JSON",
            path=source_path,
        )
    return data


_VERSION_RE = re.compile(r"ffprobe\s+version\s+(\S+)")


def ffprobe_version(runner: Any) -> str:
    """Devuelve la version de FFprobe para `tool_versions` (AGENTS.md 9)."""
    returncode, stdout, stderr = runner.run(["-version"])
    if returncode != 0:
        raise FFprobeFailedError(
            f"FFprobe no informo su version (codigo de salida {returncode}): "
            f"{stderr.strip()[:300] or 'sin mensaje en stderr'}",
            details={"returncode": returncode, "stderr": stderr.strip()},
        )
    text = stdout.strip() or stderr.strip()
    if not text:
        raise FFprobeFailedError("FFprobe no devolvio informacion de version")
    first_line = text.splitlines()[0].strip()
    match = _VERSION_RE.search(first_line)
    return match.group(1) if match else first_line
