"""Verificacion visual objetiva de un clip ya renderizado (sin mirar imagenes).

No podemos inspeccionar el MP4 a ojo en este entorno, asi que se mide lo que
la composicion dibuja sobre los pixeles reales del video. Las tres cosas que
importan son:

1. Los subtitulos caen DENTRO del encuadre y no se solapan con nada.
2. El hook se dibuja donde debe y se retira al final.
3. El crop central no se come la mitad de la escena sin querer.

Para (1) y (2) no basta con buscar pixeles brillantes: el video real tambien
los tiene. Lo que se hace es restar el fotograma SIN texto. El master vertical
de la ruta de Remotion es exactamente el mismo clip sin las capas de texto, y
sus timestamps de imagen son los mismos, luego al restarlo queda solo lo que
Remotion dibujo encima. Esa diferencia es la prueba directa.

Uso:
    python tools/check_overlay.py <clip.mp4> <master_sin_texto.mp4> <props.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media import ffmpeg  # noqa: E402

WIDTH, HEIGHT = 1080, 1920


def gray_frame(video: Path, seconds: float) -> np.ndarray:
    """Un fotograma en escala de grises, sin reescalar."""
    with __import__("tempfile").TemporaryDirectory() as tmp:
        raw = Path(tmp) / "f.gray"
        ffmpeg.run(
            [
                ffmpeg.require("ffmpeg"),
                "-y", "-loglevel", "error",
                "-ss", f"{seconds:.3f}",
                "-i", str(video),
                "-frames:v", "1",
                "-vf", "format=gray",
                "-f", "rawvideo", "-pix_fmt", "gray",
                str(raw),
            ]
        )
        data = np.frombuffer(raw.read_bytes(), dtype=np.uint8)
    if data.size != WIDTH * HEIGHT:
        raise SystemExit(f"fotograma inesperado: {data.size} bytes, esperado {WIDTH * HEIGHT}")
    return data.reshape(HEIGHT, WIDTH).astype(np.int16)


def overlay_mask(final: np.ndarray, master: np.ndarray) -> np.ndarray:
    """Pixeles donde Remotion dibujo algo encima del video.

    Umbral de 12: por debajo es ruido de recompresion (el master es CRF 16 y el
    final CRF 20, asi que el recompresion sola mueve pixeles), por encima el
    blanco de un texto sobre el fondo de la caja es de 150 o mas.
    """
    return np.abs(final - master) > 12


def report_band(mask: np.ndarray, y0: int, y1: int, label: str) -> None:
    band = mask[y0:y1]
    rows = np.where(band.any(axis=1))[0]
    cols = np.where(band.any(axis=0))[0]
    if rows.size == 0:
        print(f"  {label:<22} sin pixeles dibujados")
        return
    top, bottom = int(rows[0]) + y0, int(rows[-1]) + y0
    left, right = int(cols[0]), int(cols[-1])
    print(
        f"  {label:<22} y={top:>4}-{bottom:<4} x={left:>4}-{right:<4} "
        f"({bottom - top + 1}px alto, {right - left + 1}px ancho)"
    )


def main() -> int:
    final, master, props_path = (Path(p) for p in sys.argv[1:4])
    props = json.loads(props_path.read_text(encoding="utf-8"))
    cues = props.get("subtitles") or []
    has_title = bool(props.get("title"))

    print(f"clip     {final.name}")
    print(f"capas    {'hook + ' if has_title else ''}{len(cues)} cues")
    print(f"margen   abajo={HEIGHT - 0}px  (el padding del subtitulo son 260px)\n")

    # El hook solo vive en el primer tramo, asi que un instante con cue y sin
    # titulo hay que buscarlo aparte.
    title_window = 1.0
    cue_inside = [c for c in cues if c["start"] > title_window + 0.5]
    cue_first = cue_inside[0] if cue_inside else None
    cue_last = cues[-1] if cues else None

    if cue_first:
        t = (cue_first["start"] + cue_first["end"]) / 2
        mask = overlay_mask(gray_frame(final, t), gray_frame(master, t))
        print(f"t={t:.2f}s  cue activo: {cue_first['text']!r}")
        report_band(mask, 1200, HEIGHT, "subtitulo (inferior)")

        rows = np.where(mask[1200:].any(axis=1))[0]
        if rows.size:
            bottom = int(rows[-1]) + 1200
            margin = HEIGHT - bottom
            verdict = "OK" if margin > 8 else "TOCA EL BORDE INFERIOR"
            print(f"  {'':<22} margen inferior = {margin}px  [{verdict}]")
        else:
            print("  [FALLO] no se dibuja ningun subtitulo")

    if cue_last:
        t = (cue_last["start"] + cue_last["end"]) / 2
        mask = overlay_mask(gray_frame(final, t), gray_frame(master, t))
        print(f"\nt={t:.2f}s  ultimo cue: {cue_last['text']!r}")
        report_band(mask, 1200, HEIGHT, "subtitulo (inferior)")

    if has_title:
        t = min(0.6, props["durationInSeconds"] / 2)
        mask = overlay_mask(gray_frame(final, t), gray_frame(master, t))
        print(f"\nt={t:.2f}s  hook presente: {props['title']!r}")
        report_band(mask, 0, 1200, "hook (superior)")

        end = props["durationInSeconds"] - 0.1
        mask_end = overlay_mask(gray_frame(final, end), gray_frame(master, end))
        top_half = mask_end[:600]
        print(f"t={end:.2f}s  final del clip (el hook deberia haberse ido)")
        if top_half.any():
            rows = np.where(top_half.any(axis=1))[0]
            print(f"  [AVISO] quedan {int(rows[-1]) + 1}px dibujados arriba al final")
        else:
            print("  OK: el titulo se retira antes del final")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
