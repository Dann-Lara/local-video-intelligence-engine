"""Mecanismo mínimo para preparar rutas de DLL CUDA antes de cargar bibliotecas nativas.

La prueba controlada demostró que anteponer al PATH del proceso los directorios
`bin` de los paquetes NVIDIA instalados bajo `site-packages/nvidia/*` permite
resolver `cublas64_12.dll` para CTranslate2/faster-whisper. Esta inicialización
solo afecta al proceso actual y no modifica variables de entorno persistentes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

_initialized = False


def _iter_nvidia_bin_dirs() -> Iterable[Path]:
    """Itera directorios `bin` de paquetes NVIDIA bajo site-packages/nvidia."""
    try:
        base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    except Exception:
        return

    if not base.exists():
        return

    for root, dirs, files in os.walk(base):
        root_path = Path(root)
        if root_path.name == "bin" and root_path.is_dir():
            yield root_path


def _is_in_path(path: str, path_env: str) -> bool:
    sep = os.pathsep
    paths = [p.strip() for p in path_env.split(sep)]
    return path in paths


def ensure_cuda_dll_paths() -> None:
    """Anteponer directorios bin de NVIDIA al PATH si existen (idempotente)."""
    global _initialized
    if _initialized:
        return

    path_env = os.environ.get("PATH", "")
    additions: list[str] = []
    for bin_dir in _iter_nvidia_bin_dirs():
        try:
            bin_str = str(bin_dir)
        except Exception:
            continue
        if bin_str and not _is_in_path(bin_str, path_env):
            additions.append(bin_str)

    if additions:
        os.environ["PATH"] = os.pathsep.join(additions) + os.pathsep + path_env

    _initialized = True


# Inicializar automáticamente al importar este módulo.
ensure_cuda_dll_paths()
