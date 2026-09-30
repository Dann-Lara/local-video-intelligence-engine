"""Tests del segundo LLM pass (secciones 33-34).

Lo que mas importa aqui no es que el modelo apruebe, sino que el SOFTWARE pueda
vetar un aprobado. Un QC que solo refleja lo que dice Gemma no es un QC: es un
eco. Por eso hay tantos tests de `sanitize_verdict` y del downgrade.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import artifacts  # noqa: E402
from src.llm.lmstudio import LLMRecoverableError  # noqa: E402
from src.quality import semantic  # noqa: E402


def _answers(**overrides: Any) -> dict[str, Any]:
    base = {key: {"ok": True, "reason": ""} for key in (
        "makes_sense",
        "understands_standalone",
        "has_payoff",
        "ends_complete",
        "hook_works",
    )}
    for key, value in overrides.items():
        base[key] = value
    return base


def _verdict(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "approved": True,
        "score": 85.0,
        "answers": _answers(),
        "problems": [],
        "recommendations": [],
    }
    body.update(overrides)
    return body


class FakeClient:
    """Cliente determinista. `responses` se consume en orden; el ultimo se repite."""

    temperature = 0.1
    max_tokens = None
    model = "gemma-fake"

    def __init__(self, *responses: Any):
        self._responses = list(responses) or [_verdict()]
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


PARAGRAPHS = [
    {"id": "p0", "text": "We got intel on the suspect.", "start": 10.0, "end": 12.0},
    {"id": "p1", "text": "They set up cameras for months.", "start": 12.5, "end": 15.0},
    {"id": "p2", "text": "The plan is finally ready.", "start": 40.0, "end": 42.0},
]


# --- sanitize_verdict ------------------------------------------------------


def test_un_veredicto_limpio_se_aprueba() -> None:
    v = semantic.sanitize_verdict(_verdict(), min_score=70)
    assert v["approved"] is True
    assert v["score"] == 85.0
    assert v["problems"] == []


def test_una_pregunta_fallada_veta_el_aprobado() -> None:
    """Regla 3: las cinco preguntas tienen que pasar todas."""
    v = semantic.sanitize_verdict(
        _verdict(answers=_answers(hook_works={"ok": False, "reason": "no engancha"})),
        min_score=70,
    )
    assert v["approved"] is False
    assert any("hook_works" in p for p in v["problems"])


def test_un_score_bajo_veta_el_aprobado_aunque_el_modelo_aprobe() -> None:
    v = semantic.sanitize_verdict(_verdict(score=30.0), min_score=70)
    assert v["approved"] is False
    assert any("por debajo del minimo" in p for p in v["problems"])


def test_el_score_se_recorta_a_100() -> None:
    v = semantic.sanitize_verdict(_verdict(score=250.0), min_score=70)
    assert v["score"] == 100.0


def test_el_score_negativo_se_recorta_a_0() -> None:
    v = semantic.sanitize_verdict(_verdict(score=-5.0), min_score=70)
    assert v["score"] == 0.0
    assert v["approved"] is False


def test_falta_de_una_respuesta_es_error_de_modelo() -> None:
    answers = _answers()
    del answers["has_payoff"]
    with pytest.raises(semantic.ModelOutputError):
        semantic.sanitize_verdict(_verdict(answers=answers), min_score=70)


def test_score_no_numerico_es_error_de_modelo() -> None:
    with pytest.raises(semantic.ModelOutputError):
        semantic.sanitize_verdict(_verdict(score="mucho"), min_score=70)


def test_el_score_justo_en_el_minimo_aprueba() -> None:
    v = semantic.sanitize_verdict(_verdict(score=70.0), min_score=70)
    assert v["approved"] is True


# --- clip_transcript -------------------------------------------------------


def test_el_transcript_del_clip_usa_tiempos_relativos() -> None:
    texto, truncado = semantic.clip_transcript(PARAGRAPHS, 10.0, 15.0)
    assert not truncado
    # Los parrafos empiezan en [00:10] absoluto, que es 0 s dentro del clip.
    assert "[00:00-00:02]" in texto
    assert "[00:02-00:05]" in texto
    assert "40.0" not in texto


def test_el_transcript_excluye_parrafos_fuera_de_la_ventana() -> None:
    texto, _ = semantic.clip_transcript(PARAGRAPHS, 10.0, 15.0)
    assert "finally ready" not in texto


def test_el_transcript_se_recorta_si_pasa_el_presupuesto() -> None:
    largo = [
        {"id": f"p{i}", "text": "palabra " * 200, "start": float(i), "end": float(i) + 0.5}
        for i in range(20)
    ]
    texto, truncado = semantic.clip_transcript(largo, 0.0, 20.0)
    assert truncado
    assert "recortado por presupuesto" in texto
    assert len(texto) <= semantic.CLIP_MAX_CHARS + 200


# --- run_semantic_qc -------------------------------------------------------


def _run(tmp_path: Path, config: dict[str, Any], client: FakeClient, **kwargs: Any):
    return semantic.run_semantic_qc(
        clip_id="c1",
        paragraphs=PARAGRAPHS,
        start=10.0,
        end=15.0,
        clip_metadata={"topic": "escape"},
        edit_information={"crop_mode": "center"},
        config=config,
        cache_dir=tmp_path / "qc",
        client=client,
        **kwargs,
    )


def test_el_segundo_pass_devuelve_el_veredicto(tmp_path: Path, config: dict[str, Any]) -> None:
    cliente = FakeClient()
    resultado = _run(tmp_path, config, cliente)
    assert resultado.approved is True
    assert resultado.score == 85.0
    assert cliente.calls == 1
    assert cliente.schema_names == ["semantic_qc"]
    assert "aprobado" in resultado.summary()


def test_el_segundo_pass_cachea_el_veredicto(tmp_path: Path, config: dict[str, Any]) -> None:
    primero = FakeClient()
    _run(tmp_path, config, primero)
    assert primeros_en_cache(tmp_path)

    segundo = FakeClient()
    resultado = _run(tmp_path, config, segundo)
    assert segundo.calls == 0, "la segunda vez debe leerse de la cache"
    assert resultado.approved is True


def primeros_en_cache(tmp_path: Path) -> bool:
    return (tmp_path / "qc" / "semantic_c1.json").is_file()


def test_overwrite_vuelve_a_preguntar(tmp_path: Path, config: dict[str, Any]) -> None:
    _run(tmp_path, config, FakeClient())
    cliente = FakeClient(_verdict(score=40.0))
    resultado = _run(tmp_path, config, cliente, overwrite=True)
    assert cliente.calls == 1
    assert resultado.score == 40.0


def test_reintenta_si_el_primer_intento_es_ilegible(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    cliente = FakeClient("esto no es json", _verdict())
    resultado = _run(tmp_path, config, cliente)
    assert resultado.approved is True
    assert cliente.calls == 2


def test_tolera_fences_de_markdown(tmp_path: Path, config: dict[str, Any]) -> None:
    envuelto = "```json\n" + json.dumps(_verdict()) + "\n```"
    resultado = _run(tmp_path, config, FakeClient(envuelto))
    assert resultado.approved is True


def test_tolera_json_roto_recortando(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    # El modelo antepone un "Here you go:" antes del objeto: se recorta al primer {.
    con_ruido = 'Aqui tienes el JSON:\n' + json.dumps(_verdict()) + '\nListo.'
    resultado = _run(tmp_path, config, FakeClient(con_ruido))
    assert resultado.approved is True


def test_degrada_a_json_por_prompt_si_el_servidor_rechaza_el_schema(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    """Seccion 59: un 400 por schema no es un fallo del servidor."""
    error = LLMRecoverableError("Invalid JSON Schema")
    error.status_code = 400
    cliente = FakeClient(error, _verdict())
    resultado = _run(tmp_path, config, cliente)
    assert resultado.approved is True
    # El primer intento pidio schema, el segundo ya va sin schema, sin gastar
    # un reintento del presupuesto.
    assert cliente.schema_names == ["semantic_qc", None]


def test_falla_si_el_modelo_no_produce_nada_valido(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    config["orchestration"] = {**config.get("orchestration", {}), "retry_backoff_seconds": 0}
    cliente = FakeClient("basura", "mas basura", "otra basura")
    with pytest.raises(semantic.SemanticRecoverableError):
        _run(tmp_path, config, cliente)
    assert cliente.calls == 3


def test_sin_transcript_en_la_ventana_es_fatal(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    """Sin texto no se puede evaluar, y fingir un veredicto seria inventar."""
    with pytest.raises(semantic.SemanticFatalError):
        semantic.run_semantic_qc(
            clip_id="c1",
            paragraphs=PARAGRAPHS,
            start=500.0,
            end=505.0,
            clip_metadata={},
            edit_information={},
            config=config,
            cache_dir=tmp_path / "qc",
            client=FakeClient(),
        )


def test_el_prompt_incluye_el_transcript_y_las_preguntas(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    mensajes = semantic.build_messages(
        "TEXTO DEL CLIP", {"topic": "escape"}, {"crop_mode": "center"}
    )
    assert mensajes[0]["role"] == "system"
    assert "makes_sense" in mensajes[0]["content"] or "cinco preguntas" in mensajes[0]["content"]
    assert "TEXTO DEL CLIP" in mensajes[1]["content"]
    assert "escape" in mensajes[1]["content"]
    assert "center" in mensajes[1]["content"]


def test_el_veredicto_escribe_en_la_cache(tmp_path: Path, config: dict[str, Any]) -> None:
    _run(tmp_path, config, FakeClient())
    cache = json.loads((tmp_path / "qc" / "semantic_c1.json").read_text(encoding="utf-8"))
    assert cache["clip_id"] == "c1"
    assert cache["model"] == "gemma-fake"
    assert cache["approved"] is True
    assert cache["source_window"] == {"start": 10.0, "end": 15.0}
