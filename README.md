# Tempu Drafter

Analizador de drafts, matchups, estadísticas y **coaching de tus partidas** de League of Legends, con interfaz web
local. Combina **OP.GG** y **Lolalytics** (muestras de cientos de miles de partidas), las **notas de parche oficiales de
Riot**, **tu propia base de datos** (Riot API) y la fuerza estadística de cada campeón.

Todo funciona solo: al abrir la app se descargan en segundo plano los datos que usa cada vista, se siguen y analizan tus
partidas y, si lo dejaste en marcha, el recolector sigue recogiendo partidas. Mientras juegas, las descargas se pausan.

| Sección | Qué hace |
|---|---|
| **Mi progreso** | Guarda tu perfil y analiza **solas** tus partidas de la Grieta (normales y clasificatorias). Detecta qué haces mal teniendo en cuenta el contexto (ganks del jungla rival, muertes adelantado o sin visión, oro que te daba para tu build y no gastaste, objetivos regalados frente a buenos cambios, minutos vacíos, pathing, visión) y tu build frente a la más jugada en OP.GG. Te propone propósitos, sigue si los cumples y te dice cuándo estás listo para clasificatorias. Las secciones con muchas estadísticas van en desplegables. |
| **Repeticiones** | Cada partida reconstruida en 2D con **tus errores marcados en la línea de tiempo**: se para sola en cada uno, muestra dónde pasó, quién estaba cerca, quién te mató, por qué cuenta como error y qué hacer. Con el cliente de LoL abierto, un botón abre la **repetición real** y salta al segundo exacto. |
| **Draft** | Probabilidad de victoria con intervalo, nota S–F, fuerza de cada equipo, los 5 enfrentamientos, sinergias, perfil de daño y curva por duración. Los mejores picks de cada hueco salen solos. Funciona con el draft a medias. |
| **Matchups** | Todos los rivales de un campeón: WR ajustado, esperado, ventaja propia (Δ), veredicto y fiabilidad. El detalle separa OP.GG, Lolalytics y tu BD (y avisa si las webs no coinciden), los cambios de parche de ambos campeones, el consejo de OP.GG y la build contra ese rival. |
| **Campeones** | Tier list por rol (OP.GG + Lolalytics + tu BD) y ficha de campeón: roles, mejores y peores matchups, sinergias, perfil y cambios recientes. |
| **Jugador** / **Partida** | Perfil de cualquier jugador y detalle de cualquier partida (Riot API). |
| **Regiones** | Meta por región y, con datos propios, objetivos, duración, lado azul/rojo y elo. |
| **Guías** | Guías guardadas, generación con IA (Gemini) y conversión de vídeos de coaching de YouTube en guías. |
| **Datos y API** | API key, estado de la precarga y de cada fuente, recolector de partidas por servidores y elo (también **solo de OTPs o jugadores con muchísima maestría de los campeones que elijas**, p. ej. KR + Aspirante + Ahri), límites de la API, base de datos y calibración del modelo. |

## Puesta en marcha

### Windows (recomendado)

1. Instala **Python 3.14** desde [python.org](https://www.python.org/downloads/) (marca *Add python.exe to PATH*).
2. Doble clic en **`iniciar.bat`**. La primera vez crea el entorno e instala las dependencias (~1 minuto); las siguientes
   arranca al momento. Se abre el navegador solo en http://localhost:8000.

Si ya estaba abierta, `iniciar.bat` solo abre el navegador. Si el puerto 8000 lo usa otro programa, busca el siguiente
libre. Para cerrarla, cierra la ventana negra (o `Ctrl+C`).

### Manual (cualquier sistema)

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

```bash
python run.py
```

Opciones: `--port 8001`, `--no-browser`, `--reload` (desarrollo).

**API key de Riot.** Pégala en *Datos y API* (o en `.env`, ver `.env.example`). Se recarga en caliente: cuando caduque
la development key (24 h), pega la nueva y todo sigue solo. Sin key funcionan el draft, los matchups, la tier list y las
regiones con OP.GG y Lolalytics; la key hace falta para tus partidas, perfiles y el recolector.

**Mi progreso.** Escribe tu Riot ID (o pulsa *Detectar del cliente* con el cliente de LoL abierto) y guarda. A partir de
ahí no tienes que hacer nada: cada 3 minutos se buscan tus partidas nuevas y se analizan; si el cliente está abierto en el
mismo ordenador, la partida se analiza en cuanto Riot la publica (1-3 min tras terminar).

**Opcional:** guías con IA, YouTube y amigos del cliente: `pip install -r requirements-coaching.txt` y las claves
`GEMINI_KEY_*` en `.env`. Tests: `pip install -r requirements-dev.txt` y `python -m pytest`.

## Todo automático y sin esperas

- **Precarga en segundo plano.** Al arrancar se planifica qué hace falta y se descarga por orden de importancia:
  1. tus campeones: builds, counters y guías de tus matchups;
  2. lo esencial con los filtros que usas: tier lists y counters del parche actual y el anterior;
  3. extras: tipo de daño, builds de todos los campeones, sinergias, guías de los matchups más jugados, notas de parche.

  Cada web tiene su carril y su ritmo educado (OP.GG ≈1-2 páginas/s, Lolalytics ≈1/s), así que avanzan en paralelo sin
  saturar a nadie. Recuerda los filtros que usas (elo, región, parche) y los mantiene al día. Lo caducado se sirve al
  instante y se refresca por detrás (6 h el parche en curso, 7 días el anterior).
- **Nunca te deja esperando.** Cada petición de la web tiene como mucho 3 s para traer datos que aún no estén en caché;
  si no da tiempo, la vista se muestra con lo que hay y **se completa sola** a los pocos segundos (aviso *Completando
  datos…* arriba), sin perder tu posición ni interrumpirte si estás escribiendo.
- **Mientras juegas** (cliente de LoL abierto en este ordenador) se pausan la precarga y el recolector para no quitarte
  ancho de banda; en Windows la app corre además con prioridad baja de CPU. Al terminar la partida sigue sola.
- **Recolector**: si lo dejas en marcha, se reanuda solo al volver a abrir la app.
- En la barra superior, *Datos al día* / *Preparando datos N %* resume el estado; el detalle está en *Datos y API*.

## De dónde salen los datos

1. **OP.GG** — web pública (counters y tier list con filtros de parche, elo y región) y su **API oficial para apps**
   (MCP: guía de cada matchup, sinergias, builds y tipo de daño).
2. **Lolalytics** — API JSON pública de su web (tier list y counters). Su `robots.txt` permite clientes genéricos.
   Como OP.GG y Lolalytics miden la misma población de partidas (y se solapan), **no se suman a ciegas**: se toma el WR
   ponderado y como tamaño de muestra el mayor de los dos. El detalle del matchup muestra cada web por separado y avisa
   si no coinciden.
3. **Notas de parche oficiales de Riot** (leagueoflegends.com) — cambios de campeones y objetos de las últimas versiones,
   con una estimación de mejora/debilitación a partir de los números (subir daño mejora; subir enfriamiento o coste
   empeora).
4. **Tu base de datos** (Riot API: lo que recoge el recolector, tus partidas y búsquedas) más el histórico importado.
5. **Estadísticas de campeón**: el WR general de cada campeón en su rol da el resultado *esperado* de cualquier
   enfrentamiento (log5).

u.gg, League of Graphs, METAsrc y mobalytics bloquean clientes automáticos con un control anti-bot: no se usan (no se
salta ninguna protección). Todo se cachea en SQLite (`data/lol.db`).

## Repeticiones

- **Repetición 2D**: de cada partida se descargan el detalle y el *timeline* de match-v5 (posición de los 10 jugadores
  cada minuto y en cada evento). Entre medias la posición se interpola. Sobre el mapa: tus errores con su gravedad
  (grave, a mejorar, leve) y tus aciertos, un globo donde ocurrió cada uno, los campeones implicados resaltados, tu
  rastro de los últimos segundos y, en cada instante, marcador, oro de equipo, tu nivel, CS y oro sin gastar.
  - Al reproducir se para en cada error (desactivable). Teclas: espacio (reproducir/pausar), ← → (5 s; con Mayús 30 s),
    N / P (siguiente/anterior error).
  - Panel de errores filtrable por gravedad y categoría, con la explicación, las etiquetas (sin ward, adelantado, jungla
    rival…), quién te mató, vuestro nivel y oro en ese momento, quién estaba cerca y un consejo.
- **Repetición real en el cliente**: con el cliente de LoL abierto, *Ver en el cliente de LoL* descarga (si hace falta) y
  abre la repetición (.rofl) y salta a unos segundos antes del error.
  - Solo existen para partidas del **parche actual**, y Riot limita las descargas a **50 al día**: solo se descarga la
    que pides.
  - Para saltar solo al segundo exacto, el juego necesita `EnableReplayApi=1` en `Config/game.cfg`. La web te ofrece
    activarlo con un botón (guarda una copia `game.cfg.tempu-backup`); nunca se toca sin que lo pidas.

## Mi progreso: cómo se analizan tus partidas

- **Sin falsos positivos**: algo solo se marca como error (o como acierto) cuando los datos lo respaldan. Si una
  comprobación no se puede hacer con fiabilidad (rival de línea desconocido, desconexión, objetos desconocidos,
  Swiftplay), se omite en vez de adivinar.
- **Muertes.** Cada una se clasifica: *gank* (participa alguien que no es de tu línea, sobre todo el jungla rival; si no
  se sabe quién era tu rival de línea, solo cuenta el jungla), *1 contra 1*, *adelantado* (claramente más cerca de su torre
  que de la tuya; si esa torre ya cayó no se juzga), *sin visión* (ni tus wards ni los de un aliado cercano en los 2
  minutos previos, ni un ward de control vivo; y solo cuando la visión importaba: ganks, río o jungla, nunca en una
  ejecución de torre), *con oro sin gastar*, *antes de un objetivo* (el rival coge justo después uno cercano o que te
  tocaba disputar), *en su jungla* y *aislado*. Se mira la pelea entera: caer en una pelea que ganáis, en el empujón final,
  justo antes de una desconexión o mientras tu equipo saca algo a cambio pesa mucho menos y no cuenta en tus estadísticas
  de errores.
- **Oro.** Se reconstruye tu inventario (compras, ventas, «deshacer», objetos gastados y recetas de Data Dragon). Un
  minuto cuenta como *oro sin gastar* solo si te llegaba para una pieza (≥ 1.000) de tu siguiente objeto y tenías hueco.
  Ahorrar con el inventario lleno, tener la build terminada, estar en plena pelea u objetivo o el empujón final no son
  errores; a cada vuelta tardía se le dan 45 s de margen. Las pociones y los wards de control apilados ocupan un solo
  hueco. Cada partida se analiza con el catálogo de objetos de **su** parche (Data Dragon); si aun así aparecen objetos
  desconocidos, el análisis de oro y build se omite en vez de arriesgar avisos falsos.
- **Objetivos y cambios.** Si el rival coge un objetivo y estabas lejos, se suma lo que sacó tu equipo alrededor (kills,
  torres, placas, su jungla, otro objetivo) y se compara con lo que vale. Solo es *buen cambio* si participaste (si tu
  equipo lo compensó sin ti, no se dice nada); en inferioridad numérica, recién muerto o justo al reaparecer no se te
  culpa, y antes del 14 no se culpa a un top o a la pareja de bot de un objetivo menor al otro lado del mapa.
- **Build.** Orden y minuto de cada objeto, compras por vuelta, inventario lleno de componentes, objetos vendidos, la R a
  tiempo (6, 11 y 16), Heridas graves contra curanderos y resistencias frente al daño recibido, comparado con la build
  más jugada de tu campeón y rol en OP.GG.
- **Tiempo.** *Minutos vacíos* (desde el 15: sin farmear, sin daño, sin wards ni limpiar visión, sin volver a base ni
  participar en kills, torres u objetivos; preparar o disputar un objetivo, recibir daño, aplicar control o, de support,
  acompañar a un aliado no cuentan como vacío) y porcentaje de la partida muerto. Las desconexiones/AFK no cuentan y se
  avisan aparte; con un AFK tampoco se juzgan la fase de líneas ni la R a tiempo.
- **Jungla.** Campamento inicial, ruta de los minutos 1-8, presencia en cada línea, impacto antes del 10 (kills, placas,
  objetivos, invasiones), CS de jungla y ganks del jungla rival en la otra mitad del mapa.
- **Referencias.** Cada métrica se compara con una referencia de tu rol escalada a tu *elo objetivo* (o las medias reales
  de tu BD si hay ≥ 300 partidas de ese elo y rol). Los puntos débiles necesitan al menos 5 partidas (y que la diferencia
  sea real, no ruido); las tendencias *mejorando/empeorando*, al menos 8 del mismo rol y un cambio mayor que su error;
  los propósitos solo se comprueban en partidas donde la métrica aplica a tu rol. Las partidas de Swiftplay o con alguien
  desconectado 3 minutos o más no cuentan en los agregados ni en la nota (se ven igual en *Repeticiones*).
- **¿Listo para clasificatorias?** Nota 0-100 con tus últimas 10 partidas: 60 % los propósitos que cumples y 40 % la
  nota de fundamentos (0-10, 5 = como la referencia).

## Configuración (`.env`)

| Variable | Por defecto | Para qué |
|---|---|---|
| `RIOT_API_KEY` | — | Key de Riot (también desde la web). |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Dirección del servidor (por defecto solo tu ordenador). |
| `OPGG_ENABLED`, `LOLALYTICS_ENABLED`, `PATCHNOTES_ENABLED` | `1` | Activar o desactivar cada fuente. |
| `PRELOAD_ENABLED` | `1` | Precarga en segundo plano. |
| `PAUSE_IN_GAME` | `1` | Pausar descargas de fondo mientras juegas. |
| `LOW_PRIORITY` | `1` | Windows: prioridad baja del proceso. |
| `STATS_QUEUES` | `420,440` | Colas que alimentan tus estadísticas propias. |
| `DDRAGON_LANG` | `es_ES` | Idioma de campeones, objetos y notas de parche. |
| `LOL_INSTALL_DIR` | — | Carpeta de LoL si no se detecta sola (se busca en las rutas por defecto, en `RiotClientInstalls.json` del Riot Client y en el proceso del cliente abierto). |
| `GEMINI_KEY_1..3`, `GEMINI_MODELS` | — | Guías con IA (opcional). |
| `DATA_DIR`, `COACHINGS_DIR` | `data`, `data/coachings` | Carpetas. |

## Cómo se calcula (resumen)

- **WR ajustado (bayesiano):** `(victorias + k·esperado) / (partidas + k)`. Con muchas partidas mandan los datos del
  enfrentamiento; con pocas, lo esperado por la fuerza de los campeones. `k` se estima de los propios datos (Bayes
  empírico, `app/analytics/priors.py`).
- **Δ ventaja propia:** WR ajustado − esperado.
- **Veredicto:** Muy favorable ≥ 54 %, Favorable ≥ 51,5 %, Equilibrado, Desfavorable, Muy desfavorable. La **fase de
  líneas** se mide aparte (oro y experiencia al 14, CS a los 10, placas y solo kills).
- **Draft:** modelo logístico aditivo (5 líneas + sinergias), `P = σ(α·L)` con intervalo del 90 %. Con ≥ 200 partidas
  propias, *Backtest* calibra `α` (leave-one-out).
- **Recolector (key gratuita: 20 peticiones/s y 100 cada 2 min por región):** semillas de las ligas oficiales, un hilo
  por región, solo ranked, sin duplicados ni remakes (~1 llamada por partida nueva), y deja un 15 % libre para tus
  búsquedas. En *Datos y API* eliges servidores (agrupados por región, con *Todas* / *Ninguna*) y elos.
- **Recolector por campeones (OTPs):** eliges campeones (hasta 10) y quién cuenta: *OTPs* (su campeón con más puntos de
  maestría es uno de los elegidos, con al menos el mínimo de puntos y 1,5 veces los del segundo) o *mucha maestría* (uno
  de los elegidos entre sus 3 primeros con el mínimo de puntos). La API de Riot no ofrece un ranking de maestría por
  campeón, así que se mira la maestría de cada jugador del ranking de esos servidores y elos (1 llamada por jugador, se
  recuerda 7 días) y solo se recogen las partidas de los que cumplen; opcionalmente, solo las que jugaron con esos
  campeones (las demás se recuerdan para no volver a pedirlas).

## Estructura

```
iniciar.bat, launcher.py, run.py   arranque en Windows (doble clic), entorno y servidor
app/
  main.py, services.py, config.py, logbus.py, budget.py   arranque, servicios, .env, logs (SSE), presupuesto por petición
  warmer.py    precarga en segundo plano (carriles, prioridades, pausa en partida)
  riot/        cliente de la API, limitador adaptativo, routing, gestión de la key
  external/    base.py (caché común), opgg.py, lolalytics.py, patchnotes.py
  data/        SQLite: esquema y escrituras con agregados incrementales
  ingest/      parseo de partidas, recolector e importación del histórico
  analytics/   estadística, evidencia combinada, matchups, campeones, draft, jugadores, partidas, regiones
  coach/       tus partidas: perfil, seguimiento, geometría del mapa, análisis, propósitos y repeticiones en el cliente
  coaching/    índice de guías, Gemini y YouTube
  lcu.py       cliente de LoL abierto (cuenta, fin de partida, repeticiones, Replay API)
  api/         endpoints REST (documentación interactiva en /docs)
web/           interfaz (HTML + CSS + módulos JS, sin compilación)
data/          lol.db (generada), legacy/ (histórico), coachings/ (guías)
tests/         tests offline (pytest)
```

Proyecto personal sin afiliación con Riot Games, OP.GG ni Lolalytics. Respeta sus condiciones de uso.
