"""Baselines protegidos: los 5 Shorts aprobados y sus manifiestos.

Los hashes estan fijados en codigo (no se leen del manifiesto nuevo) para que
cualquier modificacion de un entregable previo haga fallar la entrega. No se
tocan ni se reescriben estos archivos.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Iterable

# Valores medidos el 2026-10-29 en output/ (coinciden con los manifiestos).
PROTECTED_SHA256: Dict[str, str] = {
    "clip_0001_shorts_mvp_20261028.mp4":
        "EBBD2270DAC8A1D06FF82AB16F35F8E42E58BB9A1EEA14B32FFB05F2191D95DC",
    "clip_0001_shorts_mvp_20261028.manifest.json":
        "330D9A7C86E17304FB10ED64E151D2A360C01306A8545FFD7D3209E71B4226CE",
    "clip_0002_shorts_mvp_20261028.mp4":
        "8A45FFA4983E0705F2BDC9D761A35CE7339FFCBF8CF83CE58E0A20C85B09B311",
    "clip_0002_shorts_mvp_20261028.manifest.json":
        "D0F916FEC66D207E86E8B9E5903E4B56017BCE89B3C1D972959357D01B8AED98",
    "clip_0003_shorts_mvp_20261028.mp4":
        "089A99DEFE7CB2777FF470EFDD8FE15A68B8E5BC90B1874AD0D33547E4D8B723",
    "clip_0003_shorts_mvp_20261028.manifest.json":
        "46E11B876D4BA4B6BB6F7901417394A886FBD92034484BA32DC17EF5EB058F96",
    "clip_0004_shorts_mvp_20261028.mp4":
        "1362571FB103895D5548177B0429896CB5CC7A1AF3EC6F37AD0093C1458C2E22",
    "clip_0004_shorts_mvp_20261028.manifest.json":
        "545CA9EA76B50996B0B8FAA23E7E06796FE2DBC9332C56B7219F57012BE49029",
    "clip_0005_shorts_mvp_20261028.mp4":
        "183C6CFBAA4ED52993042BA8C3EA28A9142582F645CEB32E529A9362E5A0EF10",
    "clip_0005_shorts_mvp_20261028.manifest.json":
        "EB76D10C55D20D82C0B372E351A92E02EB1DBDB2D66B9DBC2FB088F335636AB9",
}

# Rangos reales de origen de los 5 Shorts (segundos de la fuente).
PROTECTED_RANGES = {
    "clip_0001": (526.791111, 584.949211),
    "clip_0002": (1099.488073, 1131.353240),
    "clip_0003": (780.770975, 829.352842),
    "clip_0004": (1131.485170, 1176.206168),
    "clip_0005": (1178.691338, 1228.382041),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def check_protected(output_dir: Path) -> Dict[str, object]:
    """Verifica los 5 MP4 y sus 5 manifiestos contra los hashes fijados."""
    output_dir = Path(output_dir)
    entries: Dict[str, dict] = {}
    all_ok = True
    for name, expected in PROTECTED_SHA256.items():
        path = output_dir / name
        if not path.exists():
            entries[name] = {"exists": False, "ok": False, "expected": expected}
            all_ok = False
            continue
        actual = sha256_file(path)
        ok = actual == expected
        entries[name] = {
            "exists": True,
            "bytes": path.stat().st_size,
            "sha256": actual,
            "expected": expected,
            "ok": ok,
        }
        all_ok = all_ok and ok
    return {"all_ok": all_ok, "files": entries}


def overlap_seconds(start: float, end: float) -> Dict[str, float]:
    """Solapamiento del rango dado con cada Short protegido."""
    overlaps: Dict[str, float] = {}
    for clip_id, (p0, p1) in PROTECTED_RANGES.items():
        overlap = max(0.0, min(end, p1) - max(start, p0))
        overlaps[clip_id] = round(overlap, 6)
    return overlaps


__all__ = [
    "PROTECTED_RANGES",
    "PROTECTED_SHA256",
    "check_protected",
    "overlap_seconds",
    "sha256_file",
]
