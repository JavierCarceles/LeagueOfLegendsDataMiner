# Traspaso para el siguiente chat (prompt de continuación)

> Copia este archivo entero como primer mensaje del nuevo chat. Describe el encargo, lo hecho, lo que queda y cómo
> comprobarlo. Vive en `docs/CONTINUAR.md` (antes en la raíz). Última actualización: 2026-11-19 (noche, 4.ª sesión).

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
- **Búsqueda de OTPs**: «cuanta más data haya mejor, no hace falta que se limite a 100 partidas por jugador» (hecho, 3 ter).
- **«Qué haces mal»** no puede quedarse en 1 cosa siendo Platino (perfil del usuario: **FNX Tempuro#FNIX**, EUW, Platino IV,
  juega jungla): hecho en 3 quater; seguir mejorándolo sin falsos positivos.
- **Coaches**: investigar coaches con YouTube/web (Mauro Garih, PochiPoom, Coach Kirei…) y su material gratis para ver qué
  falta en la app: investigación en `docs/coaches.md`; implementar sus huecos (pendiente, ver 4 bis).
- **Raíz ordenada y `run.py` que lo haga todo en Windows** (entorno + dependencias + arranque, sin pasos aparte): hecho.
- **Nada de Gemini** (claves caducadas, no las quiere): eliminado.
- Se ejecuta en **Windows con Python 3.14.7**.
- **Prohibido**: `git add`, `commit`, `push`, ramas o worktrees. Todo en `main`, sin commitear.
- Al llegar al ~95 % de contexto: parar y actualizar este archivo.

## 2. Entorno

- Repo: `/Users/javierivan/LeagueOfLegendsDataMiner` (macOS para desarrollar; el usuario lo usa en Windows).
- `.venv/` con Python 3.12 (Homebrew) y `tests/requirements.txt` instalado: `.venv/bin/python -m pytest -q -p no:warnings`.
- Python 3.14 para probar compatibilidad (en el scratchpad, sin tocar el sistema):
  `.venv/bin/python -m pip install --target <tmp>/uvtool uv`, luego
  `UV_PYTHON_INSTALL_DIR=<tmp>/pythons UV_CACHE_DIR=<tmp>/uvcache <tmp>/uvtool/bin/uv python install 3.14 --no-bin`
  (`--no-bin` evita el enlace en `~/.local/bin`), `uv venv --python 3.14 <tmp>/venv314` y
  `uv pip install --python <tmp>/venv314/bin/python -r tests/requirements.txt`. Suite:
  `<tmp>/venv314/bin/python -m pytest -p no:cacheprovider -o addopts="" -W error::DeprecationWarning`.
- **API key de Riot (pruebas)**: está guardada en `.env` (`RIOT_API_KEY`, archivo ignorado por git). Es una development
  key: caduca 24 h después de generarse (se dio el 2026-11-19 por la tarde). Si Riot la rechaza, pide al usuario una nueva
  y pégala en *Datos y API* (se recarga en caliente). No la copies a archivos versionados.
- Arranque: **solo `run.py`** (crea el entorno: Windows `%LOCALAPPDATA%\TempuDrafter\venv-pyXY`, macOS/Linux `.venv`;
  instala `requirements.txt` cuando cambia; se relanza dentro; candado de una sola copia en `data/.run.lock` +
  `data/run.json`; `/api/ping`). Tests: `tests/requirements.txt`. Cachés de pytest/ruff en `.cache/`.
- Servidor de pruebas: crea `.claude/launch.json` (config `tempu`) con `runtimeExecutable: /usr/bin/env` y
  `runtimeArgs: ["DATA_DIR=<scratchpad>/tempu-data", "<repo>/.venv/bin/python", "<repo>/run.py", "--no-browser",
  "--port", "8765"]` (así `DATA_DIR` va al scratchpad y no se ensucia `data/`). **Bórralo al terminar** (no es del usuario).
- Datos de prueba: la BD del scratchpad de cada sesión se pierde. Lo mejor (4.ª sesión): bajar las partidas del propio
  usuario (FNX Tempuro#FNIX, EUW) con un script que use `RiotClient` en prioridad LOW (match + timeline a JSON) y
  sembrar la BD de pruebas con `store.save_profile` + `store.save_game` (la app no las vuelve a pedir); así se revisan
  las detecciones con partidas reales suyas. `POST /api/coach/reanalyze` fuerza el re-análisis tras cambiar el código.
  Al reiniciar el servidor de pruebas, espera a que el proceso anterior termine antes de `preview_start`.
- **Datos del usuario**: `data/legacy/matchups_stats.json` (88 MB de estadísticas de matchups de antes de la migración;
  lo importa `app/ingest/legacy.py`) **no se borra nunca**. El 2026-11-19, con el visto bueno del usuario, se borraron
  los ficheros del sistema antiguo que nada usaba (`preload.json`, `region_cache.json`, `cache_jugadores_leidos.json`,
  `nombres_a_corregir.json` y `data/historial_coachings.json`; recuperables desde el historial de git). Cualquier dato
  del usuario en `data/`: enseñarle qué es y esperar a que decida.
- **Otra copia de la app usa la misma key** (seguramente la del usuario en Windows): en las pruebas de la 2.ª sesión de
  noche ocupaba ~85 de 100 peticiones/2 min en `europe`, `euw1`, `kr`, `na1` y `americas` (a ratos libres: se pausa
  cuando juega). Con el recolector en prioridad baja, la copia de pruebas apenas avanza en esos hosts: prueba en un host
  libre (mira *Límite de la API*, ahora dice cuántas peticiones son «de otra app») y no le quites cupo al usuario.

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

## 4. (Histórico) pendientes de la 1.ª sesión — ya resueltos

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

## 3 ter. Sesión del 2026-11-19 (noche, 2.ª) — hecho, sin commitear

**OTPs sin tope de partidas** (`app/ingest/crawler.py`, reescrito el modo por campeones):
- Antes cada OTP se quedaba en **100 partidas y 60 días** (una sola petición sin paginar). Ahora se baja **su historial
  entero** con match-v5 paginado de 100 en 100 hacia atrás con `endTime` fijo (el «ancla»: la lista no se mueve aunque
  siga jugando, así que `start` siempre apunta al mismo sitio) y lo posterior al ancla como «partidas nuevas». Probado
  contra Riot: el n.º 1 de EUW tenía 930 partidas de SoloQ esta temporada; con `endTime` fijo la paginación es estable y
  el prefijo es el mismo con o sin `startTime`.
- Profundidad elegible (`CrawlerConfig.otp_depth`, selector «Historial de cada OTP» en *Datos y API*): **temporada
  actual** (por defecto: 1 de enero del año del parche, 16.x = 2026, y se para en la primera partida de un parche
  anterior), 90/180/365 días o todo lo que guarde Riot. Si se pide más profundidad que la vez anterior, se reabre.
- Reparto (`_next_otp`): recién encontrados → partidas nuevas (cada `recrawl_hours` o si quedaron pendientes) →
  historial antiguo empezando por quien menos lleva. Cada visita baja ≤ 100 partidas (`OTP_VISIT_MATCHES`).
- Progreso en la tabla nueva `otp_progress` (ancla, offset, completo/motivo, partidas revisadas y con el campeón); se
  guarda al empezar, cada 25 partidas y al terminar (sigue donde se quedó aunque se cierre la app). Cambiar Flex sí/no
  recorre de nuevo la lista (lo conocido no se descarga).
- Parada por «ya no lo jugaba»: una página del historial con ≥ 30 partidas revisadas y ≤ 10 % con esos campeones.
- **Búsqueda de OTPs en un hilo aparte** (`OtpScout`): maestría y ligas van al host de plataforma (`na1`, `kr`…) y las
  partidas al de región (`americas`, `asia`…), que Riot limita por separado. Probado con NA Aspirante + Riven: en un
  minuto, 82 partidas y a la vez 82 maestrías revisadas, cada host al 85 % (tope de prioridad baja). Primero se revisan
  los jugadores con más LP.
- Corregido: con más de 300 jugadores revisados que no eran OTP, los OTPs ya visitados **no volvían a visitarse**
  (consulta `LIMIT 300` ordenada con NULL primero). Ahora el filtro por campeón va en SQL (`otp_rows`, `json_extract`).
- Una partida en la que el OTP jugó otro campeón **se guarda igual si otro OTP buscado jugó en ella uno de los
  campeones elegidos** (antes se descartaba). Un error propio de una partida (400/403/422) se recuerda y no atasca el
  historial; un OTP que falla 3 veces seguidas descansa 1 h.
- `/api/crawler/otps`: solo los servidores/elos elegidos, total de partidas y WR con el campeón, estado del historial
  (`queued`/`loading`/`done`/`share`), % de sus partidas con el campeón, total general; la web lo muestra en «OTPs
  encontrados» y en la tarjeta de cada región («OTPs: N encontrados · M con el historial completo · búsqueda …»).
- Riot ID del OTP también en su primera visita si aún no se conocía.
- `RiotClient.match_ids(end_time=…)`.

**Key compartida**: `app/riot/ratelimit.py` mide lo que Riot cuenta y este proceso no pidió (`foreign` en el
snapshot, sin contar los 130 s tras arrancar). *Datos y API* avisa en *Límite de la API* y en la tarjeta del
recolector: «Otra app está usando tu misma API key…».

Tests: 16 nuevos en `tests/test_crawler_otp.py` (paginación > 100, profundidad, partidas nuevas tras el historial,
reparto entre OTPs, parada por «ya no lo jugaba», parche de la temporada anterior, > 300 revisados, partida de otro OTP,
error de una partida, estados, endpoint) y 2 en `tests/test_ratelimit.py`. **584 tests en verde en Python 3.12 y
3.14.10** (`-W error::DeprecationWarning`). Verificado en el navegador (escritorio y 375 px) con datos reales de NA.

## 3 quater. Sesión del 2026-11-19 (noche, 3.ª) — hecho, sin commitear

- **«Qué haces mal»** (`app/coach/insights.py`, `web/js/views/coach.js`): antes, con las referencias fijas y comparando
  con su propia liga, al usuario le salía 1 punto. Ahora:
  - **Errores que repites**: los momentos que el análisis marca en cada partida se guardan en el resumen (`digest["mk"]`,
    `ANALYSIS_VERSION = 6`, se re-analiza solo) y se agrupan por tipo (`MISTAKE_TYPES`, `recurring_mistakes`): «en N de M
    partidas (X veces)» con enlaces al segundo exacto de la repetición (`#/repeticiones/<id>/<segundo>`). Solo con ≥ 3
    partidas y ≥ 20 % de las fiables; no cuentan peleas ganadas, cambios, AFK, build completa ni inferioridad numérica. Si
    coincide con un punto débil por métrica, se une a él. Con sus 27 partidas: 5 puntos en vez de 1.
  - **Liga actual** (league-v4, 1 petición/hora en el seguimiento, `settings["rank"]`) y aviso «Compararme con Esmeralda»
    si su elo objetivo no está por encima de su liga (`benchmarks.next_target`).
- **Análisis** (auditoría): una muerte 1 contra 1, ejecutada o claramente evitable (adelantado, gank sin ward) ya no se
  «cambia» por objetivos o torres de otra parte del mapa (había 14 casos reales); morir aislado (+0,7) o en la jungla
  rival a mitad de partida (+0,5) pesa más (antes «Leve»).
- **Recolector de OTPs** (revisión de descargas, todo reproducido y con test): descartes «otro campeón» por OTP
  (`otp_skips` con el campeón; los globales antiguos se liberan una vez al arrancar), la parada «ya no lo jugaba» nunca
  en sus 200 más recientes y con 2 páginas flojas seguidas, cambiar campeones/filtro/Flex recorre de nuevo la lista
  (`otp_list_key`), cursor propio de partidas nuevas (`fwd_newest`), ancla 2 h atrás, `fwd_more` hasta acabar la pasada.
- **Gemini eliminado**: `app/coaching/gemini.py`, `youtube.py` (convertir vídeos en guías dependía de Gemini),
  endpoints, config, `.env.example`, `requirements-coaching.txt` (traía PyTorch), web y README. No había claves en `.env`
  ni en el historial de git.
- **Raíz ordenada**: `run.py` único (autosuficiente, ver §2), fuera `launcher.py`, `iniciar.bat`, `.gitattributes`;
  `docs/` (CONTINUAR.md, coaches.md, enlaces.txt privado sin lo de Gemini), `tests/requirements.txt`, cachés en `.cache/`.
  Revisión de Windows aplicada: entorno sin pip se repara, candado de instalación, Ctrl+C cierra en ~3 s aunque la web
  esté abierta (probado: código 0), QuickEdit desactivado (la consola ya no congela la app), tipos MIME fijos (.js como
  `text/plain` en algunos registros dejaba la web en blanco), `LOL_INSTALL_DIR` se lee del `.env`, llamadas locales sin
  proxy, prioridad baja después de abrir el navegador. Probado desde cero con Python 3.14: entorno + dependencias +
  servidor en ~8 s; segunda copia solo abre el navegador.
- Frontend: *Datos y API* ya no se queda en «Cargando…» al volver; sin scroll lateral en «Todas tus estadísticas» a 375 px.
- **590 tests** en verde en Python 3.12 y 3.14.10 (`-W error::DeprecationWarning`).

## 3 quinquies. Sesión del 2026-11-19 (noche, 4.ª) — hecho, sin commitear

Datos reales para probar: se descargaron las 60 últimas partidas del usuario (58 normales de reclutamiento, 2 Swiftplay;
parches 16.21-16.23; juega jungla, casi siempre Hecarim; liga Platino IV) al scratchpad de la sesión (se pierde). Para
repetirlo: un script con `RiotClient` (prioridad LOW) que guarde match + timeline en JSON, y otro que siembre una BD de
pruebas con `store.save_profile` + `store.save_game` (así la app no vuelve a descargarlas).

- **Macro con relojes de 2026** (`app/coach/clocks.py` nuevo, `ANALYSIS_VERSION = 7`, se re-analiza solo al arrancar):
  vuelta a base a destiempo, llegada tarde, pelear junto a un objetivo con uno menos, pink sin usar, barón sin
  aprovechar, morir solo con la mejora, pelea ganada sin convertir (≥ 30 s de ventaja real), cazado en su mitad,
  recompensa regalada, objetivos del otro lado hasta el 20 para top/ADC/support, Ornn compra en cualquier sitio. Todo
  revisado caso a caso con las 58 partidas: cifras y lo descartado (gank previsible: el lado de inicio del jungla no
  predice su primer gank) en `docs/macro.md`, apartado **C bis**. Métricas nuevas: `obj_late_recall`,
  `obj_late_arrival`, `pink_unused`, `baron_wasted`, `fights_unconverted`, `deaths_collapsed`, `deaths_outnumbered_obj`
  (None sin relojes fiables), `max_lead`/`max_lead_min`/`max_deficit`, `riot_afk`; `result["clocks"]`.
- **«Aprende esto»** (`app/coach/learn.py`, `web/js/components/learn.js`, `/api/coach/learn`): cada punto débil, error
  repetido y momento del visor enlaza 2-3 vídeos gratis (primero en español; solo URLs verificadas de
  `docs/coaches.md`, un test lo comprueba), un ejercicio y su propósito.
- **Hábitos** (*Mi progreso*, «Hábitos: tilt, sesiones y pool», `insights.habits`): victorias tras 2 derrotas seguidas
  (solo avisa con caída clara) y por número de partida en la sesión, sesiones largas, pool por rol, **ventajas que se
  escapan** (derrotas con +2.000 de oro de equipo, con enlace al minuto), remontadas y revisiones de la semana. Con los
  datos reales: 6 de 23 derrotas yendo +2.000; 43 % de victorias en la 4.ª partida o más de una sesión.
- **Revisión guiada** (visor, desplegable): 3 momentos (los más graves, de tipos distintos) con las 3 preguntas de Mauro;
  se guarda solo (tabla `my_reviews`, `GET/PUT /api/coach/games/{id}/review`, `api.put`). Probado en el navegador.
- **Referencias calibradas** (`benchmarks.References(svc, tier, current=tu liga)`, tabla `peer_samples`,
  `GameTracker.sample_peers`): los otros 9 jugadores de cada partida tuya se analizan en segundo plano con el mismo
  código (0 peticiones, ~5 ms por jugador); con ≥ 20 muestras de tu rol, su media sustituye a la orientativa, escalada
  de tu liga a tu elo objetivo. Excluidas las métricas espejo (Δ CS/oro frente a tu rival, ganks del jungla rival).
  La web dice «referencia calibrada con N jugadores de tus partidas». Hallazgo con datos reales: la referencia fija de
  «muertes justo antes de un objetivo» era 0,32 y los junglas reales hacen 1,6 (el usuario 1,1): era un falso punto
  débil. Suelos nuevos: `idle_pct` 10 pp (los junglas reales tienen ~0 % de minutos vacíos) y `time_dead_pct` 3.
  Con todo esto el usuario no tiene puntos débiles de media frente a Esmeralda (es mejor que sus rivales en casi todo)
  y sí 4 «errores que repites».
- **Auditoría del análisis** (resto, con efecto medido en las 60 partidas): gank solo si moriste en tu línea o en el río
  junto a ella; 1 contra 1 sin muerte mutua ni asesino de fuera en la fase de líneas (38 → 30); ganks rivales en mid, o
  estando tú en base o a < 5.000, no son «la otra mitad» (21 → 9, como decía la auditoría); pinks destruidas asignadas con
  prudencia (la tuya solo cae si era la única viva); una misma torre no compensa dos objetivos; `wasAfk` de Riot excluye
  la partida de las medias (52 → 47 fiables); galletas (2010) y calzado de la runa (2422) ocupan hueco aunque lleguen sin
  compra; «Cumples tus propósitos» ya no sale sin propósitos; métricas que no son recuentos exigen que el intervalo al
  80 % quede fuera (una sola partida mala ya no basta); severidad **«info»** (desconexiones, empujón final, objetivo
  imposible, build completa, compensado estando muerto): no cuenta como error, no para la reproducción, chip propio.
- **Referencias con partidas reales de tu elo objetivo** (`app/coach/calibrate.py`, `TierCalibrator`, hilo
  `tier-calibrator`, `CALIBRATE_ENABLED=1` por defecto): cada 10 min una tanda de 6 partidas de SoloQ de tu elo
  objetivo (league-v4 rotando divisiones y páginas → 2 partidas por jugador → match + timeline, ≈ 13 peticiones en
  prioridad LOW; en pausa mientras juegas; para a las 80 partidas) y se analizan los 10 jugadores con el mismo código
  (`peer_samples` con `source = 'tier:EMERALD'`). `References` las prefiere (≥ 20 por rol, sin escalar) a las de tus
  partidas; la web dice «referencia de N jugadores en partidas reales de Esmeralda» y el progreso de la calibración
  (`summary.calibration`). Probado contra Riot: la primera tanda (6 partidas) tardó ~70 s; los junglas de Esmeralda
  hacen 69,5 CS a los 10 (tus partidas escaladas daban 63,8) y ~1,6 muertes justo antes de un objetivo.
- **Frontend** (de la revisión pendiente, cada uno comprobado en el navegador): el visor de repeticiones ya no se
  recarga solo al llegar datos pendientes (`skipSoftReload`: cortaba la reproducción y la revisión guiada); *Datos y
  API* no se repinta mientras eliges en un desplegable o escribes, y el botón del backtest no se reactiva a medio
  cálculo; el modal quita su `keydown` al cerrarse con clic; un enlace mal formado (`%E0…`) ya no tumba el router; el
  indicador «Completando datos» era 0×0 en móvil (la regla `span:last-child` ocultaba el spinner); la barra superior
  desbordaba entre 861 y 1280 px (148 px de más a 900 px en *Campeones*: ahora 0 a 865, 1050 y 1250 px en todas las
  vistas con filtros).
- **Descargas**: `_foreign` (uso de otra app con tu key) se poda al añadir (sin la web abierta crecía sin límite) y los
  hilos de descarga (`external/base.py`, ahora `_executor()`) se cierran al apagar (`shutdown_fetches`).
- **Segunda tanda (tras «sigue hasta el 95 %»)**:
  - Frontend: testigo de «carga más reciente» (`latest()` en `ui.js` + `routeToken` en el router): una respuesta lenta
    ya no se pinta encima de la vista nueva (ficha de campeón, *Matchups*, *Jugador*, visor, *Mi progreso*, *Datos y
    API*; probado abriendo dos repeticiones seguidas); el draft ya no guarda en caché recomendaciones incompletas; el
    chip «Completando datos…» se oculta si la vista cambió su URL con `replaceState`; un filtro guardado que ya no
    existe vuelve a su valor por defecto; el aviso sin conexión ya no menciona `iniciar.bat`.
  - Descargas: OP.GG devuelve **en silencio el parche actual cuando pides uno que ya no tiene** (comprobado: pedir
    16.10 da 16.23) y se guardaba como si fuera el pedido: ahora no se guarda, ni una página sin la marca de
    elo/parche (sí un vacío legítimo). Lo mismo en la tier list (`"version"` de los filtros aplicados; comprobado en vivo:
    16.23 → 62 campeones, 16.10 → no se guarda). Cortacircuitos por web (`CachedSource.trip`: 429/403/503 → nada a esa web
    durante `Retry-After` o una espera creciente; *Datos y API* lo muestra). Prioridad baja de Riot **espaciada** a lo
    largo de la ventana (`Priority.LOW`, `_Bucket.pace`): misma cuota, pero una búsqueda tuya de 22 peticiones con el
    recolector a tope pasa de 63 s de mediana (peor 115 s) a 9 s (peor 10 s), medido con el limitador simulado.
  - Windows: `game.cfg` se escribe de forma atómica (temporal + `os.replace`) y conserva sus permisos (también el
    «solo lectura»); con la PBE instalada se prefiere el cliente abierto y la versión normal; «en partida» se vigila
    en su propio hilo (`lol-client-watch`), así una sincronización larga no retrasa la pausa de las descargas.
  - Comprobado y descartado: `ITEM_DESTROYED` 2001 («Retirada») **no** marca cada vuelta a base (0,9 por jugador y
    partida): no sirve para detectar vueltas sin compra.
  - Referencias de recuentos (muertes de un tipo, objetivos…): una calibrada necesita ≥ 10 sucesos
    (`MIN_REF_EVENTS`; con 12 partidas de Esmeralda, «1 contra 1» salía de solo 2 muertes en 24 junglas y daba un falso
    punto débil) y la comparación suma también la incertidumbre de la referencia (`_count_beyond(..., ref_n)`). Con
    referencias reales de Esmeralda el usuario no tiene puntos débiles de media (por encima en 23 de 32 métricas); el
    aviso de *Mi progreso* lo dice así y remite a los errores que repites.
- **Limpieza** (pedida por el usuario: «borra lo que sea inútil y no se use»): los 5 ficheros de datos del sistema
  antiguo que nada leía (ver §2), código muerto (`routing.platform_from_match_id`, `analyzer._item_name`,
  `CachedSource.is_fresh` y `_cache_get`, `metrics.pair_analysis` y `strip_private`, constantes `MAP_MIN`, `MAP_MAX` y
  `CAMP_LABELS`, dos `import` sin uso), dos reglas CSS sin uso (`.lane-row`, `.game-main`), textos de funciones que ya
  no existen (la biblioteca de guías vacía decía «genera una con IA o procesa un vídeo»), entradas de `.gitignore`
  de ficheros que ya no existen (y se añade `data/riot_key_state.json`, que la app genera), cachés y temporales.
  Comprobado: ningún JS, `import` de JS ni variable de `.env.example` sin uso; las imágenes de rango sí se usan (ruta
  dinámica).
- Consejos del primer gank corregidos con datos; `run.py` arranca en cuanto la copia anterior suelta el candado (antes
  salía con error si se reiniciaba muy rápido). README actualizado.
- Tests nuevos: `tests/test_coach_macro.py` (19), `tests/test_coach_habits.py` (9), `tests/test_coach_audit_2026.py`
  (10), `tests/test_coach_calibrate.py` (6), más flujo de API y referencias en `tests/test_coach.py`. **646 tests en verde en Python 3.12 y 3.14.10**
  (`-W error::DeprecationWarning`; los 2 avisos son de pytest/Starlette, no del código).

## 3 sexies. Sesión del 2026-11-22 — rol por campeón en la búsqueda de OTPs (sin commitear)

- Petición: «al seleccionar OTPs, cada campeón pueda especificar el rol que busco y que se guarden las partidas de ese
  rol» (ej. Sylas jungla o mid).
- `CrawlerConfig.champion_roles` ({campeón: [roles]}, sin entrada o los 5 = cualquier rol) + `is_target(champ, role)`;
  `_ingest`, `_other_target`, `hits`/«ya no lo jugaba» y `_known_for` usan campeón **y rol**. `otp_skips.role`
  (migración `ALTER TABLE`; `''` = partida sin rol fiable, `NULL` = descarte antiguo → se vuelve a mirar si ese campeón
  tiene rol). Los roles entran en `otp_list_key`/`otp_config_key` solo si hay alguno (las claves de antes no cambian).
- `target_sql(cfg, alias)` para contar en `/api/crawler/otps` solo las partidas en ese rol; la respuesta trae
  `champion_roles` y el `role` de cada partida. `/api/crawler/start` acepta `champion_roles` por id o nombre.
- Web (`data.js` + CSS `.otp-champ`/`.role-toggle`): cada campeón elegido tiene 5 botones de rol; en móvil solo iconos.
- Tests nuevos en `tests/test_crawler_otp.py` (suite entera en verde).

## 3 septies. Sesión del 2026-11-22 (2.ª) — panel de OTPs por campeón (sin commitear)

- Quitado `matchups_stats.json.bak` del repo (copia antigua de `data/legacy/matchups_stats.json`, con 10 partidas menos;
  sigue en el historial de git) y `*.bak` en `.gitignore`: commit `6b2bfcf`, subido con permiso del usuario.
- `/api/crawler/otps` ahora da `by_champion` (por campeón elegido: OTPs, partidas, WR, reparto por rol, servidores,
  mejor elo, 3 con más partidas, historiales completos) y la lista de jugadores con filtros `champ`, `platform`, `q`,
  `sort` (games|elo|winrate|points|name), `limit`/`offset` y `matched`. En foco «maestría» un jugador sale bajo cada
  campeón de su top 3 que cumpla. Cada jugador trae `roles` (partidas por rol).
- Web: «OTPs encontrados» = una tarjeta por campeón; al pulsarla, ventana (`modal`) con KPIs, barra de roles, buscador,
  servidor, orden y lista paginada de 25 en 25 («Ver más»). Estilos `.otp-card*`, `.otp-player`, `.otp-dot`…
  (`.otp-game` se mantiene: la usa Mi progreso).

## 3 octies. Sesión del 2026-11-22 (3.ª) — base de datos de OTPs para aprender de ellos (commit 639e13b + sin commitear)

- **Objetivo del usuario**: recopilar a fondo partidas de OTPs de mucho elo (~10 campeones) para que luego Claude pueda
  dar planes de partida por draft/matchup (por dónde pathear, a qué objetivos jugar, invadir o no, qué build…) y
  ajustar «Mi progreso» (umbrales, falsos positivos). Datos de alto nivel = menos errores.
- `app/ingest/otp_details.py`:
  - `build_dossier` = `analyze_game` desde el OTP (sin el `replay` de los 10) + runas, hechizos, `riot_stats`,
    `challenges`, frames minuto a minuto suyos y del rival, compras/ventas/habilidades/wards, eventos del mapa, elo del
    OTP en esa partida (`tier`, `division`, `lp`) y `context` (DOSSIER_VERSION 2): las 5 líneas (aliado, rival,
    ventaja de oro/XP/CS a los 5/10/15/20, kills/muertes antes del 15), presencia de cada jungla (min 2-14 por zona,
    takedowns antes del 15 por línea, primer takedown), todos los objetivos y la primera torre.
  - `save` → tabla `otp_games` (bruto de partida y timeline + dossier, zlib; columnas `tier`, `division`, `lp`) y
    **escritura automática** en `<DATA_DIR>/otps/<Campeón>/<ROL>/<servidor>_<RiotID>_<puuid8>/` (`perfil.json` +
    `partidas.jsonl.gz`, una línea por partida, append). `Files` (por carpeta): resúmenes `<Campeón>/resumen.json`
    por rol (`all`) y por elo (`by_tier`) en un hilo, como mucho cada 5 min por campeón y al parar el recolector;
    `rebuild()` reescribe la carpeta desde la BD (al arrancar el recolector si falta o cambia `LAYOUT_VERSION`).
  - Resumen por rol/elo: métricas (todas, y en victorias/derrotas), builds, habilidades, runas, hechizos, primer
    campamento, rivales, `analysis_flags` (avisos de «Mi progreso» → posibles falsos positivos), `matchups` (rival de
    línea: WR y ventaja a 10/15), `lanes_impact` (WR con cada línea por delante/igualada/detrás a los 15),
    `objectives_first`, `first_tower_lane` y, en jungla, `jungle_presence` (también según qué línea gana a los 10).
  - `reanalyze` rehace dossiers desde el bruto (sin Riot) y regenera la carpeta. Aún sin botón.
- Recolector: `save_details` (sí por defecto), timeline de cada partida suya con el campeón y rol buscados (2 descargas
  en el reparto), relleno de las ya guardadas (prioridad 3 en `_next_otp`).
- API `/api/crawler/otps` → `details` (partidas, OTPs, por elo, carpeta). `POST /api/crawler/otps/files/rebuild`.
- Web: casilla en el recolector y barra informativa (sin botón: se guarda solo).
- **Siguiente paso** cuando el usuario devuelva `data/otps`: leer los `resumen.json` y usarlos para planes de partida
  en el draft/matchups y para ajustar `app/coach` y «Mi progreso».

## 3 nonies. Sesión del 2026-11-22 (4.ª) — repeticiones .rofl de este ordenador (sin commitear)

- Petición: buscar la carpeta de los `.rofl` (por defecto en Windows o, si no, por todo el disco), revisarla cada vez
  que se arranca con `run.py` y recordarla por sistema operativo.
- `app/replays_local.py` (`svc.local_replays`, arranca en `Services.startup`): `locate()` = guardada
  (`meta replays_dir:<os>`) → la del cliente abierto (`/lol-replays/v1/rofls/path`, si cambia se actualiza) → por
  defecto (`Documentos/League of Legends/Replays`, también OneDrive y Documentos movido vía registro; Wine en Linux)
  → búsqueda por todo el disco en anchura (casa primero, luego unidades; salta carpetas de sistema `SKIP_NAMES`). Si
  no hay ningún .rofl, se usa la carpeta por defecto vacía y no se repite la búsqueda hasta 12 h. Reindexa cada 3 min.
- Tabla `local_replays` (cabecera: parche, duración, estadísticas finales de los 10; formatos clásico y nuevo).
  **Los paquetes de la partida van cifrados y cambian cada parche: no se decodifican** (no dan posiciones).
- API: `GET /api/replay/local` (también `match_ids`), `POST /api/replay/local/rescan` (`{path}` = carpeta manual).
  Web: desplegable «Repeticiones .rofl de este ordenador» arriba de **Repeticiones** (`web/js/components/rofl.js`;
  antes estaba en Datos y API y el usuario no lo encontraba) y etiqueta «.rofl en tu PC» en cada partida que la tiene.
- Ideas: marcar en «Repeticiones»/«Mi progreso» qué partidas tienen ya su .rofl (abrir sin descargar).

## 3 decies. Sesión del 2026-11-22 (5.ª) — búsqueda de OTPs más rápida (sin commitear)

- Petición: aprovechar al máximo las llamadas en la búsqueda de OTPs («aunque busque 1 solo campeón tarda mucho»).
  El cuello de botella era **encontrar** OTPs (1 llamada de maestría por jugador del ranking, ~85 cada 2 min por
  plataforma), no descargarlos.
- `OtpScout` ahora es **uno por plataforma** (antes uno por región: EUW y EUNE se turnaban un mismo hilo aunque Riot
  les da cupos separados) y recorre el ranking **de más a menos elo** (antes alternaba elos en rueda).
- `_discover` revisa primero a quien ya se ha visto jugando esos campeones en partidas guardadas.
- `_seed_from_leaderboards`: al empezar, la tabla de los 10 mejores de cada campeón por servidor del MCP de OP.GG
  (`lol_list_champion_leaderboard`, una vez al día por campeón y plataforma; tabla `otp_sources`). Cada uno cuesta
  1 llamada de account-v1 (el PUUID de OP.GG no vale para nuestra key) + 1 de maestría. Cuentan como OTPs de ese
  campeón aunque su maestría no sea la de un OTP estricto (`otp_rows` columna `sourced`, `sourced_champs`).
- Buscar (maestría, ligas: host de plataforma) y descargar (partidas, timeline: host de región) **no comparten cupo**:
  bajar partidas no frena la búsqueda. Con «todos los servidores» la versión anterior tenía 1 búsqueda por región
  (Europa: 5 plataformas en un solo hilo); ahora 1 por plataforma. La web muestra jugadores revisados/min por región.
  Mínimo de maestría con pasos de 50 K y 200 K (en Platino–Esmeralda muchos OTPs no llegan a 300 K).
- `RateLimiter`: si nadie usa la web en 10 min (ninguna petición HIGH), la reserva del recolector baja del 15 % al
  5 % (`IDLE_AFTER_S`, `IDLE_RESERVE`); vuelve al 15 % en cuanto hay una petición interactiva.

## 3 undecies. Sesión del 2026-11-22 (6.ª) — «Mi progreso» calibrado con datos de OTPs (sin commitear)

- El usuario subió `data/otps` (commit 05a2534): 704 partidas de jungla con timeline, casi todas de Aspirante
  (Hecarim 492, Lee Sin 206, Viego 5, Sylas 1). Para leerlas: `gzip` + una línea JSON por partida
  (`data/otps/<Campeón>/JUNGLE/<OTP>/partidas.jsonl.gz`); el `resumen.json` trae lo agregado.
- Método: lo que el análisis marca a jugadores Aspirante en muchas partidas = umbral mal puesto. Se replicó la
  puntuación de gravedad de `_classify_death` con los `tags` guardados (coincide en 3.179/3.179 muertes) para medir
  cada cambio antes de hacerlo.
- `analyzer.py` (ANALYSIS_VERSION 8 → tus partidas se reanalizan solas):
  - Antes de un objetivo: +0,5 (no +1) si en esa pelea cayeron ≥ 2 aliados (el error es la pelea, no tu muerte).
  - Jungla: la jungla rival no suma gravedad por sí sola, y si fue una pelea con ≥ 2 aliados caídos el título es
    «Muerte en pelea» (antes «Muerte en la jungla rival»). Resultado en Aspirante: graves 0,23 → 0,11 por partida.
  - La R solo se exige a nivel 6 (a 11/16 los Aspirante la retrasan a propósito en el 11 % de partidas; a 6, 1 de
    704). Sin aviso de botas al jungla (32 % de Aspirante sin botas mejoradas al 20).
- `benchmarks.py` BASE de JUNGLE revisada: nunca más exigente que Aspirante en lo que ellos hacen «peor»
  (muertes antes del 14 → 2,0; antes de un objetivo → 1,0; tiempo muerto → 8,8 %; objetivos perdidos muerto → 0,4) y
  más cerca de su nivel donde era muy blanda (CS/min 6,6, CS10 68, oro/min 415, impacto antes del 10 4,5, CS de
  jungla 62, muertes sueltas/aislado/sin visión más bajas). `tests/test_coach_otp_calibration.py`.
- Pendiente con más datos: otros roles (solo hay jungla), referencias por campeón (Hecarim y Lee difieren en visión
  y farm), planes de partida por draft con `context` (líneas, presencia del jungla, objetivos).

## 3 duodecies. Sesión del 2026-11-22 (7.ª) — variedad de OTPs antes que cantidad (sin commitear)

- Petición: «al buscar OTPs de X elo y X servidores, el máximo de jugadores diferentes: variedad por encima de muchas
  partidas de uno solo». Confusión previa: el log `OTPs de 120 en EUW…` era el **id de Hecarim**, no 120 OTPs; ahora
  dice el nombre y cuántos de tus elos se añadieron.
- `CrawlerConfig.games_per_otp` (30 por defecto, 0 = sin tope): mientras alguna búsqueda de la región revisa
  jugadores (`RegionWorker.searching()`), el historial de cada OTP se para al llegar a N partidas con esos campeones
  (`hits`); cuando ya no queda nadie por revisar, sigue con el resto. Las partidas nuevas siempre se bajan.
  `/api/crawler/otps`: historial `capped` («En su tope»), `games_per_otp`, `searching`. Web: «Partidas por OTP
  mientras busca».
- Tabla de OP.GG: solo se añaden (y solo se gasta la llamada en) jugadores de los elos elegidos; si no hay ninguno de
  Diamante o más, ni se pide. El usuario NO quiere OTPs fuera de los elos que marca.

## 3 terdecies. Sesión del 2026-11-22 (8.ª) — más datos de OTPs: Esmeralda frente a Aspirante (sin commitear)

- Datos nuevos (commit 8217813): Hecarim 682 partidas (438 Aspirante, 240 Esmeralda de 12 OTPs, 4 GM); total 894.
  Las del usuario se analizaron en Windows con la versión 7 del análisis.
- Método nuevo: comparar cada aviso entre Esmeralda y Aspirante del mismo campeón. Los que salen 2-3 veces más en
  Esmeralda son buenos consejos (1 contra 1, oro sin gastar, pelea ganada sin aprovechar, heraldo/barón regalados,
  muerte junto al dragón con uno menos). Los que salen igual (o más en Aspirante) no distinguen nada.
- `analyzer.py` versión 9: dragón normal cedido → leve (Esmeralda 10 %, Aspirante 11 %); «inventario lleno de
  componentes» solo si se repite (≥ 2 minutos; Aspirante 6 % frente a Esmeralda 2,5 %).
- BASE de JUNGLE = lo que hace Esmeralda de verdad (muertes 5,7; tiempo muerto 9,8 %; KP 55; oro/min 445; impacto antes
  del 10: 6,0; ganks 1,7…). Visión sin tocar (los OTPs de Hecarim Esmeralda casi no ponen pinks: no es la norma).
- Falta: Esmeralda de otros campeones (solo Hecarim) y otros roles.

## 3 quattuordecies. Sesión del 2026-11-22 (9.ª) — tus partidas en el proyecto (sin commitear)

- Petición: «que las partidas se incorporen al proyecto organizadas» para subirlas a GitHub y que Claude las revise
  (se entendió: las del usuario, las de «Mi progreso»; las de OTPs ya estaban en `data/otps`).
- `app/coach/files.py` (`svc.my_games_files`, arranca en `Services.startup`): cada minuto escribe las partidas de
  `my_games` (estado ready/no_timeline/pending_timeline) nuevas o reanalizadas en
  `<DATA_DIR>/mis_partidas/<ROL>/<Campeón>/<AAAA-MM-DD>_<partida>_<V|D>.json.gz` con `analysis` completo + `match`
  y `timeline` en bruto; `resumen.json` (perfil, por rol/campeón/cola, medias de métricas por rol todas y últimas 20,
  índice) y `LEEME.md`. `.indice.json` guarda (versión, analyzed_at, estado) de lo escrito; si cambia el rol se mueve.
- Para revisar las partidas del usuario: `data/mis_partidas/resumen.json` y luego los `.json.gz` (se pueden
  reanalizar con `analyze_game(match, timeline, puuid=…)`).

## 3 quindecies. Sesión del 2026-11-22 (10.ª) — embudo de la búsqueda de OTPs (sin commitear)

- Queja: con Hecarim, Platino–Esmeralda, todos los servidores, «Mucha maestría» y mínimo 100 K solo salen 34 OTPs
  «en todo este rato». No se vio ningún fallo en el código; faltan los números reales (están en su Windows).
- `search_funnel(db, cfg)` (crawler.py): por plataforma y en total, revisados → con el campeón (top 3 o principal)
  → con el mínimo → OTP estricto (solo en modo OTP) → jugado en el último mes (= OTPs, coincide con `otp_rows`),
  más jugadores del ranking y revisados en la última hora. En `/api/crawler/otps` (`funnel`), en el panel («Búsqueda:»
  y tabla por servidor) y en `data/otps/_busqueda.json` (cada 5 min y al parar; `Crawler.write_funnel`) con la
  configuración y el estado de cada región. **Siguiente paso**: cuando el usuario suba `_busqueda.json`, ver en qué
  fase se pierden (¿pocos revisados/hora → velocidad? ¿muchos con el campeón pero pocos activos → filtro del mes?).
- Mensaje de la terminal «Semillas … +205» → «Ranking … +205 jugadores por revisar (candidatos…)».

## 3 sedecies. Sesión del 2026-11-22 (11.ª) — tus partidas frente a los OTPs de tu campeón

- Llegaron `data/mis_partidas` (51 partidas del usuario, todas normales de reclutamiento; 47 jungla, 27 Hecarim; ya en
  análisis versión 9) y más Hecarim (294 Esmeralda de 17 OTPs, 438 Aspirante). Aún no llegó `_busqueda.json`.
- Sus Hecarim frente a OTPs Esmeralda/Aspirante: mejor en farm (CS10 77 vs 70/82), visión (1,03 vs 0,73/0,99; pinks
  2,9 vs 0,7/2,4) y muertes (4,2 vs 5,8/4,6); peor en ganks con kill antes del 10 (1,3 vs 1,8), muertes aislado tras el
  14 (0,41 vs 0,30/0,20), minutos vacíos desde el 15 (3,2 % vs ~0) y KP (50 vs 52/59). Build igual que los OTPs
  (Lanza de Shojin, Q>W>E). Ojo: normales frente a clasificatorias.
- `app/coach/otp_compare.py` + `otp_compare` en `/api/coach/summary` + sección «Tú frente a los OTPs de tu campeón» en
  Mi progreso (abierta): por campeón y rol con ≥ 5 partidas tuyas, tus medias frente a los OTPs de tu elo objetivo
  (≥ 40 partidas de ≥ 3 OTPs) y del más alto con datos, ordenado de peor a mejor, y su build más jugada. Lee
  `data/otps/<Campeón>/resumen.json`. `tests/test_otp_compare.py`.

## 3 septendecies. Sesión del 2026-11-22 (12.ª) — `_busqueda.json`: la búsqueda va bien, Hecarim es raro

- Informe real (Hecarim, jungla, Platino–Esmeralda, 15 servidores, «Mucha maestría», ≥ 100 K): 11.120 revisados
  (9.608 en la última hora, ~650/h por servidor, sin errores) → 121 con Hecarim en su top 3 (1,1 %) → 83 con ≥ 100 K →
  35 jugado en 30 días. No hay fallo: la búsqueda va a tope; el campeón es raro (~1 OTP por 320 revisados, ~30/hora).
- El filtro de actividad dejaba fuera al 58 % de los candidatos: `CrawlerConfig.active_days` (14/30/60/90/180/365,
  30 por defecto) + «Jugado en los últimos» en la web; el embudo lo usa y lo guarda en la configuración.

## 3 duodevicies. Sesión del 2026-11-22 (13.ª) — biblioteca de OTPs y «limpio de primeras» (sin commitear)

- **Regla general del usuario para todo el proyecto**: de primeras, lo justo (resumen, pocas cifras); el detalle, al
  pulsar (desplegable cerrado, ventana o «ver más»). Todo visible, configurable, filtrable y bonito. Ejemplo que dio:
  «Límite de la API» → una tarjeta por región y el detalle al entrar.
- Nueva página **#/otps** (menú «OTPs», `web/js/views/otps.js`, API `app/api/otps.py`): cifras clave, ranking de
  campeones (más OTPs; a igualdad, mejor elo y más partidas), reparto por elo/servidor/rol (cerrado), lista de OTPs
  con filtros (campeón, rol, elo, servidor, nombre, orden; de 30 en 30) y detalle de cada OTP en ventana (medias frente
  a los OTPs de su elo, build, partidas). Un OTP = jugador + campeón + rol con partidas en `otp_games`; su elo y
  servidor, los de su partida más reciente. `tests/test_otps_library.py`.
- «Límite de la API»: tarjetas por región (uso de la ventana de 2 min del servidor más ocupado y estado); detalle por
  servidor en ventana.
- Primera tanda de limpieza: Mi progreso (5 partidas + «ver más»; muertes, pathing y OTPs cerrados con resumen; tabla
  de métricas de OTPs en un desplegable), Repeticiones (20 + «ver más»; ruta de jungla cerrada), Datos y API (detalle
  de la precarga, del recolector y opciones de la búsqueda de OTPs en desplegables; párrafos → ⓘ con `title`).
  Ojo: `collapsible` recuerda el estado en localStorage (también el abierto por defecto): para cambiar el valor por
  defecto de un panel que ya existía hay que cambiarle el id (se usó `.v2`).
- Cabecera de las tablas: quitado `position: sticky` de `.table th` (se quedaba flotando al bajar la página).
- **Segunda tanda, hecha** (misma regla; 4 agentes en paralelo, uno por vista, revisado en el navegador):
  Matchups (lista a 4 columnas, resto en `title` y en la ventana; 3 KPIs; fuentes, más datos de la línea, cambios y
  build completa en desplegables `matchup.*`), Campeones (tier list a 6 columnas, desglose de WR en `title`;
  sinergias, tus estadísticas y desglose en desplegables `campeon.*`), Jugador (4 KPIs + «Más estadísticas»; tabla de
  campeones 4 columnas × 8 + ventana con la tabla completa; equipos de cada partida en su detalle), Partida (tablas de
  equipo a 5 columnas; líneas en `partida.lineas`; cola/parche/fecha en una línea), Regiones (4 KPIs; objetivos,
  duración, elo y baneos en `regiones.detalle`; meta por rol 4 + «ver más»), Draft («Cómo se calcula» en
  `draft.modelo`; 2 sinergias por lado + «ver más»; detalle de cada línea en `title`), Guías (un desplegable por rol
  `guias.rol.*`; top 6 + «ver más»), Mi progreso (debilidades: título + consejo, «Ver detalle»; errores que repites
  en `progreso.repites`), Repeticiones («Más contexto» en cada momento).
- Comprobar sintaxis JS sin Node: quitar `import`/`export` y parsear con JavaScriptCore
  (`osascript -l JavaScript`, `new Function(texto)`); detecta errores de sintaxis.

## 4 bis. Pendiente (por prioridad)

0. **PRIMERO (petición del usuario, 2026-11-19): runas, objetos, orden y componentes de cada partida según la
   identidad del campeón.** Palabras del usuario, resumidas: «puedo jugar bien y tener las runas incorrectas o los
   objetos que no tocan justo en esa partida, o el orden de los objetos, o sus componentes; depende de la identidad de
   cada campeón: si es de late debería estar tranquilo hasta conseguir algún pico de poder, y todo eso también son
   errores. Analiza builds de OTPs o de jugadores de Gran Maestro o Aspirante (más fiables que las de un plata), lo
   mismo con las runas, y todo para cada campeón, parche, etc.». Lo pidió para un chat nuevo con el contexto vacío.
   - **Qué hay ya**: el recolector (`app/ingest/crawler.py`, modo OTP por campeón y por elo/servidor) guarda en
     `participants` los objetos finales (`items`), hechizos y runas **parciales** (`extractor.py` solo guarda
     [clave, árbol principal, árbol secundario]: faltan las 6 runas, los fragmentos y el orden de compra, que necesita
     el timeline). OP.GG (`external/opgg.py`: `champion_build` y la guía de matchup) da por campeón y rol núcleo,
     botas, iniciales, hechizos, orden de habilidades, 2 páginas de runas (clave, árboles, ids, partidas y WR) y
     **WR por duración de partida** (`game_lengths`: sirve para saber si un campeón es de early o de late). Tu análisis
     (`analyzer._build`) ya reconstruye orden y minuto de cada objeto y compras por vuelta, y `insights.compare_build`
     te compara con la build más jugada de OP.GG (sin runas). `TierCalibrator` (`app/coach/calibrate.py`) ya sabe bajar
     partidas con timeline de un elo en segundo plano: sirve de modelo para bajar las de OTPs/GM/Aspirante.
   - **Referencias de expertos por campeón, rol y parche**: partidas de GM/Aspirante y de OTPs (maestría alta y muchas
     partidas con el campeón; el recolector ya los encuentra) con su **timeline** para tener el orden de compra y los
     componentes. Guardar por partida y jugador experto: runas completas (`perks.styles[*].selections`, `statPerks`),
     orden de objetos terminados, componentes por vuelta, botas, hechizos, orden de habilidades, rival de línea y
     composición rival (daño físico/mágico, curaciones, control). Agregar por (campeón, rol, parche o ventana de
     parches) con frecuencia y WR de cada opción, y mínimo de muestra (sin muestra suficiente: no se juzga).
   - **Juicio por partida (sin falsos positivos)**: solo marcar una elección si casi ningún experto la usa en ese
     contexto (p. ej. < 5-10 % con muestra ≥ 30) y no la justifica la partida (rival de línea, daño que recibes,
     curanderos, si ibas por delante o por detrás). Ejemplos: runa clave o árbol secundario que los expertos no usan
     con ese campeón; primer objeto fuera de su abanico; componentes comprados en un orden que retrasa tu pico (o
     componentes de otro objeto que no vas a hacer); botas o defensivos que no responden al daño rival; no comprar
     Heridas graves cuando los expertos sí la compran contra ese rival. Explicar siempre qué usan los expertos y con
     qué frecuencia/WR, igual que el resto de errores (y con su «Aprende esto»).
   - **Identidad del campeón y picos de poder**: curva de poder por campeón (WR por minuto de OP.GG, minuto típico del
     1.er/2.º objeto en expertos, campeones de escalado conocidos). Detecciones posibles: un campeón de late que pelea
     y muere antes de su pico (p. ej. antes de completar su objeto clave o su nivel clave) cuando no le tocaba, o uno
     de early que no aprovecha su ventana. Cuidado con los falsos positivos: hay jugadas que dependen del contexto.
   - **Dónde se ve**: en el visor de repeticiones (la build y las runas de esa partida frente a las de expertos, con lo
     que no encaja marcado), en *Mi progreso* (errores de build/runas que repites) y en la selección de campeones
     (`lcu.py`: recomendar runas y build de expertos para ese matchup antes de la partida).
   - **Datos de Riot**: match-v5 trae `perks` completos y objetos finales; el orden y los componentes salen del
     timeline (`ITEM_PURCHASED`, `ITEM_DESTROYED`, `ITEM_UNDO`; ver `coach/items.py`, que ya reconstruye el inventario
     y conoce los objetos que llegan sin compra). Data Dragon tiene `runesReforged.json` para nombres e iconos. Cuidar
     el cupo de la key (compartida con otra copia de la app): prioridad LOW y tandas pequeñas, como el calibrador.

1. **Calibración**: comprobar en la app del usuario que llega a 80 partidas de su elo objetivo y revisar entonces sus
   puntos débiles (con las referencias reales de Esmeralda probablemente reaparezcan algunos, ahora con fundamento).
   Si cambia el elo objetivo, empieza otra fuente (`tier:<ELO>`); las muestras viejas se quedan (no molestan).
2. **Coaches → app (resto)**: preparación de cada objetivo como nota positiva (llegar a tiempo, pink puesta, vuelta a
   base 60-120 s antes), checklist en la selección de campeones (`lcu.py`: matchup, cuándo pega el primer objeto del
   rival, a qué minuto ganquea su jungla según tu BD de OTPs, condición de victoria, foco de la sesión), notas propias
   por matchup, glosario.
3. **Auditoría (lo que queda)**: excepciones de campeón (Yuumi, Maokai, campeones con Heridas graves en el kit),
   «inventario lleno» con mejora posible, comparación de build con objetos desconocidos, recuentos sin ajustar por
   duración (las referencias calibradas lo compensan en parte: son de las mismas partidas). Las «Retiradas» (2001/2002)
   y las misiones de rol (1200-1208) llegan sin compra pero no ocupan hueco (comprobado).
4. **Descargas**: las respuestas del MCP de OP.GG (guías, builds, sinergias) no traen marca de parche comprobable;
   el MCP comparte cortacircuitos con la web (separarlos si hiciera falta).
5. Probar en Windows real: `run.py` con doble clic y desde PyCharm, cliente de LoL, pausa en partida, repetición.
6. El usuario tiene **otra copia abierta con la misma key** (ocupa ~85/100 peticiones cada 2 min): la app lo avisa;
   recordárselo.

## 5. Ideas siguientes (no pedidas, por si el usuario quiere más)

- Probar en Windows real: `run.py`, detección del cliente (`lockfile` / `psutil`), pausa en partida y la repetición
  real (`EnableReplayApi`), que aquí (macOS sin cliente) solo se han probado con el cliente simulado en tests.
