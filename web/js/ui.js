// Utilidades de interfaz: creación de nodos, formato y componentes comunes.
import { dd, ROLE_LABEL, TIER_LABEL } from "./state.js";

/** h("div.card#id", {attrs}, ...children) */
export function h(tag, attrs, ...children) {
  const [, name = "div", rest = ""] = tag.match(/^([a-z0-9-]*)(.*)$/i) || [];
  const el = document.createElement(name || "div");
  for (const part of rest.match(/[.#][^.#]+/g) || []) {
    if (part[0] === ".") el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  if (attrs && (attrs.constructor !== Object || attrs instanceof Node)) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className += (el.className ? " " : "") + v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function mount(container, ...children) {
  container.replaceChildren();
  append(container, children);
  return container;
}

export function svg(markup) {
  const tpl = document.createElement("template");
  tpl.innerHTML = markup.trim();
  return tpl.content.firstChild;
}

// ---------------------------------------------------------------- iconos
const ICONS = {
  draft: '<path d="M4 6h7v5H4zM13 6h7v5h-7zM4 13h7v5H4zM13 13h7v5h-7z"/>',
  swords: '<path d="M14.5 17.5 3 6V3h3l11.5 11.5M13 19l6-6M16 16l4 4M19 21l2-2"/><path d="M14.5 6.5 18 3h3v3l-3.5 3.5M5 14l4 4M7 17l-3 3M3 19l2 2"/>',
  crown: '<path d="m3 8 4 4 5-7 5 7 4-4-2 11H5z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  match: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  book: '<path d="M4 4h10a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z"/><path d="M8 20V8"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  activity: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  swap: '<path d="M7 7h13l-3-3M17 17H4l3 3"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5"/>',
  play: '<path d="M7 4v16l13-8z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>',
  chart: '<path d="M4 20V4M4 20h16M8 16v-5M12 16V8M16 16v-8"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
};

export function icon(name, cls = "") {
  return svg(
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`,
  );
}

const ROLE_PATHS = {
  TOP: '<path d="M4 4h12l-4 4H8v4l-4 4z" fill="currentColor"/><path d="M20 20H8l4-4h4v-4l4-4z" fill="currentColor" opacity=".35"/>',
  JUNGLE: '<path d="M12 3c-2 4-6 5-6 10 0 3 3 6 6 8 3-2 6-5 6-8 0-5-4-6-6-10z" fill="currentColor"/>',
  MID: '<path d="M4 20 20 4" stroke="currentColor" stroke-width="3"/><path d="M4 4h6L4 10zM20 20h-6l6-6z" fill="currentColor" opacity=".35"/>',
  ADC: '<path d="M20 20H8l4-4h4v-4l4-4z" fill="currentColor"/><path d="M4 4h12l-4 4H8v4l-4 4z" fill="currentColor" opacity=".35"/>',
  SUPPORT: '<path d="M12 4l3 4h5l-4 4 2 7-6-3-6 3 2-7-4-4h5z" fill="currentColor"/>',
};

export function roleIcon(role, cls = "role-ico") {
  return svg(`<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ROLE_PATHS[role] || ""}</svg>`);
}

// ---------------------------------------------------------------- formato
export const fmt = {
  int: (n) => (n === null || n === undefined ? "—" : Math.round(n).toLocaleString("es-ES")),
  num: (n, d = 1) => (n === null || n === undefined || Number.isNaN(n) ? "—" : Number(n).toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d })),
  pct: (n, d = 1) => (n === null || n === undefined ? "—" : `${Number(n).toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d })}%`),
  signed: (n, d = 1, suffix = "") => {
    if (n === null || n === undefined) return "—";
    const s = Number(n).toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d });
    return `${n > 0 ? "+" : ""}${s}${suffix}`;
  },
  compact: (n) => {
    if (n === null || n === undefined) return "—";
    if (n >= 1e6) return `${(n / 1e6).toLocaleString("es-ES", { maximumFractionDigits: 1 })} M`;
    if (n >= 1e4) return `${Math.round(n / 1e3).toLocaleString("es-ES")} K`;
    if (n >= 1e3) return `${(n / 1e3).toLocaleString("es-ES", { maximumFractionDigits: 1 })} K`;
    return String(Math.round(n));
  },
  duration: (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "—"),
  ago: (ts) => {
    if (!ts) return "—";
    const ms = ts > 1e12 ? ts : ts * 1000;
    const diff = (Date.now() - ms) / 1000;
    if (diff < 60) return "ahora";
    if (diff < 3600) return `hace ${Math.round(diff / 60)} min`;
    if (diff < 86400) return `hace ${Math.round(diff / 3600)} h`;
    return `hace ${Math.round(diff / 86400)} d`;
  },
  date: (ts) => (ts ? new Date(ts > 1e12 ? ts : ts * 1000).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" }) : "—"),
};

export function wrClass(wr, neutral = 1.5) {
  if (wr === null || wr === undefined) return "muted";
  if (wr >= 50 + neutral) return "good";
  if (wr <= 50 - neutral) return "bad";
  return "";
}

export function wrColor(wr) {
  if (wr === null || wr === undefined) return "var(--line-2)";
  const d = Math.max(-8, Math.min(8, wr - 50)) / 8;
  if (d >= 0) return `color-mix(in srgb, var(--good) ${Math.round(40 + 60 * d)}%, #8a94a8)`;
  return `color-mix(in srgb, var(--bad) ${Math.round(40 - 60 * d)}%, #8a94a8)`;
}

// ---------------------------------------------------------------- componentes
export function champImg(id, size = "") {
  const img = h(`img.champ-img${size ? "." + size : ""}`, { src: dd.champIcon(id), alt: dd.name(id), loading: "lazy", title: dd.name(id) });
  img.addEventListener("error", () => (img.style.visibility = "hidden"), { once: true });
  return img;
}

export function champ(id, { size = "", sub = null, onClick = null } = {}) {
  return h(
    "span.champ",
    { style: onClick ? { cursor: "pointer" } : null, onclick: onClick },
    champImg(id, size),
    h("span", { style: { minWidth: 0 } }, h("div.champ-name", dd.name(id)), sub ? h("div.tiny.muted", sub) : null),
  );
}

export function itemImg(id) {
  if (!id) return h("span.item-slot");
  const img = h("img.item-img", { src: dd.itemIcon(id), alt: dd.itemName(id), title: dd.itemName(id), loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(h("span.item-slot")), { once: true });
  return img;
}

export function spellImg(id) {
  const src = dd.spellIcon(id);
  return src ? h("img.item-img", { src, alt: dd.spellName(id), title: dd.spellName(id), loading: "lazy" }) : h("span.item-slot");
}

export function runeImg(id, size = 26) {
  const src = dd.runeIcon(id);
  return src ? h("img", { src, alt: dd.runeName(id), title: dd.runeName(id), width: size, height: size, loading: "lazy" }) : h("span.item-slot");
}

export function rankImg(tier, cls = "rank-img") {
  const t = (tier || "unranked").toLowerCase();
  const valid = ["iron", "bronze", "silver", "gold", "platinum", "emerald", "diamond", "master", "grandmaster", "challenger"];
  return h(`img.${cls}`, { src: `/static/img/ranks/${valid.includes(t) ? t : "unranked"}.png`, alt: TIER_LABEL[(tier || "").toUpperCase()] || "Sin clasificar" });
}

export function tierBadge(tier) {
  const cls = tier === "S+" ? "Splus" : tier === "?" || !tier ? "q" : tier;
  return h(`span.tier.tier-${cls}`, { title: "Tier calculado con el WR ajustado" }, tier || "?");
}

export function verdictEl(verdict) {
  if (!verdict) return h("span.muted", "—");
  return h(`span.verdict.v-${verdict.code}`, verdict.label);
}

export function wrBar(wr, { lo = null, hi = null } = {}) {
  const pos = Math.max(0, Math.min(100, ((wr ?? 50) - 35) / 30 * 100));
  const bar = h("div.wrbar", h("div.mid"), h("div.fill", { style: { width: `${pos}%`, background: wrColor(wr) } }));
  if (lo !== null && hi !== null) bar.title = `IC 95 %: ${fmt.pct(lo)} – ${fmt.pct(hi)}`;
  return bar;
}

export function wrCell(wr, sub = null, ci = null) {
  return h("div.wrcell", h("div.row", h(`span.v.${wrClass(wr)}`, fmt.pct(wr)), sub ? h("span.tiny.muted", sub) : null), wrBar(wr, ci ? { lo: ci[0], hi: ci[1] } : {}));
}

export function sourceBadges(sources) {
  if (!sources) return null;
  const out = [];
  if (sources.opgg) out.push(h("span.src.src-opgg", { title: `OP.GG: ${fmt.pct(sources.opgg.wr)} en ${fmt.int(sources.opgg.games)} partidas` }, h("b", fmt.compact(sources.opgg.games))));
  if (sources.own) out.push(h("span.src.src-own", { title: `Tu base de datos: ${fmt.pct(sources.own.wr)} en ${fmt.int(sources.own.games)} partidas` }, h("b", fmt.compact(sources.own.games))));
  return out.length ? h("span.row-wrap", { style: { gap: "4px" } }, out) : h("span.tiny.faint", "estimado");
}

export function confidenceEl(conf) {
  if (!conf) return null;
  const cls = conf.level >= 4 ? "pill-good" : conf.level >= 3 ? "pill-gold" : conf.level >= 2 ? "pill-warn" : "pill-bad";
  return h(`span.pill.${cls}`, { title: "Fiabilidad según el tamaño de muestra" }, conf.label);
}

export function kpi(label, value, sub = null, cls = "") {
  return h("div.kpi", h("div.kpi-label", label), h(`div.kpi-value${cls ? "." + cls : ""}`, value), sub ? h("div.kpi-sub", sub) : null);
}

export function empty(title, text = "", iconName = "search") {
  return h("div.empty", icon(iconName), h("h3", title), text ? h("p", text) : null);
}

export function loading(text = "Cargando…") {
  return h("div.loading", h("div.spinner"), text);
}

export function notice(text, kind = "info") {
  return h(`div.notice.notice-${kind}`, icon(kind === "bad" || kind === "warn" ? "alert" : "info"), h("div", text));
}

export function card(title, ...children) {
  return h("section.card", title ? h("h3.card-title", title) : null, ...children);
}

export function roleTabs(active, onChange) {
  return h(
    "div.tabs",
    ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"].map((r) =>
      h(`button.tab${r === active ? ".active" : ""}`, { onclick: () => onChange(r), title: ROLE_LABEL[r] }, roleIcon(r), ROLE_LABEL[r]),
    ),
  );
}

export function select(options, value, onChange, cls = "select") {
  const el = h(`select.${cls.split(" ").join(".")}`, { onchange: (e) => onChange(e.target.value) });
  for (const opt of options) {
    if (opt.group) {
      const g = h("optgroup", { label: opt.group });
      opt.options.forEach((o) => g.append(h("option", { value: o.value, selected: String(o.value) === String(value) }, o.label)));
      el.append(g);
    } else {
      el.append(h("option", { value: opt.value, selected: String(opt.value) === String(value) }, opt.label));
    }
  }
  return el;
}

/** Tabla con columnas ordenables. cols: [{key, label, render, sort, class}] */
export function table(cols, rows, { sortKey = null, sortDir = -1, onRow = null, rowClass = null } = {}) {
  let key = sortKey;
  let dir = sortDir;
  const wrap = h("div.table-wrap");
  const draw = () => {
    const sorted = [...rows];
    const col = cols.find((c) => c.key === key);
    if (col && col.sort !== false) {
      const getter = typeof col.sort === "function" ? col.sort : (r) => r[col.key];
      sorted.sort((a, b) => {
        const va = getter(a);
        const vb = getter(b);
        if (va === vb) return 0;
        if (va === null || va === undefined) return 1;
        if (vb === null || vb === undefined) return -1;
        return (va > vb ? 1 : -1) * dir;
      });
    }
    const head = h(
      "tr",
      cols.map((c) =>
        h(
          `th${c.sort !== false ? ".sortable" : ""}${c.key === key ? ".sorted" : ""}`,
          {
            class: c.class,
            onclick: c.sort === false ? null : () => {
              dir = key === c.key ? -dir : -1;
              key = c.key;
              draw();
            },
          },
          c.label,
          c.key === key ? (dir > 0 ? " ▲" : " ▼") : "",
        ),
      ),
    );
    const body = sorted.map((r, i) =>
      h(
        `tr${onRow ? ".clickable" : ""}`,
        { class: rowClass ? rowClass(r) : null, onclick: onRow ? () => onRow(r) : null },
        cols.map((c) => h("td", { class: c.class }, c.render ? c.render(r, i) : r[c.key] ?? "—")),
      ),
    );
    mount(wrap, h("table.table", h("thead", head), h("tbody", body)));
  };
  draw();
  return wrap;
}

// ---------------------------------------------------------------- toasts / modal
export function toast(message, kind = "info", ms = 4200) {
  const el = h(`div.toast.${kind}`, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), ms);
}

export function modal(title, body, { wide = false } = {}) {
  const close = () => backdrop.remove();
  const backdrop = h(
    "div.modal-backdrop",
    { onclick: (e) => e.target === backdrop && close() },
    h(
      "div.modal",
      { style: wide ? { width: "min(1180px, 100%)" } : null },
      h("div.modal-head", typeof title === "string" ? h("h3", title) : title, h("button.btn.btn-ghost.btn-icon.modal-close", { onclick: close, title: "Cerrar" }, icon("close"))),
      h("div.modal-body", body),
    ),
  );
  const onKey = (e) => {
    if (e.key === "Escape") {
      close();
      document.removeEventListener("keydown", onKey);
    }
  };
  document.addEventListener("keydown", onKey);
  document.body.append(backdrop);
  return { close, body: backdrop.querySelector(".modal-body") };
}

export function errorBox(err) {
  const msg = err?.message || String(err);
  if (err?.code === "riot_key") {
    return notice(h("span", "Falta o ha caducado la API key de Riot. ", h("a", { href: "#/datos" }, "Configúrala en Datos"), "."), "warn");
  }
  return notice(msg, "bad");
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
