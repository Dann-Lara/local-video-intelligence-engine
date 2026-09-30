"""Carga y validacion de JSON Schemas (blueprint seccion 60).

Los schemas son el contrato entre modulos (seccion 77). Toda salida de IA y
todo artefacto persistente se valida contra un schema antes de aceptarse.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"

SCHEMA_FILES = {
    "transcript": "transcript.schema.json",
    "normalized_transcript": "normalized_transcript.schema.json",
    "content_map": "content_map.schema.json",
    "content_map_body": "content_map_body.schema.json",
    "candidate": "candidate.schema.json",
    "candidate_body": "candidate_body.schema.json",
    "candidate_set": "candidate_set.schema.json",
    "hook_extraction": "hook_extraction.schema.json",
    "edit_plan": "edit_plan.schema.json",
    "clip_record": "clip_record.schema.json",
    "performance_snapshot": "performance_snapshot.schema.json",
}


class SchemaNotFoundError(KeyError):
    """Nombre de schema desconocido."""


class SchemaInvalidError(Exception):
    """El schema en disco no es un JSON Schema valido."""


@lru_cache(maxsize=1)
def _registry() -> dict[str, dict[str, Any]]:
    if not SCHEMA_DIR.is_dir():
        raise SchemaInvalidError(f"No existe el directorio de schemas: {SCHEMA_DIR}")
    registry: dict[str, dict[str, Any]] = {}
    for name, filename in SCHEMA_FILES.items():
        path = SCHEMA_DIR / filename
        if not path.is_file():
            raise SchemaInvalidError(f"Schema faltante: {path}")
        document = json.loads(path.read_text(encoding="utf-8"))
        validator_class = validator_for(document)
        validator_class.check_schema(document)
        registry[name] = document
    return registry


@lru_cache(maxsize=1)
def _resource_registry() -> Registry:
    """Registro `referencing` para resolver $ref entre archivos.

    Cada schema se registra bajo su propio $id, de modo que las referencias
    relativas (p.ej. "content_map.schema.json#/$defs/model_run") resuelven
    contra la base correcta.
    """
    resources = [
        (document["$id"], Resource.from_contents(document))
        for document in _registry().values()
    ]
    return Registry().with_resources(resources)


@lru_cache(maxsize=len(SCHEMA_FILES))
def _store_for(name: str) -> Draft202012Validator:
    registry = _registry()
    if name not in registry:
        raise SchemaNotFoundError(
            f"Schema desconocido: {name!r}. Disponibles: {sorted(registry)}"
        )
    return Draft202012Validator(registry[name], registry=_resource_registry())


def load(name: str) -> dict[str, Any]:
    """Devuelve el documento de schema crudo."""
    return _registry()[name]


def validator(name: str) -> Draft202012Validator:
    """Devuelve un validador Draft 2020-12 con $ref resueltos entre archivos."""
    return _store_for(name)


def validate(name: str, instance: Any) -> None:
    """Valida una instancia. Levanta jsonschema.ValidationError si no cumple."""
    validator(name).validate(instance)


def iter_errors(name: str, instance: Any) -> list[str]:
    """Devuelve mensajes legibles de todos los errores, sin lanzar excepcion."""
    errors = []
    for error in sorted(validator(name).iter_errors(instance), key=lambda e: list(e.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def is_valid(name: str, instance: Any) -> bool:
    return not iter_errors(name, instance)


def available() -> list[str]:
    return sorted(_registry())


# --- Derivacion para envio al LLM -------------------------------------------
#
# LM Studio (llama.cpp) genera gramáticas a partir del JSON Schema y RECHAZA
# cualquier $ref con HTTP 400 "Invalid JSON Schema": necesita un documento
# autocontenido. Ejemplo real:
#
#   candidate.schema.json -> $ref "content_map.schema.json#/$defs/hook"
#
# Por eso se desreferencia antes de mandarlo. La validacion local sigue
# usando el schema con $ref (gracias al `referencing` Registry).


class UnresolvableRefError(Exception):
    """Un $ref no se puede resolver dentro del registro."""


def _split_ref(ref: str) -> tuple[str, str]:
    """'a.json#/$defs/x' -> ('a.json', '#/$defs/x')"""
    document, _, pointer = ref.partition("#")
    return document, pointer or ""


@lru_cache(maxsize=1)
def _by_filename() -> dict[str, str]:
    """'content_map.schema.json' -> 'content_map'"""
    return {filename: name for name, filename in SCHEMA_FILES.items()}


def _resolve_document(document_name: str, registry: dict[str, dict[str, Any]]) -> str:
    if document_name in registry:
        return document_name
    alias = _by_filename().get(document_name)
    if alias and alias in registry:
        return alias
    raise UnresolvableRefError(f"schema desconocido en $ref: {document_name}")


def _lookup_pointer(document: Any, pointer: str) -> Any:
    node = document
    for token in pointer.lstrip("#/").split("/"):
        if not token:
            continue
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise UnresolvableRefError(f"puntero {pointer!r} no aplicable")
    return node


def _deref(node: Any, registry: dict[str, dict[str, Any]], base: str, seen: frozenset[str]) -> Any:
    if isinstance(node, list):
        return [_deref(item, registry, base, seen) for item in node]

    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str):
            raise UnresolvableRefError(f"$ref no es string: {ref!r}")
        if ref in seen:
            raise UnresolvableRefError(f"ref recursivo sin base: {ref}")
        document_name, pointer = _split_ref(ref)
        if document_name:
            base_for_ref = _resolve_document(document_name, registry)
        else:
            base_for_ref = base
        target = _lookup_pointer(registry[base_for_ref], pointer)
        resolved = _deref(target, registry, base_for_ref, seen | {ref})
        # Las claves hermanas de $ref (title, description) se conservan.
        siblings = {k: _deref(v, registry, base, seen) for k, v in node.items() if k != "$ref"}
        if siblings:
            merged = dict(resolved) if isinstance(resolved, dict) else {}
            merged.update(siblings)
            return merged
        return resolved

    return {key: _deref(value, registry, base, seen) for key, value in node.items()}


def deref(name: str) -> dict[str, Any]:
    """Devuelve el schema con todos los $ref resueltos en linea.

    Es lo que se envia como `response_format.json_schema` a LM Studio.
    """
    registry = _registry()
    if name not in registry:
        raise SchemaNotFoundError(f"Schema desconocido: {name!r}")
    resolved = _deref(registry[name], registry, name, frozenset())
    resolved = {k: v for k, v in resolved.items() if k not in {"$schema", "$id"}}
    return resolved


def has_external_refs(name: str) -> bool:
    """True si el schema referencia a otro archivo (requiere desreferenciar)."""
    registry = _registry()
    if name not in registry:
        raise SchemaNotFoundError(f"Schema desconocido: {name!r}")

    def walk(node: Any) -> bool:
        if isinstance(node, list):
            return any(walk(item) for item in node)
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                document_name, _ = _split_ref(ref)
                if document_name and _resolve_document(document_name, registry):
                    return True
            return any(walk(value) for value in node.values())
        return False

    return walk(registry[name])
