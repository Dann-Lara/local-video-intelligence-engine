"""Tests de los contratos JSON Schema (blueprint secciones 60 y 63)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import schemas  # noqa: E402
from src.core.invariants import is_invariant  # noqa: E402

SHA = "a" * 64

MODEL_RUN = {
    "provider": "lmstudio",
    "model": "gemma-3-4b-it",
    "prompt_version": "candidate-v1.0",
    "temperature": 0.1,
}

HOOK = {
    "type": "question",
    "text": "¿Por que falla esto?",
    "origin": "original",
    "time": {"start": 4.2, "end": 6.0},
}

SCORE = {
    "total": 78,
    "hook": 16,
    "curiosity": 12,
    "value": 12,
    "emotion": 8,
    "narrative": 8,
    "payoff": 8,
    "shareability": 7,
    "standalone": 4,
    "visual": 3,
}

TRANSCRIPT = {
    "schema_version": "1.0",
    "language": "es",
    "duration": 10.5,
    "segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 4.2,
            "text": "Hola mundo",
            "words": [
                {"word": "Hola", "start": 0.0, "end": 0.4, "probability": 0.95},
                {"word": "mundo", "start": 0.45, "end": 0.9, "probability": None},
            ],
        }
    ],
    "generator": {
        "provider": "faster_whisper",
        "model": "large-v3-turbo",
        "parameters": {"compute_type": "int8_float16"},
    },
}

NORMALIZED_TRANSCRIPT = {
    "schema_version": "1.0",
    "derived_from": {
        "file": "transcript.json",
        "sha256": SHA,
        "schema_version": "1.0",
    },
    "language": "es",
    "duration": 10.5,
    "paragraphs": [
        {
            "id": 0,
            "text": "Hola mundo",
            "start": 0.0,
            "end": 4.2,
            "source": {"start": 0.0, "end": 4.2, "segment_ids": [0]},
        }
    ],
    "context_windows": [
        {"id": "w1", "level": 1, "start": 0.0, "end": 10.5, "text": "Hola mundo"}
    ],
    "full_text": "Hola mundo",
}

CONTENT_MAP = {
    "schema_version": "1.0",
    "language": "es",
    "duration": 10.5,
    "generated_by": MODEL_RUN,
    "topics": [
        {
            "label": "saludo",
            "time": {"start": 0.0, "end": 4.2},
            "signature": "topic:saludo",
        }
    ],
    "potential_hooks": [HOOK],
}

CANDIDATE = {
    "schema_version": "1.0",
    "candidate_id": "cand_001",
    "job_id": "job_001",
    "source": {"file": "video_001.mp4", "sha256": SHA, "start": 10.0, "end": 40.0},
    "content": {"topic": "explicacion", "reason": "setup + payoff"},
    "hook": HOOK,
    "score": SCORE,
    "generated_by": MODEL_RUN,
}

# El cuerpo de candidatos es lo que Gemma emite por bloque (seccion 60): los
# candidatos SIN el envoltorio que firma el software (schema_version,
# candidate_id, job_id, source.file/sha256, generated_by). El total del score
# es opcional: la capa de generacion lo recalcula en software (seccion 20).
CANDIDATE_BODY = {
    "candidates": [
        {
            "source": {
                "start": 10.0,
                "end": 40.0,
                "boundary_reason": "la frase cierra en 40.0",
            },
            "content": {
                "topic": "explicacion",
                "summary": "Como funciona el corte de frases",
                "reason": "setup + payoff",
                "content_type": "explanation",
            },
            "hook": dict(HOOK),
            "progression": {
                "pattern": "setup_payoff",
                "beats": ["setup", "payoff"],
                "has_payoff": True,
                "ends_complete": True,
            },
            "value_provided": ["information"],
            "signatures": {
                "topic_signature": "topic:explicacion",
                "semantic_signature": "sem:explicacion",
            },
            "score": {
                key: SCORE[key]
                for key in SCORE
                if key != "total"
            },
        }
    ]
}

CANDIDATE_SET = {
    "schema_version": "1.0",
    "job_id": "job_001",
    "source": {"file": "video_001.mp4", "sha256": SHA, "duration": 1228.0},
    "block_count": 1,
    "content_map_sha256": SHA,
    "normalized_transcript_sha256": SHA,
    "candidates": [CANDIDATE],
    "selection": {
        "minimum_score": 72.0,
        "max_temporal_overlap": 0.5,
        "diversity_threshold": 0.8,
        "target_minimum": 3,
        "target_maximum": 10,
        "selected": ["cand_001"],
        "dropped": [],
    },
    "warnings": [],
}

EDIT_PLAN = {
    "schema_version": "1.0",
    "clip_id": "clip_001",
    "job_id": "job_001",
    "source": {"file": "video_001.mp4", "start": 10.0, "end": 40.0, "duration": 30.0},
    "output": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "format": "9:16",
        "video_codec": "h264_nvenc",
        "audio_codec": "aac",
        "container": "mp4",
    },
    "crop": {"mode": "center", "background": "crop"},
    "subtitles": {"enabled": True, "max_words_per_line": 4, "max_lines": 2},
    "hook": {"type": "question", "origin": "original"},
    "audio": {"normalize": True},
}

PERFORMANCE_SNAPSHOT = {
    "platform": "youtube_shorts",
    "captured_at": "2026-09-30T18:00:00Z",
    "age_hours": 24,
    "metrics": {
        "exposure": {"views": 12450, "shown_in_feed": None, "unique_viewers": None},
        "engagement": {"likes": 812, "comments": 94, "shares": None},
        "retention": {"average_percentage_viewed": 78.4},
    },
    "derived_metrics": {"like_rate": 0.0652, "comment_rate": 0.0075},
    "derived_metrics_version": "1.0",
}

CLIP_RECORD = {
    "schema_version": "1.0",
    "clip_id": "clip_001",
    "job_id": "job_001",
    "source": {
        "file": "video_001.mp4",
        "sha256": SHA,
        "start": 10.0,
        "end": 40.0,
        "duration": 30.0,
    },
    "content": {
        "topic": "explicacion",
        "hook_type": "question",
        "content_type": "explanation",
    },
    "selection": {"score": 80, "hook": 16, "curiosity": 12},
    "generation": {
        "model": "gemma-3-4b-it",
        "provider": "lmstudio",
        "prompt_version": "candidate-v1.0",
    },
    "editing": {"format": "9:16", "width": 1080, "height": 1920, "fps": 30},
    "qc": {"passed": True, "problems": []},
    "performance": [PERFORMANCE_SNAPSHOT],
}

HOOK_EXTRACTION = {
    "schema_version": "1.0",
    "type": "question",
    "origin": "original",
    "text": "La pregunta que me hacen siempre es por que la gente abandona a las tres semanas.",
    "requires_generated_hook": False,
    "time": {"start": 4.0, "end": 9.5},
    "reason": "Pregunta directa que abre un hueco de curiosidad.",
    "integrity_note": None,
}

# El cuerpo del content map es el schema que Gemma debe emitir (seccion 60):
# el CONTENT_MAP completo menos el envoltorio que firma el software, con todas
# las categorias de momento presentes (todas son obligatorias).
CONTENT_MAP_BODY = {
    key: CONTENT_MAP[key]
    for key in CONTENT_MAP
    if key not in {"schema_version", "job_id", "language", "duration", "generated_by"}
}
CONTENT_MAP_BODY.setdefault("global_summary", "Resumen generico del material de prueba.")
for _category in (
    "entities",
    "claims",
    "stories",
    "questions",
    "answers",
    "conflicts",
    "revelations",
    "surprising_points",
    "emotional_moments",
    "humorous_moments",
    "actionable_points",
    "strong_statements",
    "memorable_phrases",
    "conclusions",
    "calls_to_action",
    "topic_changes",
):
    CONTENT_MAP_BODY.setdefault(_category, [])

VALID_INSTANCES = {
    "transcript": TRANSCRIPT,
    "normalized_transcript": NORMALIZED_TRANSCRIPT,
    "content_map": CONTENT_MAP,
    "content_map_body": CONTENT_MAP_BODY,
    "candidate": CANDIDATE,
    "candidate_body": CANDIDATE_BODY,
    "candidate_set": CANDIDATE_SET,
    "hook_extraction": HOOK_EXTRACTION,
    "edit_plan": EDIT_PLAN,
    "clip_record": CLIP_RECORD,
    "performance_snapshot": PERFORMANCE_SNAPSHOT,
}


def test_all_registered_schemas_load():
    assert set(schemas.available()) == set(VALID_INSTANCES)


@pytest.mark.parametrize("name", sorted(VALID_INSTANCES))
def test_minimal_valid_instance(name):
    assert schemas.iter_errors(name, VALID_INSTANCES[name]) == []


def test_unknown_schema_raises():
    with pytest.raises(schemas.SchemaNotFoundError):
        schemas.validator("no_existe")


def test_transcript_requires_english_keys_only():
    """El contrato es estricto: campos no declarados se rechazan (seccion 77)."""
    instance = dict(TRANSCRIPT)
    instance["campo_inventado"] = 1
    assert not schemas.is_valid("transcript", instance)


def test_source_rejects_negative_bounds():
    instance = dict(EDIT_PLAN)
    source = dict(instance["source"])
    source["start"] = -1.0
    instance["source"] = source
    assert not schemas.is_valid("edit_plan", instance)


def test_source_end_must_be_exclusive_minimum_zero():
    instance = dict(EDIT_PLAN)
    source = dict(instance["source"])
    source["end"] = 0.0
    instance["source"] = source
    assert not schemas.is_valid("edit_plan", instance)


def test_start_before_end_is_enforced_by_invariants_not_schema():
    """JSON Schema (draft 2020-12) no puede comparar dos campos.

    Seccion 63 exige 'inicio < final': esa invariante vive en
    src.core.invariants, no en el schema. Este test documenta el limite.
    """
    instance = dict(EDIT_PLAN)
    source = dict(instance["source"])
    source["start"] = 40.0
    source["end"] = 10.0
    instance["source"] = source
    assert schemas.is_valid("edit_plan", instance), "el schema no puede compararlos"
    assert not is_invariant("edit_plan", instance), "la capa semantica si"


def test_score_dimension_cannot_exceed_its_cap():
    """Cada dimension de la seccion 20 tiene tope propio; 20 es el maximo de hook."""
    instance = dict(CANDIDATE)
    score = dict(SCORE)
    score["hook"] = 21
    instance["score"] = score
    assert not schemas.is_valid("candidate", instance)


def test_score_visual_cannot_exceed_five():
    instance = dict(CANDIDATE)
    score = dict(SCORE)
    score["visual"] = 6
    instance["score"] = score
    assert not schemas.is_valid("candidate", instance)


def test_total_score_cannot_exceed_100():
    instance = dict(CANDIDATE)
    score = dict(SCORE)
    score["total"] = 101
    instance["score"] = score
    assert not schemas.is_valid("candidate", instance)


def test_candidate_body_rejects_score_total():
    """Seccion 20: el total lo cierra el software, nunca lo emite Gemma.

    El schema del cuerpo de candidatos no declara `total` en el score
    (additionalProperties=false), de modo que un modelo que lo mande se
    rechaza en vez de confiarse.
    """
    instance = dict(CANDIDATE_BODY)
    score = dict(instance["candidates"][0]["score"])
    score["total"] = 78
    instance["candidates"][0]["score"] = score
    assert not schemas.is_valid("candidate_body", instance)


def test_candidate_body_rejects_candidate_wrapping_fields():
    """El cuerpo no admite el envoltorio que firma el software (candidate_id,
    job_id, source.file...): esos son del modulo de generacion, no del LLM."""
    instance = dict(CANDIDATE_BODY)
    instance["candidates"][0]["candidate_id"] = "cand_001"
    assert not schemas.is_valid("candidate_body", instance)


def test_candidate_set_validates_each_nested_candidate():
    instance = dict(CANDIDATE_SET)
    bad = dict(CANDIDATE)
    score = dict(SCORE)
    score["hook"] = 21
    bad["score"] = score
    instance["candidates"] = [bad]
    assert not schemas.is_valid("candidate_set", instance)


def test_generated_hook_must_be_flagged():
    """Seccion 26: un hook generado nunca se presenta como pronunciado."""
    instance = dict(CANDIDATE)
    hook = dict(HOOK)
    hook["origin"] = "generated"
    hook["requires_generated_hook"] = True
    instance["hook"] = hook
    assert schemas.is_valid("candidate", instance)


def test_hook_type_must_be_known():
    instance = dict(CANDIDATE)
    hook = dict(HOOK)
    hook["type"] = "no_existe"
    instance["hook"] = hook
    assert not schemas.is_valid("candidate", instance)


def test_unknown_metric_is_null_not_zero():
    """Seccion 38: 'no proporcionado' es null, nunca 0."""
    snapshot = {
        "platform": "youtube_shorts",
        "captured_at": "2026-09-30T18:00:00Z",
        "metrics": {"engagement": {"likes": None, "shares": None}},
    }
    assert schemas.is_valid("performance_snapshot", snapshot)


def test_performance_snapshot_rejects_unknown_platform():
    snapshot = dict(PERFORMANCE_SNAPSHOT)
    snapshot["platform"] = ""
    assert not schemas.is_valid("performance_snapshot", snapshot)


def test_clip_record_can_have_empty_performance():
    """Seccion 36: en el MVP el clip se registra antes de publicarse."""
    record = dict(CLIP_RECORD)
    record["performance"] = []
    assert schemas.is_valid("clip_record", record)


def test_clip_record_nested_snapshot_is_validated():
    record = dict(CLIP_RECORD)
    bad = dict(PERFORMANCE_SNAPSHOT)
    bad["platform"] = 123
    record["performance"] = [bad]
    assert not schemas.is_valid("clip_record", record)
