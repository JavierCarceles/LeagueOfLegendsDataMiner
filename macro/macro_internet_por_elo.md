# Macro de internet por elo: lo que aún no estaba en el proyecto

Investigación del 25-11-2026 para la IA que integra la macro en el análisis de partidas. Completa a `macro/macro_general.txt`, `macro/macro_por_rol.txt`, `macro/macro_composiciones.txt`, `docs/macro.md` y `docs/coaches.md`: antes de escribir se hizo un inventario de esos ficheros y del código (`app/coach/analyzer.py`, `insights.py`, `route_macro.py`, `rule_metrics.py`), y aquí solo va lo nuevo o lo que añade cifras a algo que ya estaba (marcado **[amplía]**).

Cómo leerlo:

- Cada regla lleva su **fuente** (sección A), el **elo** al que la asocia la fuente (o «todos» si no lo dice) y una **detección** propuesta con los datos que tenemos: posiciones cada minuto (y la reconstrucción de `analyzer.py` entre minutos: peleas sin muerte, wards rotos, recorridos típicos), eventos (kills con posición, objetivos, torres y placas, wards puestos y quitados **sin posición**, compras), y por minuto el oro, la experiencia, el CS, el CS de jungla, el daño a campeones, la vida (`championStats.health/healthMax`), la velocidad y el control aplicado.
- **Fiabilidad**: casi todo son blogs de coaches (opinión experta, no datos). Las cifras con datos de verdad son las de la sección C (estudios y roflstats) y las de mecánicas de la sección B (wiki oficial y notas de parche). Ninguna regla debería activarse como error sin validarla antes con las partidas guardadas por elo (como se hizo con `track_check` y el perfil por elo).
- Grupos de elo como en `app/coach/elo_profile.py`: Hierro-Bronce, Plata-Oro, Platino, Esmeralda, Diamante, Maestro (con Gran Maestro) y Aspirante.

---

## A) Fuentes

Leídas página a página salvo indicación. Mobalytics, nerfplz, Skill Capped y Sportskeeda bloquean o fallan (403/405): de ellos solo hay extractos del buscador y no se usan para cifras.

**Datos y estudios**
- **[FR]** Hojaji, McIlroy, Majeed, Dupuy, Toth y Campbell (2026), *Frontiers in Sports and Active Living* 8:1982844. API de Riot, EUW, parches 14.1–14.7, de Hierro a Aspirante (casi todo Diamante). https://www.frontiersin.org/journals/sports-and-active-living/articles/10.3389/fspor.2026.1982844/full
- **[RS]** roflstats.com: estadísticas por rango sacadas de repeticiones (posiciones cada segundo, visibilidad, wards), parches 16.17–16.20, ~8,5 millones de partidas, 13 servidores (2026). https://roflstats.com/
- **[UW]** Wen (2025), informe de estudiante UCLA, **no revisado**: 9.879 partidas Diamante a los 10 min. https://astro-wen.github.io/UCLA_C111_LoL/report.html
- No legibles (403/PDF): Computers in Human Behavior Reports 2025 (KPIs de navegación y visión, https://www.sciencedirect.com/science/article/pii/S2451958825001332) y arXiv 2309.02449. Del primero solo se sabe por el buscador que los jugadores de Diamante a Aspirante se mueven más a principio y mitad de partida y pasan más tiempo cerca de los objetivos.

**Mecánicas (wiki oficial y Riot)**
- **[WK-D]** Muerte y reaparición: https://wiki.leagueoflegends.com/en-us/Respawn
- **[WK-B]** Recompensas de campeón: https://wiki.leagueoflegends.com/en-us/Champion_gold_bounties · **[WK-OB]** Recompensas de objetivo: https://wiki.leagueoflegends.com/en-us/Objective_bounties
- **[WK-T]** Torres: https://wiki.leagueoflegends.com/en-us/Turret · **[WK-I]** Inhibidor: https://wiki.leagueoflegends.com/en-us/Inhibitor
- **[WK-V]** Larvas: https://wiki.leagueoflegends.com/en-us/Voidgrub · **[WK-H]** Heraldo: https://wiki.leagueoflegends.com/en-us/Rift_Herald · **[WK-DS]** Dragones y almas: https://wiki.leagueoflegends.com/en-us/Dragon_Slayer
- **[R26.1]** Notas del 26.1 (Riot): https://www.leagueoflegends.com/en-us/news/game-updates/patch-26-1-notes/ · **[R26.3]** Notas del 26.3 (3-feb-2026): https://www.leagueoflegends.com/en-us/news/game-updates/patch-26-3-notes/ · **[RCB]** Riot sobre remontadas (2022): https://www.leagueoflegends.com/en-us/news/dev/quick-gameplay-thoughts-2-25-comeback-mechanics/

**Guías por elo**
- **[WC]** WeCoach, «How to Climb Out of Every Rank» (14-may-2026): https://wecoach.gg/blog/article/how-to-climb-out-of-every-rank-in-league-of-legends (ya citado en `docs/macro.md`, aquí con todo lo que dice por rango)
- **[DG-DM]** Dodge.gg, «How to climb Diamond to Master» (24-mar-2026): https://www.dodge.gg/en-US/lol/news/how-to-climb-diamond-to-master-2026
- **[EN-D]** Esports.net, «How to reach Diamond» (31-oct-2025): https://www.esports.net/wiki/guides/how-to-reach-diamond-rank-lol/
- **[DI-UH]** Dignitas, «Low elo play: how to get un-hardstuck» (23-nov-2025, Hierro-Oro): https://dignitas.gg/articles/low-elo-play-how-to-get-un-hardstuck
- **[DI-SS]** Dignitas, «Sins of Silver» (2015): https://dignitas.gg/articles/sins-of-silver-mistakes-low-elo-players-insist-on-making
- **[DI-TM]** Dignitas, «Top Mistakes in Low ELO» (2016): https://dignitas.gg/articles/blogs/Unknown/10010/top-mistakes-in-low-elo
- **[DI-DP]** Dignitas, «Stop Making Dumb Plays» (2016): https://dignitas.gg/articles/stop-making-dumb-plays-improving-by-learning-what-not-to-do
- **[DI-5W]** Dignitas, «The Five Worst Mistakes» (2024): https://dignitas.gg/articles/the-five-worst-mistakes-that-most-players-make

**Guías por tema**
- **[DI-LP]** Dignitas, guía de prioridad de línea (2020): https://dignitas.gg/articles/the-pro-unspoken-rule-of-league-a-lane-priority-guide · **[DI-PR]** «Priority and its importance» (2021): https://dignitas.gg/articles/priority-and-its-importance-in-league-of-legends · **[DI-TV]** «Transitioning lane priority into tempo and vision» (2016): https://dignitas.gg/articles/blogs/League-of-Legends/10280/transitioning-lane-priority-into-tempo-and-vision-control · **[DI-AM]** «Advanced macro: laning phase» (2019): https://dignitas.gg/articles/advanced-macro-guide-dominate-the-laning-phase
- **[MT-O]** MOBA Trainer, «Which objective to take first» (10-nov-2026, revisado por un Aspirante): https://www.mobatrainer.com/improve/macro-game · **[MT-G]** MOBA Trainer, ganks (10-nov-2026): https://www.mobatrainer.com/guides/ganking
- **[BM]** Boosting Market, prioridad de objetivos (21-oct-2026; blog comercial): https://boostingmarket.com/blogs/lol-objective-priority-guide/ · **[NB]** NeuralBoost, relojes 2026 (1-nov-2026; blog comercial): https://neuralboost.gg/blog/league-of-legends-objective-timers-2026-dragon-baron-herald-voidgrubs
- **[DI-RD]** Dignitas, «A detailed roaming guide» (2020): https://dignitas.gg/articles/blogs/Unknown/14224/a-detailed-roaming-guide-for-lol · **[DI-RW]** «Win the map: roaming» (2020): https://dignitas.gg/articles/blogs/Unknown/14225/win-the-map-roaming-in-league-of-legends · **[DI-RM]** «Roaming in the mid lane» (2022): https://dignitas.gg/articles/all-you-need-to-know-about-roaming-in-the-mid-lane
- **[DD-J]** Dodge.gg, guía de jungla 2026 (24-mar-2026): https://www.dodge.gg/en-US/lol/news/jungle-guide-2026 · **[DI-TJ]** Dignitas, «How to track the enemy jungler» (29-nov-2025): https://dignitas.gg/articles/how-to-track-the-enemy-jungler · **[DI-JM]** «5 jungle myths» (11-dic-2025): https://dignitas.gg/articles/the-5-most-common-jungle-myths-and-why-they-re-wrong · **[DI-CJ]** «How to counter jungle» (2021): https://dignitas.gg/articles/how-to-counter-jungle
- **[MB-W]** MetaBot, oleadas (15-oct-2026): https://metabot.gg/en/league/guides/wave-management-freezing-slow-push
- **[DI-SP]** Dignitas, «Split pushing 101» (2021): https://dignitas.gg/articles/split-pushing-101-how-to-play-the-map-properly-on-summoner-s-rift · **[DI-M1]** «How to Macro 101» (2019): https://dignitas.gg/articles/how-to-macro-101-which-objective-is-more-important-in-league-of-legends · **[LP]** Leanpub, «League of Legends fundamentals» (teoría; leído solo en parte): https://leanpub.com/read/league-of-legends-fundamentals-en/leanpub-auto-theory
- **[DI-VC]** Dignitas, «Mastering vision control» (2020): https://dignitas.gg/articles/blogs/Blog/14167/mastering-vision-control-in-league-of-legends · **[DI-BF]** Dignitas con Biofrost, warding (2022): https://dignitas.gg/articles/pro-level-warding-guide-with-dignitas-biofrost · **[DX]** Dexerto, visión (2023): https://dexerto.com/league-of-legends/how-get-better-warding-league-of-legends-vision-control-guide-1849126
- **[LT-D]** LoLTheory, dives y aggro de torre (actualizado 7-nov-2026): https://blog.loltheory.gg/tower-dive-lol/

---

## B) Mecánicas de 2026 que cambian cuánto cuesta cada decisión

**B1. Tiempo de muerte [WK-D].** Base por nivel 1-18 (s): 10, 10, 12, 12, 14, 16, 20, 25, 28, 32,5, 35, 37,5, 40, 42,5, 45, 47,5, 50, 52,5. Desde el 15:00 se suma un porcentaje que sube cada 30 s: entre 15 y 30 min, `ceil(2·(min−15))·0,425 %`; entre 30 y 45, `12,75 % + ceil(2·(min−30))·0,30 %`; entre 45 y 55, `21,75 % + ceil(2·(min−45))·1,45 %`, con tope del 50 % (máximo 78,75 s). Ejemplos a nivel 16: 49,5 s a los 20 min, 53,6 s a los 30 y 56,4 s a los 40.
→ Para el análisis: el coste de una muerte antes de un objetivo depende de si el muerto vuelve a tiempo. Hoy `before_objective` mira una ventana fija; con la tabla se sabe si la muerte dejó a su equipo con uno menos **en el momento del objetivo** (muerte + reaparición > aparición del objetivo).

**B2. Recompensas de campeón [WK-B].** Base 300 de nivel 1 a 6, hasta 420 a nivel 18 (V25.09); primera sangre +100 (V26.01). La recompensa sube 1 por cada 3 de oro de kills y asistencias; por súbditos y monstruos, 1 por cada 20 (o 1 por cada 7 si tu equipo va por detrás, V26.03). Cobrar un apagado: el acumulado más 100; con tope de base + 700 por kill. Desde el 6:00, si el equipo del que mata no va claramente por delante, el apagado se reduce un 30/60/90/100 % según la ventaja. **[amplía]** la regla 34 de `macro_general.txt`.

**B3. Recompensas de objetivo [WK-OB].** Solo desde el 14:00 y para el equipo que va por detrás (umbral por oro, sobre todo, más experiencia, épicos y torres; los valores exactos no son públicos). Se activan ~60 s después de quedar por debajo. Valen la base más el 10 % del déficit de oro del equipo, con tope de 1.000, repartido por igual entre los cinco aunque estén muertos. Bases: barón 300, dragón o ancestral 500, heraldo 500, torre exterior 250, interior 400, de nexo 400. Con una de objetivo activa, el equipo que va por detrás **no cobra apagados**.
→ Regla nueva (todos los elos): yendo por detrás desde el 14, un objetivo con recompensa vale mucho más que una pelea: el dragón puede dar ~800 por cabeza. Detección: oro del equipo al minuto (diferencia ≥ ~umbral) y qué hizo con los objetivos que tenían recompensa (los cogió, los cedió o murió por perseguir kills).

**B4. Torres [WK-T, LT-D].**
- **Calentamiento:** cada disparo seguido a un campeón hace un 50 % más, hasta +150 % (al cuarto disparo); se reinicia 5 s después del último disparo a un campeón, no al cambiar de objetivo.
- **Sin súbditos rivales cerca,** la torre recibe un 80 % menos de daño (Reinforced Armor); vuelve 3 s después de que se vaya o muera el último súbdito.
- **Torres exteriores:** entre el 11:00 y el 15:00 pierden 10 de oro por placa y 15 de armadura y RM por minuto (tope −40 y −60).
- **Bulwark:** al bajar al 90/75/55/30 % de vida, la torre gana una carga de 20 s de armadura y RM (V26.3: 30-50 por carga según los campeones cerca).
- **Inhibidor:** vuelve a los 5 min, con aviso unos 15 s antes; con él caído salen 8 oleadas de supersúbditos.
- **Torre de nexo:** vuelve a los 3 min con el 40 % de vida.
- **Conflicto:** la wiki dice que el bonus de 300 de la primera torre se quitó en la 25.S1.1, pero las notas del 26.1 dicen que vuelve (y `macro_general.txt` 41 lo da por bueno). Fiarse de las notas de Riot y comprobarlo en el oro del evento.

**B5. Larvas [WK-V, NB].** Desde la V25.09 son **3**, una sola vez, a las 8:00; se van a las 14:45 (14:55 en combate). En la V26.01 empiezan a nivel 7, dan 30 de oro solo a quien las mata y +50 % de daño a no campeones. Cada una da al equipo una carga de **Touch of the Void** (los ataques a estructuras queman daño verdadero 4 s), y la tercera da **Hunger of the Void** (invocar ácaros contra estructuras). Según [NB], la 26.11 subió un tercio la quemadura y los ácaros pasaron a tener la vida de un súbdito cuerpo a cuerpo. La wiki tiene un conflicto interno sobre desde qué larva se gana Hunger: comprobarlo.
→ Su valor es **tirar torres**: la regla es convertirlas en placas o en una torre por el lado donde ya hay presión (detección C-2 abajo). Hoy `docs/macro.md` solo las cuenta.

**B6. Heraldo [WK-H, NB].**
- **Aparición:** a las 15:00, una sola vez; se va a las 19:45 (19:55 en combate).
- **V26.01:** empieza a nivel 9 y da 100 de oro y 240 de experiencia.
- **El ojo:** queda 20 s para recogerlo. Invocado, el heraldo va a la línea más cercana y su carga hace **3.000 de daño verdadero** a la torre. Las siguientes cargas hacen menos.
- **Golpe de Duchess Smash:** quita la Reinforced Armor de las torres cercanas (cambia el cálculo de un dive, B4).
- **Desde la 26.6:** su carga activa el Crecimiento cristalino de la torre.
- **Regla:** se elige por la torre que puede tirar con una oleada, no por el reloj. Se suelta en la línea contraria a la del objetivo del rival, para dividirle [BM, WK-H].

**B7. Dragones, almas y ancestral [WK-DS, BM].** Por dragón (hasta 4 del elemento de la grieta):

| Elemento | Por dragón | Alma |
|---|---|---|
| Químico | 6 % de tenacidad y de curación y escudos | +13 % de daño y de reducción de daño por debajo del 50 % de vida |
| Nube | 5 % de resistencia a ralentizaciones y velocidad fuera de combate | +15 % de velocidad; 60 % durante 6 s tras la definitiva (recarga 30 s) |
| Hextech | 5 de haste y 5 % de velocidad de ataque | Daño verdadero y ralentización en cadena a 3 rivales (recarga 8 s) |
| Infernal | 3 % de AD y AP | Explosión cada 3 s |
| Montaña | 5 % de armadura y RM | Escudo tras 5 s sin recibir daño |
| Océano | 2 % de la vida perdida curada cada 5 s | Curación fuerte en 4 s, más maná |

- **Ancestral:** da 150 s de quemadura y **ejecuta por debajo del 20 % de vida**.
- **Cuánto vale disputarlo** [BM, 2026]: con 0-2 dragones, el dragón no justifica una pelea perdida. Con 3, empujar, volver a base y moverse en grupo. Si el rival está a un dragón del alma, negarlo es lo prioritario. **[amplía]** reglas 19-20 y 53 de `macro_general.txt`.
- **Qué alma para qué composición:** ninguna fuente de 2026 legible lo dice. La inferencia razonable, sin validar: montaña y océano para peleas largas, hextech para enganche y protección, nube para rotar, infernal para daño y poke.

---

## C) Lo que separa los elos según los datos

**C1. Medias por rango de roflstats [RS]** (de Hierro IV a Aspirante, parches 16.17-16.20). Son medias de población, **referencias de banda, no umbrales de error**:

| Métrica | Hierro | Oro | Diamante | Maestro | Aspirante |
|---|---|---|---|---|---|
| Support fuera de bot, min 0-14 | 14,1 % | — | — | — | 37,3 % |
| Pinks por 10 min | 0,11 | ~0,35 | ~0,55 | ~0,6 | 0,77 |
| Farsight (trinket azul) por 10 min | 0,02 | ~0,15 | ~0,3 | ~0,4 | 0,50 |
| Pings por 10 min (solo con repetición) | 5,4 | 9,1 | 12,4 | 14,4 | 19,3 |
| Jungla cerca de su support, min 0-14 | 11,4 % | — | — | — | 17,3 % |
| Primer full clear (mediana) | 3:24 | — | — | — | 2:56 |
| Primera vuelta a base (mediana) | 4:42 | — | — | — | 3:54 |
| Participación en kills | 36,4 % | ~45 % | — | ~48 % | 50,2 % |
| Muertes ante un asesino casi sin verlo | 14,7 % | — | — | — | 20,5 % |
| Partidas con un AFK | 8,6 % | — | — | — | 2,6 % |

Cuadra con lo medido en el proyecto el 25-11-2026 con ~48.000 partidas guardadas: supports fuera de bot antes del 14, 23 % en Hierro-Bronce frente a 52 % en Aspirante (la definición es distinta: por minuto y fuera de la zona de bot); junglas con menos minutos sin CS de jungla en elo alto (16,4 % frente a 8,4 %) y más CS por minuto (5,6 frente a 7,5) **y a la vez** más participación en kills. En elo alto no se saltan más campamentos: farmean y juegan a la vez.

**C2. Frontiers 2026 [FR]** (52.704 partidas de EUW; el resumen dice «más de 35.000»). Por equipo y minuto:
- Los rangos altos **no** pelean más ni cogen más objetivos por minuto (η² ≤ 0,002): no usar «kills por minuto» ni «objetivos por minuto» como señal de elo.
- Lo que más separa son los pings (η² = 0,244) y un índice de visión (η² = 0,107). Después vienen, con efecto pequeño pero constante, el movimiento total (η² = 0,021: distancia entre las posiciones de cada minuto) y las rotaciones (η² = 0,020).
- **Kills fuera de línea:** 27,1 % en Aspirante frente a 25,9 % en Oro.
- **Dispersión de las kills (SDist):** de principio a final de partida se concentra un 56 % en Aspirante y un 35 % en Oro. En elo alto las peleas tardías se agrupan en pocos sitios: alrededor de los objetivos.
- **Cálculo:** todo sale del timeline de un minuto de la API, así que se puede repetir con nuestros datos (detección C-3).

**C3. A los 10 minutos [UW]** (Diamante, no revisado, correlación y no causa): la diferencia de oro (r = 0,51) y de experiencia (0,49) pesan más que las kills (0,34). Con dragón se gana el 64 % frente al 42 %, y con dragón y heraldo, el 74 % frente al 40 %. **[amplía]** `leads_report`.

---

## D) Reglas nuevas por elo

### D1. Hierro-Bronce
1. **Morir menos que farmear** [WC]. Referencias de Hierro: menos de 8 muertes por partida y KDA > 2; 5 CS/min en jungla y 5-6 en línea. Bronce: 6 CS/min. → Detección: ya existen `deaths_*` y `cs_min`; añadir estas referencias como «objetivo de la banda» en «Mi progreso» si el usuario está en esos elos.
2. **Primera ward al volver por primera vez** [WC, Bronce]. → Detección: primer WARD_PLACED después de la primera compra en base. Hoy se mide «wards antes del 14», no si la primera vuelta trajo visión.
3. **Tras perder tu primera torre, agrúpate con dos compañeros para un objetivo** en vez de volver solo a la línea [WC, Bronce]. → Detección: tras la BUILDING_KILL de tu primera torre, en los 2 min siguientes, ¿estuviste a ≤ 2.000 de al menos 2 aliados o seguiste solo en esa línea? Cruzar con muertes en solitario en ese rato.
4. **Una kill vale 12-15 CS: CS antes que kills** [DI-DP]. Su ejemplo: 2/0 con 35 CS al 10 vale menos que 0/2 con 95. → Detección: partidas con kills tempranas y CS al 10 bajo para el rol (`cs10` existe); la regla es el texto que lo explica.
5. **No perseguir más de 5-7 s; a partir de 10, déjalo y busca un objetivo** [DI-TM, DI-DP]. → Detección (con la reconstrucción): muerte de quien iba atacando (hizo daño a campeones ese minuto) tras más de ~10 s avanzando hacia terreno rival, sin kill propia en la jugada y con un objetivo vivo cerca. Es aproximada: necesita la posición entre minutos.
6. **No divear antes del nivel 6 sin ayuda** [DI-TM]. → Detección: muertes bajo una torre rival (≤ 900 de la torre) antes del minuto 6-7 sin aliados cerca. Sirve también el «calentamiento» de la torre (B4): se muere al tercer o cuarto disparo.
7. **No pelear 30-60 s antes de que aparezca un dragón o el barón: agrúpate y pon visión** [DI-UH, Hierro-Oro]. **[amplía]** `before_objective`: usar esa ventana y el tiempo de reaparición (B1).
8. **Contexto de banda** [RS]: en Hierro hay un 8,6 % de partidas con un AFK. No juzgar como error de macro lo que pasa con un compañero AFK (`afk` ya existe en el análisis).

### D2. Plata-Oro
1. **Plata: muertes avanzando sin visión** [WC]. **[amplía]** `deaths_no_vision_pre14`: también pasado el 14, en la jungla rival y el río.
2. **Plata: no más de dos derrotas seguidas; 53 % o más en 30 partidas con tu campeón** [WC]. **[amplía]** los informes de racha y de pool.
3. **Oro: dejar de buscar jugadas heroicas (1 contra 3) y cerrar la partida** [WC]. → Detección: muerte en solitario contra ≥ 3 rivales (por kill con asistencias) yendo por delante en oro (≥ 2.000) después del 20.
4. **Oro: oleadas antes de agruparse** (congelar, slow push, chocarla antes de irse) [WC, MB-W]. Congelar con unos 3 súbditos rivales más, fuera del alcance de tu torre, y nunca sin saber dónde está el jungla rival [MB-W]. → Detección indirecta: CS perdido al irse de la línea (`rm_roam_cs_cost` existe) y muertes en tu mitad de la línea sin visión.
5. **Oro, de Plata alta a Oro bajo: el salto de mecánicas a macro** [Mobalytics, vía buscador]. Es la banda en la que más rinden los consejos de macro de «Mi progreso».
6. **Oro frente a Aspirante: peleas tardías dispersas** [FR]: en Oro, las kills tardías se concentran la mitad que en Aspirante. → Detección C-3.

### D3. Platino-Esmeralda
1. **Platino: partidas ganadas que se pierden** [WC]. **[amplía]** `leads_report`. Añadir qué pasó tras la ventaja: la secuencia que recomienda [WC] es objetivo cercano, base y presión, y la detección C-1 mide si la ventaja se convirtió en estructuras.
2. **Platino: no conocer los picos de poder del rival** [WC, DI-5W]. → Detección: muerte en un duelo o intercambio justo después de que el rival subiera a 6, 11 o 16 o completara un objeto (LEVEL_UP e ITEM_PURCHASED del rival ≤ 60 s antes). Es un hueco del inventario: hoy solo se miran tus picos.
3. **Esmeralda: convertir el tempo en unos 15 s y leer la condición de victoria** [WC]. Ya estaba; [WC] añade **avisar del jungla rival a intervalos de ~30 s**. → Detección: de la reconstrucción, cuánto tiempo pasó el jungla rival sin aparecer en ningún sitio que tu equipo pudiera ver. Hacerlo bien necesita las repeticiones (visibilidad).

### D4. Diamante
1. **Cada oleada lateral que llega a tu torre sin nadie es oro perdido** [DG-DM]. → Detección: después del 14, minutos en los que nadie de tu equipo está en una línea lateral cuyo lado del mapa va perdiendo placas o torres. Aproximado: las oleadas no se ven.
2. **Llegar 5 s tarde a un objetivo pierde la pelea; decidirlo en menos de 3 s** [DG-DM]. **[amplía]** la detección 2 de `docs/macro.md` (llegada tarde).
3. **Encadenar objetivos** [DG-DM]: larvas, heraldo y torre; dragón y torre; barón e inhibidor. → **Detección C-1** (nueva y medible).
4. **Evitar peleas 40/60** [EN-D]: no se puede medir la probabilidad, pero sí las peleas en inferioridad numérica (`fight_outnumbered` existe).

### D5. Maestro y Aspirante
1. **La diferencia es la constancia, no saber más: evitar las malas partidas, no solo mejorar las buenas** [DG-DM, WC]. → Detección: dispersión entre partidas del usuario (desviación de muertes, de CS/min y de errores graves por partida) y la cola mala (el 20 % de peores partidas). Nueva para «Mi progreso».
2. **Referencias de Aspirante** [RS]: primer full clear a las 2:56 de mediana (Hierro, 3:24), primera vuelta a base a las 3:54 (Hierro, 4:42), 0,77 pinks y 0,5 Farsight por 10 min. → Detecciones C-4 y C-5.

### D6. Todos los elos, por tema

**Prioridad de línea y objetivos de 2026**
1. **A las 4:30, antes del primer dragón, mira bot y mid**: solo se pelea si esas oleadas pueden moverse y el jungla está sano y sin rivales a la vista [BM].
2. **A las 8:00, larvas o dragón según quién tenga prioridad**: con top y mid, larvas; con bot y mid, dragón; unas larvas a medias también valen [BM]. → **Detección C-2.**
3. **Empujar la oleada unos 40 s antes del dragón o el barón**, para que llegue a la torre rival cuando acabe el objetivo, y el mid con prioridad, agruparse unos 20 s antes [DI-PR]. **[amplía]** la regla de volver 60-90 s antes.
4. **Sin prioridad no se muere por el objetivo**: ceder el cangrejo, el primer dragón o campamentos [DI-LP]. Ya existe como «no error» (`no_prio`).
5. **No empezar un objetivo con las dos líneas más cercanas empujadas contra tu torre** [DI-RD]. → Detección: posiciones de tus laners de ese lado en el minuto anterior (en tu mitad de la línea) y quién murió en el objetivo.
6. **Barón desde la 26.2: ya no termina la partida** (150 de oro en vez de 300, según [NB]). Su mejora de 3 min se usa para hacer avanzar una oleada o tirar una torre, no para volver a base sin más [BM]. **[amplía]** reglas 22 y 52.
7. **Estar en el lado correcto del mapa a las 19:30** [NB]: el barón sale a las 20:00. → Detección: como «llegada tarde», pero para el primer barón.

**Roams**
8. **El mid roamea mejor cuando la siguiente oleada trae cañón**: la torre tarda más en limpiarla [DI-RM]. → Detección: con el reloj de oleadas (una de cada tres trae cañón, desde la tercera en 2026), ¿el roam empezó en una oleada de cañón?
9. **Roamear hacia el lado de tu jungla** [DI-RW], para tener ayuda si contraatacan. → Detección: destino del roam (`rm_roam_lane`, `rm_roam_river`, `rm_roam_own_jg` y `rm_roam_enemy_jg` ya lo calculan) frente a la posición de tu jungla en ese minuto.
10. **Si el mid rival roamea, empuja y quítale placas** [DI-RM]. → Detección: placas (TURRET_PLATE_DESTROYED) que cogiste mientras tu rival de línea estaba a más de 4.000 de la línea. Hoy solo se mide al que roamea.
11. **Al caer tu torre, el rival de línea roamea más: tira la suya pronto** [DI-RD].
12. **Support: aprovecha que tu ADC vuelve de base para ir a poner visión en la jungla de su lado** [DI-RD].
13. **No roamear si tu línea pierde fuerte o si tu campeón no tiene daño**: que vengan a tu línea [DI-RM].

**Jungla**
14. **Full clear a las 3:15-3:30 en 2026**: nivel 4 y unos 1.200-1.400 de oro [DD-J]. La mediana de Aspirante es 2:56 [RS]. → **Detección C-4.**
15. **Contar el CS de jungla** [DD-J, DI-TJ]: 4 por campamento, así que 8 son 2 campamentos, 12 son 3 (gank a nivel 3), 24 el full clear y 28 tras el cangrejo. Si bot llega tarde a la línea, el jungla rival empezó abajo con ayuda. **[amplía]** el seguimiento del jungla rival.
16. **Ventanas de gank**: a las 3:30-4:00 por la ventaja de nivel (4 frente a 3) y a las 7:00-9:00 con el primer objeto [DD-J]. **[amplía]** «gank con el objeto del minuto 7-9».
17. **Un gank fallido cuesta ~20 s de farmeo y enseña tu ruta. Lo grave no es hacer full clear, sino quedarse 30 s sin hacer nada después** [DD-J]. → **Detección C-6.**
18. **Invadir el lado contrario cuando el jungla rival aparece en el otro** [DI-JM, DD-J]. → Detección: kill o wards del rival vistos en un lado y, en el minuto siguiente, campamentos rivales (`enemy_camps10` existe) o cangrejo del otro lado.

**Laterales, split y cierre**
19. **El split empieza tras caer la primera torre del mapa** (de cualquier línea) [DI-SP, LP].
20. **ADC en la lateral solo si tiene alcance o movilidad** (Lucian, Ezreal); los hipercarry sin movilidad (Jinx, Aphelios) se quedan en mid, porque no aguantan un dive pasada la mitad de la línea [DI-SP]. → Detección: muertes de ADC inmóviles en una lateral, pasado el río, después del 14 (`rm_adc_alone_deaths` existe sin distinguir campeón).
21. **Antes de empujar, ¿tiene el rival el teletransporte?, ¿su jungla está en tu lado?** Si tus wards no ven a nadie, es la señal para empujar [DI-SP].
22. **El jungla solo va a la lateral si gana tanto como lo que puede perder en el otro lado**: una kill y una torre sí, varias torres o el barón por una kill no [DI-SP]. → Detección: cambios cruzados entre lados (`_exchange`/`trade` ya existen) evaluados desde el jungla.
23. **Hacia el 14, intercambio de líneas habitual**: ADC a mid, top a bot, mid a top [LP]. → Detección: posiciones por rol tras la primera torre. Es útil para no marcar como «fuera de su línea» lo que es normal.

**Visión**
24. **Visión según el marcador** [DI-VC, DX]. Con ventaja, en la jungla rival y alrededor del barón y el dragón; igualados, en las entradas a la jungla desde el río; por detrás, en los cruces de tu jungla. → Detección: lugar estimado de cada ward (la posición reconstruida de quien la pone en ese segundo) frente a la diferencia de oro. Ya hay `rm_ward_lane_pct`, `rm_ward_enemy_jg_pct` y similares (% de wards por zona): falta cruzarlo con el marcador.
25. **Capas alrededor de un objetivo** [DI-VC]: pinks en el foso y wards normales en los pasillos y en la jungla rival de al lado, 60-90 s antes. **[amplía]** `rm_pink_obj` y `rm_obj_ward_pct`.
26. **Pinks solo donde puedas defenderlas** [DI-VC]. → Detección: pinks quitadas por el rival (WARD_KILL de tipo CONTROL_WARD) ≤ 30 s después de ponerlas.
27. **Farsight (trinket azul)** [RS]: de 0,02 a 0,5 por 10 min entre Hierro y Aspirante. → **Detección C-5.**

**Dives y remontadas**
28. **Dive** [LT-D]. Entra primero quien más vida tenga, o uno a distancia que pueda salirse del alcance y reiniciar el aggro. No divear con oleada congelada (todos los disparos van a ti), con Flash y una curación del rival disponibles, ni sin aguantar 2-3 disparos. Un dive falla sobre todo al segundo o tercer cambio de aggro, cuando ya está la kill y alguien se come un disparo de más. → Detección: muertes bajo una torre rival en los 10 s siguientes a una kill propia allí (el «disparo de más»).
29. **Yendo por detrás desde el 14, prioriza los objetivos con recompensa** (B3) y no regales apagados [WK-OB, WK-B].

---

## E) Detecciones nuevas propuestas (por prioridad)

Todas se pueden calcular con lo que ya guarda el análisis. Antes de enseñarlas como error, validarlas como las de `docs/macro.md` (sección C): tests, 20 casos revisados a mano y comparación por elo con las partidas guardadas.

**C-1. Convertir objetivos en estructuras** (D4.3, D3.1). Valor alto, riesgo bajo.
- **Qué mide:** tras dragón, larvas, heraldo o barón de tu equipo, ¿cayó una torre o un inhibidor rival en los 90 s siguientes (180 s tras el barón)?
- **Datos:** ELITE_MONSTER_KILL, BUILDING_KILL y TURRET_PLATE_DESTROYED.
- **Excepción:** si tu equipo perdió a 2 o más en esa ventana.
- **Por elo:** mostrar el % de conversión por elo como referencia; se espera que suba con el elo, y hay que comprobarlo con el perfil por elo antes de marcarlo como error.

**C-2. Larvas o dragón a las 8:00 según la prioridad** (D6.2).
- **Qué mide:** a las 7:30-8:00, qué laners de cada equipo estaban en la mitad rival de su línea (prioridad), y qué objetivo se cogió.
- **Se marca:** pelear por las larvas sin prioridad de top y mid, o por el dragón sin prioridad de bot y mid, y morir en ello.
- **Se valora:** coger el objetivo del lado donde se tenía la prioridad. Y tras las larvas, placas o torre por ese lado en los 3 min siguientes (su valor real, B5).

**C-3. Concentración de las peleas tardías** (C2, D2.6).
- **Qué mide:** SDist de [FR], la dispersión de las posiciones de las kills por fase (0-10, 10-20 y más de 20 min) y cuánto baja de principio a final.
- **Uso:** es métrica de equipo; sirve de referencia por elo (Oro −35 %, Aspirante −56 %) y para el texto «peleáis desperdigados» en partidas largas.

**C-4. Ritmo del jungla** (D5.2, D6.14).
- **Qué mide:** cuándo se llegó a 24 de CS de jungla (interpolando los frames) y cuándo fue la primera compra en base.
- **Referencias:** Hierro 3:24 y 4:42, Aspirante 2:56 y 3:54 [RS].
- **Ojo:** `jungle_cs10` y `slow_clear` ya existen; esto da la referencia por elo y el momento exacto.

**C-5. Visión por 10 minutos con referencias por elo** (C1, D6.27).
- **Qué mide:** pinks puestas y Farsight usados por 10 min (WARD_PLACED de tipo CONTROL_WARD y BLUE_TRINKET, y compra del trinket azul), comparados con la banda del usuario [RS].
- **Ojo:** hoy hay `control_wards` y `pinks10`, pero no Farsight.

**C-6. Jungla parado tras el full clear** (D6.17).
- **Qué mide:** después de la primera ruta, minutos sin CS de jungla, sin kills ni asistencias, sin objetivo, sin estar en base y sin pelea deducida.
- **Ojo:** `idle_pct` existe en general; esto es la versión de jungla, que [DD-J] señala como el fallo típico.

**C-7. Muerte ante el pico de poder del rival** (D3.2). Hueco del inventario.
- **Qué mide:** tu muerte en un duelo o intercambio (1 o 2 rivales) ≤ 60 s después de que el que te mata subiera a 6, 11 o 16 o terminara un objeto legendario.
- **Datos:** LEVEL_UP, ITEM_PURCHASED y el catálogo de objetos.

**C-8. Placas mientras el rival roamea** (D6.10).
- **Qué mide:** si tu rival de línea estaba a más de 4.000 de la línea en un minuto, ¿cogiste placas o empujaste? ¿O le seguiste sin aviso?
- **Hoy:** solo se mide al que roamea.

**C-9. Muerte cara antes de un objetivo** (B1, D1.7).
- **Qué mide:** muertes en las que el muerto reaparece **después** de que aparezca el dragón o el barón (con la tabla de reaparición).
- **Gravedad:** mayor cuanto más se pierde el objetivo y cuanto más tarde en la partida. Sustituye la ventana fija de `before_objective`.

**C-10. Por detrás con recompensa de objetivo** (B3, D6.29).
- **Qué mide:** desde el 14, con tu equipo por detrás en oro, si se cogieron o se cedieron los objetivos y cuántas muertes hubo persiguiendo kills.
- **Requisito:** necesita estimar cuándo hay recompensa activa (el umbral exacto no es público): empezar con la diferencia de oro del equipo y comprobarlo con el oro de los eventos.

**C-11. Constancia entre partidas** (D5.1). Para «Mi progreso».
- **Qué mide:** desviación de muertes, CS/min y errores graves por partida, y la cola de las peores partidas, comparadas con la banda.

**C-12. Agruparse tras perder la primera torre** (D1.3).
- **Qué mide:** en los 2 min siguientes, si estabas con al menos 2 aliados o solo en la línea, y si moriste solo.

**C-13. ADC inmóvil en una lateral** (D6.20).
- **Qué mide:** muertes de ADC sin desplazamientos (lista de campeones a definir: Jinx, Aphelios…) en una lateral pasado el río, después de la primera torre.

**C-14. El «disparo de más» en un dive** (D6.28).
- **Qué mide:** muertes bajo una torre rival ≤ 10 s después de una kill de tu equipo allí mismo.

---

## F) Conflictos y cosas sin confirmar

- **Primera torre:** 300 de oro según las notas del 26.1 y `macro_general.txt`; la wiki dice que se quitó en la 25.S1.1. Mirar el oro real en el evento.
- **Larvas:** 3 desde la V25.09 (no 6). Desde qué larva se gana Hunger of the Void (¿la 3.ª?) tiene un conflicto en la wiki.
- **Heraldo:** [DD-J] lo da a las 14:00; la wiki y los relojes de 2026, a las 15:00 (usar las 15:00, como `docs/macro.md`). El cangrejo, 3:30 en las guías de 2026; las de 2016-2021 dan 2:30 o 3:15, de parches viejos.
- **Barón:** 150 de oro desde la 26.2 según [NB] (no verificado en las notas de Riot).
- **Placas de torre:** 120 de oro según la wiki, sin la fecha del cambio; Esports.net hablaba en su previsión de placas permanentes y en torres interiores, sin confirmar.
- **Feats of Strength:** se quitaron en el 26.1 [R26.1]. **Faelights:** la página de la wiki da 404; las notas del 26.3 acortan su zona de revelado cerca del pasillo del cangrejo y del foso.
- **Repeticiones (.rofl):** las referencias de roflstats con visibilidad y pings necesitan repeticiones. La app las tiene solo de las partidas del usuario (`app/replays_local.py`); con ellas se podrían medir sitios de ward reales, «visto por el rival» y pings en sus propias partidas.
