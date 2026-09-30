"""Ranking Engine (blueprint secciones 20, 21, 22 y 23).

Es la etapa RANKING: de TODOS los candidatos descubiertos a los que se van a
rendir. Opera sobre el `candidate_set` ya validado (cada candidato completo,
con score normalizado en software por la etapa GENERATING_CANDIDATES) y aplica,
en este orden:

1. Umbral de score (seccion 21): `score.total >= minimum_score`. El score NO
   es la unica medida (seccion 21: "no usar solo el score"): este modulo lo
   compone con estructura y diversidad, no lo sustituye.
2. Solapamiento temporal (seccion 23): dos clips que cubren casi el mismo
   tramo son el mismo clip. Se descarta el de menor score.
3. Diversidad (seccion 22): misma `topic_signature` con solape no nulo, o
   `semantic_signature` demasiado similar, produce clips intercambiables.
   Se descarta el segundo.
4. Limite superior (seccion 23): nunca mas de `target_maximum`. "Tantos como
   existan, dentro de limites", nunca un numero exacto (seccion 18).
5. Orden determinista: score descendente, tiempo ascendente. Mismo conjunto de
   entrada + misma config = misma seleccion (seccion 2.4).

Este modulo NO toca el modelo ni la base de datos: es una operacion pura sobre
el documento, testeable sin red.
"""

from __future__ import annotations

from typing import Any

from src.core import config as config_module
from src.core.invariants import SCORE_DIMENSION_MAX

RANKING_CONFIG_KEYS = (
    "minimum_score",
    "target_minimum",
    "target_maximum",
    "diversity_threshold",
    "max_temporal_overlap",
)


def _defaults(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "minimum_score": float(config_module.get(config, "selection.minimum_score", 72.0)),
        "target_minimum": int(config_module.get(config, "selection.target_minimum", 3)),
        "target_maximum": int(config_module.get(config, "selection.target_maximum", 10)),
        "diversity_threshold": float(config_module.get(config, "selection.diversity_threshold", 0.80)),
        "max_temporal_overlap": float(config_module.get(config, "selection.max_temporal_overlap", 0.5)),
    }


def _total(candidate: dict[str, Any]) -> float:
    score = candidate.get("score") or {}
    total = score.get("total")
    return float(total) if isinstance(total, (int, float)) else 0.0


def _temporal_overlap_ratio(
    first_start: float, first_end: float, second_start: float, second_end: float
) -> float:
    """Solape real sobre la duracion del mas corto.

    Si un clip de 60 s y uno de 30 comparten 20 s, el de 30 esta copiando el
    67% de su contenido: se parece (y se solaparia en pantalla). El ratio sobre
    el mas corto es lo que mide eso.
    """
    overlap = max(0.0, min(first_end, second_end) - max(first_start, second_start))
    if overlap <= 0.0:
        return 0.0
    shortest = min(first_end - first_start, second_end - second_start)
    if shortest <= 0.0:
        return 0.0
    return overlap / shortest


def _token_set(text: str) -> set[str]:
    return {token for token in str(text or "").lower().split() if token}


def _signature_similarity(first: str, second: str) -> float:
    """Similitud de Jaccard entre firmas semanticas (seccion 22)."""
    a, b = _token_set(first), _token_set(second)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def select_candidates(
    document: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Aplica la seleccion sobre el conjunto y devuelve (selection, warnings).

    `selection` es el bloque que se escribe en `candidate_set.selection`:
    con los parametros usados, los `selected` en orden y los `dropped` con su
    motivo. Es una funcion pura: no muta `document`.
    """
    params = _defaults(config)
    candidates = [
        candidate for candidate in document.get("candidates", []) if isinstance(candidate, dict)
    ]
    ordered = sorted(
        candidates,
        key=lambda candidate: (-round(_total(candidate), 2), float(candidate["source"]["start"])),
    )

    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    warnings: list[str] = []

    for candidate in ordered:
        candidate_id = str(candidate.get("candidate_id"))
        start, end = float(candidate["source"]["start"]), float(candidate["source"]["end"])

        if _total(candidate) < params["minimum_score"]:
            dropped.append(
                {
                    "candidate_id": candidate_id,
                    "reason": f"score {_total(candidate):.1f} por debajo del minimo "
                    f"{params['minimum_score']:.0f} (seccion 21)",
                }
            )
            continue

        def _temporal_reason(kept: dict[str, Any]) -> float | None:
            ratio = _temporal_overlap_ratio(
                start, end, float(kept["source"]["start"]), float(kept["source"]["end"])
            )
            return ratio if ratio > params["max_temporal_overlap"] else None

        for kept in selected:
            ratio = _temporal_reason(kept)
            if ratio is not None:
                dropped.append(
                    {
                        "candidate_id": candidate_id,
                        "reason": f"se solapa {ratio:.0%} con {kept['candidate_id']} "
                        f"(max_temporal_overlap={params['max_temporal_overlap']:.0%})",
                    }
                )
                break
        else:
            signatures_next = candidate.get("signatures") or {}
            duplicate = False
            for kept in selected:
                signatures_kept = kept.get("signatures") or {}
                topic_next = signatures_next.get("topic_signature")
                topic_kept = signatures_kept.get("topic_signature")
                sharing_topic = (
                    isinstance(topic_next, str)
                    and bool(topic_next.strip())
                    and topic_next == topic_kept
                    and _temporal_overlap_ratio(
                        start, end, float(kept["source"]["start"]), float(kept["source"]["end"])
                    )
                    > 0.0
                )
                semantic_a = signatures_next.get("semantic_signature")
                semantic_b = signatures_kept.get("semantic_signature")
                semantic_close = (
                    isinstance(semantic_a, str)
                    and isinstance(semantic_b, str)
                    and _signature_similarity(semantic_a, semantic_b) >= params["diversity_threshold"]
                )
                if sharing_topic:
                    dropped.append(
                        {
                            "candidate_id": candidate_id,
                            "reason": f"mismo topic_signature que {kept['candidate_id']} con solape "
                            "de tiempo (seccion 22)",
                        }
                    )
                    duplicate = True
                    break
                if semantic_close:
                    dropped.append(
                        {
                            "candidate_id": candidate_id,
                            "reason": f"semantic_signature demasiado similar a {kept['candidate_id']} "
                            f"(seccion 22, Jaccard>={params['diversity_threshold']:.0%})",
                        }
                    )
                    duplicate = True
                    break
            if duplicate:
                continue

            if len(selected) >= params["target_maximum"]:
                dropped.append(
                    {
                        "candidate_id": candidate_id,
                        "reason": f"se supera target_maximum={params['target_maximum']} "
                        "(seccion 23: nunca mas, solo menos)",
                    }
                )
                continue

            selected.append(candidate)

    selected_ids = [str(candidate["candidate_id"]) for candidate in selected]
    if len(selected) < params["target_minimum"]:
        warnings.append(
            f"solo {len(selected)} candidato(s) seleccionado(s), por debajo de "
            f"target_minimum={params['target_minimum']} (seccion 23). "
            "El ranking no inventa clips que no existen."
        )
    if not selected_ids and document.get("candidates"):
        warnings.append(
            "ningun candidato supero la seleccion; revisa minimum_score o la calidad "
            "de los candidatos descubiertos (es preferible a emitir clips malos)."
        )

    return (
        {
            "minimum_score": params["minimum_score"],
            "max_temporal_overlap": params["max_temporal_overlap"],
            "diversity_threshold": params["diversity_threshold"],
            "target_minimum": params["target_minimum"],
            "target_maximum": params["target_maximum"],
            "selected": selected_ids,
            "dropped": dropped,
        },
        warnings,
    )


def dimension_max() -> dict[str, int]:
    """Cotas de la seccion 20, para que edit_plan/test las reutilicen."""
    return dict(SCORE_DIMENSION_MAX)