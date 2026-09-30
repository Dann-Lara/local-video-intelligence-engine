"""Ejecucion segura de binarios externos (blueprint secciones 5 y 29).

Regla de seguridad de este modulo: NUNCA `shell=True`.

La ruta del proyecto contiene un espacio y un acento (U+00B4):

    C:\\Users\\SPARTAN PC\\Documents\\dann´s projects\\clipping

Pasar argumentos por un shell en Windows es la causa clasica de fallos
intermitentes con ffmpeg y Node. Con argv lists, Python aplica el escapado
correcto y no hay superficie de inyeccion.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

# Variables que fuerzan UTF-8 en procesos hijos. Sin esto, el acento de la
# ruta puede llegar como '?' al proceso hijo.
UTF8_ENV = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "LC_ALL": "C.UTF-8",
}


class BinaryNotFoundError(RuntimeError):
    """El binario requerido no esta en PATH."""


class CommandFailedError(RuntimeError):
    """El proceso devolvio codigo distinto de 0."""

    def __init__(self, argv: Sequence[str], returncode: int, stderr: str):
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        super().__init__(
            f"Comando fallo (codigo {returncode}): {argv[0]}\n"
            f"argumentos: {argv[1:]}\n"
            f"stderr:\n{tail}"
        )


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    argv: list[str]

    def json(self) -> Any:
        """Parsea stdout como JSON. Falla de forma explicita si no lo es."""
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"La salida de {self.argv[0]} no es JSON valido: {exc}\n"
                f"stdout (primeros 500): {self.stdout[:500]}"
            ) from exc


def which(binary: str) -> str | None:
    return shutil.which(binary)


def require(binary: str) -> str:
    path = which(binary)
    if path is None:
        raise BinaryNotFoundError(
            f"'{binary}' no esta en PATH. Instalalo o ajustalo en config/default.yaml."
        )
    return path


def build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(UTF8_ENV)
    if extra:
        env.update(extra)
    return env


def run(
    argv: Sequence[str],
    *,
    check: bool = True,
    timeout: float | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Ejecuta un comando con argv list (sin shell) y captura stdout/stderr."""
    if not argv:
        raise ValueError("argv vacio")
    completed = subprocess.run(  # noqa: S603 - argv list, shell=False explicito
        [str(a) for a in argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        env=build_env(env),
        shell=False,
    )
    result = CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        argv=[str(a) for a in argv],
    )
    if check and result.returncode != 0:
        raise CommandFailedError(result.argv, result.returncode, result.stderr)
    return result
