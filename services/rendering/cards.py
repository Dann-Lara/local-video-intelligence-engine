"""Tarjetas CTA renderizadas como PNG RGBA (heredadas del MVP aprobado).

Porta `make_card.py` del pipeline aprobado: mismo fondo, gradiente, borde
electrico, filamento, badge, barra de progreso, tipografia y layout responsivo
del subtitulo. No se rediseña nada; solo se empaqueta como modulo reutilizable.

`build_card` trabaja con la configuracion por accion de `card_family.json`
(`subscribe`, `like`, `comment`, `share`) y devuelve una imagen RGBA del tamaño
`card + glow`, que es la que espera el compositor de overlays.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from services.rendering.card_text import resolve_card_text

DEFAULT_FONTS_DIR = Path(os.environ.get("LVS_FONTS_DIR", r"C:\Windows\Fonts"))
DEFAULT_FAMILY_PATH = Path(__file__).with_name("card_family.json")

GLOW = 26
SUPERSAMPLE = 3


def load_font(name: str, size: int, *, fonts_dir: Path = DEFAULT_FONTS_DIR) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(Path(fonts_dir) / name), size)


def glyph_play(d: ImageDraw.ImageDraw, cx, cy, s, color):
    w = 0.52 * s
    h = 0.60 * s
    ox = 0.04 * s
    pts = [(cx - w / 2 + ox, cy - h / 2),
           (cx - w / 2 + ox, cy + h / 2),
           (cx + w / 2 + ox, cy)]
    d.polygon(pts, fill=color)


def glyph_watch_play(d: ImageDraw.ImageDraw, cx, cy, s, color):
    w = 0.66 * s
    h = 0.48 * s
    r = 0.06 * s
    x0, y0 = cx - w / 2, cy - h / 2 - 0.03 * s
    x1, y1 = cx + w / 2, cy + h / 2 - 0.03 * s
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=color,
                        width=max(1, int(0.05 * s)))
    tw = 0.20 * s
    th = 0.24 * s
    ox = 0.02 * s
    pts = [(cx - tw / 2 + ox, cy - th / 2 - 0.03 * s),
           (cx - tw / 2 + ox, cy + th / 2 - 0.03 * s),
           (cx + tw / 2 + ox, cy - 0.03 * s)]
    d.polygon(pts, fill=color)
    d.line([(cx - 0.10 * s, y1 + 0.10 * s), (cx + 0.10 * s, y1 + 0.10 * s)],
           fill=color, width=max(1, int(0.05 * s)))


def glyph_subscribe_plus(d: ImageDraw.ImageDraw, cx, cy, s, color):
    pcx = cx - 0.14 * s
    hr = 0.12 * s
    head_cy = cy - 0.12 * s
    d.ellipse([pcx - hr, head_cy - hr, pcx + hr, head_cy + hr], fill=color)
    bw = 0.30 * s
    bh = 0.22 * s
    d.pieslice([pcx - bw / 2, head_cy + 0.06 * s,
                pcx + bw / 2, head_cy + 0.06 * s + 2 * bh],
               start=180, end=360, fill=color)
    plus_cx = cx + 0.20 * s
    plus_cy = cy + 0.02 * s
    arm = 0.11 * s
    thick = max(1, int(0.055 * s))
    d.line([(plus_cx - arm, plus_cy), (plus_cx + arm, plus_cy)],
           fill=color, width=thick)
    d.line([(plus_cx, plus_cy - arm), (plus_cx, plus_cy + arm)],
           fill=color, width=thick)


def draw_bell(draw: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, color):
    s = scale
    w = 0.62 * s
    h = 0.62 * s
    x0, x1 = cx - w / 2, cx + w / 2
    top = cy - h / 2
    bottom = cy + h / 2
    pts = []
    steps = 24
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (0.14 * s) * t
        y = bottom - (bottom - (top + 0.10 * s)) * t
        pts.append((x, y))
    for i in range(steps + 1):
        t = i / steps
        x = (x0 + 0.14 * s) + ((x1 - 0.14 * s) - (x0 + 0.14 * s)) * t
        y = (top + 0.10 * s) - 0.10 * s * math.sin(math.pi * t)
        pts.append((x, y))
    for i in range(steps + 1):
        t = i / steps
        x = x1 - (0.14 * s) * t
        y = (top + 0.10 * s) + (bottom - (top + 0.10 * s)) * t
        pts.append((x, y))
    pts.append((x0, bottom))
    draw.polygon(pts, fill=color)
    r = 0.09 * s
    draw.ellipse([cx - r, bottom - 0.02 * s, cx + r, bottom + 0.16 * s], fill=color)


def glyph_thumbs_up(d: ImageDraw.ImageDraw, cx, cy, s, color):
    bw = 0.26 * s
    bh = 0.42 * s
    bx0 = cx - 0.34 * s
    by0 = cy - 0.02 * s
    d.rounded_rectangle([bx0, by0, bx0 + bw, by0 + bh], radius=0.06 * s, fill=color)
    pw = 0.40 * s
    ph = 0.36 * s
    px0 = bx0 + bw + 0.02 * s
    py0 = cy - 0.06 * s
    d.rounded_rectangle([px0, py0, px0 + pw, py0 + ph], radius=0.10 * s, fill=color)
    tw = 0.20 * s
    th = 0.30 * s
    tx0 = px0 + 0.06 * s
    d.rounded_rectangle([tx0, cy - 0.06 * s - th, tx0 + tw, cy - 0.02 * s],
                        radius=0.08 * s, fill=color)


def glyph_comment_bubble(d: ImageDraw.ImageDraw, cx, cy, s, color):
    w = 0.74 * s
    h = 0.54 * s
    r = 0.12 * s
    x0, y0 = cx - w / 2, cy - h / 2 - 0.04 * s
    x1, y1 = cx + w / 2, cy + h / 2 - 0.04 * s
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=color)
    tail = [(cx - 0.14 * s, y1 - 0.02 * s),
            (cx - 0.02 * s, y1 - 0.02 * s),
            (cx - 0.16 * s, y1 + 0.16 * s)]
    d.polygon(tail, fill=color)


def glyph_share(d: ImageDraw.ImageDraw, cx, cy, s, color):
    r = 0.125 * s
    nodes = [(cx + 0.26 * s, cy - 0.26 * s),
             (cx + 0.26 * s, cy + 0.26 * s),
             (cx - 0.26 * s, cy)]
    lw = max(1, int(0.075 * s))
    d.line([nodes[2], nodes[0]], fill=color, width=lw)
    d.line([nodes[2], nodes[1]], fill=color, width=lw)
    for (nx, ny) in nodes:
        d.ellipse([nx - r, ny - r, nx + r, ny + r], fill=color)
    d.ellipse([nodes[2][0] - r * 0.42, nodes[2][1] - r * 0.42,
               nodes[2][0] + r * 0.42, nodes[2][1] + r * 0.42],
              fill=(0, 0, 0, 0))


ICON_RENDERERS = {
    "play": glyph_play,
    "watch_play": glyph_watch_play,
    "subscribe_plus": glyph_subscribe_plus,
    "thumbs_up": glyph_thumbs_up,
    "comment_bubble": glyph_comment_bubble,
    "share": glyph_share,
    "bell": None,
}


def _measure(font, text):
    return font.getlength(text)


def _greedy_wrap(font, text, max_width):
    words = text.split()
    if not words:
        return [""]
    lines = []
    cur = words[0]
    for w in words[1:]:
        if _measure(font, cur + " " + w) <= max_width:
            cur = cur + " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def layout_subtitle(
    font_path: Path,
    text: str,
    max_width: float,
    start_size: int,
    min_size: int,
    max_lines: int,
    line_spacing: float,
    line_height_pad: float,
    max_height: Optional[float] = None,
    *,
    fonts_dir: Path = DEFAULT_FONTS_DIR,
):
    def compute(size):
        font = load_font(font_path.name, size, fonts_dir=fonts_dir)
        lines = _greedy_wrap(font, text, max_width)
        widest = max(_measure(font, ln) for ln in lines) if lines else 0.0
        ascent, descent = font.getmetrics()
        line_height = (ascent + descent) * line_spacing + line_height_pad
        block_height = line_height * len(lines)
        fits = (len(lines) <= max_lines and widest <= max_width
                and (max_height is None or block_height <= max_height))
        return font, lines, line_height, block_height, widest, fits

    font, lines, lh, bh, widest, fits = compute(start_size)
    if fits:
        return {"size": start_size, "lines": lines, "line_height": lh,
                "block_height": bh, "fits": True, "widest": widest}
    for size in range(start_size - 1, min_size - 1, -1):
        font, lines, lh, bh, widest, fits = compute(size)
        if fits:
            return {"size": size, "lines": lines, "line_height": lh,
                    "block_height": bh, "fits": True, "widest": widest}
    font, lines, lh, bh, widest, _ = compute(min_size)
    return {"size": min_size, "lines": lines, "line_height": lh,
            "block_height": bh, "fits": False, "widest": widest}


def build_card(
    cfg: Dict[str, Any],
    subtitle_text: Optional[str] = None,
    report: Optional[Dict[str, Any]] = None,
    icon_kind: Optional[str] = None,
    *,
    title_text: Optional[str] = None,
    language: Optional[str] = None,
    localization: Optional[Dict[str, Any]] = None,
    fonts_dir: Path = DEFAULT_FONTS_DIR,
) -> Image.Image:
    cw = cfg["card"]["width"]
    ch = cfg["card"]["height"]
    r = cfg["card"]["corner_radius"]
    glow = GLOW
    W = cw + 2 * glow
    H = ch + 2 * glow
    SS = SUPERSAMPLE

    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pal = cfg["palette"]
    cfill = tuple(pal["card_fill"])
    cfill_top = tuple(pal["card_fill_top"])
    cborder = tuple(pal["border_electric"])
    cfila = tuple(pal["filament_white"])

    x0 = glow * SS
    y0 = glow * SS
    x1 = (glow + cw) * SS
    y1 = (glow + ch) * SS

    for i, a in enumerate((26, 46, 70)):
        off = (8 + i * 7) * SS
        d.rounded_rectangle([x0 - off, y0 - off, x1 + off, y1 + off],
                            radius=(r + i * 7) * SS,
                            outline=(cborder[0], cborder[1], cborder[2], a),
                            width=3 * SS)

    grad = Image.new("RGBA", ((x1 - x0), (y1 - y0)), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    gh = y1 - y0
    for yy in range(gh):
        t = yy / max(1, gh - 1)
        col = tuple(int(cfill_top[k] * (1 - t) + cfill[k] * t) for k in range(4))
        gd.line([(0, yy), (x1 - x0, yy)], fill=col)
    mask = Image.new("L", ((x1 - x0), (y1 - y0)), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, (x1 - x0), (y1 - y0)],
                                           radius=r * SS, fill=255)
    img.paste(grad, (x0, y0), mask)

    d.rounded_rectangle([x0, y0, x1, y1], radius=r * SS,
                        outline=(cborder[0], cborder[1], cborder[2], 255),
                        width=5 * SS)
    d.rounded_rectangle([x0 + 3 * SS, y0 + 3 * SS, x1 - 3 * SS, y1 - 3 * SS],
                        radius=(r - 2) * SS,
                        outline=(cfila[0], cfila[1], cfila[2], cfila[3]),
                        width=2 * SS)

    tp = cfg["typography"]
    comp = cfg.get("composition", {})
    tcol = tuple(tp["title_color"])
    scol = tuple(tp["subtitle_color"])
    title_font = load_font(tp["title_font"], tp["title_size"] * SS, fonts_dir=fonts_dir)
    title_meas = load_font(tp["title_font"], tp["title_size"], fonts_dir=fonts_dir)
    tx = x0 + tp["text_left"] * SS

    title_usage = title_text if title_text is not None else cfg["text"]["title"]
    tbb = title_meas.getbbox(title_usage)
    title_ink_h = tbb[3] - tbb[1]
    title_ink_top_off = tbb[1]

    usage = subtitle_text if subtitle_text is not None else cfg["text"]["subtitle"]
    sl = cfg.get("subtitle_layout", {})
    text_left_px = tp["text_left"]
    right_reserve = sl.get("subtitle_right_reserve", 96)
    max_width_px = sl.get("subtitle_max_width", cw - text_left_px - right_reserve)
    max_width_px = min(max_width_px, cw - text_left_px - right_reserve)
    max_width_px = max(40, max_width_px)

    bottom_margin = sl.get("subtitle_bottom_margin", 22)
    avail_sub_h = ch - comp.get("content_top_margin", 40) - title_ink_h \
        - comp.get("title_subtitle_gap", 6) - comp.get("content_bottom_margin", 52)

    lay = layout_subtitle(
        Path(tp["subtitle_font"]), usage, max_width_px,
        sl.get("subtitle_font_size", tp["subtitle_size"]),
        sl.get("subtitle_min_font_size", 26),
        sl.get("subtitle_max_lines", 3),
        sl.get("subtitle_line_spacing", 1.12),
        sl.get("subtitle_line_height_pad", 0),
        max_height=avail_sub_h,
        fonts_dir=fonts_dir,
    )
    sub_font = load_font(tp["subtitle_font"], lay["size"] * SS, fonts_dir=fonts_dir)
    sub_meas = load_font(tp["subtitle_font"], lay["size"], fonts_dir=fonts_dir)
    sbb = sub_meas.getbbox("Ag")
    sub_ink_asc = sbb[1]
    sub_ink_desc = sbb[3]

    gap = comp.get("title_subtitle_gap", 6)
    sub_ink_h = sub_ink_desc - sub_ink_asc
    sub_block_ink = (len(lay["lines"]) - 1) * lay["line_height"] + sub_ink_h
    group_ink_h = title_ink_h + gap + sub_block_ink

    content_top = comp.get("content_top_margin", 40)
    content_bottom = ch - comp.get("content_bottom_margin", 52)
    content_area_h = content_bottom - content_top
    content_center = (content_top + content_bottom) / 2.0

    group_ink_top = content_center - group_ink_h / 2.0
    sub_ink_top = group_ink_top + title_ink_h + gap

    title_draw_y = group_ink_top - title_ink_top_off
    d.text((tx, y0 + title_draw_y * SS), title_usage,
           font=title_font, fill=tcol)

    y_cursor = sub_ink_top - sub_ink_asc
    for i, ln in enumerate(lay["lines"]):
        d.text((tx, y0 + (y_cursor + i * lay["line_height"]) * SS), ln,
               font=sub_font, fill=scol)

    ic = cfg["icon"]
    bd = ic["badge_diameter"]
    bl = ic["badge_left"]
    bcx = x0 + (bl + bd / 2) * SS
    group_ink_center = group_ink_top + group_ink_h / 2.0
    group_ink_center = min(max(group_ink_center, bd / 2 + 8), ch - bd / 2 - 8)
    bcy = y0 + group_ink_center * SS
    br = bd / 2 * SS
    accent = tuple(pal["badge_accent"])
    accent2 = tuple(pal["badge_accent_2"])
    d.ellipse([bcx - br - 3 * SS, bcy - br - 3 * SS,
               bcx + br + 3 * SS, bcy + br + 3 * SS],
              outline=(accent2[0], accent2[1], accent2[2], 180), width=2 * SS)
    bgrad = Image.new("RGBA", (int(2 * br), int(2 * br)), (0, 0, 0, 0))
    bgd = ImageDraw.Draw(bgrad)
    for yy in range(int(2 * br)):
        t = yy / max(1, int(2 * br) - 1)
        col = tuple(int(accent2[k] * (1 - t) + accent[k] * t) for k in range(4))
        bgd.line([(0, yy), (int(2 * br), yy)], fill=col)
    bmask = Image.new("L", (int(2 * br), int(2 * br)), 0)
    ImageDraw.Draw(bmask).ellipse([0, 0, int(2 * br), int(2 * br)], fill=255)
    img.paste(bgrad, (int(bcx - br), int(bcy - br)), bmask)

    kind = icon_kind or ic.get("kind", "watch_play")
    glyph_scale = bd * ic["glyph_scale"] * SS
    if kind == "bell":
        draw_bell(d, bcx, bcy, glyph_scale, (255, 255, 255, 255))
    else:
        ICON_RENDERERS[kind](d, bcx, bcy, glyph_scale, (255, 255, 255, 255))

    chev_x = x1 - comp.get("chevron_right_offset", 54) * SS
    chev_y = y0 + content_center * SS
    cs = 16 * SS
    d.line([(chev_x - cs * 0.4, chev_y - cs), (chev_x + cs * 0.5, chev_y),
            (chev_x - cs * 0.4, chev_y + cs)],
           fill=(accent2[0], accent2[1], accent2[2], 230),
           width=4 * SS, joint="curve")

    pt = tuple(pal["progress_track"])
    by = y1 - 20 * SS
    bh = 6 * SS
    d.rounded_rectangle([x0 + 34 * SS, by, x1 - 34 * SS, by + bh],
                        radius=bh / 2, fill=(pt[0], pt[1], pt[2], pt[3]))

    if report is not None:
        report.update({
            "icon_kind": kind,
            "title_text": title_usage,
            "language": language,
            "localization": localization or {},
            "title_ink_h": round(title_ink_h, 2),
            "subtitle_text": usage,
            "subtitle_font_size": lay["size"],
            "subtitle_lines": len(lay["lines"]),
            "subtitle_line_texts": lay["lines"],
            "subtitle_line_height": round(lay["line_height"], 2),
            "subtitle_block_ink_h": round(sub_block_ink, 2),
            "group_ink_h": round(group_ink_h, 2),
            "group_ink_top": round(group_ink_top, 2),
            "group_ink_center": round(group_ink_center, 2),
            "content_area_center": round(content_center, 2),
            "icon_center_y": round(group_ink_center, 2),
            "chevron_center_y": round(content_center, 2),
            "title_subtitle_gap": gap,
            "sub_bottom_ink_y": round(sub_ink_top + sub_block_ink, 2),
            "card_center_y": ch / 2.0,
            "fits": lay["fits"],
        })

    return img.resize((W, H), Image.LANCZOS)


def load_family(path: Path = DEFAULT_FAMILY_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_from_config(
    cfg: Dict[str, Any],
    out_png: Path,
    subtitle_text: Optional[str] = None,
    icon_kind: Optional[str] = None,
    report_out: Optional[Path] = None,
    *,
    title_text: Optional[str] = None,
    language: Optional[str] = None,
    localization: Optional[Dict[str, Any]] = None,
    fonts_dir: Path = DEFAULT_FONTS_DIR,
) -> Tuple[Image.Image, Dict[str, Any]]:
    report: Dict[str, Any] = {}
    card = build_card(cfg, subtitle_text=subtitle_text, report=report,
                      icon_kind=icon_kind, title_text=title_text,
                      language=language, localization=localization,
                      fonts_dir=fonts_dir)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    card.save(out_png)
    if report_out is not None:
        Path(report_out).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return card, report


def build_card_for_action(
    action: str,
    out_png: Path,
    subtitle_text: Optional[str] = None,
    *,
    language: str,
    title_text: Optional[str] = None,
    family_path: Path = DEFAULT_FAMILY_PATH,
    title_subtitle_gap: int = 21,
    report_out: Optional[Path] = None,
    fonts_dir: Path = DEFAULT_FONTS_DIR,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """Construye una tarjeta localizada para `action` y `language`.

    `language` es el idioma canonico del audio fuente. El titulo y el subtitulo
    salen del catalogo revisado (`card_text.py`); `card_family.json` solo aporta
    geometria, colores y animacion. Si el job aporta un `subtitle_text` concreto
    se usa como gancho editorial, pero el pipeline valida que su idioma coincida
    con el canonico antes de llegar aqui.
    """
    localized = resolve_card_text(action, language)
    family = load_family(family_path)
    actions = family.get("actions", {})
    if action not in actions:
        raise ValueError(f"accion de tarjeta desconocida: {action!r}")
    cfg = json.loads(json.dumps(actions[action]))
    cfg.setdefault("composition", {})["title_subtitle_gap"] = title_subtitle_gap
    usage_title = title_text if title_text is not None else localized["title"]
    usage_subtitle = (
        subtitle_text if subtitle_text is not None else localized["subtitle"]
    )
    localization_info = {
        **localized,
        "used_title": usage_title,
        "used_subtitle": usage_subtitle,
        "title_source": "job_override" if title_text is not None else "catalog",
        "subtitle_source": (
            "job_override" if subtitle_text is not None else "catalog"
        ),
    }
    return build_from_config(
        cfg, out_png, subtitle_text=usage_subtitle,
        title_text=usage_title, language=language,
        localization=localization_info,
        report_out=report_out, fonts_dir=fonts_dir,
    )


__all__ = [
    "DEFAULT_FAMILY_PATH",
    "DEFAULT_FONTS_DIR",
    "GLOW",
    "build_card",
    "build_card_for_action",
    "build_from_config",
    "layout_subtitle",
    "load_family",
    "load_font",
]
