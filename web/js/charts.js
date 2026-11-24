// Gráficos SVG ligeros (sin dependencias).
import { h } from "./ui.js";

const NS = "http://www.w3.org/2000/svg";

function el(name, attrs = {}, text = null) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, v);
  if (text !== null) node.textContent = text;
  return node;
}

/** Color del indicador de victoria: verde desde 52,5 %, rojo hasta 47,5 % y dorado entre medias. */
function gaugeColor(v) {
  if (v >= 52.5) return "var(--good)";
  return v <= 47.5 ? "var(--bad)" : "var(--gold)";
}

/** Semicírculo 0-100 % con zona central neutra. */
export function gauge(value, { label = "", low = null, high = null } = {}) {
  const v = Math.max(0, Math.min(100, value ?? 50));
  const r = 90;
  const cx = 105;
  const cy = 104;
  const angle = (p) => Math.PI * (1 - p / 100);
  const point = (p, rad = r) => [cx + rad * Math.cos(angle(p)), cy - rad * Math.sin(angle(p))];
  const arc = (from, to, rad = r) => {
    const [x1, y1] = point(from, rad);
    const [x2, y2] = point(to, rad);
    return `M ${x1} ${y1} A ${rad} ${rad} 0 0 1 ${x2} ${y2}`;
  };
  const color = gaugeColor(v);
  const root = el("svg", { viewBox: "0 0 210 118" });
  root.append(el("path", { d: arc(0, 100), stroke: "var(--line)", "stroke-width": 14, fill: "none", "stroke-linecap": "round" }));
  if (low !== null && high !== null) {
    root.append(el("path", { d: arc(Math.max(0, low), Math.min(100, high)), stroke: color, "stroke-opacity": 0.25, "stroke-width": 14, fill: "none" }));
  }
  root.append(el("path", { d: arc(0, v), stroke: color, "stroke-width": 14, fill: "none", "stroke-linecap": "round" }));
  const [nx, ny] = point(50, r + 10);
  const [nx2, ny2] = point(50, r - 10);
  root.append(el("line", { x1: nx, y1: ny, x2: nx2, y2: ny2, stroke: "var(--faint)", "stroke-width": 1.5 }));
  const wrap = h("div.gauge");
  wrap.append(root, h("div.gauge-value", h("b", `${v.toLocaleString("es-ES", { maximumFractionDigits: 1 })}%`), label ? h("small", label) : null));
  return wrap;
}

/** Línea con eje cero (p. ej. diferencia de oro por minuto). */
export function lineChart(points, { height = 300, yLabel = "", markers = [], colorPos = "var(--blue)", colorNeg = "var(--red)" } = {}) {
  const width = 1180;
  const pad = { l: 52, r: 14, t: 12, b: 26 };
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xMax = Math.max(1, ...xs);
  const yAbs = Math.max(1000, ...ys.map(Math.abs)) * 1.1;
  const X = (x) => pad.l + (x / xMax) * (width - pad.l - pad.r);
  const Y = (y) => pad.t + ((yAbs - y) / (2 * yAbs)) * (height - pad.t - pad.b);
  const root = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart" });
  const defs = el("defs");
  const grad = el("linearGradient", { id: "lc-grad", x1: 0, y1: 0, x2: 0, y2: 1 });
  grad.append(el("stop", { offset: "0%", "stop-color": colorPos, "stop-opacity": 0.35 }), el("stop", { offset: "50%", "stop-color": colorPos, "stop-opacity": 0.02 }), el("stop", { offset: "50%", "stop-color": colorNeg, "stop-opacity": 0.02 }), el("stop", { offset: "100%", "stop-color": colorNeg, "stop-opacity": 0.35 }));
  defs.append(grad);
  root.append(defs);
  for (const frac of [-1, -0.5, 0.5, 1]) {
    const y = Y(yAbs * frac * 0.9);
    root.append(el("line", { x1: pad.l, x2: width - pad.r, y1: y, y2: y, class: "grid-line" }));
    root.append(el("text", { x: pad.l - 8, y: y + 4, "text-anchor": "end" }, Math.round(yAbs * frac * 0.9).toLocaleString("es-ES")));
  }
  root.append(el("line", { x1: pad.l, x2: width - pad.r, y1: Y(0), y2: Y(0), class: "zero" }));
  for (let m = 0; m <= xMax; m += 5) {
    root.append(el("text", { x: X(m), y: height - 8, "text-anchor": "middle" }, `${m}'`));
  }
  if (points.length) {
    const d = points.map((p, i) => `${i ? "L" : "M"} ${X(p.x).toFixed(1)} ${Y(p.y).toFixed(1)}`).join(" ");
    root.append(el("path", { d: `${d} L ${X(points.at(-1).x)} ${Y(0)} L ${X(points[0].x)} ${Y(0)} Z`, fill: "url(#lc-grad)" }));
    root.append(el("path", { d, fill: "none", stroke: "var(--gold)", "stroke-width": 2.2 }));
  }
  for (const m of markers) {
    const x = X(m.x);
    const y = m.team === 100 ? pad.t + 4 : height - pad.b - 4;
    const g = el("g");
    g.append(el("circle", { cx: x, cy: y, r: 6, fill: m.team === 100 ? colorPos : colorNeg, stroke: "var(--bg)", "stroke-width": 2 }));
    g.append(el("title", {}, m.label));
    root.append(g);
  }
  if (yLabel) root.append(el("text", { x: 6, y: 12 }, yLabel));
  return root;
}

/** Curva de probabilidad por tramo (p. ej. duración de la partida). */
export function curveChart(items, { height = 170 } = {}) {
  const width = 520;
  const pad = { l: 36, r: 12, t: 14, b: 28 };
  const vals = items.map((i) => i.p ?? 50);
  const lo = Math.min(44, ...vals) - 1;
  const hi = Math.max(56, ...vals) + 1;
  const X = (i) => pad.l + (i / Math.max(1, items.length - 1)) * (width - pad.l - pad.r);
  const Y = (v) => pad.t + ((hi - v) / (hi - lo)) * (height - pad.t - pad.b);
  const root = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart" });
  for (const v of [lo + 1, 50, hi - 1]) {
    root.append(el("line", { x1: pad.l, x2: width - pad.r, y1: Y(v), y2: Y(v), class: v === 50 ? "zero" : "grid-line" }));
    root.append(el("text", { x: pad.l - 6, y: Y(v) + 4, "text-anchor": "end" }, `${Math.round(v)}%`));
  }
  const d = items.map((it, i) => `${i ? "L" : "M"} ${X(i)} ${Y(it.p ?? 50)}`).join(" ");
  root.append(el("path", { d, fill: "none", stroke: "var(--gold)", "stroke-width": 2.4 }));
  items.forEach((it, i) => {
    const v = it.p ?? 50;
    root.append(el("circle", { cx: X(i), cy: Y(v), r: 5, fill: v >= 50 ? "var(--good)" : "var(--bad)", stroke: "var(--bg)", "stroke-width": 2 }));
    root.append(el("text", { x: X(i), y: Y(v) - 10, "text-anchor": "middle", style: "fill: var(--text-2); font-weight: 700" }, `${v.toLocaleString("es-ES", { maximumFractionDigits: 1 })}%`));
    root.append(el("text", { x: X(i), y: height - 8, "text-anchor": "middle" }, it.label));
  });
  return root;
}

/** Calibración: probabilidad predicha vs observada. */
export function calibrationChart(bins, { size = 240 } = {}) {
  const pad = 30;
  const S = (v) => pad + (v / 100) * (size - pad - 10);
  const Yc = (v) => size - pad - (v / 100) * (size - pad - 10);
  const root = el("svg", { viewBox: `0 0 ${size} ${size}`, class: "chart", style: `max-width:${size}px` });
  root.append(el("line", { x1: S(0), y1: Yc(0), x2: S(100), y2: Yc(100), stroke: "var(--line-2)", "stroke-dasharray": "4 4" }));
  for (const v of [0, 50, 100]) {
    root.append(el("text", { x: S(v), y: size - 10, "text-anchor": "middle" }, `${v}`));
    root.append(el("text", { x: 22, y: Yc(v) + 4, "text-anchor": "end" }, `${v}`));
  }
  const maxN = Math.max(1, ...bins.map((b) => b.n));
  for (const b of bins) {
    if (b.predicted === null || b.observed === null) continue;
    root.append(el("circle", { cx: S(b.predicted), cy: Yc(b.observed), r: 3 + 7 * Math.sqrt(b.n / maxN), fill: "var(--gold)", "fill-opacity": 0.75 }));
  }
  return root;
}
