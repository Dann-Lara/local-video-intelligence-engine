"""DLLs de CUDA para ctranslate2 en Windows (blueprint seccion 73, "CUDA").

El problema, con su sintoma exacto:

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

Ocurre con el driver NVIDIA instalado y con la GPU bien visible. No es que falte
la GPU: es que `ctranslate2` busca `cublas64_12.dll` con el cargador de DLL de
Windows, y las librerías no vienen con el driver. Hay dos formas de aportarlas:

1. Instalar el CUDA Toolkit completo (~3 GB, y no hace falta para dos DLLs).
2. Instalar las ruedas `nvidia-cublas-cu12` y `nvidia-cudnn-cu12`, que las
   dejan en `site-packages/nvidia/<paquete>/bin/`.

Se usa la segunda. El blueprint pide verificar CUDA y anade "unicamente
dependencias necesarias"; dos ruedas de CUDA son necesarias, un Toolkit entero
no.

Detalle que hace perder una hora si no se sabe: **`os.add_dll_directory()` no
basta**. Registra el directorio, pero ctranslate2 no lo consulta: solo mira el
PATH del proceso. Hay que ampliar `os.environ["PATH"]`. Y aun asi conviene
llamar tambien a `add_dll_directory`, guardando el handle, porque hay
versiones de ctranslate2 que si lo usan.

Por que `ensure_cuda_dlls()` tiene que ejecutarse ANTES de importar
`faster_whisper`: la importacion de `ctranslate2` es la que fija las
dependencias de la libreria, y si el PATH se amplía despues ya no se
recarga. De ahi que este modulo no importe `faster_whisper` nunca.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Paquetes de site-packages que traen las DLLs de runtime de CUDA.
NVIDIA_PACKAGES = ("cublas", "cudnn", "cuda_nvrtc", "cufft", "curand", "cusparse", "cusolver")

# DLLs imprescindibles para decodificar con faster-whisper en GPU. Si falta
# alguna, ctranslate2 revienta al PRIMER SEGMENTO, no al cargar el modelo.
REQUIRED_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")

# Las dos anteriores cubren el caso normal. Se listan tambien las alternativas
# por numero de version, para no depender de una version exacta de CUDA.
_CUBLAS_ALTERNATIVES = ("cublas64_12.dll", "cublas64_11.dll")
_CUDNN_ALTERNATIVES = ("cudnn64_9.dll", "cudnn64_8.dll")

_ensured = False
_handles: list[Any] = []
_added_paths: list[Path] = []


@dataclass(frozen=True)
class CudaReport:
    """Estado del runtime de CUDA para ctranslate2."""

    dll_paths: list[Path] = field(default_factory=list)
    found: dict[str, list[Path]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """Hay como minimo una variante de cuBLAS y otra de cuDNN."""
        return not self._missing_families()

    def _missing_families(self) -> list[str]:
        missing: list[str] = []
        if not any(self.found.get(name) for name in ("cublas64_12.dll", "cublas64_11.dll")):
            missing.append("cuBLAS")
        if not any(self.found.get(name) for name in ("cudnn64_9.dll", "cudnn64_8.dll")):
            missing.append("cuDNN")
        return missing

    def summary(self) -> str:
        # Se decide por lo ENCONTRADO, no por `dll_paths`. Un informe puede
        # tener DLLs sin que se haya pasado `dll_paths` (por ejemplo, construido
        # a mano en un test), y mirar solo los directorios daria "no hay nada"
        # cuando en realidad falta una sola libreria.
        if not self.found:
            return "sin ruedas nvidia-* en el venv (el runtime de CUDA no esta disponible)"
        if self.is_usable:
            total = sum(len(paths) for paths in self.found.values())
            donde = f" en {len(self.dll_paths)} directorios" if self.dll_paths else ""
            return f"{total} DLLs de CUDA{donde}, cuBLAS y cuDNN presentes"
        return f"faltan {' y '.join(self._missing_families())} en las ruedas nvidia-*"

    def hint(self) -> str:
        if self.is_usable and self.dll_paths:
            return ""
        return (
            "Instala el runtime de CUDA con las ruedas de pip, que es lo que necesita "
            "ctranslate2: uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 "
            "(no hace falta el CUDA Toolkit completo)"
        )


def nvidia_bin_dirs() -> list[Path]:
    """Directorios `site-packages/nvidia/*/bin` que existen de verdad."""
    # `purelib` y `platlib` suelen ser el mismo directorio en un venv normal, y
    # `os.path.normcase` evita contarlos dos veces en Windows.
    roots: list[Path] = []
    seen: set[str] = set()
    for key in ("purelib", "platlib"):
        try:
            candidate = Path(sysconfig.get_paths()[key]) / "nvidia"
        except (KeyError, OSError):
            continue
        marker = os.path.normcase(str(candidate))
        if marker in seen:
            continue
        seen.add(marker)
        roots.append(candidate)

    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for package in sorted(root.iterdir()):
            if not package.is_dir() or package.name not in NVIDIA_PACKAGES:
                continue
            binary = package / "bin"
            if binary.is_dir() and any(binary.glob("*.dll")):
                found.append(binary)
    return found


def _scan(directories: list[Path]) -> tuple[dict[str, list[Path]], list[str]]:
    found: dict[str, list[Path]] = {}
    for directory in directories:
        try:
            entries = list(directory.glob("*.dll"))
        except OSError:
            continue
        for entry in entries:
            found.setdefault(entry.name, []).append(entry)

    missing = [
        name
        for name in REQUIRED_DLLS
        if not any(found.get(alternative) for alternative in _alternatives_for(name))
    ]
    return found, missing


def _alternatives_for(required: str) -> tuple[str, ...]:
    if required.startswith("cublas"):
        return _CUBLAS_ALTERNATIVES
    if required.startswith("cudnn"):
        return _CUDNN_ALTERNATIVES
    return (required,)


def _es_entorno_previa(part: str) -> bool:
    """True si esta entrada del PATH la anadio este modulo en una llamada previa."""
    return any(os.path.normcase(part) == os.path.normcase(str(p)) for p in _added_paths)


def ensure_cuda_dlls(*, force: bool = False) -> CudaReport:
    """Amplia el PATH del proceso con las DLLs de las ruedas nvidia-*.

    Idempotente y segura de llamar varias veces. Debe llamarse antes de importar
    `faster_whisper`.
    """
    global _ensured

    directories = nvidia_bin_dirs()
    if _ensured and not force:
        found, missing = _scan(directories)
        return CudaReport(dll_paths=directories, found=found, missing=missing)

    if directories:
        existing = os.environ.get("PATH", "")
        # Se quitan las entradas que se hubieran añadido en una llamada previa
        # antes de anteponer las nuevas. Sin esto, cada invocacion con `force`
        # deja una copia mas en el PATH, y un proceso que las va concatenando
        # acaba con una PATH de megabytes.
        nuevos = [str(d) for d in directories]
        clave = os.path.normcase
        nuevos_norm = {clave(str(d)) for d in directories}
        previos = [
            parte
            for parte in existing.split(";")
            if parte and clave(parte) not in nuevos_norm and not _es_entorno_previa(parte)
        ]
        os.environ["PATH"] = ";".join(nuevos + previos)

        for directory in directories:
            try:
                # El handle DEBE guardarse: en cuanto se basura, el directorio
                # se deregistra. Guardarlo en una lista de modulo es lo correcto.
                _handles.append(os.add_dll_directory(str(directory)))
                _added_paths.append(directory)
            except (OSError, AttributeError):
                # `add_dll_directory` no existe en Python < 3.8 ni en POSIX.
                # En POSIX el PATH basta (y no hace falta nada).
                pass

    _ensured = True
    found, missing = _scan(directories)
    return CudaReport(dll_paths=directories, found=found, missing=missing)


def preflight(device: str = "cuda") -> CudaReport:
    """Comprueba el runtime sin lanzar. Para el doctor y para los tests."""
    return ensure_cuda_dlls()


def is_windows() -> bool:
    return sys.platform == "win32"


__all__ = [
    "CudaReport",
    "NVIDIA_PACKAGES",
    "REQUIRED_DLLS",
    "ensure_cuda_dlls",
    "is_windows",
    "nvidia_bin_dirs",
    "preflight",
]
