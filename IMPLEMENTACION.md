# yachaq-01 — Implementación en código (migración desde Azure Foundry)

Reimplementación del agente de tutoría YachAI como aplicación Python de
terminal, usando la API gratuita de Google Gemini.

## Puesta en marcha

```bash
pip install -r requirements.txt
copy .env.example .env      # y pega tu clave de https://aistudio.google.com/apikey
python precache.py          # pre-carga el banco offline (correr donde haya señal)
python main.py              # tutor interactivo (terminal)
python web_demo.py          # interfaz web del evento -> http://localhost:5000
python eval_quechua.py      # evaluación de calidad en quechua
```

Requiere Python 3.9+. Única dependencia externa: `requests`.

## Estructura

```
main.py                  CLI del tutor (sesión interactiva por estudiante)
web_demo.py              Interfaz web del evento (Flask, localhost)
templates/demo.html      Pantallas de la demo (entrada, charla, cierre positivo)
precache.py              Pre-carga del banco offline + procesa pendientes (con señal)
eval_quechua.py          Harness de evaluación (quechua y calibración en español)
eval/quechua_eval_set.json   56 preguntas calibradas a los niveles CNEB 3-5
yachaq/
  config.py              Claves, modelo, rutas (lee .env sin dependencias)
  gemini_client.py       Cliente REST de Gemini con reintentos (429/5xx)
  curriculum.py          Carga del documento CNEB + mapeo grado→nivel
  memory.py              Memory store en SQLite (reemplaza Foundry Memory)
  agent.py               Prompt pedagógico + turno de chat + resumen de sesión
  offline.py             Banco local de Q&A, matching y cola de pendientes
yachaq.db                Base SQLite (se crea sola; NO subir a git)
```

## Decisiones de diseño

### 1. RAG: inyección completa, no embeddings

El documento curricular filtrado pesa ~10 KB ≈ 3.000 tokens. Se inyecta
completo en el system prompt de cada llamada. Razones:

- **Recall perfecto:** con retrieval por embeddings existe la posibilidad de
  no recuperar el estándar correcto; inyectándolo todo, el modelo siempre ve
  las 7 competencias y los 3 niveles.
- **Presupuesto:** el límite relevante del free tier es por *requests* diarios,
  no por tokens; embeddings agregarían 1 request extra por turno (o un índice
  que mantener) sin ahorrar nada que importe.
- **Simplicidad operativa:** cero infraestructura de vectores, cero
  re-indexación, un archivo Markdown editable por cualquier docente.

Los embeddings se justifican cuando el corpus no cabe o mete ruido
(el PDF completo de 224 páginas del CNEB, múltiples áreas, textos escolares).
Si eso pasa, el punto de cambio es `yachaq/curriculum.py`: sustituir
`full_text()` por una función de retrieval, sin tocar nada más.

### 2. Memoria: SQLite con dos capas

- `interactions`: transcripción cruda de cada turno (auditoría, re-análisis,
  material para futuros evals).
- `profiles`: un resumen compacto por estudiante (temas trabajados, errores
  recurrentes, temas dominados, nivel estimado) que **el propio modelo
  actualiza al cerrar cada sesión** y que se inyecta en el system prompt de
  la siguiente. Esto replica el patrón del Foundry Memory Store.

SQLite es apropiado para 220 alumnos con acceso secuencial desde una máquina;
no requiere servidor ni conectividad.

### 3. Trilingüe con foco en quechua

- El idioma se guarda por estudiante y el prompt exige responder en él.
- Para quechua: variante sureña (chanka), oraciones cortas, y una línea final
  `[ES]` con resumen en español para que el docente supervise
  (configurable con `YACHAQ_QU_GLOSS=0`).
- `eval_quechua.py` corre el eval set contra el tutor, hace back-translation
  y chequeos automáticos (¿es quechua?, ¿dio la respuesta directa?, ¿verificó
  comprensión?, ¿calibrado al nivel?) y genera un reporte en `eval/reports/`
  **diseñado para que lo valide un hablante nativo** — el juez automático
  solo filtra fallas baratas.

### 3b. Eval set: cobertura CNEB (56 ítems)

`eval/quechua_eval_set.json` pasó de 10 a 56 ítems. Cada uno declara
competencia, nivel CNEB, grado y `lang_in` (idioma en que escribe el
estudiante). Cobertura **en español** por competencia y nivel:

| Competencia | n3 | n4 | n5 |
|-------------|----|----|----|
| C23 Cantidad | 3 | 3 | 4 |
| C24 Regularidad y cambio | 3 | 3 | 3 |
| C25 Datos e incertidumbre | 3 | 3 | 3 |
| C26 Forma y localización | 3 | 3 | 3 |
| C20 Indaga | 2 | 2 | 2 |
| C21 Explica el mundo físico | 1 | 2 | 1 |
| C22 Diseña soluciones | 1 | 2 | 2 |

Matemática (C23-C26) tiene 3+ ítems por celda; ciencia 1-2 (priorización
deliberada). Los 4 ítems en quechua originales se conservan sin cambios.
Cada pregunta está calibrada al estándar textual de su nivel: p. ej. C23-n3
usa juntar/quitar y valor posicional de 2 cifras, C23-n5 usa decimales,
porcentajes y divisores; C24-n5 usa proporcionalidad, ecuación simple y
término general de un patrón — nada de álgebra formal (eso es nivel 6-7).
El `grado` de cada ítem es coherente con su `nivel` según
`curriculum.level_for_grade`, de modo que el tutor y el juez trabajan sobre
el mismo nivel.

**Dos modos de corrida** (`--lang-out`):

```bash
python eval_quechua.py                       # respuestas en quechua (validación lingüística)
python eval_quechua.py --lang-out es         # calibración pedagógica en español
python eval_quechua.py --competencia C26     # filtrar por competencia
python eval_quechua.py --nivel 5             # filtrar por nivel
python eval_quechua.py --lang-in es          # solo ítems escritos en español
```

En modo quechua el reporte incluye back-translation, variante detectada y
casillas de validación humana; en español esos campos no aplican y el
reporte se concentra en las reglas pedagógicas y la calibración de nivel.

**Cuota:** cada ítem son 2 requests (agente + juez), así que el set completo
son **112 requests** — más que la cuota diaria cómoda. Correrlo por
competencia o nivel (`--competencia C23` ≈ 24 requests) es lo práctico. El
script imprime el costo antes de empezar.

### 4. Modo offline de contingencia (aula sin señal)

La escuela del piloto solo tiene señal móvil en puntos específicos fuera del
aula. El modo offline (activo por defecto, `YACHAQ_OFFLINE=0` lo apaga) tiene
tres piezas:

**a) Pre-carga (`precache.py`) — correrla donde SÍ hay señal, antes de cada
visita:**

```bash
python precache.py                   # banco en español: 21 requests (7 comp x 3 niveles)
python precache.py --lang qu         # además, banco en quechua (21 requests más)
python precache.py --solo-pendientes # solo responder lo que quedó pendiente
python precache.py --dry-run         # ver el plan y el costo sin gastar cuota
```

Genera N variantes (default 4) de pregunta+respuesta modelo por cada
(competencia C20-C26, nivel 3-5) y las guarda en la tabla `question_bank` de
`yachaq.db`, junto con palabras clave para la búsqueda. Re-correrlo no
duplica (INSERT OR IGNORE) y cada corrida agrega variantes nuevas, así que
el banco crece visita a visita. Primero procesa las **pendientes**
acumuladas (ver c). Costo: 1 request por combinación + 1 por pendiente; el
script imprime el costo estimado antes de empezar.

**b) Fallback en el tutor:** cada turno intenta Gemini con timeout corto
(12 s, 1 intento; `YACHAQ_FALLBACK_TIMEOUT` / `YACHAQ_FALLBACK_RETRIES`).
Si falla por lo que sea (sin señal, 429 de cuota, 503), busca en el banco
local la entrada más parecida y la entrega anteponiendo "te explico con un
problema parecido: «…»". El estudiante nunca espera más de ~12 s ni ve un
error crudo. El cierre de sesión (resumen de perfil) también usa timeout
corto; si falla, la transcripción ya quedó guardada.

**c) Pendientes:** si ninguna entrada del banco supera el umbral
(`YACHAQ_MATCH_THRESHOLD`, default 0.45), la pregunta se guarda en
`pending_questions`, el estudiante recibe un mensaje en su idioma explicando
que se responderá después, y al salir el CLI recuerda al docente correr
`python precache.py --solo-pendientes`. Al procesarla, la respuesta entra al
banco: si el estudiante repite la pregunta en la siguiente visita offline,
esta vez sí hay match.

**Qué tan buena es la búsqueda del banco (honesto):** es coincidencia
léxica, no semántica. Se normaliza todo (minúsculas, sin tildes, sin
números, sin stopwords, plurales recortados) y dos palabras coinciden si son
iguales o comparten las primeras 4 letras ("vendí" ~ "vender"). El puntaje
pondera cuánto de la pregunta del estudiante está cubierto por la entrada
(70%) y cuántas palabras clave aparecen (30%), con penalización por
distancia de nivel CNEB. En la práctica:

- SÍ matchea: mismo tema con otras palabras cercanas u otros números
  ("¿cuánto alambre para cercar mi chacra de 15 por 9?" → entrada de
  perímetro con 10 por 6). Los números se ignoran a propósito: la respuesta
  enseña el método con sus propios números.
- NO matchea: sinónimos que no comparten raíz ni están en las palabras clave
  ("contorno" por "perímetro"), preguntas muy cortas o vagas ("no entiendo"),
  y quechua escrito con sufijos muy distintos a los del banco (la
  aglutinación del quechua rompe la coincidencia por prefijo; por eso las
  entradas en quechua guardan también palabras clave en español).
- El banco responde UN turno: no usa el historial ni el perfil, y no puede
  sostener el bucle de re-explicación. Es contingencia, no reemplazo.

### 5. Interfaz web para el evento (11-13 de setiembre)

Contexto: NO es una clase sostenida de 220 alumnos. Es una demostración
puntual dentro del evento de entrega de zapatillas: un grupo pequeño de
estudiantes voluntarios (los que levanten la mano) prueba YachAI en vivo,
con proyector si lo hay, en turnos de 2-3 minutos.

`python web_demo.py` → http://localhost:5000. Flujo por turno:

1. **Entrada rápida:** campo de nombre (crea el perfil en SQLite si es
   nuevo; si el estudiante vuelve, la UI lo saluda con "¡te recuerdo!").
   Grado e idioma son chips opcionales con default 4°/español. No hay
   selector de perfiles existentes: solo escribir el nombre y empezar.
2. **Pregunta:** cuadro para escribir o dictar (micrófono vía Web Speech
   API de Chrome; el dictado suele requerir internet — si no está
   disponible, el botón se oculta solo y se escribe).
3. **Respuestas en modo demo:** máximo ~110 palabras para que el turno
   quepa en 2-3 minutos, pero las reglas duras se mantienen: nunca el
   resultado final, siempre cerrar con UNA pregunta de verificación. El
   modelo devuelve además un estado (`explicando`/`correcto`/`incorrecto`).
4. **Cierre positivo:** cuando el estudiante responde bien la verificación
   (estado `correcto`), pantalla verde "¡Muy bien, {nombre}!" y vuelta
   automática a la entrada para el siguiente estudiante. Botón "Siguiente
   estudiante ▸" siempre visible para que el facilitador corte a tiempo, y
   un reloj visual de 3:00 (no corta a nadie, solo avisa).

   Tiempos del cierre (constantes al inicio del `<script>`):
   `PAUSA_ANTES_DE_CELEBRAR` 2,2 s para leer la felicitación del tutor,
   `MIN_CELEBRACION` 4 s de piso legible (el botón "Siguiente" queda
   deshabilitado hasta cumplirlo) y `AUTO_CELEBRACION` 6 s hasta el avance
   automático, con una barra de progreso que lo hace visible.

   **Ojo con los temporizadores:** todos se registran en `timers[]` y
   `pantalla()` los cancela al cambiar de vista. Sin eso, el timer de cierre
   de un turno sobrevive al turno siguiente y cierra la celebración del
   estudiante nuevo a los pocos segundos (era un bug real, no teórico: la
   guarda "¿está activa la celebración?" no distingue de quién es).

Diseño: texto grande, alto contraste con FONDO CLARO (los proyectores
débiles lavan los fondos oscuros), sin dependencias externas en el HTML.
Funciona igual sin proyector (pantalla de la laptop).

Sistema visual, todo autocontenido (cero CDNs, cero fuentes descargadas):

- **Tipografía:** Trebuchet MS para display/titulares (tiene más carácter
  que la default y viene instalada en Windows y macOS) sobre Segoe UI /
  system-ui para el texto corrido.
- **Color con función**, no decorativo: azul = voz del estudiante y
  elementos del tutor; terracota = acción principal (Empezar); verde =
  logro; ocre = avisos del sistema (sin conexión, banco local). Fondo papel
  cálido `#fdfbf5` en vez de blanco puro, más suave en proyección.
- **Banda tipo telar andino** hecha con `repeating-linear-gradient` puro:
  detalle cultural sin ningún archivo de imagen.
- Tarjeta con sombra y jerarquía clara en la entrada; burbujas con etiqueta
  de autor ("YACHAQ" / nombre del estudiante) y acento lateral azul;
  dock de escritura fijo abajo (`#charla` usa `height:100vh` y `#mensajes`
  lleva `min-height:0`, sin lo cual el flex item no encoge y empuja el dock
  fuera de la pantalla).
- `@media (max-height: 800px)` comprime el ritmo vertical para proyectores
  de 1024x768; `prefers-reduced-motion` desactiva las animaciones.

Sin señal funciona el mismo plan de contingencia del CLI: timeout corto →
banco local → pendientes. Como sin modelo no hay quién juzgue la respuesta
de verificación, en modo offline aparecen botones para que el FACILITADOR
decida ("✓ ¡Respondió bien!" dispara el cierre positivo). Antes del evento:
`python precache.py` (y `--lang qu`) desde el punto con señal.

El resumen de perfil se genera en segundo plano al cerrar cada turno, para
que el siguiente estudiante empiece sin esperar.

### Advertencia lingüística importante

El piloto es en Cajabamba (región Cajamarca). El quechua de esa zona es
**quechua de Cajamarca**, una variante distinta (y con pocos datos digitales)
del quechua sureño que los LLM manejan mejor. El agente produce quechua
sureño; hay que validar con hablantes locales si es comprensible/aceptable
para los estudiantes del piloto, o si el uso real será español con quechua
como refuerzo oral del docente.

## Modelo y consumo de cuota

- Modelo por defecto: `gemini-3.1-flash-lite`. Verificado 2026-07-20 con la
  clave de este proyecto:
  - `gemini-2.0-flash` / `gemini-2.5-flash*`: retirados para claves nuevas —
    429 con `limit: 0` (cuota cero, no agotada) o 404.
  - `gemini-flash-latest` (→ `gemini-3.5-flash`): free tier de **solo 20
    requests/día** y 503 frecuentes por alta demanda. Inviable para tutoría
    (una sesión son ~9 requests).
  - `gemini-3.1-flash-lite`: la opción con cuota diaria utilizable. Su calidad
    en quechua debe confirmarse con el eval (es un modelo más pequeño).
- El retry usa backoff exponencial (3s → 6s → 12s → 24s, tope 60s) y respeta
  el `retryDelay` que Google envía en el cuerpo del 429. Dos casos abortan sin
  reintentar, con mensaje accionable: cuota `limit: 0` (modelo sin free tier
  para la clave) y cuota **diaria** agotada (resetea a medianoche PT).
- 1 request por turno de conversación + 1 por cierre de sesión.
- Sesión típica (8 turnos): ~9 requests. Los límites exactos del free tier
  varían por modelo; revisa https://ai.google.dev/gemini-api/docs/rate-limits
  y mide el uso real del piloto antes de escalarlo a los 220 alumnos.
- `eval_quechua.py` completo: 112 requests (56 ítems x 2). Filtrar por
  competencia (~18-24) o nivel (~34-38) para no agotar la cuota diaria.
- `precache.py` por idioma: 21 requests (+1 por cada pendiente acumulada).
  Presupuestar es + qu (42) para el día antes de cada visita al piloto.
- Demo del evento (`web_demo.py`): un turno típico de 2-3 minutos son 2-4
  requests (pregunta + verificación + resumen de perfil). 20 estudiantes
  voluntarios ≈ 60-80 requests; medir contra el límite diario del modelo.
