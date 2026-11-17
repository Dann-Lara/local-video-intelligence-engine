# Local Video Studio

Aplicación local para convertir videos largos en clips cortos revisables y
exportables. La automatización propone; la persona revisa y aprueba antes
de exportar. Todo el procesamiento es **local-first**: los archivos,
transcripciones y proyectos permanecen en la computadora del usuario.

Este repositorio cubre las **Fases 0–3** del `BLUEPRINT.md`: contratos de
datos (Fase 0), ingesta local con FFprobe (Fase 1), transcripción local con
`faster-whisper` (Fase 2) y motor editorial de propuestas de clips con
LM Studio (Fase 3). Todavía no existe API, interfaz web, base de datos ni
renderizado.

## Estado actual

### Fase 0 — Contratos (terminada)

Implementado:

- Contratos de datos mínimos: `Project`, `TranscriptSegment` (con
  `Word`), `ClipProposal` y `Task`.
- Validación de límites temporales, duración del archivo fuente, estados
  permitidos y referencias a segmentos existentes.
- Rechazo explícito de `NaN`/`inf`, de colecciones mal tipadas (`None`,
  cadenas) y de fechas obligatorias vacías o inválidas.
- Serialización/deserialización (`to_dict` / `from_dict`).
- Pruebas automatizadas de esos contratos.
- Documentos `AGENTS.md`, `BLUEPRINT.md`, `README.md` y `.env.example`.

### Fase 1, Etapa A — Ingesta y metadatos (terminada)

Implementado en `services/ingestion/`:

- Validación de la fuente: existe, es un archivo regular, no está vacío y
  es legible.
- Comprobación de espacio libre en el volumen de destino.
- Ejecución segura de **FFprobe** (lista de argumentos, `shell=False`,
  `stdin=DEVNULL`, tiempo de espera configurable, captura de código de
  salida y `stderr`).
- Metadatos normalizados: duración, ancho/alto, FPS, códecs, tamaño,
  número de pistas, presencia de audio y extensión.
- Identificador estable del activo: `source_asset_id = "sha256-<hex>"` del
  contenido, calculado en bloques.
- Creación **idempotente** de proyectos: repetir la ingesta del mismo
  contenido devuelve el proyecto existente en vez de duplicarlo.
- Publicación atómica: `source_metadata.json` y `project.json` se escriben
  en `projects/.tmp-<uuid>/` y el directorio final aparece con un único
  renombrado, así que un fallo intermedio **nunca** deja un proyecto a
  medias.
- Registro de errores en `logs/ingestion.log` con clase, mensaje y ruta.
- El archivo original **nunca** se abre en escritura, se mueve ni se
  borra.

### Fase 2 — Transcripción local (terminada)

Implementado en `services/transcription/`:

- Transcripción local con **faster-whisper** (sin servicios en la nube),
  conservando segmentos y timestamps (los timestamps por palabra solo si
  la transcripción los entrega; nunca se simulan).
- Reutilización **solo con huella coincidente**: audio (SHA-256 del archivo
  realmente transcrito) + configuración efectiva + motor + identificadores
  (`services/transcription/fingerprint.py`).
- Un artefacto **legacy sin huella o incompleto nunca se acepta por
  inferencia**: se regenera. Se distingue explícitamente entre *inferencia
  incompleta* (resultado parcial, no reutilizable) y *cero segmentos con
  inferencia completada* (válido: audio sin voz).
- Persistencia atómica (`transcription.json`, `task_transcribe.json`) con
  validación estructural al leer.
- Lock exclusivo por etapa (`transcribe.lock`, pid + host + token) y
  reconciliación de tareas `running` abandonadas.
- Orden de persistencia: artefacto primero, `succeeded` después; nunca se
  marca `succeeded` sin artefacto escrito.

### Fase 3 — Motor editorial (implementada, sin inferencia real ejecutada)

Implementado en `services/editorial/` y `schemas/editorial.py`:

- **Contratos nuevos sin romper los previos:** `EditorialCandidate`
  **embebe** un `ClipProposal` (no se crea un contrato de clip paralelo) y
  añade duración calculada, evidencia literal, señales
  (`observed`/`inferred`/`unknown`), dependencia de contexto, calidad de
  límites, limitaciones y trazabilidad. `EditorialRun` es el sobre
  persistido en `candidates.json`; `WindowRecord` registra cada ventana.
- **Ventanas deterministas** de segmentos contiguos con solapamiento y
  contexto (`windows.py`): el cuerpo es elegible; el contexto nunca puede
  formar parte del clip. Un segmento gigante no se trunca jamás.
- **Cliente LM Studio** (`client.py`) compatible con la API de chat, usando
  solo `urllib`. No se ejecuta ninguna solicitud real en esta fase.
- **El modelo propone, el código valida** (`parse.py`): el modelo devuelve
  solo `segment_id` del cuerpo; el sistema calcula inicio/fin/duración,
  exige evidencia literal verificable, aplica la política de duración y
  registra cada descarte de forma auditable.
- **Deduplicación y ordenamiento deterministas** (`selection.py`): umbral
  de solapamiento temporal **y** de similitud de texto; el orden es
  prioridad de revisión, **no** una predicción de viralidad.
- **Idempotencia y recuperación** iguales a la Fase 2: huella
  (transcripción + config + prompt + modelo + ids), lock
  (`editorial.lock`), reconciliación de `running`, artefacto antes de
  `succeeded`.
- **Fallo de cualquier ventana ⇒ fallo del run completo:** no se escribe
  `candidates.json` y la tarea queda `failed`. **Cero candidatos tras un
  análisis exitoso sí es válido** (`succeeded` con lista vacía).
- `review_status` siempre `pending`: la etapa **nunca** aprueba nada.

No implementado todavía (fases posteriores): FastAPI, React, SQLite,
análisis de escenas con PySceneDetect, renderizado con Remotion y control
de calidad.


## Requisitos

- Python 3.11 o superior (probado con 3.11.9).
- Para los contratos, la ingesta, el motor editorial y sus pruebas: solo la
  biblioteca estándar (`dataclasses`, `enum`, `datetime`, `urllib`,
  `unittest`).
- Para la Etapa A (FFprobe): **FFmpeg/FFprobe** disponibles en el `PATH`.
  Las pruebas que necesitan FFprobe real se omiten automáticamente si no
  está instalado.
- Para la Fase 2 (transcripción): **faster-whisper** y un modelo local
  (por ejemplo `large-v3-turbo`) descargado de antemano. Las pruebas de
  transcripción usan un transcriptor falso y **no** cargan el modelo.
- Para la Fase 3 (editorial): un servidor local de **LM Studio** con un
  modelo cargado, escuchando por defecto en `http://127.0.0.1:1234/v1`
  (ver `.env.example`). Las pruebas editoriales usan un cliente falso y
  **no** contactan LM Studio.

Herramientas verificadas en el entorno de desarrollo:

- Node.js 24.x / npm 11.x (panel web futuro).
- FFmpeg / FFprobe 8.x (medios). En el entorno de prueba quedaron en
  `C:\Users\<usuario>\AppData\Local\Microsoft\WinGet\Links\` y se
  localizan por `PATH`.

## Estructura

```text
clipping/
├── AGENTS.md
├── BLUEPRINT.md
├── README.md
├── .env.example
├── input/                  # videos de entrada (nunca se modifican)
│   └── .gitkeep
├── projects/               # proyectos creados por la ingesta (bajo demanda)
├── logs/                   # logs de procesos externos (bajo demanda)
├── schemas/
│   ├── __init__.py
│   ├── contracts.py        # contratos y validaciones (Fase 0)
│   ├── validation.py       # validación contextual (Fase 1)
│   └── editorial.py        # contratos editoriales (Fase 3)
├── services/
│   ├── cuda_dll_init.py    # prepara DLLs de CUDA en el PATH del proceso
│   ├── ingestion/
│   │   ├── __init__.py     # API pública de la etapa
│   │   ├── __main__.py     # CLI: python -m services.ingestion
│   │   ├── errors.py       # jerarquía de errores
│   │   ├── ffprobe.py      # ejecución segura de FFprobe
│   │   ├── metadata.py     # normalización de metadatos
│   │   └── ingest.py       # orquestación e idempotencia
│   ├── transcription/
│   │   ├── __init__.py     # API pública de la etapa
│   │   ├── config.py       # configuración del transcriptor
│   │   ├── errors.py       # jerarquía de errores
│   │   ├── fingerprint.py  # huella de identidad (idempotencia)
│   │   ├── models.py       # TranscriptionResult
│   │   ├── orchestrator.py # lock, reutilización y recuperación
│   │   ├── store.py        # persistencia atómica
│   │   └── transcriber.py  # envoltura de faster-whisper
│   └── editorial/
│       ├── __init__.py     # API pública de la etapa
│       ├── client.py       # cliente LM Studio (urllib)
│       ├── config.py       # configuración del análisis
│       ├── errors.py       # jerarquía de errores
│       ├── fingerprint.py  # huella de identidad (idempotencia)
│       ├── orchestrator.py # flujo, lock y recuperación
│       ├── parse.py        # validación de la respuesta del modelo
│       ├── prompt.py       # prompt versionado
│       ├── selection.py    # deduplicación, orden y acotado
│       ├── store.py        # persistencia atómica
│       └── windows.py      # ventanas de análisis
└── tests/
    ├── __init__.py
    ├── test_contracts.py              # contratos (Fase 0)
    ├── test_ffprobe.py                # invocación segura de FFprobe
    ├── test_ingestion.py              # ingesta, idempotencia y fallos
    ├── test_validation_context.py     # validación contextual
    ├── test_transcription_idempotency.py  # transcripción (Fase 2)
    ├── test_editorial_windows.py      # ventanas y huella (Fase 3)
    ├── test_editorial_analysis.py     # parseo y selección (Fase 3)
    └── test_editorial_pipeline.py     # pipeline editorial (Fase 3)
```

`projects/` y `logs/` se crean solo cuando la ingesta los necesita
(CLI/API). No se crean carpetas vacías para etapas futuras.

## Ejecutar el proyecto localmente

### Pruebas (todas las fases)

Desde la raíz del repositorio:

```powershell
python -m unittest discover -v
```

Para una suite concreta:

```powershell
python -m unittest tests.test_contracts -v
python -m unittest tests.test_ffprobe -v
python -m unittest tests.test_ingestion -v
python -m unittest tests.test_validation_context -v
python -m unittest tests.test_transcription_idempotency -v
python -m unittest tests.test_editorial_windows -v
python -m unittest tests.test_editorial_analysis -v
python -m unittest tests.test_editorial_pipeline -v
```

### Ingesta (Etapa A)

Colocar los videos en `input/` (el archivo `input/.gitkeep` mantiene la
carpeta en Git; se ignoran los archivos que empiezan por punto y las
subcarpetas) y ejecutar:

```powershell
# Un archivo concreto
python -m services.ingestion --file "input\mi video con acentos é.mp4"

# Todo el directorio input/
python -m services.ingestion

# Todo un directorio concreto
python -m services.ingestion --input-dir "D:\videos a procesar"

# Opciones útiles
python -m services.ingestion --file "input\v.mp4" --project-root "D:\proyectos"
python -m services.ingestion --ffprobe "C:\ffmpeg\bin\ffprobe.exe" --timeout 60
```

La salida es un resumen JSON por proyecto y el código de salida es `0` si
todo fue bien y `1` si hubo al menos un error (el detalle se escribe en
`stderr` y en `logs/ingestion.log`).

Si FFprobe no está en el `PATH`, indicar la ruta con la opción `--ffprobe`
o con la variable de entorno `LVS_FFPROBE_PATH` (ver `.env.example`).

### Qué genera la ingesta

Por cada contenido nuevo se crea `projects/<uuid>/` con:

- `project.json`: el contrato `Project` con `status = "ingested"`.
- `source_metadata.json`: los datos crudos derivados de FFprobe (pistas,
  contenedor, duración, metadatos normalizados, versiones de herramientas
  y avisos como `sin_pista_de_audio`).

Repetir la ingesta del **mismo contenido** devuelve el proyecto existente
(`created: false`) sin duplicar nada. Si el directorio de un proyecto ya
existe pero no se puede demostrar su procedencia (falta `project.json`,
JSON ilegible o duraciones incoherentes), la ingesta **se detiene sin
borrar ni sobrescribir** y pide revisión manual.

Si se prefiere un entorno virtual (opcional, no requiere instalar nada
adicional porque no hay dependencias externas):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m unittest discover -v
```

### Analizar un proyecto (Fase 3, motor editorial)

Sobre un proyecto que ya tenga una transcripción válida, el motor editorial
construye ventanas, consulta al modelo local (LM Studio) y escribe las
propuestas para revisión humana. Desde código:

```python
from pathlib import Path
from services.editorial import EditorialConfig, analyze_project

run = analyze_project(
    Path("projects/<uuid>"),
    config=EditorialConfig(model_id="<modelo-cargado-en-lm-studio>"),
)
```

Produce `projects/<uuid>/editorial/` con:

- `candidates.json`: el sobre `EditorialRun` (huella de identidad,
  configuración efectiva, motor, ventanas procesadas, selección, descartes
  y candidatos propuestos). Cada candidato incluye su `ClipProposal` con
  `review_status = "pending"`.
- `task_editorial.json`: la `Task` de la etapa (`stage = "editorial"`).

Reglas: la reutilización exige huella coincidente; el fallo de cualquier
ventana hace fallar el run completo (no se escribe `candidates.json` y la
tarea queda `failed`); un análisis exitoso sin propuestas es válido
(`succeeded` con lista vacía). **Ninguna propuesta se aprueba
automáticamente.**

> Nota: en la entrega de la Fase 3 **no se ejecutó** ninguna inferencia real
> ni se cargó un modelo; el cliente de LM Studio y el flujo completo están
> implementados y cubiertos con un cliente falso.

## Contratos y validaciones

### Tiempos: tres conceptos distintos

1. **Duración del archivo fuente** (`Project.duration_seconds`): duración
   total del medio. Debe ser mayor que cero.
2. **Límites del segmento de transcripción** (`TranscriptSegment`):
   `start_seconds` / `end_seconds` propios del segmento. Los timestamps de
   sus `words` deben caer dentro de esos límites.
3. **Límites del clip** (`ClipProposal`): `start_seconds` / `end_seconds`
   del fragmento propuesto.

Regla común: `0 <= start_seconds < end_seconds`, y todos los campos
numéricos de tiempo, duración y `confidence` deben ser **finitos** (se
rechazan `NaN`, `inf` y `-inf`).

Los clips **pueden solaparse** entre sí; no se prohíbe ni se valida como
error.

### Validación contra la duración de la fuente: es explícita, aún no automática

**Qué se valida solo:**

- `0 <= start_seconds < end_seconds` → automático, se ejecuta en la
  construcción de `Word`, `TranscriptSegment` y `ClipProposal`.

**Qué se valida solo si alguien lo llama (no automático):**

- `end_seconds <= duración del archivo fuente` → mediante
  `validate_clip_against_source_duration` y
  `validate_segment_against_source_duration`, o a través de
  `ProjectContext` (ver abajo). El código de producción **sí** lo invoca:
  la transcripción (Fase 2) valida cada segmento y el motor editorial
  (Fase 3) valida cada candidato contra `ProjectContext`.

Motivo de que no sea automático en el contrato: `ClipProposal` y
`TranscriptSegment` **no duplican** `duration_seconds` (BLUEPRINT 7.2 y 7.3
no lo definen), así que el contrato no conoce ese límite y la comprobación
necesita el `Project`. El punto de entrada recomendado en código de
producción es `schemas/validation.py` (ver más abajo).

#### Validación contextual en la Fase 1 (implementada)

La Etapa A obtiene la duración real con FFprobe y la guarda en `Project`
(`duration_seconds`) y en `source_metadata.json`. Para que ningún servicio
olvide comparar contra esa duración, la Fase 1 añade `schemas/validation.py`:

- `ProjectContext.from_project(project, segments)`: objeto **efímero** que
  reúne el activo, la duración real y los `segment_id` existentes. No se
  persiste, no es un contrato y **no añade campos** a los contratos.
- `ProjectContext.validate_clip(clip)` / `validate_segment(segment)`: lanzan
  `ValidationError` con **todas** las incidencias juntas (tiempo, límite de
  duración, activo fuente y referencias a segmentos inexistentes).
- `validate_clip_for_project(...)` y `validate_segment_for_project(...)`:
  atajos equivalentes a construir el contexto y validar.
- `clip_errors(...)` / `segment_errors(...)`: devuelven la lista de
  incidencias sin lanzar excepción, útil para el panel.

Las funciones de la Fase 0 (`validate_clip_against_source_duration`,
`validate_segment_against_source_duration`, `validate_clip_transcript_refs`,
`validate_clip_source_asset`) siguen existiendo y se conservan; el contexto
las usa cuando corresponde en lugar de duplicarlas.

### Estados

- `Task.status`: `pending`, `running`, `succeeded`, `failed`, `cancelled`
  (BLUEPRINT.md 7.4).
- `ClipProposal.review_status`: `pending`, `approved`, `discarded`
  (derivado de la Etapa E: aprobar, descartar o dejar pendiente).
- `Task.stage`: etapas documentadas en los servicios del proyecto.
- `Project.status`: los documentos no enumeran un conjunto completo de
  estados (solo aparecen `ingested` y `transcribing` como ejemplos), por
  lo que se valida únicamente como texto no vacío. **No se han inventado
  estados.**

### Referencias

- `ClipProposal.transcript_refs` debe apuntar a segmentos existentes
  (`validate_clip_transcript_refs`).
- `ClipProposal.source_asset_id` debe coincidir con el activo fuente del
  proyecto (`validate_clip_source_asset`).

### Serialización

Cada contrato ofrece `to_dict()` y `from_dict()`. Los `enum` se serializan
como su valor de texto, de modo que el resultado es compatible con JSON.

## Reglas del proyecto

Se respetan los principios de `AGENTS.md`: local-first, sin costos
obligatorios, sin inventar contenido, no destructivo y errores visibles.
No se usan servicios de IA en la nube.

## Decisiones pendientes para fases siguientes

1. **Conjunto completo de estados de `Project.status`:** los documentos
   solo muestran ejemplos (`ingested`, `transcribing`) sin enumerar todos
   los estados. Por ahora `status` se valida solo como texto no vacío y la
   ingesta escribe `ingested`. Falta acordar la lista definitiva.
2. **Rango de `confidence`:** se valida `0 <= confidence <= 1` como
   decisión provisional. El BLUEPRINT aclara que es una estimación no
   calibrada; confirmar si debe admitir valores fuera de ese rango.
3. **Claves de `metadata`:** la Fase 1 ya escribe un conjunto estable
   (`METADATA_KEYS` en `services/ingestion/metadata.py`: dimensiones, FPS,
   códecs, tamaño, recuento de pistas, presencia de audio y extensión), pero
   `Project.metadata` sigue aceptando cualquier diccionario. Confirmar si
   esas claves pasan a ser obligatorias en el contrato.
4. **`tool_versions`:** la ingesta registra `ffprobe` y `python`. Falta
   decidir si se añaden versiones de las demás herramientas en fases
   posteriores.
5. **Timestamps por palabra dentro del segmento:** se exige que las
   palabras caigan dentro de los límites del segmento. Confirmar si se
   necesita una tolerancia por redondeo.
6. **Identificadores y formatos de fecha:** `project_id`, `source_asset_id`
   y `task_id` se tratan como texto no vacío. En la Fase 1, `project_id` es
   un UUID v4 y `source_asset_id` un `sha256-<hex>`; falta decidir si el
   contrato debe validar esos formatos y si `created_at` admite zona
   horaria obligatoria.
7. **Ubicación de `clip_proposals.json` (Fase 3):** el BLUEPRINT §8 lo sitúa
   en la **raíz** del proyecto, mientras que el resto de etapas agrupa sus
   artefactos en subcarpetas por etapa. La Fase 3 escribe
   `projects/<id>/editorial/candidates.json`. **Decisión pendiente:**
   confirmar la ruta definitiva (o generar también un `clip_proposals.json`
   de compatibilidad en la raíz).
8. **Modelo real de LM Studio (Fase 3):** `model_id` forma parte de la huella,
   pero su valor por defecto es un marcador (`modelo-local-de-ejemplo`,
   reutilizando `.env.example`). No se ejecutó inferencia real, así que el
   modelo y su contexto efectivo **no** se han confirmado. Debe fijarse el
   valor real (y la ventana de contexto) en una ejecución autorizada.
9. **Calibración de parámetros editoriales (Fase 3):** `window_target_chars`,
   `window_hard_max_chars`, `clip_min/target/max_seconds`, umbrales de
   deduplicación y límites de candidatos son **valores iniciales no
   calibrados**; no se ejecutó LM Studio para ajustarlos. Requieren
   validación con el modelo real.
10. **Timestamps por palabra en la fuente (Fase 3):** la transcripción actual
    no los incluye (`words` vacío), por lo que los límites de los clips son a
    nivel de segmento. Si en el futuro hay timestamps por palabra, podría
    refinarse el recorte; hoy no se simula precisión inexistente.
11. **Persistencia de ventanas:** las ventanas procesadas se registran dentro
    de `candidates.json` (`windows`), no en un archivo aparte. Confirmar si el
    panel necesitará un artefacto de ventanas independiente.
