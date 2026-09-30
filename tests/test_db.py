"""Persistencia y maquina de estados de jobs (secciones 44, 49, 50, 51).

Offline: usa una base temporal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import db as db_module  # noqa: E402
from src.core.db import JobState  # noqa: E402

SHA = "a" * 64


@pytest.fixture
def database(tmp_path: Path) -> db_module.Database:
    return db_module.Database(tmp_path / "test.db").initialize()


@pytest.fixture
def repo(database: db_module.Database) -> db_module.JobRepository:
    return db_module.JobRepository(database)


# --- init y esquema ---


def test_initialize_is_idempotent(database):
    before = database.schema_version()
    database.initialize().initialize()
    assert database.schema_version() == before == db_module.SCHEMA_VERSION


def test_section_44_tables_exist(database):
    rows = database.query(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    names = {row["name"] for row in rows}
    # La seccion 44 lista estas tablas. `videos` y `jobs` explicitamente, y el
    # resto con los nombres que usa el pipeline.
    for expected in ("videos", "jobs", "performance_snapshots", "model_runs"):
        assert expected in names, f"falta la tabla {expected} (seccion 44)"
    assert {"artifacts", "stage_runs", "clip_records"} <= names


# --- seccion 51: idempotencia por contenido ---


def test_register_video_is_idempotent_by_sha256(repo):
    first = repo.register_video(path="input/a.mp4", sha256=SHA)
    second = repo.register_video(path="input/copia-renombrada.mp4", sha256=SHA)
    assert first == second
    assert len(repo.db.query("SELECT * FROM videos")) == 1


def test_different_content_creates_a_second_video(repo):
    first = repo.register_video(path="input/a.mp4", sha256=SHA)
    second = repo.register_video(path="input/b.mp4", sha256="b" * 64)
    assert first != second
    assert len(repo.db.query("SELECT * FROM videos")) == 2


def test_video_metadata_is_persisted(repo):
    vid = repo.register_video(
        path="input/a.mp4", sha256=SHA, duration=120.5, width=1920,
        height=1080, fps=30.0, video_codec="h264", audio_codec="aac",
    )
    video = repo.get_video(vid)
    assert video["duration"] == 120.5
    assert video["width"] == 1920
    assert video["audio_codec"] == "aac"


# --- seccion 49: maquina de estados ---


def test_new_job_starts_queued(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    assert job["state"] == db_module.JobState.QUEUED
    assert job["attempts"] == 0


def test_full_happy_path_walks_every_stage(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    for stage in db_module.PIPELINE_ORDER[1:]:
        job = repo.transition(job["id"], stage)
    assert job["state"] == db_module.JobState.COMPLETED


def test_skipping_a_stage_is_rejected(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    with pytest.raises(db_module.InvalidTransitionError) as exc:
        repo.transition(job["id"], db_module.JobState.RENDERING)
    assert "transicion invalida" in str(exc.value)


def test_going_backwards_is_rejected(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.transition(job["id"], db_module.JobState.INGESTING)
    repo.transition(job["id"], db_module.JobState.ANALYZING_MEDIA)
    with pytest.raises(db_module.InvalidTransitionError):
        repo.transition(job["id"], db_module.JobState.INGESTING)


def test_any_non_terminal_stage_can_fail(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.transition(job["id"], JobState.INGESTING)
    updated = repo.transition(
        job["id"], db_module.JobState.FAILED,
        error_kind="FatalError", error_message="video corrupto",
    )
    assert updated["state"] == db_module.JobState.FAILED
    assert updated["error_kind"] == "FatalError"
    assert updated["error_message"] == "video corrupto"


def test_failed_job_cannot_advance(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.transition(job["id"], db_module.JobState.FAILED, error_message="x")
    with pytest.raises(db_module.InvalidTransitionError):
        repo.transition(job["id"], db_module.JobState.INGESTING)


def test_completed_job_cannot_fail_later(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    for stage in db_module.PIPELINE_ORDER[1:]:
        repo.transition(job["id"], stage)
    with pytest.raises(db_module.InvalidTransitionError):
        repo.transition(job["id"], db_module.JobState.FAILED)


def test_retrying_increments_attempts_and_can_restart(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.transition(job["id"], db_module.JobState.INGESTING)
    updated = repo.transition(job["id"], db_module.JobState.RETRYING)
    assert updated["attempts"] == 1
    assert updated["state"] == db_module.JobState.RETRYING
    restarted = repo.transition(job["id"], db_module.JobState.INGESTING)
    assert restarted["state"] == db_module.JobState.INGESTING


def test_failed_is_not_terminal_because_jobs_can_be_resumed(repo):
    """FAILED se puede abandonar; solo COMPLETED es final.

    Marcar FAILED como terminal hacia imposible el unico camino de reintento
    (FAILED -> RETRYING -> etapa) y contradecia a `resume_stage`, que si sabe
    decir desde donde seguir.
    """
    assert db_module.TERMINAL == frozenset({JobState.COMPLETED})
    assert db_module.can_transition(JobState.FAILED, JobState.RETRYING)
    assert db_module.can_transition(JobState.INGESTING, JobState.FAILED)


def test_a_failed_stage_can_be_retried(repo):
    """El reintento completo: fallar, reintentar y volver a INGESTING."""
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.transition(job["id"], JobState.INGESTING)
    repo.transition(job["id"], JobState.FAILED, error_kind="no_audio")

    retrying = repo.transition(job["id"], JobState.RETRYING)
    assert retrying["attempts"] == 1
    again = repo.transition(job["id"], JobState.INGESTING)
    assert again["state"] == JobState.INGESTING
    assert again["attempts"] == 1


def test_failed_cannot_jump_straight_to_ingesting(repo):
    """Reintentar es pasar por RETRYING, no saltarse la maquina de estados."""
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.transition(job["id"], JobState.INGESTING)
    repo.transition(job["id"], JobState.FAILED)
    with pytest.raises(db_module.InvalidTransitionError):
        repo.transition(job["id"], JobState.INGESTING)


def test_completed_cannot_be_retried(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    for stage in db_module.PIPELINE_ORDER[1:]:
        repo.transition(job["id"], stage)
    with pytest.raises(db_module.InvalidTransitionError):
        repo.transition(job["id"], JobState.RETRYING)
    with pytest.raises(db_module.InvalidTransitionError):
        repo.transition(job["id"], JobState.FAILED)


def test_register_video_backfills_metadata_on_a_second_call(repo):
    """Un job fallido deja la fila del video con la metadata a null.

    Si la segunda llamada solo devolviera el id, esa fila se quedaria incompleta
    para siempre. `jobs show` y los informes la necesitan.
    """
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    assert repo.get_video(vid)["duration"] is None

    same = repo.register_video(
        path="input/a.mp4", sha256=SHA, duration=123.4, width=1080, height=1920, fps=30.0,
    )
    assert same == vid
    row = repo.get_video(vid)
    assert row["duration"] == 123.4
    assert (row["width"], row["height"], row["fps"]) == (1080, 1920, 30.0)
    assert len(repo.db.query("SELECT * FROM videos")) == 1


def test_register_video_never_overwrites_known_metadata(repo):
    """COALESCE: un valor conocido no se pisa con un desconocido."""
    vid = repo.register_video(path="input/a.mp4", sha256=SHA, duration=10.0, width=640)
    repo.register_video(path="otro/nombre.mp4", sha256=SHA, duration=None, width=None)
    row = repo.get_video(vid)
    assert row["duration"] == 10.0
    assert row["width"] == 640
    assert row["path"] == "input/a.mp4"


def test_transition_on_unknown_job_raises(repo):
    with pytest.raises(KeyError):
        repo.transition("job_inexistente", db_module.JobState.INGESTING)


def test_next_stage_helper():
    assert db_module.next_stage(JobState.QUEUED) == JobState.INGESTING
    assert db_module.next_stage(JobState.GENERATING_SEO) == JobState.COMPLETED
    assert db_module.next_stage(JobState.COMPLETED) is None
    assert db_module.next_stage(JobState.FAILED) is None


class TestEnterStage:
    """`enter_stage` es la version idempotente de `transition`.

    Sin ella, relanzar `python main.py analyze video.mp4` dos veces seguidas
    reventaba con `TRANSCRIBING -> TRANSCRIBING`, que no es una transicion
    valida. Es el fallo que aparecio al repetir el comando.
    """

    def _job(self, repo) -> str:
        vid = repo.register_video(path="input/a.mp4", sha256=SHA)
        return repo.create_job(vid, job_id="job_enter")["id"]

    def test_mueve_el_job_desde_queued(self, repo):
        job_id = self._job(repo)

        assert repo.enter_stage(job_id, JobState.INGESTING) is True
        assert repo.get_job(job_id)["state"] == JobState.INGESTING.value

    def test_ya_esta_en_ese_estado_no_transiciona(self, repo):
        job_id = self._job(repo)
        repo.enter_stage(job_id, JobState.INGESTING)

        assert repo.enter_stage(job_id, JobState.INGESTING) is False
        assert repo.get_job(job_id)["state"] == JobState.INGESTING.value

    def test_desde_failed_pasa_por_retrying(self, repo):
        job_id = self._job(repo)
        repo.enter_stage(job_id, JobState.INGESTING)
        repo.transition(job_id, JobState.FAILED, error_kind="x", error_message="y")

        assert repo.enter_stage(job_id, JobState.INGESTING) is True
        assert repo.get_job(job_id)["state"] == JobState.INGESTING.value
        assert repo.get_job(job_id)["attempts"] == 1, "el reintento tiene que contar"

    def test_desde_retrying_reejecuta_la_etapa(self, repo):
        job_id = self._job(repo)
        repo.enter_stage(job_id, JobState.INGESTING)
        repo.transition(job_id, JobState.FAILED)
        repo.transition(job_id, JobState.RETRYING)

        assert repo.enter_stage(job_id, JobState.INGESTING) is True
        assert repo.get_job(job_id)["state"] == JobState.INGESTING.value

    def test_no_retrocede_si_el_job_ya_esta_mas_adelante(self, repo):
        """Regenerar un artefacto temprano no debe deshacer el progreso."""
        job_id = self._job(repo)
        for estado in (JobState.INGESTING, JobState.ANALYZING_MEDIA, JobState.TRANSCRIBING):
            repo.enter_stage(job_id, estado)

        assert repo.enter_stage(job_id, JobState.INGESTING) is False
        assert repo.get_job(job_id)["state"] == JobState.TRANSCRIBING.value

    def test_completed_no_se_reabre(self, repo):
        job_id = self._job(repo)
        for estado in db_module.WORK_STAGES:
            repo.enter_stage(job_id, estado)
        repo.transition(job_id, JobState.COMPLETED)

        assert repo.enter_stage(job_id, JobState.INGESTING) is False
        assert repo.get_job(job_id)["state"] == JobState.COMPLETED.value

    def test_job_desconocido(self, repo):
        with pytest.raises(KeyError):
            repo.enter_stage("no_existe", JobState.INGESTING)

    def test_repetir_analyze_no_rompe_la_maquina_de_estados(self, repo):
        """El escenario real: analysis, transcripcion, y otra vez analysis."""
        job_id = self._job(repo)

        for _ in range(3):
            repo.enter_stage(job_id, JobState.INGESTING)
            repo.enter_stage(job_id, JobState.ANALYZING_MEDIA)
            repo.enter_stage(job_id, JobState.TRANSCRIBING)

        assert repo.get_job(job_id)["state"] == JobState.TRANSCRIBING.value


# --- seccion 50: checkpoints ---


def test_resume_returns_first_incomplete_stage(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    for stage in (JobState.INGESTING, JobState.ANALYZING_MEDIA, JobState.TRANSCRIBING):
        run_id = repo.start_stage(job["id"], stage)
        repo.finish_stage(run_id)
        repo.transition(job["id"], stage)

    assert repo.completed_stages(job["id"]) == [
        JobState.INGESTING,
        JobState.ANALYZING_MEDIA,
        JobState.TRANSCRIBING,
    ]
    assert repo.resume_stage(job["id"]) == JobState.BUILDING_CONTENT_MAP


def test_remotion_failure_does_not_force_rewisper(repo):
    """El escenario concreto de la seccion 50."""
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    for stage in db_module.PIPELINE_ORDER[1 : db_module.PIPELINE_ORDER.index(JobState.RENDERING)]:
        run_id = repo.start_stage(job["id"], stage)
        repo.finish_stage(run_id)
        repo.transition(job["id"], stage)

    failing = repo.start_stage(job["id"], JobState.RENDERING)
    repo.finish_stage(failing, state="FAILED", error_kind="RecoverableError")
    repo.transition(job["id"], db_module.JobState.FAILED, error_message="remotion fallo")

    assert repo.resume_stage(job["id"]) == JobState.RENDERING
    assert JobState.TRANSCRIBING in repo.completed_stages(job["id"])
    assert repo.completed_stages(job["id"]) == [
        str(stage)
        for stage in db_module.WORK_STAGES
        if stage
        not in (JobState.RENDERING, JobState.QUALITY_CONTROL, JobState.GENERATING_SEO)
    ]


def test_incomplete_stage_run_does_not_count_as_done(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    run_id = repo.start_stage(job["id"], JobState.INGESTING)
    repo.finish_stage(run_id, state="FAILED", error_kind="RecoverableError")
    assert repo.completed_stages(job["id"]) == []
    assert repo.resume_stage(job["id"]) == JobState.INGESTING


def test_completed_job_resumes_to_completed(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    for stage in db_module.PIPELINE_ORDER[1:]:
        run_id = repo.start_stage(job["id"], stage)
        repo.finish_stage(run_id)
        repo.transition(job["id"], stage)
    assert repo.resume_stage(job["id"]) == JobState.COMPLETED


# --- artifacts ---


def test_artifact_is_upserted_per_stage_and_kind(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.record_artifact(job["id"], JobState.TRANSCRIBING, "transcript", "work/a/t.json")
    repo.record_artifact(job["id"], JobState.TRANSCRIBING, "transcript", "work/a/t2.json")

    artifacts = repo.list_artifacts(job["id"])
    assert len(artifacts) == 1, "reescribir un artefacto no debe duplicarlo"
    assert artifacts[0]["path"] == "work/a/t2.json"


def test_different_kinds_coexist(repo):
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.record_artifact(job["id"], JobState.RENDERING, "video", "output/a.mp4")
    repo.record_artifact(job["id"], JobState.RENDERING, "subtitles", "work/a.srt")
    assert len(repo.list_artifacts(job["id"])) == 2
    assert repo.get_artifact(job["id"], JobState.RENDERING, "video")["path"] == "output/a.mp4"


def test_a_known_sha_is_never_degraded_to_null(repo):
    """Reutilizar un artefacto no puede borrar su hash.

    Cuando `analyze` se relanza, la etapa de transcripcion reutiliza
    `transcript.json` en vez de regenerarlo. Si esa ruta devuelve el hash vacio,
    el upsert lo graba como NULL y la trazabilidad del artefacto se pierde sin
    que nadie se entere: el JSON de la base ya no permite verificar que el
    archivo del disco no fue editado a mano.
    """
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.record_artifact(
        job["id"], JobState.TRANSCRIBING, "transcript", "work/a/t.json", sha256="a" * 64
    )

    repo.record_artifact(job["id"], JobState.TRANSCRIBING, "transcript", "work/a/t.json", sha256=None)

    fila = repo.get_artifact(job["id"], JobState.TRANSCRIBING, "transcript")
    assert fila["sha256"] == "a" * 64


def test_a_new_sha_replaces_the_old_one(repo):
    """COALESCE protege el NULL, no el valor genuineo: si cambia, cambia."""
    vid = repo.register_video(path="input/a.mp4", sha256=SHA)
    job = repo.create_job(vid)
    repo.record_artifact(job["id"], JobState.RENDERING, "video", "out/a.mp4", sha256="a" * 64)

    repo.record_artifact(job["id"], JobState.RENDERING, "video", "out/b.mp4", sha256="b" * 64)

    fila = repo.get_artifact(job["id"], JobState.RENDERING, "video")
    assert fila["sha256"] == "b" * 64
    assert fila["path"] == "out/b.mp4"


# --- LLM telemetry ---


def test_model_runs_track_adhesion_rate(repo):
    """El riesgo C-4 (JSON invalido) se mide con esta tabla."""
    repo.record_model_run(stage="candidates", model="gemma-3-4b", provider="lmstudio",
                          schema_name="candidate", attempts=2, succeeded=True)
    repo.record_model_run(stage="candidates", model="gemma-3-4b", provider="lmstudio",
                          schema_name="candidate", attempts=3, succeeded=False,
                          error_detail="score fuera de rango")
    stats = repo.model_run_stats()
    assert stats["total"] == 2
    assert stats["succeeded"] == 1
    assert stats["attempts"] == 5


def test_model_run_stats_empty_database(repo):
    stats = repo.model_run_stats()
    assert stats["total"] == 0


# --- seccion 74: URL de base de datos ---


def test_parse_database_url_accepts_sqlite():
    assert db_module.parse_database_url("sqlite:///database/lvi.db") == Path("database/lvi.db")


def test_parse_database_url_rejects_other_schemes():
    for url in ("postgresql://user:pw@host/lvi", "mysql://host/lvi", "lvi.db"):
        with pytest.raises(ValueError):
            db_module.parse_database_url(url)


def test_open_database_uses_config(tmp_path, monkeypatch):
    from src.core import config as config_module

    config = config_module.load()
    target = tmp_path / "from_config.db"
    monkeypatch.setitem(
        config, "orchestration", {"database_url": f"sqlite:///{target.as_posix()}"}
    )
    database = db_module.open_database(config)
    assert database.path == target
    assert database.path.exists()
    assert database.schema_version() == db_module.SCHEMA_VERSION


def test_open_database_resolves_relative_url_against_project_root():
    from src.core import config as config_module

    config = config_module.load()
    database = db_module.open_database(config, initialize=False)
    expected = config_module.project_root(config) / "database" / "local_video_intelligence.db"
    assert database.path == expected


def test_transaction_rolls_back_on_error(database):
    repo = db_module.JobRepository(database)
    with pytest.raises(RuntimeError):
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO videos (id, path, sha256, created_at) VALUES ('x', 'p', 's', 't')"
            )
            raise RuntimeError("fallo a mitad")
    assert database.query("SELECT * FROM videos WHERE id = 'x'") == []


def test_foreign_keys_are_enforced(database):
    """Un artefacto sin job no debe existir: el esquema lo prohibe."""
    with pytest.raises(Exception):  # sqlite3.IntegrityError
        with database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, job_id, stage, kind, path, created_at)
                VALUES ('a1', 'job_fantasma', 'RENDERING', 'video', 'x.mp4', 't')
                """
            )
    del database
