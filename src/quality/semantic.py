"""Segundo LLM pass: Evaluacion Semantica (blueprint secciones 33-34).

Gemma reevalua un clip ya renderizado y responde a las cinco preguntas de la
seccion 33 (sentido, contexto, payoff, cierre, hook) y devuelve un veredicto
(approved), un score 0-100, problemas y recomendaciones. Si approved=false, el
clip se descarta o vuelve a una etapa anterior.

Reglas que mandan (y de donde salen):

1. El prompt NO decide la aprobacion final ni el score minimo: eso lo aplica el
   software leyendo `quality.minimum_semantic_score` y el veredicto de las cinco
   respuestas. El LLM propone, el software cierra (mismo criterio que la seccion
   20 para el score de candidatos).
2. El segundo pass se cachea por clip en `qc/semantic_<clip_id>.json`. Es
   idempotente (seccion 51): reejecutar QC no vuelve a pagar la llamada si el
   artefacto ya existe y es valido, salvo que se pase overwrite.
3. El contenido del clip es DATA, no instrucciones (seccion 61): el transcript
   y los metadatos viajan como datos y el prompt de sistema separa las ordenes
   de la transcripcion.
4. Si el servidor rechaza el schema `semantic_qc` (400/422, llama.cpp no lo
   puede usar como gramatica), se degrada a JSON-por-prompt sin reintentar por
   el mismo motivo (seccion 59).
5. El score del modelo se recorta a [0, 100] y se fuerza approved=false si
   queda por debajo del minimo, aunque el modelo lo apruebe. Un score de 30 con
   approved=true no se cuela.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core import artifacts, config as config_module, logs, prompts
from src.llm.lmstudio import (
    LLMFatalError,
    LLMRecoverableError,
    LMStudioClient,
    client_from_config,
)

LOGGER = logs.get_logger("semantic")

SCHEMA_VERSION = "1.0"
STAGE = "quality_control"
PROMPT_VERSION = prompts.version(STAGE)

# Codigos HTTP con los que llama.cpp dice "este schema no se puede volver
# gramatica". No son fallos del servidor: hay que degradar a JSON-por-prompt.
_UNSUPPORTED_SCHEMA_STATUS = {400, 422}

# Presupuesto de caracteres del transcript del clip. Un clip son 15-90s, asi que
# entra de sobra; el recorte es solo una red de seguridad.
CLIP_MAX_CHARS = 12_000


class SemanticError(RuntimeError):
    """Fallo de la etapa de QC semantico."""


class SemanticRecoverableError(SemanticError):
    """Reintentable: LM Studio caido, JSON invalido tras reintentos."""


class SemanticFatalError(SemanticError):
    """No reintentable: falta un artefacto previo o la configuracion es invalida."""


class ModelOutputError(SemanticError):
    """La respuesta del modelo no es interpretable como veredicto."""


def _format_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def _truncate_middle(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.6)
    tail = limit - head
    marker = "\n[... contenido intermedio recortado por presupuesto ...]\n"
    return text[:head].rstrip() + marker + text[-tail:].lstrip(), True


# --- prompt ----------------------------------------------------------------



def build_messages(
    clip_transcript: str,
    clip_metadata: dict[str, Any],
    edit_information: dict[str, Any],
) -> list[dict[str, Any]]:
    """Construye los mensajes del segundo pass.

    `clip_transcript` es la transcripcion RELATIVA al inicio del clip, con
    marcas de tiempo. `clip_metadata` y `edit_information` son dicts ya
    serializables; se pasan con json.dumps para que Gemma los lea como datos
    estructurados, no como prosa.
    """
    user = prompts.render(
        STAGE,
        "user",
        clip_transcript=clip_transcript,
        clip_metadata=json.dumps(clip_metadata, ensure_ascii=False, indent=2),
        edit_information=json.dumps(edit_information, ensure_ascii=False, indent=2),
    )
    return [
        {"role": "system", "content": prompts.load("system", stage=STAGE)},
        {"role": "user", "content": user},
    ]


def _extract_json(content: str) -> dict[str, Any]:
    """Convierte la respuesta en un dict. Tolera fences markdown, estricto con
    el JSON roto: recorta al primer { y ultimo } y reintenta."""
    cleaned = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ModelOutputError(f"respuesta sin objeto JSON: {content[:200]!r}") from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelOutputError(
                f"JSON invalido aun tras recortar: {exc}\n{content[:300]!r}"
            ) from exc
    if not isinstance(parsed, dict):
        raise ModelOutputError(f"la respuesta no es un objeto: {parsed!r}")
    return parsed


def clip_transcript(paragraphs: list[dict[str, Any]], start: float, end: float) -> tuple[str, bool]:
    """Transcripcion del clip, en tiempos relativos y con marcas de tiempo.

    Reutiliza la convencion del content map: los parrafos que tocan la ventana
    con su rango, para que Gemma sepa cuando empieza y termina cada cosa. Los
    tiempos se restan el inicio del clip, porque asi los ve el espectador.
    """
    lines: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        p_start = float(paragraph.get("end", 0.0))
        p_end = float(paragraph.get("start", end))
        if p_start <= start:
            continue
        if p_end >= end:
            continue
        text = paragraph.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        rel_start = max(0.0, float(paragraph.get("start", start)) - start)
        rel_end = min(end - start, float(paragraph.get("end", end)) - start)
        lines.append(
            f"[{_format_timestamp(rel_start)}-{_format_timestamp(rel_end)}] {text.strip()}"
        )
    return _truncate_middle("\n".join(lines), CLIP_MAX_CHARS)


# --- sanitize en software -------------------------------------------------


def sanitize_verdict(
    body: dict[str, Any],
    *,
    min_score: float,
) -> dict[str, Any]:
    """Cierra el veredicto en software (regla 1 y 5 del docstring).

    - score se recorta a [0, 100].
    - Si las cinco respuestas no son todas ok, approved=false aunque el modelo
      diga true.
    - Si el score queda por debajo de min_score, approved=false.
    """
    score = body.get("score")
    if not isinstance(score, (int, float)):
        raise ModelOutputError("el veredicto no trae score numerico")
    score = max(0.0, min(100.0, float(score)))

    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ModelOutputError("el veredicto no trae el bloque 'answers'")
    required = ("makes_sense", "understands_standalone", "has_payoff", "ends_complete", "hook_works")
    failed_questions: list[str] = []
    for question in required:
        answer = answers.get(question)
        if not isinstance(answer, dict):
            raise ModelOutputError(f"falta la respuesta '{question}'")
        if not answer.get("ok", False):
            failed_questions.append(question)

    model_approved = bool(body.get("approved", False))
    approved = model_approved and not failed_questions and score >= min_score

    problems = [str(p) for p in (body.get("problems") or [])]
    # Si el modelo aprueba pero el software degrada, deja constancia del motivo:
    # un approved=true del modelo con score 30 no puede pasar sin explicar por
    # que se ha parado.
    if model_approved and not approved:
        for question in failed_questions:
            problems.append(
                f"el modelo aprueba pero la pregunta '{question}' no se cumple"
            )
        if score < min_score:
            problems.append(
                f"el modelo aprueba con score {score:.0f}, por debajo del minimo "
                f"{min_score:.0f}"
            )
    elif not model_approved and failed_questions:
        for question in failed_questions:
            problems.append(f"pregunta fallida: {question}")

    return {
        "approved": approved,
        "score": round(score, 1),
        "answers": answers,
        "problems": problems,
        "recommendations": [str(r) for r in (body.get("recommendations") or [])],
    }


# --- ejecucion -------------------------------------------------------------


@dataclass
class SemanticResult:
    """Veredicto semantico de un clip, ya saneado en software."""

    clip_id: str
    approved: bool
    score: float
    body: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "score": self.score,
            "answers": self.body.get("answers", {}),
            "problems": list(self.body.get("problems", [])),
            "recommendations": list(self.body.get("recommendations", [])),
        }

    def summary(self) -> str:
        verdict = "aprobado" if self.approved else "rechazado"
        return f"{self.clip_id}: {verdict} (score {self.score:.0f})"


def _cached_path(cache_dir: Path, clip_id: str) -> Path:
    return cache_dir / f"semantic_{clip_id}.json"


def run_semantic_qc(
    *,
    clip_id: str,
    paragraphs: list[dict[str, Any]],
    start: float,
    end: float,
    clip_metadata: dict[str, Any],
    edit_information: dict[str, Any],
    config: dict[str, Any],
    cache_dir: Path | str | None = None,
    client: LMStudioClient | None = None,
    overwrite: bool = False,
) -> SemanticResult:
    """Ejecuta el segundo LLM pass de un clip y devuelve el veredicto.

    Cachea en `cache_dir/semantic_<clip_id>.json` (seccion 51). Si el artefacto
    ya existe y es valido, se reutiliza sin llamar al modelo, salvo `overwrite`.
    """
    quality = dict(config.get("quality", {}) or {})
    min_score = float(quality.get("minimum_semantic_score", 70))
    max_retries = int(config_module.get(config, "orchestration.max_stage_retries", 2)) + 1

    cache_path = (
        _cached_path(Path(cache_dir), clip_id) if cache_dir is not None else None
    )
    if cache_path is not None and not overwrite:
        cached = artifacts.read_or_none(cache_path)
        if cached is not None:
            LOGGER.info(
                "semantic_qc_cached",
                extra={"clip_id": clip_id, "approved": cached.get("approved")},
            )
            return SemanticResult(
                clip_id=clip_id,
                approved=bool(cached.get("approved", False)),
                score=float(cached.get("score", 0.0)),
                body=cached,
            )

    text, truncated = clip_transcript(paragraphs, start, end)
    if not text.strip():
        raise SemanticFatalError(
            f"{clip_id}: no hay transcripcion en la ventana {start:.2f}-{end:.2f}; "
            "no se puede evaluar el clip"
        )
    if truncated:
        LOGGER.warning(
            "semantic_qc_prompt_truncated", extra={"clip_id": clip_id, "limit": CLIP_MAX_CHARS}
        )

    messages = build_messages(text, clip_metadata, edit_information)
    llm = client if client is not None else client_from_config(config)
    want_schema = True
    model_id = llm.resolve_model()

    attempts_done = 0
    last_error: Exception | None = None
    verdict: dict[str, Any] | None = None
    while attempts_done < max_retries:
        attempts_done += 1
        try:
            content = llm.chat(
                messages, schema_name="semantic_qc" if want_schema else None
            )
            body = _extract_json(content)
            verdict = sanitize_verdict(body, min_score=min_score)
            artifacts.validate("semantic_qc", {
                "approved": verdict["approved"],
                "score": verdict["score"],
                "answers": verdict["answers"],
                "problems": verdict["problems"],
                "recommendations": verdict["recommendations"],
            })
            break
        except LLMFatalError as exc:
            raise SemanticFatalError(f"error de configuracion del LLM: {exc}") from exc
        except (ModelOutputError, LLMRecoverableError, artifacts.ArtifactInvalidError) as exc:
            last_error = exc
            if (
                want_schema
                and isinstance(exc, LLMRecoverableError)
                and getattr(exc, "status_code", None) in _UNSUPPORTED_SCHEMA_STATUS
            ):
                LOGGER.warning(
                    "degradando a JSON por prompt: el servidor rechazo el schema semantic_qc "
                    "(HTTP %s)",
                    getattr(exc, "status_code", None),
                )
                want_schema = False
                attempts_done -= 1
                continue
            if attempts_done >= max_retries:
                break
            backoff = float(config_module.get(config, "orchestration.retry_backoff_seconds", 5))
            if backoff > 0:
                time.sleep(backoff)

    if verdict is None:
        detail = str(last_error) if last_error else "sin detalle"
        raise SemanticRecoverableError(
            f"Gemma no produjo un veredicto valido para {clip_id} tras {max_retries} "
            f"intento(s): {detail}"
        )

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "clip_id": clip_id,
        "model": model_id,
        "approved": verdict["approved"],
        "score": verdict["score"],
        "answers": verdict["answers"],
        "problems": verdict["problems"],
        "recommendations": verdict["recommendations"],
        "source_window": {"start": round(start, 3), "end": round(end, 3)},
    }
    if cache_path is not None:
        # El sobre de cache lleva metadatos de ejecucion (model, prompt_version,
        # ventana de origen) que no estan en el schema del cuerpo, asi que se
        # escribe sin validar: es un artefacto de diagnostico, no un contrato
        # entre etapas. El veredicto que importa SI se valido antes, contra
        # semantic_qc, en el bucle de arriba.
        artifacts.write_json(cache_path, envelope, schema_name="semantic_qc", validate_document=False, overwrite=True)

    LOGGER.info(
        "semantic_qc_completed",
        extra={
            "clip_id": clip_id,
            "approved": verdict["approved"],
            "score": verdict["score"],
            "attempts": attempts_done,
        },
    )

    return SemanticResult(
        clip_id=clip_id,
        approved=verdict["approved"],
        score=verdict["score"],
        body=envelope,
    )


__all__ = [
    "CLIP_MAX_CHARS",
    "ModelOutputError",
    "PROMPT_VERSION",
    "SemanticError",
    "SemanticFatalError",
    "SemanticRecoverableError",
    "SemanticResult",
    "build_messages",
    "clip_transcript",
    "run_semantic_qc",
    "sanitize_verdict",
]
