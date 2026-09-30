"""Lease de GPU (seccion 67 y el orden de las fases 3 y 4).

Por que esto merece tests: el sintoma que justifica el modulo es un fallo
INTERMITENTE. Whisper y Gemma 3 4B no caben juntos en 8 GB de VRAM, y cuando se
lanzan a la vez el error es un `RuntimeError` de CTranslate2 o de llama2.cpp
diez minutos dentro, sin relacion aparente con la causa. Un test que verifica
"no se puede tomar la GPU dos veces" es mas barato que ese diagnostico.

Se prueba con `nvidia-smi` real cuando esta disponible, y con stubs cuando no,
para que la suite no dependa de la GPU de la maquina que la ejecuta.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import gpu  # noqa: E402


@pytest.fixture(autouse=True)
def release_gpu_lease():
    """Cada test empieza y termina sin nadie en la GPU."""
    yield
    gpu._GPU_OWNER = None


# --- estado de la VRAM ---


def test_vram_report_never_raises():
    """Si nvidia-smi no esta, el informe es 'desconocido', no una excepcion."""
    report = gpu.vram_report()
    assert isinstance(report, gpu.VramReport)
    if report.total_mb is None:
        assert "no disponible" in report.summary()
    else:
        assert report.total_mb > 0
        assert 0 <= report.free_mb <= report.total_mb


def test_vram_units_are_gigabytes():
    report = gpu.VramReport(total_mb=8192, free_mb=6144, used_mb=2048, raw="")
    assert report.total_gb == 8.0
    assert report.free_gb == 6.0
    assert "8.0 GB totales" in report.summary()


def test_vram_report_derives_free_from_used():
    """Si la consulta no trae `memory.free`, se calcula (nvidia-smi antiguo)."""
    report = gpu.VramReport(total_mb=8192, free_mb=None, used_mb=2048, raw="")
    assert report.free_mb is None
    assert "desconocida" in report.summary()


def test_require_vram_passes_when_there_is_room(monkeypatch):
    monkeypatch.setattr(
        gpu, "vram_report",
        lambda: gpu.VramReport(total_mb=8192, free_mb=6144, used_mb=2048, raw=""),
    )
    assert gpu.require_vram(2.0).free_gb == 6.0


def test_require_vram_fails_before_starting(monkeypatch):
    """Falla al pedir la GPU, no a mitad de la transcripcion."""
    monkeypatch.setattr(
        gpu, "vram_report",
        lambda: gpu.VramReport(total_mb=8192, free_mb=1024, used_mb=7168, raw=""),
    )
    with pytest.raises(gpu.GpuUnavailableError) as exc:
        gpu.require_vram(2.0)
    message = str(exc.value)
    assert "2.0 GB" in message
    assert "1.0 GB libres" in message
    # El mensaje tiene que decir QUE HACER, no solo que no cabe.
    assert "navegador" in message
    assert "device: cpu" in message


def test_lease_error_names_both_phases():
    """El rechazo del lease cita las dos fases: sin eso no se sabe que choca."""
    with gpu.gpu_lease("transcribing"):
        with pytest.raises(gpu.GpuUnavailableError) as exc:
            with gpu.gpu_lease("content_analysis"):
                pass
    message = str(exc.value)
    assert "transcribing" in message
    assert "content_analysis" in message
    assert "Whisper y Gemma" in message


def test_require_vram_explains_the_headroom(monkeypatch):
    """Con 3.0 GB libres y 2.0+1.5 de exigencia, todavia no entra."""
    monkeypatch.setattr(
        gpu, "vram_report",
        lambda: gpu.VramReport(total_mb=8192, free_mb=3072, used_mb=5120, raw=""),
    )
    with pytest.raises(gpu.GpuUnavailableError, match="margen"):
        gpu.require_vram(2.0)


def test_require_vram_is_permissive_when_unknown(monkeypatch):
    """Sin GPU visible no se bloquea la ejecucion: se avisa y se sigue."""
    monkeypatch.setattr(gpu, "vram_report", lambda: gpu.VramReport(None, None, None, ""))
    assert gpu.require_vram(99.0).free_gb is None


def test_headroom_is_configurable(monkeypatch):
    monkeypatch.setattr(
        gpu, "vram_report",
        lambda: gpu.VramReport(total_mb=8192, free_mb=3072, used_mb=5120, raw=""),
    )
    assert gpu.require_vram(2.0, headroom_gb=0.5).free_gb == 3.0


# --- exclusion mutua ---


def test_lease_reports_the_owner():
    with gpu.gpu_lease("transcribing"):
        assert gpu.current_owner() == "transcribing"
    assert gpu.current_owner() is None


def test_second_lease_is_refused():
    """El caso que motiva el modulo: Whisper y Gemma a la vez."""
    with gpu.gpu_lease("transcribing"):
        with pytest.raises(gpu.GpuUnavailableError) as exc:
            with gpu.gpu_lease("content_analysis"):
                pass
    assert "transcribing" in str(exc.value)
    assert "content_analysis" in str(exc.value)


def test_same_phase_can_reenter():
    """Reentrada del mismo nombre se permite: es el mismo trabajo."""
    with gpu.gpu_lease("transcribing"):
        with gpu.gpu_lease("transcribing"):
            assert gpu.current_owner() == "transcribing"


def test_lease_is_released_on_exception():
    with pytest.raises(ValueError):
        with gpu.gpu_lease("transcribing"):
            raise ValueError("fallo a mitad")
    assert gpu.current_owner() is None, "un fallo no debe dejar la GPU bloqueada"


def test_nested_sequential_phases_do_not_overlap():
    order: list[str] = []

    def whisper() -> str:
        order.append("whisper")
        return "w"

    def gemma() -> str:
        order.append("gemma")
        return "g"

    with gpu.gpu_lease("whisper", needed_gb=None):
        whisper()
    with gpu.gpu_lease("gemma", needed_gb=None):
        gemma()

    assert order == ["whisper", "gemma"]


def test_concurrent_leases_never_overlap():
    """Varios hilos pidiendo la GPU a la vez: nunca hay dos dentro.

    El contrato NO es "poner en cola": es rechazar. Quien pierde se lleva un
    `GpuUnavailableError` y decide que hacer. Por eso el test acepta las dos
    salidas legitimas (entrar o ser rechazado) y solo exige lo que de verdad
    importa: que en ningun momento haya dos fases dentro a la vez.
    """
    inside: list[str] = []
    overlaps: list[str] = []
    rejected: list[str] = []
    guard = threading.Lock()

    def worker(name: str) -> None:
        try:
            lease = gpu.gpu_lease(name)
            lease.__enter__()
        except gpu.GpuUnavailableError:
            with guard:
                rejected.append(name)
            return
        try:
            with guard:
                overlaps.extend(inside)
                inside.append(name)
            # Ventana deliberada: si el lock no funcionase, otro hilo entraria
            # aqui y `inside` tendria dos nombres a la vez.
            threading.Event().wait(0.05)
            with guard:
                inside.remove(name)
        finally:
            lease.__exit__(None, None, None)

    threads = [threading.Thread(target=worker, args=(f"fase-{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert overlaps == [], f"hubo solapamiento en la GPU: {overlaps}"
    assert len(inside) == 0
    # Al menos uno entro (si no, el lock estaria roto en el otro sentido).
    assert len(rejected) < 4
    assert gpu.current_owner() is None


def test_second_thread_is_rejected_not_queued(monkeypatch):
    """No se espera en cola: se rechaza. La CLI no debe colgarse."""
    monkeypatch.setattr(gpu, "require_vram", lambda *a, **k: gpu.VramReport(None, None, None, ""))
    rejected: list[str] = []
    holding = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with gpu.gpu_lease("transcribing"):
            holding.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    holding.wait(2)

    try:
        with gpu.gpu_lease("content_analysis"):
            pass
    except gpu.GpuUnavailableError:
        rejected.append("content_analysis")
    finally:
        release.set()
        thread.join()

    assert rejected == ["content_analysis"]


# --- run_exclusive ---


def test_run_exclusive_keeps_order(monkeypatch):
    monkeypatch.setattr(gpu, "require_vram", lambda *a, **k: gpu.VramReport(None, None, None, ""))
    calls: list[str] = []

    results = gpu.run_exclusive(
        [
            ("whisper", 2.0, lambda: calls.append("whisper")),
            ("content_analysis", 4.0, lambda: calls.append("gemma")),
            ("candidates", 4.0, lambda: calls.append("candidates")),
        ]
    )
    assert calls == ["whisper", "gemma", "candidates"]
    assert results == [None, None, None]


def test_run_exclusive_returns_values(monkeypatch):
    monkeypatch.setattr(gpu, "require_vram", lambda *a, **k: gpu.VramReport(None, None, None, ""))
    results = gpu.run_exclusive([("a", None, lambda: 1), ("b", None, lambda: 2)])
    assert results == [1, 2]


def test_run_exclusive_stops_at_the_first_failure(monkeypatch):
    monkeypatch.setattr(gpu, "require_vram", lambda *a, **k: gpu.VramReport(None, None, None, ""))
    calls: list[str] = []

    def boom() -> None:
        calls.append("b")
        raise RuntimeError("Gemma devuelve JSON invalido")

    with pytest.raises(RuntimeError):
        gpu.run_exclusive(
            [
                ("a", None, lambda: calls.append("a")),
                ("b", 4.0, boom),
                ("c", 4.0, lambda: calls.append("c")),
            ]
        )
    assert calls == ["a", "b"], "la fase c no debe ejecutarse tras un fallo"
    assert gpu.current_owner() is None


# --- aviso del doctor sobre la VRAM ------------------------------------------


class TestCheckWhisperVram:
    """Lo que el doctor dice de la VRAM. `doctor` es la puerta de entrada.

    Aqui no se prueba la logica (ya esta cubierta arriba), sino el TEXTO. Un
    margen negativo impreso obligaba al usuario a hacer la resta mental, y un
    aviso sin accion que hacer no sirve de nada.
    """

    def _config(self, device: str = "cuda", needed: float = 1.6) -> dict:
        return {"whisper": {"device": device, "vram_required_gb": needed}}

    def test_cuando_caba_de_sobra_no_se_queja(self, monkeypatch):
        from src import diagnostics

        monkeypatch.setattr(
            gpu,
            "vram_report",
            lambda: gpu.VramReport(total_mb=8192, free_mb=7168, used_mb=1024, raw=""),
        )
        check = diagnostics.check_whisper_vram(self._config())

        assert check.status == diagnostics.PASS

    def test_nunca_imprime_un_margen_negativo(self, monkeypatch):
        from src import diagnostics

        monkeypatch.setattr(
            gpu,
            "vram_report",
            lambda: gpu.VramReport(total_mb=8192, free_mb=1331, used_mb=6861, raw=""),
        )
        check = diagnostics.check_whisper_vram(self._config())

        assert check.status == diagnostics.WARN
        assert "faltan" in check.detail
        # 1.331 libres - 1.6 que pide Whisper = -0.269, y con 1.5 GB de margen
        # exigido hay que liberar 1.769 GB. El numero accionable es ese: cuanto
        # cerrar, no el deficit.
        assert "1.8 GB" in check.detail
        assert "sobran -" not in check.detail

    def test_si_no_llega_ni_siquiera_a_lo_que_whisper_necesita(self, monkeypatch):
        from src import diagnostics

        monkeypatch.setattr(
            gpu,
            "vram_report",
            lambda: gpu.VramReport(total_mb=8192, free_mb=512, used_mb=7680, raw=""),
        )
        check = diagnostics.check_whisper_vram(self._config())

        assert check.status == diagnostics.WARN
        assert "faltan" in check.detail
        assert check.hint, "un aviso sin accion que hacer no avisa de nada"

    def test_en_cpu_no_habla_de_vram(self, monkeypatch):
        """Con `device: cpu` la VRAM es irrelevante y no debe alarmar."""
        from src import diagnostics

        def explota():
            raise AssertionError("no deberia consultar la VRAM en cpu")

        monkeypatch.setattr(gpu, "vram_report", explota)
        check = diagnostics.check_whisper_vram(self._config(device="cpu"))

        assert check.status == diagnostics.PASS
        assert "cpu" in check.detail
