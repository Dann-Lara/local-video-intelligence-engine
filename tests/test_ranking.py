"""Tests del Ranking Engine (secciones 20-23).

`select_candidates` es pura: no toca red ni base de datos. Se le pasa un
`candidate_set` minimal y una config, y se comprueba el orden de las reglas:
umbral de score, solape temporal, diversidad, target_maximum y warnings.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis import ranking  # noqa: E402

CONFIG = {
    "selection": {
        "minimum_score": 72.0,
        "target_minimum": 3,
        "target_maximum": 10,
        "diversity_threshold": 0.80,
        "max_temporal_overlap": 0.5,
    }
}


def _candidate(
    candidate_id: str,
    total: float,
    start: float,
    end: float,
    *,
    topic_signature: str | None = None,
    semantic_signature: str | None = None,
) -> dict:
    candidate = {
        "candidate_id": candidate_id,
        "source": {"start": start, "end": end},
        "score": {"total": total},
    }
    if topic_signature is not None or semantic_signature is not None:
        candidate["signatures"] = {
            "topic_signature": topic_signature,
            "semantic_signature": semantic_signature,
        }
    return candidate


def _select(candidates, config=None):
    selection, warnings = ranking.select_candidates(
        {"candidates": candidates}, config or CONFIG
    )
    return selection, warnings


def test_score_below_minimum_is_dropped():
    selection, _ = _select([_candidate("a", 71.0, 0.0, 20.0)])
    assert selection["selected"] == []
    assert selection["dropped"][0]["candidate_id"] == "a"
    assert "por debajo del minimo" in selection["dropped"][0]["reason"]


def test_score_at_minimum_is_selected():
    selection, _ = _select([_candidate("a", 72.0, 0.0, 20.0)])
    assert selection["selected"] == ["a"]


def test_output_is_deterministic_score_desc_start_asc():
    """Seccion 2.4: mismo conjunto + misma config = misma seleccion."""
    candidates = [
        _candidate("a", 75.0, 40.0, 60.0),
        _candidate("b", 90.0, 5.0, 25.0),
        _candidate("c", 75.0, 0.0, 10.0),
    ]
    first, _ = _select(candidates)
    second, _ = _select(candidates)
    assert first["selected"] == ["b", "c", "a"]
    assert first == second


def test_temporal_overlap_drops_the_lower_score():
    """Dos clips del mismo tramo: sobrevive el de mayor score (seccion 23)."""
    candidates = [
        _candidate("a", 80.0, 0.0, 20.0),
        _candidate("b", 74.0, 5.0, 20.0),
    ]
    selection, _ = _select(candidates)
    assert selection["selected"] == ["a"]
    assert selection["dropped"][0]["candidate_id"] == "b"
    assert "se solapa" in selection["dropped"][0]["reason"]


def test_adjacent_clips_are_not_overlapping():
    candidates = [
        _candidate("a", 80.0, 0.0, 10.0),
        _candidate("b", 74.0, 10.0, 20.0),
    ]
    selection, _ = _select(candidates)
    assert selection["selected"] == ["a", "b"]


def test_same_topic_with_overlap_is_a_duplicate():
    """Misma topic_signature + solape temporal no nulo = duplicado (seccion 22)."""
    candidates = [
        _candidate("a", 80.0, 0.0, 20.0, topic_signature="topic:x"),
        _candidate("b", 74.0, 16.0, 30.0, topic_signature="topic:x"),
    ]
    selection, _ = _select(candidates)
    assert selection["selected"] == ["a"]
    assert selection["dropped"][0]["candidate_id"] == "b"
    assert "topic_signature" in selection["dropped"][0]["reason"]


def test_same_topic_without_overlap_is_not_a_duplicate():
    candidates = [
        _candidate("a", 80.0, 0.0, 20.0, topic_signature="topic:x"),
        _candidate("b", 74.0, 40.0, 60.0, topic_signature="topic:x"),
    ]
    selection, _ = _select(candidates)
    assert selection["selected"] == ["a", "b"]


def test_semantic_signature_too_similar_is_dropped():
    candidates = [
        _candidate("a", 80.0, 0.0, 20.0, semantic_signature="explica como funciona el corte vertical"),
        _candidate("b", 74.0, 30.0, 50.0, semantic_signature="explica como funciona el corte vertical"),
    ]
    selection, _ = _select(candidates)
    assert selection["selected"] == ["a"]
    assert "semantic_signature" in selection["dropped"][0]["reason"]


def test_distinct_semantic_signatures_both_selected():
    candidates = [
        _candidate("a", 80.0, 0.0, 20.0, semantic_signature="teoria de autodeterminacion"),
        _candidate("b", 74.0, 30.0, 50.0, semantic_signature="recetas de cocina vegana"),
    ]
    selection, _ = _select(candidates)
    assert selection["selected"] == ["a", "b"]


def test_target_maximum_caps_the_selection():
    """Seccion 23: 'nunca mas que target_maximum'; el ranking elige los mejores."""
    candidates = [_candidate(f"c{i}", 90.0 - i, float(i) * 30.0, float(i) * 30.0 + 20.0) for i in range(12)]
    selection, _ = _select(candidates)
    assert len(selection["selected"]) == 10
    assert selection["dropped"][-1]["reason"].startswith("se supera target_maximum")


def test_below_target_minimum_warns_but_does_not_invent():
    selection, warnings = _select([_candidate("a", 80.0, 0.0, 20.0)])
    assert selection["selected"] == ["a"]
    assert any("target_minimum" in warning for warning in warnings)


def test_nothing_selected_triggers_quality_warning():
    selection, warnings = _select([_candidate("a", 10.0, 0.0, 20.0)])
    assert selection["selected"] == []
    assert any("ningun candidato" in warning for warning in warnings)


def test_empty_candidate_list_is_valid_without_warnings():
    selection, warnings = _select([])
    assert selection["selected"] == []
    assert selection["dropped"] == []
    # Con target_minimum=3 avisa de que no llega, pero no inventa candidatos.
    assert any("target_minimum" in warning for warning in warnings)


def test_selection_block_records_parameters():
    selection, _ = _select([_candidate("a", 80.0, 0.0, 20.0)])
    assert selection["minimum_score"] == 72.0
    assert selection["max_temporal_overlap"] == 0.5
    assert selection["diversity_threshold"] == 0.80
    assert selection["target_minimum"] == 3
    assert selection["target_maximum"] == 10


def test_config_defaults_apply_without_selection_section():
    selection, _ = ranking.select_candidates(
        {"candidates": [_candidate("a", 80.0, 0.0, 20.0)]}, {}
    )
    assert selection["minimum_score"] == 72.0
    assert selection["selected"] == ["a"]