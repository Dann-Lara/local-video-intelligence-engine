"""Motor de ingesta (seccion 12, pasos 1 a 7).

Comprueba los siete pasos en orden, el manifiesto que deja en el workspace y,
sobre todo, los rechazos: un video mudo, un fichero corrupto o uno que no
existe tienen que fallar con un mensaje util, no con un volcado de stderr.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media import ingest  # noqa: E402


# --- paso 1: deteccion de ficheros ---


def test_find_candidates_lists_only_videos(tmp_path: Path):
    (tmp_path / "texto.txt").write_text("no soy un video", encoding="utf-8")
    (tmp_path / "notas.md").write_text("tampoco", encoding="utf-8")
    (tmp_path / ".oculto.mp4").write_bytes(b"oculto")
    (tmp_path / "subcarpeta").mkdir()

    found = ingest.find_candidates(tmp_path)
    assert found == [], "ninguno de esos ficheros es un video valido"


def test_find_candidates_is_sorted(tmp_path: Path):
    for name in ("c.mp4", "a.mp4", "b.mp4"):
        (tmp_path / name).write_bytes(b"x")
    found = [p.name for p in ingest.find_candidates(tmp_path)]
    assert found == ["a.mp4", "b.mp4", "c.mp4"]


def test_find_candidates_is_deterministic_despite_mtime(tmp_path: Path):
    """Ordenar por mtime haria que reprocesar cambiase el orden de trabajo."""
    for index, name in enumerate(("z.mp4", "a.mp4", "m.mp4")):
        path = tmp_path / name
        path.write_bytes(b"x")
        os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
    first = [p.name for p in ingest.find_candidates(tmp_path)]
    second = [p.name for p in ingest.find_candidates(tmp_path)]
    assert first == second == ["a.mp4", "m.mp4", "z.mp4"]


def test_find_candidates_missing_directory(tmp_path: Path):
    with pytest.raises(ingest.IngestError, match="no existe el directorio"):
        ingest.find_candidates(tmp_path / "no-existe")


# --- paso 2: hash ---


def test_sha256_matches_hashlib(make_video):
    source = make_video("para_hash.mp4")
    assert ingest.sha256_of(source) == hashlib.sha256(source.read_bytes()).hexdigest()


def test_sha256_is_stable(make_video):
    source = make_video("estable.mp4")
    assert ingest.sha256_of(source) == ingest.sha256_of(source)


def test_sha256_differs_for_different_content(make_video):
    a = make_video("igual_nombre_a.mp4", seconds=1)
    b = make_video("igual_nombre_b.mp4", seconds=2)
    assert ingest.sha256_of(a) != ingest.sha256_of(b)


# --- camino feliz: los 7 pasos ---


def test_ingest_produces_workspace_audio_and_manifest(portrait_video: Path, config):
    result = ingest.ingest(portrait_video, config)

    workspace_root = Path(result.workspace_root)
    assert workspace_root.is_dir()
    for name in ("edit_plans", "frames", "renders"):
        assert (workspace_root / name).is_dir()

    assert Path(result.audio_path).is_file()
    assert Path(result.manifest_path).is_file()
    assert result.media["video"]["width"] == 1080
    assert result.media["video"]["height"] == 1920
    assert result.media["duration"] > 0
    assert result.audio["whisper_ready"] is True
    assert result.audio["sample_rate"] == 16000
    assert result.audio["channels"] == 1


def test_ingest_job_id_follows_section_12(portrait_video: Path, config):
    from src.media import workspace as ws

    result = ingest.ingest(portrait_video, config)
    expected = ws.build_job_id(portrait_video, result.sha256)
    assert result.job_id == expected
    assert result.job_id.endswith(result.sha256[:8])


def test_ingest_job_id_is_deterministic(portrait_video: Path, config):
    first = ingest.ingest(portrait_video, config)
    second = ingest.ingest(portrait_video, config)
    assert first.job_id == second.job_id
    assert first.sha256 == second.sha256


def test_ingest_respects_explicit_job_id(portrait_video: Path, config):
    result = ingest.ingest(portrait_video, config, job_id="mi-job-de-prueba")
    assert result.job_id == "mi-job-de-prueba"


def test_manifest_is_valid_json(portrait_video: Path, config):
    result = ingest.ingest(portrait_video, config)
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["job_id"] == result.job_id
    assert manifest["sha256"] == result.sha256
    assert manifest["source_name"] == portrait_video.name
    assert manifest["created_at"].endswith("Z")


def test_manifest_is_utf8(portrait_video: Path, config, tmp_path: Path):
    """La ruta del proyecto lleva acento: el manifiesto no puede corrupto."""
    source = tmp_path / "café de la mañana.mp4"
    source.write_bytes(portrait_video.read_bytes())
    result = ingest.ingest(source, config)
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert "caf" in manifest["source_name"] or "café" in manifest["source_name"]


def test_result_is_json_serialisable(portrait_video: Path, config):
    result = ingest.ingest(portrait_video, config)
    json.dumps(result.to_dict(), ensure_ascii=False, default=str)


def test_summary_mentions_the_essentials(portrait_video: Path, config):
    summary = ingest.ingest(portrait_video, config).summary()
    assert "job_id" in summary
    assert "audio" in summary
    assert "1080" in summary


def test_ingest_keeps_raw_ffprobe_output(portrait_video: Path, config):
    """`raw` se guarda en memoria para depurar, aunque no vaya al manifiesto."""
    from src.media import ffprobe

    result = ingest.ingest(portrait_video, config)
    expected = ffprobe.probe(portrait_video)
    assert result.media["format_name"] == expected.format_name


# --- paso 6: validacion de formato ---


def test_silent_video_is_rejected(silent_video: Path, config):
    with pytest.raises(ingest.IngestError) as exc:
        ingest.ingest(silent_video, config)
    assert exc.value.code == "no_audio"
    assert "no tiene audio" in str(exc.value)


def test_audio_only_is_rejected(audio_only_file: Path, config):
    with pytest.raises(ingest.IngestError) as exc:
        ingest.ingest(audio_only_file, config)
    assert exc.value.code == "no_video"


def test_missing_file_is_rejected(tmp_path: Path, config):
    with pytest.raises(ingest.IngestError) as exc:
        ingest.ingest(tmp_path / "no-existe.mp4", config)
    assert exc.value.code == "missing_file"


def test_corrupt_file_is_rejected(not_a_video: Path, config):
    with pytest.raises(ingest.IngestError) as exc:
        ingest.ingest(not_a_video, config)
    assert exc.value.code == "unreadable"
    # El mensaje tiene que ser legible, no un volcado de stderr de ffprobe.
    assert "ffprobe" in str(exc.value)
    assert len(str(exc.value)) < 400


def test_rejection_leaves_no_empty_audio(make_video, silent_video: Path, config):
    with pytest.raises(ingest.IngestError):
        ingest.ingest(silent_video, config)
    from src.media import workspace as ws

    from src.core import config as config_module

    work_root = config_module.resolve_path(config, "work_root")
    audio = work_root / ws.safe_stem(silent_video.stem) / "audio.wav"
    assert not audio.exists(), "un rechazo no debe dejar audio a medias"


def test_workspace_is_created_even_when_validation_fails(silent_video: Path, config):
    """Paso 5 antes del 6, a proposito: el fallo queda localizable en disco."""
    from src.core import config as config_module
    from src.media import workspace as ws

    with pytest.raises(ingest.IngestError):
        ingest.ingest(silent_video, config)
    work_root = config_module.resolve_path(config, "work_root")
    assert (work_root / ws.safe_stem(silent_video.stem)).is_dir()


# --- advertencias (no bloquean) ---


def test_short_video_warns_without_failing(make_video, config):
    source = make_video("corto.mp4", seconds=1)
    result = ingest.ingest(source, config)
    assert any("muy corto" in w for w in result.warnings)


def test_rotation_produces_a_warning(rotated_video: Path, config):
    result = ingest.ingest(rotated_video, config)
    if result.media["video"]["rotation"] == 0:
        pytest.skip("esta build de FFmpeg no escribio la matriz de rotacion")
    assert any("rotacion" in w for w in result.warnings)


def test_landscape_video_does_not_warn(landscape_video: Path, config):
    """1920x1080 de 3 s es raro en produccion, pero no es un error de ingesta."""
    result = ingest.ingest(landscape_video, config)
    assert isinstance(result.warnings, list)


def test_warnings_do_not_stop_ingestion(portrait_video: Path, config):
    result = ingest.ingest(portrait_video, config)
    assert result.audio_path is not None
    assert Path(result.manifest_path).is_file()


# --- rutas relativas y acentuadas ---


def test_relative_path_is_resolved_against_project_root(portrait_video: Path, config):
    import os

    relative = os.path.relpath(portrait_video, config["_project_root"])
    result = ingest.ingest(relative, config)
    assert Path(result.source_path).is_absolute()
    assert result.sha256 == ingest.sha256_of(portrait_video)
