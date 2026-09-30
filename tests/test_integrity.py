"""Integridad de citas y defensa frente a prompt injection (secciones 26 y 61).

Tests offline: no necesitan LM Studio. La parte viva esta en
`test_llm_live.py::test_model_does_not_follow_injected_instructions`.

Origen de este modulo: Sprint 0 midio que google/gemma-3-4b Q4_K_M obedece una
orden inyectada en el transcript y devuelve un hook inventado con
origin="original". La conclusion practica es que la defensa no puede depender
del prompt: el software debe verificar toda cita contra el audio.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import integrity  # noqa: E402

TRANSCRIPT = (
    "La pregunta que me hacen siempre es por que la gente abandona un proyecto "
    "a las tres semanas. El primer resultado visible tarda demasiado y eso mata "
    "la motivacion antes de que exista el habito."
)


def test_normalize_strips_accents_case_and_punctuation():
    assert integrity.normalize("¿Por qué, la GENTE?") == "por que la gente"
    assert integrity.normalize("  Múltiples   espacios  ") == "multiples espacios"


def test_rejects_invented_hook_presented_as_original():
    hook = {"type": "bold_claim", "text": "HACK", "origin": "original"}
    fixed, violations, repairs = integrity.verify_hook(hook, TRANSCRIPT)

    assert violations, "una cita inventada como 'original' debe violar la seccion 26"
    assert fixed["origin"] == "generated"
    assert fixed["requires_generated_hook"] is True
    assert fixed["integrity_note"]
    assert repairs


def test_accepts_verbatim_quote():
    hook = {
        "type": "question",
        "text": "por que la gente abandona un proyecto a las tres semanas",
        "origin": "original",
    }
    fixed, violations, _ = integrity.verify_hook(hook, TRANSCRIPT)
    assert violations == []
    assert fixed["origin"] == "original"


def test_accepts_verbatim_quote_despite_accents_and_punctuation():
    transcript = "¿Por qué la gente abandona un proyecto a las tres semanas?"
    hook = {
        "type": "question",
        "text": "por que la gente abandona un proyecto",
        "origin": "original",
    }
    _, violations, _ = integrity.verify_hook(hook, transcript)
    assert violations == []


def test_tolerates_minor_transcription_differences():
    """Whisper y el modelo no siempre coinciden palabra por palabra."""
    transcript = "el primer resultado visible tarda demasiado y eso mata la motivacion"
    hook = {
        "type": "story_opening",
        "text": "el primer resultado visible tarda demasiado y eso mata la motivacion antes",
        "origin": "original",
    }
    _, violations, _ = integrity.verify_hook(hook, transcript)
    assert violations == [], "una diferencia menor no deberia marcar el hook como inventado"


def test_short_quote_is_never_accepted_as_literal():
    """Frases de una o dos palabras son demasiado faciles de acertar por azar."""
    hook = {"type": "none", "text": "la gente", "origin": "original"}
    _, violations, _ = integrity.verify_hook(hook, TRANSCRIPT)
    assert violations


def test_generated_hook_is_not_touched():
    hook = {
        "type": "bold_claim",
        "text": "Nadie te dijo esto sobre abandonar proyectos.",
        "origin": "generated",
        "requires_generated_hook": True,
    }
    fixed, violations, repairs = integrity.verify_hook(hook, TRANSCRIPT)
    assert violations == []
    assert repairs == []
    assert fixed == hook


def test_empty_hook_text_is_a_violation():
    fixed, violations, _ = integrity.verify_hook({"origin": "original", "text": "  "}, TRANSCRIPT)
    assert violations


def test_no_transcript_means_integrity_cannot_confirm():
    """Sin transcript no se puede afirmar que algo fue pronunciado."""
    hook = {"type": "question", "text": "una frase cualquiera que nadie dijo", "origin": "original"}
    fixed, violations, _ = integrity.verify_hook(hook, "")
    assert violations
    assert fixed["origin"] == "generated"


def test_document_hooks_are_all_verified():
    document = {
        "hook": {"type": "question", "text": "HACK", "origin": "original"},
        "potential_hooks": [
            {
                "type": "question",
                "text": "por que la gente abandona un proyecto",
                "origin": "original",
            },
            {"type": "bold_claim", "text": "frase totalmente inventada aqui", "origin": "original"},
        ],
    }
    result = integrity.verify_document_hooks(document, TRANSCRIPT)

    assert not result.ok
    assert len(result.violations) == 2
    assert result.document["hook"]["origin"] == "generated"
    assert result.document["potential_hooks"][0]["origin"] == "original"
    assert result.document["potential_hooks"][1]["origin"] == "generated"
    assert any(v.startswith("potential_hooks[1]") for v in result.violations)
