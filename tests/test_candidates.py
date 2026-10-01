"""Tests del Candidate Generator (secciones 18-24, 59 y 62).

La parte que SIEMPRE se puede probar sin red es todo lo que el software hace
con lo que dice Gemma: normalizacion del score (seccion 20), clamp de tiempos,
expansion de contexto (seccion 24), degradacion del hook (secciones 25/26) y
el envoltorio que firma la capa de generacion. La llamada al modelo se fakea.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis import candidates as candidates_module  # noqa: E402
from src.core import artifacts  # noqa: E402
from src.core.invariants import SCORE_DIMENSION_MAX  # noqa: E402

from test_schemas import CONTENT_MAP, NORMALIZED_TRANSCRIPT  # noqa: E402

SHA = "b" * 64

CONFIG = {
    "clips": {
        "minimum": 1.0,
        "preferred_minimum": 1.0,
        "preferred_maximum": 10.0,
        "maximum": 90.0,
        "max_leading_silence": 1.5,
    },
    "selection": {
        "minimum_score": 72.0,
        "target_minimum": 1,
        "target_maximum": 10,
        "diversity_threshold": 0.80,
        "max_temporal_overlap": 0.5,
    },
    "llm": {"provider": "lmstudio", "structured_output": "off", "max_retries": 2},
}

BODY_CANDIDATE = {
    "source": {"start": 1.0, "end": 4.0, "boundary_reason": "frase completa"},
    "content": {"topic": "prueba", "reason": "setup + payoff", "content_type": "explanation"},
    "hook": {
        "type": "question",
        "text": "¿Por que falla esto?",
        "origin": "generated",
        "time": {"start": 1.0, "end": 1.5},
    },
    "progression": {"pattern": "setup_payoff", "ends_complete": True},
    "value_provided": ["information"],
    "signatures": {"topic_signature": "topic:prueba", "semantic_signature": "sem:prueba"},
    "score": {
        "hook": 16,
        "curiosity": 12,
        "value": 12,
        "emotion": 8,
        "narrative": 8,
        "payoff": 8,
        "shareability": 7,
        "standalone": 4,
        "visual": 3,
    },
}


class FakeClient:
    """Cliente de chat determinista: siempre devuelve un candidato valido."""

    temperature = 0.1
    max_tokens = None

    def __init__(self, body: dict | None = None):
        self._body = body or {"candidates": [BODY_CANDIDATE]}
        self.calls = 0

    def resolve_model(self) -> str:
        return "gemma-fake"

    def chat(self, messages, *, schema_name=None, temperature=None, max_tokens=None) -> str:
        self.calls += 1
        return json.dumps(self._body, ensure_ascii=False)


class FakeRepo:
    """Registra las corridas de modelo en lugar del JobRepository real."""

    def __init__(self):
        self.runs: list[dict] = []

    def record_model_run(self, **payload) -> str:
        self.runs.append(payload)
        return "model_run_id"


@pytest.fixture
def inputs(tmp_path):
    """normalized_transcript.json y content_map.json validos en un temporal."""
    normalized = dict(NORMALIZED_TRANSCRIPT)
    normalized["duration"] = 10.5
    normalized["context_windows"] = [
        {"id": "w1", "level": 1, "start": 0.0, "end": 10.5, "text": "Hola mundo"},
        {"id": "w2", "level": 2, "start": 0.0, "end": 10.5, "text": "Hola mundo"},
    ]
    normalized_path = tmp_path / "normalized_transcript.json"
    artifacts.write_json(normalized_path, normalized, schema_name="normalized_transcript")
    map_path = tmp_path / "content_map.json"
    artifacts.write_json(map_path, CONTENT_MAP, schema_name="content_map")
    return normalized_path, map_path


# --- normalizacion del score (seccion 20) ---


def test_scores_in_unit_range_are_fractions_of_the_cap():
    drops: list[str] = []
    normalized = candidates_module._normalize_score(
        {
            "hook": 0.8,      # 0.8 * 20 = 16
            "curiosity": 0.4,  # 0.4 * 15 = 6
            "value": 1.0,     # 1.0 * 15 = 15
            "emotion": 0.0,
            "narrative": 5.0,
            "payoff": 3.0,
            "shareability": 0.1,  # 0.1 * 10 = 1
            "standalone": 0.5,  # 0.5 * 5 = 2.5
            "visual": 0.2,      # 0.2 * 5 = 1
        },
        drops,
    )
    assert normalized["hook"] == 16.0
    assert normalized["curiosity"] == 6.0
    assert normalized["value"] == 15.0
    assert normalized["shareability"] == 1.0
    assert normalized["standalone"] == 2.5
    assert sum(normalized[k] for k in SCORE_DIMENSION_MAX) == normalized["total"] == pytest.approx(49.5)


def test_scores_above_cap_are_clamped():
    drops: list[str] = []
    normalized = candidates_module._normalize_score(
        {
            "hook": 30.0,
            "curiosity": 99.0,
            "value": 15.0,
            "emotion": 10.0,
            "narrative": 10.0,
            "payoff": 10.0,
            "shareability": 10.0,
            "standalone": 5.0,
            "visual": 5.0,
        },
        drops,
    )
    assert normalized["hook"] == 20.0
    assert normalized["curiosity"] == 15.0
    assert normalized["total"] == 100.0


def test_scores_never_accept_the_llm_total():
    drops: list[str] = []
    normalized = candidates_module._normalize_score(
        {"hook": 20, "curiosity": 15, "value": 15, "emotion": 10, "narrative": 10,
         "payoff": 10, "shareability": 10, "standalone": 5, "visual": 5, "total": 1000},
        drops,
    )
    assert normalized.get("total") == 100


def test_non_numeric_dimension_becomes_zero_and_is_reported():
    drops: list[str] = []
    score = dict(BODY_CANDIDATE["score"])
    score["hook"] = "nueve"
    normalized = candidates_module._normalize_score(score, drops)
    assert normalized["hook"] == 0.0
    assert normalized["total"] == pytest.approx(sum(BODY_CANDIDATE["score"][k] for k in SCORE_DIMENSION_MAX if k != "hook"))
    assert any("hook" in drop for drop in drops)


def test_confidence_is_clamped():
    drops: list[str] = []
    normalized = candidates_module._normalize_score(
        {**{k: 0 for k in SCORE_DIMENSION_MAX}, "confidence": 3.0}, drops
    )
    assert normalized["confidence"] == 1.0


# --- clamp de tiempos ---


def test_clamp_time_anchors_to_video_duration():
    assert candidates_module._clamp_time(-5.0, 999.0, 100.0) == (0.0, 100.0)


def test_clamp_time_rejects_inverted_range():
    assert candidates_module._clamp_time(50.0, 50.0, 100.0) is None


# --- parseo (seccion 59) ---


def test_extract_json_tolerates_markdown_fences():
    payload = {"candidates": []}
    wrapped = f"```json\n{json.dumps(payload)}\n```"
    assert candidates_module._extract_json(wrapped) == payload


def test_extract_json_recovers_from_surrounding_text():
    payload = {"candidates": []}
    noisy = f"Aqui tienes:\n{json.dumps(payload)}\nEspero que sirva."
    assert candidates_module._extract_json(noisy) == payload


def test_extract_json_rejects_non_json():
    with pytest.raises(candidates_module.ModelOutputError):
        candidates_module._extract_json("no soy json")


def test_extract_json_salvages_candidates_from_a_truncated_list():
    """max_tokens corta a mitad del ultimo candidato: se rescatan los cerrados."""
    first = {"candidate_id": "cand_w2_0", "source": {"start": 0.0, "end": 10.0}}
    # max_tokens corta a mitad del segundo candidato: se conserva el cerrado.
    truncated = (
        '{"candidates": [{"candidate_id": "cand_w2_0", "source": {"start": 0.0, "end": 10.0}},'
        '{"source": {"start": 10.0'
    )
    parsed = candidates_module._extract_json(truncated)
    assert parsed == {"candidates": [first]}


def test_extract_json_salvages_when_the_last_candidate_is_sliced_by_a_newline():
    """Salto de linea crudo dentro de la cadena del ULTIMO candidato: el que
    viene cerrado antes se recupera igualmente."""
    first = {"candidate_id": "cand_w2_0", "source": {"start": 0.0, "end": 10.0}}
    broken = (
        '{"candidates": [{"candidate_id": "cand_w2_0", "source": {"start": 0.0, "end": 10.0}},'
        '{"candidate_id": "cand_w2_1", "summary": "linea cortada\n'
    )
    assert candidates_module._extract_json(broken) == {"candidates": [first]}


def test_extract_json_rejects_when_even_salvage_finds_nothing():
    with pytest.raises(candidates_module.ModelOutputError):
        candidates_module._extract_json('{"candidates": [{"source": {"start": 0')


# --- cuerpo -> candidato completo ---


def test_process_candidate_assembles_the_envelope():
    cand = candidates_module._process_candidate(
        dict(BODY_CANDIDATE),
        index=0,
        block_id="w2",
        duration=10.5,
        paragraphs=NORMALIZED_TRANSCRIPT["paragraphs"],
        full_text="Hola mundo",
        video_file="video_001.mp4",
        video_sha256=SHA,
        job_id="job_001",
        model_run={"provider": "lmstudio", "model": "gemma-fake"},
        blocks_min=1.0,
        blocks_max=90.0,
        drops=[],
    )
    assert cand is not None
    assert cand["candidate_id"] == "cand_w2_0"
    assert cand["job_id"] == "job_001"
    assert cand["schema_version"] == "1.0"
    assert cand["source"]["file"] == "video_001.mp4"
    assert cand["source"]["sha256"] == SHA
    assert cand["expansion"]["expanded"] is False
    assert cand["original_window"] == {
        "start": 1.0,
        "end": 4.0,
        "duration": 3.0,
    }
    # hook origin=generated debe quedar marcado (seccion 26): _clamp_hook_to_source
    # lo garantiza incluso si el modelo lo omite.
    assert cand["hook"]["requires_generated_hook"] is True


def test_process_candidate_drops_out_of_range_duration():
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 0.0, "end": 500.0}
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=10.5,
        paragraphs=[], full_text="", video_file="v", video_sha256=SHA,
        job_id="job_001", model_run={}, blocks_min=1.0, blocks_max=5.0, drops=drops,
    )
    assert cand is None
    assert any("fuera de" in drop for drop in drops)


def test_process_candidate_drops_without_hook():
    body = dict(BODY_CANDIDATE)
    del body["hook"]
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=10.5,
        paragraphs=[], full_text="", video_file="v", video_sha256=SHA,
        job_id="job_001", model_run={}, blocks_min=1.0, blocks_max=90.0, drops=drops,
    )
    assert cand is None
    assert any("hook" in drop for drop in drops)


def test_unfixable_mid_sentence_candidate_is_dropped():
    """Seccion 24: si la expansion no encuentra parrafo al que anclar, cae."""
    body = dict(BODY_CANDIDATE)
    body["progression"] = {"pattern": "none", "ends_complete": False}
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=10.5,
        paragraphs=[], full_text="", video_file="v", video_sha256=SHA,
        job_id="job_001", model_run={}, blocks_min=1.0, blocks_max=90.0, drops=drops,
    )
    assert cand is None
    assert any("ends_complete" in drop for drop in drops)


def test_mid_sentence_candidate_expands_to_paragraph_end():
    paragraphs = [
        {"id": 0, "text": "Una frase que se corta",
         "start": 1.0, "end": 3.0, "source": {"start": 1.0, "end": 3.0, "segment_ids": [0]}},
        {"id": 1, "text": "y aqui termina el parrafo completo",
         "start": 3.0, "end": 6.0, "source": {"start": 3.0, "end": 6.0, "segment_ids": [1]}},
    ]
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 1.0, "end": 3.2, "boundary_reason": "corta a media frase"}
    body["progression"] = {"pattern": "setup_payoff", "ends_complete": False}
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=10.5,
        paragraphs=paragraphs, full_text="Una frase que se corta y aqui termina",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=1.0, blocks_max=90.0, drops=drops,
    )
    assert cand is not None
    assert cand["source"]["end"] == 6.0
    assert cand["expansion"]["expanded"] is True
    assert cand["progression"]["ends_complete"] is True


def test_a_successful_anchor_is_not_reported_as_a_drop():
    """`drops` es la lista de descartes, no un cuaderno de avisos.

    El anclaje de `_expand_to_sentence_end` se anotaba en la misma lista que los
    descarte reales, asi que al surfaced `drops` en la CLI salia "DESCARTE fin de
    parrafo alojado" en clips que se habían salvado. El motivo del anclaje va en
    `expansion.reason`, que es donde se audita.
    """
    paragraphs = [
        _paragraph("Una frase que se corta", 1.0, 3.0, 0),
        _paragraph("y aqui termina el parrafo completo", 3.0, 6.0, 1),
    ]
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 1.0, "end": 3.2, "boundary_reason": "corta a media frase"}
    body["progression"] = {"pattern": "setup_payoff", "ends_complete": False}
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=10.5,
        paragraphs=paragraphs, full_text="Una frase que se corta y aqui termina",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=1.0, blocks_max=90.0, drops=drops,
    )
    assert cand is not None
    assert drops == [], f"un clip salvado no debe generar descartes: {drops}"


# --- silencio inicial (seccion 24, simetrica del final) ----------------------


def _paragraph(text: str, start: float, end: float, pid: int) -> dict:
    return {
        "id": pid, "text": text, "start": start, "end": end,
        "source": {"start": start, "end": end, "segment_ids": [pid]},
    }


def test_leading_silence_is_trimmed_to_first_voice():
    """El fallo real: un clip de 37,9s que abria con 13,7s de silencio."""
    paragraphs = [_paragraph("primera voz aqui", 27.74, 40.0, 0)]
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 14.0, "end": 51.94, "boundary_reason": "gancho"}
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=60.0,
        paragraphs=paragraphs, full_text="primera voz aqui",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=15.0, blocks_max=90.0, drops=drops,
    )
    assert cand is not None
    # El corte se ancla a la primera voz, no al inicio de la ventana muda.
    assert cand["source"]["start"] == 27.74
    assert cand["source"]["duration"] == 24.2
    # El hueco se contabiliza y es auditable.
    assert cand["expansion"]["pre_context_seconds"] == 13.74
    assert cand["expansion"]["expanded"] is True
    assert "silencio inicial" in cand["expansion"]["reason"]
    # Y la ventana que propuso el modelo queda intacta para poder auditarlo.
    assert cand["original_window"]["start"] == 14.0
    assert drops == []


def test_silence_below_tolerance_is_left_alone():
    paragraphs = [_paragraph("habla", 1.4, 6.0, 0)]
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 1.0, "end": 6.0}
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=10.5,
        paragraphs=paragraphs, full_text="habla",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=1.0, blocks_max=90.0, leading_silence_tolerance=1.5, drops=[],
    )
    assert cand is not None
    assert cand["source"]["start"] == 1.0
    assert cand["expansion"]["expanded"] is False


def test_paragraph_spanning_the_cut_is_not_silence():
    """Un parrafo que empieza antes del corte y sigue hablando NO es silencio."""
    paragraphs = [_paragraph("empieza antes y sigue", 0.0, 20.0, 0)]
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 5.0, "end": 8.0}
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=30.0,
        paragraphs=paragraphs, full_text="empieza antes y sigue",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=1.0, blocks_max=90.0, drops=[],
    )
    assert cand is not None
    assert cand["source"]["start"] == 5.0


def test_candidate_dropped_when_trim_leaves_too_little():
    """Recortar el silencio no puede dejar un clip por debajo del minimo."""
    paragraphs = [_paragraph("voz tardia", 27.74, 29.0, 0)]
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 14.0, "end": 31.0}
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=60.0,
        paragraphs=paragraphs, full_text="voz tardia",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=15.0, blocks_max=90.0, drops=drops,
    )
    assert cand is None
    assert any("silencio inicial" in drop for drop in drops)


def test_fully_silent_window_is_left_to_the_qc():
    """Sin voz no hay nada que anclar: no se inventa un corte."""
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 1.0, "end": 6.0}
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=10.5,
        paragraphs=[], full_text="habla",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=1.0, blocks_max=90.0, drops=[],
    )
    assert cand is not None
    assert cand["source"]["start"] == 1.0


def test_silence_trim_does_not_hide_an_unanchorable_ending():
    """Recortar el inicio no arregla un final a media frase: sigue cayendo."""
    paragraphs = [_paragraph("voz", 27.74, 40.0, 0)]
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 14.0, "end": 51.94}
    body["progression"] = {"pattern": "setup_payoff", "ends_complete": False}
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=60.0,
        paragraphs=paragraphs, full_text="voz",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=15.0, blocks_max=90.0, drops=drops,
    )
    assert cand is None
    assert any("ends_complete" in drop for drop in drops)


def test_trim_below_minimum_is_reported_as_silence_not_as_a_range_error():
    """El motivo del descarte tiene que decir la verdad: el hueco se comio el clip."""
    paragraphs = [_paragraph("voz tardia", 27.74, 29.0, 0)]
    body = dict(BODY_CANDIDATE)
    body["source"] = {"start": 14.0, "end": 31.0}
    drops: list[str] = []
    cand = candidates_module._process_candidate(
        body, index=0, block_id="w2", duration=60.0,
        paragraphs=paragraphs, full_text="voz tardia",
        video_file="v", video_sha256=SHA, job_id="job_001", model_run={},
        blocks_min=15.0, blocks_max=90.0, drops=drops,
    )
    assert cand is None
    assert len(drops) == 1
    assert "13.7s de silencio inicial" in drops[0]
    assert "3.3s" in drops[0]
    # Y no se mezcla con el error de rango: aqui la ventana SI cabia, lo que
    # no cabia era el contenido despues de quitar el hueco.
    assert "fuera de" not in drops[0]


# --- prompt (seccion 18) ---


def test_build_block_messages_contains_block_range_and_transcript():
    block = {"id": "w2", "level": 2, "start": 0.0, "end": 10.5, "text": "Hola mundo"}
    messages, truncated = candidates_module.build_block_messages(
        block,
        NORMALIZED_TRANSCRIPT["paragraphs"],
        CONTENT_MAP,
        language="es",
        duration=10.5,
        blocks_min=1.0,
        blocks_max=90.0,
        preferred_min=1.0,
        preferred_max=10.0,
    )
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "BLOQUE w2, rango [00:00-00:10]" in user
    assert "Hola mundo" in user
    assert truncated is False


def test_long_transcript_gets_truncated_centrally():
    block = {"id": "w2", "level": 2, "start": 0.0, "end": 10.5, "text": "x"}
    long_paragraph = [
        {"id": 0, "text": "a" * 8000, "start": 0.0, "end": 10.5,
         "source": {"start": 0.0, "end": 10.5, "segment_ids": [0]}}
    ]
    messages, truncated = candidates_module.build_block_messages(
        block,
        long_paragraph,
        CONTENT_MAP,
        language="es",
        duration=10.5,
        blocks_min=1.0,
        blocks_max=90.0,
        preferred_min=1.0,
        preferred_max=10.0,
        max_chars=1000,
    )
    assert truncated is True
    assert "[... contenido intermedio recortado por presupuesto ...]" in messages[1]["content"]


# --- bloques de nivel 2 (seccion 16) ---


def test_level2_blocks_are_used_in_order():
    doc = {
        "context_windows": [
            {"id": "w1", "level": 1, "start": 0.0, "end": 10.0, "text": ""},
            {"id": "w2", "level": 2, "start": 0.0, "end": 4.0, "text": ""},
            {"id": "w3", "level": 2, "start": 4.0, "end": 10.0, "text": ""},
        ]
    }
    blocks = candidates_module._level2_blocks(doc, 10.5)
    assert [block["id"] for block in blocks] == ["w2", "w3"]


def test_without_level2_windows_a_single_global_block_is_used():
    doc = {"context_windows": [
        {"id": "w1", "level": 1, "start": 0.0, "end": 10.0, "text": ""}
    ]}
    blocks = candidates_module._level2_blocks(doc, 10.5)
    assert len(blocks) == 1
    assert blocks[0]["id"] == "l2_global"
    assert blocks[0]["end"] == 10.5


# --- API principal (secciones 18-26, 51) ---


def test_generate_candidates_writes_a_valid_candidate_set(inputs, tmp_path):
    normalized_path, map_path = inputs
    client = FakeClient()
    repo = FakeRepo()
    target = tmp_path / "clip_candidates.json"

    result = candidates_module.generate_candidates(
        normalized_path,
        map_path,
        CONFIG,
        job_id="job_001",
        output_path=target,
        input_video="video_001.mp4",
        input_sha256=SHA,
        blocks_dir=str(tmp_path / "candidates"),
        client=client,
        repo=repo,
    )

    assert result.candidate_count == 1
    assert result.selected_count == 1
    assert result.dropped_count == 0
    assert result.block_count == 1
    assert result.reused is False
    assert client.calls == 1

    set_doc = artifacts.read_json(target, schema_name="candidate_set")
    assert set_doc["selection"]["selected"] == ["cand_w2_0"]
    candidate = set_doc["candidates"][0]
    assert candidate["candidate_id"] == "cand_w2_0"
    assert candidate["generated_by"]["model"] == "gemma-fake"
    assert candidate["score"]["total"] == pytest.approx(
        sum(candidate["score"][k] for k in SCORE_DIMENSION_MAX)
    )
    assert repo.runs, "la telemetria debe registrarse"
    assert repo.runs[0]["stage"] == "GENERATING_CANDIDATES"
    assert repo.runs[0]["succeeded"] is True


def test_generate_candidates_reuses_existing_set(tmp_path, inputs):
    normalized_path, map_path = inputs
    client = FakeClient()
    target = tmp_path / "clip_candidates.json"
    first = candidates_module.generate_candidates(
        normalized_path, map_path, CONFIG, job_id="job_001", output_path=target,
        input_sha256=SHA, blocks_dir=str(tmp_path / "candidates"),
        client=client, repo=FakeRepo(),
    )
    calls_after_first = client.calls

    second = candidates_module.generate_candidates(
        normalized_path, map_path, CONFIG, job_id="job_001", output_path=target,
        input_sha256=SHA, blocks_dir=str(tmp_path / "candidates"),
        client=client, repo=FakeRepo(), overwrite=False,
    )
    assert second.reused is True
    assert second.candidate_count == first.candidate_count
    assert client.calls == calls_after_first, "sin overwrite no se llama al modelo"


def _two_candidates_one_unanchorable() -> dict:
    """Candidato valido mas uno que el software no puede guardar.

    Se descarta en GENERACION (no en el ranking) porque declara
    `ends_complete: false` y no hay ningun parrafo al que anclar el final. Es
    justo el caso que antes solo contaba como "8 descartados" sin explicar nada.
    """
    bad = json.loads(json.dumps(BODY_CANDIDATE))
    bad["source"] = {"start": 0.0, "end": 10.5, "boundary_reason": "corta a media frase"}
    bad["progression"] = {"pattern": "setup_payoff", "ends_complete": False}
    return {"candidates": [BODY_CANDIDATE, bad]}


def test_generation_drops_are_persisted_with_their_reason(tmp_path, inputs):
    """D2: el descarte se guarda con MOTIVO, no solo como numero.

    Antes el artefacto decia "8 candidatos descartados" y no se podia saber
    por que, de modo que tocar el prompt era adivinar. Sin este rastro no hay
    forma de distinguir "el modelo propone ventanas cortas" de "el software
    esta recortando demasiado".
    """
    normalized_path, map_path = inputs
    target = tmp_path / "clip_candidates.json"
    result = candidates_module.generate_candidates(
        normalized_path, map_path, CONFIG, job_id="job_001", output_path=target,
        input_video="video_001.mp4", input_sha256=SHA,
        blocks_dir=str(tmp_path / "candidates"),
        client=FakeClient(_two_candidates_one_unanchorable()), repo=FakeRepo(),
    )

    set_doc = artifacts.read_json(target, schema_name="candidate_set")
    assert len(set_doc["drops"]) == 1
    assert "candidate[1]" in set_doc["drops"][0]
    assert "ends_complete" in set_doc["drops"][0]
    # El aviso sigue resumiendo el numero; el detalle va en `drops`.
    assert any("1 candidatos descartados" in w for w in set_doc["warnings"])
    # Y sale tambien por el resultado de la API, no solo por el fichero.
    assert result.dropped_generation == set_doc["drops"]


def test_generation_drops_survive_a_reuse_without_overwrite(tmp_path, inputs):
    """El camino de reuso tambien devolvia una lista vacia de descartes."""
    normalized_path, map_path = inputs
    target = tmp_path / "clip_candidates.json"
    body = _two_candidates_one_unanchorable()
    candidates_module.generate_candidates(
        normalized_path, map_path, CONFIG, job_id="job_001", output_path=target,
        input_video="video_001.mp4", input_sha256=SHA,
        blocks_dir=str(tmp_path / "candidates"),
        client=FakeClient(body), repo=FakeRepo(),
    )
    reused = candidates_module.generate_candidates(
        normalized_path, map_path, CONFIG, job_id="job_001", output_path=target,
        input_video="video_001.mp4", input_sha256=SHA,
        blocks_dir=str(tmp_path / "candidates"),
        client=FakeClient(body), repo=FakeRepo(), overwrite=False,
    )
    assert reused.reused is True
    assert len(reused.dropped_generation) == 1
    assert "ends_complete" in reused.dropped_generation[0]


def test_block_cache_avoids_recalling_on_retry(tmp_path, inputs):
    """Seccion 51: el cache por bloque evita repagar los bloques ya hechos."""
    normalized_path, map_path = inputs
    target = tmp_path / "clip_candidates.json"
    client = FakeClient()
    candidates_module.generate_candidates(
        normalized_path, map_path, CONFIG, job_id="job_001", output_path=target,
        input_sha256=SHA, blocks_dir=str(tmp_path / "candidates"),
        client=client, repo=FakeRepo(),
    )
    block_file = tmp_path / "candidates" / "block_w2.json"
    assert block_file.is_file()
    assert candidates_module._extract_json(block_file.read_text(encoding="utf-8"))


def test_identical_candidates_are_pruned_by_ranking(tmp_path, inputs):
    """Dos candidatos que cubren el mismo tramo: ranking descarta el segundo."""
    normalized_path, map_path = inputs
    client = FakeClient({"candidates": [dict(BODY_CANDIDATE), dict(BODY_CANDIDATE)]})
    target = tmp_path / "clip_candidates.json"
    result = candidates_module.generate_candidates(
        normalized_path, map_path, CONFIG, job_id="job_001", output_path=target,
        input_sha256=SHA, blocks_dir=str(tmp_path / "candidates"),
        client=client, repo=FakeRepo(),
    )
    assert result.candidate_count == 2
    assert result.selected_count == 1
    assert result.dropped_count == 1
    set_doc = artifacts.read_json(target, schema_name="candidate_set")
    assert len(set_doc["candidates"]) == 2
    assert set_doc["selection"]["dropped"][0]["candidate_id"] == "cand_w2_1"
    assert "se solapa" in set_doc["selection"]["dropped"][0]["reason"]