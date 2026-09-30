"""Cliente de LM Studio (blueprint seccion 7).

Blueprint 2.3: la IA decide, el software ejecuta. Este cliente NUNCA ejecuta
comandos: solo habla HTTP con el servidor local de inferencia y devuelve texto
estructurado que el pipeline validara despues (seccion 60).

Diferencias con el blueprint que este modulo resuelve:

- El modelo del blueprint, ``google/gemma-3-4b``, no existe en LM Studio.
  Lo instalado es ``gemma-3-4b-it-Q4_K_M.gguf``. El id real se descubre
  consultando ``GET {base_url}/models`` y se puede fijar en configuracion
  para cumplir el determinismo de la seccion 2.4.
- LM Studio 0.4.x soporta ``response_format: json_schema`` via grammars de
  llama.cpp, pero no debe darse por sentado. Se prueba una vez y se degrada
  a JSON-por-prompt con validacion de schema (seccion 59).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_TIMEOUT = 30.0


class LLMError(RuntimeError):
    """Fallo del proveedor de LLM."""


class LLMRecoverableError(LLMError):
    """Reintentable: LM Studio caido, timeout, respuesta malformada (seccion 58)."""


class LLMFatalError(LLMError):
    """No reintentable: configuracion invalida, modelo inexistente."""


@dataclass
class HealthStatus:
    reachable: bool
    models: list[str] = field(default_factory=list)
    error: str | None = None
    latency_seconds: float | None = None
    server_version: str | None = None

    @property
    def ready(self) -> bool:
        return self.reachable and bool(self.models)


class LMStudioClient:
    """Cliente HTTP minimo y explicito. Sin estado global, sin singletons."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = "auto",
        model_match: str = "gemma",
        temperature: float = 0.1,
        max_tokens: int = 8192,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: str = "lm-studio",
    ):
        if not base_url:
            raise LLMFatalError("llm.base_url vacio")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.model_match = model_match.lower()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.api_key = api_key
        self._cached_models: list[str] | None = None

    # --- transporte ---

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    def health(self, *, refresh: bool = False) -> HealthStatus:
        """Comprueba el servidor y devuelve los ids de modelo disponibles.

        Nunca lanza: el proposito es que `doctor` pueda mostrar el estado.
        """
        if not refresh and self._cached_models:
            return HealthStatus(reachable=True, models=list(self._cached_models))
        started = time.perf_counter()
        try:
            with self._client() as client:
                response = client.get("/models", timeout=min(self.timeout, 10.0))
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # httpx.HTTPError, JSONDecodeError, OSError
            return HealthStatus(
                reachable=False,
                error=f"{type(exc).__name__}: {exc}",
                latency_seconds=time.perf_counter() - started,
            )

        models = [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]
        self._cached_models = models
        return HealthStatus(
            reachable=True,
            models=models,
            latency_seconds=time.perf_counter() - started,
        )

    def resolve_model(self) -> str:
        """Devuelve el id de modelo a usar. Lanza si no puede determinarlo."""
        if self.model and self.model != "auto":
            return self.model
        status = self.health()
        if not status.reachable:
            raise LLMRecoverableError(
                f"LM Studio no responde en {self.base_url} ({status.error}). "
                "Abre LM Studio > Developer > Start Server."
            )
        matches = [m for m in status.models if self.model_match in m.lower()]
        if not matches:
            raise LLMFatalError(
                f"Ningun modelo en {self.base_url} contiene '{self.model_match}'. "
                f"Disponibles: {status.models}. "
                f"Fija llm.model en config/default.yaml o ajusta llm.model_match."
            )
        # Un solo match -> sin ambigüedad. Varios -> el mas corto es el
        # identificador de la publicacion, no la ruta completa del GGUF.
        return sorted(matches, key=len)[0]

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        schema_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Una llamada de chat. Devuelve el contenido textual sin parsear.

        No valida ni repara JSON: eso es trabajo de la etapa que llama
        (seccion 59). Este modulo solo transporta.

        `schema_name` es la clave de src/core/schemas.py. Se desreferencia
        antes de enviarse porque llama.cpp rechaza los $ref.
        """
        model = self.resolve_model()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if schema_name is not None:
            from ..core.schemas import deref

            document = deref(schema_name)
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": document.get("title", schema_name),
                    "schema": document,
                    "strict": True,
                },
            }

        try:
            with self._client() as client:
                response = client.post("/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            # Adjuntar el codigo HTTP permite al llamador distinguir un 400
            # "Invalid JSON Schema" (degradar a JSON-por-prompt, seccion 59) de
            # un 503 "servidor saturado" (reintentar).
            error = LLMRecoverableError(
                f"LM Studio devolvio {exc.response.status_code}: {exc.response.text[:400]}"
            )
            error.status_code = exc.response.status_code  # type: ignore[attr-defined]
            raise error from exc
        except (httpx.HTTPError, OSError) as exc:
            raise LLMRecoverableError(
                f"Fallo de transporte contra {self.base_url}: {type(exc).__name__}: {exc}"
            ) from exc
        except ValueError as exc:
            raise LLMRecoverableError(f"Respuesta no JSON de LM Studio: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRecoverableError(
                f"Estructura de respuesta inesperada: {str(data)[:400]}"
            ) from exc
        if not isinstance(content, str):
            raise LLMRecoverableError(f"content no es texto: {type(content).__name__}")
        return content


def client_from_config(config: dict[str, Any]) -> LMStudioClient:
    """Construye el cliente desde la configuracion cargada (seccion 7)."""
    from ..core.config import get

    llm = config.get("llm", {}) or {}
    return LMStudioClient(
        base_url=str(get(config, "llm.base_url", "http://localhost:1234/v1")),
        model=str(get(config, "llm.model", "auto")),
        model_match=str(get(config, "llm.model_match", "gemma")),
        temperature=float(get(config, "llm.temperature", 0.1)),
        max_tokens=int(get(config, "llm.max_tokens", 8192)),
        timeout=float(get(config, "llm.timeout_seconds", 600)),
    )
