"""Tests de clip_record.json y report.json (secciones 37 y 56).

El record es el unico sitio donde queda registrado que le paso a un clip. Si
aqui se pierde un dato (el score de seleccion, el modelo, el QC), se pierde
para siempre, y la seccion 36 dice que sin datos no se puede aprender nada.
Por eso hay tests de que CADA campo sale del artefacto correcto.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import artifacts  # noqa: E402
from src.quality import report as report_module  # noqa: E402
from src.quality.seo import SEOResult  # noqa: E402


PLAN: dict[str, Any] = {
    "source": {"file": "input/video.mp4", "start": 14.0, "end": 51.94, "duration": 37.94},
    "output": {"format": "mp4", "width": 1080, "height": 1920, "fps": 30},
    "subtitles": {"enabled": True, "renderer": "remotion"},
    "crop": {"mode": "center"},
    "hook": {"type": "question", "origin": "original", "requires_generated_hook": False},
}

# Las nueve dimensiones con el tope de cada una (invariants.SCORE_DIMENSION_MAX):
# 15/12/11/8/9/8/7/4/3 = 77. `confidence` no es una dimension y va aparte.
CANDIDATE: dict[str, Any] = {
    "candidate_id": "cand_l2_001_0",
    "content": {"topic": "escape", "summary": "Un grupo prepara una fuga.", "content_type": "story"},
    "score": {
        "total": 77.0,
        "hook": 15, "curiosity": 12, "value": 11, "emotion": 8, "narrative": 9,
        "payoff": 8, "shareability": 7, "standalone": 4, "visual": 3,
        "confidence": 0.8,
    },
    "generated_by": {"model": "gemma-3-4b", "provider": "lmstudio", "prompt_version": "1.0"},
}

QC_OK: dict[str, Any] = {
    "passed": True,
    "problems": [],
    "technical": {"duration_ok": True, "resolution_ok": True, "codec_ok": True},
    "subtitles_ok": True,
    "semantic": {"approved": True, "score": 85.0, "problems": []},
}


def _record(clip_id: str = "c1", **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "clip_id": clip_id,
        "job_id": "job1",
        "plan": PLAN,
        "candidate": CANDIDATE,
        "qc": QC_OK,
        "source_sha256": "a" * 64,
    }
    kwargs.update(overrides)
    return report_module.build_clip_record(**kwargs)


# --- clip_record -----------------------------------------------------------


def test_el_record_trae_el_score_de_seleccion() -> None:
    """El score de la tabla de ranking, NO el del QC semantico."""
    record = _record()
    assert record["selection"]["score"] == 77.0
    assert record["qc"]["semantic"]["score"] == 85.0
    assert record["selection"]["score"] != record["qc"]["semantic"]["score"]


def test_el_score_se_recalcula_si_falta_el_total() -> None:
    """Si el modelo no dio total, se suman solo las nueve dimensiones.

    Sumar todas las claves incluiria `confidence` (0-1) y el total saldria
    inflado. Este es el error que hace que un ranking no cuadre con su report.
    """
    candidato = {**CANDIDATE, "score": {k: v for k, v in CANDIDATE["score"].items() if k != "total"}}
    record = _record(candidate=candidato)
    assert record["selection"]["score"] == 77.0


def test_el_record_trae_el_modelo_que_genero_el_candidato() -> None:
    record = _record()
    assert record["generation"]["model"] == "gemma-3-4b"
    assert record["generation"]["provider"] == "lmstudio"


def test_el_record_trae_el_sha256_de_la_fuente() -> None:
    record = _record()
    assert record["source"]["sha256"] == "a" * 64
    assert record["source"]["start"] == 14.0


def test_un_sin_qc_se_escribe_explicitamente() -> None:
    """El schema exige `qc`, y omitir la clave no valida. Se dice que no corrio."""
    record = _record(qc=None)
    assert record["qc"]["passed"] is False
    assert "QC no ejecutado" in record["qc"]["problems"]


def test_lo_que_no_se_sabe_se_queda_en_null() -> None:
    record = _record(source_sha256=None)
    assert record["source"]["sha256"] is None
    assert record["candidate_id"] == "cand_l2_001_0"


def test_sin_content_type_del_modelo_se_pone_unknown() -> None:
    """El schema exige string. "unknown" es honesto; inventar un tipo, no."""
    candidato = {**CANDIDATE, "content": {"topic": "escape", "summary": "x"}}
    record = _record(candidate=candidato)
    assert record["content"]["content_type"] == "unknown"
    artifacts.validate("clip_record", record)


def test_el_record_incluye_la_metadata_del_seo() -> None:
    """El schema guarda un objeto POR plataforma, no el cuerpo de seo_metadata."""
    metadata = {
        "youtube": {"title": "t", "description": "d", "hashtags": ["a"],
                    "keywords": ["b c"], "hook": "h", "category": "true_crime"},
    }
    record = _record(metadata=metadata)
    assert record["metadata"]["youtube"]["title"] == "t"
    artifacts.validate("clip_record", record)


def test_el_record_se_valida_contra_su_schema() -> None:
    artifacts.validate("clip_record", _record())


def test_el_record_se_escribe_a_disco(tmp_path: Path) -> None:
    resultado = report_module.ClipRecordResult(clip_id="c1", record=_record())
    ruta = report_module.write_clip_record(resultado, tmp_path / "clip_record.json")
    assert ruta.is_file()
    leido = json.loads(ruta.read_text(encoding="utf-8"))
    assert leido["clip_id"] == "c1"
    assert resultado.path == ruta


# --- report ----------------------------------------------------------------


def _candidate_set(*ids: str) -> dict[str, Any]:
    return {
        "candidates": [{"candidate_id": i} for i in ids],
        "selection": {"selected": list(ids)},
    }


def test_el_report_cuenta_los_clips_que_pasan_el_qc() -> None:
    passed = report_module.ClipRecordResult(clip_id="c1", record=_record("c1"))
    fallido = report_module.ClipRecordResult(
        clip_id="c2",
        record=_record("c2", qc={"passed": False, "problems": ["score bajo"]}),
    )
    resumen = report_module.build_report(
        job_id="job1",
        source="input/video.mp4",
        duration=1228.4,
        candidate_set=_candidate_set("c1", "c2"),
        clip_records=[passed, fallido],
        processing_time_seconds=42.2,
    )
    assert resumen.clips_rendered == 1
    assert resumen.clips_rejected == 1
    assert any("QC fallido" in e for e in resumen.errors)


def test_el_score_medio_usa_el_de_seleccion() -> None:
    """Promediar el score semantico mezcla dos cosas que no son comparables."""
    a = report_module.ClipRecordResult(clip_id="c1", record=_record("c1"))
    b = report_module.ClipRecordResult(
        clip_id="c2",
        record=_record("c2", candidate={**CANDIDATE, "score": {**CANDIDATE["score"], "total": 63.0}}),
    )
    resumen = report_module.build_report(
        job_id="job1", source=None, duration=None, candidate_set=None,
        clip_records=[a, b], processing_time_seconds=1.0,
    )
    assert resumen.average_score == 70.0


def test_sin_candidates_el_score_medio_es_null() -> None:
    """Cero clips no es score cero: promediar una lista vacia no tiene sentido."""
    resumen = report_module.build_report(
        job_id="job1", source=None, duration=None, candidate_set=None,
        clip_records=[], processing_time_seconds=1.0,
    )
    assert resumen.average_score is None


def test_sin_clip_candidates_json_los_conteos_son_aproximados() -> None:
    """Se avisa en `errors` en vez de poner un 0 que parece un dato real."""
    passed = report_module.ClipRecordResult(clip_id="c1", record=_record())
    resumen = report_module.build_report(
        job_id="job1", source=None, duration=None, candidate_set=None,
        clip_records=[passed], processing_time_seconds=1.0,
    )
    assert resumen.candidates_found == 1
    assert any("aproximados" in e for e in resumen.errors)


def test_el_report_cuenta_candidatos_aprobados_no_seleccionados() -> None:
    """`approved` es lo que el ranking eligio, no lo que el QC dejo pasar.

    Un candidato seleccionado que luego falla el QC sigue siendo un candidato
    aprobado: lo que fallo fue la publicacion, no la seleccion.
    """
    resumen = report_module.build_report(
        job_id="job1", source=None, duration=None,
        candidate_set={"candidates": [{"candidate_id": i} for i in "abcd"],
                       "selection": {"selected": ["a", "b"]}},
        clip_records=[], processing_time_seconds=1.0,
    )
    assert resumen.candidates_found == 4
    assert resumen.candidates_approved == 2


def test_el_report_lleva_el_detalle_de_cada_clip() -> None:
    passed = report_module.ClipRecordResult(
        clip_id="c1", record=_record(), path=Path("work/clip_records/c1.json")
    )
    resumen = report_module.build_report(
        job_id="job1", source=None, duration=None, candidate_set=None,
        clip_records=[passed], processing_time_seconds=1.0,
    )
    clip = resumen.to_dict()["clips"][0]
    assert clip["clip_id"] == "c1"
    assert clip["passed"] is True
    assert clip["selection_score"] == 77.0
    assert clip["semantic_score"] == 85.0
    assert clip["record_path"].endswith("c1.json")


def test_el_report_se_escribe_y_se_valida(tmp_path: Path) -> None:
    resumen = report_module.build_report(
        job_id="job1", source="input/video.mp4", duration=1228.4,
        candidate_set=_candidate_set("c1"),
        clip_records=[report_module.ClipRecordResult(clip_id="c1", record=_record())],
        processing_time_seconds=42.24,
    )
    ruta = report_module.write_report(resumen, tmp_path / "report.json")
    leido = json.loads(ruta.read_text(encoding="utf-8"))
    assert leido["job_id"] == "job1"
    assert leido["clips_rendered"] == 1
    assert leido["processing_time_seconds"] == 42.2
    artifacts.validate("report", leido)


def test_el_resumen_para_consola_incluye_los_campos_de_la_seccion_56() -> None:
    resumen = report_module.build_report(
        job_id="job1", source="v.mp4", duration=10.0, candidate_set=None,
        clip_records=[], processing_time_seconds=3.0,
    )
    texto = resumen.summary()
    for campo in ("job", "fuente", "duracion", "candidatos", "clips", "score medio", "proceso"):
        assert campo in texto


# --- metadata --------------------------------------------------------------


def _seo_result() -> SEOResult:
    documento = {
        "title": "Escape Attempt",
        "description": "Un grupo se fuga.",
        "hashtags": ["escape_attempt"],
        "keywords": ["prison break"],
        "hook": "We got intel",
        "category": "true_crime",
    }
    return SEOResult(
        clip_id="c1",
        category="true_crime",
        platforms={name: dict(documento) for name in
                   ("youtube", "tiktok", "instagram", "generic")},
    )


def test_se_escriben_los_cuatro_ficheros_de_plataforma(tmp_path: Path) -> None:
    rutas = report_module.write_metadata_files(_seo_result(), tmp_path / "metadata")
    assert len(rutas) == 5, "cuatro plataformas mas el combinado"
    for plataforma in ("youtube", "tiktok", "instagram", "generic"):
        documento = json.loads((tmp_path / "metadata" / f"{plataforma}.json").read_text(encoding="utf-8"))
        assert documento["platform"] == plataforma
        assert documento["clip_id"] == "c1"
        artifacts.validate("platform_metadata", documento)


def test_cada_plataforma_es_un_fichero_autonomo(tmp_path: Path) -> None:
    """Cada fichero lleva su schema_version y su categoria: no depende del resto."""
    report_module.write_metadata_files(_seo_result(), tmp_path / "metadata")
    documento = json.loads((tmp_path / "metadata" / "tiktok.json").read_text(encoding="utf-8"))
    assert documento["schema_version"] == report_module.SCHEMA_VERSION
    assert documento["category"] == "true_crime"


def test_el_fichero_combinado_reune_las_cuatro(tmp_path: Path) -> None:
    report_module.write_metadata_files(_seo_result(), tmp_path / "metadata")
    combinado = json.loads((tmp_path / "metadata" / "metadata.json").read_text(encoding="utf-8"))
    assert sorted(combinado["platforms"]) == ["generic", "instagram", "tiktok", "youtube"]
    assert combinado["category"] == "true_crime"
    artifacts.validate("seo_metadata", combinado)


def test_los_directorios_de_metadata_se_crean(tmp_path: Path) -> None:
    destino = tmp_path / "a" / "b" / "metadata"
    report_module.write_metadata_files(_seo_result(), destino)
    assert destino.is_dir()
