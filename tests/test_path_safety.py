"""Smoke test de la ruta del proyecto (Sprint 0, riesgo C-1).

La ruta contiene un espacio y un acento (U+00B4):

    C:\\Users\\SPARTAN PC\\Documents\\dann´s projects\\clipping

Antes de escribir el pipeline, hay que PROBAR que ffmpeg, ffprobe y la
escritura de JSON funcionan aqui. Si esto falla, el escape hatch es mover
work_root/output_root fuera via .env en vez de pelearse con el escapado.

Estos tests son deliberadamente real: generan video, ejecutan filtergraphs y
verifican el resultado con ffprobe. Si estan verdes, el resto del pipeline
puede asumir que la ruta es utilizable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media import ffmpeg  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SMOKE_DIR = PROJECT_ROOT / "work" / "_smoke"

pytestmark = pytest.mark.skipif(
    ffmpeg.which("ffmpeg") is None or ffmpeg.which("ffprobe") is None,
    reason="ffmpeg/ffprobe no disponibles",
)


@pytest.fixture(scope="module")
def source_video() -> Path:
    """Video 16:9 de 6s con audio, generado localmente."""
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    target = SMOKE_DIR / "fuente 16x9 áéí.mp4"
    if target.is_file():
        return target
    ffmpeg.run(
        [
            ffmpeg.require("ffmpeg"),
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc2=size=1920x1080:rate=30:duration=6",
            "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=48000:duration=6",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(target),
        ],
        timeout=180,
    )
    assert target.is_file()
    return target


# --- prueba basica: el binario corre y ffprobe responde ---


def test_project_path_contains_the_dangerous_characters():
    """Este test solo tiene sentido si la ruta es la que creemos."""
    assert " " in str(PROJECT_ROOT)
    assert "´" in str(PROJECT_ROOT), "la ruta ya no tiene el acento; revisa los tests"


def test_ffmpeg_runs():
    result = ffmpeg.run([ffmpeg.require("ffmpeg"), "-hide_banner", "-version"])
    assert result.returncode == 0
    assert "ffmpeg version" in result.stdout


def test_ffprobe_reads_generated_video(source_video: Path):
    probe = ffmpeg.run(
        [
            ffmpeg.require("ffprobe"),
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(source_video),
        ]
    ).json()

    assert probe["format"]["duration"]
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (1920, 1080)
    assert audio["sample_rate"] == "48000"


# --- la prueba clave: filter_complex con acentos y espacios ---


def test_filter_complex_works_in_accented_path(source_video: Path):
    """crop + scale + fps + loudnorm en una ruta con espacio y acento."""
    target = SMOKE_DIR / "salida vertical 9x16 ñ.mp4"
    if target.is_file():
        target.unlink()

    filter_complex = (
        "[0:v]crop=608:1080:(iw-608)/2:0,scale=1080:1920:flags=lanczos,"
        "setsar=1,fps=30[v];"
        "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )

    ffmpeg.run(
        [
            ffmpeg.require("ffmpeg"),
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source_video),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "h264_nvenc", "-preset", "p4",
            "-c:a", "aac",
            "-t", "3",
            str(target),
        ],
        timeout=300,
    )

    assert target.is_file()
    probe = ffmpeg.run(
        [
            ffmpeg.require("ffprobe"),
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            str(target),
        ]
    ).json()
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)


def test_output_filename_with_accent_is_preserved(source_video: Path):
    target = SMOKE_DIR / "ñandú áéíóú.mp4"
    if target.is_file():
        target.unlink()
    ffmpeg.run(
        [
            ffmpeg.require("ffmpeg"),
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source_video),
            "-t", "1",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(target),
        ],
        timeout=120,
    )
    assert target.is_file()
    assert target.name == "ñandú áéíóú.mp4"


def test_trim_preserves_timestamps(source_video: Path):
    """-ss / -t exactos: el clip debe durar lo pedido (seccion 27)."""
    target = SMOKE_DIR / "trim.mp4"
    ffmpeg.run(
        [
            ffmpeg.require("ffmpeg"),
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "2", "-i", str(source_video),
            "-t", "2",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(target),
        ],
        timeout=120,
    )
    probe = ffmpeg.run(
        [
            ffmpeg.require("ffprobe"),
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            str(target),
        ]
    ).json()
    assert 1.8 <= float(probe["format"]["duration"]) <= 2.2


# --- lectura/escritura de rutas con acentos desde Python ---


def test_json_roundtrip_with_accented_paths():
    from src.core.contracts import enforce
    from src.core import schemas

    document = {
        "schema_version": "1.0",
        "language": "es",
        "duration": 6.0,
        "segments": [{"id": 0, "start": 0.0, "end": 6.0, "text": "Árboles y ñandúes"}],
    }
    enforce("transcript", document)

    path = SMOKE_DIR / "transcripción.json"
    import json

    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["segments"][0]["text"] == "Árboles y ñandúes"
    assert schemas.is_valid("transcript", reloaded)


def test_argv_list_is_not_passed_through_a_shell():
    """Un argumento con metacarácter no debe interpretarse como shell."""
    nasty = SMOKE_DIR / "archivo & echo inyectado.mp4"
    result = ffmpeg.run(
        [ffmpeg.require("ffmpeg"), "-hide_banner", "-f", "lavfi", "-i", "anullsrc", "-t", "0.1", str(nasty)],
        check=False,
        timeout=60,
    )
    # Puede fallar por otros motivos, pero nunca debe crear 'inyectado.mp4'
    assert not (SMOKE_DIR / "inyectado.mp4").exists()
    assert result is not None
