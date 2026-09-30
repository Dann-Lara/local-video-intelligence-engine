"""Runtime de CUDA para ctranslate2 (`src/core/cuda.py`).

Existe este modulo por un fallo concreto y reproducible:

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

Con el driver NVIDIA instalado y la GPU bien visible. El modelo de Whisper
CARGA sin problema y el fallo aparece al decodificar el primer segmento.

Lo que se comprueba:

* Que se finds las DLLs de las ruedas nvidia-* y se ampli(a) el PATH.
* Que `os.add_dll_directory()` por si solo NO basta, que es el detalle que hace
  perder una hora: se registra el directorio, pero ctranslate2 no lo consulta.
* Que los handles se guardan. En cuanto se basura un handle, el directorio se
  deregistra.
* Que el informe distingue "no hay nada" de "hay pero incompleto".
"""

from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path
from typing import Any

import pytest

from src.core import cuda


@pytest.fixture(autouse=True)
def _reset_module_state() -> Any:
    """Limpia el estado global del modulo, incluido el PATH del proceso.

    `ensure_cuda_dlls()` modifica `os.environ["PATH"]`, que es global al proceso
    y no lo restauran los fixtures de path. Sin restaurarlo, el directorio
    temporal de un test se cuela en el siguiente y las aserciones sobre el PATH
    empiezan a fallar por motivos que no tienen que ver con lo que se prueba.
    """
    path_original = os.environ.get("PATH")
    cuda._ensured = False
    cuda._handles.clear()
    cuda._added_paths.clear()
    yield
    if path_original is not None:
        os.environ["PATH"] = path_original
    cuda._ensured = False
    cuda._handles.clear()
    cuda._added_paths.clear()


def _crear_venv_falso(base: Path, paquetes: dict[str, list[str]]) -> Path:
    """Crea un site-packages con ruedas nvidia-* simuladas."""
    nvidia = base / "nvidia"
    for paquete, dlls in paquetes.items():
        bin_dir = nvidia / paquete / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for dll in dlls:
            (bin_dir / dll).write_bytes(b"MZ")
    return nvidia


@pytest.fixture
def site_falso(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Sustituye `sysconfig` para apuntar a un site-packages de mentira."""
    puro = tmp_path / "site-packages"
    puro.mkdir()

    class Falso:
        @staticmethod
        def get_paths() -> dict[str, str]:
            return {"purelib": str(puro), "platlib": str(puro)}

    monkeypatch.setattr(sysconfig, "get_paths", Falso.get_paths)
    return puro


class TestNvidiaBinDirs:
    def test_ignora_un_site_packages_vacio(self, site_falso: Path) -> None:
        assert cuda.nvidia_bin_dirs() == []

    def test_ignora_paquetes_nvidia_sin_dlls(self, site_falso: Path) -> None:
        (site_falso / "nvidia" / "cublas" / "bin").mkdir(parents=True)
        (site_falso / "nvidia" / "cublas" / "bin" / "LEEME.txt").write_text("x", encoding="utf-8")

        assert cuda.nvidia_bin_dirs() == []

    def test_ignora_paquetes_no_nvidia(self, site_falso: Path) -> None:
        bin_dir = site_falso / "torch" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "cublas64_12.dll").write_bytes(b"MZ")

        assert cuda.nvidia_bin_dirs() == []

    def test_amplia_el_path_del_proceso(self, site_falso: Path) -> None:
        _crear_venv_falso(site_falso, {"cublas": ["cublas64_12.dll", "cublasLt64_12.dll"]})
        original = os.environ.get("PATH", "")

        cuda.ensure_cuda_dlls(force=True)

        path = os.environ.get("PATH", "")
        assert "nvidia" in path
        assert path.endswith(original), "el PATH previo tiene que conservarse al final"

    def test_es_idempotente(self, site_falso: Path) -> None:
        _crear_venv_falso(site_falso, {"cublas": ["cublas64_12.dll"]})

        primera = cuda.ensure_cuda_dlls(force=True)
        tras_primera = os.environ["PATH"]
        segunda = cuda.ensure_cuda_dlls(force=True)

        assert primera.dll_paths == segunda.dll_paths
        assert os.environ["PATH"] == tras_primera, "una segunda llamada no debe alargar el PATH"

    @pytest.mark.skipif(sys.platform != "win32", reason="add_dll_directory es de Windows")
    def test_guarda_los_handles_de_add_dll_directory(self, site_falso: Path) -> None:
        """Si el handle se descarta, el directorio queda deregistrado."""
        _crear_venv_falso(site_falso, {"cublas": ["cublas64_12.dll"]})

        cuda.ensure_cuda_dlls(force=True)

        assert cuda._handles, "los handles deben guardarse en el modulo"
        assert all(handle is not None for handle in cuda._handles)


class TestCudaReport:
    def test_sin_dlls_no_es_utilizable(self) -> None:
        informe = cuda.CudaReport()

        assert informe.is_usable is False
        assert "sin ruedas" in informe.summary()
        assert "nvidia-cublas-cu12" in informe.hint()

    def test_con_cublas_pero_sin_cudnn_no_es_utilizable(self) -> None:
        informe = cuda.CudaReport(found={"cublas64_12.dll": [Path("x.dll")]})

        assert informe.is_usable is False
        assert "cuDNN" in informe.summary()

    def test_con_las_dos_es_utilizable(self) -> None:
        informe = cuda.CudaReport(
            dll_paths=[Path("bin")],
            found={"cublas64_12.dll": [Path("a")], "cudnn64_9.dll": [Path("b")]},
        )

        assert informe.is_usable is True
        assert informe.hint() == "", "si esta bien, no hay que dar pista de nada"
        assert "2 DLLs" in informe.summary()

    def test_acepta_las_alternativas_de_version(self) -> None:
        """El numero de version de cuBLAS depende de la version de CUDA."""
        informe = cuda.CudaReport(
            dll_paths=[Path("bin")],
            found={"cublas64_11.dll": [Path("a")], "cudnn64_8.dll": [Path("b")]},
        )

        assert informe.is_usable is True

    def test_el_hint_solo_aparece_si_falta_algo(self) -> None:
        bueno = cuda.CudaReport(
            dll_paths=[Path("bin")],
            found={"cublas64_12.dll": [Path("a")], "cudnn64_9.dll": [Path("b")]},
        )
        assert bueno.hint() == ""
        assert cuda.CudaReport().hint() != ""


class TestDeteccionDeFaltantes:
    def test_avisa_si_falta_cudnn_nunca_solo(self, site_falso: Path) -> None:
        _crear_venv_falso(site_falso, {"cublas": ["cublas64_12.dll"]})

        informe = cuda.ensure_cuda_dlls(force=True)

        assert informe.is_usable is False
        assert informe.missing == ["cudnn64_9.dll"]

    def test_no_avisa_si_estan_las_dos(self, site_falso: Path) -> None:
        _crear_venv_falso(
            site_falso, {"cublas": ["cublas64_12.dll"], "cudnn": ["cudnn64_9.dll", "cudnn_ops64_9.dll"]}
        )

        informe = cuda.ensure_cuda_dlls(force=True)

        assert informe.is_usable is True
        assert informe.missing == []


class TestEnEstaMaquina:
    """Comprobaciones del entorno real, que es donde se manifolds el bug."""

    def test_preflight_no_lanza(self) -> None:
        informe = cuda.preflight()
        assert isinstance(informe, cuda.CudaReport)

    @pytest.mark.cuda
    def test_cublas_esta_disponible_para_ctranslate2(self) -> None:
        """El test que habria destapado el fallo original.

        Si `is_usable()` dice que no, el doctor lo dice y el pipeline no llega
        nunca a la mitad de una transcripcion de 40 minutos.
        """
        informe = cuda.preflight()
        if not informe.is_usable:
            pytest.skip(f"runtime de CUDA no disponible en este equipo: {informe.summary()}")
