"""Escritura ASS para el camino ffmpeg_libass (seccion 31)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.subtitles import ass  # noqa: E402
from src.subtitles.cues import LINE_BREAK, SubtitleCue  # noqa: E402


def _cues() -> list[SubtitleCue]:
    return [
        SubtitleCue(start=0.0, end=1.5, text="hola"),
        SubtitleCue(start=1.5, end=3.0, text=f"segunda{LINE_BREAK}linea"),
    ]


def test_cabecera_con_resolucion_y_estilo():
    text = ass.render_ass(_cues(), width=1080, height=1920)
    assert "PlayResX: 1080" in text
    assert "PlayResY: 1920" in text
    assert "WrapStyle: 0" in text
    assert "Format: Name, Fontname, Fontsize" in text


def _alignment(text: str) -> str:
    style = next(line for line in text.splitlines() if line.startswith("Style: Default"))
    # ..., Alignment, MarginL, MarginR, MarginV, Encoding
    return style.split(",")[-5]


def test_posicion_lower_center_usa_alignment_2():
    text = ass.render_ass(_cues(), width=1080, height=1920, position="lower_center")
    assert _alignment(text) == "2"


def test_posicion_center_usa_alignment_5():
    text = ass.render_ass(_cues(), width=1080, height=1920, position="center")
    assert _alignment(text) == "5"


def test_eventos_con_tiempos_y_saltos_de_linea():
    text = ass.render_ass(_cues(), width=1080, height=1920)
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,hola" in text
    assert "Dialogue: 0,0:00:01.50,0:00:03.00,Default,,0,0,0,,segunda\\Nlinea" in text


def test_escapa_llaves_del_texto():
    cue = SubtitleCue(start=0.0, end=1.0, text="voz {entre llaves}")
    text = ass.render_ass([cue], width=1080, height=1920)
    assert "voz \\{entre llaves\\}" in text


def test_sin_cues_solo_cabecera():
    text = ass.render_ass([], width=1080, height=1920)
    assert text == "[Script Info]\nScriptType: v4.00+\n"


def test_write_ass_escribe_en_disco(tmp_path):
    target = ass.write_ass(_cues(), tmp_path / "clip.ass", width=1080, height=1920)
    assert target.is_file()
    assert "PlayResX: 1080" in target.read_text(encoding="utf-8")


def test_tamano_de_letra_escala_con_el_ancho():
    def fontsize(width: int) -> int:
        text = ass.render_ass(_cues(), width=width, height=width * 2)
        style = next(line for line in text.splitlines() if line.startswith("Style: Default"))
        return int(style.split(",")[2])

    assert fontsize(720) < fontsize(1080)
    assert fontsize(1080) == max(40, round(1080 * 0.05))


@pytest.mark.parametrize("position", ["lower_third", "burned_in"])
def test_posiciones_especiales_anclan_abajo(position):
    text = ass.render_ass(_cues(), width=1080, height=1920, position=position)
    assert _alignment(text) == "2"