"""app/riot/ratelimit.py con un reloj falso: nada duerme de verdad.

``RateLimiter.acquire`` espera con ``Condition.wait``; aquí se sustituye por una
condición cuyo ``wait(t)`` adelanta el reloj falso ``t`` segundos.
"""

from __future__ import annotations

import threading

import pytest

from app.riot.ratelimit import DEV_KEY_LIMITS, MARGIN, Priority, RateLimiter, _Bucket, _Window, parse_limits

HOST = "euw1"
METHOD = "match-v5.match"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeCondition:
    """Sustituto de ``threading.Condition``: ``wait()`` adelanta el reloj en vez de dormir."""

    def __init__(self, clock: FakeClock, max_waits: int = 20_000) -> None:
        self.clock = clock
        self.waits: list[float] = []
        self.max_waits = max_waits
        self.on_wait = None
        self._lock = threading.RLock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False

    def wait(self, timeout=None):
        if len(self.waits) >= self.max_waits:
            raise AssertionError("RateLimiter.acquire() no avanza (bucle infinito)")
        step = 0.5 if timeout is None else timeout
        self.waits.append(step)
        self.clock.advance(max(step, 1e-6))
        if self.on_wait:
            self.on_wait()
        return False

    def notify_all(self):
        pass

    def notify(self, n: int = 1):
        pass


def make_limiter(**kwargs) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    limiter = RateLimiter(**kwargs)
    limiter._clock = clock
    limiter._cond = FakeCondition(clock)
    return limiter, clock


def windows(limiter: RateLimiter, host: str = HOST) -> list[tuple[int, int, int]]:
    return [(w["limit"], w["seconds"], w["used"]) for w in limiter.snapshot()[host]["windows"]]


# --------------------------------------------------------------- cabeceras
@pytest.mark.parametrize(
    "header, expected",
    [
        ("20:1,100:120", [(20, 1), (100, 120)]),
        (" 500:10 , 30000:600 ", [(500, 10), (30000, 600)]),
        ("100:120", [(100, 120)]),
        ("garbage,5:x,7:2,1:2:3,", [(7, 2)]),
        ("", []),
        (None, []),
    ],
)
def test_parse_limits(header, expected):
    assert parse_limits(header) == expected


# ------------------------------------------------------------ ventanas
def test_default_dev_key_limits():
    assert DEV_KEY_LIMITS == [(20, 1), (100, 120)]
    bucket = RateLimiter()._app_bucket(HOST)
    assert [(w.limit, w.seconds) for w in bucket.windows] == [(20, 1), (100, 120)]
    # 1 s: 20 - 1 de margen de seguridad; 2 min: el límite entero.
    assert [w.capacity(0.0) for w in bucket.windows] == [19, 100]


def test_low_priority_reserve_only_applies_to_long_windows():
    assert _Window(20, 1).capacity(0.15) == 19
    assert _Window(100, 120).capacity(0.15) == 85
    assert _Window(500, 10).capacity(0.15) == 425
    assert _Window(1, 120).capacity(0.15) == 1  # nunca baja de 1


def test_window_wait_is_time_until_oldest_blocking_stamp_expires():
    w = _Window(3, 10)
    w.stamps.extend([0.0, 1.0, 2.0])
    assert w.wait(2.5, 0.0) == pytest.approx(10 + MARGIN - 2.5)
    assert w.wait(10.5, 0.0) == 0.0  # la primera ya salió de la ventana
    assert list(w.stamps) == [1.0, 2.0]


def test_bucket_wait_is_the_max_over_its_windows():
    bucket = _Bucket([(2, 1), (3, 10)])
    for t in (0.0, 0.1, 0.2):
        bucket.record(t)
    # 1 s: capacidad 1 -> espera a que caduque 0.2; 10 s: capacidad 3 -> espera a que caduque 0.0.
    assert bucket.wait(0.3) == pytest.approx(0.0 + 10 + MARGIN - 0.3)


# --------------------------------------------------------------- acquire
def test_burst_is_capped_at_19_requests_per_second():
    limiter, clock = make_limiter()
    for _ in range(19):
        assert limiter.acquire(HOST, METHOD)
    assert clock.now == 1000.0  # ninguna esperó
    assert not limiter.acquire(HOST, METHOD, timeout=0.5)  # la 20.ª necesitaría ~1.12 s
    start = clock.now
    assert limiter.acquire(HOST, METHOD)
    assert clock.now - start == pytest.approx(1 + MARGIN, abs=1e-3)
    assert limiter.snapshot()[HOST]["requests"] == 20


def test_windows_are_per_host():
    limiter, clock = make_limiter()
    for _ in range(19):
        assert limiter.acquire(HOST, METHOD)
    assert limiter.acquire("europe", METHOD, timeout=0)
    assert not limiter.acquire(HOST, METHOD, timeout=0)


def test_two_minute_window_blocks_after_100_requests():
    limiter, clock = make_limiter()
    for _ in range(100):
        assert limiter.acquire(HOST, METHOD, timeout=0)
        clock.advance(0.2)  # 5/s: nunca toca la ventana de 1 s
    assert windows(limiter)[1] == (100, 120, 100)
    assert not limiter.acquire(HOST, METHOD, timeout=5)
    assert limiter.acquire(HOST, METHOD)  # espera (simulada) a que caduque la primera
    assert clock.now == pytest.approx(1000.0 + 120 + MARGIN, abs=1e-3)


def test_low_priority_keeps_a_reserve_of_the_long_window():
    limiter, clock = make_limiter()  # reserve_frac = 0.15 -> 15 de 100 reservadas
    for _ in range(85):
        assert limiter.acquire(HOST, METHOD, priority=Priority.LOW, timeout=0)
        clock.advance(0.2)
    assert not limiter.acquire(HOST, METHOD, priority=Priority.LOW, timeout=10)
    # Las peticiones interactivas (HIGH) sí pueden usar la reserva...
    for _ in range(15):
        assert limiter.acquire(HOST, METHOD, priority=Priority.HIGH, timeout=0)
        clock.advance(0.2)
    # ...hasta el límite real.
    assert not limiter.acquire(HOST, METHOD, priority=Priority.HIGH, timeout=10)


def test_my_games_go_ahead_of_the_crawler():
    """Con el recolector (LOW) a tope, el seguimiento de tus partidas (NORMAL) sigue teniendo cupo."""
    limiter, clock = make_limiter()  # 100/120 s: LOW hasta 85, NORMAL hasta 95, HIGH hasta 100
    for _ in range(85):
        assert limiter.acquire(HOST, METHOD, priority=Priority.LOW, timeout=0)
        clock.advance(0.2)
    assert not limiter.acquire(HOST, METHOD, priority=Priority.LOW, timeout=10)
    for _ in range(10):
        assert limiter.acquire(HOST, METHOD, priority=Priority.NORMAL, timeout=0)
        clock.advance(0.2)
    assert not limiter.acquire(HOST, METHOD, priority=Priority.NORMAL, timeout=10)
    for _ in range(5):  # lo interactivo conserva su parte
        assert limiter.acquire(HOST, METHOD, priority=Priority.HIGH, timeout=0)
        clock.advance(0.2)
    assert not limiter.acquire(HOST, METHOD, priority=Priority.HIGH, timeout=10)


def test_acquire_returns_false_when_stopped_while_waiting():
    limiter, clock = make_limiter()
    for _ in range(19):
        limiter.acquire(HOST, METHOD)
    stop = threading.Event()
    limiter._cond.on_wait = stop.set
    assert limiter.acquire(HOST, METHOD, stop=stop) is False
    assert limiter.snapshot()[HOST]["requests"] == 19


# ---------------------------------------------------------------- update
def test_update_adapts_app_limits_from_headers():
    limiter, clock = make_limiter()
    for _ in range(3):
        limiter.acquire(HOST, METHOD)
    limiter.update(HOST, METHOD, 200, {"X-App-Rate-Limit": "500:10,30000:600", "X-App-Rate-Limit-Count": "3:10,3:600"})
    assert windows(limiter) == [(500, 10, 3), (30000, 600, 3)]  # conserva el historial
    granted = sum(limiter.acquire(HOST, METHOD, timeout=0) for _ in range(400))
    assert granted == 400  # ya no hay tope de 19/s


def test_update_with_same_limits_keeps_the_buckets():
    limiter, clock = make_limiter()
    limiter.acquire(HOST, METHOD)
    bucket = limiter._app_bucket(HOST)
    before = bucket.windows
    limiter.update(HOST, METHOD, 200, {"X-App-Rate-Limit": "100:120,20:1"})
    assert bucket.windows is before
    assert bucket.set_limits([(20, 1), (100, 120)]) is False


def test_update_syncs_counts_from_server():
    limiter, clock = make_limiter()
    limiter.acquire(HOST, METHOD)
    limiter.update(HOST, METHOD, 200, {"X-App-Rate-Limit": "20:1,100:120", "X-App-Rate-Limit-Count": "1:1,90:120"})
    assert windows(limiter) == [(20, 1, 1), (100, 120, 90)]
    granted = 0
    for _ in range(20):
        granted += limiter.acquire(HOST, METHOD, timeout=0)
        clock.advance(0.2)
    assert granted == 10  # Riot ya había contado 90 de 100


def test_sync_counts_tolerates_one_request_in_flight():
    limiter, clock = make_limiter()
    for _ in range(5):
        limiter.acquire(HOST, METHOD)
    limiter.update(HOST, METHOD, 200, {"X-App-Rate-Limit-Count": "6:1,6:120"})
    assert [used for *_, used in windows(limiter)] == [5, 5]
    limiter.update(HOST, METHOD, 200, {"X-App-Rate-Limit-Count": "9:1,9:120"})
    assert [used for *_, used in windows(limiter)] == [9, 9]


def test_method_limits_from_headers():
    limiter, clock = make_limiter()
    assert limiter.acquire(HOST, "match-v5.ids")
    limiter.update(HOST, "match-v5.ids", 200, {"X-Method-Rate-Limit": "2:10", "X-Method-Rate-Limit-Count": "1:10"})
    assert limiter.acquire(HOST, "match-v5.ids", timeout=0)
    assert not limiter.acquire(HOST, "match-v5.ids", timeout=0)  # límite de método agotado
    assert limiter.acquire(HOST, "league-v4.entries", timeout=0)  # otros métodos siguen libres


def test_429_with_retry_after_blocks_the_whole_host():
    limiter, clock = make_limiter()
    applied = limiter.update(HOST, METHOD, 429, {"Retry-After": "7", "X-Rate-Limit-Type": "application"})
    assert applied == 7.0
    snap = limiter.snapshot()[HOST]
    assert snap["rate_limited"] == 1
    assert 7.0 <= snap["blocked_for"] <= 7.5
    assert not limiter.acquire(HOST, "league-v4.entries", timeout=5)  # cualquier método del host
    assert limiter.acquire("europe", METHOD, timeout=0)  # otro host, libre
    start = clock.now
    assert limiter.acquire(HOST, "league-v4.entries")
    assert clock.now - start >= 7.0


def test_429_method_type_blocks_only_that_method():
    limiter, clock = make_limiter()
    limiter.update(HOST, METHOD, 429, {"Retry-After": "3", "X-Rate-Limit-Type": "method"})
    assert not limiter.acquire(HOST, METHOD, timeout=1)
    assert limiter.acquire(HOST, "match-v5.ids", timeout=0)
    clock.advance(3.3)
    assert limiter.acquire(HOST, METHOD, timeout=0)


@pytest.mark.parametrize("kind, expected", [("service", 2.0), ("method", 5.0), ("", 5.0)])
def test_429_without_retry_after_uses_default_backoff(kind, expected):
    limiter, clock = make_limiter()
    headers = {"X-Rate-Limit-Type": kind} if kind else {}
    assert limiter.update(HOST, METHOD, 429, headers) == expected
    assert not limiter.acquire(HOST, METHOD, timeout=expected - 0.5)
