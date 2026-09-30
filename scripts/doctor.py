"""Punto de entrada deconveniente para el diagnostico del entorno.

La logica real esta en `src/diagnostics.py` porque la CLI (`python main.py
doctor`) reutiliza exactamente el mismo codigo. Este fichero existe para
poder escribir `python scripts/doctor.py`, que es la forma que usa la
documentacion de los tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.diagnostics import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
