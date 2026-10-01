"""Los prompts viven en ficheros y su version sale del contenido (seccion 54).

El contrato que se vigila aqui es deliberadamente boring: que cada stage tenga
sus dos ficheros, que el version se derive del texto y no de un numero escrito
a mano, y que las plantillas noueuseen de llaves que rompan `.format()` al
editarlas. Un error de cualquiera de estos no se ve en produccion hasta que el
prompt deja de llegar al modelo.
"""

from __future__ import annotations

import pytest

from src.core import prompts

STAGES = ("content_analysis", "candidate_detection", "quality_control", "seo")


def test_every_stage_has_its_two_prompt_files():
    for stage in STAGES:
        assert prompts.load("system", stage=stage).strip(), f"{stage} sin system"
        assert prompts.load("user", stage=stage).strip(), f"{stage} sin user"


def test_prompts_are_no_longer_embedded_in_python():
    """Seccion 54: si vuelve un prompt al .py, alguien ha extracted mal."""
    from pathlib import Path

    sources = list(Path("src").rglob("*.py")) + [Path("main.py")]
    offenders = [
        p.as_posix()
        for p in sources
        if "_SYSTEM_PROMPT" in p.read_text(encoding="utf-8")
        or "_USER_TEMPLATE" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"promptsreturned a Python: {offenders}"


def test_version_is_twelve_hex_chars():
    for stage in STAGES:
        version = prompts.version(stage)
        assert len(version) == 12
        assert all(char in "0123456789abcdef" for char in version)


def test_version_changes_when_the_prompt_text_changes(tmp_path):
    """El motivo de existir del versionado por hash.

    Con un `PROMPT_VERSION = "1.0"` a mano, retocar el prompt sin subir el
    numero dejaba la cache con veredictos del prompt viejo, y la telemetria
    diciendo que eran comparables cuando no lo eran.
    """
    (tmp_path / "demo.system.txt").write_text("instruccion A", encoding="utf-8")
    (tmp_path / "demo.user.txt").write_text("datos {x}", encoding="utf-8")
    before = prompts.version("demo", base=tmp_path)

    (tmp_path / "demo.system.txt").write_text("instruccion B", encoding="utf-8")
    assert prompts.version("demo", base=tmp_path) != before


def test_version_covers_both_halves_of_the_prompt(tmp_path):
    """Cambiar el system no puede dejar el user fuera del hash."""
    (tmp_path / "demo.system.txt").write_text("igual", encoding="utf-8")
    (tmp_path / "demo.user.txt").write_text("plantilla A", encoding="utf-8")
    before = prompts.version("demo", base=tmp_path)
    (tmp_path / "demo.user.txt").write_text("plantilla B", encoding="utf-8")
    assert prompts.version("demo", base=tmp_path) != before


def test_missing_prompt_fails_loudly(tmp_path):
    with pytest.raises(prompts.PromptNotFoundError):
        prompts.load("system", stage="no_existe", base=tmp_path)


def test_empty_prompt_fails_loudly(tmp_path):
    (tmp_path / "vacio.system.txt").write_text("   \n", encoding="utf-8")
    (tmp_path / "vacio.user.txt").write_text("algo", encoding="utf-8")
    with pytest.raises(prompts.PromptNotFoundError):
        prompts.load("system", stage="vacio", base=tmp_path)


def test_render_fills_the_holes():
    rendered = prompts.render("content_analysis", "user", language="es", duration="10.5", transcript="hola")
    assert "{language}" not in rendered
    assert "IDIOMA DEL VIDEO: es" in rendered
    assert "hola" in rendered


def test_placeholders_are_reported():
    assert prompts.placeholders("content_analysis", "user") == {
        "language",
        "duration",
        "transcript",
    }
    assert prompts.placeholders("candidate_detection", "user") == {
        "language",
        "duration",
        "block_id",
        "block_start",
        "block_end",
        "transcript",
        "content_map",
    }
    assert prompts.placeholders("quality_control", "user") == {
        "clip_transcript",
        "clip_metadata",
        "edit_information",
    }
    assert prompts.placeholders("seo", "user") == {
        "clip_transcript",
        "clip_metadata",
    }


def test_templates_survive_formatting():
    """Guarda contra el error de editar un prompt y romper `.format()`.

    `candidate_detection` y `content_analysis` pasan el system por `.format()`:
    una llave sin escapar de mas en el `.txt` revienta en tiempo de ejecucion,
    no al editar.
    """
    for stage in STAGES:
        text = prompts.load("system", stage=stage)
        if stage in ("candidate_detection",):
            text.format(
                min_duration=15, max_duration=90, preferred_min=20, preferred_max=60
            )
        else:
            text.format()
        prompts.load("user", stage=stage)


@pytest.mark.parametrize(
    "stage,values",
    [
        ("content_analysis", {"language": "es", "duration": "1.0", "transcript": "t"}),
        (
            "candidate_detection",
            {
                "language": "es", "duration": "1.0", "block_id": "w1",
                "block_start": "00:00", "block_end": "00:01",
                "transcript": "t", "content_map": "m",
            },
        ),
        (
            "quality_control",
            {"clip_transcript": "t", "clip_metadata": "{}", "edit_information": "{}"},
        ),
        ("seo", {"clip_transcript": "t", "clip_metadata": "{}"}),
    ],
)
def test_user_template_renders_with_exactly_the_expected_values(stage, values):
    rendered = prompts.render(stage, "user", **values)
    assert "{" not in rendered.replace("{}", ""), f"quedan huecos sin rellenar en {stage}"
    for value in values.values():
        assert value in rendered


def test_transcript_is_labelled_as_data_not_as_instructions():
    """Seccion 61: el contenido va marcado como DATO para que no se pueda
    inyectar como instruccion."""
    for stage in STAGES:
        system = prompts.load("system", stage=stage)
        user = prompts.load("user", stage=stage)
        assert "no instrucciones" in (system + user), stage
