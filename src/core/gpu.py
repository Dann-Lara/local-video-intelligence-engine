"""Lease de GPU (blueprint seccion 67 y el orden de las fases 3 y 4).

El problema concreto de esta maquina: RTX 3070 con 8 GB. Whisper
`large-v3-turbo` en int8_float16 y Gemma 3 4B con KV cache q8_0 no caben a la
vez. En este equipo la VRAM libre oscila entre 0.7 y 6.1 GB segun lo que haya
abierto, asi que ejecutar transcripcion y analisis en paralelo falla de forma
intermitente, que es la peor forma de fallar.

Este modulo hace que "no a la vez" sea una garantia del codigo y no una
disciplina. No es un paralelismo: es exclusion mutua. Por eso se llama `gpu.py`
y no `concurrency.py`; si alguna vez hace falta paralelismo real, es otro
modulo.

Ademas del lock hay una segunda salvaguarda: `vram_report()`. Un lease
serializado sigue sin Bastar si algo externo (un juego, el navegador) ya se ha
comido la VRAM. Sin esa comprobacion, el fallo apareceria como un error opaco de
CTranslate2 en mitad de la transcripcion, que es mucho mas dificil de leer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, TypeVar

from src.media import ffmpeg

_GPU_LOCK = threading.RLock()
_GPU_OWNER: str | None = None

# Margen que se exige por encima de lo que la fase necesita. Sin margen, un
# modulo se carga justo en el limite y falla de forma intermitente.
DEFAULT_VRAM_HEADROOM_GB = 1.5


class GpuUnavailableError(RuntimeError):
    """No hay VRAM suficiente para la fase solicitada."""


@dataclass(frozen=True)
class VramReport:
    total_mb: int | None
    free_mb: int | None
    used_mb: int | None
    raw: str

    @property
    def total_gb(self) -> float | None:
        return self.total_mb / 1024 if self.total_mb else None

    @property
    def free_gb(self) -> float | None:
        return self.free_mb / 1024 if self.free_mb else None

    def summary(self) -> str:
        if self.total_mb is None or self.free_mb is None:
            return "VRAM desconocida (nvidia-smi no disponible)"
        return (
            f"{self.total_gb:.1f} GB totales, {self.free_gb:.1f} GB libres "
            f"({self.used_mb / 1024:.1f} GB en uso)"
        )


def vram_report() -> VramReport:
    """Estado de la GPU. Si nvidia-smi no esta, no es un error."""
    binary = ffmpeg.which("nvidia-smi")
    if binary is None:
        return VramReport(None, None, None, "")

    result = ffmpeg.run(
        [
            binary,
            "--query-gpu=memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ],
        check=False,
    )
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        return VramReport(None, None, None, result.stderr)

    first = raw.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    if len(parts) < 3:
        return VramReport(None, None, None, raw)

    def as_int(value: str) -> int | None:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    total = as_int(parts[0])
    used = as_int(parts[1])
    free = as_int(parts[2])
    if total is not None and free is None and used is not None:
        free = total - used
    return VramReport(total, free, used, raw)


def require_vram(needed_gb: float, *, headroom_gb: float = DEFAULT_VRAM_HEADROOM_GB) -> VramReport:
    """Falla pronto si no hay VRAM, en vez de a mitad de la transcripcion."""
    report = vram_report()
    if report.free_gb is None:
        return report
    if report.free_gb < needed_gb + headroom_gb:
        raise GpuUnavailableError(
            f"la fase necesita {needed_gb:.1f} GB de VRAM y solo hay "
            f"{report.free_gb:.1f} GB libres (margen exigido: {headroom_gb:.1f} GB). "
            "Cierra el navegador o los juegos, o baja el tamano de contexto de LM Studio. "
            "Usa 'device: cpu' en config para transcribir en CPU."
        )
    return report


@contextmanager
def gpu_lease(name: str, *, needed_gb: float | None = None) -> Iterator[VramReport]:
    """Toma la GPU en exclusiva durante el bloque.

    Uso previsto:

        with gpu_lease("transcribing", needed_gb=2.0):
            transcribe(audio)

        with gpu_lease("content_analysis", needed_gb=4.0):
            build_content_map(transcript)

    Las fases 3 y 4 del blueprint no pueden solaparse. Esto lo hace imposible
    por construccion, en lugar de confiar en que nadie las lance en paralelo.
    """
    global _GPU_OWNER

    with _GPU_LOCK:
        if _GPU_OWNER is not None and _GPU_OWNER != name:
            raise GpuUnavailableError(
                f"la GPU ya esta ocupada por '{_GPU_OWNER}'; "
                f"'{name}' no puede empezar hasta que termine. "
                "Whisper y Gemma no caben juntos en 8 GB."
            )
        _GPU_OWNER = name

    try:
        if needed_gb is not None:
            yield require_vram(needed_gb)
        else:
            yield vram_report()
    finally:
        with _GPU_LOCK:
            _GPU_OWNER = None


def current_owner() -> str | None:
    return _GPU_OWNER


T = TypeVar("T")


def run_exclusive(phases: Sequence[tuple[str, float | None, Callable[[], T]]]) -> list[T]:
    """Ejecuta fases con la GPU en exclusiva, una detras de otra.

    `phases` es una secuencia de (nombre, vram_necesaria, funcion). Es la forma
    de expresar "no todo a la vez" de forma explicita en el orquestador: el
    orden lo fija esta llamada y la exclusion mutua la aplica `gpu_lease`.

    Devuelve los resultados en el mismo orden que las fases.
    """
    results: list[T] = []
    for name, needed, action in phases:
        with gpu_lease(name, needed_gb=needed):
            results.append(action())
    return results
