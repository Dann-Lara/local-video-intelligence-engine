"""Motion graphics reutilizables generados como cues ASS (Fase 5E, pasos 1-2).

Objetivo de esta fase: disponer de una **biblioteca de animaciones** que se
integren con la arquitectura actual del renderizador (FFmpeg + ASS) sin
introducir un motor nuevo. Este modulo **no renderiza video**: solo construye
cues ASS animados (override tags) que, en una fase posterior y sobre una copia
aislada, se quemarian con un segundo filtro `ass=` de FFmpeg.

Decisiones y limites (explicitas):

- ARQUITECTURA: se mantiene FFmpeg + ASS. No se usa Remotion, Hyperframes ni
  SVG. ASS soporta movimiento y escala con override tags (`\\move`, `\\t`,
  `\\fad`, `\\fscx`/`\\fscy`), suficiente para enfasis tipografico, indicadores
  y CTAs simples. Los trazos/circulos que ASS no reproduce bien quedan como
  extension SVG **futura** (no implementada aqui).
- CONTEXTO: todo evento se ancla a **segmentos reales** de la transcripcion o a
  un intervalo explicito provisto por el plan. Nunca se inventan datos ni se
  simulan timestamps por palabra: la transcripcion de este proyecto tiene
  `words == []`, asi que los eventos viven al nivel de segmento.
- ZONAS SEGURAS: los subtitulos ocupan la franja inferior (en 5C.1, 340 px desde
  abajo). Los efectos se colocan en franjas superiores y jamas se superponen al
  bloque de subtitulos. Todos los textos se miden con metricas reales de fuente
  (PIL) y se validan contra los margenes horizontales.
- OPCIONALIDAD: cada efecto existe como catalogo; el plan decide cuales se
  emiten. Los CTAs (like, suscripcion, compartir) son **opt-in**: no aparecen
  todos automaticamente.
- NO DESTRUCTIVO: este modulo no toca transcripcion, esquemas, contratos ni
  artefactos de las fases 5A-5D.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .subtitles import (
    CALIBRATION_FACTOR,
    FONT_FILE_DEFAULT,
    _format_ass_time,
    _load_font,
    _measure_width,
)

# ---------------------------------------------------------------------------
# Constantes de maquetacion y zonas seguras
# ---------------------------------------------------------------------------

# Geometria objetivo (identica a la usada en 5C.1 para los subtitulos).
WIDTH_DEFAULT = 1080
HEIGHT_DEFAULT = 1920
MARGIN_LR_DEFAULT = 70
# Los subtitulos se anclan a MARGIN_BOTTOM_DEFAULT=340 desde el borde inferior
# (Alignment 2 = abajo-centro). Los subtitulos pueden tener hasta 2 lineas de
# ~Fontsize 96 (aprox 2*96*1.2 = ~230 unidades de guion sobre el borde). Se
# reserva un bloque de subtitulos de SUBTITLE_BAND_PX de alto pegado al borde
# inferior; nada de motion entra en esa franja.
SUBTITLE_BAND_PX = 460

# Colores ASS (formato &HAABBGGRR en orden BGR). Se reutiliza el estilo blanco
# con contorno negro de 5C.1 para coherencia visual.
MOTION_TEXT_COLOUR = "&H00FFFFFF"
MOTION_OUTLINE_COLOUR = "&H00000000"
ACCENT_COLOUR = "&H0000D7FF"  # ambar/dorado (BGR) para enfasis y flechas

# Franjas verticales de la zona segura (centro de la caja de texto).
# x=540 es el centro horizontal de un lienzo de 1080.
ZONE_CENTER_X = 540
# Gancho superior: cerca del borde superior pero con margen.
ZONE_TOP_Y = 300
# Enfasis: tercio superior-medio, por encima del sujeto central.
ZONE_UPPER_MID_Y = 560
# Indicador/flecha: franja media-alta, nunca sobre los subtitulos.
ZONE_INDICATOR_Y = 720
# CTA: por encima del bloque de subtitulos.
ZONE_CTA_Y = 1180

# Fuente por defecto para motion (misma que subtitulos 5C.1).
FONT_NAME_DEFAULT = "Arial Black"
FONT_SIZE_DEFAULT = 84
MIN_FONT_SIZE = 40

# Duraciones por defecto (segundos) de cada animacion.
TYPIC_HOLD_DEFAULT = 1.6
INDICATOR_HOLD_DEFAULT = 1.4
CTA_HOLD_DEFAULT = 2.6


# ---------------------------------------------------------------------------
# Catalogo de tipos de efecto
# ---------------------------------------------------------------------------


class EffectType:
    """Identificadores estables de los tipos de efecto soportados."""

    TYPOGRAPHY = "typography"      # enfasis tipografico animado (palabra clave)
    ARROW = "arrow"               # flecha / indicador que senala
    CTA_LIKE = "cta_like"         # pulso de like
    CTA_SUBSCRIBE = "cta_subscribe"  # suscripcion + campana
    CTA_SHARE = "cta_share"       # compartir


ALL_EFFECT_TYPES = (
    EffectType.TYPOGRAPHY,
    EffectType.ARROW,
    EffectType.CTA_LIKE,
    EffectType.CTA_SUBSCRIBE,
    EffectType.CTA_SHARE,
)

#: Efectos que son llamadas a la accion. Son **opt-in**: no se emiten salvo que
#: el plan los pida de forma explicita.
CTA_EFFECT_TYPES = (
    EffectType.CTA_LIKE,
    EffectType.CTA_SUBSCRIBE,
    EffectType.CTA_SHARE,
)

#: Etiquetas de texto por defecto para los CTA. Son textos **funcionales**
#: (verbos de accion), no afirmaciones sobre el contenido: no inventan datos.
CTA_DEFAULT_LABELS = {
    EffectType.CTA_LIKE: "LIKE",
    EffectType.CTA_SUBSCRIBE: "SUBSCRIBE",
    EffectType.CTA_SHARE: "SHARE",
}

#: Glifo de flecha por defecto. Se usa U+2192 (RIGHTWARDS ARROW) porque es el
#: unico glifo de flecha presente en la fuente configurada (Arial Black) y
#: verificado como renderizable por libass. NO usar U+27A4 ni U+27A1: no existen
#: en Arial Black y libass haria fallback de fuente o mostraria un hueco.
ARROW_GLYPH = "\u2192"  # →

#: Glifos por defecto para los CTA (se dibujan con la fuente). Todos los glifos
#: listados aqui estan **verificados** como presentes en Arial Black (ariblk.ttf)
#: y renderizables por libass. Si la fuente no incluyera un glifo, la etiqueta de
#: texto sigue siendo legible, pero se evita introducir glifos incompatibles.
CTA_DEFAULT_GLYPHS = {
    EffectType.CTA_LIKE: "\u2665",       # corazon (U+2665, presente)
    EffectType.CTA_SUBSCRIBE: "\u25BC",  # triangulo abajo (U+25BC, presente)
    EffectType.CTA_SHARE: ARROW_GLYPH,   # flecha derecha (U+2192, presente)
}


# ---------------------------------------------------------------------------
# Modelos de datos
# ---------------------------------------------------------------------------


@dataclass
class MotionEvent:
    """Un evento de motion, resuelto o por resolver, en tiempo relativo.

    `start_seconds`/`end_seconds` son relativos al inicio del clip. Si el evento
    se construye desde un segmento (`resolve_events_from_segments`), estos
    tiempos provienen del segmento real, no de una suposicion.
    """

    effect_type: str
    start_seconds: float
    end_seconds: float
    text: str = ""
    position: str = "center"          # "top" | "upper_mid" | "indicator" | "cta"
    source_segment_id: Optional[str] = None
    accent: bool = False
    # Parametros de animacion opcionales (sobrescriben los del tipo de efecto).
    scale_from: Optional[int] = None
    slide_px: Optional[int] = None
    easing: str = "ease_out"
    notes: str = ""


@dataclass
class MotionPlan:
    """Coleccion de eventos a emitir, con metadatos de origen."""

    events: List[MotionEvent] = field(default_factory=list)
    width: int = WIDTH_DEFAULT
    height: int = HEIGHT_DEFAULT
    margin_lr: int = MARGIN_LR_DEFAULT
    font_file: str = FONT_FILE_DEFAULT
    font_size: int = FONT_SIZE_DEFAULT
    source: str = ""


@dataclass
class MotionCue:
    """Cue ASS animado listo para serializar (capa superior, Layer 1)."""

    start_seconds: float
    end_seconds: float
    text: str                         # texto con override tags ASS
    style: str                        # nombre del estilo ASS
    effect_type: str
    source_segment_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------


class MotionValidationError(ValueError):
    """Error de validacion de un evento/plan de motion."""


def _validate_interval(start: float, end: float, *, clip_duration: float) -> None:
    if not (math.isfinite(start) and math.isfinite(end)):
        raise MotionValidationError(f"Intervalo no finito: {start}, {end}")
    if start < 0:
        raise MotionValidationError(f"start_seconds negativo: {start}")
    if end <= start:
        raise MotionValidationError(f"Intervalo invalido: start={start} end={end}")
    if clip_duration > 0 and end > clip_duration + 1e-6:
        raise MotionValidationError(
            f"evento excede la duracion del clip: end={end} > {clip_duration}"
        )


def _zone_center_y(position: str) -> int:
    mapping = {
        "top": ZONE_TOP_Y,
        "upper_mid": ZONE_UPPER_MID_Y,
        "indicator": ZONE_INDICATOR_Y,
        "cta": ZONE_CTA_Y,
    }
    if position not in mapping:
        raise MotionValidationError(f"Posicion desconocida: {position}")
    return mapping[position]


def _asset_top_limit(height: int) -> int:
    """Limite inferior (y) que un efecto de motion no debe rebasar.

    Es el borde superior del bloque reservado para subtitulos. Garantiza que
    ningun efecto de motion se superponga a los subtitulos de 5C.1.
    """
    return height - SUBTITLE_BAND_PX


# ---------------------------------------------------------------------------
# Easing (curvas de aceleracion para `\\t`)
# ---------------------------------------------------------------------------

# ASS `\t(...)` acepta un parametro de aceleracion (exponente). Valores tipicos:
#   1.0  lineal
#   >1.0 comienza lento (ease-in)
#   <1.0 comienza rapido (ease-out)
# `\t` sin acelerador equivale a aceleracion 1.0.
_EASING_ACCEL = {
    "linear": 1.0,
    "ease_in": 2.0,
    "ease_out": 0.6,
    "ease_in_out": 1.0,  # se aproxima con una sola curva suave
}
# `\t` con dos extremos permite aceleracion; usamos un exponente moderado para
# ease_in_out sin complicar la sintaxis (ASS no tiene cubic-bezier nativo).
_EASING_ACCEL_IN_OUT = 1.4


def _easing_accel(easing: str) -> float:
    if easing == "ease_in_out":
        return _EASING_ACCEL_IN_OUT
    return _EASING_ACCEL.get(easing, 1.0)


# ---------------------------------------------------------------------------
# Construccion de cues animados (override tags ASS)
# ---------------------------------------------------------------------------


def _escape_ass_text(text: str) -> str:
    """Escapa el texto para el bloque Dialogue de ASS."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", r"\N")
    )


def _fit_font_size(
    text: str,
    *,
    font_file: str,
    font_size: int,
    max_units: float,
) -> int:
    """Reduce el tamanio de fuente si el texto excede el ancho util.

    Devuelve el tamanio efectivo (>= MIN_FONT_SIZE). Si la font no se puede
    medir, devuelve un tamanio conservador estimado por longitud.
    """
    font, ok = _load_font(font_file, font_size)
    if not ok:
        # Estimacion conservadora por caracteres.
        max_font = max(MIN_FONT_SIZE, int(max_units / max(1, len(text)) / 0.55))
        return min(font_size, max_font)
    size = font_size
    while size > MIN_FONT_SIZE:
        f, ok = _load_font(font_file, size)
        if not ok:
            break
        if _measure_width(text, f) <= max_units:
            return size
        size -= 2
    return max(MIN_FONT_SIZE, size)


def _pulse_tags(
    *, start_ms: int, total_ms: int, peak: int, accel: float
) -> str:
    """Genera un pulso `100 -> peak -> 100` dentro de [start_ms, total_ms].

    ASS no tiene keyframes: se encadena dos `\\t` consecutivos (uno para subir,
    otro para bajar). Si el margen es demasiado corto, se omite el pulso.
    """
    if total_ms - start_ms < 250:
        return ""
    up_end = start_ms + int((total_ms - start_ms) * 0.45)
    down_start = up_end
    return (
        rf"\t({start_ms},{up_end},{accel}\fscx{peak}\fscy{peak})"
        rf"\t({down_start},{total_ms},{accel}\fscx100\fscy100)"
    )


def _typography_tags(
    event: MotionEvent,
    *,
    width: int,
    height: int,
    easing: str,
) -> str:
    """Override tags para enfasis tipografico (escala + deslizamiento)."""
    cx = ZONE_CENTER_X
    cy = _zone_center_y(event.position)
    scale_from = event.scale_from if event.scale_from is not None else 40
    slide = event.slide_px if event.slide_px is not None else 0
    accel = _easing_accel(easing)
    total_ms = max(1, int((event.end_seconds - event.start_seconds) * 1000))
    # Entrada: desde escala reducida (y desplazamiento opcional) al estado final.
    if slide:
        motion = rf"\move({cx + slide},{cy},{cx},{cy},0,{min(400, total_ms)})"
    else:
        motion = rf"\pos({cx},{cy})"
    tags = (
        r"\an5"
        rf"\fscx{scale_from}\fscy{scale_from}"
        f"{motion}"
        rf"\t(0,{min(400, total_ms)},{accel}\fscx100\fscy100)"
        r"\fad(180,120)"
    )
    return tags


def _indicator_tags(
    event: MotionEvent,
    *,
    width: int,
    height: int,
    easing: str,
) -> str:
    """Override tags para indicador/flecha (deslizamiento + pulso)."""
    cx = ZONE_CENTER_X
    cy = _zone_center_y(event.position)
    slide = event.slide_px if event.slide_px is not None else 90
    accel = _easing_accel(easing)
    total_ms = max(1, int((event.end_seconds - event.start_seconds) * 1000))
    entry_ms = min(400, total_ms)
    pulse = _pulse_tags(
        start_ms=entry_ms, total_ms=total_ms, peak=112, accel=accel
    )
    tags = (
        r"\an5"
        rf"\move({cx - slide},{cy},{cx},{cy},0,{entry_ms})"
        f"{pulse}"
        r"\fad(120,120)"
    )
    return tags


def _cta_tags(
    event: MotionEvent,
    *,
    width: int,
    height: int,
    easing: str,
) -> str:
    """Override tags para CTA (entrada suave desde abajo + pulso)."""
    cx = ZONE_CENTER_X
    cy = _zone_center_y(event.position)
    accel = _easing_accel(easing)
    total_ms = max(1, int((event.end_seconds - event.start_seconds) * 1000))
    entry_ms = min(300, total_ms)
    rise = 40
    pulse = _pulse_tags(
        start_ms=entry_ms, total_ms=total_ms, peak=105, accel=accel
    )
    tags = (
        r"\an5"
        rf"\move({cx},{cy + rise},{cx},{cy},0,{entry_ms})"
        r"\fad(200,200)"
        f"{pulse}"
    )
    return tags


_TAG_BUILDERS = {
    EffectType.TYPOGRAPHY: _typography_tags,
    EffectType.ARROW: _indicator_tags,
    EffectType.CTA_LIKE: _cta_tags,
    EffectType.CTA_SUBSCRIBE: _cta_tags,
    EffectType.CTA_SHARE: _cta_tags,
}


def _style_name_for(effect_type: str) -> str:
    if effect_type == EffectType.TYPOGRAPHY:
        return "MotionTypo"
    if effect_type == EffectType.ARROW:
        return "MotionArrow"
    if effect_type in CTA_EFFECT_TYPES:
        return "MotionCTA"
    raise MotionValidationError(f"Tipo de efecto desconocido: {effect_type}")


def build_motion_cues(
    plan: MotionPlan,
    *,
    clip_duration: float,
) -> List[MotionCue]:
    """Convierte los eventos del plan en cues ASS animados y validados.

    Reglas:
    - Valida los intervalos contra la duracion del clip.
    - Garantiza que los efectos no entren en el bloque de subtitulos.
    - Ajusta el tamanio de fuente para no rebasar los margenes horizontales.
    - Cualquier CTA solo aparece si fue incluido explicitamente en el plan.
    """
    width = int(plan.width)
    height = int(plan.height)
    margin_lr = int(plan.margin_lr)
    if not (0 < margin_lr < width / 2):
        raise MotionValidationError(f"margin_lr invalido: {margin_lr}")
    max_units = (width - 2 * margin_lr) / CALIBRATION_FACTOR

    cues: List[MotionCue] = []
    for event in plan.events:
        if event.effect_type not in ALL_EFFECT_TYPES:
            raise MotionValidationError(
                f"Tipo de efecto no soportado: {event.effect_type}"
            )
        _validate_interval(
            event.start_seconds, event.end_seconds, clip_duration=clip_duration
        )

        position = event.position
        if position not in ("top", "upper_mid", "indicator", "cta"):
            # Deriva una posicion por defecto segun el tipo de efecto.
            position = _default_position(event.effect_type)
        cy = _zone_center_y(position)

        # Texto y tamano efectivo.
        raw_text = event.text
        if event.effect_type in CTA_EFFECT_TYPES and not raw_text:
            raw_text = CTA_DEFAULT_LABELS.get(event.effect_type, "")
        if event.effect_type == EffectType.ARROW and not raw_text:
            raw_text = ARROW_GLYPH

        font_size = _fit_font_size(
            raw_text,
            font_file=plan.font_file,
            font_size=plan.font_size,
            max_units=max_units,
        )
        # Estimar la altura de la caja (para garantizar zona segura). ~1.25 em
        # por linea; todos los efectos son de una linea (se recortan si no).
        box_half = int(font_size * 1.25 / 2)
        label_pos = position
        if cy + box_half > _asset_top_limit(height):
            # Si aun asi se acercara a la franja de subtitulos, se sube la
            # posicion a una franja segura superior.
            label_pos = "upper_mid"

        # El texto con glifo de CTA (etiqueta + icono) es legible aunque la
        # fuente no tenga el glifo.
        display_text = raw_text
        if event.effect_type in CTA_EFFECT_TYPES:
            glyph = CTA_DEFAULT_GLYPHS.get(event.effect_type, "")
            if glyph:
                display_text = f"{glyph} {raw_text}"

        eff_event = MotionEvent(
            effect_type=event.effect_type,
            start_seconds=event.start_seconds,
            end_seconds=event.end_seconds,
            text=display_text,
            position=label_pos,
            source_segment_id=event.source_segment_id,
            accent=event.accent,
            scale_from=event.scale_from,
            slide_px=event.slide_px,
            easing=event.easing,
        )

        builder = _TAG_BUILDERS[event.effect_type]
        tags = builder(eff_event, width=width, height=height, easing=event.easing)
        style = _style_name_for(event.effect_type)
        text = "{" + tags + "}" + _escape_ass_text(display_text)

        cues.append(
            MotionCue(
                start_seconds=float(event.start_seconds),
                end_seconds=float(event.end_seconds),
                text=text,
                style=style,
                effect_type=event.effect_type,
                source_segment_id=event.source_segment_id,
            )
        )

    # Sanea solapamientos de intervalos dentro del mismo carril/estilo: si dos
    # eventos del mismo tipo se pisan, se recorta el primero para evitar parpadeo.
    cues.sort(key=lambda c: (c.start_seconds, c.end_seconds))
    return cues


def _default_position(effect_type: str) -> str:
    if effect_type == EffectType.TYPOGRAPHY:
        return "top"
    if effect_type == EffectType.ARROW:
        return "indicator"
    return "cta"


# ---------------------------------------------------------------------------
# Serializacion ASS (capa superior)
# ---------------------------------------------------------------------------


def build_motion_ass(
    cues: Sequence[MotionCue],
    *,
    width: int = WIDTH_DEFAULT,
    height: int = HEIGHT_DEFAULT,
    font_name: str = FONT_NAME_DEFAULT,
    margin_lr: int = MARGIN_LR_DEFAULT,
) -> str:
    """Genera un ASS independiente con los efectos de motion.

    Los cues van en la capa 1 (por encima de los subtitulos, que usan la capa
    por defecto 0) y se centran por `\\an5` + `\\pos`, por lo que no dependen de
    los margenes del estilo. Se incluyen tres estilos: enfasis, indicador y CTA.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
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
        f"Style: MotionTypo,{font_name},{int(FONT_SIZE_DEFAULT)},"
        f"{MOTION_TEXT_COLOUR},{MOTION_TEXT_COLOUR},{MOTION_OUTLINE_COLOUR},"
        f"{MOTION_TEXT_COLOUR},-1,0,0,0,100,100,0,0,1,6,3,5,"
        f"{int(margin_lr)},{int(margin_lr)},80,1\n"
        f"Style: MotionArrow,{font_name},{int(FONT_SIZE_DEFAULT)},"
        f"{ACCENT_COLOUR},{ACCENT_COLOUR},{MOTION_OUTLINE_COLOUR},"
        f"{MOTION_TEXT_COLOUR},-1,0,0,0,100,100,0,0,1,5,2,5,"
        f"{int(margin_lr)},{int(margin_lr)},80,1\n"
        f"Style: MotionCTA,{font_name},{int(FONT_SIZE_DEFAULT)},"
        f"{MOTION_TEXT_COLOUR},{MOTION_TEXT_COLOUR},{MOTION_OUTLINE_COLOUR},"
        f"{MOTION_TEXT_COLOUR},-1,0,0,0,100,100,0,0,1,5,3,5,"
        f"{int(margin_lr)},{int(margin_lr)},80,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    out = [header]
    for cue in cues:
        out.append(
            "Dialogue: 1,{start},{end},{style},,0,0,0,,{text}\n".format(
                start=_format_ass_time(cue.start_seconds),
                end=_format_ass_time(cue.end_seconds),
                style=cue.style,
                text=cue.text,
            )
        )
    return "".join(out)


def write_motion_ass(
    path,
    cues: Sequence[MotionCue],
    *,
    width: int = WIDTH_DEFAULT,
    height: int = HEIGHT_DEFAULT,
    font_name: str = FONT_NAME_DEFAULT,
    margin_lr: int = MARGIN_LR_DEFAULT,
):
    """Escribe el ASS de motion en `path` (UTF-8) y lo devuelve."""
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_motion_ass(
            cues, width=width, height=height, font_name=font_name, margin_lr=margin_lr
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Resolucion contextual: eventos anclados a segmentos reales
# ---------------------------------------------------------------------------


def find_segment(
    segments: Sequence[Dict[str, Any]], segment_id: str
) -> Optional[Dict[str, Any]]:
    for s in segments:
        if s.get("segment_id") == segment_id:
            return s
    return None


def resolve_event_window(
    segments: Sequence[Dict[str, Any]],
    *,
    anchor: Optional[str],
    absolute_start_seconds: float,
    clip_duration_seconds: float,
    start_seconds: Optional[float] = None,
    end_seconds: Optional[float] = None,
    hold_seconds: Optional[float] = None,
) -> tuple:
    """Resuelve la ventana (relativa) de un evento de motion.

    Precedencia:
    1. Si se dan `start_seconds`/`end_seconds` explicitos, se usan (relativos).
    2. Si `anchor` es un `segment_id` real, la ventana es el intervalo de ese
       segmento convertido a tiempo relativo, opcionalmente recortado a
       `hold_seconds`.
    3. Si no, error: no se inventan tiempos.

    Devuelve (start, end). Lanza MotionValidationError si nada es valido.
    """
    if start_seconds is not None and end_seconds is not None:
        _validate_interval(start_seconds, end_seconds, clip_duration=clip_duration_seconds)
        return float(start_seconds), float(end_seconds)

    if not anchor:
        raise MotionValidationError(
            "Evento sin ancla ni intervalo explicito: no se inventan tiempos"
        )
    seg = find_segment(segments, anchor)
    if seg is None:
        raise MotionValidationError(f"Segmento ancla inexistente: {anchor}")
    try:
        abs_start = float(seg["start_seconds"])
        abs_end = float(seg["end_seconds"])
    except (TypeError, ValueError, KeyError) as exc:
        raise MotionValidationError(f"Segmento ancla invalido: {anchor}") from exc

    rel_start = max(0.0, abs_start - absolute_start_seconds)
    rel_end = min(clip_duration_seconds, abs_end - absolute_start_seconds)
    if rel_end <= rel_start:
        raise MotionValidationError(
            f"El segmento ancla {anchor} no solapa el clip"
        )
    if hold_seconds is not None and hold_seconds > 0:
        rel_end = min(rel_end, rel_start + float(hold_seconds))
    return rel_start, rel_end


def event_from_spec(
    spec: Dict[str, Any],
    *,
    segments: Sequence[Dict[str, Any]],
    absolute_start_seconds: float,
    clip_duration_seconds: float,
) -> MotionEvent:
    """Construye un MotionEvent a partir de una especificacion del plan.

    `spec` fields: `effect_type` (req), `anchor` (segment_id) o
    `start_seconds`+`end_seconds`, `text` opcional, `position` opcional,
    `hold_seconds`, `scale_from`, `slide_px`, `easing`, `accent`.
    """
    effect_type = spec.get("effect_type")
    if effect_type not in ALL_EFFECT_TYPES:
        raise MotionValidationError(
            f"effect_type no soportado: {effect_type!r}"
        )
    start, end = resolve_event_window(
        segments,
        anchor=spec.get("anchor"),
        absolute_start_seconds=absolute_start_seconds,
        clip_duration_seconds=clip_duration_seconds,
        start_seconds=spec.get("start_seconds"),
        end_seconds=spec.get("end_seconds"),
        hold_seconds=spec.get("hold_seconds"),
    )
    return MotionEvent(
        effect_type=effect_type,
        start_seconds=start,
        end_seconds=end,
        text=str(spec.get("text", "") or ""),
        position=str(spec.get("position", "") or "") or _default_position(effect_type),
        source_segment_id=spec.get("anchor"),
        accent=bool(spec.get("accent", False)),
        scale_from=spec.get("scale_from"),
        slide_px=spec.get("slide_px"),
        easing=str(spec.get("easing", "ease_out") or "ease_out"),
        notes=str(spec.get("notes", "") or ""),
    )


def build_plan_from_spec(
    specs: Sequence[Dict[str, Any]],
    *,
    segments: Sequence[Dict[str, Any]],
    absolute_start_seconds: float,
    clip_duration_seconds: float,
    width: int = WIDTH_DEFAULT,
    height: int = HEIGHT_DEFAULT,
    margin_lr: int = MARGIN_LR_DEFAULT,
    font_file: str = FONT_FILE_DEFAULT,
    font_size: int = FONT_SIZE_DEFAULT,
    source: str = "",
) -> MotionPlan:
    """Construye un MotionPlan resolviendo cada especificacion contextualmente."""
    events = [
        event_from_spec(
            spec,
            segments=segments,
            absolute_start_seconds=absolute_start_seconds,
            clip_duration_seconds=clip_duration_seconds,
        )
        for spec in specs
    ]
    return MotionPlan(
        events=events,
        width=width,
        height=height,
        margin_lr=margin_lr,
        font_file=font_file,
        font_size=font_size,
        source=source,
    )
