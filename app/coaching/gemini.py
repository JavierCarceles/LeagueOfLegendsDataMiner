"""Generación de guías con Google Gemini (opcional: ``pip install google-genai``).

Novedad: el prompt incluye las **estadísticas reales** del matchup sacadas de
tu base de datos (winrate ajustado, ventaja de línea, CS a los 10, elo…), así
la guía se apoya en datos y no solo en el conocimiento general del modelo.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.logbus import get_logger
from app.riot.routing import ROLE_LABELS

log = get_logger("gemini")


class GuideError(Exception):
    pass


class GuideSchema(BaseModel):
    dificultad_matchup: int = Field(description="Dificultad del 1 (muy fácil) al 10 (muy difícil) para el campeón principal", default=5)
    guia: str = Field(description="Guía en español estructurada en DIFICULTAD, CLAVES DE DATOS, EARLY GAME, MID GAME y LATE GAME")


class VideoSchema(BaseModel):
    rol: str = Field(description="Rol principal: Top, Jungle, Mid, ADC, Support o General")
    campeon_principal: str = Field(description="Nombre EXACTO del campeón del alumno/streamer (ej: 'Kha'Zix')")
    campeon_enemigo: str = Field(description="Nombre EXACTO del rival directo, o 'Desconocido'")
    dificultad_matchup: int = Field(description="Dificultad del 1 al 10 para el campeón principal", default=5)
    guia: str = Field(description="Guía en español estructurada en EARLY GAME, MID GAME y LATE GAME")


def sdk_available() -> bool:
    try:
        from google import genai  # noqa: F401
    except Exception:
        return False
    return True


def call_gemini(keys: list[str], models: list[str], prompt: str, schema: type[BaseModel]) -> tuple[dict, str]:
    """Prueba cada modelo con cada key. Devuelve (datos, modelo)."""
    if not keys:
        raise GuideError("No hay claves de Gemini en .env (GEMINI_KEY_1…)")
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        raise GuideError("Falta la librería google-genai (pip install -r requirements-coaching.txt)") from exc

    last_error = ""
    for model in models:
        for index, key in enumerate(keys, start=1):
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema),
                )
                return json.loads(response.text), model
            except Exception as exc:  # cuota, modelo retirado, red…
                text = str(exc)
                last_error = text[:200]
                if "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
                    log.warning("Gemini: cuota agotada en la key %d con %s, probando otra…", index, model)
                    continue
                if "404" in text or "not found" in text.lower():
                    log.warning("Gemini: el modelo %s no está disponible.", model)
                    break
                continue
    raise GuideError(f"Gemini no respondió: {last_error}")


def _stats_context(svc, detail: dict) -> str:
    dd = svc.dd
    pair = detail["pair"]
    lines = []
    if pair["games"]:
        lines.append(
            f"- {pair['games']} partidas en la base de datos. WR de {dd.name(detail['champ'])}: {pair['wr']}% "
            f"(ajustado {pair['wr_adj']}%, esperado por fuerza de campeones {pair['expected']}%, delta {pair['delta']:+} pp)."
        )
        lane = pair.get("lane")
        if lane:
            parts = []
            if lane.get("cs10_diff") is not None:
                parts.append(f"diferencia de CS a los 10 min {lane['cs10_diff']:+}")
            if lane.get("lane_win") is not None:
                parts.append(f"gana la fase de líneas con ventaja clara el {lane['lane_win']}% y la pierde el {lane['lane_loss']}%")
            if lane.get("solo_kills_for") is not None:
                parts.append(f"solo kills por partida {lane['solo_kills_for']} a favor / {lane['solo_kills_against']} en contra")
            if parts:
                lines.append("- Fase de líneas: " + "; ".join(parts) + ".")
        tiers = [t for t in detail["breakdown"].get("tier", []) if t["games"] >= 30][:4]
        if tiers:
            lines.append("- Por elo: " + ", ".join(f"{t['key']} {t['wr']}% ({t['games']})" for t in tiers) + ".")
    else:
        lines.append("- No hay partidas registradas de este enfrentamiento todavía.")
    a, b = detail["a"], detail["b"]
    lines.append(f"- WR general en el rol: {dd.name(detail['champ'])} {a['wr_adj']}%, {dd.name(detail['opp'])} {b['wr_adj']}%.")
    guide = detail.get("opgg_guide") or {}
    if guide.get("lane_advantage"):
        lines.append(f"- Según OP.GG gana la línea: {dd.name(guide['lane_advantage'])}; más solo kills: {dd.name(guide.get('solo_kill_advantage'))}.")
    if guide.get("tip"):
        lines.append(f"- Consejo de OP.GG: {guide['tip']}")
    return "\n".join(lines)


def generate_matchup_guide(svc, role: str, champ: int, opp: int, detail: dict) -> dict:
    dd = svc.dd
    main, enemy = dd.name(champ), dd.name(opp)
    prompt = f"""Eres un coach profesional de League of Legends (nivel Challenger).
Escribe una guía CLARA, CONCRETA y ACCIONABLE en ESPAÑOL para este enfrentamiento:

Campeón del jugador: {main}
Rival directo: {enemy}
Rol: {ROLE_LABELS.get(role, role)}

Datos reales de nuestra base de datos (úsalos y no los contradigas):
{_stats_context(svc, detail)}

Formato obligatorio del campo "guia":

DIFICULTAD: [nota del 1 al 10 y una línea de justificación coherente con los datos]

CLAVES DE DATOS:
- [2-3 conclusiones prácticas a partir de los datos anteriores]

EARLY GAME:
- [nivel 1-3, intercambios, control de oleada, peligro de gank, objetos iniciales y runas recomendadas]

MID GAME:
- [picos de poder, rotaciones, visión, objetivos]

LATE GAME:
- [rol en las peleas, condición de victoria, posicionamiento]
"""
    data, model = call_gemini(svc.settings.gemini_keys, svc.settings.gemini_models, prompt, GuideSchema)
    difficulty = data.get("dificultad_matchup", 5)
    guide = (data.get("guia") or "").strip() or "Sin contenido."
    content = (
        f"FUENTE: IA ({model}) con estadísticas de Tempu Drafter\n"
        f"MATCHUP: {main} vs {enemy}\n"
        f"ROL: {ROLE_LABELS.get(role, role)}\n"
        f"DIFICULTAD APROX: {difficulty}/10\n"
        f"{'=' * 50}\n\n{guide}\n"
    )
    path = svc.guides.save(role, champ, opp, content, ai=True)
    log.info("Guía IA generada: %s vs %s (%s).", main, enemy, ROLE_LABELS.get(role, role))
    return {"path": path, "content": content, "model": model}
