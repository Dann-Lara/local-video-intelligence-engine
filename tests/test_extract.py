"""Extraccion de audio (secciones 12 y 32).

El requisito es literal y no negociable: 16 kHz mono PCM. Whisper no
re-muestrea y multicanal solo gasta VRAM, asi que un audio de 48 kHz stereo que
llega al modelo da transcripciones peores de forma silenciosa.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media import extract, ffprobe  # noqa: E402


def test_extraction_produces_whisper_ready_audio(portrait_video: Path, tmp_path: Path):
    out = tmp_path / "audio.wav"
    info = extract.extract_audio(portrait_video, out)

    assert out.is_file()
    assert out.suffix == ".wav"
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.codec == "pcm_s16le"
    assert info.is_whisper_ready
    assert info.size_bytes > 0
    assert "listo para Whisper" in info.summary()


def test_stereo_source_is_downmixed_to_mono(stereo_video: Path, tmp_path: Path):
    """48 kHz stereo -> 16 kHz mono. Si esto falla, Whisper recibe otra cosa."""
    source = ffprobe.probe(stereo_video)
    assert source.audio.channels == 2, "el fixture deberia ser stereo"

    out = tmp_path / "mono.wav"
    info = extract.extract_audio(stereo_video, out)
    assert info.channels == 1
    assert info.sample_rate == 16000


def test_extraction_preserves_duration(portrait_video: Path, tmp_path: Path):
    source = ffprobe.probe(portrait_video)
    out = tmp_path / "audio.wav"
    info = extract.extract_audio(portrait_video, out)
    assert info.duration == pytest.approx(source.duration, abs=0.3)


def test_extraction_is_idempotent(portrait_video: Path, tmp_path: Path):
    """Sin `overwrite`, un audio ya valido no se vuelve a extraer (seccion 51)."""
    out = tmp_path / "audio.wav"
    first = extract.extract_audio(portrait_video, out)
    stamp = out.stat().st_mtime_ns

    second = extract.extract_audio(portrait_video, out)
    assert second == first
    assert out.stat().st_mtime_ns == stamp, "el fichero no deberia haberse reescrito"


def test_overwrite_rewrites_audio(portrait_video: Path, tmp_path: Path):
    out = tmp_path / "audio.wav"
    first = extract.extract_audio(portrait_video, out)
    again = extract.extract_audio(portrait_video, out, overwrite=True)
    assert again.path == first.path
    assert again.is_whisper_ready


def test_extraction_creates_parent_directory(portrait_video: Path, tmp_path: Path):
    out = tmp_path / "anidado" / "mas" / "audio.wav"
    assert extract.extract_audio(portrait_video, out).is_whisper_ready


def test_silent_source_raises(silent_video: Path, tmp_path: Path):
    out = tmp_path / "audio.wav"
    with pytest.raises(extract.ExtractionError, match="no tiene stream de audio"):
        extract.extract_audio(silent_video, out)
    assert not out.exists(), "no deberia quedar un WAV vacio en disco"


def test_missing_source_raises(tmp_path: Path):
    with pytest.raises(extract.ExtractionError, match="no existe el origen"):
        extract.extract_audio(tmp_path / "no-existe.mp4", tmp_path / "audio.wav")


def test_unreadable_source_raises(not_a_video: Path, tmp_path: Path):
    """Un fichero corrupto debe fallar como ExtraccionError, no como
    CommandFailedError de ffprobe (ver `probe_raw`)."""
    with pytest.raises(extract.ExtractionError):
        extract.extract_audio(not_a_video, tmp_path / "audio.wav")


def test_corrupt_existing_audio_is_reextracted(portrait_video: Path, tmp_path: Path):
    """Un WAV de 0 bytes no vale: se rehace en vez de devolverlo como bueno."""
    out = tmp_path / "audio.wav"
    out.write_bytes(b"")
    info = extract.extract_audio(portrait_video, out)
    assert info.is_whisper_ready
    assert out.stat().st_size > 0


def test_settings_come_from_config(portrait_video: Path, tmp_path: Path, config):
    """`whisper.audio_sample_rate` gobierna la extraccion (seccion 12)."""
    tweaked = {**config, "whisper": {**config["whisper"], "audio_sample_rate": 22050}}
    out = tmp_path / "audio.wav"
    info = extract.extract_audio(portrait_video, out, tweaked)
    assert info.sample_rate == 22050
    assert not info.is_whisper_ready, "22050 no es lo que espera Whisper"


# --- miniatura ---


def test_thumbnail_is_extracted(portrait_video: Path, tmp_path: Path):
    out = tmp_path / "thumb.jpg"
    result = extract.extract_thumbnail(portrait_video, out, at_seconds=1.0)
    assert result is not None
    assert out.is_file()
    assert out.stat().st_size > 0


def test_thumbnail_failure_is_not_fatal(not_a_video: Path, tmp_path: Path):
    """Una miniatura ausente no debe tumbar la ingesta."""
    assert extract.extract_thumbnail(not_a_video, tmp_path / "thumb.jpg") is None
