# Blueprint v0.1 --- Local Video Studio con OpenCode

**Estado:** propuesta inicial para validación técnica\
**Objetivo:** construir un editor asistido por IA que procese videos
localmente y convierta material largo en clips cortos revisables, con
herramientas gratuitas y sin dependencia de APIs pagadas.

## 1. Resumen ejecutivo

El usuario coloca un video en `input/`. El sistema crea un proyecto,
analiza el archivo, extrae audio, transcribe el habla con timestamps,
detecta silencios y cambios de escena, y envía el material textual
estructurado a un modelo local de LM Studio. El modelo propone segmentos
editoriales; el usuario los revisa en un panel, ajusta los límites y
aprueba los clips. El sistema renderiza las selecciones aprobadas,
aplica subtítulos y gráficos, comprueba el resultado y guarda los
archivos finales en `output/`.

OpenCode se usa para desarrollar y mantener el código. **No forma parte
del pipeline de procesamiento en tiempo de ejecución.** No se utilizará
Claude Code.

## 2. Objetivos y límites

### Objetivos de la primera versión (MVP)

1.  Ingestar un archivo de video local.
2.  Extraer metadatos técnicos con FFprobe.
3.  Extraer audio con FFmpeg.
4.  Transcribir con `faster-whisper` y guardar segmentos con timestamps;
    conservar timestamps por palabra cuando el modelo/configuración los
    entregue.
5.  Detectar silencios y, de forma opcional, cambios de escena.
6.  Preparar un paquete de contexto para un modelo local de LM Studio.
7.  Proponer clips semánticamente completos con tiempos vinculados a la
    fuente.
8.  Permitir revisar, reproducir, editar, aprobar y descartar propuestas
    desde una interfaz local.
9.  Renderizar un clip aprobado con subtítulos legibles.
10. Guardar estado, logs y artefactos para reanudar trabajos
    interrumpidos.

### No objetivos del MVP

-   Publicar automáticamente en TikTok, YouTube, Instagram o Facebook.
-   Descargar videos de plataformas.
-   Usar servicios de IA en la nube como requisito.
-   Generar escenas de video mediante modelos generativos.
-   Clonar voces o generar narración sintética.
-   Garantizar viralidad, monetización o rendimiento de un clip.
-   Procesar muchos videos en paralelo antes de medir el rendimiento
    real.
-   Reemplazar un editor profesional con una línea de tiempo multipista
    completa.

## 3. Principios de diseño

-   **Local-first:** datos y procesamiento permanecen en la máquina.
-   **Humano en el circuito:** la IA recomienda, el usuario decide.
-   **Transparencia:** cada clip se puede rastrear hasta el archivo y
    los tiempos fuente.
-   **Segmentación semántica:** cortar por ideas completas, no por una
    cuota fija de clips.
-   **Tolerancia a fallos:** resultados parciales se guardan y se pueden
    reutilizar.
-   **Modularidad:** cada herramienta se comunica mediante archivos y
    contratos definidos.
-   **Costo controlado:** sin dependencias pagadas obligatorias.
-   **Calidad por encima de cantidad:** aceptar que un video puede
    producir pocos clips o ninguno.

## 4. Arquitectura lógica

``` text
input/
  │
  ▼
[Ingesta y validación]
  │  archivo, duración, pistas, resolución, hash
  ▼
[FFprobe + FFmpeg]
  ├── metadatos
  ├── audio de trabajo
  └── miniaturas / muestras
  │
  ├──────────────► [PySceneDetect: escenas, opcional]
  │
  ▼
[faster-whisper local]
  └── transcripción + timestamps
  │
  ▼
[Paquete de análisis normalizado]
  │
  ▼
[LM Studio local]
  └── propuestas editoriales en JSON
  │
  ▼
[Validación de esquema y timestamps]
  │
  ▼
[Panel local de revisión]
  ├── reproducir fuente
  ├── ajustar entrada/salida
  ├── editar títulos
  ├── revisar subtítulos
  └── aprobar / descartar
  │
  ▼
[Renderizador: FFmpeg + Remotion]
  │
  ▼
[Control de calidad]
  ├── duración y resolución
  ├── audio presente
  ├── subtítulos dentro del cuadro
  └── archivo reproducible
  │
  ▼
output/ + informe del proyecto
```

## 5. Stack tecnológico inicial

  -----------------------------------------------------------------------
  Área              Herramienta       Responsabilidad   Observaciones
  ----------------- ----------------- ----------------- -----------------
  Desarrollo        OpenCode          Leer, crear,      No requiere
                                      probar y mantener Claude Code
                                      el repositorio    

  Backend           Python + FastAPI  API local y       Empezar con una
                                      orquestación      aplicación
                                                        modular

  Frontend          React +           Panel de revisión No construir una
                    TypeScript                          línea de tiempo
                                                        compleja al
                                                        inicio

  Estado            SQLite            Proyectos,        Sin servidor de
                                      tareas, clips,    base de datos
                                      aprobaciones      separado

  Metadatos y       FFmpeg / FFprobe  Inspección,       Verificar
  medios                              extracción,       instalación local
                                      cortes y          
                                      exportación       

  Transcripción     faster-whisper    Transcripción     Elegir modelo
                                      local con         según hardware
                                      timestamps        

  Escenas           PySceneDetect     Cambios de escena Opcional en el
                                                        primer hito

  LLM local         LM Studio         Propuestas        Usar endpoint
                                      editoriales       local compatible;
                                      estructuradas     no enviar datos
                                                        fuera

  Render de         Remotion          Composiciones     Revisar licencia
  gráficos                            reutilizables en  vigente para el
                                      React             uso previsto

  Pruebas           pytest y pruebas  Validación        Añadir desde los
                    frontend          automatizada      primeros módulos
  -----------------------------------------------------------------------

**Decisión provisional:** usar FFmpeg para cortes y tareas de medios y
Remotion para las composiciones gráficas y subtítulos animados.
Hyperframes queda como opción experimental posterior, no como
dependencia obligatoria. Brag puede inspirar el flujo de
brief/storyboard, pero no es necesario para el pipeline central.

El PDF de referencia describe Brag como herramienta para videos de
lanzamiento muy cortos, Hyperframes para motion graphics HTML/CSS/GSAP y
Remotion para composiciones de video basadas en React. Para este
proyecto, lo importante es adaptar la idea de storyboard, control de
escenas, revisión visual y composiciones reutilizables; no depender de
comandos o instalaciones específicos de Claude.

## 6. Pipeline detallado

### Etapa A --- Ingesta

**Entrada:** archivo ubicado en `input/` o seleccionado en el panel.

Acciones: - Detectar el archivo sin mover ni modificar el original. -
Validar extensión, existencia, legibilidad y espacio libre. - Obtener
metadatos con FFprobe. - Calcular un identificador estable del activo
(por ejemplo, hash del contenido cuando el costo sea razonable). - Crear
un proyecto y un directorio de trabajo. - Guardar nombre original,
tamaño, duración, resolución, FPS, codecs y pistas detectadas.

**Resultado:** registro de activo y proyecto con estado `ingested`.

### Etapa B --- Extracción y transcripción

Acciones: - Extraer audio de trabajo con FFmpeg. - Ejecutar
`faster-whisper` en local. - Guardar transcripción completa, idioma
detectado, segmentos y timestamps. - Guardar timestamps por palabra
cuando estén disponibles. - Registrar modelo, parámetros y versión de
herramienta. - No eliminar el audio de trabajo hasta verificar que el
proyecto ya no lo necesita.

**Resultado:** `transcript.json` y una representación legible en texto.

### Etapa C --- Análisis de silencios y escenas

Acciones: - Detectar rangos de silencio con FFmpeg. - Opcionalmente
detectar cambios de escena con PySceneDetect. - Crear miniaturas de
referencia en puntos relevantes. - Registrar los datos como pistas de
análisis, no como decisiones editoriales definitivas.

Los silencios no deben eliminarse de manera automática: pueden contener
pausas dramáticas, humor, respiración o énfasis.

### Etapa D --- Análisis editorial con LM Studio

Enviar al modelo local un paquete compacto que contenga: - Identificador
del proyecto y duración. - Transcripción segmentada y timestamps. -
Escenas y silencios detectados, si existen. - Objetivo de contenido y
perfil de exportación seleccionado. - Restricciones editoriales y
esquema JSON esperado.

Solicitar propuestas que incluyan: - `clip_id` - `start_seconds` -
`end_seconds` - `title` - `hook` - `main_idea` - `ending` -
`editorial_reason` - `transcript_refs` - `confidence` - `warnings`

Reglas: - El modelo no decide por sí solo que una propuesta es
correcta. - Los tiempos propuestos deben corresponder a límites
respaldados por la transcripción o a ajustes que luego pueda validar el
usuario. - Validar todos los tiempos contra la duración real del
video. - Si una respuesta no cumple el esquema, hacer un intento de
reparación limitado; si vuelve a fallar, marcar la tarea como fallida y
mostrar el error. - Si el contexto es demasiado grande, procesar
ventanas de transcripción con solapamiento y consolidar propuestas sin
duplicados. - No obligar a producir una cantidad fija de clips. - No
transformar texto parafraseado en cita textual.

### Etapa E --- Revisión humana

La primera interfaz debe permitir: - Ver la lista de propuestas y su
estado. - Reproducir el fragmento desde el video original. - Ajustar los
tiempos de inicio y fin. - Ver la transcripción alineada al tiempo. -
Editar título, gancho y texto destacado. - Elegir formato de salida. -
Aprobar, descartar o dejar pendiente cada propuesta. - Guardar cambios
sin perderlos al cerrar el navegador. - Volver a ejecutar solo la etapa
que cambió.

No se requiere una línea de tiempo multipista en el MVP. Un reproductor
con controles de tiempo y una lista de segmentos es suficiente para
validar el flujo.

### Etapa F --- Renderizado

Primera composición: - Video fuente recortado al rango aprobado. -
Subtítulos generados desde la transcripción con tiempos alineados. -
Estilo tipográfico configurable. - Título o gancho opcional. - Márgenes
seguros para interfaces móviles. - Audio preservado por defecto;
cualquier normalización debe ser configurable. - Fondo, color, fuente y
animación definidos en configuración, no codificados dentro de la lógica
de negocio.

Perfiles iniciales: - `vertical_9_16`: 1080 × 1920, si el hardware lo
permite. - `landscape_16_9`: 1920 × 1080 como opción posterior o
configurable. - Perfil de prueba de baja resolución para acelerar
desarrollo.

**No resolver formatos distintos recortando automáticamente el mismo
encuadre.** Cada composición debe considerar el área de interés y la
posición de subtítulos y títulos. El reencuadre automático avanzado
puede posponerse; en el MVP, permitir un encuadre configurable y revisar
el resultado.

### Etapa G --- Control de calidad

Antes de marcar una exportación como completa: - Confirmar que el
archivo existe y tiene tamaño mayor que cero. - Verificar que FFprobe
puede leerlo. - Confirmar duración esperada con tolerancia definida. -
Confirmar resolución, orientación y pista de audio según el perfil. -
Confirmar que los subtítulos no quedan fuera de los márgenes
configurados. - Guardar miniatura de revisión y log del render. -
Mostrar errores y permitir reintentar.

El control automático no sustituye la revisión visual del usuario.

## 7. Contratos de datos iniciales

### 7.1 Proyecto

``` json
{
  "project_id": "uuid",
  "source_asset_id": "uuid",
  "status": "transcribing",
  "created_at": "ISO-8601",
  "source_path": "ruta local",
  "duration_seconds": 1234.56,
  "metadata": {
    "width": 1920,
    "height": 1080,
    "fps": 29.97,
    "video_codec": "h264",
    "audio_codec": "aac"
  },
  "tool_versions": {}
}
```

Los valores son ilustrativos. Los datos técnicos reales deben provenir
de FFprobe, no de valores predeterminados.

### 7.2 Segmento de transcripción

``` json
{
  "segment_id": "seg-0001",
  "start_seconds": 12.34,
  "end_seconds": 15.67,
  "text": "Texto reconocido por el modelo",
  "words": [
    {
      "start_seconds": 12.34,
      "end_seconds": 12.78,
      "text": "Texto"
    }
  ]
}
```

`words` puede estar vacío si el modo de transcripción no entrega
timestamps por palabra. Nunca simular esa precisión.

### 7.3 Propuesta de clip

``` json
{
  "clip_id": "clip-0001",
  "source_asset_id": "uuid",
  "start_seconds": 12.34,
  "end_seconds": 48.2,
  "title": "Título editorial sugerido",
  "hook": "Gancho sugerido",
  "main_idea": "Idea principal respaldada por la fuente",
  "ending": "Cómo cierra el fragmento",
  "editorial_reason": "Por qué funciona como unidad independiente",
  "transcript_refs": ["seg-0001", "seg-0002"],
  "confidence": 0.82,
  "warnings": [],
  "review_status": "pending"
}
```

El valor `confidence` es una estimación del modelo, no una probabilidad
calibrada. La interfaz debe tratarlo como señal orientativa, no como
garantía.

### 7.4 Tarea

``` json
{
  "task_id": "uuid",
  "project_id": "uuid",
  "stage": "transcription",
  "status": "pending",
  "attempt_count": 0,
  "started_at": null,
  "finished_at": null,
  "error_code": null,
  "error_message": null,
  "artifact_paths": []
}
```

Estados válidos: `pending`, `running`, `succeeded`, `failed`,
`cancelled`.

## 8. Persistencia y organización de archivos

``` text
local-video-studio/
├── AGENTS.md
├── BLUEPRINT.md
├── README.md
├── .env.example
├── config/
│   ├── pipeline.yaml
│   ├── styles.yaml
│   └── export-presets.yaml
├── apps/
│   ├── api/
│   └── dashboard/
├── services/
│   ├── ingestion/
│   ├── transcription/
│   ├── scene_analysis/
│   ├── editorial/
│   ├── clip_planning/
│   ├── rendering/
│   ├── quality_control/
│   └── cleanup/
├── renderers/
│   └── remotion/
├── schemas/
├── tests/
├── input/
├── work/
├── projects/
├── output/
└── logs/
```

Por proyecto, conservar como mínimo: - `project.json` -
`source_metadata.json` - `transcript.json` - `silences.json` si se
calculó - `scenes.json` si se calculó - `clip_proposals.json` -
`review_decisions.json` - `render_manifest.json` - logs y miniaturas
necesarias para revisión

La base SQLite mantiene el estado consultable; los archivos JSON actúan
como artefactos auditables y permiten inspeccionar el proceso. Definir
una única fuente de verdad para cada dato y evitar duplicados
contradictorios.

## 9. Manejo de errores y recuperación

  -----------------------------------------------------------------------
  Situación                           Comportamiento esperado
  ----------------------------------- -----------------------------------
  Video ilegible                      Marcar ingesta como fallida;
                                      conservar original y log

  No hay audio                        Avisar y permitir cancelar o
                                      continuar solo con funciones
                                      visuales futuras

  Whisper falla                       Conservar audio extraído; permitir
                                      reintentar transcripción

  LM Studio no está disponible        Mantener transcripción y permitir
                                      revisión manual o reintentar

  JSON del modelo inválido            Validar, intentar reparación
                                      limitada y después mostrar error

  Render falla                        Conservar decisiones, configuración
                                      y logs; reintentar solo render

  Se cierra el panel                  Guardar decisiones en SQLite al
                                      confirmar cada cambio

  Espacio insuficiente                Detener de forma segura antes de
                                      operaciones que puedan dejar
                                      archivos incompletos

  Limpieza                            Borrar solo temporales conocidos y
                                      nunca fuentes, salidas aprobadas o
                                      estado requerido
  -----------------------------------------------------------------------

Cada tarea debe tener una clave de idempotencia o comprobación
equivalente para no repetir trabajo completado sin necesidad.

## 10. Configuración inicial

`config/pipeline.yaml` debe centralizar: - rutas base; - modelo y
parámetros de Whisper; - URL local de LM Studio; - identificador del
modelo cargado; - límites de contexto y tamaño de lote; - perfiles de
exportación; - concurrencia; - política de retención temporal; - rutas
de herramientas si no están en PATH.

`config/styles.yaml` debe centralizar fuentes, colores, tamaños,
márgenes seguros, animaciones y reglas de subtítulos.

`config/export-presets.yaml` debe definir resolución, relación de
aspecto, FPS, codec, bitrate/calidad y audio por perfil.

No codificar rutas absolutas específicas de una computadora dentro del
código fuente.

## 11. Fases de implementación

### Fase 0 --- Reconocimiento del entorno y contratos

-   Confirmar sistema operativo, Python, Node, FFmpeg y hardware.
-   Crear `AGENTS.md`, `BLUEPRINT.md`, `README.md` y `.env.example`.
-   Definir esquemas, estados y estructura mínima.
-   Crear pruebas de validación de tiempos y estados.

**Criterio de salida:** el repositorio tiene instrucciones, decisiones
explícitas y contratos iniciales probados.

### Fase 1 --- Ingesta y metadatos

-   API para crear proyecto desde un archivo local.
-   FFprobe para obtener metadatos.
-   Registro SQLite y artefactos del proyecto.
-   Manejo de errores de archivo y rutas con caracteres especiales.

**Criterio de salida:** un archivo válido crea un proyecto consultable y
no modifica el original.

### Fase 2 --- Transcripción local

-   Extracción de audio.
-   Integración con `faster-whisper`.
-   Persistencia de transcripción y timestamps.
-   Estado de tareas y reintentos.

**Criterio de salida:** una prueba corta se transcribe y se puede
inspeccionar sin conexión a la nube.

### Fase 3 --- Propuestas editoriales

-   Conector al servidor local de LM Studio.
-   Prompt versionado y salida JSON validada.
-   Validación temporal y referencias a segmentos fuente.
-   Modo manual si LM Studio no está disponible.

**Criterio de salida:** las propuestas se guardan y cada una se puede
rastrear a su transcripción.

### Fase 4 --- Panel de revisión

-   Lista de clips, reproductor, edición de límites y títulos.
-   Aprobación/rechazo y persistencia.
-   Estados visibles de tareas y errores.

**Criterio de salida:** el usuario puede preparar una selección de clips
sin modificar archivos a mano.

### Fase 5 --- Primer render

-   Composición base con video, audio y subtítulos.
-   Un perfil de salida vertical.
-   Pruebas de duración y reproducibilidad.
-   Revisión visual manual.

**Criterio de salida:** un clip aprobado genera un MP4 válido y
reproducible.

### Fase 6 --- Calidad y robustez

-   Detección opcional de escenas.
-   Reanudación, reintentos y limpieza segura.
-   Informes y miniaturas.
-   Perfil horizontal y ajustes visuales adicionales cuando el MVP esté
    estable.

**Criterio de salida:** el pipeline puede recuperarse de fallos comunes
sin perder decisiones ni resultados previos.

## 12. Estrategia de desarrollo con OpenCode

Trabajar una tarea por vez. Para cada tarea, pedir a OpenCode: 1. Leer
`AGENTS.md` y `BLUEPRINT.md`. 2. Inspeccionar el código actual antes de
proponer cambios. 3. Identificar el objetivo, archivos que tocará,
dependencias y riesgos. 4. Implementar la tarea mínima que cumpla el
criterio de salida. 5. Ejecutar las pruebas pertinentes. 6. Actualizar
documentación. 7. Resumir resultados reales, errores y siguiente paso.

No pedir "construye todo el sistema" en un solo prompt. No aceptar
afirmaciones de pruebas no ejecutadas. No permitir que el agente borre
originales o temporales fuera de `work/`.

### Primer prompt recomendado para OpenCode

``` text
Lee AGENTS.md y BLUEPRINT.md completos. No implementes todo el proyecto todavía.

Primero inspecciona el repositorio y el entorno disponible. Identifica qué existe, qué falta y qué herramientas están instaladas. No instales dependencias ni ejecutes comandos destructivos sin explicar antes su propósito.

Después implementa únicamente la Fase 0:
- estructura mínima del proyecto;
- esquemas iniciales para Project, TranscriptSegment, ClipProposal y Task;
- validación de timestamps y estados;
- pruebas automatizadas para esos contratos;
- README con instrucciones de ejecución local.

Antes de editar, presenta un plan breve con archivos a crear o modificar. Después ejecuta las pruebas disponibles y reporta exactamente cuáles pasaron y cuáles no pudieron ejecutarse. Respeta AGENTS.md y no uses servicios en la nube.
```

## 13. Riesgos y decisiones pendientes

1.  **Hardware:** el tamaño de Whisper y del modelo de LM Studio depende
    de RAM, GPU y VRAM. No fijar un modelo definitivo hasta conocer el
    equipo.
2.  **Rendimiento:** el primer objetivo es un solo proyecto y un render
    a la vez.
3.  **Licencias:** revisar las licencias vigentes de cada dependencia y
    de Remotion para el uso concreto antes de distribuir o monetizar la
    aplicación.
4.  **Reencuadre vertical:** el recorte automático inteligente requiere
    una fase aparte; un simple recorte central puede cortar rostros,
    textos u objetos importantes.
5.  **Calidad de transcripción:** ruido, música, acentos, varios
    hablantes y nombres propios pueden producir errores. La interfaz
    debe permitir corregir texto.
6.  **Contexto del LLM:** transcripciones largas deben dividirse en
    ventanas con solapamiento y consolidarse con validación.
7.  **Subtítulos:** no inventar precisión palabra por palabra si la
    transcripción solo proporciona timestamps por segmento.
8.  **Derechos de contenido:** procesar material propio o que el usuario
    tenga permiso para utilizar.

## 14. Criterio de éxito del MVP

La versión inicial se considera funcional cuando el usuario puede:

1.  Colocar un video local en `input/` o seleccionarlo desde el panel.
2.  Obtener metadatos y una transcripción con tiempos.
3.  Generar propuestas editoriales con un modelo local, o continuar en
    modo manual.
4.  Revisar y aprobar al menos una propuesta.
5.  Exportar un clip con subtítulos en MP4.
6.  Verificar el archivo resultante y volver a abrir el proyecto sin
    perder el estado.
7.  Reanudar una etapa fallida sin borrar el original ni repetir todas
    las etapas completadas.
