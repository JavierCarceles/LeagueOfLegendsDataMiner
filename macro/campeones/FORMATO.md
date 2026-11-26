# Fichas de campeón

Una ficha por campeón en `macro/campeones/<clave>.json`. La `<clave>` es el id de Data Dragon, por ejemplo
`MonkeyKing`, `Nunu`, `KSante` o `LeeSin`. Cada ficha cubre su rol principal y, si juega mucho un segundo rol (al
menos el 25 % de sus partidas), también ese. Se investigan en internet campeón a campeón. La primera tanda es del
26-11-2026, con el parche 16.23.

Las fichas las usa el reconstructor de partidas (`app/coach/fichas.py` las carga). Cada campo responde a una pregunta
que surge al revisar el timeline de Riot. Por ejemplo:

- ¿Peleó en su momento fuerte o en el del rival?
- ¿Era normal ese salto de una punta del mapa a la otra (R de Twisted Fate o de Shen)?
- ¿Esa ruta de jungla es la habitual?
- ¿Cuándo suele roamear un mid o un support?

Todo el texto va en **español**, con palabras propias (nada copiado de las guías) y **concreto**: niveles, minutos,
objetos, alcances y enfriamientos. Mejor «a nivel 3 con Q-W-E busca el all-in si el rival no tiene Destello» que
«es fuerte al principio».

Para comprobar las fichas:

```
python -m app.coach.fichas --comprobar macro/campeones/LeeSin.json
```

Para ver qué campeones no tienen ficha todavía:

```
python -m app.coach.fichas --faltan
```

## Campos

| Campo | Qué es |
|---|---|
| `clave`, `nombre` | Id y nombre de Data Dragon. |
| `parche`, `revisado` | Parche y fecha de la información, por ejemplo `"16.23"` y `"2026-11-26"`. |
| `roles` | Lista de `{"rol", "parte", "principal"}`. `rol` es TOP, JUNGLE, MID, ADC o SUPPORT. `parte` es la fracción de sus partidas en ese rol, sacada de estadísticas actuales (Lolalytics, OP.GG, U.GG), o `null` si no se encuentra. Exactamente un rol lleva `"principal": true`. Un segundo rol solo entra si tiene al menos el 25 % de las partidas. |
| `arquetipo` | Una frase: «asesino de jungla con picos en 6 y 2 objetos», «tirador hipercarry de rango largo». |
| `alcance` | `cuerpo_a_cuerpo` o `distancia`. |
| `fuerza_por_fase` | `{"temprana", "media", "tardia"}` de 1 a 5, y `nota` con el porqué. |
| `picos_de_poder` | Lista de `{"cuando", "nivel", "objetos", "objeto", "por_que"}`. `nivel` es 1-18 o `null`. `objetos` es cuántos objetos completos tiene, 0-6 o `null`. `objeto` es el objeto concreto o `null`. Por ejemplo, nivel 2, nivel 3, nivel 6, primer objeto, 2 objetos o un objeto clave. |
| `habilidades` | `pasiva`, `Q`, `W`, `E` y `R`: qué hace cada una en la práctica. Incluye el enfriamiento y el alcance que importan para decidir (de Data Dragon), y los detalles que cambian el juego: reinicios, cargas, ejecuciones, si atraviesa súbditos, etc. |
| `orden_habilidades` | `maximizar` es el orden de Q, W y E (y R en campeones como Udyr). `niveles_1_3` son las tres primeras subidas. `nota` recoge variantes según rival o rol. La R se sube en 6, 11 y 16 salvo que la nota diga otra cosa. |
| `movilidad` | Ver la tabla siguiente. |
| `unico` | Al menos 3 mecánicas propias del campeón: cargas, transformaciones, pasivas que dependen del mapa, interacciones especiales. |
| `combos` | Combos con su orden de teclas y cuándo usar cada uno. |
| `build` | `objetos_clave`, `runas` (las habituales y alternativas) y `hechizos`. Los hechizos van en español: Destello, Castigo, Teleportar, Prender, Agotamiento, Barrera, Curar, Fantasmal o Purificar. `nota` dice qué cambia según el rival. |
| `por_rol` | Un bloque por cada rol de `roles`. Ver más abajo. |
| `peleas` | `papel` (iniciador, flanqueador, peeler, poke, etc.), `como_entrar`, `objetivo` (a quién va) y `posicion`. |
| `macro` | `split_push`, `objetivos` (dragón, Heraldo, larvas, Barón y torres: si los hace solo, a qué nivel) y `plan_de_partida`. |
| `debilidades` | Al menos 2. |
| `contrarrestado_por`, `bueno_contra`, `sinergias` | Listas de campeones con su nombre en inglés de Data Dragon (Wukong, Nunu & Willump, Bard…). |
| `errores_comunes` | Errores típicos de quien juega el campeón, sobre todo en elo bajo. |
| `senales_reconstructor` | Lo que el reconstructor debería esperar o interpretar en el timeline de Riot de este campeón. El timeline da posiciones una vez por minuto, muertes, objetivos, objetos, niveles, subidas de habilidad, CS, CS de jungla y wards. Ejemplos: «a nivel 6 roamea con R a bot: kills en bot con su última posición en mid son normales» o «full clear en ~3:05: CS de jungla 24 a los 3:30». |
| `fuentes` | Las URLs que se usaron (al menos 2). |

### `movilidad`

| Campo | Qué es |
|---|---|
| `desplazamientos` | Qué saltos, dashes o blinks tiene y con qué alcance. |
| `salta_muros` | `no`, `si` o `con_condiciones`. Si no es `no`, `como_salta_muros` explica cómo. |
| `invisibilidad` | `null` o el texto: qué habilidad, cuánto dura y si se ve. |
| `global` | Lista, vacía si no tiene nada, de efectos de alcance enorme o global: `{"habilidad", "tipo", "desde_nivel", "alcance", "nota"}`. `habilidad` es P, Q, W, E o R. `tipo` es teletransporte, daño, escudo, curacion, vision, control u otro. `alcance` es «global» o «5000 unidades». Por ejemplo, la R de Twisted Fate, Shen, Pantheon, Galio, Ryze, Karthus, Jinx o Ezreal, o la R de Nocturne. |

### Bloques de `por_rol`

Cada bloque admite más campos si son útiles.

**JUNGLE**

| Campo | Qué es |
|---|---|
| `inicio` | Campamento de inicio y por qué. |
| `rutas` | Lista de `{"nombre", "orden", "despues", "nivel", "minuto", "cuando"}`. `orden` es una lista de campamentos: `blue`, `gromp`, `wolves`, `raptors`, `red`, `krugs`, `scuttle_top` o `scuttle_bot`. `despues` dice qué hace al acabar (gank a qué línea, scuttle, base o invasión). `minuto` es la hora aproximada al acabar la ruta, por ejemplo `"3:15"`. `cuando` explica en qué partidas elegir esa ruta. |
| `ganks` | Niveles fuertes, por dónde entra y a qué líneas. |
| `objetivos` | Larvas, dragón, Heraldo y Barón: si los hace solo y a qué nivel. |
| `invasion` | Si invade y cuándo. |
| `contra_jungla` | Opcional: cómo jugar contra invasiones. |
| `plan` | El plan de juego del jungla. |

**TOP y MID**

| Campo | Qué es |
|---|---|
| `linea` | `niveles_1_3`, `tradeos`, `oleadas`, `all_in` y `jugar_seguro`. |
| `roameos` | Cuándo, hacia dónde y con qué. |
| `fase_media` | El plan después de la fase de líneas. |
| `matchups_dificiles`, `matchups_faciles` | Listas de campeones. |

**ADC y SUPPORT**

| Campo | Qué es |
|---|---|
| `linea_2v2` | `nivel_2`, `tradeos` y `oleadas`. El ADC añade `con_que_supports` y el support `con_que_adcs` (al menos 2 cada uno). Los dos añaden `sufre_contra`. |
| `fase_media` | El plan después de la fase de líneas. |
| `matchups_dificiles`, `matchups_faciles` | Listas de campeones. |

El support añade además `roameos` y `vision`.

## Bloque `datos_otp`

Lo genera `python -m app.coach.fichas_datos` con las partidas que guarda el programa: OTPs de `data/otps` de Diamante
en adelante y de los parches más recientes que juntan bastantes partidas. Si no hay bastantes OTPs, usa las partidas
de `data/partidas_campeon`, y el bloque lo dice. No se edita a mano: se rehace con el mismo comando cuando hay más
partidas.

Por cada rol con datos incluye:

- **Muestra:** partidas, parches, elo, jugadores.
- **Victorias:** el % total y el % según lo que dura la partida (`victorias_por_duracion`, la curva de fuerza real).
- **Ritmo de compra:** minuto de la primera vuelta a base, del primer objeto y del segundo, y qué primer objeto compra.
- **Habilidades:** orden de las 9 primeras subidas y cuál maximiza.
- **Peleas y visión:** participación en kills, parte del daño, visión por minuto y muertes antes del 14.
- **Posición al final de la partida:** tiempo solo en una lateral (split push) o agrupado.

Y según el rol:

- **Junglas:** campamento inicial, CS de jungla a los 3:30 y a los 10, primer gank, ganks antes del 10 y a qué línea, CS robado y escuttles.
- **Línea:** CS a los 10, diferencias a los 10, placas y tiempo en otras líneas o en el río antes del 14 (roameos).
- **Support:** además, wards y pinks.

Las cifras van en percentiles 25, 50 y 75.
