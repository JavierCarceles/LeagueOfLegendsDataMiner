// Minimapa de la Grieta: puntos (muertes), rutas (pathing de jungla) y una
// «repetición 2D» reconstruida con el timeline (posiciones minuto a minuto +
// eventos con coordenadas). Coordenadas del juego: (-120, -120) a (14870, 14980).
import { dd } from "../state.js";
import { champImg, fmt, h, icon } from "../ui.js";

const MIN_X = -120;
const MIN_Y = -120;
const MAX_X = 14870;
const MAX_Y = 14980;
const NS = "http://www.w3.org/2000/svg";

export const mapImage = () => `https://ddragon.leagueoflegends.com/cdn/${dd.version()}/img/map/map11.png`;

/** Posición en % dentro del minimapa (y invertida: la base azul queda abajo a la izquierda). */
export function pct(x, y) {
  return { left: ((x - MIN_X) / (MAX_X - MIN_X)) * 100, top: (1 - (y - MIN_Y) / (MAX_Y - MIN_Y)) * 100 };
}

/** Refleja una posición del lado rojo para verla «desde tu lado» (tu base abajo a la izquierda). */
export function mirror(x, y) {
  return [14800 - x, 14900 - y];
}

function svgEl(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, v);
  return node;
}

export function minimap(...children) {
  const el = h("div.minimap", h("img.minimap-bg", { src: mapImage(), alt: "", draggable: "false" }), ...children);
  el.querySelector("img").addEventListener("error", (e) => (e.target.style.display = "none"), { once: true });
  return el;
}

function dot(x, y, { cls = "", title = "", size = 10, onClick = null } = {}) {
  const p = pct(x, y);
  return h(`span.map-dot${cls ? "." + cls.split(" ").join(".") : ""}`, { title, style: { left: `${p.left}%`, top: `${p.top}%`, width: `${size}px`, height: `${size}px` }, onclick: onClick });
}

const DEATH_KIND = (tags) =>
  tags.includes("gank") || tags.includes("jungler") ? "gank" : tags.includes("enemy_jungle") ? "invade" : tags.includes("solo") ? "solo" : tags.includes("isolated") ? "isolated" : "fight";
export const DEATH_KINDS = {
  gank: "Gank / jungla rival",
  solo: "1 contra 1",
  invade: "En la jungla rival",
  isolated: "Aislado",
  fight: "En pelea",
};

/** Mapa de muertes agregadas (siempre desde tu lado). */
export function deathMap(points, { onPick = null } = {}) {
  const dots = points.map((d) => {
    const [x, y] = d.team === 200 ? mirror(d.x, d.y) : [d.x, d.y];
    const kind = DEATH_KIND(d.tags || []);
    return dot(x, y, {
      cls: `death ${kind}`,
      size: 11,
      title: `${fmt.duration(d.t)} · ${DEATH_KINDS[kind]}`,
      onClick: onPick ? () => onPick(d) : null,
    });
  });
  return minimap(h("div.map-layer", dots));
}

/** Rutas de jungla de varias partidas (minutos 1-8), desde tu lado. */
export function pathMap(paths) {
  const svg = svgEl("svg", { class: "map-svg", viewBox: "0 0 100 100", preserveAspectRatio: "none" });
  const labels = [];
  paths.forEach((g, i) => {
    const pts = (g.path || []).map((s) => (g.team === 200 ? mirror(s.x, s.y) : [s.x, s.y])).map(([x, y]) => pct(x, y));
    if (pts.length < 2) return;
    svg.append(
      svgEl("polyline", {
        points: pts.map((p) => `${p.left.toFixed(2)},${p.top.toFixed(2)}`).join(" "),
        class: `map-path ${g.win ? "win" : "loss"}`,
        style: `opacity:${Math.max(0.25, 0.9 - i * 0.06)}`,
      }),
    );
    if (i === 0) {
      pts.forEach((p, j) => labels.push(h("span.map-step", { style: { left: `${p.left}%`, top: `${p.top}%` } }, String((g.path[j] || {}).minute ?? j + 1))));
    }
  });
  return minimap(svg, h("div.map-layer", labels));
}

// ------------------------------------------------------------------ replay
const SURE_S = 6; // a menos de 6 s de una posición real (frame, evento, fuente o muerte): «exacta»

/** ¿Hay una posición real de Riot a menos de SURE_S segundos de t? (las demás son reconstruidas) */
function isSure(track, t) {
  if (!track._real) track._real = track.a.filter((a) => a[3] <= 3).map((a) => a[0]);
  const real = track._real;
  let lo = 0;
  let hi = real.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (real[mid] < t) lo = mid + 1;
    else hi = mid - 1;
  }
  return [real[lo], real[lo - 1]].some((v) => v !== undefined && Math.abs(v - t) <= SURE_S);
}

export function trackPos(track, t) {
  for (const [s, e, x, y] of track.dead) {
    if (s <= t && t < e) return { dead: true, x, y, sure: true };
  }
  const a = track.a;
  if (!a.length) return null;
  let lo = 0;
  let hi = a.length - 1;
  if (t <= a[0][0]) return { x: a[0][1], y: a[0][2], sure: true };
  if (t >= a[hi][0]) return { x: a[hi][1], y: a[hi][2], sure: isSure(track, t) };
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (a[mid][0] <= t) lo = mid;
    else hi = mid;
  }
  const p = a[lo];
  const n = a[hi];
  // 3: justo antes de morir; 4: se queda ahí y salta (vuelta a base con su canalización o teletransporte).
  const sure = isSure(track, t);
  if (p[3] === 3 || p[3] === 4) return { x: p[1], y: p[2], sure };
  const f = n[0] === p[0] ? 0 : (t - p[0]) / (n[0] - p[0]);
  return { x: p[1] + (n[1] - p[1]) * f, y: p[2] + (n[2] - p[2]) * f, sure };
}

export const OBJ_ICON = { dragon: "🐉", elder: "🐉", baron: "👾", herald: "🦀", grubs: "🪲", atakhan: "💀" };

/**
 * Repetición 2D: analysis = respuesta de /api/coach/games/{id}.
 * Devuelve { el, seek(t), pause() }.
 */
export function replay(analysis) {
  const r = analysis.replay;
  const duration = analysis.duration;
  const players = Object.fromEntries((analysis.players || []).map((p) => [p.pid, p]));
  const me = analysis.me;
  const icons = {};
  const layer = h("div.map-layer");
  const fx = h("div.map-layer");
  for (const pid of Object.keys(r.tracks)) {
    const p = players[pid] || {};
    const el = h(
      `div.map-champ.${p.team === 100 ? "blue" : "red"}${Number(pid) === me ? ".me" : ""}`,
      { title: `${dd.name(p.champ)}${p.name ? ` · ${p.name}` : ""}` },
      champImg(p.champ),
    );
    icons[pid] = el;
    layer.append(el);
  }
  const map = minimap(fx, layer);
  map.classList.add("replay-map");

  let t = 0;
  let playing = false;
  let speed = 20;
  let last = 0;
  const clock = h("span.replay-clock", "0:00");
  const slider = h("input.replay-slider", { type: "range", min: 0, max: duration, step: 1, value: 0 });
  const playBtn = h("button.btn.btn-sm", { type: "button", title: "Reproducir / pausar" }, icon("play"));
  const speedSel = h(
    "select.select.select-sm",
    { title: "Velocidad", onchange: (e) => (speed = Number(e.target.value)) },
    [10, 20, 40, 80].map((v) => h("option", { value: v, selected: v === speed }, `×${v}`)),
  );

  // Marcas sobre la barra: tus muertes, tus kills y objetivos.
  const marks = h("div.replay-marks");
  const mark = (time, cls, title) => marks.append(h(`span.replay-mark.${cls}`, { title: `${fmt.duration(time)} · ${title}`, style: { left: `${(time / duration) * 100}%` }, onclick: () => seek(time - 4) }));
  for (const [time, killer, victim] of r.kills) {
    if (victim === me) mark(time, "death", `Mueres (${dd.name(players[killer]?.champ) || "torre"})`);
    else if (killer === me) mark(time, "kill", `Matas a ${dd.name(players[victim]?.champ)}`);
  }
  for (const [time, kind, team] of r.objectives) mark(time, `obj.${team === analysis.team ? "ally" : "enemy"}`, `${kind} (${team === analysis.team ? "tu equipo" : "rival"})`);

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
    }
    const recent = [];
    for (const [time, killer, victim, x, y] of r.kills) {
      if (x === null || time > t || t - time > 10) continue;
      const p = pct(x, y);
      const mine = victim === me ? "my-death" : killer === me ? "my-kill" : "";
      recent.push(h(`span.map-fx.kill${mine ? "." + mine : ""}`, { style: { left: `${p.left}%`, top: `${p.top}%`, opacity: String(1 - (t - time) / 10) }, title: `${dd.name(players[killer]?.champ) || "?"} → ${dd.name(players[victim]?.champ)}` }, "✕"));
    }
    for (const [time, kind, team, x, y] of r.objectives) {
      if (time > t || t - time > 25) continue;
      const p = pct(x, y);
      recent.push(h(`span.map-fx.obj.${team === 100 ? "blue" : "red"}`, { style: { left: `${p.left}%`, top: `${p.top}%`, opacity: String(1 - (t - time) / 25) }, title: kind }, OBJ_ICON[kind] || "★"));
    }
    for (const [time, owner, kind, lane, x, y] of r.buildings) {
      if (x === null || time > t) continue;
      const p = pct(x, y);
      recent.push(h(`span.map-fx.building.${owner === 100 ? "blue" : "red"}`, { style: { left: `${p.left}%`, top: `${p.top}%` }, title: `${kind === "tower" ? "Torre" : "Inhibidor"} destruido (${lane})` }));
    }
    fx.replaceChildren(...recent);
    clock.textContent = `${fmt.duration(Math.max(1, t))} / ${fmt.duration(duration)}`;
    slider.value = String(Math.round(t));
  };

  const loop = (now) => {
    if (!playing) return;
    const dt = last ? (now - last) / 1000 : 0;
    last = now;
    t = Math.min(duration, t + dt * speed);
    draw();
    if (t >= duration) return pause();
    requestAnimationFrame(loop);
  };
  const play = () => {
    if (t >= duration) t = 0;
    playing = true;
    last = 0;
    playBtn.replaceChildren(icon("stop"));
    requestAnimationFrame(loop);
  };
  const pause = () => {
    playing = false;
    playBtn.replaceChildren(icon("play"));
  };
  const seek = (time) => {
    t = Math.max(0, Math.min(duration, time));
    draw();
  };
  playBtn.onclick = () => (playing ? pause() : play());
  slider.addEventListener("input", () => {
    pause();
    seek(Number(slider.value));
  });

  const el = h(
    "div.replay",
    map,
    h("div.replay-bar", marks, slider),
    h("div.replay-controls", playBtn, clock, h("span.spacer"), h("span.tiny.muted", "Velocidad"), speedSel),
    h(
      "div.legend",
      h("span", h("i", { style: { background: "var(--blue)" } }), "Azul"),
      h("span", h("i", { style: { background: "var(--red)" } }), "Rojo"),
      h("span", h("i", { style: { background: "var(--gold)" } }), "Tú"),
      h("span", h("i.legend-estimated"), "Translúcido: posición reconstruida"),
      h(
        "span.faint",
        { title: "Riot solo da la posición de cada jugador una vez por minuto y en cada kill, objetivo o torre. Entre medias se reconstruye con las compras (vuelta a base), las muertes, los campamentos de jungla (su CS) y rutas que rodean los muros; validado con tus partidas." },
        "Riot da la posición cada minuto y en cada evento; el resto se reconstruye siguiendo los caminos ⓘ",
      ),
    ),
  );
  seek(0);
  return { el, seek, pause, play };
}
