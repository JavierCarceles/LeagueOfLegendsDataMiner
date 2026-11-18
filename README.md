# Tempu Drafter

Analizador de drafts, matchups y estadísticas de League of Legends. Combina **datos de OP.GG**
(muestras de cientos de miles de partidas), **tu propia base de datos** (Riot API) y la
**fuerza estadística de cada campeón**. Tiene una interfaz web local.

| Sección | Qué hace |
|---|---|
| **Draft** | Probabilidad de victoria con intervalo, nota S–F, fuerza de cada equipo, los 5 enfrentamientos (con quién gana la línea), sinergias, perfil de daño AD/AP, curva por duración y sugerencias de pick por hueco. Funciona con el draft a medias. |
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
  coaching/    índice de guías, Gemini y YouTube
  api/         endpoints REST (documentación interactiva en /docs)
web/           interfaz (HTML + CSS + módulos JS, sin compilación)
data/          lol.db (generada), legacy/ (datos del proyecto antiguo), coachings/ (guías)
tests/         435 tests offline (pytest)
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
