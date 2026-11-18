# Tempu Drafter

Analizador de drafts, matchups y estadísticas de League of Legends. Combina **datos de OP.GG**
(muestras de cientos de miles de partidas), **tu propia base de datos** (Riot API) y la
**fuerza estadística de cada campeón**. Tiene una interfaz web local.

| Sección | Qué hace |
|---|---|
| **Mi progreso** | Guarda tu perfil y recoge **solas** tus partidas (normales y clasificatorias de la Grieta), sin grabar repeticiones. Detecta qué haces mal (ganks del jungla rival, muertes adelantado o sin ward, oro sin gastar, minutos vacíos, pathing, visión, objetivos), te propone propósitos, sigue si los cumples partida a partida y te dice cuándo estás listo para clasificatorias. Cada partida tiene una repetición 2D y sus momentos clave. |
| **Draft** | Probabilidad de victoria con intervalo, nota S–F, fuerza de cada equipo, los 5 enfrentamientos (con quién gana la línea), sinergias, perfil de daño AD/AP y curva por duración. Los mejores picks de cada hueco salen solos (en el centro de cada línea y arriba de cada selector), con tus campeones si tienes perfil. Funciona con el draft a medias. |
| **Matchups** | Todos los rivales de un campeón: WR ajustado, esperado, ventaja propia (Δ), veredicto y fiabilidad. El detalle incluye fuentes por separado, desglose por parche/elo/región, consejo de OP.GG y la build contra ese rival. |
| **Campeones** | Tier list por rol (OP.GG + tu BD) y ficha de campeón: roles, mejores y peores matchups, sinergias y perfil. |
| **Jugador** | Perfil profesional: rango, forma, KDA, CS/min, Δ CS@10 frente al rival, fase de líneas, campeones, comparación con la media del mismo campeón/rol/elo y partidas detalladas. |
| **Partida** | Marcador, comparación línea a línea, objetivos, nota de rendimiento 0-10, predicción del modelo y timeline de oro. |
| **Regiones** | Meta por región (OP.GG filtrado por región) y, con datos propios, objetivos, duración, lado azul/rojo y elo. |
| **Guías** | Guías guardadas, generación con IA (Gemini, alimentada con las estadísticas reales) y conversión de vídeos de coaching de YouTube en guías. |
| **Datos y API** | API key, sincronización de OP.GG, recolector de partidas, uso del límite de la API, base de datos y calibración del modelo. |

## Puesta en marcha

Necesitas Python 3.10 o superior.

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

```bash
python run.py
```

Abre http://localhost:8000. La primera vez se descarga Data Dragon y se importa el histórico
(`data/legacy/matchups_stats.json`, ~470.000 partidas) a SQLite. Tarda unos segundos.

**API key de Riot.** Pégala en *Datos y API* o en `.env` (`RIOT_API_KEY=…`, ver `.env.example`).
Se recarga en caliente: cuando caduque la development key (24 h), pega la nueva y listo. Sin key
funcionan igualmente el draft, los matchups, la tier list y las regiones con OP.GG. La key solo hace
falta para perfiles, análisis de partidas y el recolector propio.

**Mi progreso.** Entra en *Mi progreso*, escribe tu Riot ID (o pulsa *Detectar del cliente* con el
cliente de LoL abierto) y guarda. A partir de ahí no tienes que hacer nada: mientras la web esté en
marcha, cada 3 minutos se buscan tus partidas nuevas y se analizan. La primera vez se recuperan tus
últimas 50 (configurable).

**Opcional:**
- Guías con IA, YouTube y amigos del cliente: `pip install -r requirements-coaching.txt` y las claves `GEMINI_KEY_*` en `.env`.
- Tests: `pip install -r requirements-dev.txt`, luego `python -m pytest`.

## De dónde salen los datos

1. **OP.GG**
   - **Web pública**: counters completos por campeón y línea, y tier list, con filtros de parche, elo y región.
   - **MCP oficial** (`mcp-api.op.gg`, la API que OP.GG ofrece para apps): guía de cada matchup (quién gana la línea, solo kills, consejo, build contra el rival), sinergias y tipo de daño.
   - Todo se cachea en SQLite: el parche actual 6 h y el anterior 7 días. Las peticiones se hacen a ritmo moderado.
   - onetricks.gg, u.gg y mobalytics bloquean clientes automáticos con un checkpoint anti-bot, así que no se usan.
2. **Tu base de datos** (Riot API, lo que recoge el recolector o las búsquedas) más el histórico importado.
3. **Estadísticas de campeón**: el WR general de cada campeón en su rol da el resultado *esperado* de cualquier enfrentamiento (log5). Así un matchup con pocas partidas se estima por la fuerza de ambos campeones.

## Mi progreso: cómo se analizan tus partidas

- **Sin repeticiones.** De cada partida se descarga el detalle y el *timeline* de match-v5 (2 llamadas):
  la posición de los 10 jugadores al final de cada minuto y todos los eventos con su coordenada (kills,
  wards, objetivos, torres, compras, subidas de nivel). El JSON se guarda comprimido en `lol.db`, así que
  si mejora el análisis se re-analizan solas.
- **Cuándo.** Cada 3 minutos (configurable) mientras la web esté abierta. Si el cliente de LoL está
  abierto en el mismo ordenador, se detecta el final de cada partida y se descarga en cuanto Riot la
  publica (1-3 min). Si la key caduca, el seguimiento se pausa y sigue solo al pegar la nueva.
- **Muertes.** Cada una se clasifica: *gank* (participa alguien que no es de tu línea, sobre todo el
  jungla rival), *1 contra 1*, *adelantado* (más cerca de la torre exterior rival que de la tuya),
  *sin ward* (no pusiste ninguno en los 2 minutos previos), *con oro sin gastar* (≥ 1.200), *antes de
  un objetivo* (el rival coge dragón, larvas, heraldo, barón o Atakhan en los 90 s siguientes), *en su
  jungla* y *aislado* (después del 14, sin aliados a menos de 2.200 unidades).
- **Tiempo.** Minutos vivo y fuera de la fuente con ≥ 1.500 de oro sin gastar, *minutos vacíos* (desde
  el 15: menos de 3 CS, menos de 400 de daño a campeones, sin wards ni participar en kills, torres u
  objetivos) y porcentaje de la partida muerto.
- **Jungla.** Campamento inicial, ruta de los minutos 1-8, presencia en cada línea, ganks con kill antes
  del 10, invasiones, CS de jungla y ganks del jungla rival en la otra mitad del mapa.
- **Referencias.** Cada métrica se compara con una referencia orientativa de tu rol, escalada al *elo
  objetivo* de tu perfil. Si el recolector tiene ≥ 300 partidas de ese elo y rol, se usan las medias
  reales de tu base de datos. Los puntos débiles se ordenan por lo lejos que estás de la referencia.
- **¿Listo para clasificatorias?** Nota 0-100 con tus últimas 10 partidas: 60 % los propósitos que
  cumples y 40 % la nota de fundamentos (0-10, 5 = como la referencia). A partir de 70 y con 10
  partidas analizadas, te dice que es buen momento.
- **Repetición 2D.** Posición exacta cada minuto y en cada evento; entre medias se interpola. Las
  posiciones exactas entre minutos solo existen en la repetición (.rofl), que no se puede leer fuera del
  cliente.

## Cómo se calcula (resumen)

- **WR ajustado (bayesiano):** `(victorias + k·esperado) / (partidas + k)`. Con muchas partidas mandan los datos del enfrentamiento; con pocas, lo esperado por la fuerza de los campeones. `k` se estima de los propios datos (Bayes empírico, ver `app/analytics/priors.py`).
- **Δ ventaja propia:** WR ajustado − esperado. Es lo que aporta el counter más allá de que un campeón sea más fuerte en general.
- **Veredicto de partida:** según el WR ajustado (Muy favorable ≥ 54 %, Favorable ≥ 51,5 %, Equilibrado, Desfavorable, Muy desfavorable). La **fase de líneas** se mide aparte: quién llega al minuto 14 con ≥ 20 % más oro y experiencia, CS a los 10, placas y solo kills.
- **Draft:** modelo logístico aditivo. Suma los log-odds de las 5 líneas y de las sinergias (aliadas menos enemigas): `P = σ(α·L)`. El intervalo de confianza (90 %) sale de la incertidumbre de cada línea.
  - **Backtest:** con ≥ 200 partidas propias, *Datos y API → Backtest* predice cada partida sin usarla (leave-one-out) y calibra `α`, para que un 60 % signifique de verdad ganar 6 de cada 10.
  - **Composición:** se muestra aparte y no se suma a P (ya está recogida en los WR).
- **Recolector (key gratuita: 20 peticiones/s y 100 cada 2 min por región):**
  - Las semillas salen de las ligas oficiales: 205 jugadores con PUUID por llamada.
  - Hay un hilo por región en paralelo.
  - Solo cuenta ranked y descarta duplicadas y remakes, con lo que cuesta ~1 llamada por partida nueva.
  - El limitador lee las cabeceras de Riot, respeta `Retry-After` y deja un 15 % libre para tus búsquedas.

## Estructura

```
app/
  main.py, services.py, config.py, logbus.py   arranque, servicios, .env, logs en vivo (SSE)
  riot/        cliente de la API, limitador adaptativo, routing, gestión de la key
  external/    OP.GG (web + MCP) con caché
  data/        SQLite: esquema y escrituras con agregados incrementales
  ingest/      parseo de partidas, recolector e importación del histórico
  analytics/   estadística, evidencia combinada, matchups, campeones, draft, jugadores, partidas, regiones
  coach/       tus partidas: perfil, seguimiento automático, geometría del mapa, análisis y propósitos
  coaching/    índice de guías, Gemini y YouTube
  api/         endpoints REST (documentación interactiva en /docs)
web/           interfaz (HTML + CSS + módulos JS, sin compilación)
data/          lol.db (generada), legacy/ (datos del proyecto antiguo), coachings/ (guías)
tests/         470 tests offline (pytest)
```

## Cambios respecto a la versión anterior

- El backend monolítico (`backend.py`) y `descargar_coachings.py` se han sustituido por el paquete `app/`, y el frontend por `web/`.
- `matchups_stats.json` (88 MB, se cargaba entero en cada petición) pasa a SQLite. El archivo original se conserva en `data/legacy/` y se importa solo. El código antiguo no filtraba la cola, así que ~7 % de ese histórico son partidas normales: el filtro de parche «Sin histórico antiguo» lo excluye.
- Se han eliminado:
  - El bot de renovación automática de la key (resolvía el reCAPTCHA del portal de Riot).
  - El scraping de leaderboards de OP.GG con Playwright: las semillas salen ahora de la API oficial.
  - `ffmpeg.exe`: se usa `imageio-ffmpeg` o el ffmpeg del sistema.
- Las variables `REGIONSCRAPPER`, `PAGESCRAPPER`, `EMAIL_USER` y `EMAIL_PASS` de `.env` ya no se usan.
- Si tenías la carpeta `coachings/` en la raíz, se sigue usando automáticamente.

Proyecto personal sin afiliación con Riot Games ni OP.GG. Respeta sus condiciones de uso.
