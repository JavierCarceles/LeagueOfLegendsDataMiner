# Macro: reglas de coaches y detecciones sin falsos positivos

Investigación del 19-11-2026 para `app/coach/analyzer.py` (robots.txt revisado en cada web). Leí 2 páginas de leagueoflegends.com, 5 de la wiki, 8 de dignitas.gg, 6 de wecoach.gg y 4 de maurogarih.com y weteachleague.com (sin cifras de macro). Mobalytics da 403: solo uso su extracto del buscador.

## A) Fuentes

- **R1** Patch 26.1 Notes, Riot Games (EN): https://www.leagueoflegends.com/en-us/news/game-updates/patch-26-1-notes/
- **R2** 2026 Season One Gameplay Preview, Riot Games (EN): https://www.leagueoflegends.com/en-us/news/dev/dev-2026-season-one-gameplay-preview/
- **W** Voidgrub, Rift Herald, Baron Nashor, Elder Dragon y Dragon, LoL Wiki (EN): https://wiki.leagueoflegends.com/en-us/Voidgrub (y hermanas)
- **WC1** Guía de objetivos de League of Legends (2026), Roger, WeCoach (ES): https://wecoach.gg/es/blog/article/league-of-legends-objectives-guide
- **WC2** How to Climb Out of Every Rank (2026), Barry, WeCoach (EN): https://wecoach.gg/blog/article/how-to-climb-out-of-every-rank-in-league-of-legends
- **WC3** Control de visión para junglers (2023), Charlene, WeCoach (ES): https://wecoach.gg/es/blog/article/league-of-legends-vision-control-warding-guide-for-junglers
- **D1** Timing and Tracking Objectives (2020), Cealygosa, Dignitas (EN): https://dignitas.gg/articles/blogs/League-of-Legends/14839/a-guide-on-timing-and-tracking-objectives-in-league-of-legends
- **D2** Dragon is the Win Condition (2020), Trulliam, Dignitas (EN): https://dignitas.gg/articles/blogs/League-of-Legends/14234/dragon-is-the-win-condition-a-season-10-dragon-control-guide
- **D3** How to Gain Dragon Control (2019), Friedchicken277, Dignitas (EN): https://dignitas.gg/articles/how-to-gain-dragon-control-in-lol
- **D4** Improving Your Wave Management (2019), Ahrivve, Dignitas (EN): https://dignitas.gg/articles/a-guide-to-improving-your-wave-management-in-league-of-legends
- **D5** Split Pushing Tactics (2019), Zooph, Dignitas (EN): https://dignitas.gg/articles/split-pushing-tactics-a-guide-to-proper-splitting-in-league-of-legends
- **D6** Playing From Behind (2019), Log, Dignitas (EN): https://dignitas.gg/articles/a-guide-to-playing-from-behind-in-league-of-legends
- **D7** How To Stop Throwing League Games (2021), lolmjlauer, Dignitas (EN): https://dignitas.gg/articles/how-to-stop-throwing-league-games
- **D8** How to Close Out a Game (2021), RealZesty, Dignitas (EN): https://dignitas.gg/articles/how-to-close-out-a-game-in-league-of-legends
- **MG** Cómo subir de elo en LoL, Mauro Garih (ES): https://www.maurogarih.com/subir-de-elo-lol
- **MB** 5 Best Recall Timers, How to Make the Most of Baron y How to Split Push, Mobalytics, solo vía buscador (EN): https://mobalytics.gg/blog/lol-5-best-recall-timers/ https://mobalytics.gg/blog/league-of-legends-what-to-do-after-taking-baron-nashor/ https://mobalytics.gg/blog/how-to-split-push-in-league-of-legends/
- **Vídeo:** Dishake, «Jugás bien y perdés: dejá de pushear como un BOT» (ES): https://www.youtube.com/watch?v=blVhFqlhYMc (título y canal comprobados, sin verlo).

## B) Reglas

1. **Relojes de 2026 (16.x):** dragón a las 5:00 y 5 min tras cada muerte; ancestral 6 min tras el 4.º dragón de un equipo y luego cada 6. Larvas a las 8:00 (hasta 14:45) y heraldo a las 15:00 (hasta 19:45), una sola vez. Barón a las 20:00 y 6 min tras cada muerte; ya no hay Atakhan. Súbditos desde 0:30, oleadas cada 25 s desde el 14 y cada 20 s desde el 30; campamentos a las 0:55. Placas permanentes; primera torre, 300 de oro. Mejoras: barón 180 s, ancestral 150 s [R1, R2, W, WC1].
2. **Preparar:** empuja y vuelve a base 60–90 s antes; volver tarde deja al equipo con uno menos [D1, D2].
3. **Visión:** quita y pon visión (pink en el foso) ~1 min antes: antes te la limpian, después te pillan [D1, D6, WC3].
4. **Números:** no disputes sin tu equipo y cuenta los rivales vivos; un 5 contra 4 suele ganarse; sin prioridad en mid y bot, empezar el dragón es arriesgado [D1, D2, D3].
5. **Tempo:** una pelea ganada da ~15 s. Objetivo más cercano, base y siguiente jugada [WC2].
6. **Jungla rival:** avisa al verlo y tras 30 s sin verlo; prevé dónde estará en 90 s [WC2]. Los campamentos salen 35 s antes que en 2025 [R1, R2].
7. **Oleadas:** empuja antes de volver, rotar o agruparte (si no da tiempo, congela). No vuelvas con el cañón llegando [D4, WC2, MB].
8. **Laterales:** cruza el río solo con wards y si ganas la pelea; retírate cuando roten. Split solo si tu equipo aguanta un 4 contra 5; si el rival se agrupa, agrupaos [D5, D7, MB].
9. **Cerrar:** la mejora del barón potencia a los súbditos. Base y asedio por varias líneas (1-3-1), sin pelear nada más cogerla [W, D8, MB].
10. **Por detrás:** presiona donde no está el rival y no te confíes al recuperarte [D6]. En Platino y Esmeralda manda la macro: minimapa cada 3–5 s [MG].

## C) Detecciones (por prioridad)

**Base común.** Tabla de relojes por parche (B1 para 16.x; Grieta sin Swiftplay): sin tabla no se calcula, y si en la BD un objetivo muere antes de su hora, se desactiva ese parche. Se reutilizan `reliable()`, `present()`, `respawn_buy`, `game_end` y `_exchange`. Posiciones de frames o eventos, con ≥ 1.500 de margen. La vida está en `championStats.health/healthMax` de cada frame (hoy no se lee). Antes de activar: tests en `tests/test_coach_audit.py` y 20 casos revisados.

**Notación.** S: aparece el objetivo; T: muere. *Disputado*: T − S ≤ 120 s y, en T, ≥ 2 aliados a ≤ 2.600 o un aliado muerto a ≤ 3.000 entre T − 30 y T + 10. *Te tocaba*: `_contest_expected`.

**1. Vuelta a base a destiempo** (P1; valor alto, riesgo bajo; B2)
- **Salta si:** vuelves a base (compra con vida, no al reaparecer) entre S − 30 y T en un objetivo disputado que te tocaba, y en T estás a > 2.600.
- **Excepciones:** vuelta forzada (< 50 % de vida en el frame previo o daño en los 40 s anteriores), muerte, AFK y objetivo cogido por tu equipo sin bajas.
- **Texto:** «Volviste a base {s} s antes de {objetivo} y tu equipo lo peleó sin ti: tocaba volver 60–90 s antes.»
- **Estado:** existe a medias. Solo cubre objetivos cedidos lejos (jungla, support, mid o grandes) y desde T − 60. Mejora: contar desde S, en todos los roles y sin vueltas forzadas, y marcar acierto si vuelves entre S − 120 y S − 35 y llegas.

**2. Llegada tarde a un objetivo disputado** (P1; valor alto, riesgo bajo; B2, B4)
- **Salta si:** el rival coge un objetivo disputado que te tocaba. En el frame anterior a S − 30 estabas a > 7.000 del foso y en T, entre 2.600 y 5.500 (más lejos, `obj_lost_far`).
- **Excepciones:** haber vuelto a base (es la 1), morir entre S − 90 y T (`before_objective`), jugar en otra zona (`_cross_map_play`), `outnumbered` y un cambio justo con tu participación.
- **Texto:** «Tu equipo peleó {objetivo} sin ti: un minuto antes de que apareciera estabas a {d} unidades.»
- **Estado:** nueva; hoy ese tramo no se juzga.

**3. Pelea en inferioridad junto a un objetivo** (P1; valor alto, riesgo medio-bajo; B4)
- **Salta si:** mueres en una pelea (`_fight_cluster`) a ≤ 3.000 de un foso, con el objetivo vivo o que el rival coge en 60 s. Además, 10 s antes de la primera kill el rival tenía uno más presente y tu aliado llevaba ≥ 10 s muerto.
- **Excepciones:** `fight_won`, `traded`, `game_end`, `afk`, tu base, y su alma o el ancestral (hay que disputarlos).
- **Texto:** «Disputasteis {objetivo} {a} contra {e} con un aliado muerto: con uno menos, cédelo o cámbialo al otro lado.»
- **Estado:** nueva; `outnumbered` solo disculpa objetivos perdidos con 2 de diferencia.

**4. Pelea ganada sin aprovechar** (P1; valor alto, riesgo medio; B5)
- **Salta si:** la pelea deja ≥ 2 rivales muertos, ≥ 2 de ventaja y ≤ 1 aliado caído; había un objetivo vivo a ≤ 7.000 o una torre rival a ≤ 5.000 (tabla fija de torres menos `BUILDING_KILL`); estabas vivo y a ≤ 3.000; y hasta que reaparece el 2.º rival (25–75 s, +15) tu equipo no coge objetivo, torre, inhibidor, 2 placas ni 8 CS de su jungla.
- **Excepciones:** antes de las 3:00, < 3 aliados vivos para barón o ancestral (< 2 para lo demás) y victoria en 120 s.
- **Texto:** de equipo (leve), «Ganasteis la pelea del {mm:ss} y no cayó nada: tocaba {objetivo}.» Tuyo (medio), si vuelves a base en ese tiempo con ≥ 60 % de vida: «Volviste a base tras ganar la pelea en vez de ir a por {objetivo}.»
- **Estado:** nueva (P2.7 de `coaches.md`).

**5. Pink sin usar en un objetivo** (P2; valor medio, riesgo muy bajo; B3)
- **Salta si:** llevas una pink (`Inventory.at`) de S − 90 a S + 30 sin colocar ninguna (`WARD_PLACED` `CONTROL_WARD`). Además, en algún frame del tramo estuviste a ≤ 4.000 del foso y el objetivo muere antes de S + 180.
- **Excepciones:** inventario no fiable, muerte o AFK.
- **Texto:** «Llevabas una pink y no la pusiste en el minuto previo a {objetivo}.»
- **Estado:** nueva.

**6. Barón sin aprovechar** (P2; valor medio, riesgo bajo; B9)
- **Salta si:** de equipo (leve), en los 180 s tras vuestro barón no cae torre, inhibidor, 3 placas, ancestral ni alma; tuyo, mueres aislado en ese tramo.
- **Excepciones:** victoria antes de T + 240, estar muerto al cogerlo y `traded`.
- **Texto:** «Moriste solo con la mejora del barón: se aprovecha asediando en grupo o por dos líneas.»
- **Estado:** nueva.

**7. Cazado en su mitad** (P2; valor medio, riesgo bajo; B8)
- **Salta si:** mueres después del 14 en top o bot, al otro lado del río (`side_of` rival), contra ≥ 3 y sin aliados a ≤ 3.000 (excepciones de `isolated`).
- **Texto:** «Te cazaron {n} rivales solo en su mitad del mapa: al otro lado del río, entra solo con visión.»
- **Estado:** mejora de `isolated` (etiqueta `collapsed`, +0,5 de gravedad; alta con `before_objective`).

**8. Gank previsible** (P2; valor medio, riesgo bajo; B6)
- **Salta si:** juegas top o bot, mueres entre 2:30 y 4:30 con `gank` y `jungler`, y en los frames de los minutos 1 y 2 su jungla estaba en la mitad contraria a tu línea.
- **Excepciones:** las de `gank`, mid e invasiones.
- **Texto:** «Su jungla empezó en el otro lado: hacia el 2:45 acaba su primera vuelta en el tuyo; este gank se podía prever.»
- **Estado:** mejora de `deaths_to_jungler_pre14`. Los consejos de `insights.py` (gank a las 3:15, ward desde las 2:45) pasarían a ≈ 2:40 y ≈ 2:15 (inferencia mía: campamentos 35 s antes).

**Mejoras menores**
- **Recompensa regalada (P3):** muerte con `shutdownBounty` ≥ 700 e `isolated`, `collapsed` o `enemy_jungle`. Texto: «Regalaste {x} de oro de recompensa muriendo solo». Regla: WC2 (Platino no cierra partidas ganadas). Requiere guardar `shutdownBounty` aparte (hoy va en `value`).
- **`_contest_expected`:** hoy exige el heraldo (15:00) al ADC y al support. Hasta el 20, no exigir a top, ADC ni support los objetivos del otro lado salvo que haya caído una torre exterior de su línea.

## C bis) Implementado el 19-11-2026 (`ANALYSIS_VERSION = 7`) y comprobado con 58 partidas reales del usuario

- **Relojes** (`app/coach/clocks.py`): los de B1 cuadran con los datos de 16.21-16.23 (ningún objetivo antes de su hora;
  ≥ 311 s entre dragones, ≥ 383 s entre barones, ancestral ≥ 382 s tras el alma, 3 larvas de una vez, el heraldo que
  nadie mata «muere» a las 19:45 con `killerTeamId` 300). Si en una partida un objetivo muere antes de su hora, esa
  partida no se juzga (`clocks: false`). Swiftplay, otros mapas y otras temporadas, igual.
- **1. Vuelta a base a destiempo** (`late_recall`): hecha con las excepciones de arriba más «buen cambio del equipo» y
  «sin datos de vida = no se acusa». Daño ≥ 35 % de la vida máxima en ese minuto también cuenta como vuelta obligada
  (los campamentos pegan al jungla: prudente). 1 caso en 58 partidas, revisado a mano: correcto.
- **2. Llegada tarde** (`late`): hecha; 0 casos en 58 partidas (las 26 candidatas tenían una excepción válida: muerte
  justo antes, vuelta a base, a más de 5.500 o cerca del foso).
- **3. Pelea con uno menos** (`fight_outnumbered`, etiqueta de la muerte, +0,8): 7 casos (2 contra 5 en el barón, 4
  contra 5 en larvas, heraldo y dragón…), revisados: correctos.
- **4. Pelea ganada sin aprovechar** (`unconverted`): hecha, pero solo con ≥ 30 s de ventaja numérica real (hasta que
  reaparece el 2.º rival). Sin ese mínimo salían 9 casos con ventanas de 5-28 s (fase de líneas, peleas largas): no
  daba tiempo a convertir nada. Con él, 0 casos.
- **5. Pink sin usar** (`pink`): 2 casos, revisados (p. ej. pink comprada a las 6:24, larvas a las 8:27, pink puesta a
  las 9:15). Al poner una pink el timeline trae `ITEM_DESTROYED` 2055 (628 de 628 casos): el inventario es fiable.
- **6. Barón sin aprovechar** (`baron_wasted`, de equipo, leve) y **morir solo con la mejora** (`baron_buff`): 2 + 2.
- **7. Cazado en su mitad** (`collapsed`): 1 caso. **Recompensa regalada** (`bounty`, ≥ 500; Riot la limita a 700): 0.
- **8. Gank previsible: NO se implementa.** Con datos reales el lado de inicio del jungla no predice el lado del primer
  gank (empieza arriba → ganquea abajo 10, arriba 4; empieza abajo → arriba 5, abajo 7): ~55 % de acierto.
  Tampoco se adelantan los consejos a 2:40: la primera kill de un jungla en línea llega de mediana al 4:01 (p25 3:29,
  p10 2:33) y a las 3:00 lleva 20 CS de jungla de mediana (24 al limpiar 6 campamentos). El consejo pasa a «3:15-3:45
  si limpia toda la jungla, desde 2:30 si ganquea tras 3 campamentos; ward hacia el 2:15».
- **`_contest_expected`**: hasta el 20, top, ADC y support no responden de los objetivos del otro lado salvo que haya
  caído una torre exterior de su línea (el heraldo de las 15:00 ya no se le pide al ADC).
- **Ornn** (`BUYS_ANYWHERE`): sus compras no son vueltas a base (ni anclan su posición en la fuente).

## D) No detectable con fiabilidad

- **Oleadas:** freeze, slow push, empujar antes de volver y CS perdido: no hay súbditos.
- **Wards:** `WARD_PLACED` y `WARD_KILL` no traen posición: ni sitios, ni barridos, ni wards profundos.
- **Información:** niebla, rivales desaparecidos, pings y chat.
- **Quién empieza una pelea u objetivo:** solo se sabe cuándo muere: ni barón a ciegas, ni smite, ni robos.
- **Estado exacto:** vida solo una vez por minuto; maná y enfriamientos, nunca.
- **Decisiones de equipo:** prioridad de líneas, split o agruparse, uso del TP (solo cuántas veces), condición de victoria y objetivos cedidos a propósito.
- **Entre frames:** con 60 s interpolados no cabe «llegaste 10 s tarde».
- **Rotar tras una torre:** con placas en las interiores, quedarse puede ser correcto.

## E) Cobertura de la carpeta `macro/` (22-11-2026, `ANALYSIS_VERSION = 11`)

Revisión de las 38 reglas de `macro/macro_general.txt`, las 81 de `macro/macro_por_rol.txt` y las 207 de
`macro/macro_composiciones.txt` frente a lo que analiza `app/coach/analyzer.py`. Las cifras salen de volver a analizar
las 51 partidas de `data/mis_partidas` (47 de jungla, 4 de support) con el código actual.

**Ya se detecta**

| Reglas | Detección (etiqueta) |
|---|---|
| General 1, 2, 4, 8, 28, 37; compos 33, 57, 94, 139, 145 | Pelea ganada sin aprovechar y volver a base tras ganarla (`unconverted`) |
| General 16; jungla 11; ADC 7, 14; support 9; compos 8, 21, 28 | Vuelta a base a destiempo (`late_recall`), llegada tarde (`late`), pink sin usar (`pink`) |
| General 19, 24; compos 84, 99, 108, 169, 198 | Pelear junto a un objetivo con uno menos (`fight_outnumbered`), morir antes de un objetivo (`before_objective`) |
| General 20, 158 | Alma, barón y ancestral cedidos pesan como graves |
| General 21; jungla 7, 13; general 26 | Buen cambio o cambio justo al otro lado (`trade`), ganks rivales en la otra mitad (`tracking`) |
| General 22 | Barón sin aprovechar (`baron_wasted`) y morir solo con la mejora (`baron_buff`) |
| General 25, 27; TOP 7, 12; ADC 9; support 8; compos 40, 63, 173 | Aislado (`isolated`), cazado en su mitad (`collapsed`), en su jungla (`enemy_jungle`), sin ward (`no_ward`), adelantado (`overextended`), ganks |
| General 33; ADC 6 | Oro sin gastar (`gold`) y morir con él (`unspent_gold`) |
| General 34; compos 143 | Recompensa regalada (`bounty`) |
| **TOP 5, 6; MID 13; ADC 3; compos 13, 152, 155** | **Nuevo: duelo en desventaja (`duel_behind`)** |
| Jungla 2-5, 14 | Impacto antes del 10, ganks, CS de jungla, primer gank, cangrejos e invasión |
| ADC 1; compos 150 | CS por minuto, CS a los 10 y diferencia con tu rival a los 10 y 15 |
| Support 1, 2; compos 52 | Visión por minuto, wards antes del 14, pinks compradas y puestas |
| Compos 117, 146, 157 | Minutos vacíos (`idle`) y ventajas que se escapan (*Hábitos*) |

**Nuevo: duelo en desventaja.** Mueres 1 contra 1 (un rival, sin muerte mutua ni gank) y él te sacaba ≥ 2 niveles,
o un legendario más con ≥ 1.000 de oro total de diferencia, o antes del 14 tú no tenías la R y él sí. Los niveles se
miden 3 s antes de la muerte, porque la experiencia de matarte ya le sube de nivel (sin ese margen salían 2 casos de
más). Si tú llevabas más legendarios, no salta, y la R no cuenta con Karma, Elise, Nidalee, Udyr, Jayce ni Hwei. Suma
+0,5 de gravedad y en *Errores que repites* sale como «Aceptas duelos con niveles u objetos de menos» (tema
*Intercambios*). Salen 8 de 25 muertes en duelo, revisadas a mano (p. ej. Quinn 9 contra 12 en el río y Nidalee 15
contra 17 antes del barón): todas correctas.

**Probado con tus partidas y descartado (no es fiable)**

- **Matar al jungla rival y no hacer el objetivo** (general 5, jungla 18). Salieron 8 candidatas en 47 partidas de
  jungla y las 8 tenían explicación: barón hecho 10 s después de la ventana, defender una torre del split rival, vida al
  17 %, el equipo a 9.000 unidades del foso o una pelea igualada que dejó 2 vivos. Lo que sí es error ya lo cubre la
  pelea ganada sin aprovechar.
- **Llegar al objetivo con oro sin gastar** (general 33, compos 204). Pasa 1 vez en 70 objetivos disputados contigo
  cerca: casi siempre vuelves a base antes. Ese caso ya sale como oro sin gastar.

**No detectable con los datos de Riot** (ver también D)

- **Oleadas** (general 9, 10, 13; TOP 1-4; MID 1, 2, 10; ADC 5; compos 29, 39, 41, 50, 51, 124): no hay súbditos.
- **Enfriamientos, definitivas, Flash y TP** (general 32; TOP 9, 10; support 12; compos 9, 10, 25, 27, 203): no
  vienen; del TP solo se sabe cuántas veces se usó.
- **Dónde se ponen los wards** (support 1, 10, 11; compos 42, 202): `WARD_PLACED` no trae posición.
- **Cómo se pelea** (compos 15-120: formación, peel, dive, poke, quién inicia y a quién se ataca): solo se sabe dónde
  y cuándo muere cada uno.
- **Perseguir** (general 3, 7; jungla 15): con una posición por minuto no se ve una persecución.
- **Decisiones de equipo** (general 11-15, 31, 36; compos 1-14, 121-135, 196-207): agruparse, split, asignación de
  líneas o condición de victoria. La composición se analiza en *Draft*, pero no se puede comprobar si se jugó según
  su plan.
- **Propósito de un roam y comunicación** (MID 3-6, 14; support 5-7; jungla 16): se mide el tiempo fuera de la línea y
  sus kills, no qué se buscaba ni los pings.

## F) Macro deducida de los datos (22-11-2026, `ANALYSIS_VERSION = 12`)

Con 1.818 partidas de junglas OTP (`data/otps` y `data/partidas_campeon`: 1.026 de Challenger, 560 de Esmeralda,
120 de Platino, 69 de Diamante y 43 de Master/GM) y las 47 de jungla del usuario. Cada partida trae, minuto a minuto,
la posición y el CS de jungla del OTP y de su jungla rival, todas las kills con posición, las torres y el CS de cada
línea a los 5, 10, 15 y 20.

**El CS dice quién tiene la línea (sí se usa).** Con tu línea a ≥ +10 CS a los 10, vuestra torre de esa línea cae
antes en el 84 / 75 / 72 % de las partidas de Challenger (top / mid / bot); a ≤ −10, solo en el 31 / 44 / 24 %. En
Esmeralda, 79 / 79 / 81 % frente a 26 / 32 / 24 %.

**Dragón y prioridad (sí se usa).** Prioridad = las dos líneas junto al objetivo (mid y bot para el dragón; top y
mid para larvas, heraldo y barón) a ≥ +5 CS de sus rivales en el último frame. Dragones normales, sin contar el
punto de alma ni el alma:

| | Con prioridad | Repartida | Sin prioridad |
|---|---|---|---|
| Challenger | 68 % (318) | 58 % (1.669) | 44 % (322) |
| Esmeralda | 76 % (136) | 57 % (999) | 38 % (169) |
| Platino | 60 % (35) | 58 % (200) | 37 % (49) |

Morir en el foso pasa casi igual con o sin prioridad (Challenger 6-9 %, Esmeralda 13-14 %): lo que separa los elos
es morir allí, no la prioridad. Por eso:

- Un dragón normal cedido **sin prioridad** en mid y bot ya no cuenta como «objetivo regalado»: es un momento
  informativo (`no_prio`) que explica el CS de las dos líneas y propone el cambio al otro lado. Se cuenta aparte
  (`obj_lost_noprio`). En las 51 partidas del usuario son 8 de los 21 objetivos que antes salían como regalados.
- El resto de objetivos cedidos y las muertes justo antes de un objetivo junto al foso dicen la prioridad que había.
- Mi progreso tiene la tarjeta **«Prioridad de línea y objetivos»**: tus dragones según la prioridad y la primera
  torre, frente a Challenger y Esmeralda (`insights.PRIORITY_REFS`).

**Primera torre (referencia).** La primera torre de la partida cae de mediana al 13:32 en Challenger y al 13:39 en
Esmeralda. El equipo que la tira gana el 78 % en Challenger (36 % sin ella) y el 69 % en Esmeralda (33 %).

**Probado y descartado como error**

- **Gank sin resultado en vez de farmear.** Una «visita a línea» es un frame (minutos 3-13) con el jungla en una línea.
  Las visitas sin kill, sin torre, sin farmear la oleada y sin morir son el 27 % en Challenger, el 25 % en Esmeralda,
  el 26 % en Platino y el 31 % en el usuario (59 visitas, sin diferencia real). Con un frame por minuto, una visita a
  línea casi nunca coincide con un minuto sin CS de jungla (0,00-0,03 por partida): el coste de un gank fallido no
  se ve en campamentos (≈ 2 CS a los 10). Sí se asocia a perder: en Challenger ganan el 63 % con 0 visitas vacías y
  el 49 % con 2 o más, pero eso también puede ser porque la partida ya iba mal. No se juzga partida a partida.
- **Ganks a líneas ganadas o perdidas.** Salen algo mejor a líneas que ganan (Challenger 64 % con kill frente a
  58 %; Platino 73 % frente a 49 %), con el mismo riesgo de morir (~19 %). Diferencia pequeña: queda como consejo.
- **Jugar del lado de las líneas que ganan.** Los junglas de todos los elos pasan el 47 % del early (5-14) en la mitad
  de arriba sea cual sea el CS de top y bot a los 5. Y en Challenger, los que juegan del lado que pierde ganan más
  (65 % frente a 55 %). La regla no se cumple en los datos: no se juzga.

## G) Revisión con los datos analizados en la versión 12 (22-11-2026, `ANALYSIS_VERSION = 13`)

Las 1.818 partidas de OTPs y las 51 del usuario llegaron ya analizadas con la versión 12 en Windows.

- **Mismo cálculo en los dos equipos:** al volver a analizar aquí las 51 partidas del usuario (con el timeline en bruto
  y los objetos de Data Dragon de cada parche), las métricas y los momentos salen idénticos a los guardados.
- **Fallo arreglado en los relojes:** 6 partidas se quedaban sin relojes porque el heraldo moría entre las 19:51 y
  las 19:55, después de su hora de irse (19:45). Si alguien lo está pegando, aguanta hasta que muere: el heraldo y las
  larvas tienen ahora 30 s de margen (`clocks.IN_COMBAT_GRACE`). El más tardío de los datos, a las 19:55.
- **Sin primera torre:** 10 partidas (9 de OTPs y 1 del usuario) acaban entre los 5 y los 15 minutos sin que caiga
  ninguna torre (rendiciones y remakes). Que salga vacío es lo correcto.
- **Duelo en desventaja, por elo** (partidas sin AFK de al menos 15 min): 0,05 por partida en Challenger, 0,13 en
  Esmeralda, 0,11 en Platino y 0,18 en el usuario (2,7 veces más en Esmeralda que en Challenger). Pasa a ser un punto
  débil de jungla con referencia 0,12 (`deaths_duel_behind`). Si sale, no se repite «Pierdes muchos 1 contra 1».
- **Dragones según la prioridad** con el CS minuto a minuto: Challenger 72 / 56 / 47 % (con, repartida, sin) y
  Esmeralda 75 / 55 / 41 %. Sustituyen a las cifras aproximadas del apartado F en la tarjeta de Mi progreso.
- **Objetivos regalados** con la nueva definición: Challenger 0,28 por partida y Esmeralda 0,44. La referencia de
  jungla (0,4) sigue valiendo.

## H) Top: primeras partidas de OTPs (23-11-2026)

120 partidas de OTPs de top, todas de Aspirante (11 jugadores y 20 campeones; sobre todo Fiora, Urgot, Warwick e
Irelia). Sin partidas de top de Esmeralda aún no se puede ver qué distingue a un elo de otro, pero sí que las
referencias de top eran demasiado exigentes: Aspirante muere antes del 14 2,45 veces por partida (la referencia era
1,3), por el jungla rival 0,93 (0,5), por gank 0,97 (0,6), adelantado 0,78 (0,45), justo antes de un objetivo 0,75 (0,3)
y pasa muerto el 10,4 % de la partida (7,5 %). Un Esmeralda no muere menos que un Aspirante: esas referencias suben al
menos a lo de Aspirante (`benchmarks.BASE["TOP"]`). El duelo en desventaja en top es 0,20 por partida en Aspirante (en
jungla, 0,05): sigue siendo un punto débil solo de jungla hasta que haya top de Esmeralda para compararlo.

Los 500 casos de los 50 drafts, repetidos con los resúmenes de `data/otps` (antes no se usaban en la auditoría), siguen
sin ningún problema; los OTPs cambian 1 build de 500 (Hecarim con plan de bola de nieve: Cuchilla negra en vez de
Rencor de Serylda, que no van juntas, porque sus OTPs la compran más contra ese tipo de equipo).

## I) Errores que separan elos (23-11-2026, ~14.600 partidas de `data/otps` y `data/partidas_campeon`)

Con las reglas de la versión 13, un Aspirante cometía casi los mismos errores por partida que un Platino (jungla:
2,9 frente a 2,2) y el usuario, en normales, menos que un Aspirante. Dos causas: la mitad de los errores eran muertes que
cualquier elo comete igual (morir en la jungla rival siendo jungla: 29 % de partidas en todos los elos; los Aspirante
mueren ahí más, porque invaden más) y faltaban los errores que de verdad separan elos. Se mide todo con la misma regla en
cada elo (`app/coach/elo_profile.py`) y se añaden, como errores con su minuto (`app/coach/champ_checks.py`), los que se
dan claramente menos en Aspirante (% de partidas de jungla, Platino → Aspirante):

| Error | Platino | Esmeralda | Diamante | Aspirante | Regla |
|---|---|---|---|---|---|
| Ninguna pink en 20+ min | 44 | 45 | 39 | 14 | top, jungla, mid y ADC |
| 0-1 wards antes del 14 | 36 | 12 | 15 | 2 | top, jungla, mid y ADC |
| KP < 40 % (≥ 10 kills de equipo) | 14 | 17 | 17 | 7 | solo jungla (en top no separa: 54 → 56) |
| Primer objeto tarde (> p75 + 1 min de sus OTPs Diamante+, sin ir por detrás) | 13 | 12 | 7 | 5 | por campeón |
| Segundo objeto tarde (> p75 + 1,5 min, sin ir por detrás a los 15) | | | | | por campeón (pasado el 21: 15 → 3) |
| Despeje lento (CS de jungla a los 10 < p10 de sus OTPs, ≤ 1 gank) | 11 | 12 | 4 | 2 | por campeón |
| Orden de habilidades raro (< 8 % de sus OTPs, uno ≥ 60 %) | 4 | 18 | 5 | 2 | por campeón, niveles 1-9 |
| Primera vuelta rara (< 4 % de sus OTPs y fuera de sus primeros objetos) | 3 | 1 | 5 | 1 | por campeón; defensas y Heridas graves no cuentan |

Morir en la jungla rival siendo jungla solo cuenta si fue sin visión (`MISTAKE_TYPES.role_need`). Un tipo de error que
el elo más alto con datos comete al menos el 85 % de veces que el más bajo «no separa elos» y pesa la mitad en *Errores
que repites*. Resultado con Hecarim (mismos OTPs del campeón): Platino 3,7 errores por partida, Esmeralda 3,8,
Diamante 3,2, Maestro 2,7 y Aspirante 2,1; el usuario, en normales, 2,6.

Descartado por no separar elos: volver a base gastando < 350 sin haber muerto (Platino 11 %, Diamante 11 %, Aspirante
8 %), ningún gank con kill antes del 10 (Platino 12 %, Aspirante 17 %) y 3 o más muertes antes del 14 (33 % → 29 %).

### Con las ~540 partidas nuevas (23-11-2026, sobre todo top de Aspirante)

- Top: Diamante 4,4 errores por partida frente a 3,8 de Aspirante. Dos reglas los Aspirante las cometen **más**: «sin
  botas mejoradas al 20» (0,079 frente a 0,035) y «no te defiendes del daño que más recibes» (0,075 frente a 0,052): no
  son errores en top (`skip_roles`; tampoco se marcan en las partidas nuevas). Las muertes por gank (1,27) o adelantado
  (1,01) no separan elos y pesan la mitad en *Errores que repites*.
- Referencias orientativas de top, mid, ADC y support (`benchmarks.BASE`) recalculadas con los OTPs de Diamante
  (≈ 2.000-3.000 partidas por rol) donde se desviaban más de un 25 %. Muertes justo antes de un objetivo: 0,3 → 1,1;
  muertes antes del 14 en mid, ADC y support: 1,3-1,6 → 2,4-2,9 (eran falsos puntos débiles); muertes aisladas y en su
  jungla, oro sin gastar y wards de control de support (9 por partida, no 5): más exigentes.
- Descartado también: estar cerca del foso cuando aparece el primer dragón (5:00) o las larvas (8:00), medido con la
  posición de cada minuto: Platino 35 % / 37 %, Diamante 35 % / 39 %, Aspirante 38 % / 43 %. No separa elos.

## J) Posiciones reconstruidas y validadas (23-11-2026, `ANALYSIS_VERSION = 14`)

Riot da la posición de cada jugador una vez por minuto y en cada evento. Validación con las 53 partidas del usuario
(`python -m app.coach.track_check`):

1. Se quita la posición real de cada evento (8.831) y se reconstruye con el resto. Línea recta: error medio 1.875
   unidades, 11,9 % a más de 4.000. Con la vuelta a base (si compra en la fuente estando lejos, se queda 8 s donde
   estaba y salta; casi nadie vuelve andando), teletransportes, quien hizo o recibió daño en cada muerte y kills
   especiales: 1.575 y 6,9 %. Sin efecto o peor: salir tarde de la fuente, «ir y esperar», ir por la línea al salir de
   base y el umbral del teletransporte.
2. Se esconde la posición del jungla en un minuto (su CS sí se sabe) y se reconstruye con la de antes, la de después y
   los campamentos que encajan con su CS (vivos según los que ya hicieron los dos junglas). En los 244 casos en que
   farmeaba: 3.602 → 3.069 de error medio y 40 % → 30 % de fallos grandes. Parámetros ajustados con esa prueba:
   aparición 1:30, reaparición 2:30, 5 de CS por campamento de media (unos dan 4 y otros más), 9 s de limpieza.
   De 802 tramos de jungla sin otros datos, 12 (1 %) no cuadran con el CS: ahí no se deduce nada.

Lo que queda (fallos grandes) son sobre todo minutos enteros sin ningún evento fuera de la jungla: ahí no hay datos de
por dónde fue.

Rutas (24-11-2026): la línea recta entre dos puntos atraviesa muros; con rutas que los rodean (A* en una rejilla de
128 × 128 sacada del minimapa `map11.png`, en la que caen el 96 % de las posiciones reales) el tiempo dentro de un muro
en la repetición baja del 11,2 % al 1,2 %. La precisión no cambia (error medio 1.573 en los dos casos): entre dos
posiciones conocidas, el desvío por los caminos es pequeño frente a lo que no se sabe. Las repeticiones `.rofl` sí
guardan todas las posiciones, pero sus paquetes están ofuscados y cambian en cada parche (y Riot prohíbe la ingeniería
inversa del cliente): no se usan.

## K) Macro con los recorridos reconstruidos (24-11-2026, `app/coach/route_macro.py`)

Con las 28.918 partidas re-analizadas en la versión 14 (recorrido reconstruido de cada OTP y todas las kills con
posición) se miden reglas de `macro/` que antes no se podían comprobar. Son tendencias, no errores sueltos (hasta un
Aspirante deja sin responder 4 de cada 5 jugadas del jungla rival): salen en «Cómo te mueves por el mapa» junto a cada
elo. Jungla, Platino → Esmeralda → Diamante → Maestro → Aspirante (~17.000 partidas):

| Regla | Medida | Plat | Esm | Diam | Maes | Asp |
|---|---|---|---|---|---|---|
| Jungla 10, 13; general 26 | Responde a lo que hace el jungla rival (kill o asistencia allí en 15 s) | 17 % | 17 % | 18 % | 20 % | 20 % |
| Jungla 10 | Cerca cuando actúa el jungla rival | 22 % | 23 % | 23 % | 25 % | 26 % |
| General 12-14 | Agrupado pasado el 14 | 28 % | 31 % | 32 % | 33 % | 35 % |
| Top 12; ADC 9; compos 122, 132 | Solo en una línea lateral pasado el 14 | 6,4 % | 4,8 % | 4,5 % | 4,3 % | 4,1 % |

Top (Diamante → Aspirante): roams con kill o asistencia 58 % → 66 % y CS perdido por roamear 1,2 → 0,2. ADC solo en
una lateral pasado el 14: 11 % → 7 %. Mid y support solo tienen Diamante (roams, el mid con su jungla, muertes del ADC
con el support lejos): se miden igual y la comparación aparecerá con partidas de más elos.

No separan elos (no se usan): ganks fallidos largos (≥ 30 s en una línea sin kill ni asistencia: 0,18-0,20 por partida
en todos los elos) y campamentos robados antes del 10 (0,6-0,7).

### Revisión del 24-11-2026 (28.860 partidas; primeras de Esmeralda en top, mid y support)

- Jungla, Platino → Aspirante (todos los campeones): objeto tarde 0,19 → 0,05 por partida, despeje lento 0,10 → 0,03,
  pocos wards 0,36 → 0,10, sin pinks 0,45 → 0,17, KP baja 0,14 → 0,09, muertes antes de un objetivo 1,04 → 0,83 y
  peleas ganadas sin convertir 0,08 → 0,04. Con todos los campeones mezclados, aislado y orden de habilidades no
  separan (sí con el mismo campeón: por eso se compara por campeón cuando hay varios elos).
- Morir adelantado en la línea no separa elos en ninguna línea (top: Diamante 0,76, Aspirante 0,81; Esmeralda, 0,29):
  pesa la mitad en *Errores que repites*. No se toca la regla hasta tener más Esmeralda y Platino.
- Top, Diamante → Aspirante: roams con kill o asistencia 57 % → 64 % y CS perdido por roamear 2,8 → 0,4.
- «Solo en una línea lateral» pasado el 14 depende de la línea: en ADC es de elos bajos (Diamante 11 %, Aspirante 7 %),
  en top de elos altos (Esmeralda 31 %, Aspirante 36 %: más split). La tabla de movimiento marca en cada fila hacia
  dónde van los elos altos (↑/↓) y dónde quedas tú.
- Mid y support con menos de 50 partidas de Esmeralda: sin conclusiones todavía.

## L) Reglas nuevas de las guías de 2026 y cómo las tienen en cuenta los análisis (24-11-2026)

Búsqueda en internet de guías de macro de todos los roles: Dodge.gg (jungla, mid, ADC y support, 2026), MetaBot (vueltas a
base y objetivos, 2026), Dignitas (seguir al jungla rival, contrajungla, macro por rol, vueltas a base, medio y final de
partida, top), WeCoach y Boostroom (top) y las notas de los parches 26.1 y 26.2 de Riot. Mobalytics, Nerfplz y Sheep
Esports bloquean las descargas (403). Se añadieron 20 reglas generales (39-58) y 63 por rol (top 16-25, jungla 19-34,
mid 16-27, ADC 17-28, support 18-30); las fuentes están al final de `macro/macro_por_rol.txt`.

Cada regla nueva se midió con las ~28.000 partidas de OTPs (retos y estadísticas de Riot de cada jugador, sin volver a
analizar nada) para ver si separa elos antes de juzgarla:

| Regla (fichero y número) | Cómo se tiene en cuenta | Datos (elo bajo → Aspirante) |
|---|---|---|
| Quitar visión antes de objetivos (general 46, jungla 31, ADC 26) | **Error nuevo** `no_sweep`: partida de 25+ min sin destruir ningún ward (top, jungla, mid y ADC), en «Errores que repites» y en las repeticiones | Jungla 12 % → 2,4 % de las partidas; top 19 % → 6 % |
| Pink en cada vuelta (general 45, jungla 32, ADC 26, support 22) | Fila «Vueltas a base en las que compras una pink» de *Cómo te mueves por el mapa* (`rule_metrics`) | Top 3 % → 14 %, jungla 14 % → 31 %, ADC 7 % → 23 % |
| Wards destruidos (las mismas) | Fila «Wards rivales que destruyes cada 10 min» | Top 0,5 → 0,85, jungla 0,8 → 1,6, ADC 1,1 → 2,2 |
| Visión frente a tu rival (support 21, mid 22) | Fila «Ventaja de visión sobre tu rival directo» | Top 7 % → 15 %, jungla −8 % → 19 %, ADC 18 % → 43 % |
| Roams y Teleports con kill (top 24, mid 19, support 19) | Fila «Kills en otras líneas al principio» | Top 0,04 → 0,14 por partida |
| Placas y primera torre antes del 14 (general 40, 41, 57; top 23) | Fila «Torres antes del 14 en las que participas» | Top 0,15 → 0,28, jungla 0,01 → 0,05, ADC 0,18 → 0,42 |
| Teleport (top 19-21) | Fila «Teletransportes usados» (top y mid que lo llevan) | Top 3,5 → 3,9 |
| No volver sin oro para algo útil (general 42) | Fila «Vueltas a base antes del 20 sin oro para nada útil», sin flecha: **no separa** | 6-10 % de las vueltas en todos los elos de top y jungla |

Las filas nuevas salen en *Mi progreso → Cómo te mueves por el mapa*, bajo «Reglas de las guías de macro que se pueden
medir», con la misma flecha (hacia dónde van los elos altos) y el mismo color que el resto. Para recuentos pequeños
(0,04 → 0,14 por partida) la flecha ya no exige una diferencia de 1 unidad: basta con 0,05 y un 10 %.

**Sin volver a analizar:** las partidas nuevas guardan `riot_rules` (retos de Riot del jugador) en su análisis; las que
ya tenías los sacan de la partida de Riot guardada al poner al día los resúmenes (`DIGEST_VERSION = 5`), y los OTPs, de
su dossier. `ANALYSIS_VERSION` sigue en 14.

**Ya cubiertas por detecciones anteriores:** vuelta tarde antes de un objetivo y visión 60 s antes (general 47; ADC 21;
mid 24 → `late_recall`, `late`, `pink`), pelear con menos (general 51, 55, 56 → `fight_outnumbered`), barón y pelea
ganada sin aprovechar (general 48, 52 → `baron_wasted`, `unconverted`), duelo con menos nivel u objetos (top 18, support
25 → `duel_behind`), ritmo del primer objeto (ADC 23 → `item_tempo`), despeje lento (jungla 19, 23 → `slow_clear`),
seguir al jungla rival y contraganks (jungla 24-26 → `tracking`, `rm_counter_*`), roams de mid con su jungla (mid 21 →
`rm_mid_with_jg_pct`) y support que deja solo al ADC (support 20 → `rm_adc_alone_deaths`).

**No medibles con los datos de Riot:** estados de la oleada, congelar y el reloj del cañón (general 39, 43, 44; top
16-17, 21-22; mid 16-17; ADC 18-20), Castigo (jungla 22), dónde se pone cada ward (jungla 28-29; mid 22; support 21-22,
24), avisos y pings (jungla 21; mid 20), intenciones en las peleas (support 27-28; general 49) y lo que se cede a
propósito (general 53-54; support 26).

### Recorridos frente a la experiencia y el CS de cada minuto (24-11-2026)

La experiencia de los súbditos solo se gana a menos de ~1.600 unidades de donde mueren, y el CS de súbditos exige estar
en una línea. Riot da los dos cada minuto, así que sirven para comprobar los tramos que se reconstruyen sin posición
(validación 3 de `python -m app.coach.track_check`, con las 55 partidas de `data/mis_partidas`). En 1.385 minutos de
top, mid y ADC sin pelea ni vuelta a base:

- Minutos imposibles (fuera de todas las líneas el 75 % o más del minuto con 6 o más CS de súbditos): 1 con el
  reconstruido y 2 con la línea recta de antes.
- Cuanto más tiempo pone el recorrido a alguien en su línea, más CS y experiencia tiene ese minuto: 0-25 % del minuto,
  5,6 CS y 349 de experiencia; 75-100 %, 8,7 CS y 535 de experiencia.

Así que los roams y las rotaciones reconstruidas (`route_macro`) no se inventan salidas de línea que la experiencia
contradiga. No se corrige nada más: con 1 caso de 1.385 no compensa añadir otra regla a la reconstrucción.

### Los análisis también rodean muros (24-11-2026)

Hasta ahora solo la repetición 2D rodeaba los muros; las posiciones que usan los análisis (roams, contraganks, tiempo en
cada zona, agrupado o solo en una lateral) iban en línea recta entre dos puntos y pasaban el 11,2 % del tiempo dentro
de un muro. Ahora el analizador anda por la rejilla de `navgrid` (A*, sin cortar esquinas en diagonal): 1,1 % (bordes
de la rejilla). Los que saltan muros con una habilidad pasan de 9 a 35 campeones (Zeri, Kha'Zix, Tristana, Fizz,
Camille, Elise, Riven, Ekko, Kassadin, Corki, Lucian, Jax, Katarina, Tryndamere, Gnar, Aatrox, Akali, Qiyana, Bel'Veth,
LeBlanc, Zed, Jarvan IV, Kalista, Sylas, Rengar y Taliyah, además de los que ya estaban), con IDs comprobados en Data
Dragon. Los demás solo cruzan un muro si no hay otro camino (Destello en un muro fino).

La precisión no cambia (validación 1: mediana 1.130 frente a 1.142 unidades), pero ya no hay recorridos imposibles. El
A* es ponderado (heurística octil ×1,3) para no frenar al recolector: analizar una partida pasa de 0,02 s a 0,13 s. Solo
lo usan las partidas que se analicen a partir de ahora; las de los OTPs ya guardadas no se vuelven a analizar.

### Ningún tramo sin un camino posible (24-11-2026)

- **Jungla:** los 12 tramos de 821 que no cuadraban con ningún camino de campamentos eran de partida avanzada (18-23 CS
  de jungla en un minuto) y uno de final de partida. Si con el ritmo del principio (400 u/s, 9 s por campamento) no da
  tiempo, se prueba con la velocidad real que da Riot en esos frames (hasta 600 u/s) y limpiando en 3,5 s; y los pocos
  CS que faltaban de un campamento recién hecho (los krugs se dividen) cuentan como ese campamento. Ahora 0 de 821, y la
  validación 2 mejora un poco: error medio 3.049 → 3.017 unidades, fallos grandes 28,9 % → 28,2 %.
- **Líneas:** si alguien hace 6 o más CS de súbditos en un minuto y su camino entre los dos frames (rodeando muros) pasa
  menos del 25 % cerca de una línea, se le coloca farmeando en el punto de la línea más cercano a mitad de minuto
  (`infer_lane_farm`, punto de tipo 8; en la vista 2D sale como estimado). Validación 3: 2 minutos imposibles → 0.
- **Muros:** queda un 1,05 % del tiempo en celdas de muro, pero el 0,82 % es junto a posiciones reales de Riot que ya
  caen en celdas de muro (el 4,6 % de los frames: la rejilla de 128 × 128, ~115 unidades por celda, no es exacta en los
  bordes de los muros y los arbustos). Los tramos reconstruidos solo ponen un 0,23 % dentro de un muro.

### Revisión de las subidas de las 00:39 y las 01:18 (24-11-2026)

- **Ninguna partida perdida.** Había 497 partidas guardadas dos veces (la misma partida y rol en dos carpetas, de cuando
  se reclasificaron los especialistas); ahora quedan 19 y el perfil por elo ya no las cuenta dos veces.
- **474 partidas nuevas, todas de Diamante o más** (jungla Diamante, Maestro y Aspirante; ADC, mid y support Aspirante).
  Ni una de Platino ni de Esmeralda: la búsqueda pedía al arrancar las tablas de los mejores de OP.GG de los 173
  campeones (solo traen Diamante+), cada jugador nuevo con una llamada de account-v1 del mismo cupo regional que las
  partidas, y hasta acabarlas no miraba el ranking. Esa sesión solo revisó la maestría de 335 jugadores. Ahora las
  tablas se intercalan con la búsqueda por elo (una cada dos vueltas en que toca un elo de Diamante o más).
- **ADC Aspirante (130 partidas) confirma las reglas medibles:** pink en el 7 % → 18 % de las vueltas, el doble de
  torres antes del 14 y de kills en otras líneas, ventaja de visión 18 % → 32 %, wards destruidos 1,1 → 1,8 cada 10
  min, «sin ninguna pink» 0,61 → 0,15 por partida, «sin quitar wards» 0,06 → 0,01.
- **Orden de habilidades en ADC sale más en Aspirante** (0,14 frente a 0,05 por partida): son 2 jugadores (un OTP de
  Tristana y uno de Twitch con su propio orden), no una tendencia del elo. Se deja; en mid no separa (0,06 en todos) y
  el perfil ya le da la mitad de peso. En top, jungla y support sí separa (Esmeralda 0,17-0,27 → Aspirante 0,07-0,10).
- Mid Aspirante (34 partidas) y Esmeralda (47) siguen siendo pocas para sacar conclusiones.
- **Por qué no llegaban partidas de Hierro a Esmeralda aunque se buscaran todos los elos:** casi todo lo recogido se
  hizo con búsquedas de Diamante+ (hasta el 23-11 a las 23:56). Con todos los elos, el 24-11 a las 00:20 ya había 47.705
  jugadores de Hierro a Esmeralda válidos, pero no se visitaban: al cambiar la búsqueda (campeones o líneas) todos los
  jugadores ya visitados vuelven a la cola, sus partidas dejaban de contar en «la línea y el elo con menos partidas», todo
  empataba a 0 y el desempate era el elo más alto. Ahora lo recogido cuenta siempre y, a igualdad, van antes los nunca
  visitados.
- **Velocidad del recolector:** en la subida de las 01:23 cada región usaba la mitad de su cupo (50-59 de 100 llamadas
  cada 2 min): el freno no era Riot. Elegir a quién visitar después leía y ordenaba todos los jugadores revisados (5 s con
  280.000 en un Mac) antes de cada visita y en cada región, y las 4 regiones se esperaban entre ellas (Python usa un
  núcleo). Ahora la cola se calcula una vez cada 2 min (las regiones comparten la lectura) y las visitas siguientes se
  eligen al instante; quien ya se visitó no se repite hasta la siguiente lectura. Techo con la key gratuita: ~85-95
  partidas/min con las 4 regiones (100 llamadas cada 2 min por región, 2 por partida, menos la reserva para la web).
- **Llamadas que no traían partidas** (subida de las 01:48: las regiones al 81-87 % de su cupo para 15 partidas por
  minuto, ~9 llamadas por partida cuando bastan 2): (1) el Riot ID de cada jugador que cumplía el filtro de maestría
  (account-v1, cupo regional; ~100 por minuto con ~7.500 revisados por hora) y de cada jugador nuevo visitado: ya no se
  pide, llega con su primera partida; (2) las tablas de OP.GG con líneas enteras o «cualquiera que lo juegue»: ya no se
  piden (solo traen Diamante+, del que hay 146.000 jugadores encontrados), y buscando especialistas, 20 por tabla.
- **Desglose de la subida de las 02:28:** cada región pasaba 15-19 s preparando la cola de visitas (ordenaba los ~350.000
  jugadores encontrados) y en esa sesión no llegó a visitar a nadie. Ahora la cola lee solo los ya visitados y los 100
  de más LP sin visitar de cada plataforma y elo (5,6 → 0,6 s en un Mac con 280.000); la búsqueda para en los elos con
  200+ jugadores esperando visita, y analizar cada partida tarda la mitad (0,14 → 0,077 s).
- **Desglose de la subida de las 02:38 (36 partidas/min):** cada partida costaba ~4,3 s por región en fila: ~2,6 s
  esperando a Riot (el recolector espacia sus llamadas 1,41 s para dejar sitio a la web) y 1,7 s analizando y
  guardando en Windows (0,2 s en un Mac). Ahora el análisis va en otro hilo por región, mientras se descarga la
  siguiente, y en las partidas del recolector solo el recorrido de su jugador rodea muros (es lo único que se guarda de
  ellas; los 10 se siguen rodeando en tus partidas): 0,21 → 0,12 s por partida con el perfilador.
- **36,3 partidas/min con la versión estable (subida de las 03:00):** por región, ~2,8 s por partida esperando a Riot y
  ~1,5 s analizando y guardando, en fila. El análisis en otro hilo se quedó parado en Windows (desactivado,
  `SAVE_IN_BACKGROUND`); ahora otro hilo solo **descarga** la siguiente partida (y su timeline si es de las que
  interesan) mientras la región analiza la actual (`_Prefetch`, sin tocar la base de datos ni los ficheros). Coste: al
  cortar una visita, a veces una descarga de más.
- **Registro de la subida de las 03:18 (49 partidas/min):** guardar cada partida en la base de datos tardaba 1,3-1,5 s
  por región. `otp_details.save` comprimía partida, timeline y análisis (>1 MB de JSON) dentro del candado de escritura
  de la base de datos: las demás regiones y las 15 búsquedas esperaban. Ahora se comprime antes de cogerlo.
- **Base de datos (subida de las 03:39, 42,8 partidas/min):** el WAL de SQLite ocupaba 504 MB y cada escritura reintentaba
  volcarlo; las 15 búsquedas hicieron 1.864 escrituras en 5 min (~50 ms cada una) y esperaron 2.333 s el candado; cada
  región, ~1 s por escritura. Las búsquedas no paraban porque el umbral (200 sin visitar) era mayor que lo que carga la
  cola (100 por elo): ahora 60. Ninguna escritura vuelca el WAL (lo hace un hilo cada 30 s) y se recorta a 64 MB.
- **Subida de las 03:48 (53,2 partidas/min):** el WAL bajó de 504 a 67 MB y las búsquedas ya paran (384 escrituras en
  5 min frente a 2.577). Cada región usaba ~29 llamadas por minuto de las ~47 que permite el ritmo: el hilo de descarga
  iba solo una partida por delante y se paraba mientras la región guardaba y analizaba. Ahora va 2 por delante.
- **Subida de las 03:56 (50,5 partidas/min con 2 por delante):** la descarga no mejoró: con un hilo cada llamada espera
  a la anterior (un timeline pesa 1-2 MB) y cada región se quedaba en ~28 llamadas por minuto. Ahora 2 hilos de descarga
  por región y 3 partidas por delante. Guardar sigue en 1,3-1,9 s (~0,3 s por escritura con el candado; WAL 289 MB).
- **Subida de las 09:41 (61,4 partidas/min, 2 hilos de descarga):** guardar ~1,0-1,25 s, analizar ~0,75 s y esperando
  la descarga ~1,7 s por partida; cada región, 45-69 llamadas de 100 cada 2 min. La precarga de OP.GG y Lolalytics
  (refresco de la mañana) hizo ~1.470 escrituras y esperó ~850 s el candado. Ahora la precarga espera 10 s entre tareas
  mientras recoge el recolector (lo que pides desde la web no), 3 hilos de descarga por región y 4 partidas por delante,
  y cada 5 min se intenta recortar el WAL (686 MB) esperando como mucho 2 s.
- **Subida de las 09:54 (64 partidas/min en 7 min, pico de 69,5):** el WAL pasaba de 1,25 GB y no se podía recortar
  (siempre había escrituras o lecturas en marcha); al crecer, guardar volvía a ~0,9 s. Era la partida y el timeline en
  bruto (~400 KB por partida) dentro de la base de datos: ahora van a `data/raw_otp/<plataforma>/<id>.json.gz` (una vez
  por partida, fuera de GitHub) y en la base de datos solo el análisis. Reanalizar los lee de la base de datos (las de
  antes) o de su fichero.
- **Subida de las 10:17 (65,8 partidas/min de media, 68,2 ahora):** el WAL seguía creciendo (1,25 → 1,6 → 2,1 GB en
  15 min). El recorte cada 5 min competía con las escrituras del recolector (el candado estaba cogido más de la mitad del
  tiempo: las búsquedas sumaban 3.156 s esperándolo en 9 min) y no ganaba nunca. Ahora el recorte coge el candado de la
  app (las escrituras esperan unos segundos), espera hasta 10 s a las lecturas en curso, se hace también al arrancar (el
  WAL que dejó la sesión anterior) y en cuanto pasa de 256 MB. El embudo enseña el último recorte (`db.wal_recorte`) y
  el registro apunta cada recorte grande o fallido.

## M) Revisión por rol y elo: roams, visión y errores (24-11-2026, 33.694 partidas con timeline)

Platino agrupa Hierro → Platino (ya hay partidas de elos bajos) y Maestro incluye Gran Maestro.

**Roams: no basta con contarlos, importa adónde vas** (`route_macro`: el punto más lejos de tu línea en cada salida de
20 s o más antes del 14). El support de Aspirante sale el doble que el de Platino (1,4 → 2,9 por partida):

| Support, Platino → Aspirante | Otra línea | Río | Tu jungla | Jungla rival |
|---|---|---|---|---|
| Salidas por partida | 0,42 → 1,00 | 0,70 → 1,24 | 0,21 → 0,47 | 0,03 → 0,15 |

El mid de Aspirante va más a otras líneas (0,60 → 1,20) y **menos** al río (0,56 → 0,35); el ADC, más a otras líneas
(0,19 → 0,55). En top cambia poco. «Mi progreso» enseña cada destino en el rol donde separa elos.

**Visión útil** (dónde estabas al poner cada ward, con tu recorrido):

| | Platino | Aspirante | Roles donde se enseña |
|---|---|---|---|
| Objetivos con ward tuyo cerca en los 2 min previos | 41 % | 52 % | support (top, mid, ADC y jungla: ~20 %, sin cambio) |
| Pinks junto a dragón o barón | 0,9 / 1,9 | 2,0 / 3,4 | jungla / support |
| Wards en la jungla rival antes del 10 (seguir al jungla) | 0,26 / 0,09 | 0,40 / 0,25 | jungla / support |
| Wards en la jungla rival (antes del 14, % de los tuyos) | 10 % / 2,8 % / 1,6 % | 13 % / 6,7 % / 2,9 % | jungla / support / top |
| Wards en tu línea (support, antes del 14) | 57 % | 43 % | support |
| Wards antes del 14 | 3,6 / 8,6 | 4,4 / 12,0 | jungla / support |

Tus partidas: los wards (segundo y tipo) se guardan en el análisis (`my_wards`); las de antes los sacan de la línea de
tiempo al poner al día los resúmenes (`DIGEST_VERSION = 6`), sin volver a analizar.

**Errores que no lo eran:**

- **Cambios igualados.** Entre las muertes «adelantado en la línea» que contaban como error, en el 19-27 % (top), el
  28-33 % (mid) y el 39-51 % (ADC y support) tu equipo mató a alguien allí mismo en los 12 s siguientes, y más cuanto
  más alto el elo. Ahora una muerte adelantada o en un gank en la que caísteis los mismos en los dos bandos
  (`fight_even`) no cuenta como error (`unless` en `MISTAKE_TYPES`): se ve en la repetición con «Cambio igualado».
- **Lo que los mejores hacen más.** Aun sin los cambios igualados, «te ganquean» y «adelantado» suben con el elo en las
  líneas (support en ganks 0,23 → 0,39 por partida; top adelantado 0,32 → 0,42): en elo alto se juega con más presión y
  más fuera de la línea. Un error que el elo más alto comete un 15 % más o más (`RISE_RATIO`) pesa un cuarto en «Errores
  que repites» (los que no separan, la mitad) y lo dice: «los mejores lo hacen más».
- Siguen separando bien (bajan con el elo en todos los roles): morir aislado, perder duelos 1 contra 1, aceptar duelos
  con niveles u objetos de menos, sin pinks, sin quitar wards y el ritmo de objetos (el orden de habilidades también,
  salvo en support).

### Wards rivales: te ven llegar con la lente sin usar y pinks rivales en tu jungla (24-11-2026)

Riot no da la posición de los wards ni cuál cae en cada WARD_KILL. `_ward_map` los estima: el sitio es donde estaba
quien lo puso (se pone a menos de ~600 de uno; la posición sale del recorrido reconstruido), la vida es de 90-120 s el
trinket amarillo (según el nivel), 150 s el ward de support y hasta que la quitan la pink; cada jugador tiene 1 pink y 3
wards sigilosos a la vez (el siguiente quita el más antiguo), y el ward que cae es el del tipo y equipo rival más cerca de
quien lo quita (a menos de 2.500; si no, no se sabe cuál fue).

- **Te ve un ward rival antes de la jugada y llevabas la lente** (`death_lens_unused`): mueres en una jugada de pocos
  (antes del 14, o con 1-2 aliados como mucho), fuera de las bases y donde la visión importa (gank, río, jungla); en los
  15 s anteriores pasaste a menos de 1.100 de un ward rival vivo que no quitaste, y tu trinket era la lente (la última
  compra de trinket, 3364). Un cambio igualado no cuenta. En tus 55 partidas guardadas: 25 muertes con un ward rival
  viéndote, 8 con la lente, 4 cuentan como error.
- **Pinks rivales que revelan tu jungla mucho rato** (`enemy_pink_ignored`): pink rival en tu jungla viva 3 min o más.
  Es error en cualquier rol si pasaste a su lado (una vez basta: tu posición cada 5 s a menos de 1.200, con la pink
  puesta desde hace 20 s o más, para no culparte si te la ponen en plena pelea) y siguió viva 1 min más; si no pasaste,
  solo en jungla y support (limpiar vuestra jungla es vuestro trabajo); a top, mid y ADC no se les dice nada.
- **Por elo:** no se re-analizan las ~35.000 partidas guardadas (horas de CPU con el recolector en marcha); el perfil por
  elo cuenta estos dos errores solo en las partidas analizadas con este código (`LATER_TYPES`), así que se calibran
  solos con las partidas nuevas.
- **Visión de los súbditos** (que te vean llegar a una jugada por la oleada): no se puede medir. Riot no da dónde está
  cada oleada, y entre dos posiciones conocidas el camino lo pone la reconstrucción (el más corto que rodea muros): si
  entraste por la línea o por los arbustos no está en los datos.

### «Datos y API»: contador, subida a GitHub y botón de detener (24-11-2026)

- **Partidas analizadas a saltos de 20-80:** se pintaban con la petición de la lista de OTPs, que se pide cada 10 s y
  con el recolector a tope tarda más (y una respuesta que llega tarde se descartaba). Ahora van en `/api/crawler/otps/live`,
  ligera (contadores en memoria que suman cada partida nueva al guardarla y se recuentan de verdad cada 5 min en un hilo
  aparte), cada 2,5 s y una petición cada vez.
- **Subida a GitHub:** dice en qué fase va (paso N de 6: estado y registro, esperar a la partida a medio escribir,
  `git add`, commit con cuántos ficheros, `git pull`, `git push` con su porcentaje y lo enviado) y cuánto tardó la última.
- **Detener:** responde al momento («Deteniendo…» en cuanto pulsas). Antes apuntaba en la base de datos que no arrancara
  solo antes de responder y esa escritura esperaba al candado del recolector. Si algún hilo tarda más de 15 s en parar,
  el registro dice cuál y en qué línea está (cada 30 s), y «Recolector detenido en N s» al acabar.
- **«Datos y API» congelado (se quedaba en 47 partidas con el recolector guardando 55/min):** la vista pedía el estado
  cada 2,5 s y tiraba la respuesta que llegaba cuando ya había salido otra; con el servidor cargado se tiraban todas.
  Ahora un refresco cada vez y se pinta lo que llega; las fuentes y la lista de OTPs, también una petición cada vez (la
  de OTPs se acumulaba cada 10 s y lo frenaba más). Las peticiones de más de 2 s quedan en el registro («Petición
  lenta») y `_registro.json` guarda todos los hilos aunque se llamen igual.
- **Para saber por qué va lento sin adivinar** (`app/diag.py`): dos veces por segundo se mira en qué línea del proyecto
  está cada hilo y se suma por grupo (cada región del recolector, las búsquedas, la web, la precarga, la subida). El
  embudo (`data/otps/_busqueda.json`, `diagnostico`) y `_registro.json` traen los últimos 5 min en %: esperando turno
  de la key de Riot, a la red, un candado, la base de datos, analizando, ficheros, parado sin trabajo, y las líneas
  donde más está. Cada 5 min el registro lo resume («Dónde se va el tiempo») junto con las consultas de más de 0,5 s y
  las escrituras de más de 1 s con el candado cogido, con la línea que las lanza (`db.lentas`).
- **Primer diagnóstico (subida de las 11:14):** el freno es la key de desarrollo de Riot. Los hilos que descargan pasan
  el 54-67 % del tiempo esperando turno del limitador y los recolectores el 75 % esperando esas descargas; guardar y
  analizar ya solo son ~20 %. El limitador da el máximo de la key en una simulación (48 llamadas/min por región con 5 %
  de reserva): estáis cerca del techo (~23 partidas/min por región; dos llamadas por partida, la partida y el timeline).
  El «63 de 100» del embudo estaba sesgado: se leía ~20 s después de detener, con la ventana medio vacía; ahora se lee
  antes del embudo, y cada servidor dice sus llamadas por minuto, el máximo con la reserva de ese momento y por qué se
  espera (`ratelimit.<host>.espera_s`; en el registro, «Key de Riot»).
- **El registro se cortaba a las 10:57:** en Windows no podía rotar (renombrar un fichero abierto falla) y el `logging`
  estándar perdía todo lo de después. Ahora, si no puede renombrar, copia y vacía; la copia que se sube junta el final
  del anterior y del actual.
- **La lista de OTPs de la web** recalculaba en cada petición la lista de jugadores (hasta 18 s) y el embudo (hasta
  22 s): CPU para el recolector y lecturas tan largas que el WAL no se podía recortar. Ahora la lista se reutiliza 60 s y
  el embudo es el que escribe el recolector cada 5 min.
- **Errores de visión rival por elo:** 758 partidas analizadas ya con ellos, todas de Esmeralda o menos (la búsqueda va
  ahora por los elos bajos): aún no se puede comparar con Diamante → Aspirante.
- **Subida de las 11:39 (6,5 min):** ~75 partidas/min en total (Europa 20,0, SEA 19,6, Americas 18,6, Asia 16,6 por
  minuto), lo mejor hasta ahora. La key va al 84-93 % de su ritmo (39-44 de 47,5 llamadas/min), todas las esperas son por
  el ritmo (ningún 429) y salen ~2,1 llamadas por partida: casi nada se desperdicia. Lo que faltaba: los hilos de descarga
  parados el 15-25 % del tiempo porque, mientras la región esperaba 1-3 s el candado para guardar, las 4 partidas por
  delante ya estaban bajadas. Ahora van 8 (`PREFETCH_AHEAD`). El resumen de la caché de OP.GG («Datos y API», cada 2,5 s,
  ~0,75 s) se reutiliza 30 s. Errores de visión rival: 1.212 partidas con ellos, solo 18 de Diamante o más.

### Auditoría de la calibración: ¿todos igual o falla el detector? (24-11-2026, `app/coach/calibration_audit.py`)

`python -m app.coach.calibration_audit data salida.json`. Para cada rol, métrica de «Mi progreso» y tipo de error:
¿separa elos (z del elo más bajo con 100+ partidas frente al más alto)?; si no, ¿la mitad de partidas con la posición más
segura (menos tiempo a más de 20 s de un punto real antes del 14) sí separa? (entonces lo iguala el ruido de la
reconstrucción); y ¿va con la estadística oficial de Riot que le toca? Cada detección guarda ahora su certeza (`ev`:
segundos de tu posición y de la de los rivales implicados a un punto real) para repartir los errores igual.

Lo que encontró con ~35.000 partidas:

- **Wards por zona (río, mitad rival, jungla rival): el detector no distingue.** Mi % de pinks en el río o la mitad rival
  no va con la estadística oficial de Riot (`controlWardTimeCoverageInRiverOrEnemyHalf`: r = −0,05 … 0,08); calculando
  lo mismo que Riot (tiempo con una pink allí), r = 0,31-0,36, y sin mirar dónde, 0,59-0,79: la posición al poner un
  ward (la del campeón, reconstruida, ~1.100 de error típico) no basta para la frontera del río, que es justo donde se
  ponen. Por eso esas métricas salían planas.
- **En su lugar, la visión exacta de Riot, que sí separa** (nuevas filas `rl_*` en «Mi progreso» y en el perfil por elo,
  `VERSION = 6`; tus partidas las sacan del participante guardado, `DIGEST_VERSION = 7`), Platino → Aspirante:

  | Por partida (o cada 10 min) | Jungla | Mid | ADC | Support |
  |---|---|---|---|---|
  | Wards rivales quitados antes del 20 | 1,4 → 2,6 | 0,9 → 2,2 | 1,2 → 3,7 | 3,5 → 5,5 |
  | Pinks cada 10 min | 0,4 → 1,1 | 0,3 → 0,6 | 0,2 → 0,6 | 0,9 → 2,2 |
  | Visión por minuto | 0,86 → 1,18 | 0,73 → 0,86 | 0,69 → 0,90 | 2,1 → 3,0 |

  Jungla, además: campamentos de la jungla rival cada 10 min 3,0 → 5,7, cangrejos 3,4 → 4,1, emboscadas (kill saliendo de
  la niebla con un aliado) 1,35 → 1,6. Support: dos wards quitados con un barrido 0,41 → 0,63, wards protegidos
  0,47 → 0,63. En top, quitar wards no cambia (~1,2).
- **Lo que el ruido iguala** (la mitad con posición segura sí separa): en ADC, el % de wards en tu línea y las visitas a tu
  jungla. Lo que de verdad no cambia (igual también con posición segura): el CS perdido por roamear, morir en los roams,
  el tiempo en base y el % de objetivos con ward tuyo en top.
- **Roams y Riot:** el éxito de los roams va con las kills oficiales en otras líneas (`killsOnOtherLanesEarlyJungleAsLaner`,
  r = 0,22 en mid): mide lo que dice.
- **Subida de las 12:11 (con 8 partidas por delante):** la key al 94-97 % de su ritmo (44,8-46,0 de 47,5 llamadas/min)
  y 21-24 partidas/min por región, ~90/min en total: el techo de la key de desarrollo. «Analizadas» queda un 8-18 % por
  debajo de «nuevas» a propósito: son partidas guardadas porque otro jugador de ellas juega un campeón objetivo
  (`_other_target`, para los enfrentamientos), sin análisis de ese jugador.
- **Primeras partidas con la certeza de cada detección** (736, sobre todo Diamante y Maestro): casi todos los errores se
  deciden con posiciones a menos de 10 s de un dato real; la excepción era «el rival coge objetivos estando tú lejos»
  (casi la mitad, con tu posición a más de 20 s de un dato). Ahora, en ese caso, o en los puntos reales de antes y de
  después también estabas lejos, o hace falta estar 2.000 unidades más lejos (`OBJ_FAR_UNSURE_MARGIN`).

### Gráfica por elo y medias con las partidas del ranking (24-11-2026)

- **Gráfica en «Datos y API» → Recolector** (en lugar de los paneles por campeón): barras por elo con su icono, el
  máximo arriba en el eje Y y sus cuartos, que suben en tiempo real (cada 2,5 s, con `/api/crawler/otps/live`). Cuenta
  las partidas analizadas por elo (en «todo el ranking», las guardadas) desde la última subida a GitHub y vuelve a 0 al
  subir (`autopush`: `tiers`). La tabla por servidor y la línea de GitHub siguen debajo.
- **Las partidas del ranking sin analizar también calibran** (`app/coach/ranking_stats.py`): cada partida nueva suma las
  métricas exactas de Riot (`rl_*`) de sus 10 jugadores, por rol y con el elo del jugador por el que se encontró, a
  `data/otps/_medias_ranking.json` (solo sumas; se vuelca cada minuto, al detener y antes de subir). El perfil por elo
  las mezcla con las de las partidas analizadas, cada una pesando sus partidas. El movimiento, los roams y los errores
  siguen necesitando el timeline (las 5 líneas).
- **Solo salían 3-4 elos en la gráfica (subida de las 12:41):** cada región visita a un jugador cada vez (hasta ~50
  partidas, 2-4 min) y la cola elegía siempre el elo con menos partidas en total, así que las 4 regiones se juntaban en
  los 2-3 elos que iban por detrás. Ahora hay turno por elo entre las 4 regiones: el siguiente jugador es del elo menos
  visitado en las últimas 12 visitas (`RECENT_TIER_VISITS`) y, a igualdad, del que va por detrás. Acortar las visitas no
  servía: el corte de «ya no juega ese campeón» se mide por tramos del historial y cortaba antes de tiempo.
- **Reparto por elo con pesos** (en vez de turnos iguales): entre los elos de los filtros, la mitad de las visitas a
  partes iguales (ninguno se para) y la otra mitad según lo que le falta a cada uno en partidas analizadas frente al que
  más tiene (`tier_shares`, `EVEN_SHARE = 0,5`). El siguiente jugador es del elo más por debajo de su parte en las
  últimas 40 visitas de las 4 regiones; si un elo no tiene a nadie esperando, se pasa al siguiente sin frenar. El embudo
  (`reparto_elo`) y el registro cada 5 min dicen, por elo, la parte objetivo, las visitas recientes y las analizadas.
