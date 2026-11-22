# Partidas de otros jugadores (no OTPs) (micro y macro)

Se escribe solo mientras el recolector baja partidas de OTPs (Tempu Drafter). Estructura:

* `<Campeón>/resumen.json`: lo agregado de sus OTPs, **por rol** (`roles.<ROL>.all`) y, dentro, **por elo**
  (`roles.<ROL>.by_tier.CHALLENGER`, `GRANDMASTER`, `MASTER`…): medias de todas las métricas del análisis de
  «Mi progreso», las mismas en victorias y en derrotas, primer objeto y builds más jugadas (con winrate), orden de
  habilidades, runas, hechizos, primer campamento (jungla), rivales y los avisos que el análisis les marca. Se rehace
  solo cada pocos minutos mientras entran partidas.
* `<Campeón>/<ROL>/<servidor>_<Riot ID>_<id>/perfil.json`: el OTP (Riot ID, servidor, elo actual) y sus partidas
  con ese campeón en ese rol, por elo.
* `<Campeón>/<ROL>/<servidor>_<Riot ID>_<id>/partidas.jsonl.gz`: una línea JSON por partida con el dossier entero:
  elo del OTP en ese momento (`tier`, `division`, `lp`), análisis (muertes con contexto, oro, build, jungla,
  objetivos, macro, fase de líneas, series por minuto), build completa (cada compra/venta con su segundo, orden de
  objetos, vueltas a base, objetos finales, habilidades), runas, hechizos, estadísticas de Riot, posición/zona/oro/CS
  minuto a minuto suyo y de su rival, wards y eventos del mapa.

Ids de campeones y objetos de Data Dragon; tiempos en segundos salvo `min` (minutos). La partida y el timeline en bruto
están en la base de datos (`otp_games`): si esta carpeta se borra o cambia de formato, se regenera sola desde ahí.

**Aquí no hay OTPs**: son jugadores que tienen el campeón entre sus 3 más jugados (búsqueda «Cualquiera que lo juegue»). Sirven para builds y matchups de ese campeón; las comparaciones con OTPs usan solo la carpeta `otps`.
