"""Normalizacion del transcript (`src/transcription/normalize.py`, seccion 15).

La invariante que atraviesa TODOS estos tests: `normalize()` no modifica
`transcript.json` ni un solo byte. No es una preferencia, es el requisito de la
seccion 15 y la razon por la que el derivado se puede regenerar gratis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.core import artifacts
from src.transcription import normalize


def _segment(identifier: int, start: float, end: float, text: str) -> dict[str, Any]:
    return {"id": identifier, "start": start, "end": end, "text": text}


def _transcript_document(
    segments: list[dict[str, Any]], *, duration: float = 60.0, language: str = "es"
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "job_id": "job_norm",
        "source": {
            "audio_file": "audio.wav",
            "audio_sample_rate": 16000,
            "audio_channels": 1,
            "sha256": "b" * 64,
        },
        "language": language,
        "language_probability": 0.95,
        "duration": duration,
        "generator": {"provider": "faster_whisper", "model": "tiny", "parameters": {}},
        "segments": segments,
    }


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    """Un transcript de 60 s con 6 segmentos y dos pausas marcadas."""
    segments = [
        _segment(0, 0.0, 4.0, "Primera frase. Segunda frase."),
        _segment(1, 4.0, 8.0, "Tercera frase con mas palabras."),
        # Pausa de 3 s: por encima del umbral, debe cerrar parrafo.
        _segment(2, 11.0, 15.0, "Tras la pausa empieza otra idea."),
        _segment(3, 15.0, 19.0, "Y sigue la idea con detalle."),
        _segment(4, 30.0, 34.0, "Bloque nuevo mucho despues."),
        _segment(5, 34.0, 38.0, "Ultimo bloque del ejemplo."),
    ]
    target = tmp_path / "transcript.json"
    artifacts.write_json(target, _transcript_document(segments), schema_name="transcript")
    return target


# --- limpieza de espacios (seccion 15) ---------------------------------------


class TestCleanText:
    def test_colapsa_espacios_multiples(self) -> None:
        assert normalize.clean_text("hola    mundo") == "hola mundo"

    def test_normaliza_espacios_especiales(self) -> None:
        """SAPI y algunos navegadores meten NBSP y espacios finos."""
        assert normalize.clean_text("hola\u00a0mundo") == "hola mundo"
        assert normalize.clean_text("hola\u2009mundo") == "hola mundo"
        assert normalize.clean_text("hola\u202fmundo") == "hola mundo"

    def test_convierte_saltos_de_linea_en_espacios(self) -> None:
        assert normalize.clean_text("primera\n\nsegunda") == "primera segunda"

    def test_recorta_los_extremos(self) -> None:
        assert normalize.clean_text("   texto   ") == "texto"

    def test_vacio_no_revienta(self) -> None:
        assert normalize.clean_text("") == ""
        assert normalize.clean_text("   \n  ") == ""


# --- frases ------------------------------------------------------------------


class TestSplitSentences:
    def test_parte_por_puntuacion_conservando_el_signo(self) -> None:
        frases = normalize.split_sentences("Uno. Dos! Tres?", 0.0, 9.0, (0,))

        assert [f["text"] for f in frases] == ["Uno.", "Dos!", "Tres?"]

    def test_los_tiempos_avanzan_y_no_se_solapan(self) -> None:
        frases = normalize.split_sentences("Uno. Dos. Tres.", 10.0, 16.0, (0,))

        for anterior, siguiente in zip(frases, frases[1:]):
            assert anterior["end"] <= siguiente["start"] + 1e-6
        assert frases[0]["start"] == pytest.approx(10.0)
        assert frases[-1]["end"] == pytest.approx(16.0)

    def test_una_sola_frase_ocupa_todo_el_intervalo(self) -> None:
        frases = normalize.split_sentences("Sin puntos", 4.0, 9.0, (3,))

        assert len(frases) == 1
        assert frases[0]["start"] == 4.0
        assert frases[0]["end"] == 9.0

    def test_reparte_los_segmentos_entre_frases(self) -> None:
        """Sin esto, cada frase llevaria los ids de todo el parrafo."""
        frases = normalize.split_sentences(
            "Primera frase con muchas palabras. Segunda frase corta. Tercera frase.",
            0.0, 12.0, (10, 11, 12),
        )

        todos = [i for f in frases for i in f["segment_ids"]]
        assert todos == [10, 11, 12], "cada segmento debe aparecer exactamente una vez"
        assert len(frases[0]["segment_ids"]) >= 1
        # Las frases posteriores no empiezan todas por el primer segmento.
        assert frases[-1]["segment_ids"] != [10, 11, 12]

    def test_sin_segmentos_devuelve_listas_vacias(self) -> None:
        frases = normalize.split_sentences("Uno. Dos.", 0.0, 4.0, ())
        assert all(f["segment_ids"] == [] for f in frases)


# --- pausas ------------------------------------------------------------------


class TestDetectPauses:
    def test_detecta_los_silencios(self) -> None:
        segmentos = [_segment(0, 0.0, 4.0, "a"), _segment(1, 7.0, 9.0, "b")]

        pausas = normalize.detect_pauses(segmentos)

        assert len(pausas) == 1
        assert pausas[0].start == 4.0
        assert pausas[0].end == 7.0
        assert pausas[0].duration == 3.0

    def test_ignora_los_solapes(self) -> None:
        """Whisper solapa segmentos centesimas: un gap de -0.03 no es una pausa."""
        segmentos = [_segment(0, 0.0, 5.0, "a"), _segment(1, 4.98, 9.0, "b")]

        assert normalize.detect_pauses(segmentos) == []

    def test_ignora_las_pausas_cortas(self) -> None:
        segmentos = [_segment(0, 0.0, 4.0, "a"), _segment(1, 4.3, 9.0, "b")]
        assert normalize.detect_pauses(segmentos) == []

    def test_sin_segmentos_no_hay_pausas(self) -> None:
        assert normalize.detect_pauses([]) == []


# --- parrafos ----------------------------------------------------------------


class TestBuildParagraphs:
    def test_cierra_en_la_pausa_larga(self, transcript: Path) -> None:
        document = artifacts.read_json(transcript)
        parrafos = normalize.build_paragraphs(document["segments"])

        assert len(parrafos) >= 2
        assert parrafos[0]["end"] <= 11.0, "el primer parrafo debe terminar antes de la pausa"

    def test_cumple_la_duracion_objetivo(self) -> None:
        segmentos = [
            _segment(i, float(i) * 10, float(i) * 10 + 9.5, f"Frase numero {i} con texto.")
            for i in range(10)
        ]

        parrafos = normalize.build_paragraphs(segmentos)

        for parrafo in parrafos:
            assert parrafo["end"] - parrafo["start"] <= 130, (
                f"el parrafo dura {parrafo['end'] - parrafo['start']}s, demasiado"
            )

    def test_los_ids_son_correlativos_desde_cero(self, transcript: Path) -> None:
        document = artifacts.read_json(transcript)
        parrafos = normalize.build_paragraphs(document["segments"])

        assert [p["id"] for p in parrafos] == list(range(len(parrafos)))

    def test_conserva_los_ids_de_segmento(self, transcript: Path) -> None:
        document = artifacts.read_json(transcript)
        parrafos = normalize.build_paragraphs(document["segments"])

        for parrafo in parrafos:
            assert parrafo["source"]["segment_ids"], "un parrafo sin segmentos no se puede recortar"
            assert parrafo["source"]["start"] == parrafo["start"]
            assert parrafo["source"]["end"] == parrafo["end"]

    def test_fusiona_los_parrafos_demasiado_cortos(self) -> None:
        segmentos = [
            _segment(0, 0.0, 1.0, "A."),
            _segment(1, 1.0, 2.0, "B."),
            _segment(2, 2.0, 40.0, "C con bastante mas texto para que valga la pena."),
        ]

        parrafos = normalize.build_paragraphs(segmentos)

        assert len(parrafos) == 1
        assert parrafos[0]["source"]["segment_ids"] == [0, 1, 2]

    def test_texto_vacio_no_crea_parrafos(self) -> None:
        assert normalize.build_paragraphs([]) == []
        assert normalize.build_paragraphs([_segment(0, 0.0, 1.0, "   ")]) == []


# --- ventanas de contexto (seccion 16) --------------------------------------


class TestChunk:
    def test_un_video_corto_cabe_en_una_ventana(self) -> None:
        assert normalize._chunk(45.0, 30, 120, 10) == [(0.0, 45.0)]

    def test_un_video_largo_se_divide_con_solape(self) -> None:
        ventanas = normalize._chunk(1000.0, 300, 900, 30)

        assert len(ventanas) > 1
        assert ventanas[0][0] == 0.0
        assert ventanas[-1][1] == 1000.0, "la ultima ventana tiene que llegar al final"
        for anterior, siguiente in zip(ventanas, ventanas[1:]):
            assert siguiente[0] < anterior[1], "las ventanas tienen que solaparse"

    def test_cubre_toda_la_duracion(self) -> None:
        for duracion in (100.0, 500.0, 3821.0, 14400.0):
            ventanas = normalize._chunk(duracion, 30, 120, 10)
            assert ventanas[0][0] == 0.0
            assert ventanas[-1][1] == pytest.approx(duracion, abs=0.01)

    def test_la_cola_corta_se_re_ancla_al_minimo(self) -> None:
        """Regresion del E2E real: en 1228 s la cola salia de 18 s y la seccion
        16 exige 30-120 s para el nivel 1. No se emite una ventana invalida:
        se ancla hacia atras hasta cubrir como minimo 30 s (seccion 62: no
        fabricar datos; el rango cubre el audio real)."""
        ventanas = normalize._chunk(1228.0, 30, 120, 10)
        assert ventanas[-1][1] == pytest.approx(1228.0, abs=0.01)
        assert ventanas[-1][1] - ventanas[-1][0] == 30.0
        assert ventanas[-1][0] > ventanas[-2][0], "no retrocede por detras de la anterior"
        for inicio, fin in ventanas:
            assert 30.0 <= fin - inicio <= 120.0

    def test_duracion_justo_por_encima_del_maximo(self) -> None:
        ventanas = normalize._chunk(121.0, 30, 120, 10)
        assert ventanas[-1][1] == pytest.approx(121.0, abs=0.01)
        assert ventanas[-1][1] - ventanas[-1][0] == 30.0


class TestContextWindows:
    def test_tres_niveles_para_un_video_largo(self) -> None:
        parrafos = [
            {"id": 0, "text": "a" * 50, "start": float(i * 100), "end": float(i * 100 + 100)}
            for i in range(40)
        ]

        ventanas = normalize.build_context_windows(parrafos, 4000.0)

        niveles = {v["level"] for v in ventanas}
        assert niveles == {1, 2, 3}

    def test_hay_siempre_ventana_global(self, transcript: Path) -> None:
        document = artifacts.read_json(transcript)
        parrafos = normalize.build_paragraphs(document["segments"])

        ventanas = normalize.build_context_windows(parrafos, document["duration"])

        globales = [v for v in ventanas if v["level"] == 3]
        assert len(globales) == 1
        assert globales[0]["start"] == 0.0
        assert globales[0]["end"] == document["duration"]

    def test_la_jerarquia_no_se_rompe(self, transcript: Path) -> None:
        document = artifacts.read_json(transcript)
        parrafos = normalize.build_paragraphs(document["segments"])
        ventanas = normalize.build_context_windows(parrafos, 4000.0)

        por_id = {v["id"]: v for v in ventanas}
        for ventana in ventanas:
            if ventana["level"] == 3:
                assert ventana["parent_id"] is None
                continue
            assert ventana["parent_id"] in por_id, "el padre tiene que existir"
            # El padre esta en un nivel SUPERIOR, no necesariamente el inmediato:
            # una ventana de nivel 1 que cae en el borde de dos bloques de nivel
            # 2 no esta contenida en ninguno, y se cuelga del resumen global.
            assert por_id[ventana["parent_id"]]["level"] > ventana["level"]
            assert ventana["id"] in por_id[ventana["parent_id"]]["child_ids"]

    def test_los_bordes_se_cuelgan_del_resumen_global(self, transcript: Path) -> None:
        """El caso limite del solape, comprobado a proposito."""
        document = artifacts.read_json(transcript)
        parrafos = normalize.build_paragraphs(document["segments"])
        ventanas = normalize.build_context_windows(parrafos, 4000.0)

        colgadas = [v for v in ventanas if v["level"] == 1 and v["parent_id"] == "l3_global"]

        for ventana in colgadas:
            assert "l3_global" in [v["id"] for v in ventanas]
            assert ventana["id"] in next(
                v for v in ventanas if v["id"] == "l3_global"
            )["child_ids"]

    def test_ningun_id_aparece_dos_veces(self, transcript: Path) -> None:
        document = artifacts.read_json(transcript)
        parrafos = normalize.build_paragraphs(document["segments"])
        ventanas = normalize.build_context_windows(parrafos, 4000.0)

        ids = [v["id"] for v in ventanas]
        assert len(ids) == len(set(ids))

    def test_los_parrafos_que_solapan_se_incluyen(self) -> None:
        """Una idea que empieza antes del corte no se manda partida."""
        parrafos = [{"id": 0, "text": "empieza antes", "start": 0.0, "end": 100.0}]

        ventanas = normalize.build_context_windows(parrafos, 100.0)

        assert all("empieza antes" in v["text"] for v in ventanas)

    def test_sin_parrafos_no_hay_ventanas(self) -> None:
        assert normalize.build_context_windows([], 100.0) == []


# --- API principal -----------------------------------------------------------


class TestNormalize:
    def test_escribe_un_normalizado_valido(self, transcript: Path) -> None:
        result = normalize.normalize(transcript, job_id="job_norm")

        destino = transcript.with_name("normalized_transcript.json")
        assert destino.is_file()
        assert result.paragraph_count > 0

        document = normalize.load_normalized(destino)
        assert document["derived_from"]["file"] == "transcript.json"
        assert len(document["derived_from"]["sha256"]) == 64
        assert document["job_id"] == "job_norm"

    def test_no_modifica_el_transcript_original(self, transcript: Path) -> None:
        antes = transcript.read_bytes()

        normalize.normalize(transcript)

        assert transcript.read_bytes() == antes, "el original es inmutable (seccion 14)"

    def test_el_sha256_del_derivado_apunta_al_original(self, transcript: Path) -> None:
        import hashlib

        normalize.normalize(transcript)
        document = normalize.load_normalized(transcript.with_name("normalized_transcript.json"))

        esperado = hashlib.sha256(transcript.read_bytes()).hexdigest()
        assert document["derived_from"]["sha256"] == esperado

    def test_idempotente(self, transcript: Path) -> None:
        primero = normalize.normalize(transcript)
        segundo = normalize.normalize(transcript)

        assert primero.reused is False
        assert segundo.reused is True
        assert segundo.paragraph_count == primero.paragraph_count

    def test_overwrite_regenera(self, transcript: Path) -> None:
        normalize.normalize(transcript)
        normalize.normalize(transcript, overwrite=True)

    def test_transcript_invalido_falla_antes_de_normalizar(self, tmp_path: Path) -> None:
        roto = tmp_path / "transcript.json"
        roto.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")

        with pytest.raises(artifacts.ArtifactInvalidError):
            normalize.normalize(roto)

    def test_transcript_inexistente(self, tmp_path: Path) -> None:
        with pytest.raises(artifacts.ArtifactError, match="no existe"):
            normalize.normalize(tmp_path / "nada.json")

    def test_sin_segmentos_avisa_pero_escribe(self, tmp_path: Path) -> None:
        vacio = tmp_path / "transcript.json"
        artifacts.write_json(
            vacio, _transcript_document([], duration=0.0), schema_name="transcript"
        )

        result = normalize.normalize(vacio)

        assert result.paragraph_count == 0
        assert any("voz" in w for w in result.warnings)
        assert normalize.load_normalized(vacio.with_name("normalized_transcript.json"))["full_text"] == ""

    def test_idioma_unknown_se_propaga(self, tmp_path: Path) -> None:
        documento = _transcript_document(
            [_segment(0, 0.0, 5.0, "Algo.")], language="unknown"
        )
        origen = tmp_path / "transcript.json"
        artifacts.write_json(origen, documento, schema_name="transcript")

        normalize.normalize(origen)

        assert normalize.load_normalized(origen.with_name("normalized_transcript.json"))["language"] == "unknown"

    def test_duracion_cero_se_recupera_del_ultimo_segmento(self, tmp_path: Path) -> None:
        """Whisper puede devolver duration=0; sin esto no habria ventanas."""
        documento = _transcript_document(
            [_segment(0, 0.0, 5.0, "Uno."), _segment(1, 5.0, 42.0, "Dos.")], duration=0.0
        )
        origen = tmp_path / "transcript.json"
        artifacts.write_json(origen, documento, schema_name="transcript")

        normalize.normalize(origen)
        resultado = normalize.load_normalized(origen.with_name("normalized_transcript.json"))

        assert resultado["duration"] == 42.0
        assert resultado["context_windows"][-1]["end"] == 42.0

    def test_conserva_acentos_y_texto_no_latino(self, tmp_path: Path) -> None:
        documento = _transcript_document(
            [
                _segment(0, 0.0, 4.0, "Transcripción automática. Segunda frase."),
                _segment(1, 4.0, 9.0, "日本語のテキストです。"),
            ]
        )
        origen = tmp_path / "transcript.json"
        artifacts.write_json(origen, documento, schema_name="transcript")

        normalize.normalize(origen)
        destino = origen.with_name("normalized_transcript.json")

        crudo = destino.read_text(encoding="utf-8")
        assert "Transcripción automática" in crudo
        assert "日本語" in crudo
        assert "\\u" not in crudo

    def test_output_path_alternativo(self, transcript: Path, tmp_path: Path) -> None:
        destino = tmp_path / "otro" / "norm.json"
        normalize.normalize(transcript, output_path=destino)
        assert destino.is_file()

    def test_is_valid_no_lanza_con_basura(self, tmp_path: Path) -> None:
        assert normalize.is_valid(tmp_path / "no_existe.json") is False

        basura = tmp_path / "basura.json"
        basura.write_text("no soy json", encoding="utf-8")
        assert normalize.is_valid(basura) is False

    def test_avisa_de_un_video_largo_dividido(self, tmp_path: Path) -> None:
        segmentos = [
            _segment(i, float(i) * 95, float(i) * 95 + 90.0, f"Frase {i} con contenido.")
            for i in range(40)
        ]
        origen = tmp_path / "transcript.json"
        artifacts.write_json(origen, _transcript_document(segmentos, duration=3800.0), schema_name="transcript")

        result = normalize.normalize(origen)

        assert any("min" in w for w in result.warnings)
