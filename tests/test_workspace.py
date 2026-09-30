"""Workspace y Job ID (secciones 11 y 12).

El workspace se nombra por el fichero, no por el job. Se prueba aqui porque es
una decision con consecuencias: si el nombre llevase el Job ID, reprocesar el
mismo video crearia un directorio distinto y los checkpoints de la seccion 50
dejarian de encontrar los artefactos anteriores.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media import workspace as ws  # noqa: E402


# --- safe_stem ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("video_001", "video_001"),
        ("Mi Video Final", "Mi_Video_Final"),
        ("clip.2026.09.28", "clip.2026.09.28"),
        ("con-acentos_y mas", "con-acentos_y_mas"),
    ],
)
def test_safe_stem_keeps_readable_names(raw, expected):
    assert ws.safe_stem(raw) == expected


def test_safe_stem_removes_accents():
    """Un workspace con acento dentro de una ruta que ya tiene acento es ruido."""
    assert ws.safe_stem("Introducción año 1") == "Introduccion_ano_1"
    assert ws.safe_stem("Añadir más valor") == "Anadir_mas_valor"


def test_safe_stem_strips_path_separators():
    assert "/" not in ws.safe_stem("a/b/c")
    assert "\\" not in ws.safe_stem("a\\b\\c")
    assert ws.safe_stem("../../etc/passwd") == "etc_passwd"


def test_safe_stem_handles_empty_and_symbol_only():
    assert ws.safe_stem("") == "video"
    assert ws.safe_stem("...") == "video"
    assert ws.safe_stem("///") == "video"
    assert ws.safe_stem("日本語") == "video"


def test_safe_stem_is_windows_safe():
    """Caracteres prohibidos en Windows: < > : " | ? *"""
    result = ws.safe_stem('a<b>c:d"e|f?g*h')
    for char in '<>:"|?*':
        assert char not in result


# --- build_job_id ---


def test_job_id_matches_section_12_format():
    job_id = ws.build_job_id(Path("input/video_001.mp4"), "a83f91bc2d4e5f6a", date="2026-09-28")
    assert job_id == "2026-09-28_video_001_a83f91bc"


def test_job_id_is_deterministic():
    """Mismo contenido, mismo Job ID: es lo que hace posible la seccion 51."""
    a = ws.build_job_id(Path("input/v.mp4"), "a" * 64)
    b = ws.build_job_id(Path("input/v.mp4"), "a" * 64)
    assert a == b


def test_job_id_changes_with_content():
    a = ws.build_job_id(Path("input/v.mp4"), "a" * 64)
    b = ws.build_job_id(Path("input/v.mp4"), "b" * 64)
    assert a != b


def test_job_id_changes_with_date():
    digest = "a" * 64
    assert ws.build_job_id(Path("v.mp4"), digest, date="2026-09-28") != ws.build_job_id(
        Path("v.mp4"), digest, date="2026-09-29"
    )


def test_job_id_sanitises_stem():
    job_id = ws.build_job_id(Path("input/mi video ñandú.mp4"), "b" * 64, date="2026-01-01")
    assert " " not in job_id
    assert "ñ" not in job_id
    assert job_id.startswith("2026-01-01_")


def test_job_id_defaults_to_today():
    from datetime import date as date_type

    job_id = ws.build_job_id(Path("v.mp4"), "c" * 64)
    assert job_id.startswith(date_type.today().isoformat())


# --- estructura ---


def test_create_makes_all_directories(portrait_video: Path, config):
    work = ws.create(portrait_video, "a" * 64, config)
    assert work.exists()
    for name in ("edit_plans", "frames", "renders"):
        assert (work.root / name).is_dir()


def test_artifact_paths_live_in_the_workspace(portrait_video: Path, config):
    work = ws.create(portrait_video, "a" * 64, config)
    for path in (work.audio, work.transcript, work.scenes, work.content_map,
                 work.clip_candidates, work.ingest_manifest):
        assert path.parent == work.root
        assert path.suffix == (".wav" if path == work.audio else ".json")


def test_edit_plan_lives_inside_edit_plans_directory(portrait_video: Path, config):
    """La seccion 11 pone `edit_plans/` como directorio; el plan va dentro."""
    work = ws.create(portrait_video, "a" * 64, config)
    assert work.edit_plan.parent == work.edit_plans
    assert work.edit_plan.name == "edit_plan.json"


def test_create_is_idempotent(portrait_video: Path, config):
    first = ws.create(portrait_video, "a" * 64, config)
    marker = first.root / "frames" / "marcador.txt"
    marker.write_text("x", encoding="utf-8")
    second = ws.create(portrait_video, "a" * 64, config)
    assert first.root == second.root
    assert marker.exists(), "no se debe borrar lo que ya habia"


def test_workspace_name_follows_the_file_not_the_job(config, tmp_path):
    """Dos contenidos distintos, mismo nombre de fichero -> mismo workspace.

    Es el comportamiento documentado: la identidad la lleva el Job ID, no el
    directorio. Se deja fijado por test para que nadie lo cambie sin darse cuenta.
    """
    uno = tmp_path / "a.mp4"
    uno.write_bytes(b"contenido A")
    dos = tmp_path / "b.mp4"
    dos.write_bytes(b"contenido B")

    w1 = ws.create(uno, "a" * 64, config)
    w2 = ws.create(dos, "a" * 64, config)
    assert w1.root.name == "a"
    assert w2.root.name == "b"

    j1 = ws.create(uno, "a" * 64, config)
    j2 = ws.create(uno, "b" * 64, config)
    assert j1.root == j2.root


def test_workspace_respects_configured_work_root(portrait_video: Path, config, tmp_path):
    work = ws.create(portrait_video, "a" * 64, config)
    assert work.root.parent == tmp_path / "work"


def test_accented_source_name_gives_ascii_workspace(tmp_path: Path, config):
    """La ruta del proyecto ya lleva acento; el workspace no lo repite."""
    source = tmp_path / "Introducción año 1.mp4"
    source.write_bytes(b"x")
    work = ws.create(source, "a" * 64, config)
    assert work.root.name == "Introduccion_ano_1"
    assert work.root.name.isascii()


def test_as_dict_exposes_every_artifact(portrait_video: Path, config):
    work = ws.create(portrait_video, "a" * 64, config)
    payload = work.as_dict()
    assert set(payload) == set(ws.ARTIFACT_FILES)
    for value in payload.values():
        assert Path(value).is_absolute()
