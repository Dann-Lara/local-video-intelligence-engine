# AGENTS.md --- Local Video Studio

## 1. Propósito del proyecto

Construir una aplicación local para convertir videos largos en clips
cortos editables y exportables, utilizando herramientas gratuitas y
priorizando procesamiento local. El proyecto debe funcionar como un
flujo de trabajo asistido: la automatización propone; la persona revisa
y aprueba antes de producir las exportaciones finales.

El agente de desarrollo es **OpenCode**. No usar Claude Code ni depender
de funciones exclusivas de Claude.

## 2. Principios no negociables

1.  **Local-first:** el video, el audio, las transcripciones y los
    proyectos permanecen en la computadora del usuario. No subir
    archivos a servicios externos.
2.  **Sin costos obligatorios:** priorizar software gratuito y de código
    abierto. No introducir APIs pagadas, SaaS, claves comerciales ni
    servicios en la nube como requisito.
3.  **No inventar contenido:** nunca atribuir al video palabras, datos,
    hechos, nombres, cifras o conclusiones que no estén respaldados por
    el material fuente.
4.  **La fuente manda:** los tiempos de Whisper y los metadatos del
    medio son la referencia temporal. Un modelo de lenguaje puede
    proponer límites editoriales, pero no debe inventar timestamps.
5.  **Aprobación humana:** ningún clip se considera aprobado para
    exportación final hasta que el usuario lo apruebe en la interfaz o
    en un manifiesto explícito.
6.  **No destructivo:** nunca modificar ni borrar los originales. Nunca
    limpiar salidas aprobadas, configuraciones, transcripciones finales
    ni estado necesario para reanudar un trabajo.
7.  **Reanudable:** cada etapa debe guardar estado y resultados. Si una
    etapa falla, reintentarla sin repetir innecesariamente el trabajo
    anterior.
8.  **Trazabilidad:** cada clip debe enlazar al archivo fuente, los
    tiempos originales, la transcripción que lo respalda y la
    configuración usada para renderizarlo.
9.  **Determinismo:** con la misma fuente, configuración, versiones y
    decisiones editoriales, el sistema debe producir resultados
    reproducibles en la medida en que lo permitan las herramientas.
10. **Errores visibles:** los errores deben registrarse con contexto y
    mostrarse en el panel. No ocultarlos ni continuar silenciosamente
    con datos incompletos.

## 3. Flujo objetivo

1.  Detectar archivos nuevos en `input/` o permitir seleccionarlos desde
    el panel.
2.  Validar formato, duración, resolución, pistas de audio y espacio
    disponible.
3.  Crear un proyecto por archivo fuente.
4.  Extraer metadatos y audio mediante FFmpeg.
5.  Transcribir localmente con `faster-whisper`, conservando segmentos y
    timestamps de palabras cuando estén disponibles.
6.  Detectar silencios y cambios de escena con herramientas locales.
7.  Preparar un paquete de análisis con transcripción, tiempos, escenas
    y metadatos.
8.  Consultar el servidor local de LM Studio para proponer ideas y
    segmentos editoriales basados únicamente en ese paquete.
9.  Validar automáticamente el resultado del modelo contra el esquema y
    los tiempos reales.
10. Mostrar propuestas en un panel para escuchar/ver el fragmento,
    ajustar entrada y salida, editar títulos y subtítulos, seleccionar
    formato y aprobar o descartar.
11. Renderizar los clips aprobados usando una plantilla de composición.
12. Ejecutar controles de calidad y guardar el resultado.
13. Generar un informe del trabajo y sugerencias de metadatos. No
    publicar automáticamente en plataformas en la primera versión.

## 4. Stack previsto

Usar este stack como propuesta inicial, verificando versiones y
compatibilidad antes de fijarlas:

-   **OpenCode:** agente para desarrollar el repositorio.
-   **Python:** orquestación de tareas y servicios de procesamiento.
-   **FastAPI:** API local para el panel.
-   **React + TypeScript:** interfaz web local.
-   **SQLite:** estado de proyectos, tareas, clips y aprobaciones.
-   **FFmpeg/FFprobe:** lectura, extracción, cortes, normalización y
    exportación.
-   **faster-whisper:** transcripción local.
-   **PySceneDetect:** detección opcional de cambios de escena.
-   **LM Studio:** inferencia local mediante su servidor compatible con
    API.
-   **Remotion:** renderizado de composiciones de video reutilizables;
    comprobar los términos de licencia vigentes para el uso previsto
    antes de distribuir o comercializar.
-   **pytest** y herramientas equivalentes para pruebas.

No instalar todas las dependencias al inicio. Implementar por fases y
confirmar primero el sistema operativo, la RAM, la GPU/VRAM y el espacio
disponible. Si una herramienta no es compatible o excede los recursos,
proponer una alternativa local y documentar la decisión.

## 5. Estructura de carpetas

Respetar esta estructura salvo que exista una razón técnica documentada:

``` text
local-video-studio/
├── AGENTS.md
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

No es obligatorio crear toda la estructura vacía desde el primer commit.
Crear módulos cuando su fase comience.

## 6. Reglas editoriales

-   No asumir que un video debe producir una cantidad fija de clips.
-   Priorizar ideas completas, contexto suficiente y finales naturales.
-   Evitar cortes que interrumpan una frase, una explicación, una
    pregunta o una conclusión.
-   No recortar silencios automáticamente sin una opción de revisión; un
    silencio puede ser expresivo o necesario.
-   No convertir una paráfrasis del modelo en una cita textual.
-   Diferenciar claramente entre transcripción literal, título
    editorial, resumen y texto añadido.
-   Cada propuesta debe incluir razón editorial, tema, gancho sugerido,
    conclusión, timestamp de inicio y fin, nivel de confianza y
    advertencias.
-   Si el modelo no encuentra un segmento útil, aceptar cero propuestas.
-   Para videos de más de 30 segundos, trabajar con beats y segmentos
    semánticos; no usar una plantilla que presuponga un ritmo fijo.
-   Considerar un cambio visual, subtítulo destacado o elemento gráfico
    cada 3--5 segundos solo cuando ayude al contenido; no añadir
    estímulos arbitrarios.

## 7. Contratos de datos

-   Definir y versionar esquemas antes de conectar servicios.
-   Usar timestamps en segundos como números decimales y guardar también
    los tiempos originales de Whisper.
-   Todo clip debe tener `source_asset_id`, `start_seconds`,
    `end_seconds` y una referencia a su transcripción fuente.
-   Validar `0 <= start_seconds < end_seconds <= duración_fuente`.
-   Las palabras de subtítulos deben conservar sus timestamps cuando
    existan.
-   Los resultados del modelo deben pasar validación de esquema y
    validación temporal antes de llegar a la interfaz.
-   Las configuraciones de exportación deben estar versionadas y ser
    independientes del contenido editorial.

## 8. Arquitectura de procesamiento y estado

-   Representar cada etapa como una tarea con estado: `pending`,
    `running`, `succeeded`, `failed`, `cancelled`.
-   Guardar inicio, fin, duración, intentos, error resumido y rutas de
    artefactos.
-   Evitar que dos trabajadores procesen simultáneamente la misma etapa
    del mismo proyecto.
-   Diseñar operaciones idempotentes cuando sea posible.
-   Permitir reintentar una etapa fallida sin borrar los resultados
    previos válidos.
-   Mantener una cola simple y local en la primera versión; no
    introducir Redis, Kubernetes ni infraestructura distribuida sin
    necesidad demostrada.
-   No lanzar renders paralelos por defecto. La concurrencia debe
    configurarse según el hardware.

## 9. Errores y seguridad operativa

-   Capturar errores de procesos externos, códigos de salida, stderr y
    rutas implicadas.
-   Comprobar que los archivos existen y tienen tamaño razonable antes
    de procesarlos.
-   No interpolar nombres de archivo sin protección en comandos de
    shell; preferir argumentos estructurados de subprocess.
-   No exponer el servidor local a la red pública. Vincularlo a
    `127.0.0.1` por defecto.
-   No guardar secretos reales en el repositorio. `.env.example` solo
    contiene nombres y valores de ejemplo no sensibles.
-   No ejecutar comandos destructivos ni borrar carpetas de usuario.
-   La limpieza solo puede afectar artefactos temporales identificados
    dentro de `work/`, y únicamente tras verificar que el proyecto y sus
    salidas requeridas están completos.
-   Registrar las versiones de herramientas utilizadas en cada proyecto.

## 10. Calidad y pruebas

-   Probar la lógica de segmentación, validación de timestamps,
    transiciones de estado, limpieza y reanudación.
-   Usar fixtures pequeños de audio/video creados para pruebas o
    material que el usuario tenga derecho a procesar.
-   Probar nombres de archivo con espacios, acentos y caracteres
    especiales.
-   Verificar duración, resolución, orientación, presencia de audio y
    legibilidad de subtítulos después del render.
-   La interfaz debe mostrar errores recuperables y no perder ediciones
    cuando se recarga la página.
-   Añadir una prueba antes de corregir una regresión cuando sea
    práctico.

## 11. Forma de trabajar con OpenCode

Antes de editar: 1. Leer `AGENTS.md`, `README.md`, el blueprint y los
esquemas existentes. 2. Inspeccionar el repositorio y detectar el estado
real del proyecto. 3. Resumir el objetivo inmediato, los archivos que
cambiarán y los riesgos. 4. Trabajar en una fase pequeña y verificable.

Durante el trabajo: - No implementar todo el sistema en una sola
operación. - No reemplazar una arquitectura existente sin explicar por
qué. - No agregar dependencias sin explicar su propósito y costo
operativo. - No inventar resultados de comandos ni afirmar que las
pruebas pasaron si no se ejecutaron. - Mantener las instrucciones y
comentarios en español; nombres de código y API pueden estar en
inglés. - Actualizar la documentación cuando cambien contratos o
comandos.

Al terminar cada tarea: 1. Resumir los cambios realizados. 2. Enumerar
comandos ejecutados y resultados reales. 3. Indicar pruebas que pasaron
y las que faltan. 4. Informar riesgos, limitaciones y siguiente paso. 5.
Dejar el repositorio en un estado funcional y coherente.

## 12. Alcance de la primera versión

La primera versión debe poder: - Crear un proyecto desde un video
local. - Obtener metadatos y transcripción con timestamps. - Proponer
segmentos con LM Studio, o permitir trabajar sin esa integración si LM
Studio no está disponible. - Revisar y editar los segmentos desde una
interfaz local. - Renderizar al menos un clip aprobado con subtítulos. -
Guardar estado y reanudar tras un fallo.

Quedan fuera de la primera versión: publicación automática, cuentas de
redes sociales, servicios en la nube, colaboración multiusuario,
generación de voz, clonación de voz, generación de video con modelos y
automatización de monetización.
