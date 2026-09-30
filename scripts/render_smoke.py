"""Verificacion reproducible del camino de render con Remotion (Sprint 0.4).

Cierra la tarea 0.4 sin depender de comandos escritos a mano. Hace tres cosas:

1. Genera un video vertical de prueba con FFmpeg.
2. Invoca `npx remotion render` con la composicion `Clip`.
3. Comprueba el resultado de forma objetiva, porque el render no se puede
   inspeccionar a ojo en este entorno:
     - resolucion 1080x1920, 30 fps, duracion esperada, audio presente
     - las capas de texto (titulo y subtitulo) existem de verdad

El punto 3 mide contraste en las regiones donde el componente dibuja. La fuente
de prueba es deliberadamente PLANA: si no lo fuera, el contraste del video
propio enmascararia el de las capas superpuestas y la comprobacion no probaria
nada. Se verifica esa precondition con una region de control.

Uso:
    python scripts/render_smoke.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.media import ffmpeg  # noqa: E402

WIDTH, HEIGHT, FPS, SECONDS = 1080, 1920, 30, 3
COMPOSITION = "Clip"
SOURCE_NAME = "render_smoke_source.mp4"

TITLE_REGION = (150, 450)
SUBTITLE_REGION = (1550, 1750)
CONTROL_REGION = (900, 1100)

PROPS = {
    "videoSrc": SOURCE_NAME,
    "durationInSeconds": SECONDS,
    "fps": FPS,
    "title": "Prueba de render vertical",
    "subtitles": [
        {"text": "Primer subtitulo de prueba", "start": 0, "end": 1.5},
        {"text": "Segundo subtitulo de prueba", "start": 1.5, "end": SECONDS},
    ],
    "fit": "cover",
    "accentColor": "#FFD400",
}

MIN_OVERLAY_STD = 8.0
MIN_OVERLAY_BRIGHT_PX = 500
MAX_CONTROL_STD = 2.0


class SmokeFailure(RuntimeError):
    pass


def build_source(destination: Path) -> None:
    """Video plano con audio. El color plano es lo que hace valida la prueba."""
    ffmpeg.run(
        [
            ffmpeg.require("ffmpeg"),
            "-y",
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"color=c=0x303030:size={WIDTH}x{HEIGHT}:rate={FPS}:duration={SECONDS}",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={SECONDS}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(destination),
        ]
    )


def render(remotion_dir: Path, output: Path, props_path: Path, public_dir: Path) -> None:
    """Invoca el render por la MISMA funcion que usa `main.py render`.

    Antes este script escribia su propio argv con `npx remotion render`, y se
    quedo obsoleto en cuanto el pipeline real anadio `--public-dir`,
    `--browser-executable` y `--color-space bt709`: los tres son obligatorios
    (el master se sirve por HTTP, sin Chrome local Remotion intenta descargar el
    suyo, y sin `--color-space` el resultado sale `yuvj420p`). Reutilizar
    `build_remotion_args` hace que el smoke test se rompa si el render cambia,
    en vez de dar verde con una invocacion que ya no representa nada.
    """
    from src.media import render as render_module

    node = shutil.which("node")
    if node is None:
        raise SmokeFailure("node no esta en PATH (Remotion lo necesita)")

    browser = render_module.resolve_browser_executable()
    if browser is None:
        raise SmokeFailure(
            "no se encontro un Chrome/Edge local: Remotion intentaria descargar el suyo"
        )

    argv = render_module.build_remotion_args(
        node,
        render_module.remotion_entry(remotion_dir),
        component_id=COMPOSITION,
        props_path=props_path,
        output_path=output,
        public_dir=public_dir,
        browser_executable=browser,
        crf=20,
        loglevel="error",
    )
    ffmpeg.run(argv, cwd=remotion_dir, timeout=1800)


def gray_frame(video: Path, seconds: float) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "frame.gray"
        ffmpeg.run(
            [
                ffmpeg.require("ffmpeg"),
                "-y",
                "-loglevel", "error",
                "-ss", str(seconds),
                "-i", str(video),
                "-frames:v", "1",
                "-vf", "format=gray",
                "-f", "rawvideo",
                "-pix_fmt", "gray",
                str(raw),
            ]
        )
        data = np.frombuffer(raw.read_bytes(), dtype=np.uint8)
    expected = WIDTH * HEIGHT
    if data.size != expected:
        raise SmokeFailure(f"frame inesperado: {data.size} bytes, esperado {expected}")
    return data.reshape(HEIGHT, WIDTH)


def probe(video: Path) -> dict:
    result = ffmpeg.run(
        [
            ffmpeg.require("ffprobe"),
            "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video),
        ]
    )
    return result.json()


def check_container(video: Path) -> None:
    data = probe(video)
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None:
        raise SmokeFailure("el render no contiene stream de video")
    if audio_stream is None:
        raise SmokeFailure("el render no conserva el audio del origen")

    if (video_stream.get("width"), video_stream.get("height")) != (WIDTH, HEIGHT):
        raise SmokeFailure(
            f"resolucion {video_stream.get('width')}x{video_stream.get('height')}, "
            f"esperado {WIDTH}x{HEIGHT}"
        )
    if video_stream.get("r_frame_rate") != f"{FPS}/1":
        raise SmokeFailure(f"fps {video_stream.get('r_frame_rate')}, esperado {FPS}/1")
    if video_stream.get("codec_name") != "h264":
        raise SmokeFailure(f"codec {video_stream.get('codec_name')}, esperado h264")

    duration = float(data.get("format", {}).get("duration", 0))
    if not (SECONDS - 0.5 <= duration <= SECONDS + 0.5):
        raise SmokeFailure(f"duracion {duration:.2f}s, esperado ~{SECONDS}s")


def stats(frame: np.ndarray, y0: int, y1: int) -> tuple[float, int]:
    block = frame[y0:y1, :]
    return float(block.std()), int((block > 200).sum())


def check_overlays(video: Path) -> None:
    early = gray_frame(video, 0.5)
    late = gray_frame(video, 2.0)

    control_std, control_bright = stats(early, *CONTROL_REGION)
    if control_std > MAX_CONTROL_STD:
        raise SmokeFailure(
            f"region de control con std={control_std:.2f}; la fuente de prueba no es "
            "plana y la comprobacion de capas no seria concluyente"
        )
    if control_bright != 0:
        raise SmokeFailure("la region de control tiene pixeles brillantes inesperados")

    for label, (y0, y1) in (("titulo", TITLE_REGION), ("subtitulo", SUBTITLE_REGION)):
        std, bright = stats(early, y0, y1)
        print(f"  {label:<10} std={std:6.2f} bright_px={bright:>7d}")
        if std < MIN_OVERLAY_STD or bright < MIN_OVERLAY_BRIGHT_PX:
            raise SmokeFailure(
                f"la capa {label} no se detecta (std={std:.2f}, bright={bright}); "
                "la composicion no esta dibujando texto"
            )

    late_std, _ = stats(late, *SUBTITLE_REGION)
    if late_std < MIN_OVERLAY_STD:
        raise SmokeFailure("el subtitulo desaparece en el segundo tramo")

    early_std, _ = stats(early, *SUBTITLE_REGION)
    if abs(early_std - late_std) < 0.5:
        print("  [aviso] los dos subtitulos tienen contraste practicamente identico")


def main() -> int:
    remotion_dir = ROOT / "remotion"
    if not (remotion_dir / "package.json").exists():
        print("FALLO: remotion/package.json no existe. Instala las dependencias primero.")
        return 1

    public_dir = remotion_dir / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    public_copy = public_dir / SOURCE_NAME

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        output = work / "clip_out.mp4"
        props_path = work / "clip_props.json"
        props_path.write_text(
            json.dumps(PROPS, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        try:
            print(f"1/3 generando fuente de prueba {WIDTH}x{HEIGHT}...")
            build_source(public_copy)

            print(f"2/3 renderizando composicion {COMPOSITION}...")
            render(remotion_dir, output, props_path, public_dir)
            if not output.exists():
                raise SmokeFailure("remotion no produjo archivo de salida")

            print("3/3 verificando resultado:")
            check_container(output)
            print("  contenedor OK: 1080x1920, 30fps, h264, audio conservado")
            check_overlays(output)
        except (SmokeFailure, ffmpeg.CommandFailedError, ffmpeg.BinaryNotFoundError) as exc:
            print(f"\nFALLO: {exc}")
            return 1
        finally:
            public_copy.unlink(missing_ok=True)

    print("\nOK: camino de render con Remotion verificado de extremo a extremo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
