"""Reglas de macro de las guías (``macro/``) que se pueden medir con las estadísticas de Riot de cada partida.

No son errores sueltos sino hábitos que se comparan con cada elo (como los roams de ``route_macro``). Medido el
24-11-2026 con ~28.000 partidas de ``data/otps`` y ``data/partidas_campeon`` (Platino o Esmeralda → Aspirante):

- Pink en cada vuelta a base (vueltas sin morir con un guardián de control): top 3 % → 14 %, jungla 14 % → 31 %,
  ADC (Diamante → Aspirante) 7 % → 23 %.
- Wards rivales destruidos cada 10 min: top 0,5 → 0,9, jungla 0,8 → 1,6, ADC 1,1 → 2,2.
- Ventaja de visión sobre tu rival de línea: top 7 % → 15 %, jungla −7 % → 19 %, ADC 18 % → 43 %.
- Kills en otras líneas al principio (roams y TP que salen bien): top 0,04 → 0,13 por partida.
- Torres de tu equipo antes del 14 en las que participas: top 0,15 → 0,28, jungla 0,01 → 0,05, ADC 0,18 → 0,41.
- Teletransportes por partida (top y mid que lo llevan): top 3,9 (Diamante) → 4,5 (Aspirante).

Medido y sin diferencia (se enseña igual, sin flecha): volver a base con menos de 400 de oro que gastar antes del 20
(«no vuelvas si no te da para nada útil»): 6-10 % de las vueltas en todos los elos de top y jungla.

Funciona igual con tus partidas (``raw_fields`` del participante de Riot) y con los dossiers (``from_dossier``).
"""

from __future__ import annotations

TELEPORT = 12
CONTROL_WARD = 2055
CHEAP_RECALL_GOLD = 400  # comprar menos de esto en una vuelta a base (sin haber muerto)
CHEAP_RECALL_UNTIL_S = 20 * 60
MIN_MINUTES = 15

CHALLENGES = (
    "visionScoreAdvantageLaneOpponent", "killsOnOtherLanesEarlyJungleAsLaner", "kTurretsDestroyedBeforePlatesFall",
)
RIOT = ("wardsKilled", "visionWardsBoughtInGame")

# (clave, etiqueta, roles, unidad): lo que enseña «Mi progreso» junto a los roams.
ROWS = [
    ("rl_pink_back_pct", "Vueltas a base en las que compras una pink", ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT"), "%"),
    ("rl_wards_killed10", "Wards rivales que destruyes cada 10 min", ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT"), ""),
    ("rl_vision_adv", "Ventaja de visión sobre tu rival directo", ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT"), "%"),
    ("rl_roam_kills", "Kills en otras líneas al principio (roams y TP)", ("TOP", "MID", "ADC", "SUPPORT"), ""),
    ("rl_towers_pre14", "Torres antes del 14 en las que participas", ("TOP", "JUNGLE", "MID", "ADC"), ""),
    ("rl_tp_casts", "Teletransportes usados (si lo llevas)", ("TOP", "MID"), ""),
    ("rl_cheap_recall_pct", "Vueltas a base antes del 20 sin oro para nada útil (< 400)", ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT"), "%"),
]
KEYS = tuple(r[0] for r in ROWS)


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def raw_fields(participant: dict) -> dict:
    """Lo que hace falta del participante de Riot (``info.participants[i]``), en pequeño para guardarlo en el análisis."""
    ch = participant.get("challenges") or {}
    spells = [participant.get("summoner1Id"), participant.get("summoner2Id")]
    tp = None
    for i, s in enumerate(spells):
        if s == TELEPORT:
            tp = participant.get(f"summoner{i + 1}Casts")
    return {
        "ch": {k: ch[k] for k in CHALLENGES if k in ch},
        "riot": {k: participant.get(k) for k in RIOT if k in participant},
        "tp_casts": tp,
    }


def metrics(raw: dict | None, backs: list[dict] | None, minutes: float | None, role: str | None) -> dict:
    """Las métricas ``rl_*`` de una partida (vacío si es demasiado corta o no hay datos)."""
    if not raw or not minutes or minutes < MIN_MINUTES:
        return {}
    ch, riot = raw.get("ch") or {}, raw.get("riot") or {}
    out: dict = {}
    if (v := _num(riot.get("wardsKilled"))) is not None:
        out["rl_wards_killed10"] = round(10 * v / minutes, 2)
    if (v := _num(ch.get("visionScoreAdvantageLaneOpponent"))) is not None:
        out["rl_vision_adv"] = round(100 * v, 1)
    if role != "JUNGLE" and (v := _num(ch.get("killsOnOtherLanesEarlyJungleAsLaner"))) is not None:
        out["rl_roam_kills"] = v
    if role != "SUPPORT" and (v := _num(ch.get("kTurretsDestroyedBeforePlatesFall"))) is not None:
        out["rl_towers_pre14"] = v
    if role in ("TOP", "MID") and (v := _num(raw.get("tp_casts"))) is not None:
        out["rl_tp_casts"] = v
    alive = [b for b in backs or [] if (b.get("t") or 0) > 60 and not b.get("dead")]
    if alive:
        out["rl_pink_back_pct"] = round(100 * sum(1 for b in alive if CONTROL_WARD in (b.get("items") or [])) / len(alive), 1)
    early = [b for b in alive if b["t"] < CHEAP_RECALL_UNTIL_S]
    if early:
        out["rl_cheap_recall_pct"] = round(100 * sum(1 for b in early if (b.get("gold") or 0) < CHEAP_RECALL_GOLD) / len(early), 1)
    return out


def from_analysis(analysis: dict) -> dict:
    """Desde un análisis guardado (tus partidas): necesita ``riot_rules`` (las nuevas lo traen; ver ``tracker``)."""
    m = analysis.get("metrics") or {}
    return metrics(analysis.get("riot_rules"), (analysis.get("build") or {}).get("backs"), m.get("minutes"), analysis.get("role"))


def from_dossier(d: dict) -> dict:
    """Desde el dossier de un OTP (``data/otps``): sus retos y estadísticas de Riot y sus vueltas a base."""
    a = d.get("analysis") or {}
    rs = d.get("riot_stats") or {}
    spells = d.get("spells") or []
    tp = None
    for i, s in enumerate(spells[:2]):
        if s == TELEPORT:
            tp = rs.get(f"summoner{i + 1}Casts")
    ch = d.get("challenges") or {}
    raw = {"ch": {k: ch[k] for k in CHALLENGES if k in ch}, "riot": {k: rs[k] for k in RIOT if k in rs}, "tp_casts": tp}
    return metrics(raw, (a.get("build") or {}).get("backs"), (d.get("duration") or 0) / 60, d.get("role") or a.get("role"))
