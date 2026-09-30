"""Fixtures compartidas.

Lo importante de este fichero: los vídeos de prueba se GENERAN con FFmpeg en
un `tmp_path_factory` de sesión, no se versionan. Un binario de vídeo en el
repositorio pesa, no se puede diffear y además el .gitignore tendría que
mantenerse sincronizado con algo que no aporta nada.

Se generan de verdad (con vídeo y audio reales) en vez de escribir JSON de
ffprobe a mano, porque un fixture de 20 líneas que "simula" la salida de
ffprobe acaba dejando pasar bugs que solo aparecen con un contenedor de
verdad: fps racional, matriz de rotación, duración ausente a nivel de formato.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import config as config_module  # noqa: E402
from src.media import ffmpeg  # noqa: E402

# Videos cortos a proposito: los tests no miran el contenido, miran la
# metadata y la extraccion de audio. 3 s bastan y se ejecutan rapido.
SHORT_SECONDS = 3


def _ffmpeg_or_skip() -> str:
    if ffmpeg.which("ffmpeg") is None or ffmpeg.which("ffprobe") is None:
        pytest.skip("FFmpeg/FFprobe no estan en PATH; se omiten los tests de media")
    return ffmpeg.require("ffmpeg")


def _run(args: list[str]) -> None:
    result = ffmpeg.run(args, check=False)
    if result.returncode != 0:
        pytest.fail(
            f"no se pudo generar el fixture de test:\n{' '.join(args)}\n{result.stderr[-800:]}"
        )


def _video_with_audio(
    destination: Path, *, size: str, rate: str, seconds: int
) -> Path:
    _run(
        [
            _ffmpeg_or_skip(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            "-shortest",
            str(destination),
        ]
    )
    return destination


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Carpeta compartida de los vídeos generados."""
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def portrait_video(media_dir: Path) -> Path:
    """1080x1920, 30 fps, con audio. El caso normal."""
    return _video_with_audio(
        media_dir / "portrait.mp4", size="1080x1920", rate="30", seconds=SHORT_SECONDS
    )


@pytest.fixture(scope="session")
def landscape_video(media_dir: Path) -> Path:
    """1920x1080, 30 fps, con audio."""
    return _video_with_audio(
        media_dir / "landscape.mp4", size="1920x1080", rate="30", seconds=SHORT_SECONDS
    )


@pytest.fixture(scope="session")
def vfr_video(media_dir: Path) -> Path:
    """29.97 fps (30000/1001). El caso que rompe `float()` directo."""
    return _video_with_audio(
        media_dir / "vfr.mp4", size="720x1280", rate="30000/1001", seconds=SHORT_SECONDS
    )


@pytest.fixture(scope="session")
def rotated_video(media_dir: Path) -> Path:
    """Vertical en un contenedor horizontal: se vera girada 90 grados.

    La rotacion NO se puede escribir con `-metadata:s:v:0 rotate=90`: el muxer
    de MP4 lo descarta en silencio y el fichero sale sin girar. Hay que pasar
    por la Display Matrix, con `-display_rotation` en la ENTRADA y `-c copy` en
    la salida. Se hace en dos pasos (grabar, luego remuxar) porque no se puede
    pedir a la vez codificar y fijar la matriz.
    """
    flat = media_dir / "_rot_source.mp4"
    destination = media_dir / "rotated.mp4"
    _run(
        [
            _ffmpeg_or_skip(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            str(flat),
        ]
    )
    _run(
        [
            _ffmpeg_or_skip(), "-y", "-loglevel", "error",
            "-display_rotation", "90",
            "-i", str(flat),
            "-c", "copy",
            str(destination),
        ]
    )
    return destination


@pytest.fixture(scope="session")
def stereo_video(media_dir: Path) -> Path:
    """Audio multicanal: la extraccion tiene que bajar a mono."""
    destination = media_dir / "stereo.mp4"
    _run(
        [
            _ffmpeg_or_skip(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=25:duration={SHORT_SECONDS}",
            "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=48000:duration={SHORT_SECONDS}",
            "-ac", "2",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            str(destination),
        ]
    )
    return destination


@pytest.fixture(scope="session")
def silent_video(media_dir: Path) -> Path:
    """Sin audio. La ingesta debe rechazarlo (seccion 12, paso 6)."""
    destination = media_dir / "silent.mp4"
    _run(
        [
            _ffmpeg_or_skip(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=640x480:rate=25:duration={SHORT_SECONDS}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            str(destination),
        ]
    )
    return destination


@pytest.fixture(scope="session")
def audio_only_file(media_dir: Path) -> Path:
    """Solo audio, sin stream de video."""
    destination = media_dir / "solo_audio.m4a"
    _run(
        [
            _ffmpeg_or_skip(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={SHORT_SECONDS}",
            "-c:a", "aac", str(destination),
        ]
    )
    return destination


@pytest.fixture(scope="session")
def not_a_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fichero con extension de video que no lo es."""
    destination = tmp_path_factory.mktemp("basura") / "falso.mp4"
    destination.write_text("esto no es un mp4, es texto plano\n", encoding="utf-8")
    return destination


# --- voz real para los tests de transcripcion -------------------------------

# Frase usada por SAPI. Se reconoce de forma fiable con `tiny`, que es lo que
# usan los tests: el objetivo es que la voz sea REAL (que el modelo tenga algo que
# transcribir), no que el modelo grande acierte cada palabra.
SAPI_PHRASE = (
    "El motor de clips verticales funciona correctamente. "
    "Cada fragmento se recorta, se subtitula y se exporta a nueve por dieciseis."
)
SAPI_SAMPLE_RATE = 22050


def _sapi_wav(destination: Path, phrase: str = SAPI_PHRASE) -> Path | None:
    """Genera un WAV de voz con el sintetizador de Windows. None si no hay SAPI.

    Se usa `System.Speech` desde PowerShell en vez de `pywin32` a proposito: no
    anade una dependencia al venv solo para los tests (seccion 73), y funciona en
    cualquier Windows sin instalar nada.

    SAPI es lo que permite probar la transcripcion de verdad sin versionar audio
    ni depender de la red. Un WAV con un tono de 440 Hz NO sirve: Whisper lo
    transcribe como vacio y no se puede distinguir "el modelo no funciona" de
    "el audio no tiene voz".
    """
    import subprocess
    import sys as _sys
    import wave

    if _sys.platform != "win32":
        return None
    if "'" in phrase or "$" in phrase or "`" in phrase:
        raise ValueError("la frase no puede contener comillas simples, $ ni acentos graves")

    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{destination}'); "
        f"$s.Speak('{phrase}'); "
        "$s.Dispose();"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False, capture_output=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not destination.is_file():
        return None

    # SAPI escribe RIFF PCM de 16 bits mono, pero a 22.05 kHz, no a los 16 kHz
    # que exige el pipeline. Se remuestrea para que el test cubra de verdad el
    # contrato de `extract.py` y no un audio que el modelo adataria.
    try:
        with wave.open(str(destination), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
    except (OSError, wave.Error):
        return None

    if (channels, width) != (1, 2):
        return None

    normalised = destination.with_name(f"{destination.stem}_16k.wav")
    with wave.open(str(normalised), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(_resample_pcm16(frames, rate, 16000))
    return normalised


def _resample_pcm16(frames: bytes, source_rate: int, target_rate: int) -> bytes:
    """Remuestrea PCM 16 bits con interpolacion lineal.

    Es una aproximacion suficiente para voz sintetica en tests. No se usa
    `scipy` ni `soxr` a proposito: la seccion 73 pide anadir solo las
    dependencias necesarias, y un remuestreo lineal sobre unos segundos de voz
    da algo que Whisper transcribe bien.
    """
    import array

    if source_rate == target_rate:
        return frames

    samples = array.array("h")
    samples.frombytes(frames)
    if not samples:
        return frames

    ratio = source_rate / target_rate
    out_length = max(int(len(samples) / ratio), 1)
    result = array.array("h", bytes(out_length * 2))
    for index in range(out_length):
        position = index * ratio
        left = int(position)
        right = min(left + 1, len(samples) - 1)
        weight = position - left
        result[index] = int(samples[left] * (1 - weight) + samples[right] * weight)
    return result.tobytes()


@pytest.fixture(scope="session")
def speech_wav(media_dir: Path) -> Path:
    """WAV 16 kHz mono con voz real en espanol. Es lo que transcribe Whisper."""
    destination = _sapi_wav(media_dir / "voz.wav")
    if destination is None or not destination.is_file():
        pytest.skip(
            "SAPI no disponible (no es Windows, o falta pywin32). "
            "Los tests de transcripcion con audio real se omiten."
        )
    return destination


@pytest.fixture
def tiny_whisper_config(config: dict[str, Any]) -> dict[str, Any]:
    """Configuracion de Whisper con `tiny` en CPU.

    `tiny` CPU transcribe una frase en ~2 s y no toca la GPU, asi que los tests
    no compiten por la VRAM con LM Studio ni dependen de cuBLAS. El modelo real
    del pipeline (`large-v3-turbo` en CUDA) se ejercita aparte, en un test
    marcado, porque necesita la GPU libre.
    """
    config["whisper"] = {
        **config.get("whisper", {}),
        "model": "tiny",
        "device": "cpu",
        "compute_type": "int8",
        "vram_required_gb": 0.0,
        "beam_size": 1,
    }
    return config


@pytest.fixture
def config(tmp_path: Path) -> dict[str, Any]:
    """Configuracion real, con `work_root` apuntando a un temporal.

    Se usa `config_module.load()` de verdad en vez de un dict a mano: los tests
    tienen que fallar si la forma de la configuracion cambia.
    """
    loaded = config_module.load()
    loaded["paths"] = {**loaded.get("paths", {}), "work_root": str(tmp_path / "work")}
    return loaded


@pytest.fixture
def make_video(tmp_path: Path) -> Callable[..., Path]:
    """Genera un vídeo a medida dentro del temporal del test."""

    def factory(
        name: str = "clip.mp4",
        *,
        size: str = "640x360",
        rate: str = "25",
        seconds: int = 2,
        with_audio: bool = True,
    ) -> Path:
        destination = tmp_path / name
        args = [_ffmpeg_or_skip(), "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={seconds}"]
        if with_audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
        args += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"]
        if with_audio:
            args += ["-c:a", "aac", "-shortest"]
        args += [str(destination)]
        _run(args)
        return destination

    return factory
