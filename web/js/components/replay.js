// Reproductor de la «repetición 2D» con tus errores encima.
//
// Se reconstruye con el timeline de Riot: posición exacta de los 10 jugadores
// cada minuto y en cada evento (kills, objetivos…); entre medias se interpola.
// Encima se pintan tus momentos clave (errores y aciertos) con su gravedad, un
// globo en el mapa cuando ocurren y los campeones implicados resaltados.
import { dd } from "../state.js";
import { champImg, champText, fmt, h, icon } from "../ui.js";
import { minimap, OBJ_ICON, pct, trackPos } from "./minimap.js";

const NS = "http://www.w3.org/2000/svg";
export const SEV_LABEL = { high: "Grave", medium: "A mejorar", low: "Leve", good: "Bien hecho", info: "Informativo" };
const OBJ_NAME = { dragon: "Dragón", elder: "Dragón ancestral", baron: "Barón", herald: "Heraldo", grubs: "Larvas", atakhan: "Atakhan" };
const SPEEDS = [1, 2, 5, 10, 20, 40];
const CALLOUT_BEFORE = 8; // s antes del momento en que aparece el globo
const CALLOUT_AFTER = 6;
const TRAIL_S = 45;

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
    const p = players[pid] || {};
    const el = h(
      `div.map-champ.${p.team === 100 ? "blue" : "red"}${Number(pid) === me ? ".me" : ""}`,
      { title: `${dd.name(p.champ)}${p.name ? ` · ${p.name}` : ""}` },
      champImg(p.champ),
    );
    icons[pid] = el;
    champsLayer.append(el);
  }
  const trailSvg = svgEl("svg", { class: "map-svg", viewBox: "0 0 100 100", preserveAspectRatio: "none" });
  const trail = svgEl("polyline", { class: "replay-trail", points: "" });
  trailSvg.append(trail);
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
    for (const [pid, el] of Object.entries(icons)) {
      const pos = trackPos(r.tracks[pid], t);
      if (!pos || pos.x === null || pos.x === undefined) {
        el.style.display = "none";
        continue;
      }
      el.style.display = "";
      const p = pct(pos.x, pos.y);
      el.style.left = `${p.left}%`;
      el.style.top = `${p.top}%`;
      el.classList.toggle("dead", !!pos.dead);
      el.classList.toggle("estimated", !pos.dead && !pos.sure);
      el.classList.toggle("focus", focus.has(Number(pid)));
    }
    // Rastro de tus últimos segundos.
    const pts = [];
    for (let s = Math.max(0, t - TRAIL_S); s <= t; s += 3) {
      const pos = trackPos(r.tracks[String(me)], s);
      if (pos && !pos.dead && pos.x !== null && pos.x !== undefined) {
        const p = pct(pos.x, pos.y);
        pts.push(`${p.left.toFixed(2)},${p.top.toFixed(2)}`);
      }
    }
    trail.setAttribute("points", pts.join(" "));

    const recent = [];
    for (const [time, killer, victim, x, y] of r.kills) {
      if (x === null || time > t || t - time > 10) continue;
      const p = pct(x, y);
      const mine = victim === me ? "my-death" : killer === me ? "my-kill" : "";
      recent.push(h(`span.map-fx.kill${mine ? "." + mine : ""}`, { style: { left: `${p.left}%`, top: `${p.top}%`, opacity: String(1 - (t - time) / 10) }, title: `${dd.name(players[killer]?.champ) || "torre/súbditos"} → ${dd.name(players[victim]?.champ)}` }, "✕"));
    }
    for (const [time, kind, team, x, y] of r.objectives) {
      if (time > t || t - time > 25) continue;
      const p = pct(x, y);
      recent.push(h(`span.map-fx.obj.${team === 100 ? "blue" : "red"}`, { style: { left: `${p.left}%`, top: `${p.top}%`, opacity: String(1 - (t - time) / 25) }, title: OBJ_NAME[kind] || kind }, OBJ_ICON[kind] || "★"));
    }
    for (const [time, owner, kind, lane, x, y] of r.buildings) {
      if (x === null || time > t) continue;
      const p = pct(x, y);
      recent.push(h(`span.map-fx.building.${owner === 100 ? "blue" : "red"}`, { style: { left: `${p.left}%`, top: `${p.top}%` }, title: `${kind === "tower" ? "Torre" : "Inhibidor"} destruido (${lane})` }));
    }
    fx.replaceChildren(...recent);

    // Globos de los momentos cercanos.
    const callouts = [];
    let active = null;
    for (const m of moments) {
      const near = t >= m.t - CALLOUT_BEFORE && t <= m.t + CALLOUT_AFTER;
      if (!near && m !== selected) continue;
      if (m === selected && !(t >= m.t - 12 && t <= m.t + 10)) continue;
      if (t >= m.t - 1 && t <= m.t + CALLOUT_AFTER) active = active && active.severity === "high" ? active : m;
      if (m.x === null || m.x === undefined) continue;
      const p = pct(m.x, m.y);
      const reached = t >= m.t;
      callouts.push(
        h(
          `div.map-callout.sev-${m.severity}${m === selected ? ".selected" : ""}${reached ? ".reached" : ""}`,
          { style: { left: `${p.left}%`, top: `${p.top}%` }, onclick: () => select(m), title: champText(m.title) },
          h("span.callout-ring"),
          h("span.callout-label", fmt.duration(m.t), " · ", SEV_LABEL[m.severity] || ""),
        ),
      );
    }
    calloutLayer.replaceChildren(...callouts);
    if (active) {
      banner.className = `replay-banner sev-${active.severity}`;
      banner.replaceChildren(h("b", SEV_LABEL[active.severity] || ""), " · ", champText(active.title));
    } else {
      banner.className = "replay-banner hidden";
    }

    // HUD
    let blue = 0;
    let red = 0;
    for (const [time, , victim] of r.kills) {
      if (time > t) break;
      if (players[victim]?.team === 100) red++;
      else if (players[victim]?.team === 200) blue++;
    }
    const mineBlue = a.team === 100;
    hud.score.replaceChildren(h("span.blue-text", String(mineBlue ? blue : red)), " – ", h("span.red-text", String(mineBlue ? red : blue)));
    hud.score.title = "Tu equipo – rival (kills hasta este momento)";
    const diff = seriesAt(a.series, "team_gold_diff", t);
    hud.gold.textContent = diff === null ? "—" : fmt.signed(Math.round(diff / 100) / 10, 1, " k");
    hud.gold.className = diff > 500 ? "good" : diff < -500 ? "bad" : "";
    const level = seriesStep(a.series, "level", t);
    hud.level.textContent = level ?? "—";
    const cs = seriesStep(a.series, "cs", t);
    hud.cs.textContent = cs ?? "—";
    const cur = seriesStep(a.series, "current_gold", t);
    hud.current.textContent = cur === null || cur === undefined ? "—" : fmt.int(cur);

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
      document.removeEventListener("keydown", onKey);
    },
  };
}
