"""Carga de configuracion (blueprint seccion 52).

Prioridad, de mayor a menor:

1. Variables de entorno con prefijo ``LVI_`` (``LVI_LLM__BASE_URL`` -> llm.base_url)
2. ``config/local.yaml`` (override local, no versionado)
3. ``config/default.yaml``

La ruta del proyecto contiene un espacio y un acento (riesgo C-1), asi que
toda lectura/escritura aqui es UTF-8 explicita.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ENV_PREFIX = "LVI_"
ENV_SEPARATOR = "__"
CONFIG_FILENAME = "default.yaml"
LOCAL_CONFIG_FILENAME = "local.yaml"
LOCAL_CONFIG_TEMPLATE = """# Override local. No se versiona.
# Copia aqui solo lo que quieras cambiar respecto a default.yaml.
#
# llm:
#   base_url: http://192.168.1.50:1234/v1
#   model: lmstudio-community/gemma-3-4b-it-GGUF/gemma-3-4b-it-Q4_K_M.gguf
#
# whisper:
#   device: cpu
"""


class ConfigError(RuntimeError):
    """La configuracion no se pudo cargar o es incoherente."""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", ""}:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.startswith(("[", "{")):
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError:
            return raw
    return raw


def _set_in(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    # Las variables de entorno llegan en MAYUSCULAS (`LVI_PATHS__LOGS_ROOT`) y
    # las claves del YAML van en minusculas (`paths.logs_root`). Sin bajar a
    # minusculas, el override no sobrescribia nada: creaba claves nuevas
    # (`PATHS` -> `LOGS_ROOT`) y el override pasaba desapercibido.
    parts = [part.lower() for part in dotted_key.split(ENV_SEPARATOR)]
    node = target
    for part in parts[:-1]:
        existing = node.get(part)
        if not isinstance(existing, dict):
            existing = {}
            node[part] = existing
        node = existing
    node[parts[-1]] = value


def apply_env_overrides(config: dict[str, Any], environ: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    overrides = {
        key[len(ENV_PREFIX) :]: _coerce(value)
        for key, value in env.items()
        if key.startswith(ENV_PREFIX) and len(key) > len(ENV_PREFIX)
    }
    result = dict(config)
    for dotted_key, value in overrides.items():
        _set_in(result, dotted_key, value)
    return result


def load(
    project_root: Path | None = None,
    *,
    use_env: bool = True,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Carga la configuracion combinada."""
    root = (project_root or default_project_root()).resolve()
    if use_env:
        load_dotenv(root / ".env", override=False)

    config_dir = root / "config"
    default_path = config_dir / CONFIG_FILENAME
    if not default_path.is_file():
        raise ConfigError(f"No existe {default_path}")

    config = yaml.safe_load(default_path.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise ConfigError(f"{default_path} no contiene un mapeo YAML")

    local_path = config_dir / LOCAL_CONFIG_FILENAME
    if local_path.is_file():
        local = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        if isinstance(local, dict):
            config = _deep_merge(config, local)

    if use_env:
        config = apply_env_overrides(config, environ)

    config["_project_root"] = str(root)
    return config


def resolve_path(config: dict[str, Any], key: str, project_root: Path | None = None) -> Path:
    """Resuelve una ruta de `paths:` contra la raiz del proyecto."""
    root = (project_root or Path(config["_project_root"])).resolve()
    value = config.get("paths", {}).get(key)
    if not value:
        raise ConfigError(f"paths.{key} no esta definido")
    candidate = Path(str(value)).expanduser()
    return candidate if candidate.is_absolute() else (root / candidate)


def project_root(config: dict[str, Any]) -> Path:
    """Raiz del proyecto, tal y como la fijo `load()`.

    Para rutas que NO viven bajo `paths:` (el log, la base de datos) hay que
    resolverlas contra la raiz a mano; `resolve_path` solo acepta claves de
    `paths:` y fallaria con una ruta con puntos.
    """
    return Path(config["_project_root"]).resolve()


def resolve_relative(config: dict[str, Any], dotted_key: str, default: str) -> Path:
    """Resuelve el valor de una clave con puntos contra la raiz del proyecto."""
    value = get(config, dotted_key, default)
    candidate = Path(str(value)).expanduser()
    return candidate if candidate.is_absolute() else (project_root(config) / candidate)


def default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def write_local_template(project_root: Path | None = None) -> Path:
    """Crea config/local.yaml a partir de la plantilla si no existe."""
    root = project_root or default_project_root()
    target = root / "config" / LOCAL_CONFIG_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(LOCAL_CONFIG_TEMPLATE, encoding="utf-8")
    return target


def get(config: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    """Acceso por ruta con puntos: get(cfg, 'llm.base_url')."""
    node: Any = config
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
