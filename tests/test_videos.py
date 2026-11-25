"""Recolector de vídeos de YouTube: enlaces, cola, subtítulos, búsquedas guiadas, frames y API (sin red)."""

from __future__ import annotations

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
    youtube = pytest.importorskip("app.videos.youtube")
    assert youtube.classify(text) == expected


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
