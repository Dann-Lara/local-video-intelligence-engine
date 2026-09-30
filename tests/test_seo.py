"""Tests del SEO engine (seccion 35).

La seccion 35 dice dos cosas: metadata por plataforma, e independiente por
clip. Lo que se comprueba aqui es que el software no regale nada de eso: que no
se cuele una descripcion generica, que la categoria no se contradiga entre
plataformas, y que lo que sale del modelo se limpie antes de publicarlo.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm.lmstudio import LLMRecoverableError  # noqa: E402
from src.quality import seo  # noqa: E402


PARAGRAPHS = [
    {"id": "p0", "text": "We got intel on the suspect.", "start": 10.0, "end": 12.0},
    {"id": "p1", "text": "They set up cameras for months.", "start": 12.5, "end": 15.0},
]


def _platform(title: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "title": title,
        "description": "Una descripcion del clip.",
        "hashtags": ["escape_attempt", "police_chase"],
        "keywords": ["prison break", "police chase"],
        "hook": "We got intel",
        "category": "true_crime",
    }
    base.update(overrides)
    return base


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "platforms": {
            "youtube": _platform("Escape Attempt: Police Pursuit"),
            "tiktok": _platform("GET OUT! Cops closing in"),
            "instagram": _platform("Prison Break: Tactical Escape"),
            "generic": _platform("Escape Attempt: Preparing for a Police Pursuit"),
        },
        "category": "true_crime",
    }
    body.update(overrides)
    return body


class FakeClient:
    temperature = 0.1
    max_tokens = None
    model = "gemma-fake"

    def __init__(self, *responses: Any):
        self._responses = list(responses) or [_body()]
        self.calls = 0
        self.schema_names: list[str | None] = []

    def resolve_model(self) -> str:
        return "gemma-fake"

    def chat(self, messages, *, schema_name=None, temperature=None, max_tokens=None) -> str:
        self.calls += 1
        self.schema_names.append(schema_name)
        index = min(self.calls - 1, len(self._responses) - 1)
        value = self._responses[index]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


# --- sanitize_metadata -----------------------------------------------------


def test_las_cuatro_plataformas_se_generan() -> None:
    limpio = seo.sanitize_metadata(_body())
    assert sorted(limpio["platforms"]) == ["generic", "instagram", "tiktok", "youtube"]


def test_la_categoria_se_propaga_a_las_cuatro() -> None:
    limpio = seo.sanitize_metadata(_body(category="TRUE CRIME"))
    assert limpio["category"] == "true_crime"
    for plataforma in limpio["platforms"].values():
        assert plataforma["category"] == "true_crime"


def test_la_categoria_se_recupera_de_una_plataforma() -> None:
    """Gemma omite la categoria de la raiz con facilidad; no es un fallo fatal."""
    body = _body()
    del body["category"]
    limpio = seo.sanitize_metadata(body)
    assert limpio["category"] == "true_crime"


def test_sin_categoria_en_ningun_sitio_es_error() -> None:
    body = _body()
    del body["category"]
    for plataforma in body["platforms"].values():
        del plataforma["category"]
    with pytest.raises(seo.ModelOutputError):
        seo.sanitize_metadata(body)


def test_una_plataforma_ausente_se_rellena() -> None:
    """El directorio debe tener siempre los cuatro ficheros."""
    body = _body()
    del body["platforms"]["tiktok"]
    limpio = seo.sanitize_metadata(body)
    assert limpio["platforms"]["tiktok"]["title"] == limpio["platforms"]["generic"]["title"]


def test_los_hashtags_se_limpian_y_no_se_duplican() -> None:
    body = _body()
    body["platforms"]["youtube"]["hashtags"] = ["#escape attempt", "escape_attempt", "!!", "policía"]
    limpio = seo.sanitize_metadata(body)
    tags = limpio["platforms"]["youtube"]["hashtags"]
    for tag in tags:
        assert "#" not in tag
        assert " " not in tag, f"{tag!r} lleva espacios"
        assert re.fullmatch(r"[A-Za-z0-9_]+", tag), f"{tag!r} no es un hashtag valido"
    # El espacio del hashtag se vuelve guion bajo, no se borra: asi "#escape
    # attempt" y "escape_attempt" son el mismo tag y no ocupa dos huecos.
    assert tags.count("escape_attempt") == 1
    # Sin acentos ni signos, como exige el schema de cada plataforma.
    assert "policía" not in tags
    assert any(tag.startswith("pol") for tag in tags)


def test_el_titulo_se_recorta_al_tope_de_la_plataforma() -> None:
    largo = "palabra " * 40
    body = _body()
    body["platforms"]["youtube"]["title"] = largo
    limpio = seo.sanitize_metadata(body)
    titulo = limpio["platforms"]["youtube"]["title"]
    assert len(titulo) <= seo._TITLE_MAX_CHARS["youtube"]
    # Recortar a media palabra es peor que un titulo corto de origen.
    assert not titulo.rstrip("…").endswith("palab")


def test_el_titulo_de_youtube_no_supera_100_caracteres() -> None:
    body = _body()
    body["platforms"]["youtube"]["title"] = "a" * 300
    limpio = seo.sanitize_metadata(body)
    assert len(limpio["platforms"]["youtube"]["title"]) == 100


def test_las_descripciones_son_distintas_por_plataforma() -> None:
    """La seccion 35 prohibe una descripcion generica compartida."""
    body = _body()
    body["platforms"]["youtube"]["description"] = "Contexto largo para YouTube."
    body["platforms"]["tiktok"]["description"] = "Una frase para TikTok."
    limpio = seo.sanitize_metadata(body)
    assert limpio["platforms"]["youtube"]["description"] != limpio["platforms"]["tiktok"]["description"]


# --- run_seo ---------------------------------------------------------------


def _run(tmp_path: Path, config: dict[str, Any], client: FakeClient, **kwargs: Any):
    return seo.run_seo(
        clip_id="c1",
        paragraphs=PARAGRAPHS,
        start=10.0,
        end=15.0,
        clip_metadata={"topic": "escape"},
        config=config,
        cache_dir=tmp_path / "qc",
        client=client,
        **kwargs,
    )


def test_seo_devuelve_las_cuatro_plataformas(tmp_path: Path, config: dict[str, Any]) -> None:
    cliente = FakeClient()
    resultado = _run(tmp_path, config, cliente)
    assert sorted(resultado.files()) == ["generic", "instagram", "tiktok", "youtube"]
    assert resultado.category == "true_crime"
    assert cliente.schema_names == ["seo_metadata"]


def test_seo_cachea(tmp_path: Path, config: dict[str, Any]) -> None:
    _run(tmp_path, config, FakeClient())
    segundo = FakeClient()
    resultado = _run(tmp_path, config, segundo)
    assert segundo.calls == 0
    assert resultado.category == "true_crime"


def test_seo_reintenta_si_el_primer_intento_es_basura(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    cliente = FakeClient("nada de json", _body())
    resultado = _run(tmp_path, config, cliente)
    assert sorted(resultado.files()) == ["generic", "instagram", "tiktok", "youtube"]
    assert cliente.calls == 2


def test_seo_degrada_si_el_servidor_rechaza_el_schema(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    error = LLMRecoverableError("Invalid JSON Schema")
    error.status_code = 422
    cliente = FakeClient(error, _body())
    _run(tmp_path, config, cliente)
    assert cliente.schema_names == ["seo_metadata", None]


def test_seo_falla_si_no_produce_nada_valido(tmp_path: Path, config: dict[str, Any]) -> None:
    config["orchestration"] = {**config.get("orchestration", {}), "retry_backoff_seconds": 0}
    cliente = FakeClient("basura", "mas basura", "otra")
    with pytest.raises(seo.SEORecoverableError):
        _run(tmp_path, config, cliente)


def test_seo_sin_transcript_es_fatal(tmp_path: Path, config: dict[str, Any]) -> None:
    with pytest.raises(seo.SEOFatalError):
        seo.run_seo(
            clip_id="c1",
            paragraphs=PARAGRAPHS,
            start=500.0,
            end=505.0,
            clip_metadata={},
            config=config,
            cache_dir=tmp_path / "qc",
            client=FakeClient(),
        )


def test_el_prompt_de_seo_pide_las_cuatro_plataformas() -> None:
    mensajes = seo.build_messages("TEXTO", {"topic": "escape"})
    assert "cuatro plataformas" in mensajes[1]["content"]
    for plataforma in seo.PLATFORMS:
        assert plataforma in mensajes[0]["content"]
