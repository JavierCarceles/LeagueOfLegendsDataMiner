"""Recolector de vídeos de YouTube: enlaces, cola, subtítulos, búsquedas guiadas, frames y API (sin red)."""

from __future__ import annotations

import itertools
import json

import pytest

from app.videos import store as S
from app.videos.collector import build_queries
from app.videos.store import VideoStore

VID = "8jnpy90gy1w"
LUX = "lux support"
ADD = "/api/videos/add"
COACH_VIDEOS = "https://www.youtube.com/@CoachK/videos"


# ---------------------------------------------------------------------- enlaces
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"https://www.youtube.com/watch?v={VID}", ("video", VID)),
        (f"youtube.com/watch?v={VID}&list=PL123&t=40s", ("video", VID)),
        (f"https://youtu.be/{VID}?t=12", ("video", VID)),
        (f"https://www.youtube.com/shorts/{VID}", ("video", VID)),
        (f"https://m.youtube.com/live/{VID}", ("video", VID)),
        (VID, ("video", VID)),
        ("https://www.youtube.com/playlist?list=PLabc_DEF", ("lista", "https://www.youtube.com/playlist?list=PLabc_DEF")),
        (COACH_VIDEOS, ("canal", COACH_VIDEOS)),
        ("https://www.youtube.com/@CoachK", ("canal", COACH_VIDEOS)),
        ("https://www.youtube.com/channel/UC1234567890123456789012", ("canal", "https://www.youtube.com/channel/UC1234567890123456789012/videos")),
        ("https://www.youtube.com/results?search_query=lux+support", ("busqueda", LUX)),
        (LUX, ("busqueda", LUX)),
    ],
)
def test_classify_links(text, expected):
    from app.videos.links import classify

    assert classify(text) == expected


def test_add_works_before_tools_are_installed(tmp_path, monkeypatch):
    """En un Windows recién actualizado yt-dlp aún no está (se instala después de añadir): «Añadir» no puede necesitarlo."""
    import sys
    from types import SimpleNamespace

    import app.videos
    from app.videos.collector import VideoCollector

    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    monkeypatch.delitem(sys.modules, "app.videos.youtube", raising=False)
    monkeypatch.delattr(app.videos, "youtube", raising=False)
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path, pause_in_game=False), tracker=None)
    sources = VideoCollector(svc).add("youtube.com/@maurogarih", start=False)
    assert [(s["tipo"], s["valor"]) for s in sources] == [("canal", "https://www.youtube.com/@maurogarih/videos")]


def test_error_kinds():
    youtube = pytest.importorskip("app.videos.youtube")
    assert youtube.classify_error("ERROR: [youtube] abc: Private video") == "final"
    assert youtube.classify_error("HTTP Error 429: Too Many Requests") == "limite"
    assert youtube.classify_error("Sign in to confirm you’re not a bot") == "limite"
    assert youtube.classify_error("Connection reset") == "otro"


# ---------------------------------------------------------------------- búsquedas guiadas
def test_build_queries_both_languages():
    queries = build_queries("", role="jungla", topic="rutas", champion="Lee Sin")
    assert queries == [("es", "Lee Sin jungla rutas de jungla league of legends"), ("en", "Lee Sin jungle jungle pathing league of legends")]
    assert build_queries(LUX, langs=("en",)) == [("en", f"{LUX} league of legends")]
    assert build_queries("lol jungle tips", langs=("en",)) == [("en", "lol jungle tips")]
    assert build_queries("", langs=("es",)) == []


# ---------------------------------------------------------------------- cola
def test_queue_flow(tmp_path):
    store = VideoStore(tmp_path)
    source = store.add_source("busqueda", LUX, {"n": 5})
    assert store.next_source()["id"] == source["id"]
    added = store.add_videos([{"id": "aaaaaaaaaaa", "title": "Uno", "duration": 600}, {"id": "bbbbbbbbbbb"}, {"id": "aaaaaaaaaaa"}], source)
    assert added == 2
    store.finish_source(source["id"], found=3, added=added)
    assert store.next_source() is None
    first = store.next_video()
    assert first["id"] == "aaaaaaaaaaa" and first["titulo"] == "Uno" and first["fuente"] == source["id"]
    store.start(first["id"])
    assert store.counts()["procesando"] == 1
    # La app se cerró a mitad: vuelve a la cola al arrancar.
    assert VideoStore(tmp_path).reset_working() == 1
    store.reload()
    store.done("aaaaaaaaaaa", {"trozos": 3})
    counts = store.counts()
    assert counts["hecho"] == 1 and counts["pendiente"] == 1 and counts["total"] == 2
    on_disk = json.loads((tmp_path / S.QUEUE_FILE).read_text(encoding="utf-8"))
    assert on_disk["videos"]["aaaaaaaaaaa"]["resumen"] == {"trozos": 3}
    assert not list(tmp_path.glob("*.tmp"))


def test_failures_retry_and_skip(tmp_path):
    store = VideoStore(tmp_path)
    store.add_videos([{"id": "ccccccccccc"}, {"id": "ddddddddddd"}])
    for _ in range(S.MAX_TRIES):
        store.fail("ccccccccccc", "boom")
    assert store.get("ccccccccccc")["estado"] == S.FAILED
    # Un fallo reciente espera antes de reintentarse.
    store.fail("ddddddddddd", "red")
    assert store.next_video() is None
    assert store.next_video(now=10**12)["id"] == "ddddddddddd"
    store.fail("ddddddddddd", "Private video", final=True)
    assert store.get("ddddddddddd")["estado"] == S.SKIPPED
    assert store.retry("ccccccccccc") and store.get("ccccccccccc")["estado"] == S.PENDING
    store.save_result("ccccccccccc", {"id": "ccccccccccc"})
    assert store.load_result("ccccccccccc") == {"id": "ccccccccccc"}
    assert store.remove("ccccccccccc") and store.load_result("ccccccccccc") is None
    store.set_paused(True)
    assert VideoStore(tmp_path).paused


# ---------------------------------------------------------------------- subtítulos
def test_parse_json3_and_chunks():
    transcript = pytest.importorskip("app.videos.transcript")
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 4000},
            {"tStartMs": 1000, "segs": [{"utf8": "hola"}, {"utf8": " a", "tOffsetMs": 400}, {"utf8": " todos", "tOffsetMs": 800}]},
            {"tStartMs": 3000, "aAppend": 1, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 15000, "segs": [{"utf8": "vamos al dragón."}]},
        ]
    }
    words = transcript.parse_json3(data)
    assert words[0] == (1.0, "hola") and words[2] == (1.8, " todos") and len(words) == 4
    assert transcript.chunks(words, span=20) == [[1.0, "hola a todos vamos al dragón."]]
    assert transcript.chunks(words, span=10) == [[1.0, "hola a todos"], [15.0, "vamos al dragón."]]


def test_parse_vtt_drops_rolling_repeats():
    transcript = pytest.importorskip("app.videos.transcript")
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nwe go top\n\n"
        "00:00:03.000 --> 00:00:05.000\nwe go top\nand <c>ward</c> the river\n\n"
        "01:02.500 --> 01:04.000\nback\n"
    )
    assert transcript.parse_vtt(vtt) == [(1.0, "we go top"), (3.0, "and ward the river"), (62.5, "back")]
    assert transcript.parse_srv3('<p t="1500" d="10">gank <s>bot</s></p>') == [(1.5, "gank  bot")]


def test_track_preference_original_language_first():
    transcript = pytest.importorskip("app.videos.transcript")
    info = {
        "language": "es",
        "subtitles": {},
        "automatic_captions": {"en": [{"ext": "json3"}], "es-orig": [{"ext": "json3"}], "es": [{"ext": "json3"}], "fr": [{"ext": "json3"}]},
    }
    kinds = [(kind, lang) for kind, lang, _ in transcript.candidate_tracks(info)]
    assert kinds[0] == ("auto", "es")
    assert ("traducido", "en") in kinds and all(lang != "fr" for _, lang in kinds)
    info["subtitles"] = {"en-US": [{"ext": "vtt"}]}
    assert transcript.candidate_tracks(info)[0][:2] == ("manual", "en-US")


# ---------------------------------------------------------------------- frames
def _fake_map(np, cv2):
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    return cv2.GaussianBlur(cv2.resize(base, (512, 512), interpolation=cv2.INTER_CUBIC), (0, 0), 4)


def test_minimap_located_and_presence():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from app.videos import frames as vf

    world = _fake_map(np, cv2)
    frame = np.full((1080, 1920, 3), 40, np.uint8)
    frame[760:1070, 1600:1910] = cv2.resize(world, (310, 310), interpolation=cv2.INTER_AREA)
    maps = vf.MapMatcher(world)
    box = maps.locate(frame)
    assert box is not None and box.corner == "derecha"
    assert abs(box.x - 1600) <= 3 and abs(box.y - 760) <= 3 and abs(box.size - 310) <= 3
    assert maps.presence(frame, box) > 0.9
    assert maps.presence(np.full_like(frame, 40), box) < vf.PRESENT_SCORE
    assert maps.locate(np.full_like(frame, 40)) is None
    # Coordenadas del juego: abajo a la izquierda es la base azul.
    assert box.to_game(0, box.size) == (vf.MAP_MIN, vf.MAP_MIN)
    assert vf.consensus([box, box, box]).x == box.x
    assert vf.consensus([box, box]) is None


def test_clock_ocr_reads_mm_ss():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from app.videos import frames as vf

    digits = vf.load_digits()
    frame = np.full((1080, 1920, 3), 25, np.uint8)
    x = 1861
    for i, d in enumerate("1234"):
        if i == 2:
            x += 4  # los dos puntos
        frame[9:21, x:x + 9] = digits[int(d)][:, :, None]
        x += 9
    assert vf.Clock(digits).read(frame) == 12 * 60 + 34


def test_camera_box_and_segments():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from app.videos import frames as vf

    minimap = np.full((300, 300, 3), 60, np.uint8)
    cv2.rectangle(minimap, (100, 120), (180, 164), (255, 255, 255), 1)
    u, v, score, cover = vf.find_camera(vf.white_mask(minimap), 81, 45)
    assert abs(u - 140) <= 2 and abs(v - 142) <= 2 and cover > 0.9 and score > 0.5
    present = [(t * 2.0, 0.6) for t in range(30)] + [(60.0 + t * 2.0, 0.0) for t in range(20)] + [(100.0 + t * 2.0, 0.6) for t in range(5)]
    assert vf.segments(present) == [[0.0, 58.0]]


# ---------------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.services
    from app.main import create_app
    from app.videos.collector import VideoCollector
    from tests.factories import make_settings, write_env

    monkeypatch.setattr(VideoCollector, "start", lambda self: None)  # sin hilo (instalaría cosas y saldría a la red)
    monkeypatch.setattr(app.services, "ENV_PATH", write_env(tmp_path))
    application = create_app(make_settings(tmp_path), run_startup=False)
    with TestClient(application) as test_client:
        yield test_client
    application.state.svc.db.close()


def test_api_add_and_list(client):
    res = client.post(ADD, json={"texto": f"https://youtu.be/{VID}"})
    assert res.status_code == 200 and res.json()["fuentes"][0]["tipo"] == "video"
    res = client.post(ADD, json={"texto": "", "rol": "support", "tema": "vision", "idiomas": ["en"], "por_idioma": 3})
    assert [s["valor"] for s in res.json()["fuentes"]] == ["support vision warding guide league of legends"]
    assert client.post(ADD, json={"texto": ""}).status_code == 400
    status = client.get("/api/videos/status").json()
    assert status["cuentas"]["fuentes_pendientes"] == 2 and "jungla" in status["roles"]
    listing = client.get("/api/videos/list").json()
    assert len(listing["fuentes"]) == 2 and listing["videos"] == []
    assert client.delete("/api/videos/zzzzzzzzzzz").status_code == 404
    assert client.post("/api/videos/pause", json={"pausado": True}).json()["pausa"] == "manual"


# ---------------------------------------------------------------------- registro e instalación
def test_videos_log_keeps_every_detail(tmp_path):
    """Todo el detalle del recolector (también DEBUG: lo que dice yt-dlp) va a data/registro/videos_vivo.log."""
    import logging

    from app import logbus

    logbus.add_videos_log(tmp_path)
    log = logging.getLogger("tempu.videos")
    log.debug("yt-dlp: [youtube] detalle")
    log.warning("Vídeos: algo falló")
    for handler in log.handlers:
        handler.flush()
    text = (tmp_path / "registro" / logbus.VIDEOS_LIVE_LOG).read_text(encoding="utf-8")
    assert "detalle" in text and "algo falló" in text
    other = tmp_path / "otra"
    logbus.add_videos_log(other)  # otra carpeta de datos: el registro se va con ella (un solo fichero abierto)
    assert sum(1 for h in log.handlers if getattr(h, "_tempu_videos", None)) == 1
    assert logbus.copy_tail(tmp_path / "registro" / logbus.VIDEOS_LIVE_LOG, tmp_path / "copia.log", max_bytes=120)
    assert (tmp_path / "copia.log").read_text(encoding="utf-8").endswith("algo falló\n")


def test_run_installs_video_tools_without_blocking_the_app(tmp_path, monkeypatch):
    """run.py instala requirements-videos.txt al arrancar; si falla, la app arranca igual y el motivo queda en el registro."""
    import subprocess

    import run

    req = tmp_path / "requirements-videos.txt"
    req.write_text("yt-dlp\n", encoding="utf-8")
    monkeypatch.setattr(run, "VIDEO_REQUIREMENTS", req)
    monkeypatch.setattr(run, "VIDEO_LOG", tmp_path / "registro" / "videos_vivo.log")
    calls = []

    def fake_pip(python, requirements, code=1):
        calls.append(requirements)
        return subprocess.CompletedProcess([], code, stdout="ERROR: sin red")

    monkeypatch.setattr(run, "_pip_install", fake_pip)
    run.ensure_video_requirements(tmp_path, tmp_path / "python")  # no lanza: la app sigue
    assert calls == [req] and not (tmp_path / run.VIDEO_STAMP).exists()
    assert "sin red" in run.VIDEO_LOG.read_text(encoding="utf-8")
    monkeypatch.setattr(run, "_pip_install", lambda python, requirements: fake_pip(python, requirements, 0))
    run.ensure_video_requirements(tmp_path, tmp_path / "python")
    assert (tmp_path / run.VIDEO_STAMP).exists()
    run.ensure_video_requirements(tmp_path, tmp_path / "python")  # ya instalado: no vuelve a llamar a pip
    assert len(calls) == 2


def test_ytdlp_tries_other_connections_and_remembers(monkeypatch):
    """Si YouTube le da a yt-dlp respuestas vacías (Windows, 25-11-2026), prueba otras formas de conectarse y recuerda la buena."""
    youtube = pytest.importorskip("app.videos.youtube")
    yt_dlp = pytest.importorskip("yt_dlp")
    seen = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download=False):
            seen.append(self.opts.get("proxy"))
            if self.opts.get("proxy") != "":
                raise yt_dlp.utils.DownloadError("ERROR: [youtube] x: Failed to extract any player response")
            return {"id": "x", "title": "ok"}

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setitem(youtube._state, "preferred", {})
    assert youtube.video_info(VID)["title"] == "ok"
    assert seen == [None, ""] and youtube._state["preferred"] == {"proxy": ""}
    assert youtube.video_info(VID)["title"] == "ok" and seen[-1] == ""  # a la primera con la que funcionó
    with pytest.raises(youtube.VideoError) as err:  # un vídeo privado no se reintenta
        monkeypatch.setattr(FakeYDL, "extract_info", lambda self, url, download=False: (_ for _ in ()).throw(
            yt_dlp.utils.DownloadError("ERROR: Private video")))
        youtube.video_info(VID)
    assert err.value.kind == "final"


def test_dubbed_video_uses_the_original_audio_language():
    """Con doblajes automáticos YouTube decía «ar» de un vídeo en español: manda la pista de audio original."""
    transcript = pytest.importorskip("app.videos.transcript")
    json3 = [{"ext": "json3"}]
    info = {
        "language": "ar",
        "formats": [
            {"acodec": "opus", "language": "ar", "format_note": "Arabic, low", "language_preference": -1},
            {"acodec": "opus", "language": "es-US", "format_note": "Spanish (US) original (default), low", "language_preference": 10},
            {"acodec": "none", "language": "en"},
        ],
        "automatic_captions": {"ar-orig": json3, "en-US-orig": json3, "es-orig": json3, "pt-BR-orig": json3, "es": json3},
    }
    assert transcript.original_language(info) == "es"
    order = [(kind, lang) for kind, lang, _ in transcript.candidate_tracks(info)]
    assert order == [("auto", "es"), ("doblaje", "en-US"), ("doblaje", "ar"), ("doblaje", "pt-BR")]


def test_ytdlp_error_text_without_console_colours():
    youtube = pytest.importorskip("app.videos.youtube")
    raw = "\x1b[0;31mERROR:\x1b[0m [youtube] abc: Failed to extract any player response"
    assert youtube._clean(raw) == "[youtube] abc: Failed to extract any player response"


def test_parallel_pass_keeps_time_order(tmp_path):
    """El vídeo se lee a trozos en paralelo: las muestras salen en orden y ninguna se repite ni se pierde."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from app.videos import frames as vf

    world = _fake_map(np, cv2)
    path = tmp_path / "partida.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (640, 360))
    minimap = cv2.resize(world, (110, 110), interpolation=cv2.INTER_AREA)
    for k in range(5 * 50):  # 50 s: partida del 10 al 40
        frame = np.full((360, 640, 3), 30, np.uint8)
        if 50 <= k < 200:
            frame[240:350, 520:630] = minimap
        writer.write(frame)
    writer.release()
    box = vf.Box(520, 240, 110, "derecha", 0.9)
    tools = vf.Tools(vf.MapMatcher(world), vf.Clock(vf.load_digits()))
    one = vf.sample_pass(str(path), box, None, tools, 50.0, lambda: False, workers=1)
    many = vf.sample_pass(str(path), box, None, tools, 50.0, lambda: False, workers=4)
    times = [t for t, _ in many.present]
    assert times == sorted(times) and len(times) == len(set(times)) == one.samples == many.samples
    assert many.present == one.present
    assert vf.segments(many.present) == [[10.0, 38.0]]


def test_time_text_and_spans():
    pytest.importorskip("cv2")
    from app.videos import frames as vf

    assert vf.parse_time("04:07") == 247 and vf.parse_time("19.09") == 1149
    assert vf.parse_time("8.6K") is None and vf.parse_time("12:75") is None
    assert vf.parse_time("91:11") is None  # más de 80 min de partida: mala lectura
    spans = vf._spans(600.0, 3)
    assert spans[0][0] == 0.0 and spans[-1][1] == float("inf") and len(spans) == 6
    assert all(a[1] == b[0] for a, b in itertools.pairwise(spans))


def test_clock_reader_falls_back_per_frame():
    """Un vídeo mezcla repeticiones (reloj en el centro) y partidas (a la derecha): se decide frame a frame."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from app.videos import frames as vf

    class FakeGeneric:
        def read(self, frame, region):
            return 243 if region[0] == "espectador" and frame[0, 0, 0] == 1 else None

    reader = vf.ClockReader(vf.Clock(vf.load_digits()), FakeGeneric())
    spectator = np.zeros((1080, 1920, 3), np.uint8)
    spectator[0, 0, 0] = 1
    assert reader.read(spectator) == 243
    assert reader.read(np.zeros((1080, 1920, 3), np.uint8)) is None
    assert reader.hud == "espectador" and reader.modes == {"espectador": 1}
    assert vf.ClockReader(vf.Clock(vf.load_digits()), None).hud is None


def test_outdated_videos_are_redone(tmp_path):
    from types import SimpleNamespace

    from app.videos.collector import FRAMES_VERSION, VERSION, VideoCollector

    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path, pause_in_game=False), tracker=None)
    collector = VideoCollector(svc)
    store = collector.store
    ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd", "eeeeeeeeeee"]
    store.add_videos([{"id": vid} for vid in ids])
    results = {
        "aaaaaaaaaaa": {"version": VERSION, "frames": {"partida": True}},  # partida con frames de antes: se rehace
        "bbbbbbbbbbb": {"version": VERSION, "frames": {"partida": False}},  # sin partida y al día: no hace falta
        "ccccccccccc": {"version": VERSION, "frames": None},  # recogido sin frames (plan B): se rehace
        "ddddddddddd": {"version": VERSION, "version_frames": FRAMES_VERSION, "frames": {"partida": True}},  # al día
        "eeeeeeeeeee": {"version": VERSION - 1, "frames": {"partida": False}},  # versión anterior: todo se rehace
    }
    for vid, data in results.items():
        store.save_result(vid, data)
        store.done(vid, {})
    assert collector._requeue_outdated() == 3
    states = {vid: store.get(vid)["estado"] for vid in results}
    assert states == {"aaaaaaaaaaa": "pendiente", "bbbbbbbbbbb": "hecho", "ccccccccccc": "pendiente",
                      "ddddddddddd": "hecho", "eeeeeeeeeee": "pendiente"}
    assert collector._requeue_outdated() == 0  # los pendientes no se tocan más


def test_champion_from_ability_icons():
    """El campeón sale de los iconos de sus 4 habilidades, cada uno en su casilla (el retrato cambia con el aspecto)."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from app.videos import frames as vf

    rng = np.random.default_rng(11)
    spells = {
        f"C{c}": [cv2.GaussianBlur(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8), (0, 0), 2) for _ in range(4)]
        for c in range(4)
    }
    frame = np.full((1080, 1920, 3), 35, np.uint8)
    for k, icon in enumerate(spells["C2"]):
        x = 780 + k * 44
        frame[990:1028, x:x + 38] = cv2.resize(icon, (38, 38), interpolation=cv2.INTER_AREA)
    cooldown = frame.copy()
    for k in range(3):  # Q, W y E en enfriamiento: oscuras y con el número grande encima
        x = 780 + k * 44
        cooldown[990:1028, x:x + 38] //= 3
        cv2.circle(cooldown, (x + 19, 1009), 11, (255, 255, 255), -1)
    abilities = vf.Abilities(spells)
    slots, keys = abilities.calibrate([cooldown, frame, frame], workers=2)
    assert slots.side == 38 and slots.xs[1] - slots.xs[0] == 44 and keys == ["C2", "C2"]
    assert json.loads(json.dumps(slots.public()))["columnas"] == list(slots.xs)  # se guarda en el JSON del vídeo
    assert abilities.read(frame, slots)[0] == "C2"
    assert abilities.read(np.full_like(frame, 35), slots) is None
    assert abilities.calibrate([np.full_like(frame, 35)] * 4) == (None, [])
    # Dos iconos claros que no están en fila ni a distancia de casilla no dan casillas
    assert vf.fit_slots([0.95, 0.9, 0.2, 0.1], [(10, 50), (54, 80)], 38) is None
    assert vf.fit_slots([0.95, 0.9, 0.2, 0.1], [(10, 50), (200, 50)], 38) is None
    quality, fitted = vf.fit_slots([0.95, 0.2, 0.9, 0.1], [(10, 50), (0, 0), (98, 51), (0, 0)], 38)
    assert fitted.xs == (10, 54, 98, 142) and quality == pytest.approx(1.85)
    assert vf.vote_champion(["C2", "C2", "C1", "C2"]) == {"clave": "C2", "votos": 3, "de": 4}
    assert vf.vote_champion(["C2", "C1", "C3", "C2"]) is None  # menos de 3 votos
    assert vf.champion_counts(["C2", "C1", "C2", "C1", "C3"]) == [{"clave": "C2", "votos": 2}, {"clave": "C1", "votos": 2}]


def test_review_flags_suspicious_results(tmp_path, capsys):
    from app.videos import review
    from app.videos.__main__ import main

    good = {
        "id": "aaaaaaaaaaa", "titulo": "Bien", "duracion": 600, "idioma": "en",
        "transcripcion": {"idioma": "en", "tipo": "manual", "segmentos": [[t, "x"] for t in range(0, 600, 12)]},
        "frames": {"partida": True, "hud": "jugador", "segundos_con_partida": 400, "camara_px": [80, 44],
                   "campeon": {"clave": "Vi", "nombre": "Vi"},
                   "reloj": [[t, 60 + t] for t in range(0, 400, 10)], "camara": [[t, 1, 1] for t in range(0, 400, 2)]},
    }
    bad = {
        "id": "bbbbbbbbbbb", "titulo": "Mal", "duracion": 600, "idioma": "es",
        "transcripcion": {"idioma": "en", "tipo": "traducido", "segmentos": [[0, "x"]]},
        "frames": {"partida": True, "hud": "jugador", "segundos_con_partida": 30, "camara_px": None,
                   "reloj": [[0, 100], [10, 50], [20, 900], [30, 10], [40, 2000]], "reloj_apartadas": [[5, 1]] * 4},
    }
    assert review.check(good) == []
    issues = " | ".join(review.check(bad))
    for text in ("poca transcripción", "traducido", "«es»", "partida muy corta", "apartadas", "recuadro de la cámara"):
        assert text in issues
    # Clips sueltos de un vídeo explicativo (minimapa claro): ni partida dudosa, ni saltos, ni falta el campeón
    clips = {**bad["frames"], "minimapa": {"parecido": 0.61}, "camara_px": [80, 44], "reloj_apartadas": [],
             "camara": [[t, 1, 1] for t in range(0, 30, 2)]}
    assert review.check({**good, "frames": clips}) == []
    # Con más partida, los saltos del reloj y el campeón que no sale sí se avisan
    long_game = {**clips, "segundos_con_partida": 600, "camara": [[t, 1, 1] for t in range(0, 600, 2)],
                 "reloj": [[t * 10, (t % 2) * 900] for t in range(10)]}
    issues = " | ".join(review.check({**good, "frames": long_game}))
    assert "saltos" in issues and "sin campeón" in issues
    assert review.check({"duracion": 300}) == ["sin transcripción", "sin datos de frames"]
    # La orden solo lee lo recogido (sin herramientas de vídeo ni cambios)
    store = VideoStore(tmp_path / "videos")
    store.add_videos([{"id": "aaaaaaaaaaa"}, {"id": "bbbbbbbbbbb"}])
    for data in (good, bad):
        store.save_result(data["id"], data)
        store.done(data["id"], {})
    assert main(["--datos", str(tmp_path), "revisar"]) == 0
    out = capsys.readouterr().out
    assert "bbbbbbbbbbb" in out and "aaaaaaaaaaa" not in out and "2 vídeos revisados, 1 con avisos" in out
    assert main(["--datos", str(tmp_path), "revisar", "--todos"]) == 0
    assert "✓ aaaaaaaaaaa" in capsys.readouterr().out


def test_clock_drops_lone_misreads_but_keeps_real_jumps():
    pytest.importorskip("cv2")
    from app.videos import frames as vf

    clock = [[30, 888], [40, 41], [50, 51], [640, 641], [650, 21], [660, 661],  # «0:21» suelto entre 10:41 y 11:01
             [670, 700], [680, 730],  # cortes de edición: el reloj corre más que el vídeo
             [690, 100], [700, 110], [710, 120],  # rebobinado del coach: sigue desde ahí
             [720, 130], [730, 9000]]
    keep, odd = vf.clean_clock(clock)
    assert odd == [[30, 888], [650, 21], [730, 9000]]
    assert [690, 100] in keep and [670, 700] in keep and len(keep) == len(clock) - 3
    assert vf.clean_clock([[0, 5], [10, 900]]) == ([[0, 5], [10, 900]], [])
    # Vídeo con cortes de edición (el reloj corre más que el vídeo) y una mala lectura: sobra 585, no el 601 de antes
    edited = [[410, 552], [420, 563], [430, 580], [440, 601], [450, 585], [460, 634], [470, 644]]
    assert vf.clean_clock(edited)[1] == [[450, 585]]
    # Dos malas casi seguidas: la buena de en medio (793) se queda
    twice = [[890, 763], [900, 773], [910, 417], [920, 793], [930, 397], [940, 813], [950, 823]]
    assert vf.clean_clock(twice) == ([[890, 763], [900, 773], [920, 793], [940, 813], [950, 823]], [[910, 417], [930, 397]])


def test_autostart_resumes_after_manual_pause(tmp_path, monkeypatch):
    """Al abrir la app sigue solo, como el buscador de partidas: la pausa a mano dura hasta cerrar la app."""
    from types import SimpleNamespace

    from app.videos import collector as C

    monkeypatch.setattr(C, "_clean_temp", lambda: None)
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path, pause_in_game=False), tracker=None)
    collector = C.VideoCollector(svc)
    collector.store.add_videos([{"id": "aaaaaaaaaaa"}])
    collector.store.set_paused(True)
    started = []
    monkeypatch.setattr(collector, "start", lambda: started.append(True))
    collector.autostart()
    assert not collector.store.paused and collector.paused_reason() is None and started == [True]
