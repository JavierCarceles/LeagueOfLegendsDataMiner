# Traspaso para el siguiente chat (prompt de continuación)

> Copia este archivo entero como primer mensaje del nuevo chat. Describe el encargo, lo hecho, lo que queda y cómo
> comprobarlo. Última actualización: 2026-11-19 (sesión de tarde).

## 1. El encargo (palabras del usuario, resumidas)

Dejar **LeagueOfLegendsDataMiner / Tempu Drafter** profesional:

- Datos de **varias webs**, descargados **solos en segundo plano sin perder rendimiento**.
- **Coaching** del usuario con perfil guardado: **sin casos de error sin cubrir y sin falsos positivos** (ni errores
  inventados ni «bien hecho» falsos).
- **Todo automático**: al entrar en cualquier apartado los datos ya deben estar (nada de esperar a que se descargue un
  matchup al hacer clic).
- Poder entrar y **ver sus repeticiones con los errores, al máximo detalle**.
- Apartados con muchas estadísticas → **desplegables organizados**.
- Recolección de partidas **por servidores cómoda**: marcar/desmarcar todos de golpe (no solo uno a uno).
- **Todo responsive**.
- Se ejecuta en **Windows con Python 3.14.7**.
- **Prohibido**: `git add`, `commit`, `push`, ramas o worktrees. Todo en `main`, sin commitear.
- Al llegar al ~95 % de contexto: parar y actualizar este archivo.

## 2. Entorno

- Repo: `/Users/javierivan/LeagueOfLegendsDataMiner` (macOS para desarrollar; el usuario lo usa en Windows).
- `.venv/` con Python 3.12 (Homebrew) y `requirements-dev.txt` instalado: `.venv/bin/python -m pytest -q -p no:warnings`.
- Python 3.14 para probar compatibilidad: se instaló con `uv` en el scratchpad de la sesión (puede haberse borrado).
  Para recrearlo sin tocar el sistema: `pip install --target <tmp>/uvtool uv`, luego
  `UV_PYTHON_INSTALL_DIR=<tmp>/pythons <tmp>/uvtool/bin/uv python install 3.14`, crear un venv con ese intérprete e
  instalar `requirements-dev.txt`. **Ojo**: `uv` crea un enlace `~/.local/bin/python3.14`; bórralo después (y
  `~/.local/bin` si queda vacío). Con 3.14 los tests pasaban también con `-W error::DeprecationWarning`.
- **API key de Riot (pruebas)**: está guardada en `.env` (`RIOT_API_KEY`, archivo ignorado por git). Es una development
  key: caduca 24 h después de generarse (se dio el 2026-11-19 por la tarde). Si Riot la rechaza, pide al usuario una nueva
  y pégala en *Datos y API* (se recarga en caliente). No la copies a archivos versionados.
- Servidor de pruebas: `.claude/launch.json` (config `tempu`) arranca `run.py --no-browser --port 8765` con
  `DATA_DIR` en el scratchpad (para no ensuciar `data/`). Si el scratchpad ya no existe, cambia `DATA_DIR` a otra carpeta
  temporal. **Borra `.claude/launch.json` al terminar** (no es del usuario).
- Datos de prueba: se guardó como perfil un jugador Challenger de EUW elegido del ranking público (solo en la BD del
  scratchpad). Sus 20 partidas sirven para probar *Repeticiones* y *Mi progreso*.

## 3. Hecho en esta sesión (sin commitear)

Backend:
- `app/budget.py`: presupuesto de 3 s por petición `/api/…` (middleware ASGI). Lo que no da tiempo a descargar se
  encarga al precargador y la respuesta lleva `X-Data-Pending: 1`.
- `app/external/base.py`: caché común (SQLite `ext_cache`) con *stale-while-revalidate*, *single-flight*, backoff
  exponencial y refresco en línea dentro del precargador.
- `app/external/opgg.py`: ahora hereda de `CachedSource` (misma API pública).
- `app/external/lolalytics.py` (**nueva fuente**, API JSON pública `a1.lolalytics.com/mega`, robots permite `*`):
  tier list y counters. Se combina con OP.GG sin doble conteo (WR ponderado, muestra = la mayor) en
  `app/analytics/evidence.py` (`combine_web`, `pair_sources`, filtro de fuentes `all|web|opgg|lolalytics|own`).
- `app/external/patchnotes.py` (**nueva fuente**: notas de parche oficiales de Riot, `html.parser`): cambios por
  campeón/objeto y veredicto mejora/debilitación/ajuste estimado a partir de los números.
  U.GG, League of Graphs, METAsrc y Mobalytics devuelven 403 anti-bot → **no se usan** (no saltarse protecciones).
- `app/warmer.py`: precargador con carriles por web (`opgg-web` ×2, `opgg-mcp` ×2, `lolalytics`, `riot-web`, `demand`),
  prioridades (tus campeones → esencial del ámbito → extras), ámbitos de filtros recordados (`meta` `warmer.scopes`),
  **pausa automática mientras juegas** (LCU `InProgress`), estado para la web.
- Endpoints nuevos en `app/api/sources.py` (`/api/warmer/*`, `/api/sources/status`, `/api/patches*`) y
  `app/api/replays.py` (`/api/replay/{match}/client`, `/download`, `/watch`, `/client/seek`, `/client/enable-api`).
- `app/lcu.py`: repeticiones reales vía cliente (`lol-replays`), Replay API (`127.0.0.1:2999`) para saltar al segundo
  exacto, `enable_replay_api()` edita `Config/game.cfg` con copia (solo si el usuario lo pulsa).
  `app/coach/replay_client.py`: flujo descargar → abrir → saltar, con mensajes. Límite Riot: 50 descargas/día.
- `app/ingest/crawler.py`: se reanuda solo al abrir la app si lo dejaste en marcha (`meta crawler_autostart`) y se pausa
  mientras juegas.
- `app/main.py`: handler de errores inesperados (500 con mensaje legible), middleware de presupuesto.
- `run.py`: abre el navegador solo, reutiliza una instancia ya abierta, busca puerto libre (con `SO_REUSEADDR` en POSIX),
  consola UTF-8, prioridad baja en Windows (`psutil`). `launcher.py` + `iniciar.bat` (CRLF, `.gitattributes`):
  doble clic en Windows → crea `.venv`, instala dependencias solo si cambian, arranca.
- `requirements.txt`: sin `uvicorn[standard]` (evita compilar en Windows/3.14), `pydantic>=2.12`, `psutil`.
- Config nueva (`.env`): `LOLALYTICS_ENABLED`, `PATCHNOTES_ENABLED`, `PRELOAD_ENABLED`, `PAUSE_IN_GAME`, `LOW_PRIORITY`
  (todas 1 por defecto). Tests: `tests/factories.make_settings` las desactiva.

Frontend (`web/`):
- `ui.js`: `collapsible()` (recuerda abierto/cerrado), `multiSelect()` (Todas/Ninguna/grupos/presets), `patchBadge`,
  `patchChanges`, iconos nuevos; `h()` ya no descarta un `0` como primer hijo.
- `api.js`: detecta `X-Data-Pending` (`isPending`, evento `tempu:pending`) y errores de conexión.
- `main.js`: vista nueva **Repeticiones**, recarga suave automática cuando llegan datos pendientes (sin perder scroll ni
  interrumpir si escribes o hay un diálogo), píldora «Datos al día / Preparando datos %», filtros plegables en móvil,
  filtros ocultos en vistas personales (`noFilters`).
- `views/replays.js` + `components/replay.js`: lista de repeticiones (filtros, orden por errores/nota) y visor: mapa 2D,
  marcas de errores por gravedad en la línea de tiempo, globos en el mapa, implicados resaltados, rastro, HUD (marcador,
  oro de equipo, nivel, CS, oro sin gastar), pausa automática en cada error, teclas (espacio, ←/→, N/P), panel de
  errores filtrable (gravedad/categoría) con detalle, etiquetas, quién te mató, contexto cercano y consejo, botón «Ver en
  el cliente de LoL», secciones plegables (build, oro, estadísticas).
- `views/coach.js` (*Mi progreso*): secciones pesadas en desplegables, «Ver N puntos débiles más», la ruta antigua
  `#/progreso/<id>` redirige al visor.
- `views/data.js`: recolector por servidores agrupado por región con Todas/Ninguna/grupo/presets (verificado en el
  navegador), panel de precarga, fuentes, secciones técnicas plegables.
- `views/matchups.js`: fuentes por web, aviso si OP.GG y Lolalytics discrepan (≥ 3 pp con ≥ 300 partidas), cambios de
  parche de ambos campeones, desglose plegable, el modal se completa solo si había datos pendientes.
- `views/champions.js`: columna «WR Lolalytics», KPI Lolalytics, cambios de parche en la ficha.
- CSS: rejilla `minmax(0,1fr)` (había desbordamiento horizontal a 1024 px), estilos de todo lo anterior, móvil ampliado.

Verificado en el navegador (1024 px y 375 px): Datos y API, recolector (Todas/Ninguna), Repeticiones (lista y visor,
pausa automática, selección de errores), Mi progreso, Campeones, Matchups (modal), Draft (insignias ↑/↓), Jugador,
Partida, Regiones, Guías. **Primer arranque simulado** con Python 3.14.10 desde cero (`launcher.py` en una copia sin
`.venv`): crea el entorno, instala dependencias con ruedas y la web responde.

Robustez añadida después: el planificador precarga Lolalytics aunque OP.GG esté desactivado o caído; si el primer
arranque es sin internet, Data Dragon se reintenta cada minuto (y la web pide los metadatos sola cada 10 s);
`launcher.py` trata Ctrl+C / cerrar ventana como salida normal (sin «se ha cerrado con un error»); el aviso
«Completando datos…» se oculta tras agotar reintentos; `game.cfg` respeta CRLF (había un fallo real, cubierto por test).
Tests propios: `tests/test_sources.py` (32), `tests/test_crawler_otp.py` (10) y uno más en `tests/test_coach_audit.py`.

Segunda tanda (tras «sigue hasta el 95 %»): mensaje correcto en *Mi progreso* cuando falta muestra (antes decía «no hay
puntos débiles» con < 5 partidas: falso «lo haces bien»; `summarize` devuelve `weakness_sample`/`min_games_weakness`/
`reliable_games`); nivel y oro de cada implicado en cada muerte del visor; detección de LoL instalado en otra carpeta
(`RiotClientInstalls.json`); catálogo de objetos por parche; rejillas de Guías y Regiones responsive (tenían estilos en
línea), `100dvh` en móvil y título visible con el botón de filtros compacto. Además: los OTPs encontrados resuelven su Riot ID
(account-v1, solo para los que cumplen), el endpoint `/api/crawler/otps` acepta maestrías con y sin fecha, y el botón
«Ver en el cliente de LoL» se desactiva (con explicación) si el cliente no está abierto. Verificado con datos reales:
OTPs de Lee Sin del Challenger coreano con sus partidas guardadas.

**Petición añadida a mitad de sesión** (hecha): recolector **por servidor + elo + OTPs / jugadores con mucha maestría de
campeones concretos** (p. ej. KR + Aspirante + OTPs de Ahri). Backend: `CrawlerConfig.champions/focus/min_mastery/
only_target_games` y `qualifies()`, `RegionWorker._next_target/_check_mastery` (`app/ingest/crawler.py`), tabla
`mastery_checks` (`app/data/db.py`), `RiotClient.top_masteries` (champion-mastery-v4 `/top`), el endpoint
`/api/crawler/start` acepta nombres o ids de campeón. Frontend: sección «Buscar por campeones (OTPs)» en el formulario del
recolector (`views/data.js`, `championSection`) y contadores por región (maestría revisada, encontrados, partidas).
También se corrigió el progreso de la precarga (tareas duplicadas impedían llegar al 100 %).

## 4. En curso al cerrar / pendiente (por prioridad)

1. ~~Auditoría de falsos positivos del análisis~~ **HECHO**: los 23 defectos corregidos en `app/coach/*`
   (`ANALYSIS_VERSION = 3`, re-análisis automático al arrancar), 33 tests de regresión en `tests/test_coach_audit.py`
   (32 fallaban con el código antiguo). Etiquetas nuevas `afk` y `fair_trade`; métricas `obj_fair_trades`,
   `ally_afk_min`, `enemy_afk_min`, `crabs_pre10`; claves `items_reliable`, `unknown_items`; `insights.reliable()`
   (Swiftplay o AFK ≥ 3 min no cuentan en agregados ni nota: la API devuelve `reliable` y `score: null`).
   Después (ya hecho): `DDragon.catalog_for(gameVersion)` carga el `item.json` del parche de cada partida (caché en disco,
   reintento a la hora sin red) y `GameTracker.items_for(match)` lo usa; `ANALYSIS_VERSION = 4` (añade
   `replay.frames`: nivel y oro de los 10 por minuto, que el visor muestra en cada muerte).
2. ~~Tests nuevos~~ HECHO: `tests/test_sources.py` (27 tests: caché SWR/single-flight/backoff/presupuesto, cabecera,
   precargador, `combine_web`, Lolalytics, notas de parche, repetición en cliente con LCU simulado, `game.cfg` CRLF,
   endpoints sin cliente, autoarranque del recolector). Pasan en 3.12 y 3.14.
3. ~~Repaso en navegador~~ HECHO (escritorio y 375 px) de todas las vistas; desplegables en Jugador (comparativa) y
   Regiones (comparativa); insignias ↑/↓ de parche en el Draft.
4. ~~Suite completa en 3.14~~ HECHO: 551 tests en verde en Python 3.12 y 3.14 (`-W error::DeprecationWarning`).
5. ~~README / .env.example~~ HECHO (la sección del análisis ya describe lo implementado tras la auditoría).
6. Limpieza final: borrar `.claude/launch.json`; no dejar procesos del servidor de pruebas; **no commitear**.

## 3 bis. Sesión del 2026-11-19 (noche) — hecho, sin commitear

- La key de Riot se **valida sola al arrancar** (`Services.check_key_on_startup`; antes quedaba «Key sin validar»).
- La **edad de la key** ya no se reinicia al abrir la app: `KeyStore(state_path=data/riot_key_state.json)` guarda una
  huella (nunca la key) y la fecha en que se vio; si es nueva y viene del `.env`, usa la fecha del fichero. Pasadas 24 h
  con la key aún válida, la web deja de mostrar cuenta atrás. Tests en `tests/test_keys.py`.
- **Prioridad `NORMAL`** en `app/riot/ratelimit.py` para el seguimiento de tus partidas (tracker): va por delante del
  recolector (LOW). Antes, con el recolector a tope, tus partidas casi no avanzaban. Test en `tests/test_ratelimit.py`.
- *Mi progreso*: con pocas partidas la nota sale gris como **«provisional»** y la píldora dice «Faltan partidas».
- *Repeticiones*: los contadores llevan texto («1 grave», «7 a mejorar»), no solo color.
- Análisis (`ANALYSIS_VERSION = 5`, se re-analiza solo al arrancar), con datos reales de 16.22:
  - **Placas 2026**: desde el parche 16.1 hay placas toda la partida (120 de oro; torres interiores y de inhibidor con
    3 placas). Se valoran a 120, las placas de una torre que cae en la misma ventana no se suman dos veces
    (`_structures_value`) y **unas placas en otra línea ya no «compensan» una muerte** (antes casi toda muerte salía
    como cambio y no contaba como error).
  - Las **botas** nunca cuentan como componente que «sobra» en un inventario lleno (era un consejo erróneo).
  - «El rival cogió X y no lo compensasteis» explica **también lo que sacó el rival** a la vez.
  - Tests nuevos en `tests/test_coach.py` y `tests/test_coach_audit.py` (fallaban con el código anterior).
- Suite completa en verde (Python 3.12). `.claude/launch.json` borrado; sin servidores en marcha.
- Observado con Riot: **otra copia de la app estaba usando la misma key** (≈85 de 100 peticiones/2 min en *europe*).

## 4 bis. Pendiente (por prioridad)

1. Repetir las 4 revisiones independientes que se cortaron por el límite de uso (no dieron resultados): Windows/arranque
   (`iniciar.bat`, `launcher.py`, `run.py`, `app/lcu.py`), descargas en segundo plano (`budget.py`, `external/*`,
   `warmer.py`, crawler), auditoría del análisis y frontend (XSS, fugas de temporizadores, responsive).
2. Revisar con datos reales si quedan muertes demasiado indulgentes («Muerte aislado» como leve) tras el cambio de placas.
3. Ejecutar la suite con Python 3.14 (no se hizo en esta sesión).
4. Probar en Windows real (`iniciar.bat`, cliente de LoL, pausa en partida, repetición en el cliente).

## 5. Ideas siguientes (no pedidas, por si el usuario quiere más)

- Probar en Windows real: `iniciar.bat`, detección del cliente (`lockfile` / `psutil`), pausa en partida y la repetición
  real (`EnableReplayApi`), que aquí (macOS sin cliente) solo se han probado con el cliente simulado en tests.
