"""Diagnostico del entorno (Sprint 0, tarea 0.3; blueprint seccion 73).

Comprueba automaticamente Python, Node, FFmpeg, FFprobe, Git, CUDA, driver
NVIDIA, LM Studio y los modelos ya descargados. Devuelve un codigo de salida
distinto de cero si hay un bloqueador, para poder encadenarlo en scripts.

    python main.py doctor
    python scripts/doctor.py
    python scripts/doctor.py --json

La logica vive aqui, no en `scripts/`, para que la CLI pueda reutilizarla sin
volver a implementarla.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # ejecucion directa del fichero
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import config as config_module  # noqa: E402
from src.core import schemas as schemas_module  # noqa: E402
from src.llm import client_from_config  # noqa: E402
from src.media import ffmpeg  # noqa: E402

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

MIN_PYTHON = (3, 12)
MIN_VRAM_GB = 4.0

# Capacidades que el pipeline necesita de verdad. OJO: `ffmpeg -version` solo
# lista flags de compilacion (--enable-nvenc), NO nombres de encoder. Los
# nombres hay que buscarlos en `-encoders`, o el check da falso negativo.
REQUIRED_BUILD_FLAGS = ("libx264", "libass", "libfreetype")
REQUIRED_ENCODERS = ("h264_nvenc", "aac")


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""

    @property
    def blocking(self) -> bool:
        return self.status == FAIL


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str, hint: str = "") -> None:
        self.checks.append(Check(name, status, detail, hint))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    @property
    def blocking(self) -> bool:
        return bool(self.failures)


# --- comprobaciones individuales ---


def check_python() -> Check:
    version = sys.version_info
    detail = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) < MIN_PYTHON:
        return Check(
            "Python",
            FAIL,
            f"{detail} (se requiere {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)",
            "uv venv --python 3.12 && uv pip install -r requirements.txt",
        )
    return Check("Python", PASS, f"{detail} {sys.executable}")


def check_ffmpeg() -> Check:
    """Comprueba flags de compilacion Y encoders disponibles, por separado."""
    path = ffmpeg.which("ffmpeg")
    if path is None:
        return Check(
            "FFmpeg",
            FAIL,
            "no encontrado en PATH",
            "Descarga la build full de ffmpeg (gyan.dev) y anadela al PATH",
        )
    try:
        version = ffmpeg.run([path, "-hide_banner", "-version"], timeout=30)
    except ffmpeg.CommandFailedError as exc:
        return Check("FFmpeg", FAIL, f"presente pero no responde: {exc}")

    first_line = version.stdout.splitlines()[0] if version.stdout else "?"
    missing_flags = [flag for flag in REQUIRED_BUILD_FLAGS if flag not in version.stdout]
    if missing_flags:
        return Check(
            "FFmpeg",
            FAIL,
            f"{first_line} — sin flags: {', '.join(missing_flags)}",
            "Se necesita una build 'full'; gyan.dev incluye todas",
        )

    encoders = ffmpeg.run([path, "-hide_banner", "-encoders"], timeout=30).stdout
    missing_encoders = [name for name in REQUIRED_ENCODERS if name not in encoders]
    if missing_encoders:
        return Check(
            "FFmpeg",
            FAIL,
            f"{first_line} — sin encoders: {', '.join(missing_encoders)}",
            "h264_nvenc requiere GPU NVIDIA con driver; revisa que no sea una build minima",
        )

    return Check(
        "FFmpeg",
        PASS,
        f"{first_line} — flags y encoders OK ({', '.join(REQUIRED_ENCODERS)})",
    )


def check_ffprobe() -> Check:
    path = ffmpeg.which("ffprobe")
    if path is None:
        return Check("FFprobe", FAIL, "no encontrado en PATH", "Viene con la build de ffmpeg")
    try:
        version = ffmpeg.run([path, "-hide_banner", "-version"], timeout=30)
    except ffmpeg.CommandFailedError as exc:
        return Check("FFprobe", FAIL, f"presente pero no responde: {exc}")
    return Check("FFprobe", PASS, version.stdout.splitlines()[0])


def check_node() -> Check:
    node = ffmpeg.which("node")
    npm = ffmpeg.which("npm")
    if node is None:
        return Check(
            "Node.js",
            FAIL,
            "no encontrado en PATH",
            "Instala Node.js LTS (https://nodejs.org)",
        )
    try:
        node_version = ffmpeg.run([node, "--version"], timeout=20).stdout.strip()
        major = int(node_version.lstrip("v").split(".")[0])
    except (ffmpeg.CommandFailedError, ValueError) as exc:
        return Check("Node.js", FAIL, f"no responde: {exc}")

    detail = f"{node_version}" + (f", npm {ffmpeg.run([npm, '--version'], timeout=20).stdout.strip()}" if npm else ", npm AUSENTE")
    if npm is None:
        return Check("Node.js", WARN, detail, "npm es necesario para Remotion")
    if major < 18:
        return Check("Node.js", FAIL, f"{detail} (se requiere 18+)", "Actualiza Node.js")
    return Check("Node.js", PASS, detail)


def check_git() -> Check:
    path = ffmpeg.which("git")
    if path is None:
        return Check("Git", WARN, "no encontrado en PATH", "Se usa para trazabilidad de artefactos")
    try:
        version = ffmpeg.run([path, "--version"], timeout=20).stdout.splitlines()[0]
    except ffmpeg.CommandFailedError as exc:
        return Check("Git", FAIL, f"no responde: {exc}")
    return Check("Git", PASS, version)


def check_nvidia() -> Check:
    """Driver + VRAM. nvidia-smi no viene con todas las instalaciones."""
    smi = ffmpeg.which("nvidia-smi")
    if smi is None:
        return Check(
            "NVIDIA",
            FAIL,
            "nvidia-smi no encontrado",
            "Sin GPU no hay CUDA; Whisper y Gemma iran por CPU (muy lento)",
        )
    try:
        result = ffmpeg.run(
            [
                smi,
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            timeout=20,
        )
    except ffmpeg.CommandFailedError as exc:
        return Check("NVIDIA", FAIL, f"nvidia-smi fallo: {exc}")

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return Check("NVIDIA", FAIL, "nvidia-smi no reporto GPUs", "Revisa el driver")
    name, total_mb, used_mb = (part.strip() for part in lines[0].split(","))
    free_gb = (int(total_mb) - int(used_mb)) / 1024
    detail = f"{name}, {int(total_mb) / 1024:.1f} GB totales, {free_gb:.1f} GB libres"

    if free_gb < MIN_VRAM_GB:
        return Check(
            "NVIDIA",
            WARN,
            detail,
            f"Cierra juegos/navegadores para liberar VRAM. Whisper + Gemma necesitan ~{MIN_VRAM_GB:.0f} GB",
        )
    return Check("NVIDIA", PASS, detail)


def check_cuda_torch() -> Check:
    try:
        import torch
    except ImportError:
        return Check(
            "PyTorch",
            WARN,
            "no instalado en el venv",
            "faster-whisper no lo necesita (usa ctranslate2). Se requiere solo si usas openai-whisper",
        )
    if not torch.cuda.is_available():
        return Check("PyTorch CUDA", FAIL, f"torch {torch.__version__} sin CUDA disponible")
    device = torch.cuda.get_device_name(0)
    return Check("PyTorch CUDA", PASS, f"torch {torch.__version__}, {device}")


def check_ctranslate2() -> Check:
    try:
        import ctranslate2
    except ImportError:
        return Check("ctranslate2", FAIL, "no instalado", "uv pip install faster-whisper")
    try:
        count = ctranslate2.get_cuda_device_count()
    except Exception as exc:  # la libreria puede fallar al cargar la DLL
        return Check("ctranslate2", FAIL, f"error al consultar CUDA: {exc}")
    if count < 1:
        return Check("ctranslate2", WARN, f"{ctranslate2.__version__}, 0 GPUs CUDA detectadas")
    supported = ctranslate2.get_supported_compute_types("cuda")
    return Check(
        "ctranslate2",
        PASS,
        f"{ctranslate2.__version__}, {count} GPU(s), compute types: {', '.join(supported)}",
    )


def check_cuda_runtime(config: dict[str, Any]) -> Check:
    """Comprueba que ctranslate2 podrá DECODIFICAR, no solo que ve la GPU.

    `ctranslate2.get_cuda_device_count()` devuelve 1 con el driver instalado y
    el runtime de CUDA ausente: la GPU existe, pero cuBLAS no. Whisper carga el
    modelo sin problema y revienta al primer segmento con

        RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

    Este check existe para que ese fallo aparezca en el doctor y no a los 20
    minutos de transcribir. Solo es bloqueante si la configuracion pide CUDA.
    """
    from src.core import cuda as cuda_module
    from src.core.config import get

    device = str(get(config, "whisper.device", "cuda"))
    report = cuda_module.preflight(device)

    if report.is_usable:
        return Check("Runtime CUDA", PASS, report.summary())

    if device != "cuda":
        return Check(
            "Runtime CUDA",
            WARN,
            f"{report.summary()} — irrelevante: whisper.device='{device}'",
            "",
        )
    return Check(
        "Runtime CUDA",
        FAIL,
        f"whisper.device='cuda' pero {report.summary()}. "
        "ctranslate2 NO podra decodificar: el modelo cargara y fallara al primer segmento.",
        report.hint(),
    )


def check_whisper_vram(config: dict[str, Any]) -> Check:
    """Comprueba que la VRAM que exige Whisper cabe ahora mismo.

    Se informa del margen real en lugar de un minimo fijo: con 8 GB, lo que
    decide si esto falla es lo que haya abierto el usuario, no la GPU.
    """
    from src.core import gpu
    from src.core.config import get

    device = str(get(config, "whisper.device", "cuda"))
    if device != "cuda":
        return Check("VRAM para Whisper", PASS, f"whisper.device='{device}', no usa VRAM")

    needed = get(config, "whisper.vram_required_gb")
    vram = gpu.vram_report()
    if vram.free_gb is None:
        return Check("VRAM para Whisper", WARN, "no se puede consultar la VRAM")

    if needed is None:
        return Check("VRAM para Whisper", WARN, f"{vram.summary()}, 'whisper.vram_required_gb' sin definir")

    needed_gb = float(needed)
    margin = vram.free_gb - needed_gb
    detail = f"{vram.summary()}; Whisper necesita {needed_gb:.1f} GB (medido, no estimado)"
    if margin < gpu.DEFAULT_VRAM_HEADROOM_GB:
        # Un margen negativo no se imprime como tal. "sobran -0.3 GB" obliga al
        # usuario a hacer la resta mental; lo que importa es cuanto falta.
        faltan = gpu.DEFAULT_VRAM_HEADROOM_GB - margin
        situacion = (
            f"no llega: faltan {faltan:.1f} GB para el margen"
            if margin < 0
            else f"solo sobran {margin:.1f} GB de margen"
        )
        return Check(
            "VRAM para Whisper",
            WARN,
            f"{detail} — {situacion}",
            "Cierra el navegador o los juegos. El lease rechazara la transcripcion si no hay "
            f"al menos {gpu.DEFAULT_VRAM_HEADROOM_GB:.1f} GB de margen.",
        )
    return Check("VRAM para Whisper", PASS, detail)


def check_lm_studio(config: dict[str, Any]) -> Check:
    client = client_from_config(config)
    status = client.health(refresh=True)
    base_url = client.base_url

    if not status.reachable:
        return Check(
            "LM Studio",
            FAIL,
            f"sin respuesta en {base_url} ({status.error})",
            "Abre LM Studio > pestana Developer > Start Server (puerto 1234). "
            "El proceso puede estar abierto con el servidor apagado: autoStartOnLaunch esta en false.",
        )

    detail = f"{base_url} responde en {(status.latency_seconds or 0) * 1000:.0f} ms, {len(status.models)} modelo(s)"
    if not status.models:
        return Check("LM Studio", FAIL, f"{detail} — ninguno cargado", "Carga un modelo en el chat de LM Studio")

    try:
        resolved = client.resolve_model()
    except Exception as exc:
        return Check("LM Studio", FAIL, f"{detail} — {exc}")

    return Check("LM Studio", PASS, f"{detail} — modelo: {resolved}")


def check_whisper_models(config: dict[str, Any]) -> Check:
    """Busca el modelo en la cache de HuggingFace o en la de openai-whisper.

    Los nombres de cache de HF llevan el prefijo del repo y guiones:
    `models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`. Comparar contra
    el nombre desnudo (`large-v3-turbo`) falla, hay que normalizar a la vez
    con y sin guiones.
    """
    from src.core.config import get

    model = str(get(config, "whisper.model", "large-v3-turbo"))
    needle_plain = model.replace("-", "").lower()

    hub = Path.home() / ".cache" / "huggingface" / "hub"
    hf_hits: list[str] = []
    if hub.is_dir():
        for path in hub.iterdir():
            if not path.is_dir():
                continue
            slug = path.name.lower()
            # Normaliza a dos formas: con guiones y sin guiones.
            if model.lower() in slug or needle_plain in slug.replace("-", "").replace("--", ""):
                hf_hits.append(path.name)

    whisper_cache = Path.home() / ".cache" / "whisper"
    openai_hits = (
        sorted(p.stem for p in whisper_cache.glob("*.pt")) if whisper_cache.is_dir() else []
    )

    details = [f"faster-whisper en cache: {len(hf_hits)}"]
    if openai_hits:
        details.append(f"openai-whisper en cache: {', '.join(openai_hits)}")

    if hf_hits:
        return Check("Modelos Whisper", PASS, f"'{model}' disponible — {'; '.join(details)}")
    return Check(
        "Modelos Whisper",
        WARN,
        f"'{model}' no esta en cache — {'; '.join(details)}",
        "Se descargara en la primera transcripcion (~1.6 GB desde huggingface.co)",
    )


def check_remotion(config: dict[str, Any]) -> Check:
    remotion_dir = config_module.resolve_path(config, "remotion_dir")
    package_json = remotion_dir / "package.json"
    if not package_json.is_file():
        return Check(
            "Remotion",
            FAIL,
            f"sin package.json en {remotion_dir}",
            "cd remotion && npm install",
        )
    node_modules = remotion_dir / "node_modules"
    if not node_modules.is_dir():
        return Check("Remotion", FAIL, f"node_modules ausente en {remotion_dir}", "npm install")
    try:
        pkg = json.loads(package_json.read_text(encoding="utf-8"))
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    except (json.JSONDecodeError, OSError) as exc:
        return Check("Remotion", FAIL, f"package.json ilegible: {exc}")
    required = ["remotion", "@remotion/cli", "react", "react-dom"]
    missing = [r for r in required if r not in deps]
    if missing:
        return Check("Remotion", FAIL, f"faltan dependencias: {', '.join(missing)}", "npm install")
    versions = ", ".join(f"{r}@{deps[r]}" for r in required)
    return Check("Remotion", PASS, versions)


def check_schemas() -> Check:
    try:
        available = schemas_module.available()
    except Exception as exc:
        return Check("Schemas", FAIL, f"no cargan: {exc}")
    if len(available) < 6:
        return Check("Schemas", FAIL, f"solo {len(available)} schemas: {available}")
    return Check("Schemas", PASS, f"{len(available)} validos: {', '.join(available)}")


def check_path_health() -> Check:
    root = config_module.default_project_root()
    text = str(root)
    problems = []
    if " " in text:
        problems.append("contiene espacios")
    if any(ord(ch) > 127 for ch in text):
        problems.append("contiene caracteres no-ASCII (acentos)")
    if not problems:
        return Check("Ruta del proyecto", PASS, text)

    ffmpeg_path = ffmpeg.which("ffmpeg")
    verified = False
    if ffmpeg_path:
        try:
            ffmpeg.run([ffmpeg_path, "-hide_banner", "-filters"], timeout=30)
            verified = True
        except ffmpeg.CommandFailedError:
            verified = False
    detail = f"{text} — {', '.join(problems)}"
    if verified:
        return Check(
            "Ruta del proyecto",
            PASS,
            f"{detail}; ffmpeg responde igual (tests/test_path_safety.py lo confirma)",
            "",
        )
    return Check(
        "Ruta del proyecto",
        WARN,
        detail,
        "Si algo falla, mueve los datos fuera: LVI_PATHS__WORK_ROOT=C:/lvi/work",
    )


def check_config() -> Check:
    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        return Check("Configuracion", FAIL, str(exc))
    if not config.get("llm", {}).get("base_url"):
        return Check("Configuracion", FAIL, "llm.base_url vacio (seccion 7: no hardcodear)")
    return Check("Configuracion", PASS, f"{len(config)} secciones desde {config_module.CONFIG_FILENAME}")


def check_disk() -> Check:
    root = config_module.default_project_root()
    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        return Check("Disco", WARN, f"no consultable: {exc}")
    free_gb = usage.free / 1024**3
    if free_gb < 10:
        return Check("Disco", FAIL, f"{free_gb:.1f} GB libres", "Se necesitan >=10 GB para video temporal")
    if free_gb < 30:
        return Check("Disco", WARN, f"{free_gb:.1f} GB libres", "Margen ajustado para renders 1080x1920")
    return Check("Disco", PASS, f"{free_gb:.1f} GB libres en {usage.total / 1024**3:.0f} GB")


# --- orquestacion ---


def build_report(*, skip_llm: bool = False) -> tuple[Report, dict[str, Any]]:
    report = Report()
    try:
        config = config_module.load()
    except config_module.ConfigError as exc:
        report.add("Configuracion", FAIL, str(exc))
        # Sin configuracion no se pueden resolver rutas ni consultar el LLM.
        for check in (
            check_python(),
            check_ffmpeg(),
            check_ffprobe(),
        ):
            report.add(check.name, check.status, check.detail, check.hint)
        return report, {}

    report.add("Configuracion", PASS, f"{len(config)} secciones desde {config_module.CONFIG_FILENAME}")
    for check in (
        check_python(),
        check_ffmpeg(),
        check_ffprobe(),
        check_node(),
        check_git(),
        check_nvidia(),
        check_ctranslate2(),
        check_cuda_runtime(config),
        check_whisper_vram(config),
        check_cuda_torch(),
        check_schemas(),
        check_disk(),
        check_path_health(),
        check_whisper_models(config),
        check_remotion(config),
    ):
        report.add(check.name, check.status, check.detail, check.hint)

    if skip_llm:
        report.add("LM Studio", WARN, "omitido por --skip-llm")
    else:
        check = check_lm_studio(config)
        report.add(check.name, check.status, check.detail, check.hint)

    return report, config


def render_text(report: Report) -> str:
    icons = {PASS: "OK  ", WARN: "AVISO", FAIL: "FALLA"}
    width = max((len(c.name) for c in report.checks), default=10)
    lines = []
    for check in report.checks:
        lines.append(f"[{icons[check.status]}] {check.name.ljust(width)}  {check.detail}")
        if check.hint and check.status != PASS:
            lines.append(f"{' ' * (width + 8)}{check.hint}")
    failures = len(report.failures)
    warnings = len(report.warnings)
    lines.append("")
    if failures:
        lines.append(f"RESULTADO: {failures} bloqueador(es), {warnings} aviso(s). El pipeline no puede ejecutarse.")
    elif warnings:
        lines.append(f"RESULTADO: entorno operativo con {warnings} aviso(s).")
    else:
        lines.append("RESULTADO: entorno completo. Todo listo.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnostico del entorno")
    parser.add_argument("--json", action="store_true", help="salida en JSON")
    parser.add_argument("--skip-llm", action="store_true", help="no contactar a LM Studio")
    args = parser.parse_args(argv)

    report, _ = build_report(skip_llm=args.skip_llm)
    if args.json:
        print(
            json.dumps(
                {"checks": [asdict(c) for c in report.checks], "blocking": report.blocking},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_text(report))
    return 1 if report.blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
