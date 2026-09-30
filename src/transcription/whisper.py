"""Transcript engine (blueprint seccion 14, y el orden de fases de la 67).

Produce `transcript.json`, la salida cruda e inmutable de Whisper. Reglas que no
se negocian:

* Los timestamps son absolutos respecto al inicio del VIDEO original, no del
  audio extraido. Con VAD activo, faster-whisper recorta los tramos silenciosos
  internamente y despues los restituye (`restore_speech_timestamps`); si se
  guardara el tiempo recortado, todos los cortes de las etapas siguientes
  saldrian desplazados.
* Se conservan los timestamps por palabra si el backend los da. No se
  descarta informacion temporal: el recorte vertical necesita cortar entre
  palabras, no entre frases.
* El transcript original NUNCA se modifica. La normalizacion de la seccion 15
  va a un segundo fichero.

Sobre el fallo que cuesta mas tiempo de diagnostico de todo este modulo:

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

Sale al DECODIFICAR el primer segmento, no al cargar el modelo. Es decir,
`model.transcribe(...)` devuelve un generador sin error y la excepcion aparece
despues, al consumirlo. Un `try/except` alrededor de la llamada no captura
nada. Por eso aqui la iteracion va dentro del bloque protegido, y
`_classify` convierte ese RuntimeError en un mensaje que dice que instalar.

Segundo detalle relacionado: `WhisperModel` tarda 4-8 s en cargar y ocupa ~1.1
GB de VRAM. Se cachea a nivel de modulo para no recargarlo entre videos del
mismo lote, y `release()` lo suelta de forma explicita antes de que Gemma tome
la GPU (seccion 67: Whisper y Gemma no caben a la vez en 8 GB).
"""

from __future__ import annotations

import gc
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.core import artifacts, config as config_module, cuda, gpu, logs, schemas

LOGGER = logs.get_logger("transcription")

SCHEMA_VERSION = "1.0"
PROVIDER = "faster_whisper"

# Idioma cuando la deteccion no es concluyente (seccion 62: no alucinar).
UNKNOWN_LANGUAGE = "unknown"

# Tolerancia antes de avisar de que un segmento se pasa de la duracion del
# video. Whisper redondea y el VAD restituye consmallos desfases; 1 s es ruido.
DURATION_OVERSHOOT_TOLERANCE = 1.0

# Minimo de caracteres para que un segmento cuente como voz. Whisper emite
# segmentos casi vacios en los bordes del audio con musica o ruido de fondo.
MIN_SEGMENT_CHARS = 1


class TranscriptionError(RuntimeError):
    """La transcripcion no puede completarse."""

    def __init__(self, message: str, *, code: str = "transcription_failed", retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


# --- Carga perezosa ----------------------------------------------------------

# v2 importaba `faster_whisper` al inicio del modulo, lo que arrastraba
# ctranslate2 y torch en cada arranque de la CLI, incluido `doctor` y `analyze`
# de un fichero que ya estaba transcrito. Ahora se importa la primera vez que
# hace falta, y siempre despues de `ensure_cuda_dlls()`.
_MODELS: dict[tuple[str, str, str], Any] = {}
_MODEL_CACHE_HIT = 0
_MODEL_CACHE_MISS = 0


def _import_faster_whisper() -> Any:
    """Importa faster-whisper con el runtime de CUDA en su sitio.

    El orden es obligatorio: `ensure_cuda_dlls()` tiene que ejecutarse ANTES del
    import, porque la importacion de ctranslate2 es la que resuelve y cachea las
    DLLs.
    """
    cuda.ensure_cuda_dlls()
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            f"faster-whisper no esta instalado: {exc}. "
            "Instala las dependencias con: uv pip install -r requirements.txt",
            code="missing_dependency",
        ) from exc
    return WhisperModel


# Tipos de computo que ctranslate2 NO implementa en CPU. Son exclusivamente de
# GPU: en CPU existen `int8` y `float32`, pero no las variantes hibridas con
# `_float16`. Pedirlas en CPU no da un aviso, revienta al construir el modelo:
#     ValueError: Requested int8_float16 compute type, but the target device or
#     backend do not support efficient int8_float16 computation.
_GPU_ONLY_COMPUTE_TYPES = {"int8_float16", "float16", "int8_bfloat16", "bfloat16"}


def resolve_compute_type(config: dict[str, Any], device: str) -> str:
    """Devuelve el tipo de computo que el dispositivo soporta de verdad.

    Sin esto, poner `device: cpu` en la configuracion no basta: el
    `compute_type` por defecto (`int8_float16`) es de GPU y el proceso muere al
    cargar el modelo. Es un callejon sin salida, porque el propio mensaje de
    error de VRAM agotada sugiere `device: cpu` como salida, y seguir ese
    consejo rompia igual.

    El valor de la configuracion manda sobre el dispositivo: si alguien escribe
    `float32` para CPU a proposito, se respeta. Solo se corrige lo que el
    dispositivo no puede ejecutar.
    """
    configured = str(config_module.get(config, "whisper.compute_type", "int8_float16"))
    if device == "cpu" and configured in _GPU_ONLY_COMPUTE_TYPES:
        return "int8" if configured.startswith("int8") else "float32"
    return configured


def load_model(config: dict[str, Any]) -> Any:
    """Carga (o reutiliza del cache) el modelo de faster-whisper."""
    global _MODEL_CACHE_HIT, _MODEL_CACHE_MISS

    model_name = str(config_module.get(config, "whisper.model", "large-v3-turbo"))
    device = str(config_module.get(config, "whisper.device", "cuda"))
    compute_type = resolve_compute_type(config, device)

    key = (model_name, device, compute_type)
    cached = _MODELS.get(key)
    if cached is not None:
        _MODEL_CACHE_HIT += 1
        LOGGER.debug("reutilizando modelo en cache", extra={"model": model_name, "device": device})
        return cached

    _MODEL_CACHE_MISS += 1
    if device == "cuda":
        report = cuda.preflight("cuda")
        if not report.is_usable:
            raise TranscriptionError(
                "Whisper esta configurado con device=cuda pero el runtime de CUDA no esta "
                f"completo: {report.summary()}. {report.hint()} "
                "O cambia 'whisper.device' a 'cpu' en config (mas lento, pero funciona).",
                code="cuda_runtime_missing",
            )

    WhisperModel = _import_faster_whisper()
    LOGGER.info(
        "cargando modelo de transcripcion",
        extra={"model": model_name, "device": device, "compute_type": compute_type},
    )
    started = time.perf_counter()
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except (RuntimeError, OSError) as exc:
        raise _classify(exc, model=model_name, device=device) from exc

    elapsed = time.perf_counter() - started
    LOGGER.info(
        "modelo cargado",
        extra={"model": model_name, "device": device, "load_seconds": round(elapsed, 2)},
    )
    _MODELS[key] = model
    return model


def release() -> None:
    """Suelta los modelos y la VRAM que sostienen.

    Se llama entre la transcripcion y el analisis: en 8 GB, los ~1.1 GB de pesos
    de Whisper decide si Gemma entra o no (seccion 67).
    """
    global _MODELS
    count = len(_MODELS)
    _MODELS.clear()
    gc.collect()
    if count:
        LOGGER.info("modelos de transcripcion liberados", extra={"models_released": count})


def model_cache_stats() -> dict[str, int]:
    return {
        "loaded": len(_MODELS),
        "hits": _MODEL_CACHE_HIT,
        "misses": _MODEL_CACHE_MISS,
    }


# --- Errores -----------------------------------------------------------------


def _classify(exc: BaseException, *, model: str, device: str) -> TranscriptionError:
    """Convierte una excepcion del backend en un error accionable.

    Se busca por texto, no por tipo: ctranslate2 lanza `RuntimeError` plano tanto
    para una DLL que falta como para un OOM, y solo el mensaje los distingue.
    """
    text = str(exc)
    lowered = text.lower()

    if "cublas" in lowered or "cudnn" in lowered or "cannot be loaded" in lowered:
        # El hint NO se pide a `cuda.preflight()`: si las DLLs ya estan
        # instaladas, el preflight dice que todo va bien y el mensaje acabaria
        # diciendo "instala nvidia-cublas-cu12" sin dar ninguna pista de por que
        # sigue fallando. Aqui la causa casi siempre es un PATH que no se ha
        # ampliado en ESTE proceso, o una version de CUDA incompatible.
        return TranscriptionError(
            f"faltan las DLLs de CUDA para faster-whisper en '{device}': {text}. "
            "Comprueba dos cosas, en este orden: (1) que esten instaladas las ruedas "
            "'nvidia-cublas-cu12' y 'nvidia-cudnn-cu12' (uv pip install nvidia-cublas-cu12 "
            "nvidia-cudnn-cu12); (2) que 'cuda.ensure_cuda_dlls()' se haya ejecutado ANTES de "
            "importar faster_whisper, porque ctranslate2 solo busca en el PATH del proceso. "
            "Este fallo aparece al decodificar el primer segmento, despues de que el modelo ya "
            "haya cargado, asi que parece un fallo de memoria y no lo es.",
            code="cuda_runtime_missing",
        )

    if "out of memory" in lowered or "cuda_error_out_of_memory" in lowered or "not enough memory" in lowered:
        return TranscriptionError(
            f"la GPU se queda sin memoria con el modelo '{model}': {text}. "
            "Cierra el navegador o los juegos, baja 'whisper.compute_type' a 'float16' "
            "o transcribe en 'cpu'.",
            code="vram_oom",
            retryable=True,
        )

    if "no such file" in lowered or "not found" in lowered or "does not appear" in lowered:
        return TranscriptionError(
            f"Whisper no encuentra el modelo '{model}': {text}. "
            "Comprueba la conexion o descarga el modelo antes de desconectarte.",
            code="model_unavailable",
            retryable=True,
        )

    return TranscriptionError(
        f"faster-whisper fallo con el modelo '{model}': {text}",
        code="backend_error",
        retryable=True,
    )


# --- Saneo -------------------------------------------------------------------

def _clean_text(value: str) -> str:
    """Quita el espacio inicial que faster-whisper pone en cada segmento.

    Se quita aqui y no en la normalizacion porque un `lstrip` no destruye
    informacion temporal ni altera el significado: es la forma del texto, no su
    contenido.
    """
    return value.strip()


def _sanitize_time(value: Any) -> float:
    """Redondea a 3 decimales y garantiza un float no negativo y finito.

    Whisper puede devolver `-0.0`, que serializa como `-0.0` y es valido para el
    schema (`minimum: 0.0` lo acepta), pero rompe las comparaciones de la
    normalizacion.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return 0.0
    return round(max(number, 0.0), 3)


def _build_word(raw: Any) -> dict[str, Any] | None:
    text = _clean_text(getattr(raw, "word", "") or "")
    if not text:
        return None
    word: dict[str, Any] = {
        "word": text,
        "start": _sanitize_time(getattr(raw, "start", 0.0)),
        "end": _sanitize_time(getattr(raw, "end", 0.0)),
    }
    probability = getattr(raw, "probability", None)
    # `null` y no 0.0: la seccion 38 prohibe usar 0 como sustituto de "desconocido".
    word["probability"] = (
        round(float(probability), 4) if isinstance(probability, (int, float)) else None
    )
    if word["end"] < word["start"]:
        word["end"] = word["start"]
    # Una palabra con start == end es un artefacto del backend (Whisper emite
    # algun token sin duracion, tipicamente relleno o silencio). No aporta
    # alineacion temporal y el invariante del transcript exige start < end.
    # Fabricar tiempos (end = start + epsilon) seria inventar datos (seccion
    # 62): se descarta la palabra y el texto del segmento se conserva intacto.
    if word["end"] <= word["start"]:
        return None
    return word


def _build_segment(raw: Any, *, index: int, with_words: bool) -> dict[str, Any] | None:
    text = _clean_text(getattr(raw, "text", "") or "")
    if len(text) < MIN_SEGMENT_CHARS:
        return None

    start = _sanitize_time(getattr(raw, "start", 0.0))
    end = _sanitize_time(getattr(raw, "end", start))
    if end < start:
        end = start

    # Se usa el id que devuelve el backend, no el indice del bucle: si un
    # segmento se descarta, los ids quedan con huecos y se sigue pudiendo
    # rastrear de donde salio cada cosa.
    segment_id = getattr(raw, "id", index)
    if not isinstance(segment_id, int) or segment_id < 0:
        segment_id = index

    segment: dict[str, Any] = {"id": segment_id, "start": start, "end": end, "text": text}

    no_speech = getattr(raw, "no_speech_prob", None)
    if isinstance(no_speech, (int, float)):
        segment["no_speech_prob"] = round(float(no_speech), 4)

    if with_words:
        words = [_build_word(word) for word in (getattr(raw, "words", None) or [])]
        collected = [word for word in words if word is not None]
        if collected:
            segment["words"] = collected

    return segment


def _resolve_language(raw_info: Any, segments: list[dict[str, Any]]) -> tuple[str, float | None]:
    """Idioma detectado, o 'unknown' si no es fiable (seccion 62).

    Sin voz no hay idioma que detectar. Whisper devuelve algo con probabilidad
    baja sobre audio mudo o de musica, y apuntarlo en el transcript fijaria un
    idioma falso que se propagaria a todo el analisis posterior. 'unknown' es la
    respuesta honesta.
    """
    detected = str(getattr(raw_info, "language", "") or "").strip()
    probability = getattr(raw_info, "language_probability", None)
    confidence = round(float(probability), 4) if isinstance(probability, (int, float)) else None

    if not segments:
        return UNKNOWN_LANGUAGE, confidence

    if not detected or len(detected) > 8:
        return UNKNOWN_LANGUAGE, confidence

    # Por debajo de este valor, la deteccion es una apuesta.
    if confidence is not None and confidence < 0.35:
        return UNKNOWN_LANGUAGE, confidence

    return detected, confidence


# --- API principal -----------------------------------------------------------


@dataclass
class TranscriptionResult:
    job_id: str | None
    audio_path: str
    language: str
    language_probability: float | None
    duration: float
    segment_count: int
    word_count: int
    elapsed_seconds: float
    reused: bool
    transcript_path: str | None = None
    transcript_sha256: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        confidence = (
            f"{self.language_probability:.0%}" if self.language_probability is not None else "n/d"
        )
        lines = [
            f"idioma      {self.language} ({confidence})",
            f"segmentos   {self.segment_count}",
            f"palabras    {self.word_count}",
            f"duracion    {self.duration:.1f}s",
            f"tiempo      {self.elapsed_seconds:.1f}s",
        ]
        if self.reused:
            lines.append("reutilizado transcript.json existente (seccion 51)")
        for warning in self.warnings:
            lines.append(f"AVISO       {warning}")
        return "\n".join(lines)


def transcribe(
    audio_path: Path | str,
    config: dict[str, Any],
    *,
    job_id: str | None = None,
    transcript_path: Path | str | None = None,
    overwrite: bool = False,
) -> TranscriptionResult:
    """Transcribe un WAV 16 kHz mono y escribe `transcript.json`.

    Idempotente (seccion 51): si el transcript ya existe y es valido, se
    devuelve tal cual sin volver a cargar el modelo.
    """
    audio = Path(audio_path)
    if not audio.is_file():
        raise TranscriptionError(
            f"no existe el audio a transcribir: {audio}. "
            "La ingesta tiene que extraerlo antes (seccion 12, paso 7).",
            code="missing_audio",
        )

    target = Path(transcript_path) if transcript_path else audio.with_name("transcript.json")

    # --- Idempotencia -------------------------------------------------------
    if target.is_file() and not overwrite:
        existing = artifacts.read_or_none(target, schema_name="transcript")
        if existing is not None:
            segments = existing.get("segments", [])
            LOGGER.info(
                "transcript ya existe, se reutiliza",
                extra={"path": str(target), "segments": len(segments)},
            )
            return TranscriptionResult(
                job_id=existing.get("job_id"),
                audio_path=str(audio),
                language=existing.get("language", UNKNOWN_LANGUAGE),
                language_probability=existing.get("language_probability"),
                duration=float(existing.get("duration", 0.0)),
                segment_count=len(segments),
                word_count=sum(len(segment.get("words", [])) for segment in segments),
                elapsed_seconds=0.0,
                reused=True,
                transcript_path=str(target),
                # El SHA se calcula igual al reutilizar. Si no, el resultado
                # llega con None a `record_artifact` y el upsert BORRA el hash
                # que se habia guardado en la primera transcripcion. El
                # artefacto en disco no ha cambiado, asi que su hash tampoco.
                transcript_sha256=artifacts.sha256_of_file(target),
                warnings=[],
            )

    whisper_config = config.get("whisper", {}) if isinstance(config, dict) else {}
    device = str(whisper_config.get("device", "cuda"))
    model_name = str(whisper_config.get("model", "large-v3-turbo"))
    with_words = bool(whisper_config.get("word_timestamps", True))
    language_setting = whisper_config.get("language", "auto")
    language_arg = None if str(language_setting).lower() in ("auto", "", "none") else str(language_setting)

    vad_parameters = whisper_config.get("vad_parameters") or {}
    if vad_parameters and not isinstance(vad_parameters, dict):
        vad_parameters = {}

    needed_gb = whisper_config.get("vram_required_gb")
    started = time.perf_counter()

    # En CPU no se pide VRAM. Exigir 1.6 GB de VRAM de CUDA a quien ha puesto
    # `device: cpu` haria que el pipeline fuese inejecutable sin GPU, que es
    # justo el caso de quien configura CPU: o no tiene grafica, o quiere
    # reservarla para Gemma (seccion 49). El lease se sigue tomando, porque la
    # exclusion mutua con la etapa de Gemma es de la maquina de estados, no de
    # la VRAM.
    if device != "cpu" and needed_gb:
        vram_needed: float | None = float(needed_gb)
    else:
        vram_needed = None

    with gpu.gpu_lease("transcribing", needed_gb=vram_needed):
        model = load_model(config)
        LOGGER.info("transcribiendo", extra={"audio": audio.name, "model": model_name, "device": device})

        try:
            # La llamada NO falla: faster-whisper devuelve un generador perezoso.
            # Todo el trabajo real, y todos los errores, ocurren al iterarlo.
            stream, info = model.transcribe(
                str(audio),
                language=language_arg,
                beam_size=int(whisper_config.get("beam_size", 5)),
                vad_filter=bool(whisper_config.get("vad_filter", True)),
                vad_parameters=vad_parameters or None,
                word_timestamps=with_words,
                condition_on_previous_text=bool(whisper_config.get("condition_on_previous_text", False)),
            )

            segments: list[dict[str, Any]] = []
            for index, raw in enumerate(stream):
                built = _build_segment(raw, index=index, with_words=with_words)
                if built is not None:
                    segments.append(built)
        except TranscriptionError:
            raise
        except BaseException as exc:  # noqa: BLE001 - se clasifica abajo
            raise _classify(exc, model=model_name, device=device) from exc

    elapsed = time.perf_counter() - started

    language, confidence = _resolve_language(info, segments)
    duration = _sanitize_time(getattr(info, "duration", 0.0))

    document = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "source": {
            "audio_file": audio.name,
            "audio_sample_rate": int(whisper_config.get("audio_sample_rate", 16000)),
            "audio_channels": int(whisper_config.get("audio_channels", 1)),
            "sha256": artifacts.sha256_of_file(audio),
        },
        "language": language,
        "language_probability": confidence,
        "duration": duration,
        "generator": {
            "provider": PROVIDER,
            "model": model_name,
            "parameters": {
                "device": device,
                "compute_type": resolve_compute_type(config, device),
                "beam_size": int(whisper_config.get("beam_size", 5)),
                "vad_filter": bool(whisper_config.get("vad_filter", True)),
                "vad_parameters": vad_parameters,
                "word_timestamps": with_words,
                "condition_on_previous_text": bool(
                    whisper_config.get("condition_on_previous_text", False)
                ),
                "language_setting": str(language_setting),
            },
        },
        "segments": segments,
    }

    warnings = _warn_about(document, whisper_config)
    info_written = artifacts.write_json(
        target, document, schema_name="transcript", overwrite=True
    )

    LOGGER.info(
        "transcripcion completada",
        extra={
            "segments": len(segments),
            "words": sum(len(s.get("words", [])) for s in segments),
            "language": language,
            "elapsed_seconds": round(elapsed, 2),
            "path": str(target),
        },
    )

    return TranscriptionResult(
        job_id=job_id,
        audio_path=str(audio),
        language=language,
        language_probability=confidence,
        duration=duration,
        segment_count=len(segments),
        word_count=sum(len(segment.get("words", [])) for segment in segments),
        elapsed_seconds=round(elapsed, 2),
        reused=False,
        transcript_path=str(info_written.path),
        transcript_sha256=info_written.sha256,
        warnings=warnings,
    )


def _warn_about(document: dict[str, Any], whisper_config: dict[str, Any]) -> list[str]:
    """Avisos de calidad. Ninguno bloquea: el pipeline continua (seccion 67)."""
    warnings: list[str] = []
    segments = document["segments"]
    duration = document["duration"]

    if not segments:
        warnings.append(
            "Whisper no encontro voz. Puede ser un video mudo, musica o silencio. "
            "El analisis semantico no tendra nada que evaluar."
        )

    if document["language"] == UNKNOWN_LANGUAGE and segments:
        warnings.append(
            "idioma no detectado de forma fiable. Se guarda 'unknown' en vez de un valor "
            "supuesto (seccion 62). Fijalo con 'whisper.language' en config si lo sabes."
        )

    if duration > 0:
        overshoot = [s for s in segments if s["end"] > duration + DURATION_OVERSHOOT_TOLERANCE]
        if overshoot:
            warnings.append(
                f"{len(overshoot)} segmentos terminan despues de la duracion del audio "
                f"({duration:.1f}s). Suele ser redondeo del VAD; los tiempos se conservan tal cual."
            )

    if whisper_config.get("word_timestamps") and segments:
        without_words = [s for s in segments if not s.get("words")]
        if len(without_words) == len(segments):
            warnings.append(
                "se pidieron timestamps por palabra pero ningun segmento los trae. "
                "Los cortes entre palabras no seran posibles en esta transcripcion."
            )

    return warnings


def load_transcript(path: Path | str) -> dict[str, Any]:
    """Lee `transcript.json` validandolo. Lo consumen las etapas siguientes."""
    return artifacts.read_json(path, schema_name="transcript")


def is_valid(path: Path | str) -> bool:
    """True si el fichero existe y cumple el schema. No lanza."""
    target = Path(path)
    if not target.is_file():
        return False
    try:
        return schemas.is_valid("transcript", artifacts.read_json(target))
    except artifacts.ArtifactError:
        return False
