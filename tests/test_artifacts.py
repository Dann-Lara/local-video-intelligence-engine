"""Escritura atomica y validada de artefactos (`src/core/artifacts.py`).

Lo que se prueba aqui, y por que importa:

* Que un JSON invalido NUNCA llega a disco. Si `write_json` dejara el temporal
  con un nombre final, un schema roto contaminaria el workspace y el fallo
  apareceria tres etapas mas adelante.
* Que un temporal fallido no se queda tirado en el workspace.
* Que `ensure_ascii=False` se respeta: un transcript con acentos debe seguir
  siendo legible.
* Que la validacion combina schema E invariantes de negocio.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.core import artifacts, schemas


def _transcript(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "1.0",
        "job_id": "job_test",
        "source": {
            "audio_file": "audio.wav",
            "audio_sample_rate": 16000,
            "audio_channels": 1,
            "sha256": "a" * 64,
        },
        "language": "es",
        "language_probability": 0.98,
        "duration": 12.5,
        "generator": {"provider": "faster_whisper", "model": "tiny", "parameters": {}},
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.5, "text": "Hola mundo"},
        ],
    }
    base.update(overrides)
    return base


class TestWriteJson:
    def test_escribe_y_devuelve_sha256_del_contenido(self, tmp_path: Path) -> None:
        target = tmp_path / "transcript.json"
        document = _transcript()

        info = artifacts.write_json(target, document, schema_name="transcript")

        assert target.is_file()
        assert info.path == target
        assert info.size_bytes == target.stat().st_size
        assert len(info.sha256) == 64
        assert artifacts.read_json(target) == document

    def test_sha256_coincide_con_el_contenido_en_disco(self, tmp_path: Path) -> None:
        import hashlib

        target = tmp_path / "transcript.json"
        info = artifacts.write_json(target, _transcript(), schema_name="transcript")

        on_disk = hashlib.sha256(target.read_bytes()).hexdigest()
        assert info.sha256 == on_disk

    def test_no_deja_temporales_en_el_workspace(self, tmp_path: Path) -> None:
        artifacts.write_json(tmp_path / "transcript.json", _transcript(), schema_name="transcript")

        assert [p.name for p in tmp_path.iterdir()] == ["transcript.json"]

    def test_un_schema_invalido_no_escribe_nada(self, tmp_path: Path) -> None:
        target = tmp_path / "transcript.json"
        # Sin `segments`, que es required.
        roto = _transcript()
        del roto["segments"]

        with pytest.raises(artifacts.ArtifactInvalidError):
            artifacts.write_json(target, roto, schema_name="transcript")

        assert not target.exists()
        assert list(tmp_path.iterdir()) == []

    def test_informa_de_todos_los_errores_de_schema(self, tmp_path: Path) -> None:
        roto = _transcript()
        roto["language"] = 12345
        roto["schema_version"] = "9.9"

        with pytest.raises(artifacts.ArtifactInvalidError) as excinfo:
            artifacts.write_json(tmp_path / "t.json", roto, schema_name="transcript")

        mensaje = str(excinfo.value)
        assert "language" in mensaje
        assert "schema_version" in mensaje

    def test_rechaza_invariantes_de_negocio_no_expresables_en_schema(
        self, tmp_path: Path
    ) -> None:
        """Un segmento que termina antes de empezar es valido para JSON Schema.

        El tipo es number y el minimo 0, asi que `start: 5, end: 2` lo pasa sin
        problema. Lo tiene que parar `invariants`, que es justo lo que existe
        esa capa para no tener que duplicar reglas.
        """
        documento = _transcript(
            segments=[{"id": 0, "start": 5.0, "end": 2.0, "text": "Imposible"}]
        )
        assert schemas.is_valid("transcript", documento), "el schema no debe coger esto"

        with pytest.raises(artifacts.ArtifactInvalidError):
            artifacts.write_json(tmp_path / "t.json", documento, schema_name="transcript")

    def test_conserva_acentos_sin_escapar(self, tmp_path: Path) -> None:
        target = tmp_path / "transcript.json"
        documento = _transcript(
            segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "transcripción automática"}]
        )

        artifacts.write_json(target, documento, schema_name="transcript")

        crudo = target.read_text(encoding="utf-8")
        assert "transcripción automática" in crudo
        assert "\\u" not in crudo
        assert "�" not in crudo

    def test_acepta_texto_no_latino(self, tmp_path: Path) -> None:
        """El motivo de forzar UTF-8 en la consola: un transcript en japones."""
        target = tmp_path / "transcript.json"
        documento = _transcript(
            segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "こんにちは世界"}]
        )

        artifacts.write_json(target, documento, schema_name="transcript")

        leido = artifacts.read_json(target, schema_name="transcript")
        assert leido["segments"][0]["text"] == "こんにちは世界"

    def test_sobrescribe_por_defecto(self, tmp_path: Path) -> None:
        target = tmp_path / "transcript.json"
        artifacts.write_json(target, _transcript(), schema_name="transcript")
        artifacts.write_json(target, _transcript(language="en"), schema_name="transcript")

        assert artifacts.read_json(target)["language"] == "en"

    def test_overwrite_false_respeta_la_inmutabilidad(self, tmp_path: Path) -> None:
        target = tmp_path / "transcript.json"
        artifacts.write_json(target, _transcript(), schema_name="transcript")

        with pytest.raises(artifacts.ArtifactExistsError):
            artifacts.write_json(
                target, _transcript(language="en"), schema_name="transcript", overwrite=False
            )

        assert artifacts.read_json(target)["language"] == "es"

    def test_crea_el_directorio_padre(self, tmp_path: Path) -> None:
        target = tmp_path / "work" / "video" / "transcript.json"
        artifacts.write_json(target, _transcript(), schema_name="transcript")
        assert target.is_file()

    def test_una_excepcion_durante_la_escritura_no_deja_temporal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.core.artifacts as modulo

        def reventar(src: Any, dst: Any) -> None:
            raise OSError("disco lleno")

        monkeypatch.setattr(modulo.os, "replace", reventar)
        target = tmp_path / "transcript.json"

        with pytest.raises(OSError):
            artifacts.write_json(target, _transcript(), schema_name="transcript")

        assert list(tmp_path.iterdir()) == []


class TestReadJson:
    def test_falla_con_json_truncado(self, tmp_path: Path) -> None:
        target = tmp_path / "transcript.json"
        target.write_text('{"schema_version": "1.0", "segm', encoding="utf-8")

        with pytest.raises(artifacts.ArtifactError, match="JSON legible"):
            artifacts.read_json(target)

    def test_valida_al_leer_si_se_pide(self, tmp_path: Path) -> None:
        target = tmp_path / "transcript.json"
        target.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")

        with pytest.raises(artifacts.ArtifactInvalidError):
            artifacts.read_json(target, schema_name="transcript")

    def test_sin_validar_solo_lo_lee(self, tmp_path: Path) -> None:
        target = tmp_path / "libre.json"
        target.write_text(json.dumps({"cualquier": "cosa"}), encoding="utf-8")

        assert artifacts.read_json(target) == {"cualquier": "cosa"}

    def test_read_or_none_devuelve_none_si_no_existe(self, tmp_path: Path) -> None:
        assert artifacts.read_or_none(tmp_path / "nada.json") is None

    def test_read_or_none_no_traga_un_fichero_roto(self, tmp_path: Path) -> None:
        target = tmp_path / "transcript.json"
        target.write_text("basura", encoding="utf-8")

        with pytest.raises(artifacts.ArtifactError):
            artifacts.read_or_none(target)


def test_sha256_of_file_coincide_con_hashlib(tmp_path: Path) -> None:
    import hashlib

    target = tmp_path / "audio.wav"
    target.write_bytes(b"RIFF" + bytes(range(256)) * 40)

    assert artifacts.sha256_of_file(target) == hashlib.sha256(target.read_bytes()).hexdigest()
