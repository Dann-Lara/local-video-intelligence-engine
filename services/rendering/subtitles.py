"""Subtitulos sincronizados para las versiones verticales (Fase 5C, minimo).

Objetivo de esta fase: integrar visualmente subtitulos en los MP4 verticales de
la Fase 5B usando **solo** los tiempos a nivel de segmento de la transcripcion
existente. La transcripcion de este proyecto tiene `words == []`, por lo que NO
existe sincronizacion por palabra: no se debe simular.

Decisiones (minimas y explicitas):

- Formato **ASS** (SubStation Alpha) renderizado con el filtro `ass` de FFmpeg
  (libass). No requiere dependencias adicionales: ya esta disponible.
- Los tiempos absolutos de la transcripcion se convierten a tiempos **relativos
  al clip** restando el desplazamiento absoluto efectivo de inicio del clip
  (`absolute_start_seconds`). Ese desplazamiento se obtuvo en la Fase 5C por
  correlacion de audio contra la fuente (ver informe), no por suposicion.
- Un segmento se incluye solo si su intervalo solapa el rango absoluto del clip.
  Los segmentos que atraviesan los bordes se recortan a los limites del clip.
- Se descartan intervalos invalidos (`end <= start`), tiempos negativos, duracion
  cero y cues fuera de la duracion del video.
- El texto original se conserva; solo se ajusta el formato (saltos de linea y
  escapes de ASS). No se inventan palabras ni se completa audio inaudible.
- Estilo de alto contraste, tamanio legible en 1080x1920, contorno marcado y
  posicion en zona segura (por encima del borde inferior de la plataforma).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .errors import RenderingSourceError, RenderingValidationError

# ---------------------------------------------------------------------------
# Estilo (ASS) y constantes de maquetacion
# ---------------------------------------------------------------------------

# Fuente gruesa realmente instalada en Windows (Arial Black). Se usa como
# display por su peso; mantiene identidad visual coherente entre clips.
FONT_NAME_DEFAULT = "Arial Black"
FONT_FILE_DEFAULT = r"C:\Windows\Fonts\ariblk.ttf"
# Tamanio en unidades de guion (PlayRes). Calibrado empiricamente: libass
# renderiza el ancho medido por PIL multiplicado por ~0.7071 cuando
# PlayResX coincide con el ancho de salida (ver CALIBRATION_FACTOR).
FONT_SIZE_DEFAULT = 96
MAX_LINES = 2
# Zona segura: margen inferior amplio para no chocar con los controles
# habituales de Shorts. En 1920 de alto, ~340 px desde abajo.
MARGIN_BOTTOM_DEFAULT = 340
# Margen horizontal: deja ~70 px por lado; con 1080 de ancho queda 940 util.
MARGIN_LR_DEFAULT = 70
# Contorno y sombra para separar el texto de la imagen.
OUTLINE_WIDTH_DEFAULT = 5
SHADOW_WIDTH_DEFAULT = 3
# Duracion minima visible de un cue. Evita destellos sub-fotograma cuando un
# segmento apenas atraviesa el borde del clip (p.ej. 13 ms). No inventa texto:
# solo descarta un cue que no podria verse de forma legible.
MIN_CUE_SECONDS_DEFAULT = 0.3

# Factor de calibracion: ancho_render_libass ~= ancho_PIL * CALIBRATION_FACTOR.
# Medido con Arial Black en 1080x1920 (PlayResX=1080). Permite garantizar que
# ninguna linea rebase el ancho util antes de renderizar.
CALIBRATION_FACTOR = 0.70710678
# Si una linea sola (una palabra muy larga) no cabe, se reduce el tamanio de
# ese cue como ultimo recurso, sin bajar de este minimo.
MIN_FONT_SIZE_FALLBACK = 56

# ASS BGR color: blanco &H00FFFFFF, contorno negro &H00000000.
PRIMARY_COLOUR = "&H00FFFFFF"
OUTLINE_COLOUR = "&H00000000"
BACK_COLOUR = "&H64000000"      # fondo semi-transparente por si se usa BorderStyle=4

_WHITESPACE_RE = re.compile(r"\s+")


def _load_font(font_file: str, size: int):
    """Carga la fuente para medir anchos reales; devuelve (font, ok).

    Si PIL no esta disponible o la fuente no se puede abrir, devuelve (None,
    False) y el wrapping cae al modo por caracteres.
    """
    try:
        from PIL import ImageFont  # import perezoso, opcional
    except Exception:
        return None, False
    try:
        return ImageFont.truetype(font_file, size), True
    except Exception:
        return None, False


@dataclass
class Cue:
    """Un subtitulo listo para renderizar, en tiempo relativo al clip."""

    start_seconds: float
    end_seconds: float
    text: str
    source_segment_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Conversion de tiempos
# ---------------------------------------------------------------------------


def segment_bounds(segment: Dict[str, Any]) -> Optional[tuple]:
    """Devuelve (start, end) validos de un segmento o `None` si son invalidos.

    Un segmento es invalido si le faltan tiempos, no son numericos, son
    negativos o `end <= start`.
    """
    try:
        start = float(segment.get("start_seconds"))
        end = float(segment.get("end_seconds"))
    except (TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    return start, end


def segment_to_relative(
    segment: Dict[str, Any],
    *,
    absolute_start_seconds: float,
    clip_duration_seconds: float,
) -> Optional[tuple]:
    """Convierte un segmento absoluto a (start, end) relativos al clip.

    Devuelve `None` si el segmento queda completamente fuera del clip, si sus
    tiempos son invalidos o si el resultado no cae dentro de la duracion del
    clip. Si el segmento atraviesa un borde, se recorta al limite del clip.
    """
    bounds = segment_bounds(segment)
    if bounds is None:
        return None
    abs_start, abs_end = bounds

    rel_start = abs_start - absolute_start_seconds
    rel_end = abs_end - absolute_start_seconds

    # Recorte a los limites del clip.
    rel_start = max(rel_start, 0.0)
    rel_end = min(rel_end, clip_duration_seconds)

    if rel_end <= rel_start:
        return None
    return rel_start, rel_end


def _normalize_text(text: Any) -> str:
    if text is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(text)).strip()


def _measure_width(text: str, font) -> float:
    """Ancho en unidades de guion (mismo espacio que `Fontsize`)."""
    if font is None:
        # Aproximacion conservadora si no hay metrica de fuente (~0.6 em).
        return len(text) * 0.6
    try:
        return float(font.getlength(text))
    except Exception:
        return float(font.getbbox(text)[2] - font.getbbox(text)[0])


def _fits(text: str, font, max_units: float) -> bool:
    """True si `text` cabe en el ancho util (unidades de guion)."""
    return _measure_width(text, font) <= max_units


def _wrap_lines_metric(words: List[str], font, max_units: float) -> List[str]:
    """Envuelve palabras en lineas segun ancho real medido (no por caracteres)."""
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if not current or _fits(candidate, font, max_units):
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _greedy_lines(words: List[str], font, max_units: float) -> List[str]:
    """Lineas resultantes de un empaquetado voraz por ancho real."""
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if not current or _fits(candidate, font, max_units):
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _balanced_lines(words: List[str], font, max_units: float, max_lines: int) -> List[str]:
    """Reparte `words` en como maximo `max_lines` lineas equilibradas por ancho.

    Minimiza la anchura de la linea mas larga respetando el orden y sin descartar
    texto. Si el texto no cabe en `max_lines` lineas al ancho dado, se devuelve la
    mejor division posible (puede exceder `max_lines`); el llamador decide si
    reduce el tamanio de fuente. Nunca parte palabras ni inventa contenido.
    """
    n = len(words)
    if n == 0:
        return []
    # Si todo cabe en una sola linea, no partir: no se fuerzan cortes.
    if _fits(" ".join(words), font, max_units):
        return [" ".join(words)]
    if n <= max_lines:
        return list(words)

    def width_of(chunk_words: List[str]) -> float:
        return _measure_width(" ".join(chunk_words), font)

    def pack(limit: float) -> List[str]:
        """Empaqueta vorazmente sin que ninguna linea supere `limit`."""
        blocks: List[str] = []
        current: List[str] = []
        for w in words:
            if not current:
                current = [w]
            elif width_of(current + [w]) <= limit:
                current.append(w)
            else:
                blocks.append(" ".join(current))
                current = [w]
        if current:
            blocks.append(" ".join(current))
        return blocks

    # Busqueda binaria del menor ancho maximo que cabe en `max_lines` lineas,
    # sin que ninguna linea supere el ancho disponible `max_units`.
    lo = max(width_of([w]) for w in words)  # una palabra larga no se parte
    hi = width_of(words)
    best: Optional[List[str]] = None
    while lo <= hi:
        mid = (lo + hi) / 2.0
        blocks = pack(mid)
        if len(blocks) <= max_lines and all(width_of([b]) <= max_units for b in blocks):
            best = blocks
            hi = mid - 1.0
        else:
            lo = mid + 1.0
    if best is not None:
        return best
    # No cabe en `max_lines` sin rebasar el ancho: division voraz al ancho
    # disponible (puede exceder `max_lines`; el llamador reduce la fuente).
    return _greedy_lines(words, font, max_units)


def wrap_text_metric(
    text: str,
    *,
    font_file: str = FONT_FILE_DEFAULT,
    font_size: int = FONT_SIZE_DEFAULT,
    available_units: float,
    max_lines: int = MAX_LINES,
) -> str:
    """Envuelve el texto usando metricas reales de la fuente.

    `available_units` es el ancho util en unidades de guion (PlayRes). Devuelve
    el texto con separadores `\\N` de ASS, repartido en como maximo `max_lines`
    lineas equilibradas por ancho, sin rebasar el ancho disponible. Si el texto
    no cabe en `max_lines` lineas (segmento muy largo), devuelve la division
    voraz al ancho util; el llamador debe trocearlo en varios cues.
    """
    words = text.split()
    if not words:
        return ""
    font, ok = _load_font(font_file, font_size)
    if not ok:
        # Fallback por caracteres: ancho util estimado a ~0.5 em por caracter.
        approx_chars = max(1, int(available_units / (font_size * 0.5)))
        return _wrap_text_chars(words, approx_chars, max_lines)
    lines = _balanced_lines(words, font, available_units, max_lines)
    return r"\N".join(lines)


def split_text_into_blocks(
    text: str,
    *,
    font_file: str = FONT_FILE_DEFAULT,
    font_size: int = FONT_SIZE_DEFAULT,
    available_units: float,
    max_lines: int = MAX_LINES,
) -> List[str]:
    """Trocea un segmento largo en bloques legibles de <= max_lines lineas.

    Cada bloque se muestra en un cue secuencial dentro del mismo intervalo. No
    se descarta ni se altera el texto. Los bloques se equilibran por anchura
    real para evitar un bloque final con una linea corta huerfana.
    """
    words = text.split()
    if not words:
        return []
    font, ok = _load_font(font_file, font_size)
    if not ok:
        approx_chars = max(1, int(available_units / (font_size * 0.5)))
        return [_wrap_text_chars(words, approx_chars, max_lines)]

    # Numero minimo de bloques que permiten <= max_lines lineas por bloque.
    one_line = _greedy_lines(words, font, available_units)
    if len(one_line) <= max_lines:
        return [r"\N".join(_balanced_lines(words, font, available_units, max_lines))]
    n_blocks = (len(one_line) + max_lines - 1) // max_lines

    # Repartir en `n_blocks` bloques contiguos equilibrando la anchura maxima.
    # Cada bloque se ajusta luego a `max_lines` lineas de forma equilibrada.
    total = _measure_width(" ".join(words), font)
    lo = max(_measure_width(w, font) for w in words)
    hi = total
    best: Optional[List[str]] = None
    while lo <= hi:
        mid = (lo + hi) / 2.0
        blocks: List[str] = []
        current: List[str] = []
        for w in words:
            if not current:
                current = [w]
            elif _measure_width(" ".join(current + [w]), font) <= mid:
                current.append(w)
            else:
                blocks.append(" ".join(current))
                current = [w]
        if current:
            blocks.append(" ".join(current))
        if len(blocks) <= n_blocks:
            best = blocks
            hi = mid - 1.0
        else:
            lo = mid + 1.0
    if best is None:
        best = one_line
        # Reagrupar por lineas si no se pudo equilibrar por anchura.
        groups: List[str] = []
        base, extra = divmod(len(best), n_blocks)
        sizes = [base + (1 if i < extra else 0) for i in range(n_blocks)]
        idx = 0
        for size in sizes:
            groups.append(r"\N".join(best[idx : idx + size]))
            idx += size
        return groups

    # Evitar huerfanos: si el ultimo bloque queda con una sola palabra, cederle
    # la ultima palabra del bloque anterior (sin romper el orden ni el texto).
    best = list(best)
    i = len(best) - 1
    while i >= 1 and len(best[i].split()) == 1 and len(best[i - 1].split()) > 2:
        prev_words = best[i - 1].split()
        moved = prev_words.pop()
        best[i - 1] = " ".join(prev_words)
        best[i] = f"{moved} {best[i]}"
        i -= 1

    # Ajustar cada bloque a <= max_lines lineas equilibradas.
    return [
        r"\N".join(_balanced_lines(b.split(), font, available_units, max_lines))
        for b in best
    ]


def _wrap_text_chars(words: List[str], max_chars: int, max_lines: int) -> str:
    """Fallback simple por caracteres cuando no hay metricas de fuente."""
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :])
        lines = head + [tail]
    return r"\N".join(lines)


def _wrap_text(text: str, max_chars: int, max_lines: int) -> str:
    """Compatibilidad: wrapping por caracteres (usado solo en fallback/pruebas)."""
    return _wrap_text_chars(text.split(), max_chars, max_lines)


def _escape_ass(text: str) -> str:
    """Escapa los caracteres con significado especial en ASS."""
    # Los saltos de linea ASS (`\N`) ya estan insertados y deben conservarse.
    text = text.replace("\r", "").replace("\n", r"\N")
    # Escapar llaves que libass interpretaria como override blocks.
    text = text.replace("{", r"\{").replace("}", r"\}")
    return text


def build_cues(
    segments: Iterable[Dict[str, Any]],
    *,
    absolute_start_seconds: float,
    clip_duration_seconds: float,
    width: int = 1080,
    height: int = 1920,
    margin_lr: int = MARGIN_LR_DEFAULT,
    font_file: str = FONT_FILE_DEFAULT,
    font_size: int = FONT_SIZE_DEFAULT,
    max_lines: int = MAX_LINES,
    min_cue_seconds: float = MIN_CUE_SECONDS_DEFAULT,
) -> List[Cue]:
    """Construye la lista ordenada de cues relativas, sin solapamientos.

    - Convierte tiempos absolutos a relativos.
    - Descarta segmentos fuera del clip o con intervalos invalidos.
    - Ajusta el texto al ancho real disponible usando metricas de la fuente
      (`wrap_text_metric`), no un conteo de caracteres.
    - Ordena cronologicamente y elimina solapamientos innecesarios ajustando el
      inicio del cue siguiente al fin del anterior.
    - Descarta cues mas cortos que `min_cue_seconds` para evitar destellos
      sub-fotograma; no inventa ni reordena texto.
    - No simula sincronizacion por palabra (no hay timestamps de palabra).
    """
    if clip_duration_seconds <= 0:
        raise RenderingValidationError(
            f"Duracion de clip invalida: {clip_duration_seconds}",
            details={"clip_duration_seconds": clip_duration_seconds},
        )

    avail_px = max(1, int(width) - 2 * int(margin_lr))
    # Conversion px de pantalla -> unidades de guion para el filtro `ass`.
    avail_units = avail_px / CALIBRATION_FACTOR

    raw: List[Cue] = []
    for seg in segments:
        rel = segment_to_relative(
            seg,
            absolute_start_seconds=absolute_start_seconds,
            clip_duration_seconds=clip_duration_seconds,
        )
        if rel is None:
            continue
        text = _normalize_text(seg.get("text"))
        if not text:
            continue
        start, end = rel
        blocks = split_text_into_blocks(
            text,
            font_file=font_file,
            font_size=font_size,
            available_units=avail_units,
            max_lines=max_lines,
        )
        if not blocks:
            continue
        # Reparte el intervalo del segmento entre los bloques, proporcional al
        # numero de palabras, para que cada bloque sea legible a lo largo de su
        # tiempo. No se descarta ni se modifica el texto original.
        counts = [len(b.split()) for b in blocks]
        total_words = sum(counts) or 1
        cursor = start
        for block, count in zip(blocks, counts):
            share = (end - start) * (count / total_words)
            block_end = cursor + share if count else cursor
            raw.append(
                Cue(
                    start_seconds=cursor,
                    end_seconds=block_end,
                    text=_escape_ass(block),
                    source_segment_id=seg.get("segment_id"),
                )
            )
            cursor = block_end

    raw.sort(key=lambda c: (c.start_seconds, c.end_seconds))

    # Resolver solapamientos: el cue siguiente no empieza antes de que acabe el
    # anterior. Si el ajuste anula el cue, se conserva pegado al anterior.
    cues: List[Cue] = []
    prev_end = 0.0
    for cue in raw:
        start = max(cue.start_seconds, prev_end)
        end = max(cue.end_seconds, start)
        if end <= start:
            continue
        if (end - start) < min_cue_seconds:
            continue
        cues.append(Cue(start, end, cue.text, cue.source_segment_id))
        prev_end = end
    return cues


# ---------------------------------------------------------------------------
# Serializacion ASS
# ---------------------------------------------------------------------------


def _format_ass_time(seconds: float) -> str:
    """Formatea segundos como `H:MM:SS.cc` (centesimas) para ASS."""
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


def build_ass(
    cues: Sequence[Cue],
    *,
    width: int,
    height: int,
    font_name: str = FONT_NAME_DEFAULT,
    font_size: int = FONT_SIZE_DEFAULT,
    margin_bottom: int = MARGIN_BOTTOM_DEFAULT,
    margin_lr: int = MARGIN_LR_DEFAULT,
    outline: int = OUTLINE_WIDTH_DEFAULT,
    shadow: int = SHADOW_WIDTH_DEFAULT,
) -> str:
    """Genera el contenido de un archivo ASS a partir de las cues.

    Estilo de alto contraste: texto blanco, contorno negro grueso y sombra. El
    ajuste de linea ya viene precalculado con metricas reales, por lo que se usa
    `WrapStyle: 0` solo como red de seguridad.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
        f"PlayResX: {int(width)}\n"
        f"PlayResY: {int(height)}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{int(font_size)},{PRIMARY_COLOUR},"
        f"{PRIMARY_COLOUR},{OUTLINE_COLOUR},{BACK_COLOUR},"
        f"-1,0,0,0,100,100,0,0,1,{int(outline)},{int(shadow)},2,"
        f"{int(margin_lr)},{int(margin_lr)},{int(margin_bottom)},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    lines = [header]
    for cue in cues:
        lines.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n".format(
                start=_format_ass_time(cue.start_seconds),
                end=_format_ass_time(cue.end_seconds),
                text=cue.text,
            )
        )
    return "".join(lines)


def write_ass(
    path: Path,
    cues: Sequence[Cue],
    *,
    width: int,
    height: int,
    font_name: str = FONT_NAME_DEFAULT,
    font_size: int = FONT_SIZE_DEFAULT,
    margin_bottom: int = MARGIN_BOTTOM_DEFAULT,
    margin_lr: int = MARGIN_LR_DEFAULT,
    outline: int = OUTLINE_WIDTH_DEFAULT,
    shadow: int = SHADOW_WIDTH_DEFAULT,
) -> Path:
    """Escribe el archivo ASS en `path` (UTF-8) y lo devuelve."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = build_ass(
        cues,
        width=width,
        height=height,
        font_name=font_name,
        font_size=font_size,
        margin_bottom=margin_bottom,
        margin_lr=margin_lr,
        outline=outline,
        shadow=shadow,
    )
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Render con subtitulos integrados
# ---------------------------------------------------------------------------


def _escape_filter_path(path: Path) -> str:
    """Normaliza una ruta para usarla dentro del filtro FFmpeg `ass`.

    El filtro interpreta `:` y `\\` como separadores, y `'` como cierre de
    comillas. En Windows se usa la forma con unidad escapada (`C\\:/...`) y se
    escapan comillas simples. Se evita interpolar en una shell: los argumentos
    van como lista, pero el filtro en si sigue necesitando el escapado.
    """
    posix = Path(path).resolve().as_posix()
    # En Windows, "C:/..." -> "C\:/..."
    if len(posix) >= 2 and posix[1] == ":":
        posix = posix[0] + r"\:" + posix[2:]
    posix = posix.replace("'", r"\'")
    return posix


def render_with_subtitles(
    *,
    source_path: Path,
    output_path: Path,
    cues: Sequence[Cue],
    width: int,
    height: int,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    timeout: float = 600.0,
    font_name: str = FONT_NAME_DEFAULT,
    font_size: int = FONT_SIZE_DEFAULT,
    margin_bottom: int = MARGIN_BOTTOM_DEFAULT,
    margin_lr: int = MARGIN_LR_DEFAULT,
    outline: int = OUTLINE_WIDTH_DEFAULT,
    shadow: int = SHADOW_WIDTH_DEFAULT,
) -> Dict[str, Any]:
    """Renderiza `source_path` con subtitulos quemados sobre un nuevo MP4.

    El video se recodifica (necesario para integrar los subtitulos); el audio se
    conserva sin recodificar para preservar la sincronizacion. Delega en el
    renderer existente para el corte y la verificacion con FFprobe.
    """
    # Import perezoso para evitar ciclo con renderer (renderer no conoce subtitles).
    from .renderer import _run, _stream_of, probe  # type: ignore

    source_path = Path(source_path)
    output_path = Path(output_path)
    if not source_path.exists() or source_path.stat().st_size == 0:
        raise RenderingSourceError(
            f"Clip fuente inexistente o vacio: {source_path}",
            details={"source_path": str(source_path)},
        )

    source_probe = probe(source_path, ffprobe=ffprobe, timeout=timeout)
    src_v = _stream_of(source_probe, "video")
    src_a = _stream_of(source_probe, "audio")
    if src_v is None:
        raise RenderingSourceError(
            f"El clip fuente no tiene stream de video: {source_path}",
            details={"source_path": str(source_path)},
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = output_path.with_suffix(".ass")
    write_ass(
        ass_path,
        cues,
        width=width,
        height=height,
        font_name=font_name,
        font_size=font_size,
        margin_bottom=margin_bottom,
        margin_lr=margin_lr,
        outline=outline,
        shadow=shadow,
    )

    filter_arg = f"ass='{_escape_filter_path(ass_path)}'"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source_path),
        "-vf",
        filter_arg,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    proc = _run(cmd, timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RenderingSourceError(
            f"FFmpeg fallo integrando subtitulos en {output_path}: {err}",
            details={"command": cmd},
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RenderingSourceError(
            f"Salida con subtitulos no generada o vacia: {output_path}",
            details={"output_path": str(output_path)},
        )

    out_probe = probe(output_path, ffprobe=ffprobe, timeout=timeout)
    v = _stream_of(out_probe, "video")
    a = _stream_of(out_probe, "audio")
    out_duration = float(out_probe.get("format", {}).get("duration", 0.0) or 0.0)
    return {
        "output_path": str(output_path),
        "source_path": str(source_path),
        "ass_path": str(ass_path),
        "cue_count": len(cues),
        "actual_duration_seconds": out_duration,
        "size_bytes": output_path.stat().st_size,
        "width": v.get("width") if v else None,
        "height": v.get("height") if v else None,
        "video_codec": v.get("codec_name") if v else None,
        "audio_codec": a.get("codec_name") if a else None,
        "has_audio": a is not None,
        "source_had_audio": src_a is not None,
        "container": out_probe.get("format", {}).get("format_name"),
    }
