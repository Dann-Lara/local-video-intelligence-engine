"""Cues de subtitulo: de la transcripcion a carteles por ventana (seccion 31)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.subtitles import cues  # noqa: E402


def _transcript(*word_tuples: tuple[float, float, str], duration: float = 10.0) -> dict:
    """Transcript con una sola ventana de palabras: (start, end, word)."""
    words = [
        {"word": word, "start": start, "end": end, "probability": 0.9}
        for start, end, word in word_tuples
    ]
    return {
        "schema_version": "1.0",
        "language": "es",
        "duration": duration,
        "segments": [{"id": 0, "start": 0.0, "end": duration, "text": " ".join(w["word"] for w in words), "words": words}],
    }


def test_recorta_palabras_a_la_ventana_y_devuelve_tiempos_relativos():
    tr = _transcript(
        (0.0, 0.4, "fuera"),
        (0.9, 1.2, "esto"),
        (1.2, 1.5, "es"),
        (3.8, 4.3, "una"),
        (4.3, 4.6, "cerrada"),
    )
    result = cues.build_cues(tr, 1.0, 4.0)
    assert len(result) == 1
    cue = result[0]
    # "esto" se recorta (1.0 -> rel 0.0), "una" tambien (hasta rel 3.0).
    assert cue.text == "esto es una"
    assert cue.start == pytest.approx(0.0)
    assert cue.end == pytest.approx(3.0)


def test_el_ventana_fuera_de_rango_no_produce_cues():
    tr = _transcript((5.0, 5.5, "lejos"))
    assert cues.build_cues(tr, 1.0, 4.0) == []


def test_max_lines_cierra_el_cartel_y_abre_otro():
    tr = _transcript(
        (0.0, 0.3, "aaaaaaaaaaaaaaaa"),
        (0.3, 0.6, "bbbbbbbbbbbbbbbb"),
        (0.6, 0.9, "cccccccccccccccc"),
    )
    # max_chars tan pequeno que solo cabe una palabra por linea y max_lines=1
    # cierra el cartel a la segunda palabra.
    result = cues.build_cues(
        tr, 0.0, 3.0, max_words_per_line=4, max_lines=1, max_chars_per_line=10, min_duration_ms=0
    )
    assert len(result) == 3
    assert result[0].text == "aaaaaaaaaaaaaaaa"


def test_max_duration_parte_un_tramo_largo():
    tr = _transcript((0.0, 0.5, "primera"), (5.0, 5.5, "otra"))
    result = cues.build_cues(
        tr, 0.0, 6.0, min_duration_ms=0, max_duration_ms=2000
    )
    assert len(result) == 2
    assert result[0].text == "primera"
    assert result[1].start == pytest.approx(5.0)
    assert result[1].end == pytest.approx(5.5)


def test_cues_cortas_se_fusionan_si_cabe_en_el_maximo():
    tr = _transcript(
        (0.0, 0.2, "aaaaaaaaaa"),
        (0.3, 0.6, "bbbbbbbbbbbbbbbbbb"),
        (2.0, 2.3, "cccccccccc"),
    )
    result = cues.build_cues(
        tr,
        0.0,
        3.0,
        max_words_per_line=4,
        max_lines=1,
        max_chars_per_line=10,
        min_duration_ms=500,
        max_duration_ms=4000,
    )
    # packing separa por lineas; la fusion junta los tres en uno solo.
    assert len(result) == 1
    assert result[0].text == f"aaaaaaaaaa{cues.LINE_BREAK}bbbbbbbbbbbbbbbbbb{cues.LINE_BREAK}cccccccccc"


def test_transcript_sin_palabras_devuelve_vacio():
    tr = {"schema_version": "1.0", "language": "es", "duration": 3.0, "segments": [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "hola", "words": []}
    ]}
    assert cues.build_cues(tr, 0.0, 3.0) == []


def test_ventana_invertida_o_nula_devuelve_vacio():
    tr = _transcript((0.0, 1.0, "palabra"))
    assert cues.build_cues(tr, 3.0, 1.0) == []
    assert cues.build_cues(tr, 1.0, 1.0) == []


def test_limites_invalidos_rebotan():
    tr = _transcript((0.0, 1.0, "palabra"))
    with pytest.raises(ValueError):
        cues.build_cues(tr, 0.0, 2.0, max_lines=0)
    with pytest.raises(ValueError):
        cues.build_cues(tr, 0.0, 2.0, max_chars_per_line=0)


def test_cue_to_dict_se_serializa_para_remotion():
    cue = cues.SubtitleCue(start=0.0, end=1.5, text="hola")
    assert cue.to_dict() == {"start": 0.0, "end": 1.5, "text": "hola"}


def test_el_silbido_no_genera_texto():
    """Palabras marcadas como silencio no aparecen en el cartel."""
    tr = _transcript()
    assert cues.build_cues(tr, 0.0, 3.0) == []