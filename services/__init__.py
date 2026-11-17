"""Servicios de procesamiento local de Local Video Studio.

Cada paquete corresponde a una etapa documentada del pipeline en
AGENTS.md 5 / BLUEPRINT.md 8. Solo se crean los que tienen fase propia;
no se generan carpetas vacias para etapas futuras.
"""

# Asegurar que las DLL de CUDA sean localizables antes de cargar
# bibliotecas nativas (CTranslate2/faster-whisper).
try:
    from . import cuda_dll_init  # noqa: F401
except Exception as exc:
    import sys

    print(
        f"ADVERTENCIA: No se pudo inicializar la resolucion de DLL CUDA: {exc}",
        file=sys.stderr,
    )
    # No continuar silenciosamente: propagamos para hacer visible el fallo.
    raise
