"""Logging estructurado (seccion 57).

Offline: no necesita LM Studio ni la base de datos.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import config as config_module  # noqa: E402
from src.core import logs  # noqa: E402


def read_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def logfile(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "pipeline.jsonl"


@pytest.fixture
def config() -> dict:
    return config_module.load()


def setup_logger(config: dict, logfile: Path) -> logging.Logger:
    return logs.setup(
        config, log_file=logfile, level="DEBUG", console_level="CRITICAL"
    )


def test_event_has_the_fields_required_by_section_57(config, logfile):
    logger = setup_logger(config, logfile)
    with logs.log_context(job_id="job_001", stage="transcribing"):
        logger.info("Generated 31 candidates")

    events = read_events(logfile)
    assert len(events) == 1
    event = events[0]
    assert event["level"] == "INFO"
    assert event["message"] == "Generated 31 candidates"
    assert event["job_id"] == "job_001"
    assert event["stage"] == "transcribing"
    assert event["timestamp"].endswith("Z")


def test_every_line_is_valid_json(config, logfile):
    logger = setup_logger(config, logfile)
    logger.warning("acentos: la motivacion y el proposito")
    logger.error("fallo")
    raw = logfile.read_text(encoding="utf-8")
    for line in raw.splitlines():
        if line.strip():
            json.loads(line)  # lanza si no es JSON

    events = read_events(logfile)
    assert events[0]["message"].startswith("acentos:")


def test_extra_fields_are_included(config, logfile):
    logger = setup_logger(config, logfile)
    logger.info("candidato generado", extra={"candidate_id": "clip_003", "score": 82})
    event = read_events(logfile)[0]
    assert event["candidate_id"] == "clip_003"
    assert event["score"] == 82


def test_context_is_restored_after_the_block(config, logfile):
    logger = setup_logger(config, logfile)

    with logs.log_context(job_id="job_001", stage="ranking"):
        logger.info("dentro")
    logger.info("fuera")

    events = read_events(logfile)
    assert events[0]["stage"] == "ranking"
    assert "stage" not in events[1], "el contexto debe restaurarse al salir del bloque"
    assert "job_id" not in events[1]


def test_context_is_restored_even_on_exception(config, logfile):
    logger = setup_logger(config, logfile)

    with pytest.raises(RuntimeError):
        with logs.log_context(job_id="job_002", stage="rendering"):
            raise RuntimeError("remoto fallo")

    assert logs.current_context() == {}, "una excepcion no debe dejar el contexto puesto"
    logger.info("despues")
    assert "job_id" not in read_events(logfile)[0]


def test_nested_contexts_are_merged(config, logfile):
    logger = setup_logger(config, logfile)
    with logs.log_context(job_id="job_003"):
        with logs.log_context(stage="qc", clip_id="clip_001"):
            logger.info("anidado")
    event = read_events(logfile)[0]
    assert event["job_id"] == "job_003"
    assert event["stage"] == "qc"
    assert event["clip_id"] == "clip_001"


def test_none_values_are_not_logged_as_nulls(config, logfile):
    logger = setup_logger(config, logfile)
    with logs.log_context(job_id="job_004", stage=None):
        logger.info("sin stage")
    event = read_events(logfile)[0]
    assert "stage" not in event


def test_exception_is_recorded_as_a_field(config, logfile):
    logger = setup_logger(config, logfile)
    try:
        raise ValueError("video corrupto")
    except ValueError:
        logger.exception("fallo recuperable")
    event = read_events(logfile)[0]
    assert "ValueError: video corrupto" in event["exception"]


def test_setup_is_idempotent_and_does_not_duplicate_lines(config, logfile):
    first = setup_logger(config, logfile)
    second = setup_logger(config, logfile)
    assert first is second
    assert len(logs.get_logger().handlers) == 2  # consola + fichero

    second.info("una vez")
    assert len(read_events(logfile)) == 1, "setup repetido duplico la linea"


def test_console_level_filters_console_but_not_file(config, logfile, capsys):
    logs.setup(config, log_file=logfile, level="DEBUG", console_level="ERROR")
    logger = logs.get_logger()
    logger.info("no debe salir en consola")
    logger.error("si debe salir en consola")

    captured = capsys.readouterr()
    assert "no debe salir" not in captured.err
    assert "si debe salir" in captured.err
    assert len(read_events(logfile)) == 2, "el fichero guarda todo el nivel"


def test_named_logger_is_child_of_lvi(config, logfile):
    setup_logger(config, logfile)
    child = logs.get_logger("media.ffmpeg")
    assert child.name == "lvi.media.ffmpeg"
    assert child.parent.name == "lvi"


def test_log_file_path_resolves_against_project_root(config):
    path = config_module.resolve_relative(config, "logging.file", "logs/pipeline.jsonl")
    assert path == config_module.project_root(config) / "logs" / "pipeline.jsonl"


def test_absolute_log_path_is_respected(config, tmp_path, logfile):
    logs.setup(config, log_file=tmp_path / "abs.jsonl", console_level="CRITICAL")
    logs.get_logger().info("absoluto")
    assert (tmp_path / "abs.jsonl").exists()
    del logfile
