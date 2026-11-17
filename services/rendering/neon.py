"""Marco de neon perimetral reutilizable (heredado del MVP aprobado).

Porta `make_neon_frame_adj.py` del pipeline aprobado sin cambiar el diseno:
marco de tiras azul electrico/cian, con nucleos blancos, segmentado y animado
con parpadeos desfasados. Solo se dibuja en el borde del lienzo 1080x1920.

El generador es determinista: la misma duracion y semilla producen el mismo
ASS. No renderiza video: devuelve el texto ASS que se compone con FFmpeg.
"""

from __future__ import annotations

import random

WIDTH = 1080
HEIGHT = 1920

# Margen tecnico pequeno respecto al lienzo real (0..1080, 0..1920).
MARGIN = 18

# Centros de las tiras (linea central del tubo de neon) en cada lado.
TOP_Y = MARGIN + 8
BOTTOM_Y = HEIGHT - MARGIN - 8
LEFT_X = MARGIN + 8
RIGHT_X = WIDTH - MARGIN - 8

TUBE_W = 2
CORE_W = 0
HALO_BORD = 7
BODY_BORD = 2
CORE_BORD = 0

# Colores ASS en formato &HAABBGGRR (BGR), con alfa AA (00 = opaco).
ELECTRIC = "H00FF8000"
CYAN = "H00FFFF00"
WHITE = "H00FFFFFF"

BASE_SEED = 20261020
SEG_LEN = 120
SEG_GAP = 10


def _fmt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _seg_path(x0: int, y0: int, x1: int, y1: int) -> str:
    return f"m {x0} {y0} l {x1} {y0} l {x1} {y1} l {x0} {y1} l {x0} {y0}"


def _side_segments(length: int, step: int, gap: int):
    segs = []
    pos = 0
    while pos < length:
        end = min(length, pos + step)
        segs.append((pos, end))
        pos = end + gap
    return segs


def _tube_band(orientation: str, center: int, a0: int, a1: int, half: int) -> str:
    if orientation == "h":
        return _seg_path(a0, center - half, a1, center + half)
    return _seg_path(center - half, a0, center + half, a1)


def build_neon_ass(duration: float, *, seed: int = BASE_SEED) -> str:
    """Devuelve el ASS del marco de neon para `duration` segundos."""
    if not duration or duration <= 0:
        raise ValueError("duration debe ser > 0")
    rng = random.Random(seed)

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
        f"PlayResX: {WIDTH}\n"
        f"PlayResY: {HEIGHT}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Neon,Arial Black,40,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    lines = [header]
    segments = []

    def add_edge(orient, center, start, end, skip_prob):
        for a0, a1 in _side_segments(end - start, SEG_LEN, SEG_GAP):
            if rng.random() < skip_prob:
                continue
            segments.append((orient, center, start + a0, start + a1))

    add_edge("h", TOP_Y, LEFT_X - TUBE_W, RIGHT_X + TUBE_W, 0.12)
    add_edge("h", BOTTOM_Y, LEFT_X - TUBE_W, RIGHT_X + TUBE_W, 0.12)
    add_edge("v", LEFT_X, TOP_Y - TUBE_W, BOTTOM_Y + TUBE_W, 0.15)
    add_edge("v", RIGHT_X, TOP_Y - TUBE_W, BOTTOM_Y + TUBE_W, 0.15)

    for cx in (LEFT_X, RIGHT_X):
        segments.append(("v", cx, TOP_Y - TUBE_W, TOP_Y + 34))
        segments.append(("v", cx, BOTTOM_Y - 34, BOTTOM_Y + TUBE_W))

    core_flags = [rng.random() < 0.45 for _ in segments]

    halo_all = " ".join(
        _tube_band(o, c, a0, a1, TUBE_W + 2) for o, c, a0, a1 in segments
    )
    lines.append(
        f"Dialogue: 2,{_fmt_time(0)},{_fmt_time(duration)},Neon,,0,0,0,,"
        f"{{\\an7\\pos(0,0)\\p1\\1a&HFF&\\bord{HALO_BORD}\\3c&{ELECTRIC}&"
        f"\\3a&H88&\\t(0,1400,\\3a&H68&)\\t(1400,2800,\\3a&H88&)}}{halo_all}\n"
    )

    body_all = " ".join(
        _tube_band(o, c, a0, a1, TUBE_W) for o, c, a0, a1 in segments
    )
    lines.append(
        f"Dialogue: 2,{_fmt_time(0)},{_fmt_time(duration)},Neon,,0,0,0,,"
        f"{{\\an7\\pos(0,0)\\p1\\1a&HFF&\\bord{BODY_BORD}\\3c&{ELECTRIC}&"
        f"\\3a&H30&\\t(0,400,\\3c&{CYAN}&)\\t(400,800,\\3c&{ELECTRIC}&)}}{body_all}\n"
    )

    for orient, center, a0, a1 in segments:
        if rng.random() > 0.34:
            continue
        seg_path = _tube_band(orient, center, a0, a1, TUBE_W)
        t = rng.uniform(0.0, 1.2)
        while t < duration:
            fl = rng.uniform(0.06, 0.22)
            t2 = min(duration, t + fl)
            dur_ms = max(1, int((t2 - t) * 1000))
            half = max(1, dur_ms // 2)
            col = rng.choice([WHITE, CYAN, ELECTRIC])
            lines.append(
                f"Dialogue: 2,{_fmt_time(t)},{_fmt_time(t2)},Neon,,0,0,0,,"
                f"{{\\an7\\pos(0,0)\\p1\\1a&HFF&\\bord{BODY_BORD}\\3c&{col}&"
                f"\\3a&H20&\\t(0,{half},\\3a&HD0&)\\t({half},{dur_ms},\\3a&H20&)}}"
                f"{seg_path}\n"
            )
            t = t2 + rng.uniform(0.6, 3.2)
            if rng.random() < 0.5:
                t += rng.uniform(0.05, 0.18)

    for i, (orient, center, a0, a1) in enumerate(segments):
        if not core_flags[i]:
            continue
        core_path = _tube_band(orient, center, a0, a1, CORE_W)
        lines.append(
            f"Dialogue: 2,{_fmt_time(0)},{_fmt_time(duration)},Neon,,0,0,0,,"
            f"{{\\an7\\pos(0,0)\\p1\\1a&HFF&\\bord{CORE_BORD}\\3c&{WHITE}&"
            f"\\3a&H55&}}{core_path}\n"
        )
        t = rng.uniform(0.0, 1.5)
        while t < duration:
            spike = rng.uniform(0.10, 0.35)
            t2 = min(duration, t + spike)
            dur_ms = max(1, int((t2 - t) * 1000))
            half = max(1, dur_ms // 2)
            lines.append(
                f"Dialogue: 2,{_fmt_time(t)},{_fmt_time(t2)},Neon,,0,0,0,,"
                f"{{\\an7\\pos(0,0)\\p1\\1a&HFF&\\bord{CORE_BORD + 1}\\3c&{WHITE}&"
                f"\\3a&H00&\\t(0,{half},\\3a&H40&)\\t({half},{dur_ms},\\3a&H00&)}}"
                f"{core_path}\n"
            )
            t = t2 + rng.uniform(0.9, 4.0)

    return "".join(lines)


__all__ = ["BASE_SEED", "HEIGHT", "MARGIN", "WIDTH", "build_neon_ass"]
