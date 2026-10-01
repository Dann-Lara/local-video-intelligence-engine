"""CLI (blueprint secciones 65 y 66).

La base de datos se aísla con el override de entorno `LVI_ORCHESTRATION__...`
(seccion 52), así que estos tests no tocan la base real del proyecto.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as cli  # noqa: E402
from src.core import artifacts  # noqa: E402
from src.core import db as db_module  # noqa: E402
from src.core.db import JobState  # noqa: E402

runner = CliRunner()

SHA = "c" * 64


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Cada test usa su propia base de datos y su propio log."""
    database = tmp_path / "cli_test.db"
    log = tmp_path / "logs" / "pipeline.jsonl"
    monkeypatch.setenv("LVI_ORCHESTRATION__DATABASE_URL", f"sqlite:///{database.as_posix()}")
    monkeypatch.setenv("LVI_LOGGING__FILE", log.as_posix())
    monkeypatch.setenv("LVI_LOGGING__CONSOLE_LEVEL", "CRITICAL")
    return database


def seed_job(stage_through: JobState | None = None) -> str:
    """Crea un job con etapas completadas HASTA `stage_through` (sin incluirla).

    Es decir, `stage_through` queda como la siguiente etapa a ejecutar, que es
    justo lo que devuelve `resume_stage`.
    """
    from src.core import config as config_module

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    video_id = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(video_id)
    if stage_through is not None:
        for stage in db_module.PIPELINE_ORDER[1:]:
            if stage == stage_through:
                break
            run_id = repo.start_stage(job["id"], stage)
            repo.finish_stage(run_id)
            repo.transition(job["id"], stage)
    return job["id"]


# --- superficie de comandos (seccion 65) ---


def test_help_lists_every_command_of_section_65():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("doctor", "process", "analyze", "candidates", "render", "report", "performance"):
        assert command in result.stdout, f"falta el comando {command} (seccion 65)"


def test_no_command_is_left_waiting_for_a_sprint():
    """Anadir un comando al registro sin implementarlo tiene que ser visible.

    El registro `STAGE_ROADMAP` guardaba los comandos cuya etapa venia en un
    sprint posterior. Con el Sprint 8 quedo vacio, y con el, el mecanismo que
    lo consumia. El dict se mantiene vacio a proposito, con este test, para que
    reutilizarlo sin terminar la etapa se note en la suite.
    """
    assert cli.STAGE_ROADMAP == {}, (
        f"quedan comandos sin implementar: {sorted(cli.STAGE_ROADMAP)}"
    )


def test_every_command_returns_zero_or_one(isolated_env, tmp_path):
    """Ningun comando devuelve otro codigo. Existia un 2 para "el comando esta
    aqui pero su etapa viene en un sprint posterior"; con el Sprint 8 no queda
    ninguno, asi que un 2 hoy solo puede significar un comando roto."""
    results = []
    for argv in (
        ["doctor"],
        ["jobs", "list"],
        ["db", "init"],
        ["performance", "--help"],
        ["performance", "sync", "--platform", "tiktok"],
    ):
        result = runner.invoke(cli.app, argv)
        results.append((argv, result.exit_code))
    offenders = [argv for argv, code in results if code not in (cli.EXIT_OK, cli.EXIT_ERROR)]
    assert not offenders, f"codigos de salida inesperados: {offenders}"


def test_process_is_no_longer_a_stub(isolated_env, tmp_path):
    """D5: `process` paso de stub a implementado.

    Un fichero inexistente da 1 (error de ejecucion), no 2 (sprint pendiente): si
    volviera a dar 2, el atajo de pipeline completo se habria perdido.
    """
    result = runner.invoke(cli.app, ["process", str(tmp_path / "no-existe.mp4")])
    assert result.exit_code == cli.EXIT_ERROR
    assert "no existe el fichero" in result.output
    assert "todavia no esta implementado" not in result.output


def test_process_help_lists_the_full_chain():
    result = runner.invoke(cli.app, ["process", "--help"])
    assert result.exit_code == 0
    assert "WHISPER" in result.output
    assert "RENDER" in result.output
    assert "SEO" in result.output
    for flag in ("--skip-llm", "--overwrite", "--json"):
        assert flag in result.output


def test_process_defaults_are_shared_with_the_stage_commands():
    """`process` no puede tener un criterio distinto al de `report`.

    Si `report` reintenta el QC semantico con `--overwrite` y `process` no, el
    atajo "rehacerlo todo" no rehace el QC y el usuario no tiene forma de
    descubrir por que.
    """
    def flag_of(command: str) -> set[str]:
        out = runner.invoke(cli.app, [command, "--help"]).output
        return {line.strip() for line in out.splitlines() if line.strip().startswith("--")}

    process_flags = flag_of("process")
    assert flag_of("report") <= process_flags


def test_the_stage_roadmap_got_emptied_on_sprint_8():
    """Guarda contra un comando nuevo que se quede en stub sin que se note."""
    assert cli.STAGE_ROADMAP == {}


def test_render_is_no_longer_a_stub(isolated_env):
    """Sprint 6: `render` paso de stub a implementado.

    Un job inexistente da 1 (error de ejecucion), no 2 (sprint pendiente).
    """
    result = runner.invoke(cli.app, ["render", "job_inexistente"])
    assert result.exit_code == cli.EXIT_ERROR
    assert "Job desconocido" in result.output
    assert "todavia no esta implementado" not in result.output


def test_candidates_is_no_longer_a_stub(isolated_env, tmp_path):
    """Sprint 5: `candidates` paso de stub a implementado.

    Un fichero inexistente da 1 (error de ejecucion), no 2 (sprint pendiente).
    """
    result = runner.invoke(cli.app, ["candidates", str(tmp_path / "no-existe.mp4")])
    assert result.exit_code == cli.EXIT_ERROR
    assert "no existe el fichero" in result.output
    assert "todavia no esta implementado" not in result.output


def test_analyze_is_no_longer_a_stub(isolated_env, tmp_path):
    """Prueba de que el comando paso deImplemented a implementado.

    Un fichero inexistente da 1 (error de ejecucion), no 2 (sprint pendiente):
    si volviera a dar 2, la ingesta se habria perdido.
    """
    result = runner.invoke(cli.app, ["analyze", str(tmp_path / "no-existe.mp4")])
    assert result.exit_code == cli.EXIT_ERROR
    assert "no existe el fichero" in result.output
    assert "todavia no esta implementado" not in result.output


def test_ranking_keeps_the_warnings_of_the_generation_stage(
    isolated_env, portrait_video, tmp_path, monkeypatch
):
    """El resumen de descartes de GENERATING_CANDIDATES no puede evaporarse.

    `_run_ranking` relee el `clip_candidates.json` y lo reescribe con la
    seleccion. Si asigna `warnings = warnings` en vez de a?adirlos, el aviso "N
    candidatos descartados en generacion" desaparece y el artefacto se queda con
    el detalle en `drops` pero sin la cifra, que es lo que se lee primero.
    """
    import json as _json

    from src.core import artifacts as artifacts_module
    from src.core import config as config_module

    work = tmp_path / "work_warn"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())
    config = config_module.load()

    repo = db_module.JobRepository(db_module.open_database(config))
    video_id = repo.register_video(path=str(portrait_video), sha256="a" * 64)
    job_id = repo.create_job(video_id)["id"]
    # Se avanza hasta GENERATING_CANDIDATES: `_run_ranking` hace `enter_stage(RANKING)`.
    for stage in db_module.PIPELINE_ORDER[1:]:
        repo.transition(job_id, stage)
        if stage == JobState.GENERATING_CANDIDATES:
            break

    # Se escribe a mano el set tal como lo deja GENERATING_CANDIDATES.
    target = work / "portrait" / "clip_candidates.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    generation_warning = (
        "1 candidatos descartados en generacion (fuera de rangos, cortes a media frase, etc.)"
    )
    artifacts_module.write_json(
        target,
        {
            "schema_version": "1.0",
            "job_id": job_id,
            "source": {"file": str(portrait_video), "sha256": "a" * 64, "duration": 3.0},
            "block_count": 1,
            "content_map_sha256": "b" * 64,
            "normalized_transcript_sha256": "c" * 64,
            "candidates": [],
            "warnings": [generation_warning],
            "drops": ["candidate[0]: duracion 1.0s fuera de [15, 90] (seccion 23)"],
        },
        schema_name="candidate_set",
        overwrite=True,
    )

    cli._run_ranking(portrait_video, config, repo, job_id=job_id, as_json=True)

    after = _json.loads(target.read_text(encoding="utf-8"))
    assert generation_warning in after["warnings"]
    assert after["drops"], "el detalle de descartes tambien debe sobrevivir"
    assert after["selection"]["selected"] == []
    assert after["selection"]["dropped"] == []


# --- db ---


def test_db_init_is_idempotent(isolated_env):
    # La version no se escribe a mano: si sube, este test sigue valiendo.
    expected = f"schema v{db_module.SCHEMA_VERSION}"
    first = runner.invoke(cli.app, ["db", "init"])
    assert first.exit_code == 0
    assert expected in first.output
    second = runner.invoke(cli.app, ["db", "init"])
    assert second.exit_code == 0
    assert expected in second.output


def test_db_path_points_at_the_isolated_database(isolated_env):
    result = runner.invoke(cli.app, ["db", "path"])
    assert result.exit_code == 0
    assert result.output.strip() == str(isolated_env)


# --- jobs ---


def test_jobs_list_when_empty():
    result = runner.invoke(cli.app, ["jobs", "list"])
    assert result.exit_code == 0
    assert "No hay jobs" in result.output


def test_jobs_list_shows_registered_jobs():
    seed_job()
    result = runner.invoke(cli.app, ["jobs", "list"])
    assert result.exit_code == 0
    assert "QUEUED" in result.output


def test_jobs_list_json_is_parseable():
    job_id = seed_job()
    result = runner.invoke(cli.app, ["jobs", "list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows[0]["id"] == job_id


def test_jobs_show_includes_resume_stage():
    job_id = seed_job(JobState.TRANSCRIBING)
    result = runner.invoke(cli.app, ["jobs", "show", job_id])
    assert result.exit_code == 0
    assert "reanudar en TRANSCRIBING" in result.output
    assert "INGESTING, ANALYZING_MEDIA" in result.output


def test_jobs_show_json_exposes_checkpoints():
    job_id = seed_job(JobState.ANALYZING_MEDIA)
    result = runner.invoke(cli.app, ["jobs", "show", job_id, "--json"])
    assert result.exit_code == 0
    detail = json.loads(result.stdout)
    assert detail["resume_stage"] == "ANALYZING_MEDIA"
    assert detail["completed_stages"] == ["INGESTING"]


def test_jobs_resume_of_failed_render_stays_at_rendering():
    """El escenario de la seccion 50 desde la CLI."""
    job_id = seed_job(JobState.RENDERING)
    from src.core import config as config_module

    repo = db_module.JobRepository(db_module.open_database(config_module.load()))
    repo.transition(job_id, JobState.FAILED, error_kind="RecoverableError", error_message="remotion")

    result = runner.invoke(cli.app, ["jobs", "resume", job_id])
    assert result.exit_code == 0
    assert "Reanudar desde: RENDERING" in result.output


def test_jobs_resume_of_completed_job():
    job_id = seed_job(JobState.COMPLETED)
    result = runner.invoke(cli.app, ["jobs", "resume", job_id])
    assert result.exit_code == 0
    assert "ya esta COMPLETED" in result.output


def test_jobs_list_marks_failed_jobs_with_resume_stage():
    job_id = seed_job(JobState.RENDERING)
    from src.core import config as config_module

    repo = db_module.JobRepository(db_module.open_database(config_module.load()))
    repo.transition(job_id, JobState.FAILED, error_message="boom")

    result = runner.invoke(cli.app, ["jobs", "list"])
    assert "reanudar: RENDERING" in result.output


def test_unknown_job_exits_with_error():
    result = runner.invoke(cli.app, ["jobs", "show", "job_fantasma"])
    assert result.exit_code == cli.EXIT_ERROR
    assert "Job desconocido" in result.output


# --- doctor (seccion 73) ---


def test_doctor_runs_without_contacting_llm():
    result = runner.invoke(cli.app, ["doctor", "--skip-llm"])
    # El doctor devuelve 1 si hay un bloqueador. En este equipo no lo hay, pero
    # lo que importa aqui es que se ejecuta y produce su informe.
    assert result.exit_code in (0, 1)
    assert "RESULTADO" in result.output
    assert "FFmpeg" in result.output


def test_doctor_json_output():
    result = runner.invoke(cli.app, ["doctor", "--json", "--skip-llm"])
    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    names = {check["name"] for check in payload["checks"]}
    assert {"Configuracion", "FFmpeg", "Remotion", "Schemas"} <= names


# --- analyze: ingesta de la seccion 12 ---


@pytest.fixture
def whisper_en_cpu(monkeypatch):
    """Fuerza Whisper a CPU con el modelo mas pequeno.

    Sin esto, estos tests dependerian de quanta VRAM hay libre en el momento de
    ejecutarlos. `analyze` exige 1.6 GB de VRAM mas 1.5 GB de margen (seccion
    49), y en una RTX 3070 de 8 GB con el navegador y LM Studio abiertos solo
    quedan ~1.3 GB: el test fallaria por el estado del escritorio, no por el
    codigo. Un test que depende de lo que tenga abierto el usuario no es un
    test. La ruta CUDA real se comprueba aparte, marcada como `cuda`.
    """
    monkeypatch.setenv("LVI_WHISPER__DEVICE", "cpu")
    monkeypatch.setenv("LVI_WHISPER__MODEL", "tiny")


CONTENT_MAP_BODY = {
    "global_summary": "Video de prueba generado con FFmpeg, sin dialogo.",
    "topics": [
        {
            "label": "prueba",
            "time": {"start": 0.0, "end": 2.0},
            "signature": "topic:prueba",
            "keywords": ["prueba"],
        }
    ],
    "entities": [],
    "claims": [],
    "stories": [],
    "questions": [],
    "answers": [],
    "conflicts": [],
    "revelations": [],
    "surprising_points": [],
    "emotional_moments": [],
    "humorous_moments": [],
    "actionable_points": [],
    "strong_statements": [
        {
            "summary": "Cierre de prueba.",
            "time": {"start": 0.0, "end": 2.0},
            "reason": "conclusion del video de prueba",
        }
    ],
    "memorable_phrases": [],
    "conclusions": [],
    "calls_to_action": [],
    "topic_changes": [],
    "potential_hooks": [
        {
            "type": "question",
            "text": "Que sigue?",
            "origin": "generated",
            "time": {"start": 0.0, "end": 1.0},
            "requires_generated_hook": True,
            "reason": "no hay frase literal en el audio",
        }
    ],
}


@pytest.fixture
def content_map_llm(monkeypatch):
    """Fake de Gemma para la etapa BUILDING_CONTENT_MAP.

    `analyze` ahora continua hasta GENERATING_CANDIDATES. Sin un cliente falso,
    los tests dependen de que LM Studio este levantado, lo cual es una
    propiedad del escritorio del usuario, no del codigo. Este fixture a la vez:
      - fija llm.model a un id que no requiere consultar /models (no hay red),
      - apaga el structured output para no depender de la gramatica,
      - sustituye `chat` por una respuesta determinista y valida.
    """
    from src.llm import lmstudio as lmstudio_module

    monkeypatch.setenv("LVI_LLM__MODEL", "gemma-fake")
    monkeypatch.setenv("LVI_LLM__STRUCTURED_OUTPUT", "off")

    def fake_chat(self, messages, *, schema_name=None, temperature=None, max_tokens=None):
        return json.dumps(CONTENT_MAP_BODY, ensure_ascii=False)

    monkeypatch.setattr(lmstudio_module.LMStudioClient, "chat", fake_chat)
    return CONTENT_MAP_BODY


def test_analyze_ingests_a_valid_video(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu, content_map_llm
):
    """El comando crea el job, extrae el audio, transcribe y genera el content map."""
    from src.core import config as config_module

    work = tmp_path / "work"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())

    result = runner.invoke(cli.app, ["analyze", str(portrait_video)])
    assert result.exit_code == 0, result.output
    assert "job_id" in result.output
    assert "audio" in result.output

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    jobs = repo.list_jobs()
    assert len(jobs) == 1
    # `analyze` termina en BUILDING_CONTENT_MAP (el estado) y dice que su
    # siguiente etapa es GENERATING_CANDIDATES, que es un sprint posterior.
    assert jobs[0]["state"] == JobState.BUILDING_CONTENT_MAP.value
    assert repo.resume_stage(jobs[0]["id"]) == JobState.GENERATING_CANDIDATES
    assert repo.completed_stages(jobs[0]["id"]) == [
        JobState.INGESTING,
        JobState.ANALYZING_MEDIA,
        JobState.TRANSCRIBING,
        JobState.BUILDING_CONTENT_MAP,
    ]

    assert (work / "portrait" / "audio.wav").is_file()
    assert (work / "portrait" / "ingest.json").is_file()
    assert (work / "portrait" / "transcript.json").is_file()
    assert (work / "portrait" / "normalized_transcript.json").is_file()
    assert (work / "portrait" / "content_map.json").is_file()

    artifact = repo.get_artifact(jobs[0]["id"], JobState.BUILDING_CONTENT_MAP, "content_map")
    assert artifact is not None
    assert len(artifact["sha256"]) == 64
    assert artifact["path"].endswith("content_map.json")


def test_analyze_can_stop_after_ingest(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu
):
    """`--skip-transcription` deja el job en ANALYZING_MEDIA sin cargar Whisper."""
    from src.core import config as config_module

    work = tmp_path / "work_solo_ingesta"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())

    result = runner.invoke(cli.app, ["analyze", str(portrait_video), "--skip-transcription"])
    assert result.exit_code == 0, result.output
    assert "--skip-transcription" in result.output

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    job = repo.list_jobs()[0]
    assert job["state"] == JobState.ANALYZING_MEDIA.value
    assert (work / "portrait" / "audio.wav").is_file()
    assert not (work / "portrait" / "transcript.json").exists(), "no deberia transcribir"


def test_analyze_can_stop_after_transcription(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu
):
    """`--skip-content-map` transcribe pero no llama a Gemma."""
    from src.core import config as config_module

    work = tmp_path / "work_sin_mapa"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())

    result = runner.invoke(cli.app, ["analyze", str(portrait_video), "--skip-content-map"])
    assert result.exit_code == 0, result.output
    assert "--skip-content-map" in result.output

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    job = repo.list_jobs()[0]
    assert job["state"] == JobState.TRANSCRIBING.value
    assert (work / "portrait" / "normalized_transcript.json").is_file()
    assert not (work / "portrait" / "content_map.json").exists(), "no deberia llamar a Gemma"
    assert repo.resume_stage(job["id"]) == JobState.BUILDING_CONTENT_MAP


def test_analyze_json_output(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu, content_map_llm
):
    work = tmp_path / "work_json"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())
    result = runner.invoke(cli.app, ["analyze", str(portrait_video), "--json"])
    assert result.exit_code == 0, result.output
    # `analyze` cubre tres etapas. Si cada una imprimiese su propio JSON, esto
    # serian varios objetos pegados y `json.loads` reventaria. Tiene que haber
    # UN documento.
    payload = json.loads(result.output)
    assert payload["source_name"] == "portrait.mp4"
    assert payload["audio"]["whisper_ready"] is True
    assert len(payload["sha256"]) == 64
    assert payload["transcription"]["transcript"]["segment_count"] >= 0
    assert payload["content_map"]["content_map"]["reused"] is False
    assert payload["content_map"]["content_map"]["sha256"]
    assert payload["content_map"]["content_map_path"].endswith("content_map.json")
    assert payload["content_map"]["next_state"] == JobState.GENERATING_CANDIDATES.value


def test_analyze_is_idempotent(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu, content_map_llm
):
    """Reejecutar el mismo fichero no duplica el job ni vuelve a extraer (seccion 51)."""
    from src.core import config as config_module

    work = tmp_path / "work_idem"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())

    first = runner.invoke(cli.app, ["analyze", str(portrait_video)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(cli.app, ["analyze", str(portrait_video)])
    assert second.exit_code == 0, second.output
    assert "Ya ingerido" in second.output

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    assert len(repo.list_jobs()) == 1
    assert len(repo.db.query("SELECT * FROM videos")) == 1


def test_analyze_reuses_the_transcript_on_a_second_run(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu, content_map_llm
):
    """La segunda pasada no vuelve a transcribir, y no pierde el SHA.

    Reejecutar `analyze` sobre el mismo video es la operacion mas frecuente del
    uso diario. Si la idempotencia se rompe aqui, cada reejecucion cuesta otro
    `large-v3-turbo` entero.
    """
    work = tmp_path / "work_reuso"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())
    assert runner.invoke(cli.app, ["analyze", str(portrait_video)]).exit_code == 0
    transcript = work / "portrait" / "transcript.json"
    stamp = transcript.stat().st_mtime_ns
    sha = artifacts.sha256_of_file(transcript)

    assert runner.invoke(cli.app, ["analyze", str(portrait_video)]).exit_code == 0

    assert transcript.stat().st_mtime_ns == stamp, "el transcript no se debe reescribir"
    assert artifacts.sha256_of_file(transcript) == sha

    from src.core import config as config_module

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    fila = repo.get_artifact(repo.list_jobs()[0]["id"], JobState.TRANSCRIBING, "transcript")
    assert fila["sha256"] == sha, "el hash del artefacto no puede degradarse a NULL"


def test_analyze_rejects_missing_file(isolated_env, tmp_path):
    result = runner.invoke(cli.app, ["analyze", str(tmp_path / "no-existe.mp4")])
    assert result.exit_code == 1
    assert "no existe el fichero" in result.output


def test_analyze_rejects_silent_video(isolated_env, silent_video, tmp_path, monkeypatch):
    """Un video mudo deja el job en FAILED, listo para `jobs resume`."""
    from src.core import config as config_module

    work = tmp_path / "work_mudo"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())

    result = runner.invoke(cli.app, ["analyze", str(silent_video)])
    assert result.exit_code == 1
    assert "no tiene audio" in result.output

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    jobs = repo.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["state"] == JobState.FAILED.value
    assert jobs[0]["error_kind"] == "no_audio"
    assert str(repo.resume_stage(jobs[0]["id"])) == JobState.INGESTING


def test_a_failed_job_can_be_retried_through_the_cli(isolated_env, silent_video, tmp_path, monkeypatch):
    """El reintento pasa por RETRYING; FAILED no es un callejon sin salida."""
    from src.core import config as config_module

    work = tmp_path / "work_retry"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())

    assert runner.invoke(cli.app, ["analyze", str(silent_video)]).exit_code == 1
    assert runner.invoke(cli.app, ["analyze", str(silent_video)]).exit_code == 1

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    jobs = repo.list_jobs()
    assert len(jobs) == 1, "el reintento no debe crear un job nuevo"
    assert jobs[0]["attempts"] == 1
    assert jobs[0]["state"] == JobState.FAILED.value


def test_analyze_logs_a_structured_event(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu, content_map_llm
):
    """Cada evento es un objeto JSON con job_id y stage (seccion 57)."""
    log = tmp_path / "logs" / "pipeline.jsonl"
    monkeypatch.setenv("LVI_LOGGING__FILE", log.as_posix())
    runner.invoke(cli.app, ["analyze", str(portrait_video)])

    events = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events, "no se escribio ningun evento de ingesta"
    completed = [e for e in events if e["message"] == "ingest_completed"]
    assert len(completed) == 1
    event = completed[0]
    assert event["stage"] == JobState.INGESTING.value
    assert event["job_id"].endswith(completed[0]["sha256"][:8])
    assert event["warnings"], "deberia registrar la advertencia del video corto"


def test_analyze_stores_the_media_metadata(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu, content_map_llm
):
    from src.core import config as config_module

    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", (tmp_path / "work_meta").as_posix())
    runner.invoke(cli.app, ["analyze", str(portrait_video)])

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    job = repo.list_jobs()[0]
    video = repo.get_video(job["video_id"])
    assert video["width"] == 1080
    assert video["height"] == 1920
    assert video["duration"] > 0
    assert video["audio_codec"] == "aac"

    artifacts = {a["kind"] for a in repo.list_artifacts(job["id"])}
    assert {"media_metadata", "audio", "ingest_manifest"} <= artifacts


# --- candidates: Candidate Engine del Sprint 5 (secciones 18-27) ---


CANDIDATES_BODY = {
    "candidates": [
        {
            "source": {"start": 0.5, "end": 2.5, "boundary_reason": "frase completa"},
            "content": {"topic": "prueba", "reason": "setup + payoff", "content_type": "explanation"},
            "hook": {
                "type": "question",
                "text": "Que sigue?",
                "origin": "generated",
                "time": {"start": 0.5, "end": 1.0},
            },
            "progression": {"pattern": "setup_payoff", "ends_complete": True},
            "value_provided": ["information"],
            "signatures": {"topic_signature": "topic:prueba", "semantic_signature": "sem:prueba"},
            "score": {
                "hook": 16, "curiosity": 12, "value": 12, "emotion": 8,
                "narrative": 8, "payoff": 8, "shareability": 7, "standalone": 4, "visual": 3,
            },
        },
        {
            "source": {"start": 2.2, "end": 3.0, "boundary_reason": "frase completa"},
            "content": {"topic": "cierre", "reason": "conclusion", "content_type": "opinion"},
            "hook": {
                "type": "emotional_statement",
                "text": "Y asi termina.",
                "origin": "generated",
                "time": {"start": 2.2, "end": 2.5},
            },
            "progression": {"pattern": "claim_conclusion", "ends_complete": True},
            "value_provided": ["entertainment"],
            "signatures": {"topic_signature": "topic:cierre", "semantic_signature": "sem:cierre"},
            "score": {
                "hook": 14, "curiosity": 10, "value": 10, "emotion": 10,
                "narrative": 10, "payoff": 10, "shareability": 6, "standalone": 3, "visual": 3,
            },
        },
    ]
}


@pytest.fixture
def candidates_llm(monkeypatch):
    """Fake de Gemma que responde al content map Y a los bloques de candidatos.

    La etapa BUILDING_CONTENT_MAP y la GENERATING_CANDIDATES comparten cliente
    (`chat`). Se distingue por el mensaje: el prompt de candidatos incluye
    "BLOQUE <id>" (seccion 18); el del content map, no.
    """
    from src.llm import lmstudio as lmstudio_module

    monkeypatch.setenv("LVI_LLM__MODEL", "gemma-fake")
    monkeypatch.setenv("LVI_LLM__STRUCTURED_OUTPUT", "off")

    def fake_chat(self, messages, *, schema_name=None, temperature=None, max_tokens=None):
        user = next(message["content"] for message in messages if message["role"] == "user")
        if "BLOQUE" in user:
            return json.dumps(CANDIDATES_BODY, ensure_ascii=False)
        return json.dumps(CONTENT_MAP_BODY, ensure_ascii=False)

    monkeypatch.setattr(lmstudio_module.LMStudioClient, "chat", fake_chat)
    return CANDIDATES_BODY


def test_candidates_runs_the_whole_engine_until_edit_plans(
    isolated_env, portrait_video, tmp_path, monkeypatch, whisper_en_cpu, candidates_llm
):
    """`candidates` completa GENERATING_CANDIDATES, RANKING y PLANNING_EDITS.

    El video de prueba dura 3 s: hay que acercar los limites de duracion al
    video, no al reves (la config por entorno permite bajar clips.minimum).
    """
    from src.core import config as config_module

    work = tmp_path / "work_candidates"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())
    monkeypatch.setenv("LVI_CLIPS__MINIMUM", "0.5")
    monkeypatch.setenv("LVI_CLIPS__PREFERRED_MINIMUM", "0.5")
    monkeypatch.setenv("LVI_CLIPS__PREFERRED_MAXIMUM", "3")
    monkeypatch.setenv("LVI_CLIPS__MAXIMUM", "10")

    result = runner.invoke(cli.app, ["candidates", str(portrait_video)])
    assert result.exit_code == 0, result.output
    assert "bloques" in result.output

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    job = repo.list_jobs()[0]
    assert job["state"] == JobState.PLANNING_EDITS.value
    assert repo.resume_stage(job["id"]) == JobState.RENDERING
    assert repo.completed_stages(job["id"]) == [
        JobState.INGESTING,
        JobState.ANALYZING_MEDIA,
        JobState.TRANSCRIBING,
        JobState.BUILDING_CONTENT_MAP,
        JobState.GENERATING_CANDIDATES,
        JobState.RANKING,
        JobState.PLANNING_EDITS,
    ]

    set_doc = artifacts.read_json(work / "portrait" / "clip_candidates.json", schema_name="candidate_set")
    assert len(set_doc["selection"]["selected"]) == 2
    assert set_doc["selection"]["minimum_score"] == 72.0

    plans_dir = work / "portrait" / "edit_plans"
    plan_files = sorted(plans_dir.glob("edit_plan_*.json"))
    assert len(plan_files) == 2
    for plan_file in plan_files:
        artifacts.read_json(plan_file, schema_name="edit_plan")

    kinds = {a["kind"] for a in repo.list_artifacts(job["id"])}
    assert "clip_candidates" in kinds
    plan_kinds = sorted(kind for kind in kinds if kind.startswith("edit_plan:"))
    assert len(plan_kinds) == 2
    assert all(name in ",".join(plan_kinds) for name in ("cand_l2_global_0", "cand_l2_global_1"))


def test_candidates_resumes_after_a_failed_generation_attempt(
    isolated_env,
    portrait_video,
    tmp_path,
    monkeypatch,
    whisper_en_cpu,
    candidates_llm,
):
    """Tras un fallo, el estado del job se recorta al ultimo intento completado
    (aquellos periodos quedan sin corrida COMPLETED, como paso en el E2E real:
    TRANSCRIBING y BUILDING_CONTENT_MAP quedaron a medias). Al relanzar
    `candidates` hay que re-ejecutar las etapas previas EN ORDEN - aun
    reutilizando sus artefactos - para que la maquina de estados pueda avanzar
    de uno en uno hasta GENERATING_CANDIDATES (seccion 50).
    """
    from src.core import config as config_module

    work = tmp_path / "work_candidates_resume"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work.as_posix())
    monkeypatch.setenv("LVI_CLIPS__MINIMUM", "0.5")
    monkeypatch.setenv("LVI_CLIPS__PREFERRED_MINIMUM", "0.5")
    monkeypatch.setenv("LVI_CLIPS__PREFERRED_MAXIMUM", "3")
    monkeypatch.setenv("LVI_CLIPS__MAXIMUM", "10")

    first = runner.invoke(cli.app, ["candidates", str(portrait_video)])
    assert first.exit_code == 0, first.output

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    job = repo.list_jobs()[0]

    for stage in (
        "TRANSCRIBING",
        "BUILDING_CONTENT_MAP",
        "GENERATING_CANDIDATES",
        "RANKING",
        "PLANNING_EDITS",
    ):
        with repo.db.transaction() as conn:
            conn.execute(
                "UPDATE stage_runs SET state='DROPPED' WHERE job_id=? AND stage=?",
                (job["id"], stage),
            )
            conn.execute(
                "UPDATE jobs SET state=? WHERE id=?",
                (JobState.ANALYZING_MEDIA.value, job["id"]),
            )
    assert repo.resume_stage(job["id"]) == JobState.TRANSCRIBING

    second = runner.invoke(cli.app, ["candidates", str(portrait_video)])
    assert second.exit_code == 0, second.output

    job = repo.get_job(job["id"])
    assert job["state"] == JobState.PLANNING_EDITS.value
    assert repo.resume_stage(job["id"]) == JobState.RENDERING
    assert repo.completed_stages(job["id"]) == [
        JobState.INGESTING,
        JobState.ANALYZING_MEDIA,
        JobState.TRANSCRIBING,
        JobState.BUILDING_CONTENT_MAP,
        JobState.GENERATING_CANDIDATES,
        JobState.RANKING,
        JobState.PLANNING_EDITS,
    ]


# --- Sprint 6: render (FFmpeg + libass) -------------------------------------


def _edit_plan_document(source: Path, *, clip_id: str = "cand_001") -> dict:
    """Plan minimo valido contra `edit_plan.schema.json`."""
    return {
        "schema_version": "1.0",
        "clip_id": clip_id,
        "job_id": "job_render",
        "candidate_id": clip_id,
        "source": {"file": str(source), "start": 0.0, "end": 3.0, "duration": 3.0},
        "output": {
            "width": 1080, "height": 1920, "fps": 30, "format": "9:16",
            "video_codec": "h264_nvenc", "crf": 20, "preset": "p4",
            "pixel_format": "yuv420p", "audio_codec": "aac",
            "audio_bitrate": "192k", "container": "mp4",
        },
        "crop": {"mode": "center", "background": "crop"},
        "subtitles": {
            "enabled": True, "style": "default", "language": None,
            "max_words_per_line": 4, "max_lines": 2, "max_chars_per_line": 22,
            "min_duration_ms": 800, "max_duration_ms": 4000,
            "position": "lower_center", "renderer": "ffmpeg_libass",
        },
        "hook": {
            "type": "none", "origin": "original", "text": None,
            "requires_generated_hook": False, "displayed": False,
            "integrity_note": None,
        },
        "audio": {
            "normalize": True, "target_lufs": -14.0, "true_peak_db": -1.5,
            "fade_in_seconds": 0.0, "fade_out_seconds": 0.15, "music": None,
        },
        "effects": [],
    }


def _transcript_document() -> dict:
    words = [
        {"word": word, "start": start, "end": end, "probability": 0.9}
        for start, end, word in [
            (0.2, 0.7, "el"), (0.7, 1.2, "motor"), (1.2, 1.8, "funciona"),
            (1.8, 2.4, "muy"), (2.4, 3.0, "bien"),
        ]
    ]
    return {
        "schema_version": "1.0",
        "language": "es",
        "duration": 3.0,
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0,
             "text": "el motor funciona muy bien", "words": words}
        ],
    }


@pytest.fixture
def planned_job(isolated_env, portrait_video, tmp_path, monkeypatch):
    """Job con estado en PLANNING_EDITS y su workspace con plan + transcript.

    Se construye a mano en vez de correr `candidates`: el render solo necesita
    los artefactos de PLANNING_EDITS, y asi el test no gasta una transcripcion
    de Whisper para probar FFmpeg.
    """
    from src.core import config as config_module
    from src.media import ffmpeg as ffmpeg_module
    from src.media import workspace as workspace_module

    if ffmpeg_module.which("ffmpeg") is None:
        pytest.skip("FFmpeg no esta en PATH; se omite el E2E de render")

    work_root = tmp_path / "work_render"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work_root.as_posix())
    monkeypatch.setenv("LVI_SUBTITLES__RENDERER", "ffmpeg_libass")

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    video_id = repo.register_video(path=str(portrait_video), sha256="f" * 64)
    job = repo.create_job(video_id)
    for stage in db_module.PIPELINE_ORDER[1:]:
        if stage == JobState.RENDERING:
            break
        run_id = repo.start_stage(job["id"], stage)
        repo.finish_stage(run_id)
        repo.transition(job["id"], stage)

    work = workspace_module.Workspace(
        root=work_root / workspace_module.safe_stem(portrait_video.stem)
    ).create()
    artifacts.write_json(work.transcript, _transcript_document(), schema_name="transcript")
    artifacts.write_json(
        work.edit_plans / "edit_plan_cand_001.json",
        _edit_plan_document(portrait_video),
        schema_name="edit_plan",
    )
    return job["id"], work


def test_render_produce_un_clip_por_edit_plan(isolated_env, planned_job):
    job_id, work = planned_job

    result = runner.invoke(cli.app, ["render", job_id])
    assert result.exit_code == 0, result.output
    assert "renderizado" in result.output

    out = work.renders / "cand_001.mp4"
    assert out.is_file() and out.stat().st_size > 0
    assert (work.renders / "cand_001.ass").is_file()

    from src.core import config as config_module

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    job = repo.get_job(job_id)
    assert job["state"] == JobState.RENDERING.value
    assert repo.resume_stage(job_id) == JobState.QUALITY_CONTROL
    assert JobState.RENDERING in repo.completed_stages(job_id)

    kinds = {a["kind"] for a in repo.list_artifacts(job_id)}
    assert "render:cand_001" in kinds
    artifact = repo.get_artifact(job_id, JobState.RENDERING, "render:cand_001")
    assert artifact["path"] == str(out)
    assert artifact["sha256"] and len(artifact["sha256"]) == 64


def test_render_registra_tambien_los_auxiliares(isolated_env, planned_job, monkeypatch):
    """El master y el props.json son artefactos distintos.

    `kind` es unico por (job, stage), asi que un kind comun para los dos deja
    registrado solo el ultimo en el ON CONFLICT.
    """
    from src.core import config as config_module
    from src.media import workspace as workspace_module
    from src.media import ffmpeg as ffmpeg_module

    if ffmpeg_module.which("ffmpeg") is None:
        pytest.skip("FFmpeg no esta en PATH; se omite el E2E de render")
    if ffmpeg_module.which("node") is None:
        pytest.skip("node no esta en PATH; no hay compositor que ejecutar")

    job_id, work = planned_job
    # El plan fixture nace con ffmpeg_libass (rapido). Se cambia el renderer del
    # plan en disco a remotion, que es el unico que deja master + props.
    plan_path = next(work.edit_plans.glob("edit_plan_*.json"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["subtitles"]["renderer"] = "remotion"
    artifacts.write_json(plan_path, plan, schema_name="edit_plan")

    result = runner.invoke(cli.app, ["render", job_id])
    assert result.exit_code == 0, result.output

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    kinds = {a["kind"] for a in repo.list_artifacts(job_id)}
    assert "render:cand_001" in kinds
    assert "render_aux:cand_001:mp4" in kinds, "falta el master vertical"
    assert "render_aux:cand_001:json" in kinds, "falta el props.json"

    master = repo.get_artifact(job_id, JobState.RENDERING, "render_aux:cand_001:mp4")
    assert master["path"].endswith("_master.mp4")
    assert Path(master["path"]).is_file()


def test_render_no_repite_un_clip_ya_hecho(isolated_env, planned_job):
    """Seccion 50: si el fichero ya existe, no se vuelve a codificar."""
    job_id, work = planned_job

    first = runner.invoke(cli.app, ["render", job_id])
    assert first.exit_code == 0, first.output
    stamp = (work.renders / "cand_001.mp4").stat().st_mtime_ns

    second = runner.invoke(cli.app, ["render", job_id])
    assert second.exit_code == 0, second.output
    assert "reutilizado" in second.output
    assert (work.renders / "cand_001.mp4").stat().st_mtime_ns == stamp


def test_render_sin_edit_plans_dice_que_ejecute_candidates(isolated_env, portrait_video, tmp_path, monkeypatch):
    from src.core import config as config_module

    work_root = tmp_path / "work_vacio"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work_root.as_posix())
    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    video_id = repo.register_video(path=str(portrait_video), sha256="e" * 64)
    job = repo.create_job(video_id)

    result = runner.invoke(cli.app, ["render", job["id"]])
    assert result.exit_code == cli.EXIT_ERROR
    assert "candidates" in result.output


# --- Integracion de configuracion + logging (secciones 52 y 57) ---


def test_env_override_reaches_lowercase_config_keys(monkeypatch):
    """Las variables van en MAYUSCULAS; el YAML usa minusculas.

    Sin normalizar, `LVI_LLM__BASE_URL` creaba una clave `LLM`/`BASE_URL` nueva
    en vez de sobrescribir `llm.base_url`, y el override pasaba desapercibido.
    """
    from src.core import config as config_module

    monkeypatch.setenv("LVI_LLM__BASE_URL", "http://192.168.1.50:1234/v1")
    config = config_module.load()
    assert config["llm"]["base_url"] == "http://192.168.1.50:1234/v1"
    assert "LLM" not in config, "el override no debe crear una rama en mayusculas"


def test_env_override_creates_nested_keys_when_absent(monkeypatch):
    from src.core import config as config_module

    monkeypatch.setenv("LVI_PATHS__LOGS_ROOT", "C:/lvi/logs")
    config = config_module.load()
    assert config["paths"]["logs_root"] == "C:/lvi/logs"
    assert config_module.resolve_path(config, "logs_root") == Path("C:/lvi/logs")


def test_env_override_routes_logs_to_the_isolated_path(isolated_env):
    """`LVI_LOGGING__FILE` debe apartar el log de la CLI del log real."""
    from src.core import config as config_module
    from src.core import logs

    config = config_module.load()
    expected = Path(config["logging"]["file"])
    assert expected.parent != Path("logs"), "el override de entorno no se aplico"

    logs.setup(config, console_level="CRITICAL")
    with logs.log_context(job_id="job_test", stage="cli"):
        logs.get_logger().info("evento desde la CLI")

    events = [
        json.loads(line)
        for line in expected.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(events) == 1
    assert events[0]["message"] == "evento desde la CLI"
    assert events[0]["job_id"] == "job_test"
    assert events[0]["stage"] == "cli"


# --- report: E2E de QC + clip_record + report.json (secciones 33, 37, 56) --


def _normalized_document(source: Path) -> dict:
    """Parrafos en la ventana del clip: el QC semantico los lee de aqui."""
    return {
        "schema_version": "1.0",
        "derived_from": {
            "file": str(source),
            "sha256": "d" * 64,
            "schema_version": "1.0",
        },
        "language": "es",
        "duration": 3.0,
        "paragraphs": [
            {"id": 0, "start": 0.2, "end": 1.4, "text": "el motor funciona muy bien"},
            {"id": 1, "start": 1.5, "end": 2.9, "text": "y el equipo esta listo"},
        ],
        "full_text": "el motor funciona muy bien y el equipo esta listo",
    }


def _candidate_set_document(job_id: str, source: Path) -> dict:
    """candidate_set valido, construido por el camino real del generador.

    No se escribe a mano porque el schema de `candidate` tiene una decena de
    campos obligatorios (hook, progression, signatures, generated_by...) y un
    fixture manual se queda desincronizado en cuanto el schema cambia.
    """
    from src.analysis import candidates as candidates_module
    from test_candidates import BODY_CANDIDATE
    from test_schemas import NORMALIZED_TRANSCRIPT

    candidato = candidates_module._process_candidate(
        dict(BODY_CANDIDATE),
        index=0,
        block_id="w2",
        duration=3.0,
        paragraphs=NORMALIZED_TRANSCRIPT["paragraphs"],
        full_text="el motor funciona muy bien y el equipo esta listo",
        video_file=str(source),
        video_sha256="f" * 64,
        job_id=job_id,
        model_run={
            "provider": "lmstudio",
            "model": "gemma-fake",
            "prompt_version": "1.0",
            "temperature": 0.1,
        },
        blocks_min=1.0,
        blocks_max=90.0,
        drops=[],
    )
    assert candidato is not None
    # El plan de este fixture es cand_001; se renombra el id para que el QC,
    # el clip_record y el report hablen del mismo clip.
    candidato["candidate_id"] = "cand_001"
    return {
        "schema_version": "1.0",
        "job_id": job_id,
        "source": {"file": str(source), "sha256": "f" * 64, "duration": 3.0},
        "candidates": [candidato],
        "selection": {
            "minimum_score": 60.0,
            "max_temporal_overlap": 0.3,
            "selected": ["cand_001"],
        },
    }


@pytest.fixture
def rendered_job(isolated_env, portrait_video, tmp_path, monkeypatch):
    """Job en RENDERING con un clip YA renderizado de verdad, para el QC.

    El render se hace con `ffmpeg_libass` y no con Remotion: el QC tecnico no
    sabe que renderer se uso, y asi el E2E no depende de Node ni de la GPU.
    """
    from src.core import config as config_module
    from src.media import ffmpeg as ffmpeg_module
    from src.media import workspace as workspace_module

    if ffmpeg_module.which("ffmpeg") is None:
        pytest.skip("FFmpeg no esta en PATH; se omite el E2E de report")

    work_root = tmp_path / "work_report"
    monkeypatch.setenv("LVI_PATHS__WORK_ROOT", work_root.as_posix())
    monkeypatch.setenv("LVI_PATHS__REPORTS_ROOT", (tmp_path / "reports").as_posix())
    monkeypatch.setenv("LVI_SUBTITLES__RENDERER", "ffmpeg_libass")

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    video_id = repo.register_video(path=str(portrait_video), sha256="f" * 64)
    job = repo.create_job(video_id)
    for stage in db_module.PIPELINE_ORDER[1:]:
        if stage == JobState.RENDERING:
            break
        run_id = repo.start_stage(job["id"], stage)
        repo.finish_stage(run_id)
        repo.transition(job["id"], stage)

    work = workspace_module.Workspace(
        root=work_root / workspace_module.safe_stem(portrait_video.stem)
    ).create()
    artifacts.write_json(work.transcript, _transcript_document(), schema_name="transcript")
    artifacts.write_json(
        work.normalized_transcript,
        _normalized_document(portrait_video),
        schema_name="normalized_transcript",
    )
    artifacts.write_json(
        work.clip_candidates,
        _candidate_set_document(job["id"], portrait_video),
        schema_name="candidate_set",
    )
    artifacts.write_json(
        work.edit_plans / "edit_plan_cand_001.json",
        _edit_plan_document(portrait_video),
        schema_name="edit_plan",
    )

    render = runner.invoke(cli.app, ["render", job["id"]])
    assert render.exit_code == 0, render.output
    assert (work.renders / "cand_001.mp4").is_file()
    return job["id"], work


def test_report_escribe_clip_record_y_report_json(rendered_job, isolated_env, tmp_path):
    """`report --skip-llm` cierra el pipeline sin llamar al LLM."""
    job_id, work = rendered_job

    result = runner.invoke(cli.app, ["report", job_id, "--skip-llm"])

    assert result.exit_code == 0, result.output
    assert "report" in result.output

    record = json.loads((work.root / "clip_records" / "cand_001.json").read_text(encoding="utf-8"))
    assert record["clip_id"] == "cand_001"
    assert record["qc"]["passed"] is True
    assert record["qc"]["subtitles_ok"] is True

    # El score sale del candidato real, no de un numero fijo en el test: si la
    # normalizacion de la seleccion cambia, este test sigue siendo cierto.
    candidatos = json.loads(work.clip_candidates.read_text(encoding="utf-8"))["candidates"]
    esperado = candidatos[0]["score"]["total"]
    assert record["selection"]["score"] == esperado
    # Sin LLM no hay veredicto semantico ni metadata, y el record lo refleja
    # en vez de inventarlos.
    assert record["qc"]["semantic"] is None
    assert "metadata" not in record

    reports = sorted((tmp_path / "reports").glob("report_*.json"))
    assert reports, "no se escribio report.json"
    informe = json.loads(reports[0].read_text(encoding="utf-8"))
    assert informe["job_id"] == job_id
    assert informe["candidates_found"] == 1
    assert informe["candidates_approved"] == 1
    assert informe["clips_rendered"] == 1
    assert informe["clips_rejected"] == 0
    assert informe["average_score"] == esperado
    assert informe["errors"] == []
    assert informe["clips"][0]["passed"] is True


def test_report_cierra_el_job_en_completed(rendered_job):
    job_id, _work = rendered_job
    assert runner.invoke(cli.app, ["report", job_id, "--skip-llm"]).exit_code == 0

    from src.core import config as config_module

    config = config_module.load()
    repo = db_module.JobRepository(db_module.open_database(config))
    assert repo.get_job(job_id)["state"] == JobState.COMPLETED.value


def test_report_es_idempotente(rendered_job):
    """Repetir el comando no duplica artefactos ni rompe el estado."""
    job_id, work = rendered_job
    assert runner.invoke(cli.app, ["report", job_id, "--skip-llm"]).exit_code == 0

    segundo = runner.invoke(cli.app, ["report", job_id, "--skip-llm"])
    assert segundo.exit_code == 0, segundo.output

    records = sorted((work.root / "clip_records").glob("*.json"))
    assert len(records) == 1
    assert "QC fallido" not in segundo.output


def test_report_detecta_un_render_roto(rendered_job):
    """Un MP4 que no se puede leer es un QC fallido, no un jobtumbao."""
    job_id, work = rendered_job
    (work.renders / "cand_001.mp4").write_bytes(b"esto no es un mp4")

    result = runner.invoke(cli.app, ["report", job_id, "--skip-llm"])

    assert result.exit_code != 0, "un clip que no pasa QC no puede cerrar el job"
    record = json.loads((work.root / "clip_records" / "cand_001.json").read_text(encoding="utf-8"))
    assert record["qc"]["passed"] is False
    assert record["qc"]["problems"]


def test_report_sin_edit_plans_dice_que_ejecute_render(rendered_job):
    job_id, work = rendered_job
    for plan in work.edit_plans.glob("edit_plan_*.json"):
        plan.unlink()

    result = runner.invoke(cli.app, ["report", job_id, "--skip-llm"])
    assert result.exit_code != 0
    assert "render" in result.output


def test_report_json_sale_por_stdout(rendered_job):
    """Con `--json` la salida es parseable, para scripting."""
    job_id, _work = rendered_job
    result = runner.invoke(cli.app, ["report", job_id, "--skip-llm", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    informe = payload["report"]
    assert informe["clips_rendered"] == 1
    assert informe["job_id"] == job_id
    assert payload["next_state"] == JobState.COMPLETED.value
    assert payload["report_path"].endswith(".json")
