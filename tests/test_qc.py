"""Tests de QC tecnico y de subtitulos (seccion 33, sin IA).

Los fallos que se comprueban aqui son los que en produccion se manifesting
raro pero carisimos: un render con la resolucion equivocada que se publica
igual, un clip sin audio, o un subtitulo con un caracter de reemplazo que
sale como un rombo negro en pantalla.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.quality import qc  # noqa: E402


# --- media -----------------------------------------------------------------


def test_media_ok_para_un_render_correcto(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    # El fixture es 1080x1920 a 3 s, que es justo lo que espera output.*
    informe = qc.check_media(portrait_video, expected_duration=3.0, config=config)
    assert informe.exists
    assert informe.video_present
    assert informe.audio_present
    assert informe.resolution_ok
    assert informe.codec_ok
    assert informe.duration_ok
    assert informe.passed
    assert informe.problems == []


def test_media_falla_si_el_fichero_no_existe(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    informe = qc.check_media(
        tmp_path / "no_existe.mp4", expected_duration=3.0, config=config
    )
    assert not informe.exists
    assert not informe.passed
    assert "no existe" in informe.problems[0]


def test_media_no_lanza_si_el_fichero_es_basura(
    not_a_video: Path, config: dict[str, Any]
) -> None:
    # ffprobe no puede leerlo. Eso es un RESULTADO de QC, no un error de la
    # etapa: si se dejara escapar como excepcion, el QC de un clip roto
    # tumbaria el job entero.
    informe = qc.check_media(not_a_video, expected_duration=3.0, config=config)
    assert not informe.passed
    assert any("ffprobe" in problem for problem in informe.problems)


def test_media_detecta_resolucion_equivocada(
    landscape_video: Path, config: dict[str, Any]
) -> None:
    # El fixture es 1920x1080 y output.* espera 1080x1920.
    informe = qc.check_media(landscape_video, expected_duration=3.0, config=config)
    assert not informe.resolution_ok
    assert not informe.passed
    assert any("resolucion inesperada" in problem for problem in informe.problems)


def test_media_detecta_sin_audio(
    silent_video: Path, config: dict[str, Any]
) -> None:
    informe = qc.check_media(silent_video, expected_duration=3.0, config=config)
    assert informe.exists
    assert not informe.audio_present
    assert not informe.passed


def test_media_detecta_duracion_fuera_de_tolerancia(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    # El clip dura 3 s; se le piden 40. La tolerancia por defecto es 0.2 s.
    informe = qc.check_media(portrait_video, expected_duration=40.0, config=config)
    assert not informe.duration_ok
    assert not informe.passed
    assert any("duracion fuera de tolerancia" in p for p in informe.problems)


def test_media_acepta_duracion_dentro_de_tolerancia(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    informe = qc.check_media(portrait_video, expected_duration=3.1, config=config)
    assert informe.duration_ok


def test_media_detecta_fichero_demasiado_grande(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    config["quality"] = {**config.get("quality", {}), "max_render_size_bytes": 10}
    informe = qc.check_media(portrait_video, expected_duration=3.0, config=config)
    assert not informe.size_ok
    assert any("demasiado grande" in p for p in informe.problems)


def test_media_usa_las_dimensiones_de_presentacion(
    rotated_video: Path, config: dict[str, Any]
) -> None:
    """Un 1920x1080 con rotate=90 se VE 1080x1920 y por eso vale como vertical.

    Comparar contra width/height en crudo marcaria como mala resolucion un
    clip que se ve perfectamente vertical, que es justo lo que pasa con los
    telefonos.
    """
    from src.media import ffprobe

    info = ffprobe.probe(rotated_video)
    if info.video is None or info.video.rotation % 180 != 90:
        pytest.skip("el fixture no lleva matriz de rotacion")

    informe = qc.check_media(rotated_video, expected_duration=None, config=config)
    assert informe.resolution_ok
    assert informe.actual_resolution == "1080x1920"


# --- subtitulos ------------------------------------------------------------


def _cues(*pairs: tuple[float, float, str]) -> list[dict[str, Any]]:
    return [{"start": s, "end": e, "text": t} for s, e, t in pairs]


def test_sin_subtitulos_no_es_fallo(config: dict[str, Any]) -> None:
    # subtitles.enabled=false: un clip mudo en texto no es un fallo de QC.
    informe = qc.check_subtitles([], duration=38.0, config=config)
    assert not informe.subtitles_present
    assert informe.passed


def test_subtitulos_validos_pasan(config: dict[str, Any]) -> None:
    informe = qc.check_subtitles(
        _cues((0.0, 2.0, "primera frase"), (2.5, 4.5, "segunda frase")),
        duration=10.0,
        config=config,
    )
    assert informe.subtitles_present
    assert informe.passed
    assert informe.problems == []


def test_subtitulo_con_salto_de_linea_no_es_caracter_invalido(
    config: dict[str, Any]
) -> None:
    """U+000A es el separador de linea del cue, no un caracter corrupto.

    Marcarlo como invalido haria fallar TODOS los clips con subtitulos de dos
    lineas, que es la configuracion por defecto.
    """
    informe = qc.check_subtitles(
        _cues((0.0, 2.0, "primera\nsegunda")), duration=10.0, config=config
    )
    assert not informe.invalid_characters
    assert informe.passed


def test_subtitulo_con_caracter_de_reemplazo_falla(config: dict[str, Any]) -> None:
    # U+FFFD sale como un rombo negro en pantalla.
    informe = qc.check_subtitles(
        _cues((0.0, 2.0, "primer�\nsegunda")), duration=10.0, config=config
    )
    assert informe.invalid_characters
    assert not informe.passed
    assert any("U+FFFD" in p for p in informe.problems)


def test_subtitulo_con_nota_de_control_falla(config: dict[str, Any]) -> None:
    informe = qc.check_subtitles(
        _cues((0.0, 2.0, "texto con \x07 campana")), duration=10.0, config=config
    )
    assert informe.invalid_characters
    assert any("U+0007" in p for p in informe.problems)


def test_subtitulo_muy_largo_se_marca_fuera_de_pantalla(
    config: dict[str, Any]
) -> None:
    # max_chars_per_line=22 por defecto. Una linea de 60 no cabe en un 1080
    # vertical con fuente de movil.
    informe = qc.check_subtitles(
        _cues((0.0, 2.0, "x" * 60)), duration=10.0, config=config
    )
    assert informe.out_of_screen
    assert not informe.passed


def test_subtitulo_fuera_de_la_duracion_del_clip_falla(
    config: dict[str, Any]
) -> None:
    informe = qc.check_subtitles(
        _cues((0.0, 2.0, "ok"), (2.5, 99.0, "se pasa de largo")),
        duration=10.0,
        config=config,
    )
    assert informe.unsynchronized
    assert not informe.passed


def test_subtitulos_solapados_fallan(config: dict[str, Any]) -> None:
    informe = qc.check_subtitles(
        _cues((0.0, 3.0, "primera"), (2.0, 5.0, "segunda")),
        duration=10.0,
        config=config,
    )
    assert informe.unsynchronized
    assert any("solapa" in p for p in informe.problems)


def test_subtitulo_demasiado_corto_falla(config: dict[str, Any]) -> None:
    # min_duration_ms=800 por defecto: 0.2 s no se puede leer.
    informe = qc.check_subtitles(
        _cues((0.0, 0.2, "centisegundo")), duration=10.0, config=config
    )
    assert informe.unsynchronized
    assert any("minimo" in p for p in informe.problems)


def test_subtitulo_demasiado_largo_falla(config: dict[str, Any]) -> None:
    # max_duration_ms=4000 por defecto: 9 s de cartel fijo es un error.
    informe = qc.check_subtitles(
        _cues((0.0, 9.0, "nueve segundos")), duration=20.0, config=config
    )
    assert informe.unsynchronized
    assert any("maximo" in p for p in informe.problems)


# --- ClipQC ----------------------------------------------------------------


def test_clip_qc_pasa_con_media_y_subtitulos_correctos(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    # El clip dura 3 s: los cues tienen que caber dentro.
    clip = qc.run_clip_qc(
        portrait_video,
        _cues((0.0, 1.4, "hola"), (1.6, 2.9, "adios")),
        clip_id="c1",
        expected_duration=3.0,
        config=config,
    )
    assert clip.passed
    assert clip.problems() == []
    bloque = clip.to_record_qc(config)
    assert bloque["passed"] is True
    assert bloque["technical"]["duration_ok"] is True
    assert bloque["subtitles_ok"] is True
    assert bloque["semantic"] is None


def test_clip_qc_falla_si_el_render_falta(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    clip = qc.run_clip_qc(
        tmp_path / "nada.mp4",
        [],
        clip_id="c1",
        expected_duration=3.0,
        config=config,
    )
    assert not clip.passed
    assert "c1: FALLO" in clip.summary()


def test_clip_qc_sin_subtitulos_sigue_pasando(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    clip = qc.run_clip_qc(
        portrait_video, [], clip_id="c1", expected_duration=3.0, config=config
    )
    assert clip.passed
    assert "subs=sin" in clip.summary()


def test_clip_qc_falla_si_la_semantica_rechaza(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    clip = qc.run_clip_qc(
        portrait_video, [], clip_id="c1", expected_duration=3.0, config=config
    )
    clip.semantic = {"approved": False, "score": 40.0, "problems": ["no se entiende"]}
    assert not clip.passed
    assert "no se entiende" in clip.problems()


def test_clip_qc_falla_si_el_score_semantico_es_bajo_aunque_apruebe(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    """El software es la ultima palabra del umbral (regla 1 de semantic.py)."""
    config["quality"] = {**config.get("quality", {}), "minimum_semantic_score": 70}
    clip = qc.run_clip_qc(
        portrait_video, [], clip_id="c1", expected_duration=3.0, config=config
    )
    clip.semantic = {"approved": True, "score": 55.0, "problems": []}
    assert not clip.passed


def test_clip_qc_pasa_con_score_justo_por_encima_del_minimo(
    portrait_video: Path, config: dict[str, Any]
) -> None:
    config["quality"] = {**config.get("quality", {}), "minimum_semantic_score": 70}
    clip = qc.run_clip_qc(
        portrait_video, [], clip_id="c1", expected_duration=3.0, config=config
    )
    clip.semantic = {"approved": True, "score": 70.0, "problems": []}
    assert clip.passed
