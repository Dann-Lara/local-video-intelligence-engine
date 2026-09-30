"""Mide el pico de VRAM durante una transcripcion real de voz, no con silencio.

El silencio no sirve: el VAD descarta los tramos silenciosos antes de
decodificar, asi que la memoria de pico nunca llega a reservarse. Con un
silencio de dos minutos, Whisper devuelve 0 segmentos en menos de un segundo y
la lectura de VRAM sale engaosa.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import gpu  # noqa: E402


def build_speech(seconds: int) -> Path:
    """Genera voz real con SAPI (Windows) y la devuelve como WAV."""
    import subprocess
    import tempfile
    import wave

    target = Path(tempfile.gettempdir()) / f"lvi_vram_{seconds}s.wav"
    phrase = (
        "El motor de clips verticales funciona correctamente. "
        "Cada fragmento se recorta, se subtitula y se exporta. "
    )
    repeats = max(1, round(seconds / 12))
    script = phrase * repeats

    powershell = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 1; "
        f"$s.SetOutputToWaveFile('{target}'); "
        f"$s.Speak(\"{script}\"); "
        "$s.Dispose();"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", powershell],
        check=True, capture_output=True, timeout=300,
    )
    with wave.open(str(target)) as handle:
        actual = handle.getnframes() / handle.getframerate()
    print(f"audio de voz: {actual:.1f}s  ({target.name}, {target.stat().st_size / 1e6:.1f} MB)")
    return target


class Peak:
    """Muestrea nvidia-smi en segundo plano y guarda la minima observada."""

    def __init__(self) -> None:
        self.minimum_free_mb: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            report = gpu.vram_report()
            if report.free_mb is not None:
                if self.minimum_free_mb is None or report.free_mb < self.minimum_free_mb:
                    self.minimum_free_mb = report.free_mb
            self._stop.wait(0.1)

    def __enter__(self) -> "Peak":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


def main() -> int:
    from faster_whisper import WhisperModel

    audio = build_speech(120)

    baseline = gpu.vram_report()
    print(f"linea base:      {baseline.summary()}")
    if baseline.free_mb is None:
        print("nvidia-smi no disponible; no se puede medir")
        return 0

    load_started = time.monotonic()
    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
    print(f"modelo cargado:  {time.monotonic() - load_started:.1f}s  {gpu.vram_report().summary()}")

    for word_timestamps in (False, True):
        started = time.monotonic()
        with Peak() as peak:
            segments, info = model.transcribe(
                str(audio),
                language="es",
                beam_size=5,
                word_timestamps=word_timestamps,
                vad_filter=True,
            )
            count = sum(1 for _ in segments)
        elapsed = time.monotonic() - started
        peak_free = peak.minimum_free_mb or 0
        print(
            f"word_timestamps={word_timestamps!s:5}  "
            f"{count:3d} segs  {elapsed:6.1f}s  "
            f"pico libre {peak_free / 1024:.2f} GB  "
            f"=> usa ~{(baseline.free_mb - peak_free) / 1024:.2f} GB"
        )

    print(f"final:           {gpu.vram_report().summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
