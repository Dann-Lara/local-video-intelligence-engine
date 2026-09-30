"""Tests de las invariantes semanticas (blueprint secciones 20, 23, 63)."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import contracts, invariants  # noqa: E402

from test_schemas import (  # noqa: E402
    CANDIDATE,
    CANDIDATE_SET,
    CLIP_RECORD,
    CONTENT_MAP,
    EDIT_PLAN,
    NORMALIZED_TRANSCRIPT,
    SCORE,
    TRANSCRIPT,
)


def test_all_shipped_examples_are_coherent():
    """Ningun ejemplo valido del contrato debe violar una invariante."""
    for name, doc in [
        ("transcript", TRANSCRIPT),
        ("normalized_transcript", NORMALIZED_TRANSCRIPT),
        ("content_map", CONTENT_MAP),
        ("candidate", CANDIDATE),
        ("candidate_set", CANDIDATE_SET),
        ("edit_plan", EDIT_PLAN),
        ("clip_record", CLIP_RECORD),
    ]:
        assert contracts.is_valid(name, doc), contracts.errors_for(name, doc)


# --- start < end (seccion 63) ---


def test_candidate_rejects_inverted_time_range():
    doc = deepcopy(CANDIDATE)
    doc["source"]["start"] = 100.0
    doc["source"]["end"] = 50.0
    assert not invariants.is_invariant("candidate", doc)
    assert any("menor que end" in e for e in invariants.invariant_errors("candidate", doc))


def test_edit_plan_rejects_inverted_time_range():
    doc = deepcopy(EDIT_PLAN)
    doc["source"]["start"] = 40.0
    doc["source"]["end"] = 10.0
    doc["source"]["duration"] = -30.0
    assert not invariants.is_invariant("edit_plan", doc)


def test_candidate_rejects_duration_mismatch():
    doc = deepcopy(CANDIDATE)
    doc["source"]["duration"] = 999.0
    assert not invariants.is_invariant("candidate", doc)


# --- scoring (seccion 20) ---


def test_total_must_equal_sum_of_dimensions():
    doc = deepcopy(CANDIDATE)
    doc["score"]["total"] = 50  # la suma real es 78
    errors = invariants.invariant_errors("candidate", doc)
    assert any("no es la suma" in e for e in errors)


def test_dimension_above_its_cap_is_rejected():
    doc = deepcopy(CANDIDATE)
    doc["score"]["standalone"] = 6  # maximo 5
    errors = invariants.invariant_errors("candidate", doc)
    assert any("excede el maximo 5" in e for e in errors)


def test_score_caps_sum_to_one_hundred():
    assert sum(invariants.SCORE_DIMENSION_MAX.values()) == invariants.SCORE_MAX_TOTAL == 100


# --- integridad de hook (secciones 25 y 26) ---


def test_generated_hook_without_flag_is_a_violation():
    doc = deepcopy(CANDIDATE)
    doc["hook"]["origin"] = "generated"
    doc["hook"]["requires_generated_hook"] = False
    errors = invariants.invariant_errors("candidate", doc)
    assert any("requires_generated_hook" in e for e in errors)


def test_original_hook_does_not_require_the_flag():
    doc = deepcopy(CANDIDATE)
    doc["hook"]["origin"] = "original"
    doc["hook"]["requires_generated_hook"] = False
    assert invariants.is_invariant("candidate", doc)


# --- fin de clip (seccion 19) ---


def test_clip_ending_mid_sentence_is_a_violation():
    doc = deepcopy(CANDIDATE)
    doc["progression"] = {"pattern": "none", "ends_complete": False}
    errors = invariants.invariant_errors("candidate", doc)
    assert any("media frase" in e for e in errors)


# --- candidate set (secciones 20-23) ---


def test_candidate_set_selection_cannot_reference_ghost_candidates():
    doc = deepcopy(CANDIDATE_SET)
    doc["selection"]["selected"] = ["cand_no_existe"]
    errors = invariants.invariant_errors("candidate_set", doc)
    assert any("no existe entre los candidatos" in e for e in errors)


def test_candidate_set_dropped_must_reference_existing_candidates():
    doc = deepcopy(CANDIDATE_SET)
    doc["selection"]["dropped"] = [{"candidate_id": "fantasma", "reason": "duplicado"}]
    errors = invariants.invariant_errors("candidate_set", doc)
    assert any("no existe entre los candidatos" in e for e in errors)


def test_candidate_set_checks_score_of_each_candidate():
    doc = deepcopy(CANDIDATE_SET)
    doc["candidates"][0]["score"]["total"] = 50  # la suma real es 78
    errors = invariants.invariant_errors("candidate_set", doc)
    assert any("candidates[0]" in e and "no es la suma" in e for e in errors)


# --- verticalidad (seccion 30) ---


def test_horizontal_output_is_a_violation():
    doc = deepcopy(EDIT_PLAN)
    doc["output"]["width"] = 1920
    doc["output"]["height"] = 1080
    errors = invariants.invariant_errors("edit_plan", doc)
    assert any("no es vertical" in e for e in errors)


# --- duracion (seccion 23) ---


@pytest.mark.parametrize("duration", [15, 20, 45, 60, 90])
def test_durations_inside_limits_are_accepted(duration):
    assert invariants.check_duration_within_limits(duration) == []


@pytest.mark.parametrize("duration", [5, 14.9, 91, 300])
def test_durations_outside_limits_are_rejected(duration):
    assert invariants.check_duration_within_limits(duration)


def test_beyond_preferred_range_is_still_valid():
    """Seccion 23: se puede alargar si la estructura narrativa lo justifica."""
    assert invariants.check_duration_within_limits(75) == []


# --- metricas: null vs 0 (seccion 38) ---


def test_zero_used_as_unknown_is_rejected():
    doc = deepcopy(CLIP_RECORD)
    doc["performance"][0]["metrics"]["engagement"]["shares"] = 0
    errors = invariants.invariant_errors("clip_record", doc)
    assert any("usar null" in e for e in errors)


# --- transcript ---


def test_transcript_segments_must_not_move_backwards():
    doc = deepcopy(TRANSCRIPT)
    doc["segments"].append({"id": 1, "start": 0.5, "end": 2.0, "text": "atras"})
    errors = invariants.invariant_errors("transcript", doc)
    assert any("retrocede" in e for e in errors)


def test_transcript_rejects_segment_with_end_before_start():
    doc = deepcopy(TRANSCRIPT)
    doc["segments"][0]["end"] = 0.0
    assert not invariants.is_invariant("transcript", doc)


# --- context windows (seccion 16) ---


def test_level_1_context_window_outside_30_120_seconds():
    doc = deepcopy(NORMALIZED_TRANSCRIPT)
    doc["duration"] = 400.0
    doc["context_windows"][0]["end"] = 200.0
    errors = invariants.invariant_errors("normalized_transcript", doc)
    assert any("nivel 1" in e for e in errors)


def test_level_1_context_window_inside_range_passes():
    doc = deepcopy(NORMALIZED_TRANSCRIPT)
    doc["duration"] = 400.0
    doc["context_windows"][0]["end"] = 95.0
    assert invariants.is_invariant("normalized_transcript", doc)


def test_short_source_is_exempt_from_level_1_minimum():
    """Un video de 10s no puede producir una ventana de nivel 1 de 30s."""
    doc = deepcopy(NORMALIZED_TRANSCRIPT)
    assert doc["duration"] < invariants.CONTEXT_WINDOW_LEVEL_1[0]
    assert invariants.is_invariant("normalized_transcript", doc)


# --- contracts ---


def test_enforce_raises_on_bad_artifact():
    doc = deepcopy(EDIT_PLAN)
    doc["output"]["width"] = 1920
    doc["output"]["height"] = 1080
    with pytest.raises(contracts.ContractViolation) as exc:
        contracts.enforce("edit_plan", doc)
    assert exc.value.invariant_errors


def test_enforce_returns_document_when_valid():
    assert contracts.enforce("candidate", CANDIDATE) is CANDIDATE
