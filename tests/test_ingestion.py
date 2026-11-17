"""Pruebas de la Etapa A: ingesta de archivos locales (Fase 1).

Ejecutar con:
    python -m unittest discover -v

Las pruebas usan un runner falso que simula la salida de FFprobe, de
manera que no se depende de un video real salvo en la clase
`RealFFmpegTests`, que se omite si FFmpeg no esta instalado.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ingestion import (  # noqa: E402
    FFprobeFailedError,
    FFprobeNotFoundError,
    FFprobeTimeoutError,
    InsufficientSpaceError,
    InvalidSourceError,
    MetadataInvalidError,
    ProjectDirectoryConflictError,
    ProjectWriteError,
    SourceNotFoundError,
    compute_source_asset_id,
    default_input_dir,
    default_project_root,
    ingest_directory,
    ingest_file,
    list_source_files,
)
from services.ingestion import ingest as ingest_module  # noqa: E402
from services.ingestion.__main__ import main as cli_main  # noqa: E402
from services.ingestion.metadata import METADATA_KEYS  # noqa: E402
from schemas import Project  # noqa: E402

CONTENIDO = b"CONTENIDO-DEL-VIDEO-ORIGINAL-" * 64


def sample_probe(duration=8.0, *, with_audio=True, fps="30/1", size=1048576):
    streams = [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1280,
            "height": 720,
            "avg_frame_rate": fps,
            "r_frame_rate": fps,
        }
    ]
    if with_audio:
        streams.append(
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            }
        )
    return {
        "streams": streams,
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "format_long_name": "QuickTime / MOV",
            "duration": str(duration),
            "size": str(size),
            "bit_rate": "1048576",
        },
    }


class FakeRunner:
    def __init__(self, probe_payload=None, returncode=0, stderr="",
                 version="ffprobe version 8.1.1", stdout_override=None, error=None):
        self.probe_payload = probe_payload if probe_payload is not None else {}
        self.returncode = returncode
        self.stderr = stderr
        self.version = version
        self.stdout_override = stdout_override
        self.error = error
        self.calls = []

    def run(self, args):
        self.calls.append(list(args))
        if self.error is not None:
            raise self.error
        if args and args[0] == "-version":
            if self.stdout_override is not None:
                return self.returncode, self.stdout_override, self.stderr
            if self.returncode != 0:
                return self.returncode, "", self.stderr
            return 0, self.version, ""
        if self.returncode != 0:
            return self.returncode, "", self.stderr
        if self.stdout_override is not None:
            return 0, self.stdout_override, ""
        return 0, json.dumps(self.probe_payload), ""


class IngestionTestCase(unittest.TestCase):
    """Base: espacio temporal con un archivo fuente de nombre acentuado."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.project_root = self.root / "projects"
        self.source = self.root / "video de prueba (v1) y acentos é ñ.mp4"
        self.source.write_bytes(CONTENIDO)

    def ingest(self, runner=None, source=None, **kwargs):
        kwargs.setdefault("project_root", self.project_root)
        active_runner = (
            FakeRunner(probe_payload=sample_probe()) if runner is None else runner
        )
        return ingest_file(
            self.source if source is None else source, runner=active_runner, **kwargs
        )

    def published_dirs(self):
        if not self.project_root.is_dir():
            return []
        return [
            entry
            for entry in self.project_root.iterdir()
            if entry.is_dir() and not entry.name.startswith(".tmp-")
        ]

    def read_json(self, path):
        with open(str(path), encoding="utf-8") as handle:
            return json.load(handle)

    def assert_no_published_project(self):
        self.assertEqual(self.published_dirs(), [])
        if self.project_root.is_dir():
            self.assertEqual(list(self.project_root.glob("*/project.json")), [])


class IngestionExitosaTests(IngestionTestCase):
    def test_ingesta_exitosa_crea_los_dos_artefactos(self):
        result = self.ingest()

        self.assertTrue(result.created)
        project = result.project
        self.assertEqual(project.status, "ingested")
        self.assertTrue(project.source_asset_id.startswith("sha256-"))
        self.assertEqual(project.duration_seconds, 8.0)
        self.assertTrue(result.project_json_path.is_file())
        self.assertTrue(result.source_metadata_path.is_file())

        project_data = self.read_json(result.project_json_path)
        metadata_data = self.read_json(result.source_metadata_path)
        restored = Project.from_dict(project_data)

        self.assertEqual(restored.status, "ingested")
        self.assertEqual(restored.duration_seconds, 8.0)
        self.assertEqual(project_data["duration_seconds"], metadata_data["duration_seconds"])
        self.assertEqual(project_data["metadata"], metadata_data["metadata"])
        self.assertEqual(project_data["source_asset_id"], metadata_data["source_asset_id"])
        self.assertEqual(metadata_data["schema_version"], 1)
        self.assertEqual(metadata_data["warnings"], [])

        self.assertEqual(set(project_data["metadata"]), set(METADATA_KEYS))
        meta = project_data["metadata"]
        self.assertEqual(meta["width"], 1280)
        self.assertEqual(meta["height"], 720)
        self.assertEqual(meta["fps"], 30.0)
        self.assertEqual(meta["video_codec"], "h264")
        self.assertEqual(meta["audio_codec"], "aac")
        self.assertEqual(meta["size_bytes"], 1048576)
        self.assertEqual(meta["video_track_count"], 1)
        self.assertEqual(meta["audio_track_count"], 1)
        self.assertTrue(meta["has_audio"])
        self.assertEqual(meta["extension"], "mp4")

        self.assertEqual(project_data["tool_versions"]["ffprobe"], "8.1.1")
        self.assertIn("python", project_data["tool_versions"])
        self.assertEqual(metadata_data["tool_versions"], project_data["tool_versions"])
        self.assertTrue(metadata_data["streams"])
        self.assertIn("format_name", metadata_data["container"])

        self.assertTrue(Path(project_data["source_path"]).is_file())
        self.assertEqual(Path(project_data["source_path"]).name, self.source.name)

    def test_no_quedan_temporales_y_hay_un_solo_proyecto(self):
        self.ingest()
        entries = sorted(entry.name for entry in self.project_root.iterdir())
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].startswith(".tmp-"))

    def test_original_intacto(self):
        before = self.source.read_bytes()
        self.ingest()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), CONTENIDO)

    def test_opcionales_ausentes_como_null_o_cero(self):
        probe = sample_probe(with_audio=False, fps="0/0")
        probe["streams"][0].pop("avg_frame_rate")
        probe["streams"][0].pop("r_frame_rate")
        del probe["format"]["bit_rate"]
        result = self.ingest(runner=FakeRunner(probe_payload=probe))
        meta = result.project.metadata
        self.assertFalse(meta["has_audio"])
        self.assertEqual(meta["audio_track_count"], 0)
        self.assertIsNone(meta["audio_codec"])
        self.assertIsNone(meta["fps"])
        artifact = self.read_json(result.source_metadata_path)
        self.assertIn("sin_pista_de_audio", artifact["warnings"])
        self.assertEqual(result.project.status, "ingested")


class IdempotenciaTests(IngestionTestCase):
    def test_reintentar_no_duplica(self):
        first = self.ingest()
        second = self.ingest()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.project.project_id, second.project.project_id)
        self.assertEqual(len(self.published_dirs()), 1)
        self.assertEqual(
            first.project.source_asset_id, compute_source_asset_id(self.source)
        )

    def test_contenido_distinto_genera_un_proyecto_nuevo(self):
        first = self.ingest()
        self.source.write_bytes(CONTENIDO + b"-modificado")
        second = self.ingest()
        self.assertTrue(second.created)
        self.assertNotEqual(first.project.project_id, second.project.project_id)
        self.assertNotEqual(
            first.project.source_asset_id, second.project.source_asset_id
        )
        self.assertEqual(len(self.published_dirs()), 2)

    def test_mismo_contenido_en_dos_rutas_comparte_activo(self):
        copia = self.root / "copia de la fuente.mp4"
        copia.write_bytes(CONTENIDO)
        first = self.ingest()
        second = self.ingest(source=copia)
        self.assertEqual(
            first.project.source_asset_id, second.project.source_asset_id
        )
        self.assertEqual(len(self.published_dirs()), 1)


class ValidacionDeFuenteTests(IngestionTestCase):
    def test_fuente_inexistente(self):
        with self.assertRaises(SourceNotFoundError):
            self.ingest(source=self.root / "no existe.mp4")
        self.assertFalse(self.project_root.exists())

    def test_archivo_vacio(self):
        vacio = self.root / "vacio.mp4"
        vacio.write_bytes(b"")
        with self.assertRaises(InvalidSourceError) as ctx:
            self.ingest(source=vacio)
        self.assertIn("vacio", str(ctx.exception))
        self.assert_no_published_project()

    def test_directorio_como_fuente(self):
        carpeta = self.root / "carpeta"
        carpeta.mkdir()
        with self.assertRaises(InvalidSourceError):
            self.ingest(source=carpeta)

    def test_directorio_de_entrada_inexistente(self):
        with self.assertRaises(SourceNotFoundError):
            ingest_directory(self.root / "no-existe", project_root=self.project_root)


class FallosDeFFprobeTests(IngestionTestCase):
    def test_codigo_de_salida_distinto_de_cero(self):
        runner = FakeRunner(returncode=1, stderr="moov atom not found")
        with self.assertRaises(FFprobeFailedError) as ctx:
            self.ingest(runner=runner)
        self.assertIn("moov atom", str(ctx.exception))
        self.assert_no_published_project()

    def test_json_invalido(self):
        runner = FakeRunner(stdout_override="{esto no es json")
        with self.assertRaises(MetadataInvalidError):
            self.ingest(runner=runner)
        self.assert_no_published_project()

    def test_json_no_objeto(self):
        runner = FakeRunner(stdout_override="[1, 2, 3]")
        with self.assertRaises(MetadataInvalidError):
            self.ingest(runner=runner)
        self.assert_no_published_project()

    def test_timeout(self):
        runner = FakeRunner(error=FFprobeTimeoutError("FFprobe supero el tiempo de espera de 30 segundos"))
        with self.assertRaises(FFprobeTimeoutError):
            self.ingest(runner=runner)
        self.assert_no_published_project()

    def test_ffprobe_ausente(self):
        runner = FakeRunner(error=FFprobeNotFoundError("FFprobe no disponible"))
        with self.assertRaises(FFprobeNotFoundError):
            self.ingest(runner=runner)
        self.assert_no_published_project()

    def test_errores_quedan_registrados_en_log(self):
        runner = FakeRunner(returncode=1, stderr="archivo corrupto")
        with self.assertRaises(FFprobeFailedError):
            self.ingest(runner=runner)
        log_path = self.root / "logs" / "ingestion.log"
        self.assertTrue(log_path.is_file())
        contenido = log_path.read_text(encoding="utf-8")
        self.assertIn("FFprobeFailedError", contenido)
        self.assertIn("archivo corrupto", contenido)
        self.assertIn(self.source.name, contenido)


class MetadatosInvalidosTests(IngestionTestCase):
    def test_sin_pista_de_video(self):
        probe = sample_probe()
        probe["streams"] = [
            {"index": 0, "codec_type": "audio", "codec_name": "aac", "duration": "8.0"}
        ]
        with self.assertRaises(InvalidSourceError) as ctx:
            self.ingest(runner=FakeRunner(probe_payload=probe))
        self.assertIn("video", str(ctx.exception))
        self.assert_no_published_project()

    def test_sin_pistas(self):
        probe = {"streams": [], "format": {"duration": "8.0"}}
        with self.assertRaises(InvalidSourceError):
            self.ingest(runner=FakeRunner(probe_payload=probe))

    def test_sin_duracion_utilizable(self):
        probe = sample_probe()
        del probe["format"]["duration"]
        for stream in probe["streams"]:
            stream.pop("duration", None)
        with self.assertRaises(InvalidSourceError) as ctx:
            self.ingest(runner=FakeRunner(probe_payload=probe))
        self.assertIn("duracion", str(ctx.exception).lower())
        self.assert_no_published_project()

    def test_duracion_no_finita(self):
        probe = sample_probe()
        probe["format"]["duration"] = "nan"
        for stream in probe["streams"]:
            stream.pop("duration", None)
        with self.assertRaises(InvalidSourceError):
            self.ingest(runner=FakeRunner(probe_payload=probe))

    def test_duracion_cero(self):
        probe = sample_probe()
        probe["format"]["duration"] = "0"
        for stream in probe["streams"]:
            stream.pop("duration", None)
        with self.assertRaises(InvalidSourceError):
            self.ingest(runner=FakeRunner(probe_payload=probe))

    def test_formato_no_es_objeto(self):
        probe = {"streams": [{"index": 0, "codec_type": "video"}], "format": "no-es-objeto"}
        with self.assertRaises(MetadataInvalidError):
            self.ingest(runner=FakeRunner(probe_payload=probe))

    def test_formato_nulo_sin_duracion(self):
        probe = {"streams": [{"index": 0, "codec_type": "video"}], "format": None}
        with self.assertRaises(InvalidSourceError):
            self.ingest(runner=FakeRunner(probe_payload=probe))

    def test_streams_no_es_lista(self):
        probe = {"streams": {"0": {"codec_type": "video"}}, "format": {"duration": "8.0"}}
        with self.assertRaises(MetadataInvalidError):
            self.ingest(runner=FakeRunner(probe_payload=probe))


class ProcedenciaDeDirectoriosTests(IngestionTestCase):
    def test_uuid_sin_artefactos_se_conserva(self):
        conflictivo = self.project_root / str(uuid.uuid4())
        conflictivo.mkdir(parents=True)
        (conflictivo / "sentinela.txt").write_text("no borrar", encoding="utf-8")
        with self.assertRaises(ProjectDirectoryConflictError):
            self.ingest()
        self.assertTrue((conflictivo / "sentinela.txt").is_file())
        self.assertEqual(
            (conflictivo / "sentinela.txt").read_text(encoding="utf-8"), "no borrar"
        )
        self.assertEqual(len(list(self.project_root.iterdir())), 1)

    def test_duracion_incoherente_se_conserva(self):
        identificador = str(uuid.uuid4())
        conflictivo = self.project_root / identificador
        conflictivo.mkdir(parents=True)
        (conflictivo / "project.json").write_text(
            json.dumps(
                {
                    "project_id": identificador,
                    "source_asset_id": "sha256-" + "c" * 64,
                    "status": "ingested",
                    "created_at": "2026-10-15T12:00:00+00:00",
                    "source_path": str(self.source),
                    "duration_seconds": 3.0,
                    "metadata": {},
                    "tool_versions": {},
                }
            ),
            encoding="utf-8",
        )
        (conflictivo / "source_metadata.json").write_text(
            json.dumps({"duration_seconds": 9.0}), encoding="utf-8"
        )
        with self.assertRaises(ProjectDirectoryConflictError) as ctx:
            self.ingest()
        self.assertIn("duration_seconds", str(ctx.exception))
        self.assertTrue((conflictivo / "project.json").is_file())

    def test_nombre_de_directorio_no_reconocido(self):
        ajeno = self.project_root / "copias-de-seguridad"
        ajeno.mkdir(parents=True)
        (ajeno / "otra-cosa.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ProjectDirectoryConflictError):
            self.ingest()
        self.assertTrue((ajeno / "otra-cosa.txt").is_file())

    def test_residuo_temporal_ignorado(self):
        residuo = self.project_root / ".tmp-residuo-antiguo"
        residuo.mkdir(parents=True)
        (residuo / "a_medias.json").write_text("{}", encoding="utf-8")
        result = self.ingest()
        self.assertTrue(result.created)
        self.assertTrue(residuo.is_dir())
        self.assertEqual(len(self.published_dirs()), 1)


class EspacioYEscrituraTests(IngestionTestCase):
    def test_espacio_insuficiente(self):
        usage = namedtuple("usage", "total used free")(100, 50, ingest_module.MIN_FREE_BYTES - 1)
        with mock.patch.object(ingest_module.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(InsufficientSpaceError) as ctx:
                self.ingest()
        self.assertIn("Espacio libre insuficiente", str(ctx.exception))
        self.assert_no_published_project()

    def test_fallo_al_escribir_project_json_no_publica_el_proyecto(self):
        original = ingest_module._write_json_atomic

        def sabotaje(target, data):
            if Path(target).name == "project.json":
                raise ProjectWriteError("disco lleno (simulado)", path=target)
            original(target, data)

        with mock.patch.object(
            ingest_module, "_write_json_atomic", side_effect=sabotaje
        ):
            with self.assertRaises(ProjectWriteError):
                self.ingest()

        self.assert_no_published_project()

    def test_fallo_al_escribir_project_json_registra_el_error(self):
        original = ingest_module._write_json_atomic

        def sabotaje(target, data):
            if Path(target).name == "project.json":
                raise ProjectWriteError("disco lleno (simulado)", path=target)
            original(target, data)

        with mock.patch.object(
            ingest_module, "_write_json_atomic", side_effect=sabotaje
        ):
            with self.assertRaises(ProjectWriteError):
                self.ingest()
        log_path = self.root / "logs" / "ingestion.log"
        self.assertTrue(log_path.is_file())
        self.assertIn("ProjectWriteError", log_path.read_text(encoding="utf-8"))


class PathAwareRunner:
    """Runner falso que distingue archivos por el nombre de la ruta."""

    def __init__(self, corruptos=(), version="ffprobe version 8.1.1"):
        self.corruptos = tuple(corruptos)
        self.version = version
        self.calls = []

    def run(self, args):
        self.calls.append(list(args))
        if args and args[0] == "-version":
            return 0, self.version, ""
        ruta = args[-1]
        if any(marca in ruta for marca in self.corruptos):
            return 1, "", "formato desconocido"
        return 0, json.dumps(sample_probe()), ""


class DirectorioDeEntradaTests(IngestionTestCase):
    def test_list_source_files_omite_ocultos_y_subcarpetas(self):
        entrada = self.root / "entrada"
        entrada.mkdir()
        (entrada / "b video.mp4").write_bytes(CONTENIDO)
        (entrada / "a video.mp4").write_bytes(CONTENIDO)
        (entrada / ".oculto.mp4").write_bytes(CONTENIDO)
        (entrada / "subcarpeta.mp4").mkdir()
        names = [path.name for path in list_source_files(entrada)]
        self.assertEqual(names, ["a video.mp4", "b video.mp4"])

    def test_list_source_files_directorio_ausente(self):
        self.assertEqual(list_source_files(self.root / "no-existe"), [])

    def test_ingest_directory_procesa_el_lote_y_acumula_errores(self):
        entrada = self.root / "entrada con acentos ñ"
        entrada.mkdir()
        (entrada / "primero.mp4").write_bytes(CONTENIDO)
        (entrada / "segundo.mp4").write_bytes(CONTENIDO + b"-otro")
        malo = entrada / "dañado.mp4"
        malo.write_bytes(b"corrupto")

        resultado = ingest_directory(
            entrada,
            project_root=self.project_root,
            runner=PathAwareRunner(corruptos=("dañado",)),
        )

        self.assertEqual(len(resultado.results), 2)
        self.assertEqual(len(resultado.failures), 1)
        self.assertEqual(resultado.failures[0][0], malo)
        self.assertIsInstance(resultado.failures[0][1], FFprobeFailedError)
        self.assertEqual(len(self.published_dirs()), 2)
        self.assertEqual(
            {Path(item.project.source_path).name for item in resultado.results},
            {"primero.mp4", "segundo.mp4"},
        )

    def test_ingest_directory_lote_limpio(self):
        entrada = self.root / "entrada"
        entrada.mkdir()
        (entrada / "unico.mp4").write_bytes(CONTENIDO)
        resultado = ingest_directory(
            entrada,
            project_root=self.project_root,
            runner=FakeRunner(probe_payload=sample_probe()),
        )
        self.assertEqual(len(resultado.results), 1)
        self.assertEqual(resultado.failures, [])

    def test_ingest_directory_vacio(self):
        entrada = self.root / "vacia"
        entrada.mkdir()
        resultado = ingest_directory(
            entrada, project_root=self.project_root, runner=FakeRunner()
        )
        self.assertEqual(resultado.results, [])
        self.assertEqual(resultado.failures, [])

    def test_ingest_directory_ffprobe_ausente_re_propaga(self):
        entrada = self.root / "entrada"
        entrada.mkdir()
        (entrada / "uno.mp4").write_bytes(CONTENIDO)
        (entrada / "dos.mp4").write_bytes(CONTENIDO + b"x")
        runner = FakeRunner(error=FFprobeNotFoundError("FFprobe no disponible"))
        with self.assertRaises(FFprobeNotFoundError):
            ingest_directory(entrada, project_root=self.project_root, runner=runner)

    def test_ingest_directory_ruta_es_archivo(self):
        with self.assertRaises(InvalidSourceError):
            ingest_directory(self.source, project_root=self.project_root)


class UtilidadesTests(IngestionTestCase):
    def test_compute_source_asset_id_en_bloques(self):
        identificador = compute_source_asset_id(self.source, block_size=16)
        self.assertTrue(identificador.startswith("sha256-"))
        self.assertEqual(len(identificador), len("sha256-") + 64)
        self.assertEqual(identificador, compute_source_asset_id(self.source, block_size=4096))

    def test_rutas_por_defecto_del_repositorio(self):
        self.assertTrue(str(default_project_root()).endswith("projects"))
        self.assertTrue(str(default_input_dir()).endswith("input"))


class CliTests(IngestionTestCase):
    def test_cli_archivo_correcto(self):
        salida, error = io.StringIO(), io.StringIO()
        codigo = cli_main(
            [
                "--file", str(self.source),
                "--project-root", str(self.project_root),
            ],
            runner=FakeRunner(probe_payload=sample_probe()),
            stdout=salida,
            stderr=error,
        )
        self.assertEqual(codigo, 0)
        self.assertEqual(error.getvalue(), "")
        payload = json.loads(salida.getvalue())
        self.assertTrue(payload["created"])
        self.assertEqual(payload["status"], "ingested")
        self.assertEqual(payload["duration_seconds"], 8.0)
        self.assertTrue(Path(payload["project_json"]).is_file())

    def test_cli_error_sale_con_codigo_1(self):
        salida, error = io.StringIO(), io.StringIO()
        codigo = cli_main(
            [
                "--file", str(self.source),
                "--project-root", str(self.project_root),
            ],
            runner=FakeRunner(returncode=1, stderr="rotto"),
            stdout=salida,
            stderr=error,
        )
        self.assertEqual(codigo, 1)
        self.assertIn("ERROR", error.getvalue())

    def test_cli_directorio_con_fallos_parciales(self):
        entrada = self.root / "entrada"
        entrada.mkdir()
        (entrada / "bueno.mp4").write_bytes(CONTENIDO)
        (entrada / "dañado.mp4").write_bytes(CONTENIDO + b"z")
        salida, error = io.StringIO(), io.StringIO()
        codigo = cli_main(
            [
                "--input-dir", str(entrada),
                "--project-root", str(self.project_root),
            ],
            runner=PathAwareRunner(corruptos=("dañado",)),
            stdout=salida,
            stderr=error,
        )
        self.assertEqual(codigo, 1)
        payload = json.loads(salida.getvalue())
        self.assertEqual(len(payload), 1)
        self.assertIn("dañado.mp4", error.getvalue())
        self.assertIn("ERROR", error.getvalue())

    def test_cli_directorio_vacio(self):
        entrada = self.root / "vacia"
        entrada.mkdir()
        salida, error = io.StringIO(), io.StringIO()
        codigo = cli_main(
            [
                "--input-dir", str(entrada),
                "--project-root", str(self.project_root),
            ],
            runner=FakeRunner(),
            stdout=salida,
            stderr=error,
        )
        self.assertEqual(codigo, 0)
        self.assertIn("No se encontraron archivos", salida.getvalue())


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "requiere FFmpeg y FFprobe en el PATH")
class RealFFmpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls._temp.name) / "clip de prueba (v1) & ñ é.mp4"
        comando = [
            FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1.5",
            "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(cls.fixture),
        ]
        proc = subprocess.run(comando, capture_output=True, timeout=120, shell=False)
        if proc.returncode != 0 or not cls.fixture.is_file():
            raise unittest.SkipTest(
                "ffmpeg no pudo crear el archivo de prueba: "
                + proc.stderr.decode("utf-8", "replace")[-300:]
            )
        cls.fixture_bytes = cls.fixture.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_ingesta_con_ffprobe_real_y_nombre_acentuado(self):
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp) / "projects"
            result = ingest_file(self.fixture, project_root=project_root)

            self.assertTrue(result.created)
            self.assertEqual(result.project.status, "ingested")
            self.assertGreater(result.project.duration_seconds, 1.0)
            self.assertLess(result.project.duration_seconds, 3.0)
            meta = result.project.metadata
            self.assertEqual(meta["width"], 320)
            self.assertEqual(meta["height"], 240)
            self.assertFalse(meta["has_audio"])
            self.assertEqual(meta["extension"], "mp4")
            self.assertTrue(str(meta["size_bytes"]).isdigit())
            self.assertTrue(result.project.tool_versions["ffprobe"])

            artifact = json.loads(result.source_metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["duration_seconds"], result.project.duration_seconds)
            self.assertIn("sin_pista_de_audio", artifact["warnings"])

            self.assertEqual(self.fixture.read_bytes(), self.fixture_bytes)
            self.assertFalse([p for p in project_root.iterdir() if p.name.startswith(".tmp-")])

            repetido = ingest_file(self.fixture, project_root=project_root)
            self.assertFalse(repetido.created)
            self.assertEqual(repetido.project.project_id, result.project.project_id)
            self.assertEqual(len(list(project_root.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
