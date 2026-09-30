"""clip_record.json y report.json (blueprint secciones 37 y 56).

Este modulo cierra el pipeline: toma los clips ya renderizados y QC, escribe el
registro central de cada clip (seccion 37) y el informe del job (seccion 56).

Reglas que mandan (y de donde salen):

1. El clip_record es la fuente central de TODO lo que le pasa a un clip: de
   donde salio, como se eligio, con que modelo se genero, como se corto y como
   paso el QC (seccion 37). Sin este registro no hay forma de aprender de los
   resultados (seccion 36: primero recolectar datos).
2. report.json (seccion 56) resume el JOB: cuantos candidatos hubo, cuantos se
   aprobaron, cuantos clips se renderizaron y rechazaron, el score medio, el
   tiempo de proceso y los errores. Es el entregable que se mira de un vistazo.
3. Los scores que van al report son los de la etapa que decidio (seleccion), no
   los del QC semantico: el score de seleccion es comparable entre candidatos,
   el score semantico es un veredicto de publicacion. Se guardan los dos, pero
   `average_score` usa el de seleccion, que es el unico que tiene sentido
   promediar.
4. Todo lo que se escribe se valida contra su schema antes de aceptarse
   (seccion 60): si un clip_record no valida, se registra como error del report
   en vez de colarse.
5. Nada se inventa: cada campo sale de un artefacto real del workspace. Si un
   artefacto falta, el campo se queda en null y se anota en `errors`, nunca se
   rellena con un valor por defecto que parezca real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core import artifacts, logs
from src.core.invariants import SCORE_DIMENSION_MAX
from src.quality import seo as seo_module

LOGGER = logs.get_logger("report")

SCHEMA_VERSION = "1.0"


class ReportError(RuntimeError):
    """Fallo irrecuperable al construir el informe."""


@dataclass
class ClipRecordResult:
    """clip_record.json de un clip, ya validado."""

    clip_id: str
    record: dict[str, Any]
    path: Path | None = None

    def summary(self) -> str:
        qc_passed = self.record.get("qc", {}).get("passed")
        return (
            f"{self.clip_id}: record escrito "
            f"(qc={'ok' if qc_passed else 'fallo'})"
        )


@dataclass
class ReportSummary:
    """Resumen del job, el objeto que se serializa a report.json."""

    job_id: str
    source: str | None
    duration: float | None
    candidates_found: int
    candidates_approved: int
    clips_rendered: int
    clips_rejected: int
    average_score: float | None
    processing_time_seconds: float
    errors: list[str] = field(default_factory=list)
    clips: list[dict[str, Any]] = field(default_factory=list)
    records: list[ClipRecordResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """El JSON de la seccion 56, mas el detalle por clip.

        Los campos de la seccion 56 son los obligatorios. `clips` y `records`
        son un añadido: el informe de un vistazo es util, pero saber QUE clip
        fallo y por que tambien, y eso no cabe en un agregado.
        """
        return {
            "job_id": self.job_id,
            "source": self.source,
            "duration": self.duration,
            "candidates_found": self.candidates_found,
            "candidates_approved": self.candidates_approved,
            "clips_rendered": self.clips_rendered,
            "clips_rejected": self.clips_rejected,
            "average_score": self.average_score,
            "processing_time_seconds": self.processing_time_seconds,
            "errors": list(self.errors),
            "clips": list(self.clips),
        }

    def summary(self) -> str:
        lines = [
            f"job            {self.job_id}",
            f"fuente         {self.source or '?'}",
            f"duracion       {self.duration:.1f}s" if self.duration else "duracion       ?",
            f"candidatos     {self.candidates_found} encontrados, "
            f"{self.candidates_approved} aprobados",
            f"clips          {self.clips_rendered} renderizados, "
            f"{self.clips_rejected} rechazados",
            f"score medio    {self.average_score:.1f}" if self.average_score is not None else "score medio    ?",
            f"proceso        {self.processing_time_seconds:.1f}s",
        ]
        if self.errors:
            lines.append(f"errores        {len(self.errors)}")
            for error in self.errors:
                lines.append(f"  - {error}")
        return "\n".join(lines)


# --- clip_record (seccion 37) ---------------------------------------------


def build_clip_record(
    *,
    clip_id: str,
    job_id: str,
    plan: dict[str, Any],
    candidate: dict[str, Any],
    qc: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Construye el clip_record de un clip (seccion 37).

    `plan` es el edit_plan.json y `candidate` el candidato del que salio. Lo que
    no este en ninguno de los dos se deja en null, nunca se rellena con un valor
    inventado. `qc` es obligatorio en el schema, asi que si aun no se ha corrido
    se escribe un bloque explicito que dice eso, en vez de omitir la clave.
    """
    source = plan.get("source", {}) or {}
    output = plan.get("output", {}) or {}
    subtitles = plan.get("subtitles", {}) or {}
    crop = plan.get("crop", {}) or {}
    hook = plan.get("hook", {}) or {}
    content = candidate.get("content", {}) or {}
    score = candidate.get("score", {}) or {}
    generation = candidate.get("generated_by", {}) or {}

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "clip_id": clip_id,
        "job_id": job_id,
        "candidate_id": candidate.get("candidate_id"),
        "source": {
            "file": source.get("file"),
            "sha256": source_sha256,
            "start": source.get("start"),
            "end": source.get("end"),
            "duration": source.get("duration"),
        },
        "content": {
            "topic": content.get("topic"),
            "summary": content.get("summary"),
            "hook_type": hook.get("type") or "unknown",
            # El schema exige string, no null. "unknown" es un valor honesto
            # (esta en el enum del candidato) cuando el modelo no emito el campo:
            # inventar "explanation" seria mentir sobre lo que el clip es.
            "content_type": content.get("content_type") or "unknown",
            "hook_origin": hook.get("origin"),
            "requires_generated_hook": hook.get("requires_generated_hook"),
        },
        "selection": {
            "score": _score_of(candidate),
            "hook": score.get("hook"),
            "curiosity": score.get("curiosity"),
            "value": score.get("value"),
            "emotion": score.get("emotion"),
            "narrative": score.get("narrative"),
            "payoff": score.get("payoff"),
            "shareability": score.get("shareability"),
            "standalone": score.get("standalone"),
            "visual": score.get("visual"),
        },
        "editing": {
            "format": output.get("format"),
            "width": output.get("width"),
            "height": output.get("height"),
            "fps": output.get("fps"),
            "subtitles": bool(subtitles.get("enabled", False)),
            "crop_mode": crop.get("mode"),
            "renderer": subtitles.get("renderer"),
        },
        "generation": {
            "model": generation.get("model"),
            "provider": generation.get("provider"),
            "prompt_version": generation.get("prompt_version"),
        },
        "qc": qc if qc is not None else {"passed": False, "problems": ["QC no ejecutado"]},
        "performance": [],
    }

    if metadata is not None:
        record["metadata"] = metadata

    return record


# --- report del job (seccion 56) ------------------------------------------


def _score_of(candidate: dict[str, Any]) -> float | None:
    """Score de seleccion de un candidato, o None si no lo hay.

    El campo fiable es `score.total`, que es el que fijo la capa de seleccion
    (seccion 20: el total se recalcula en software, nunca se acepta el del
    modelo). Si no esta, se suman solo las nueve dimensiones conocidas: sumar
    todas las claves del dict incluiria `confidence` (0-1) y inflaria el total.
    """
    score = candidate.get("score")
    if not isinstance(score, dict):
        return None
    total = score.get("total")
    if isinstance(total, (int, float)):
        return float(total)
    values = [
        float(score[key])
        for key in SCORE_DIMENSION_MAX
        if isinstance(score.get(key), (int, float))
    ]
    return round(sum(values), 1) if values else None


def build_report(
    *,
    job_id: str,
    source: str | None,
    duration: float | None,
    candidate_set: dict[str, Any] | None,
    clip_records: list[ClipRecordResult],
    processing_time_seconds: float,
    errors: list[str] | None = None,
) -> ReportSummary:
    """Construye el resumen del job (seccion 56) a partir de sus clips.

    `candidate_set` es el clip_candidates.json con la seleccion del ranking. Si
    no esta, `candidates_found` se calcula a partir de los clips, que es peor
    pero mejor que inventar un cero.
    """
    error_list = list(errors or [])
    all_errors = list(error_list)

    selection = (candidate_set or {}).get("selection", {}) or {}
    selected_ids = list(selection.get("selected", []) or [])
    candidates = list((candidate_set or {}).get("candidates", []) or [])

    if candidate_set is not None:
        candidates_found = len(candidates)
        candidates_approved = len(selected_ids)
    else:
        candidates_found = len(clip_records)
        candidates_approved = len(clip_records)
        all_errors.append("no hay clip_candidates.json: los conteos de candidatos son aproximados")

    # Clips renderizados = los que tienen render y pasaron el QC. Rechazados = los
    # que se renderizaron pero no pasaron, mas los que no llegaron a renderizarse.
    clips_rendered = 0
    clips_rejected = 0
    clips: list[dict[str, Any]] = []
    scores: list[float] = []

    for result in clip_records:
        record = result.record
        qc = record.get("qc", {}) or {}
        passed = bool(qc.get("passed"))
        semantic = qc.get("semantic") or {}
        problems = list(qc.get("problems", []) or [])

        if passed:
            clips_rendered += 1
        else:
            clips_rejected += 1
            all_errors.append(
                f"{result.clip_id}: QC fallido" + (f" ({'; '.join(problems[:3])})" if problems else "")
            )

        # El score que se promedia es el de SELECCION (record["selection"]["score"]),
        # no el semantico: el de seleccion es comparable entre candidatos.
        total = (record.get("selection") or {}).get("score")
        total = float(total) if isinstance(total, (int, float)) else None
        if total is not None:
            scores.append(total)

        clips.append(
            {
                "clip_id": result.clip_id,
                "passed": passed,
                "selection_score": total,
                "semantic_score": semantic.get("score"),
                "problems": problems,
                "record_path": str(result.path) if result.path else None,
            }
        )

    average_score = round(sum(scores) / len(scores), 1) if scores else None

    return ReportSummary(
        job_id=job_id,
        source=source,
        duration=round(duration, 3) if duration is not None else None,
        candidates_found=candidates_found,
        candidates_approved=candidates_approved,
        clips_rendered=clips_rendered,
        clips_rejected=clips_rejected,
        average_score=average_score,
        processing_time_seconds=round(processing_time_seconds, 1),
        errors=all_errors,
        clips=clips,
        records=clip_records,
    )


def write_report(summary: ReportSummary, path: Path | str) -> Path:
    """Escribe report.json en disco, validado contra su schema. Devuelve la ruta."""
    target = Path(path)
    artifacts.write_json(target, summary.to_dict(), schema_name="report", overwrite=True)
    return target


def write_clip_record(result: ClipRecordResult, path: Path | str) -> Path:
    """Escribe un clip_record.json validandolo antes (seccion 60)."""
    target = Path(path)
    artifacts.write_json(target, result.record, schema_name="clip_record", overwrite=True)
    result.path = target
    return target


def write_metadata_files(
    seo_result: seo_module.SEOResult,
    directory: Path | str,
) -> list[Path]:
    """Escribe `metadata/<plataforma>.json` (seccion 35).

    Cada plataforma en su fichero, mas un `metadata.json` con las cuatro, para
    que quien automation lea el conjunto de un vistazo sin abrir cuatro ficheros.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for platform, document in seo_result.files().items():
        target = root / f"{platform}.json"
        artifacts.write_json(
            target,
            {
                "schema_version": SCHEMA_VERSION,
                "clip_id": seo_result.clip_id,
                "platform": platform,
                **document,
            },
            schema_name="platform_metadata",
            overwrite=True,
        )
        written.append(target)

    # El fichero combinado es el mismo cuerpo que valida seo_metadata
    # (platforms + category), para que un unico schema cubra lo que sale del
    # modelo y lo que se guarda junto.
    combined = root / "metadata.json"
    artifacts.write_json(
        combined,
        {
            "platforms": seo_result.files(),
            "category": seo_result.category,
        },
        schema_name="seo_metadata",
        overwrite=True,
    )
    written.append(combined)
    return written


__all__ = [
    "ClipRecordResult",
    "ReportError",
    "ReportSummary",
    "SCHEMA_VERSION",
    "build_clip_record",
    "build_report",
    "write_clip_record",
    "write_metadata_files",
    "write_report",
]
