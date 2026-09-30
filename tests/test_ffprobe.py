"""Analisis de media con FFprobe (seccion 13).

Mix de tests unitarios (parsers, sin depender de FFmpeg) y de integracion
(FFprobe real sobre videos generados). Los unitarios son los que pinan los
casos raros: fps racional, duracion ausente, campos "N/A" de ffprobe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media import ffprobe  # noqa: E402


# --- parseo de valores (sin FFmpeg) ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30/1", 30.0),
        ("30000/1001", pytest.approx(29.97003, rel=1e-5)),
        ("25", 25.0),
        ("24/1", 24.0),
    ],
)
def test_parse_rate_handles_rational_fps(raw, expected):
    """29.97 llega como '30000/1001'. `float()` directo revienta."""
    assert ffprobe.parse_rate(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "0/0", "N/A", "0", "abc", "-30/1"])
def test_parse_rate_rejects_unusable_values(raw):
    assert ffprobe.parse_rate(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("30", 30), (30, 30), (None, None), ("", None), ("N/A", None), ("x", None), (12.9, 12)],
)
def test_parse_int(raw, expected):
    assert ffprobe.parse_int(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3.5", 3.5), (3, 3.0), (None, None), ("N/A", None), ("abc", None), ("inf", None), ("nan", None)],
)
def test_parse_float(raw, expected):
    assert ffprobe.parse_float(raw) == expected


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [(1920, 1080, "16:9"), (1080, 1920, "9:16"), (1920, 800, "12:5"), (1000, 1000, "1:1")],
)
def test_aspect_ratio_reduces_to_simplest_form(width, height, expected):
    assert ffprobe.aspect_ratio(width, height) == expected


@pytest.mark.parametrize(("width", "height"), [(0, 100), (100, 0), (-1, 5)])
def test_aspect_ratio_rejects_degenerate_sizes(width, height):
    assert ffprobe.aspect_ratio(width, height) is None


# --- rotacion ---


def test_rotation_from_tags():
    """El campo heredado `rotate` viene con el signo invertido."""
    assert ffprobe._rotation_of({"tags": {"rotate": "-90"}}) == 90


def test_rotation_from_tags_is_normalised():
    """El signo se invierte y el resultado se deja en 0..359.

    `-90` -> 90 y `-270` -> 270. Son giros de cuarto de vuelta en sentidos
    opuestos: normalizarlos a un unico valor (por ejemplo "90") perderia la
    direccion, que luego hace falta para elegir el lado del recorte.
    """
    assert ffprobe._rotation_of({"tags": {"rotate": "-90"}}) == 90
    assert ffprobe._rotation_of({"tags": {"rotate": "-270"}}) == 270
    assert ffprobe._rotation_of({"tags": {"rotate": "-180"}}) == 180
    assert ffprobe._rotation_of({"tags": {"rotate": "0"}}) == 0


def test_rotation_from_display_matrix_is_not_negated():
    """La Display Matrix ya viene en la convencion que hay que aplicar.

    Negarla (como hacia una version anterior de este modulo) daria 270 en lugar
    de 90. El recorte saldria del reves.
    """
    assert ffprobe._rotation_of({"side_data_list": [{"rotation": 90}]}) == 90
    assert ffprobe._rotation_of({"side_data_list": [{"rotation": -90}]}) == 270


def test_display_matrix_wins_over_legacy_tag():
    """Si estan los dos, manda la matriz: es la que respeta el contenedor."""
    stream = {"tags": {"rotate": "-180"}, "side_data_list": [{"rotation": 90}]}
    assert ffprobe._rotation_of(stream) == 90


def test_rotation_absent_is_zero():
    assert ffprobe._rotation_of({}) == 0
    assert ffprobe._rotation_of({"tags": {}, "side_data_list": None}) == 0


# --- probes reales ---


def test_probe_reads_portrait_video(portrait_video: Path):
    info = ffprobe.probe(portrait_video)
    assert info.has_video and info.has_audio
    assert info.video.width == 1080
    assert info.video.height == 1920
    assert info.video.is_portrait
    assert info.aspect_ratio == "9:16"
    assert info.video.fps == pytest.approx(30.0, rel=1e-3)
    assert info.duration == pytest.approx(3.0, abs=0.3)
    assert info.size_bytes > 0
    assert info.audio.sample_rate == 48000


def test_probe_reads_landscape_video(landscape_video: Path):
    info = ffprobe.probe(landscape_video)
    assert info.video.width == 1920
    assert not info.video.is_portrait
    assert info.aspect_ratio == "16:9"


def test_probe_parses_fractional_fps(vfr_video: Path):
    """30000/1001. Si aqui se leia mal, todos los timecodes se desvan despues."""
    info = ffprobe.probe(vfr_video)
    assert info.video.fps == pytest.approx(29.97, abs=0.01)


def test_probe_reports_stereo_channels(stereo_video: Path):
    info = ffprobe.probe(stereo_video)
    assert info.audio.channels == 2


def test_probe_handles_rotation(rotated_video: Path):
    """El contenedor es 1920x1080 pero se muestra girado: 1080x1920."""
    info = ffprobe.probe(rotated_video)
    if info.video.rotation == 0:
        pytest.skip("esta build de FFmpeg no escribio la matriz de rotacion")
    assert info.video.rotation in (90, 270)
    assert info.video.is_portrait is False          # lo que dice el contenedor
    assert info.video.is_display_portrait is True   # lo que se ve
    assert info.video.display_width == 1080
    assert info.video.display_height == 1920
    assert info.aspect_ratio == "9:16"


def test_probe_detects_missing_audio(silent_video: Path):
    info = ffprobe.probe(silent_video)
    assert info.has_video
    assert not info.has_audio
    assert info.audio is None
    assert "SIN AUDIO" in info.summary()


def test_probe_detects_audio_only(audio_only_file: Path):
    info = ffprobe.probe(audio_only_file)
    assert info.has_audio
    assert not info.has_video
    assert info.video is None
    assert "sin stream de video" in info.summary()


def test_probe_rejects_audio_only_as_valid_video(audio_only_file: Path):
    ok, reason = ffprobe.is_valid_video(audio_only_file)
    assert not ok
    assert "stream de video" in reason


def test_probe_rejects_silent_video(silent_video: Path):
    ok, reason = ffprobe.is_valid_video(silent_video)
    assert not ok
    assert "no tiene audio" in reason


def test_is_valid_video_accepts_normal_input(portrait_video: Path):
    ok, reason = ffprobe.is_valid_video(portrait_video)
    assert ok
    assert "9:16" in reason


def test_probe_missing_file_raises(tmp_path: Path):
    with pytest.raises(ffprobe.ProbeError, match="no existe"):
        ffprobe.probe(tmp_path / "no-existe.mp4")


def test_probe_non_media_file_raises(not_a_video: Path):
    with pytest.raises(ffprobe.ProbeError):
        ffprobe.probe(not_a_video)


def test_probe_directory_raises(tmp_path: Path):
    with pytest.raises(ffprobe.ProbeError):
        ffprobe.probe(tmp_path)


# --- serializacion ---


def test_to_dict_is_json_serializable(portrait_video: Path):
    import json

    info = ffprobe.probe(portrait_video)
    payload = info.to_dict()
    # `raw` es enorme y no lo necesita nadie aguas arriba.
    assert "raw" not in payload
    assert payload["path"] == str(portrait_video)
    json.dumps(payload)  # no debe lanzar


def test_to_dict_serialises_rotation(rotated_video: Path):
    info = ffprobe.probe(rotated_video)
    payload = info.to_dict()
    assert payload["video"]["rotation"] == info.video.rotation
    assert payload["video"]["width"] == info.video.width
