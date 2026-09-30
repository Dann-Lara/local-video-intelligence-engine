"""Punto unico de entrada para validar un artefacto (secciones 60 y 63).

Aplica dos capas, en orden:

1. Schema      -> estructura: tipos, campos obligatorios, topes por campo.
2. Invariantes -> coherencia: start < end, total == suma de dimensiones, etc.

Un artefacto no se acepta, no se escribe en disco y no se pasa a la siguiente
etapa hasta que pasa las dos capas. "Nunca aceptar datos invalidos
silenciosamente" (seccion 59).
"""

from __future__ import annotations

from typing import Any

from . import invariants, schemas


class ContractViolation(Exception):
    """Artefacto que no cumple schema ni invariantes."""

    def __init__(
        self,
        name: str,
        schema_errors: list[str],
        invariant_errors: list[str],
        job_id: str | None = None,
        stage: str | None = None,
    ):
        self.schema_name = name
        self.schema_errors = schema_errors
        self.invariant_errors = invariant_errors
        self.job_id = job_id
        self.stage = stage
        context = " ".join(
            part for part in (f"job_id={job_id}" if job_id else "", f"stage={stage}" if stage else "") if part
        )
        prefix = f"[{context}] " if context else ""
        detail = "\n".join(f"  - {e}" for e in schema_errors + invariant_errors)
        super().__init__(f"{prefix}{name}: artefacto invalido\n{detail}")


def errors_for(name: str, document: Any) -> list[str]:
    """Todos los problemas del artefacto, sin lanzar excepcion."""
    problems = [f"[schema] {e}" for e in schemas.iter_errors(name, document)]
    problems += [f"[invariante] {e}" for e in invariants.invariant_errors(name, document)]
    return problems


def is_valid(name: str, document: Any) -> bool:
    return not errors_for(name, document)


def enforce(name: str, document: Any, *, job_id: str | None = None, stage: str | None = None) -> Any:
    """Valida y devuelve el documento. Levanta ContractViolation si falla."""
    schema_errors = schemas.iter_errors(name, document)
    if schema_errors:
        raise ContractViolation(name, schema_errors, [], job_id, stage)
    invariant_errors = invariants.invariant_errors(name, document)
    if invariant_errors:
        raise ContractViolation(name, [], invariant_errors, job_id, stage)
    return document
