"""Pruebas de la invocacion segura de FFprobe (Fase 1, Etapa A).

Ejecutar con:
    python -m unittest discover -v
"""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ingestion.errors import (  # noqa: E402
    FFprobeFailedError,
    FFprobeNotFoundError,
    FFprobeTimeoutError,
    IngestionError,
    MetadataInvalidError,
)
from services.ingestion.ffprobe import (  # noqa: E402
    DEFAULT_FFPROBE_EXECUTABLE,
    DEFAULT_TIMEOUT_SECONDS,
    FFprobeRunner,
    ffprobe_version,
    probe_media,
    resolve_ffprobe_executable,
    resolve_timeout,
)

RUTA_CON_ACENTOS = r"C:\videos\entrevista (v1) y acentos é ñ.mp4"


class FakeCompleted:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Runner de prueba: contesta sin crear procesos."""

    def __init__(self, probe_payload=None, returncode=0, stderr="", version="ffprobe version 8.1.1",
                 stdout_override=None):
        self.probe_payload = probe_payload if probe_payload is not None else {}
        self.returncode = returncode
        self.stderr = stderr
        self.version = version
        self.stdout_override = stdout_override
        self.calls = []

    def run(self, args):
        self.calls.append(list(args))
        if args and args[0] == "-version":
            if self.stdout_override is not None:
                return self.returncode, self.stdout_override, self.stderr
            if self.returncode != 0:
                return self.returncode, "", self.stderr
            return 0, self.version, ""
        if self.stdout_override is not None:
            return self.returncode, self.stdout_override, self.stderr
        if self.returncode != 0:
            return self.returncode, "", self.stderr
        return 0, json.dumps(self.probe_payload), self.stderr


class ResolveExecutableTests(unittest.TestCase):
    def test_argumento_explicito_tiene_prioridad(self):
        with mock.patch.dict(os.environ, {"LVS_FFPROBE_PATH": "desde-env"}):
            self.assertEqual(resolve_ffprobe_executable("explicito.exe"), "explicito.exe")

    def test_variable_de_entorno(self):
        with mock.patch.dict(os.environ, {"LVS_FFPROBE_PATH": "custom/ffprobe"}):
            self.assertEqual(resolve_ffprobe_executable(None), "custom/ffprobe")

    def test_valor_vacio_de_env_cae_al_por_defecto(self):
        with mock.patch.dict(os.environ, {"LVS_FFPROBE_PATH": "   "}):
            self.assertEqual(resolve_ffprobe_executable(None), DEFAULT_FFPROBE_EXECUTABLE)

    def test_por_defecto(self):
        env = os.environ.copy()
        env.pop("LVS_FFPROBE_PATH", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_ffprobe_executable(None), "ffprobe")


class ResolveTimeoutTests(unittest.TestCase):
    def test_explicito_tiene_prioridad(self):
        with mock.patch.dict(os.environ, {"LVS_FFPROBE_TIMEOUT": "5"}):
            self.assertEqual(resolve_timeout(12.5), 12.5)

    def test_desde_entorno(self):
        with mock.patch.dict(os.environ, {"LVS_FFPROBE_TIMEOUT": "7.5"}):
            self.assertEqual(resolve_timeout(None), 7.5)

    def test_por_defecto(self):
        env = os.environ.copy()
        env.pop("LVS_FFPROBE_TIMEOUT", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_timeout(None), DEFAULT_TIMEOUT_SECONDS)

    def test_valor_no_numerico_rechazado(self):
        for bad in ("no-numero", [], object()):
            with self.assertRaises(IngestionError):
                resolve_timeout(bad)

    def test_valor_no_positivo_rechazado(self):
        for bad in (0, -1, "0"):
            with self.assertRaises(IngestionError):
                resolve_timeout(bad)

    def test_env_invalida_rechazada(self):
        with mock.patch.dict(os.environ, {"LVS_FFPROBE_TIMEOUT": "abc"}):
            with self.assertRaises(IngestionError):
                resolve_timeout(None)


class RunnerTests(unittest.TestCase):
    def test_argumentos_como_lista_sin_shell(self):
        runner = FFprobeRunner("ffprobe.exe", 12.0)
        with mock.patch(
            "services.ingestion.ffprobe.subprocess.run",
            return_value=FakeCompleted(0, b"salida", b""),
        ) as run:
            returncode, stdout, stderr = runner.run(["-version", RUTA_CON_ACENTOS])
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, "salida")
        self.assertEqual(stderr, "")
        command = run.call_args[0][0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], "ffprobe.exe")
        self.assertEqual(command[-1], RUTA_CON_ACENTOS)
        kwargs = run.call_args[1]
        self.assertFalse(kwargs["shell"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 12.0)

    def test_stdout_bytes_se_decodifican(self):
        runner = FFprobeRunner()
        with mock.patch(
            "services.ingestion.ffprobe.subprocess.run",
            return_value=FakeCompleted(0, '{"a": 1}'.encode("utf-8"), b"aviso\n"),
        ):
            _, stdout, stderr = runner.run(["-version"])
        self.assertEqual(json.loads(stdout), {"a": 1})
        self.assertEqual(stderr, "aviso\n")

    def test_ejecutable_ausente(self):
        runner = FFprobeRunner("no-existe")
        with mock.patch(
            "services.ingestion.ffprobe.subprocess.run",
            side_effect=FileNotFoundError("no encontrado"),
        ):
            with self.assertRaises(FFprobeNotFoundError) as ctx:
                runner.run(["-version"])
        self.assertIn("no-existe", str(ctx.exception))
        self.assertEqual(ctx.exception.details["executable"], "no-existe")

    def test_timeout(self):
        runner = FFprobeRunner(timeout=1.5)
        expired = subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=1.5, output=b"parcial")
        with mock.patch(
            "services.ingestion.ffprobe.subprocess.run", side_effect=expired
        ):
            with self.assertRaises(FFprobeTimeoutError) as ctx:
                runner.run(["-show_format", RUTA_CON_ACENTOS])
        self.assertIn("1.5", str(ctx.exception))
        self.assertEqual(ctx.exception.details["timeout_seconds"], 1.5)
        self.assertEqual(ctx.exception.details["stdout_parcial"], "parcial")

    def test_error_de_sistema_operativo(self):
        runner = FFprobeRunner()
        with mock.patch(
            "services.ingestion.ffprobe.subprocess.run",
            side_effect=OSError("permiso denegado"),
        ):
            with self.assertRaises(FFprobeFailedError):
                runner.run(["-version"])


class ProbeMediaTests(unittest.TestCase):
    def test_json_valido(self):
        payload = {"streams": [], "format": {"duration": "3.5"}}
        runner = FakeRunner(probe_payload=payload)
        data = probe_media(runner, RUTA_CON_ACENTOS)
        self.assertEqual(data, payload)
        self.assertTrue(runner.calls[0][-1].endswith("é ñ.mp4"))

    def test_ruta_con_espacios_y_acentos_como_un_solo_argumento(self):
        runner = FakeRunner(probe_payload={})
        probe_media(runner, RUTA_CON_ACENTOS)
        args = runner.calls[0]
        self.assertEqual(len(args), len(["-v", "error", "-print_format", "json",
                                         "-show_format", "-show_streams", "ruta"]))
        self.assertEqual(args[-1], RUTA_CON_ACENTOS)

    def test_codigo_de_salida_distinto_de_cero(self):
        runner = FakeRunner(returncode=1, stderr="Invalid data found when processing input")
        with self.assertRaises(FFprobeFailedError) as ctx:
            probe_media(runner, RUTA_CON_ACENTOS)
        self.assertIn("Invalid data", str(ctx.exception))
        self.assertEqual(ctx.exception.details["returncode"], 1)

    def test_salida_vacia(self):
        runner = FakeRunner(stdout_override="")
        with self.assertRaises(MetadataInvalidError):
            probe_media(runner, "video.mp4")

    def test_json_invalido(self):
        runner = FakeRunner(stdout_override="esto no es { json")
        with self.assertRaises(MetadataInvalidError) as ctx:
            probe_media(runner, "video.mp4")
        self.assertIn("JSON invalido", str(ctx.exception))

    def test_json_no_objeto(self):
        runner = FakeRunner(stdout_override="[1, 2, 3]")
        with self.assertRaises(MetadataInvalidError):
            probe_media(runner, "video.mp4")


class VersionTests(unittest.TestCase):
    def test_version_extraida(self):
        runner = FakeRunner(version="ffprobe version 8.1.1-essentials_build-www.gyan.dev")
        self.assertEqual(ffprobe_version(runner), "8.1.1-essentials_build-www.gyan.dev")

    def test_sin_coincidencia_devuelve_primera_linea(self):
        runner = FakeRunner(version="version razonable")
        self.assertEqual(ffprobe_version(runner), "version razonable")

    def test_fallo_de_ejecucion(self):
        runner = FakeRunner(returncode=1, stderr="boom")
        with self.assertRaises(FFprobeFailedError):
            ffprobe_version(runner)

    def test_salida_vacia(self):
        runner = FakeRunner(stdout_override="   ")
        with self.assertRaises(FFprobeFailedError):
            ffprobe_version(runner)


if __name__ == "__main__":
    unittest.main()
