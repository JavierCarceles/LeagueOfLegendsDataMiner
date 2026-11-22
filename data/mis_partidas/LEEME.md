# Mis partidas

Se escribe solo mientras la app está abierta (Tempu Drafter, «Mi progreso»): cada partida tuya analizada, ordenada
por rol y campeón.

* `resumen.json`: tu perfil, cuántas partidas tienes por rol, campeón y cola, las medias de tus métricas por rol
  (todas y las 20 últimas) y el índice de todas las partidas con su fichero.
* `<ROL>/<Campeón>/<fecha>_<partida>_<V|D>.json.gz`: una partida (V = victoria, D = derrota). Dentro:
  `analysis` (el análisis completo: errores con su contexto, métricas, build, jungla, series minuto a minuto y la
  repetición 2D), `match` y `timeline` (lo que devuelve Riot, en bruto, para volver a analizarla).

Se leen con cualquier lector de gzip + JSON. Si borras la carpeta, se vuelve a escribir sola desde la base de datos.
