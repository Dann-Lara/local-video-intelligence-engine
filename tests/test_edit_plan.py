"""Tests del Edit Plan (seccion 27).

Un plan por clip seleccionado, deterministico y valido contra
`edit_plan.schema.json`. `build_edit_plans` no toca red: el punto de entrada es
el `candidate_set` ya validado.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis import edit_plan as edit_plan_module  # noqa: E402
from src.core import artifacts  # noqa: E402

from test_schemas import CANDIDATE_SET  # noqa: E402
from copy import deepcopy  # noqa: E402


@pytest.fixture
def candidate_set_file(tmp_path):
    """Escribe el conjunto del contrato en disco (valida schema + invariantes)."""
    target = tmp_path / "clip_candidates.json"
    info = artifacts.write_json(target, CANDIDATE_SET, schema_name="candidate_set")
    return target


def test_one_edit_plan_per_selected_candidate(candidate_set_file, tmp_path):
    plans_dir = tmp_path / "edit_plans"
    result = edit_plan_module.build_edit_plans(
        candidate_set_file,
        {},
        job_id="job_001",
        output_dir=plans_dir,
        input_video="video_001.mp4",
    )
    assert result.clip_count == 1
    assert len(result.paths) == 1
    plan_path = Path(result.paths[0])
    assert plan_path.name == "edit_plan_cand_001.json"

    plan = artifacts.read_json(plan_path, schema_name="edit_plan")
    assert plan["clip_id"] == "cand_001"
    assert plan["candidate_id"] == "cand_001"
    assert plan["job_id"] == "job_001"
    assert plan["source"] == {
        "file": "video_001.mp4",
        "start": 10.0,
        "end": 40.0,
        "duration": 30.0,
    }
    # El hook del candidato viaja al plan con su integridad (seccion 26).
    assert plan["hook"]["type"] == "question"
    assert plan["hook"]["origin"] == "original"
    # El crop es center en el MVP (seccion 30), nunca asumir el centro del sujeto.
    assert plan["crop"]["mode"] == "center"


def test_plan_respects_cap_of_three_subtitle_lines(candidate_set_file, tmp_path):
    """Seccion 31 + invariante de edit_plan: max_lines <= 3."""
    plan_path = Path(
        edit_plan_module.build_edit_plans(
            candidate_set_file, {}, output_dir=tmp_path / "edit_plans"
        ).paths[0]
    )
    plan = artifacts.read_json(plan_path, schema_name="edit_plan")
    assert plan["subtitles"]["max_lines"] <= 3
    assert plan["output"]["width"] == 1080
    assert plan["output"]["height"] == 1920


def test_no_selection_means_no_plans(tmp_path):
    set_doc = deepcopy(CANDIDATE_SET)
    set_doc["selection"]["selected"] = []
    target = tmp_path / "clip_candidates.json"
    artifacts.write_json(target, set_doc, schema_name="candidate_set")

    result = edit_plan_module.build_edit_plans(
        target, {}, output_dir=tmp_path / "edit_plans"
    )
    assert result.clip_count == 0
    assert result.paths == []
    assert not (tmp_path / "edit_plans").exists() or not any((tmp_path / "edit_plans").iterdir())


def test_default_output_dir_is_under_the_set(tmp_path):
    target = tmp_path / "clip_candidates.json"
    artifacts.write_json(target, CANDIDATE_SET, schema_name="candidate_set")

    result = edit_plan_module.build_edit_plans(target, {}, job_id="job_001")
    assert Path(result.paths[0]).parent == tmp_path / "edit_plans"


def test_plans_are_deterministic(candidate_set_file, tmp_path):
    first = edit_plan_module.build_edit_plans(
        candidate_set_file, {}, output_dir=tmp_path / "a"
    ).paths
    second = edit_plan_module.build_edit_plans(
        candidate_set_file, {}, output_dir=tmp_path / "b"
    ).paths
    assert Path(first[0]).read_text(encoding="utf-8") == Path(second[0]).read_text(encoding="utf-8")