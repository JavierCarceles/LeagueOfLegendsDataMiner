"""Recolector de vídeos de YouTube (coaches de LoL): solo recoge datos, el análisis se hace después a mano.

De cada vídeo (pegado a mano, de una lista, de un canal o de una búsqueda) se guarda en ``data/videos/<id>.json``:

* los metadatos (título, canal, duración, capítulos, descripción…);
* todo lo que se dice, con tiempos (subtítulos de YouTube y, si no hay, voz a texto en este ordenador);
* si es una partida: los tramos con partida, el reloj del juego, hacia dónde mira la cámara del jugador, su campeón
  y los iconos del minimapa (aliados y enemigos), todo sacado de los frames sin guardar ninguna imagen.

Lo descargado (vídeo, audio) va a una carpeta temporal que se borra al terminar cada vídeo. Los datos se suben a
GitHub con el resto de ``data/`` (``app.autopush``).

Las dependencias (yt-dlp, OpenCV…) se instalan solas la primera vez que hacen falta (``app.videos.deps``): este
paquete no importa nada pesado al arrancar la app.
"""
