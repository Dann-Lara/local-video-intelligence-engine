"""Transcript engine (`src/transcription/whisper.py`).

Division de los tests:

* La mayoria NO toca faster-whisper. Usa un backend falso que imita la API
  perezosa del backend real, que es justo donde estan los bugs caros: el
  generador devuelve OK y revienta al iterarlo.
* Los que si transcriben usan `tiny` en CPU con audio real de SAPI. Tardan unos
  segundos y demuestran que el circuito completo funciona.
* El modelo real (`large-v3-turbo` en CUDA) se comprueba aparte y se puede
  saltar, porque necesita la GPU libre.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import pytest

from src.core import artifacts, gpu, schemas
from src.transcription import normalize, whisper


# --- backend falso ----------------------------------------------------------


@dataclass
class FakeWord:
    word: str
    start: float
    end: float
    probability: float = 0.9


@dataclass
class FakeSegment:
    id: int
    start: float
    end: float
    text: str
    no_speech_prob: float = 0.01
    words: list[FakeWord] = field(default_factory=list)


@dataclass
class FakeInfo:
    language: str = "es"
    language_probability: float = 0.97
    duration: float = 4.0


class FakeModel:
    """Imita la API de `WhisperModel` con la misma trampa: generador perezoso."""

    def __init__(
        self,
        model_name: str = "fake",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        segments: list[FakeSegment] | None = None,
        info: FakeInfo | None = None,
        raise_on_iterate: BaseException | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._segments = segments if segments is not None else [
            FakeSegment(0, 0.0, 2.0, " Hola mundo", words=[FakeWord("Hola", 0.0, 0.5)]),
        ]
        self._info = info or FakeInfo()
        self._raise_on_iterate = raise_on_iterate
        self.calls: list[dict[str, Any]] = []
        self.released = False

    def transcribe(self, path: str, **kwargs: Any) -> tuple[Iterator[FakeSegment], FakeInfo]:
        self.calls.append({"path": path, **kwargs})

        def generator() -> Iterator[FakeSegment]:
            if self._raise_on_iterate is not None:
                raise self._raise_on_iterate
            yield from self._segments

        return generator(), self._info

    def release(self) -> None:
        self.released = True


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    model = FakeModel()
    monkeypatch.setattr(whisper, "load_model", lambda config: model)
    return model


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """Un WAV de verdad: `transcribe` valida que el audio existe y le hashea."""
    path = tmp_path / "audio.wav"
    path.write_bytes(b"RIFF" + bytes(512))
    return path


# --- clasificacion de errores -----------------------------------------------


class TestClassify:
    """El bug mas caro del modulo: el error sale al ITERAR, no al llamar."""

    def test_cublas_ausente_da_un_mensaje_util(self) -> None:
        original = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        error = whisper._classify(original, model="large-v3-turbo", device="cuda")

        assert error.code == "cuda_runtime_missing"
        assert "nvidia-cublas-cu12" in str(error)
        assert "primer segmento" in str(error)
        assert error.retryable is False

    def test_oom_es_reintentable(self) -> None:
        error = whisper._classify(
            RuntimeError("CUDA error: out of memory"), model="large-v3-turbo", device="cuda"
        )
        assert error.code == "vram_oom"
        assert error.retryable is True
        assert "cpu" in str(error)

    def test_modelo_no_descargado_es_reintentable(self) -> None:
        error = whisper._classify(
            RuntimeError("Model 'large-v3-turbo' does not appear to be downloaded"),
            model="large-v3-turbo",
            device="cpu",
        )
        assert error.code == "model_unavailable"
        assert error.retryable is True

    def test_error_generico_se_reintentable(self) -> None:
        error = whisper._classify(ZeroDivisionError("raro"), model="tiny", device="cpu")
        assert error.code == "backend_error"
        assert error.retryable is True

    def test_un_error_al_iterar_se_convierte_en_transcription_error(
        self, tmp_path: Path, audio_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La excepcion se lanza DENTRO del bucle, no en la llamada."""
        model = FakeModel(raise_on_iterate=RuntimeError("Library cublas64_12.dll is not found"))
        monkeypatch.setattr(whisper, "load_model", lambda config: model)

        with pytest.raises(whisper.TranscriptionError) as excinfo:
            whisper.transcribe(audio_file, {"whisper": {"device": "cuda"}},
                               transcript_path=tmp_path / "t.json")

        assert excinfo.value.code == "cuda_runtime_missing"


# --- saneamiento -------------------------------------------------------------


class TestSanitize:
    def test_limpia_espacios_iniciales(self) -> None:
        assert whisper._clean_text("  Hola mundo  ") == "Hola mundo"

    def test_tiempos_no_negativos_nan_e_infinito(self) -> None:
        assert whisper._sanitize_time(-3.5) == 0.0
        assert whisper._sanitize_time(float("nan")) == 0.0
        assert whisper._sanitize_time(float("inf")) == 0.0
        assert whisper._sanitize_time("no es un numero") == 0.0
        assert whisper._sanitize_time(1.23456) == 1.235

    def test_descarta_segmentos_vacios(self) -> None:
        assert whisper._build_segment(FakeSegment(0, 0.0, 1.0, "   "), index=0, with_words=False) is None

    def test_conserva_el_id_del_backend_tras_descartar(self) -> None:
        """Los huecos en los ids son correctos: permiten rastrear el origen."""
        built = whisper._build_segment(FakeSegment(7, 0.0, 1.0, "texto"), index=0, with_words=True)
        assert built is not None
        assert built["id"] == 7

    def test_corrige_end_menor_que_start(self) -> None:
        built = whisper._build_segment(FakeSegment(0, 5.0, 2.0, "raro"), index=0, with_words=False)
        assert built is not None
        assert built["end"] >= built["start"]

    def test_probabilidad_de_palabra_null_y_no_cero(self) -> None:
        """Seccion 38: 0 significa unknown, no cero reales."""
        palabra = FakeWord("Hola", 0.0, 0.5, probability=0.0)
        built = whisper._build_word(palabra)
        assert built is not None
        assert built["probability"] == 0.0

        sin_probabilidad = whisper._build_word(
            type("W", (), {"word": "Hola", "start": 0.0, "end": 0.5, "probability": None})()
        )
        assert sin_probabilidad is not None
        assert sin_probabilidad["probability"] is None

    def test_ignora_palabras_vacias(self) -> None:
        assert whisper._build_word(FakeWord("   ", 0.0, 0.5)) is None

    def test_descarta_palabras_de_duracion_nula(self) -> None:
        """Whisper emite tokens con start == end (artefacto del backend).

        Una palabra de duracion nula no aporta alineacion temporal y viola el
        invariante start < end. Se descarta en la entrada; fabricar tiempos
        (end = start + epsilon) seria inventar datos (seccion 62).
        """
        assert whisper._build_word(FakeWord("sti", 4.23, 4.23)) is None
        assert whisper._build_word(FakeWord("ok", 4.23, 4.22)) is None


class TestLanguage:
    def test_sin_segmentos_es_unknown(self) -> None:
        """Sin voz no hay idioma. Inventar uno seria alucinar (seccion 62)."""
        language, confidence = whisper._resolve_language(FakeInfo(language="en", language_probability=0.99), [])
        assert language == "unknown"
        assert confidence == 0.99

    def test_confianza_baja_es_unknown(self) -> None:
        language, _ = whisper._resolve_language(
            FakeInfo(language="en", language_probability=0.10), [FakeSegment(0, 0.0, 1.0, "x")]
        )
        assert language == "unknown"

    def test_confianza_alta_se_acepta(self) -> None:
        language, confidence = whisper._resolve_language(
            FakeInfo(language="es", language_probability=0.99), [FakeSegment(0, 0.0, 1.0, "x")]
        )
        assert language == "es"
        assert confidence == 0.99

    def test_nombre_de_idioma_absurdo_es_unknown(self) -> None:
        language, _ = whisper._resolve_language(
            FakeInfo(language="un_idioma_imposible", language_probability=0.99),
            [FakeSegment(0, 0.0, 1.0, "x")],
        )
        assert language == "unknown"


# --- API principal -----------------------------------------------------------


class TestTranscribe:
    def test_escribe_un_transcript_valido(
        self, tmp_path: Path, audio_file: Path, fake_model: FakeModel, tiny_whisper_config: dict
    ) -> None:
        target = tmp_path / "transcript.json"
        result = whisper.transcribe(audio_file, tiny_whisper_config, job_id="job_1", transcript_path=target)

        assert target.is_file()
        assert result.reused is False
        assert result.job_id == "job_1"
        assert result.transcript_path == str(target)

        document = artifacts.read_json(target, schema_name="transcript")
        assert document["job_id"] == "job_1"
        assert document["language"] == "es"
        assert document["segments"][0]["text"] == "Hola mundo"
        assert document["source"]["audio_file"] == "audio.wav"
        assert len(document["source"]["sha256"]) == 64

    def test_pasa_los_parametros_de_la_config(
        self, tmp_path: Path, audio_file: Path, fake_model: FakeModel, tiny_whisper_config: dict
    ) -> None:
        tiny_whisper_config["whisper"].update(
            {"language": "es", "vad_filter": False, "word_timestamps": True, "beam_size": 3}
        )
        whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=tmp_path / "t.json")

        llamada = fake_model.calls[0]
        assert llamada["language"] == "es"
        assert llamada["beam_size"] == 3
        assert llamada["vad_filter"] is False
        assert llamada["word_timestamps"] is True

    def test_language_auto_se_traduce_a_none(
        self, tmp_path: Path, audio_file: Path, fake_model: FakeModel, tiny_whisper_config: dict
    ) -> None:
        """`"auto"` no es un idioma valido para faster-whisper: se pasa None."""
        tiny_whisper_config["whisper"]["language"] = "auto"
        whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=tmp_path / "t.json")
        assert fake_model.calls[0]["language"] is None

    def test_descarta_palabras_de_duracion_nula_al_transcribir(
        self, tmp_path: Path, audio_file: Path, tiny_whisper_config: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regresion del E2E real: Whisper emitio tokens con start == end y el
        transcript invalido no dejaba avanzar el pipeline."""
        model = FakeModel(
            segments=[
                FakeSegment(
                    0,
                    0.0,
                    2.0,
                    " Hola mundo",
                    words=[
                        FakeWord("Hola", 0.0, 0.3),
                        FakeWord("si", 0.5, 0.5),  # artefacto: duracion nula
                        FakeWord("mundo", 0.6, 1.0),
                    ],
                )
            ]
        )
        monkeypatch.setattr(whisper, "load_model", lambda config: model)

        target = tmp_path / "transcript.json"
        whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=target)

        document = artifacts.read_json(target, schema_name="transcript")
        words = document["segments"][0]["words"]
        assert [w["word"] for w in words] == ["Hola", "mundo"]

    def test_audio_inexistente_falla_con_codigo_claro(self, tmp_path: Path, tiny_whisper_config: dict) -> None:
        with pytest.raises(whisper.TranscriptionError) as excinfo:
            whisper.transcribe(tmp_path / "nada.wav", tiny_whisper_config, transcript_path=tmp_path / "t.json")
        assert excinfo.value.code == "missing_audio"

    def test_idempotencia_no_vuelve_a_transcribir(
        self, tmp_path: Path, audio_file: Path, fake_model: FakeModel, tiny_whisper_config: dict
    ) -> None:
        target = tmp_path / "transcript.json"
        primero = whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=target)
        segundo = whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=target)

        assert primero.reused is False
        assert segundo.reused is True
        assert segundo.segment_count == primero.segment_count
        assert len(fake_model.calls) == 1, "no debe volver a llamar al modelo"

    def test_overwrite_vuelve_a_transcribir(
        self, tmp_path: Path, audio_file: Path, fake_model: FakeModel, tiny_whisper_config: dict
    ) -> None:
        target = tmp_path / "transcript.json"
        whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=target)
        whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=target, overwrite=True)
        assert len(fake_model.calls) == 2

    def test_toma_el_lease_de_la_gpu(
        self,
        tmp_path: Path,
        audio_file: Path,
        fake_model: FakeModel,
        tiny_whisper_config: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Se pide el lease con la VRAM de la configuracion, y el nombre de la fase.

        `require_vram` va Tambien espiado. Si se dejara el de verdad, este test
        pasaria o fallaria segun quanta VRAM libre hubiera en el escritorio en
        ese momento (navegador, LM Studio, Wallpaper Engine), que no tiene nada
        que ver con lo que se comprueba aqui.
        """
        visto: dict[str, Any] = {}

        @contextlib.contextmanager
        def espia(name: str, *, needed_gb: float | None = None) -> Iterator[None]:
            visto["name"] = name
            visto["needed_gb"] = needed_gb
            yield

        monkeypatch.setattr(gpu, "gpu_lease", espia)
        # La config `tiny` viene en CPU; este test es sobre CUDA, asi que se
        # cambia el dispositivo explicitamente.
        tiny_whisper_config["whisper"]["device"] = "cuda"
        tiny_whisper_config["whisper"]["vram_required_gb"] = 1.6
        whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=tmp_path / "t.json")

        assert visto["name"] == "transcribing"
        assert visto["needed_gb"] == 1.6

    def test_en_cpu_no_pide_vram_de_cuda(
        self,
        tmp_path: Path,
        audio_file: Path,
        fake_model: FakeModel,
        tiny_whisper_config: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`device: cpu` no puede exigir VRAM de CUDA.

        Si lo hiciera, el pipeline seria inejecutable sin GPU, que es
        precisamente el caso de quien configura CPU. Y en una maquina con la
        GPU ocupada (navegador, LM Studio) tampoco se podria usar el modo lento
        como recurso de escape.
        """
        visto: dict[str, Any] = {}

        @contextlib.contextmanager
        def espia(name: str, *, needed_gb: float | None = None) -> Iterator[None]:
            visto["needed_gb"] = needed_gb
            yield

        monkeypatch.setattr(gpu, "gpu_lease", espia)
        tiny_whisper_config["whisper"]["device"] = "cpu"
        tiny_whisper_config["whisper"]["vram_required_gb"] = 1.6

        whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=tmp_path / "t.json")

        assert visto["needed_gb"] is None

    def test_un_lease_realmente_insuficiente_aborta_antes_de_transcribir(
        self,
        tmp_path: Path,
        audio_file: Path,
        fake_model: FakeModel,
        tiny_whisper_config: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Falla pronto y no despues de 20 minutos de transcripcion (seccion 49)."""

        def insuficiente(*args: Any, **kwargs: Any) -> Any:
            raise gpu.GpuUnavailableError("no cabe")

        monkeypatch.setattr(gpu, "require_vram", insuficiente)
        tiny_whisper_config["whisper"]["device"] = "cuda"
        tiny_whisper_config["whisper"]["vram_required_gb"] = 1.6

        with pytest.raises(gpu.GpuUnavailableError):
            whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=tmp_path / "t.json")

        assert fake_model.calls == [], "no debe ni cargar el modelo"

    def test_avisa_si_no_hay_voz(
        self, tmp_path: Path, audio_file: Path, monkeypatch: pytest.MonkeyPatch, tiny_whisper_config: dict
    ) -> None:
        vacio = FakeModel(segments=[], info=FakeInfo(language="", language_probability=0.0, duration=3.0))
        monkeypatch.setattr(whisper, "load_model", lambda config: vacio)

        result = whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=tmp_path / "t.json")

        assert result.segment_count == 0
        assert result.language == "unknown"
        assert any("voz" in w for w in result.warnings)
        # Aun asi escribe un transcript VALIDO, con la lista de segmentos vacia.
        assert schemas.is_valid("transcript", artifacts.read_json(tmp_path / "t.json"))


class TestModelCache:
    def test_el_modelo_se_reutiliza_entre_llamadas(
        self, tiny_whisper_config: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whisper._MODELS.clear()
        creados: list[str] = []

        class Contador(FakeModel):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                creados.append("nuevo")

        monkeypatch.setattr(whisper, "_import_faster_whisper", lambda: Contador)
        monkeypatch.setattr(whisper.cuda, "ensure_cuda_dlls", lambda **_: whisper.cuda.CudaReport())

        primero = whisper.load_model(tiny_whisper_config)
        segundo = whisper.load_model(tiny_whisper_config)

        assert primero is segundo
        assert len(creados) == 1
        whisper.release()

    def test_release_suelta_los_modelos(self, tiny_whisper_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        whisper._MODELS.clear()
        monkeypatch.setattr(whisper, "_import_faster_whisper", lambda: FakeModel)
        whisper.load_model(tiny_whisper_config)
        assert whisper.model_cache_stats()["loaded"] == 1

        whisper.release()

        assert whisper.model_cache_stats()["loaded"] == 0

    def test_release_sin_modelos_no_falla(self) -> None:
        whisper.release()
        whisper.release()


class TestCudaGuard:
    """Con device=cuda y sin runtime, el fallo tiene que salir ANTES de cargar."""

    def test_falla_antes_de_intentar_cargar_el_modelo(self, tiny_whisper_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        tiny_whisper_config["whisper"]["device"] = "cuda"
        roto = whisper.cuda.CudaReport(missing=["cublas64_12.dll"])
        monkeypatch.setattr(whisper.cuda, "preflight", lambda device="cuda": roto)

        with pytest.raises(whisper.TranscriptionError) as excinfo:
            whisper.load_model(tiny_whisper_config)

        assert excinfo.value.code == "cuda_runtime_missing"
        # El mensaje tiene que decir que hacer, no solo que falla.
        assert "'whisper.device' a 'cpu'" in str(excinfo.value)

    def test_en_cpu_el_runtime_de_cuda_da_igual(self, tiny_whisper_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(whisper, "_import_faster_whisper", lambda: FakeModel)
        monkeypatch.setattr(
            whisper.cuda, "preflight", lambda device="cuda": whisper.cuda.CudaReport(missing=["x.dll"])
        )
        assert whisper.load_model(tiny_whisper_config) is not None
        whisper.release()


class TestComputeType:
    """`int8_float16` es de GPU. En CPU revienta, asi que hay que corregirlo.

    Este no es un detalle teorico: el mensaje de error de VRAM agotada sugiere
    `device: cpu` como salida, y seguir ese consejo sin corregir el compute_type
    moria igualmente al cargar el modelo.
    """

    def test_el_por_defecto_se_usa_en_cuda(self, tiny_whisper_config: dict) -> None:
        config = {**tiny_whisper_config}
        config["whisper"] = {**config["whisper"], "compute_type": "int8_float16"}

        assert whisper.resolve_compute_type(config, "cuda") == "int8_float16"

    def test_int8_float16_se_convierte_en_int8_en_cpu(self, tiny_whisper_config: dict) -> None:
        config = {**tiny_whisper_config}
        config["whisper"] = {**config["whisper"], "compute_type": "int8_float16"}

        assert whisper.resolve_compute_type(config, "cpu") == "int8"

    def test_float16_puro_se_convierte_en_float32_en_cpu(self, tiny_whisper_config: dict) -> None:
        config = {**tiny_whisper_config}
        config["whisper"] = {**config["whisper"], "compute_type": "float16"}

        assert whisper.resolve_compute_type(config, "cpu") == "float32"

    def test_un_tipo_valido_para_cpu_se_respeta(self, tiny_whisper_config: dict) -> None:
        """Si el usuario elige float32 a proposito, no se le cambia."""
        config = {**tiny_whisper_config}
        config["whisper"] = {**config["whisper"], "compute_type": "float32"}

        assert whisper.resolve_compute_type(config, "cpu") == "float32"

    def test_el_modelo_se_carga_con_el_tipo_corregido(
        self, tmp_path: Path, audio_file: Path, tiny_whisper_config: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El tipo corregido es el que llega a faster-whisper, no solo una variable."""
        pedidos: list[dict[str, Any]] = []

        class Espia(FakeModel):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pedidos.append(kwargs)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(whisper, "_import_faster_whisper", lambda: Espia)
        monkeypatch.setattr(
            whisper.cuda, "ensure_cuda_dlls", lambda **_: whisper.cuda.CudaReport()
        )
        whisper._MODELS.clear()
        tiny_whisper_config["whisper"]["device"] = "cpu"
        tiny_whisper_config["whisper"]["compute_type"] = "int8_float16"

        whisper.transcribe(audio_file, tiny_whisper_config, transcript_path=tmp_path / "t.json")
        whisper.release()

        assert pedidos[0]["compute_type"] == "int8"
        assert pedidos[0]["device"] == "cpu"


class TestReutilizacionYHashes:
    """Al reutilizar un artefacto, el SHA tiene que seguir disponible.

    La ruta de idempotencia (seccion 51) evita volver a cargar un modelo de
    1,5 GB si el transcript ya esta. Pero esa ruta es la que se ejecuta en cada
    reejecucion de `analyze`, y si devuelve el hash vacio, el `record_artifact`
    posterior degrada a NULL el SHA que ya estaba en la base. El archivo del
    disco no ha cambiado, asi que su hash tampoco puede haber cambiado.
    """

    def test_reutilizar_no_pierde_el_sha_del_transcript(
        self, tmp_path: Path, speech_wav: Path, tiny_whisper_config: dict
    ) -> None:
        target = tmp_path / "transcript.json"

        primero = whisper.transcribe(
            speech_wav, tiny_whisper_config, transcript_path=target
        )
        segundo = whisper.transcribe(
            speech_wav, tiny_whisper_config, transcript_path=target
        )

        assert primero.reused is False
        assert segundo.reused is True
        assert primero.transcript_sha256 is not None
        assert segundo.transcript_sha256 == primero.transcript_sha256
        assert segundo.transcript_sha256 == artifacts.sha256_of_file(target)

    def test_reutilizar_no_pierde_el_sha_del_normalizado(
        self, tmp_path: Path, speech_wav: Path, tiny_whisper_config: dict
    ) -> None:
        target = tmp_path / "transcript.json"
        whisper.transcribe(speech_wav, tiny_whisper_config, transcript_path=target)

        primero = normalize.normalize(target)
        segundo = normalize.normalize(target)

        assert primero.reused is False
        assert segundo.reused is True
        assert primero.sha256 is not None
        assert segundo.sha256 == primero.sha256

    def test_el_sha_reutilizado_es_el_del_archivo_en_disco(
        self, tmp_path: Path, speech_wav: Path, tiny_whisper_config: dict
    ) -> None:
        """No basta con que sea estable: tiene que ser el hash de ESE archivo."""
        target = tmp_path / "transcript.json"
        whisper.transcribe(speech_wav, tiny_whisper_config, transcript_path=target)
        resultado = whisper.transcribe(speech_wav, tiny_whisper_config, transcript_path=target)

        esperado = hashlib.sha256(target.read_bytes()).hexdigest()
        assert resultado.transcript_sha256 == esperado


# --- transcripcion real ------------------------------------------------------

@pytest.mark.slow
class TestRealTranscription:
    """Audio real de SAPI con `tiny` en CPU. Si esto pasa, el circuito funciona."""

    def test_transcribe_voz_real(
        self, tmp_path: Path, speech_wav: Path, tiny_whisper_config: dict
    ) -> None:
        target = tmp_path / "transcript.json"
        result = whisper.transcribe(speech_wav, tiny_whisper_config, job_id="real", transcript_path=target)

        assert result.segment_count > 0, "Whisper no deberia devolver 0 segmentos con voz real"
        assert result.language == "es"
        assert result.word_count > 0, "tiny CPU con word_timestamps debe devolver palabras"

        document = artifacts.read_json(target, schema_name="transcript")
        assert schemas.is_valid("transcript", document)

        # Los tiempos tienen que crecer y no salirse del audio.
        for segment in document["segments"]:
            assert 0.0 <= segment["start"] <= segment["end"] <= result.duration + 1.0

    def test_el_texto_transcrito_se_aproxima_a_la_frase(
        self, tmp_path: Path, speech_wav: Path, tiny_whisper_config: dict
    ) -> None:
        """Comprueba que no es texto inventado, comparando palabras clave.

        No se exige coincidencia literal palabra por palabra: `tiny` espeja y
        simplifica ("verticales" -> "vertical", "nueve por dieciseis" -> "9 por
        16"). Lo que se verifica es que aparezca el CONTENIDO de la frase, que es
        lo que distingue una transcripcion real de una alucinacion o de un audio
        sin voz. Se exige mayoria, no todas: una palabra fallada es esperable,
        cinco del total no lo son.
        """
        target = tmp_path / "transcript.json"
        whisper.transcribe(speech_wav, tiny_whisper_config, transcript_path=target)
        texto = " ".join(
            s["text"].lower() for s in artifacts.read_json(target)["segments"]
        )

        palabras = ("motor", "clips", "funciona", "correctamente", "fragmento", "recorta", "subtitula")
        encontradas = [p for p in palabras if p in texto]
        assert len(encontradas) >= 5, (
            f"solo {len(encontradas)}/{len(palabras)} palabras clave en: {texto[:250]}"
        )
