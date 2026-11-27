// Reproductor de la «repetición 2D» con tus errores encima.
//
// Se reconstruye con el timeline de Riot: posición exacta de los 10 jugadores
// cada minuto y en cada evento (kills, objetivos…); entre medias se interpola.
// Encima se pintan tus momentos clave (errores y aciertos) con su gravedad, un
// globo en el mapa cuando ocurren y los campeones implicados resaltados.
import { dd } from "../state.js";
import { champText, fmt, h, icon } from "../ui.js";
import { buildingEffects, champIcon, killEffects, minimap, objectiveEffects, pct, placeIcons, trackPos } from "./minimap.js";

const NS = "http://www.w3.org/2000/svg";
export const SEV_LABEL = { high: "Grave", medium: "A mejorar", low: "Leve", good: "Bien hecho", info: "Informativo" };
const OBJ_NAME = { dragon: "Dragón", elder: "Dragón ancestral", baron: "Barón", herald: "Heraldo", grubs: "Larvas", atakhan: "Atakhan" };
const SPEEDS = [1, 2, 5, 10, 20, 40];
const CALLOUT_BEFORE = 8; // s antes del momento en que aparece el globo
const CALLOUT_AFTER = 6;
const TRAIL_FADE_MS = 2000; // la estela se queda donde se pinta y se apaga del todo en 2 s (reales)
const TRAIL_JUMP = 3000; // más que esto entre dos puntos seguidos es un salto (vuelta a base, teletransporte): se corta

function svgEl(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, v);
  return node;
}

/** Valor de una serie por minuto en el instante t (s), interpolado. */
function seriesAt(series, key, t) {
  const values = series?.[key];
  const minutes = series?.minute;
  if (!values?.length || !minutes?.length) return null;
  const m = t / 60;
  let i = 0;
  while (i < minutes.length - 1 && minutes[i + 1] <= m) i++;
  const a = values[i];
  const b = values[Math.min(i + 1, values.length - 1)];
  if (a === null || a === undefined) return b ?? null;
  if (b === null || b === undefined || i + 1 >= minutes.length) return a;
  const span = minutes[i + 1] - minutes[i] || 1;
  const f = Math.max(0, Math.min(1, (m - minutes[i]) / span));
  return a + (b - a) * f;
}

/** Valor «escalonado» (el del último minuto cerrado): nivel, CS, oro actual. */
function seriesStep(series, key, t) {
  const values = series?.[key];
  const minutes = series?.minute;
  if (!values?.length || !minutes?.length) return null;
  const m = t / 60;
  let i = 0;
  while (i < minutes.length - 1 && minutes[i + 1] <= m) i++;
  return values[i];
}

/**
 * Puntos por los que pasas entre los segundos `from` y `to`: los del recorrido (ya rodean los muros) y donde estás en
 * `to`. `null` marca un corte: muerto o un salto (vuelta a base, teletransporte), para no cruzar el mapa en línea recta.
 */
function trailSteps(track, from, to) {
  const out = [];
  for (const a of track?.a || []) {
    if (a[0] <= from) continue;
    if (a[0] >= to) break;
    out.push(trackPos(track, a[0]));
  }
  out.push(trackPos(track, to));
  return out.map((pos) => (pos && !pos.dead && pos.x !== null && pos.x !== undefined ? { x: pos.x, y: pos.y } : null));
}

/** ¿Se ve el globo del momento m en el segundo t? (el seleccionado, con una ventana algo más amplia) */
function calloutVisible(m, t, selected) {
  if (m === selected) return t >= m.t - 12 && t <= m.t + 10;
  return t >= m.t - CALLOUT_BEFORE && t <= m.t + CALLOUT_AFTER;
}

/** El momento del aviso de arriba: el que está pasando en t; si ya había uno grave, se queda ese. */
function bannerMoment(active, m, t) {
  if (t < m.t - 1 || t > m.t + CALLOUT_AFTER) return active;
  return active?.severity === "high" ? active : m;
}

/** Globo de un momento en el mapa: marcado si está seleccionado y lleno cuando ya ha pasado. */
function calloutEl(m, t, selected, onSelect) {
  const p = pct(m.x, m.y);
  const sel = m === selected ? ".selected" : "";
  const reached = t >= m.t ? ".reached" : "";
  return h(
    `div.map-callout.sev-${m.severity}${sel}${reached}`,
    { style: { left: `${p.left}%`, top: `${p.top}%` }, onclick: () => onSelect(m), title: champText(m.title) },
    h("span.callout-ring"),
    h("span.callout-label", fmt.duration(m.t), " · ", SEV_LABEL[m.severity] || ""),
  );
}

/** Globos de los momentos cercanos a t y el momento que va en el aviso de arriba. */
function nearCallouts(moments, t, selected, onSelect) {
  const callouts = [];
  let active = null;
  for (const m of moments) {
    if (!calloutVisible(m, t, selected)) continue;
    active = bannerMoment(active, m, t);
    if (m.x === null || m.x === undefined) continue;
    callouts.push(calloutEl(m, t, selected, onSelect));
  }
  return { callouts, active };
}

/** Aviso de arriba con el momento que está pasando (o escondido si no hay ninguno). */
function showBanner(banner, active) {
  if (!active) {
    banner.className = "replay-banner hidden";
    return;
  }
  banner.className = `replay-banner sev-${active.severity}`;
  banner.replaceChildren(h("b", SEV_LABEL[active.severity] || ""), " · ", champText(active.title));
}

/** Kills de cada equipo hasta el segundo t: [azul, rojo]. */
function scoreAt(kills, players, t) {
  let blue = 0;
  let red = 0;
  for (const [time, , victim] of kills) {
    if (time > t) break;
    if (players[victim]?.team === 100) red++;
    else if (players[victim]?.team === 200) blue++;
  }
  return [blue, red];
}

/** Color de la diferencia de oro: a favor o en contra si pasa de 500. */
function goldClass(diff) {
  if (diff > 500) return "good";
  return diff < -500 ? "bad" : "";
}

/** Marcador, diferencia de oro y tus números (nivel, CS, oro sin gastar) en el segundo t. */
function updateHud(hud, a, players, t) {
  const [blue, red] = scoreAt(a.replay.kills, players, t);
  const mineBlue = a.team === 100;
  hud.score.replaceChildren(h("span.blue-text", String(mineBlue ? blue : red)), " – ", h("span.red-text", String(mineBlue ? red : blue)));
  hud.score.title = "Tu equipo – rival (kills hasta este momento)";
  const diff = seriesAt(a.series, "team_gold_diff", t);
  hud.gold.textContent = diff === null ? "—" : fmt.signed(Math.round(diff / 100) / 10, 1, " k");
  hud.gold.className = goldClass(diff);
  const level = seriesStep(a.series, "level", t);
  hud.level.textContent = level ?? "—";
  const cs = seriesStep(a.series, "cs", t);
  hud.cs.textContent = cs ?? "—";
  const cur = seriesStep(a.series, "current_gold", t);
  hud.current.textContent = cur === null || cur === undefined ? "—" : fmt.int(cur);
}

/**
 * a = análisis de /api/coach/games/{id}.
 * opts.moments: momentos visibles (filtrados); opts.onTime(t); opts.onSelect(moment)
 * Devuelve { el, seek, play, pause, select, setMoments, setAutoPause, destroy, time() }.
 */
export function createReplay(a, opts = {}) {
  const r = a.replay;
  const duration = Math.max(1, a.duration || 1);
  const players = Object.fromEntries((a.players || []).map((p) => [p.pid, p]));
  const me = a.me;
  const deathsByT = new Map((a.deaths || []).map((d) => [Math.round(d.t), d]));
  let moments = opts.moments || a.moments || [];
  let selected = null;
  let autoPause = opts.autoPause ?? true;

  // ------------------------------------------------------------- mapa
  const icons = {};
  const champsLayer = h("div.map-layer");
  for (const pid of Object.keys(r.tracks)) {
    const el = champIcon(pid, players[pid] || {}, me);
    icons[pid] = el;
    champsLayer.append(el);
  }
  const trailSvg = svgEl("svg", { class: "map-svg", viewBox: "0 0 100 100", preserveAspectRatio: "none" });
  const trail = svgEl("g", { class: "replay-trail" });
  trailSvg.append(trail);
  // Estela: tramos fijos (no se recalculan al avanzar) que se apagan solos en TRAIL_FADE_MS.
  let trailSegs = []; // [{ el, at }]
  let trailLast = null; // último punto pintado (coordenadas de la partida) o null tras un corte
  let trailT = null; // segundo de la partida hasta el que está pintada
  let fadeRaf = 0;
  const clearTrail = () => {
    trailSegs = [];
    trail.replaceChildren();
    trailLast = null;
  };
  const fadeTrail = () => {
    fadeRaf = 0;
    const now = performance.now();
    trailSegs = trailSegs.filter((seg) => {
      const left = 1 - (now - seg.at) / TRAIL_FADE_MS;
      if (left <= 0) {
        seg.el.remove();
        return false;
      }
      seg.el.setAttribute("stroke-opacity", left.toFixed(3));
      return true;
    });
    if (trailSegs.length) fadeRaf = requestAnimationFrame(fadeTrail);
  };
  const extendTrail = (from, to) => {
    const now = performance.now();
    for (const q of trailSteps(r.tracks[String(me)], from, to)) {
      if (q && trailLast && Math.hypot(q.x - trailLast.x, q.y - trailLast.y) <= TRAIL_JUMP) {
        const a = pct(trailLast.x, trailLast.y);
        const b = pct(q.x, q.y);
        const seg = svgEl("line", { x1: a.left.toFixed(2), y1: a.top.toFixed(2), x2: b.left.toFixed(2), y2: b.top.toFixed(2) });
        trail.append(seg);
        trailSegs.push({ el: seg, at: now });
      }
      trailLast = q;
    }
    if (!fadeRaf && trailSegs.length) fadeRaf = requestAnimationFrame(fadeTrail);
  };
  /** Al avanzar reproduciendo se alarga la estela; al saltar a otro momento se borra (no une sitios lejanos). */
  const updateTrail = () => {
    if (trailT === t) return;
    if (playing && trailT !== null && t > trailT && t - trailT <= 60) extendTrail(trailT, t);
    else clearTrail();
    trailT = t;
  };
  const fx = h("div.map-layer");
  const calloutLayer = h("div.map-layer.callouts");
  const map = minimap(trailSvg, fx, champsLayer, calloutLayer);
  map.classList.add("replay-map");
  const banner = h("div.replay-banner.hidden");
  map.append(banner);

  // ------------------------------------------------------------- barra
  let t = 0;
  let playing = false;
  let speed = 10;
  let last = 0;
  let raf = 0;
  const clock = h("span.replay-clock", "0:00");
  const slider = h("input.replay-slider", { type: "range", min: 0, max: duration, step: 1, value: 0, "aria-label": "Momento de la partida" });
  const progress = h("div.replay-progress");
  const playBtn = h("button.btn.btn-sm.btn-primary", { type: "button", title: "Reproducir / pausar (espacio)" }, icon("play"));
  const prevBtn = h("button.btn.btn-sm", { type: "button", title: "Error anterior (P)" }, icon("skipPrev"));
  const nextBtn = h("button.btn.btn-sm", { type: "button", title: "Siguiente error (N)" }, icon("skipNext"));
  const speedSel = h(
    "select.select.select-sm",
    { title: "Velocidad", "aria-label": "Velocidad", onchange: (e) => (speed = Number(e.target.value)) },
    SPEEDS.map((v) => h("option", { value: v, selected: v === speed }, `×${v}`)),
  );
  const autoBox = h("input", { type: "checkbox", checked: autoPause });
  autoBox.addEventListener("change", () => (autoPause = autoBox.checked));

  const marks = h("div.replay-marks");
  const drawMarks = () => {
    const out = [];
    const mark = (time, cls, title, onClick) =>
      out.push(h(`span.replay-mark.${cls}`, { title: `${fmt.duration(time)} · ${title}`, style: { left: `${(time / duration) * 100}%` }, onclick: onClick }));
    for (const [time, killer, victim] of r.kills) {
      if (killer === me) mark(time, "kill", `Matas a ${dd.name(players[victim]?.champ)}`, () => seek(time - 5));
    }
    for (const [time, kind, team] of r.objectives) {
      mark(time, `obj.${team === a.team ? "ally" : "enemy"}`, `${OBJ_NAME[kind] || kind} (${team === a.team ? "tu equipo" : "rival"})`, () => seek(time - 8));
    }
    for (const m of moments) {
      mark(m.t, `moment.sev-${m.severity}${selected === m ? ".selected" : ""}`, `${SEV_LABEL[m.severity] || ""}: ${champText(m.title)}`, () => select(m));
    }
    marks.replaceChildren(...out);
  };

  // ------------------------------------------------------------- HUD
  const hud = {
    score: h("b"),
    gold: h("b"),
    level: h("b"),
    cs: h("b"),
    current: h("b"),
  };
  const hudEl = h(
    "div.replay-hud",
    h("div", h("span.tiny.muted", "Marcador"), hud.score),
    h("div", h("span.tiny.muted", "Oro de equipo"), hud.gold),
    h("div", h("span.tiny.muted", "Tu nivel"), hud.level),
    h("div", h("span.tiny.muted", "Tu CS"), hud.cs),
    h("div", h("span.tiny.muted", "Oro sin gastar"), hud.current),
  );

  // ------------------------------------------------------------- dibujo
  const focus = new Set();
  const draw = () => {
    placeIcons(icons, r.tracks, t, focus);
    updateTrail();
    fx.replaceChildren(
      ...killEffects(r.kills, t, players, me, "torre/súbditos"),
      ...objectiveEffects(r.objectives, t, (kind) => OBJ_NAME[kind] || kind),
      ...buildingEffects(r.buildings, t),
    );
    const { callouts, active } = nearCallouts(moments, t, selected, (m) => select(m));
    calloutLayer.replaceChildren(...callouts);
    showBanner(banner, active);
    updateHud(hud, a, players, t);

    clock.textContent = `${fmt.duration(Math.max(1, t))} / ${fmt.duration(duration)}`;
    slider.value = String(Math.round(t));
    progress.style.width = `${(t / duration) * 100}%`;
    opts.onTime?.(t);
  };

  // ------------------------------------------------------------- control
  const loop = (now) => {
    if (!playing) return;
    const dt = last ? (now - last) / 1000 : 0;
    last = now;
    const before = t;
    t = Math.min(duration, t + dt * speed);
    if (autoPause) {
      const crossed = moments.find((m) => m.t > before && m.t <= t && m !== selected && m.severity !== "info"); // lo informativo no para
      if (crossed) {
        t = crossed.t;
        draw();
        pause();
        select(crossed, { seekTo: false });
        return;
      }
    }
    draw();
    if (t >= duration) return pause();
    raf = requestAnimationFrame(loop);
  };
  const play = () => {
    if (t >= duration) t = 0;
    playing = true;
    last = 0;
    playBtn.replaceChildren(icon("pause"));
    raf = requestAnimationFrame(loop);
  };
  const pause = () => {
    playing = false;
    cancelAnimationFrame(raf);
    playBtn.replaceChildren(icon("play"));
  };
  const seek = (time) => {
    t = Math.max(0, Math.min(duration, time));
    draw();
  };
  const select = (m, { seekTo = true } = {}) => {
    selected = m;
    focus.clear();
    if (m?.kind === "death") {
      const d = deathsByT.get(Math.round(m.t));
      if (d) {
        (d.enemies || []).forEach((pid) => focus.add(pid));
        if (d.killer) focus.add(d.killer);
      }
      focus.add(me);
    }
    if (m && seekTo) {
      pause();
      t = Math.max(0, m.t - CALLOUT_BEFORE);
    }
    drawMarks();
    draw();
    opts.onSelect?.(m);
  };
  const step = (dir) => {
    const list = [...moments].sort((x, y) => x.t - y.t);
    const ref = selected ? selected.t : t;
    const target = dir > 0 ? list.find((m) => m.t > ref + 0.5) : [...list].reverse().find((m) => m.t < ref - 0.5);
    if (target) select(target);
  };
  playBtn.onclick = () => (playing ? pause() : play());
  prevBtn.onclick = () => step(-1);
  nextBtn.onclick = () => step(1);
  slider.addEventListener("input", () => {
    pause();
    seek(Number(slider.value));
  });

  const onKey = (e) => {
    if (!el.isConnected) return;
    const tag = document.activeElement?.tagName;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag) && document.activeElement !== slider) return;
    if (document.querySelector(".modal-backdrop")) return;
    if (e.key === " " || e.code === "Space") {
      e.preventDefault();
      playing ? pause() : play();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      seek(t + (e.shiftKey ? 30 : 5));
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      seek(t - (e.shiftKey ? 30 : 5));
    } else if (e.key === "n" || e.key === "N") {
      step(1);
    } else if (e.key === "p" || e.key === "P") {
      step(-1);
    }
  };
  document.addEventListener("keydown", onKey);

  const el = h(
    "div.replay",
    map,
    hudEl,
    h("div.replay-bar", h("div.replay-track", progress), marks, slider),
    h(
      "div.replay-controls",
      prevBtn,
      playBtn,
      nextBtn,
      clock,
      h("span.spacer"),
      h("label.check.tiny", { title: "Al reproducir, se para en cada error para que lo leas" }, autoBox, "Parar en cada error"),
      speedSel,
    ),
    h(
      "div.legend",
      h("span", h("i", { style: { background: "var(--blue)" } }), "Azul"),
      h("span", h("i", { style: { background: "var(--red)" } }), "Rojo"),
      h("span", h("i", { style: { background: "var(--gold)" } }), "Tú"),
      h("span", h("i.sev-dot.sev-high"), "Grave"),
      h("span", h("i.sev-dot.sev-medium"), "A mejorar"),
      h("span", h("i.sev-dot.sev-good"), "Bien hecho"),
      h(
        "span",
        { title: "Riot solo da la posición de cada jugador una vez por minuto y en cada kill, objetivo o torre. El resto se reconstruye con las compras (vuelta a base), las muertes, los campamentos de jungla (su CS) y rutas que rodean los muros; validado con tus partidas." },
        h("i.legend-estimated"),
        "Translúcido: posición reconstruida ⓘ",
      ),
      h("span.faint", "Teclas: espacio, ← →, N/P"),
    ),
  );
  drawMarks();
  seek(0);

  return {
    el,
    seek,
    play,
    pause,
    select,
    time: () => t,
    setMoments(list) {
      moments = list;
      if (selected && !moments.includes(selected)) {
        selected = null;
        focus.clear();
      }
      drawMarks();
      draw();
    },
    setAutoPause(v) {
      autoPause = v;
      autoBox.checked = v;
    },
    destroy() {
      pause();
      cancelAnimationFrame(fadeRaf);
      document.removeEventListener("keydown", onKey);
    },
  };
}
