// Arranque de la aplicación: navegación, filtros globales, estado de la API key y logs.
import { api } from "./api.js";
import { closePickers } from "./components/picker.js";
import { dd, DEFAULT_FILTERS, onFilters, setFilters, setMeta, state, TIER_LABEL } from "./state.js";
import { errorBox, h, icon, mount, select } from "./ui.js";

const VIEWS = [
  { id: "progreso", label: "Mi progreso", icon: "target", section: "Tú", noFilters: true, load: () => import("./views/coach.js") },
  { id: "repeticiones", label: "Repeticiones", icon: "film", section: "Tú", noFilters: true, load: () => import("./views/replays.js") },
  { id: "draft", label: "Draft", icon: "draft", section: "Análisis", load: () => import("./views/draft.js") },
  { id: "matchups", label: "Matchups", icon: "swords", section: "Análisis", load: () => import("./views/matchups.js") },
  { id: "campeones", label: "Campeones", icon: "crown", section: "Análisis", load: () => import("./views/champions.js") },
  { id: "jugador", label: "Jugador", icon: "user", section: "Estadísticas", load: () => import("./views/player.js") },
  { id: "partida", label: "Partida", icon: "match", section: "Estadísticas", load: () => import("./views/match.js") },
  { id: "regiones", label: "Regiones", icon: "globe", section: "Estadísticas", load: () => import("./views/regions.js") },
  { id: "otps", label: "OTPs", icon: "crown", section: "Estadísticas", noFilters: true, load: () => import("./views/otps.js") },
  { id: "guias", label: "Guías", icon: "book", section: "Coaching", load: () => import("./views/guides.js") },
  { id: "videos", label: "Vídeos", icon: "film", section: "Coaching", noFilters: true, load: () => import("./views/videos.js") },
  { id: "datos", label: "Datos y API", icon: "database", section: "Sistema", noFilters: true, load: () => import("./views/data.js") },
];
const ALIASES = { campeon: "campeones", perfil: "progreso", "mi-progreso": "progreso", repeticion: "repeticiones", replays: "repeticiones" };

const main = document.getElementById("main");
let current = { id: null, module: null, params: [] };

// ------------------------------------------------------------------ sidebar
function renderSidebar() {
  const side = document.getElementById("sidebar");
  const items = [h("div.brand", h("div.brand-mark", "T"), h("div.brand-name", "Tempu Drafter", h("small", "LoL Data Miner")))];
  let section = null;
  for (const v of VIEWS) {
    if (v.section !== section) {
      section = v.section;
      items.push(h("div.nav-section", section));
    }
    items.push(h("a.nav-link", { href: `#/${v.id}`, dataset: { view: v.id } }, icon(v.icon), h("span", v.label)));
  }
  items.push(h("div.sidebar-foot", "Datos: OP.GG + Riot API", h("br"), "Proyecto personal sin afiliación con Riot Games."));
  mount(side, items);
}

function highlightNav(id) {
  document.querySelectorAll(".nav-link").forEach((a) => a.classList.toggle("active", a.dataset.view === id));
}

// ------------------------------------------------------------------ filtros
function filterOptions() {
  const tiers = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"];
  const platforms = state.meta?.platforms || [];
  const regions = state.meta?.regions || [];
  const opggPatches = state.opgg?.versions || [];
  const ownPatches = state.meta?.patches || [];
  const patches = [...new Set([...opggPatches, ...ownPatches])].sort((a, b) => {
    const [a1, a2] = a.split(".").map(Number);
    const [b1, b2] = b.split(".").map(Number);
    return b1 - a1 || b2 - a2;
  });
  return {
    sources: [
      { value: "all", label: "Webs + tu BD" },
      { value: "web", label: "Solo webs" },
      { value: "opgg", label: "Solo OP.GG" },
      { value: "lolalytics", label: "Solo Lolalytics" },
      { value: "own", label: "Solo tu BD" },
    ],
    tier: [
      { value: "all", label: "Todos los elos" },
      { group: "Rangos", options: [
        { value: "gold_plus", label: "Oro+" },
        { value: "platinum_plus", label: "Platino+" },
        { value: "emerald_plus", label: "Esmeralda+" },
        { value: "diamond_plus", label: "Diamante+" },
        { value: "master_plus", label: "Maestro+" },
      ] },
      { group: "Tier exacto", options: tiers.map((t) => ({ value: t, label: TIER_LABEL[t] })) },
    ],
    platform: [
      { value: "all", label: "Global" },
      ...regions.map((r) => ({ group: r.label, options: platforms.filter((p) => p.region === r.id).map((p) => ({ value: p.id, label: `${p.label} · ${p.name}` })) })),
    ],
    patch: [
      { value: "all", label: "Actual + anterior" },
      { value: "latest", label: "Solo parche actual" },
      { value: "recent", label: "Sin histórico antiguo" },
      ...(patches.length ? [{ group: "Parche concreto", options: patches.map((p) => ({ value: p, label: `Parche ${p}` })) }] : []),
      ...(state.meta?.has_legacy ? [{ value: "legacy", label: "Solo histórico (BD antigua)" }] : []),
    ],
  };
}

function flatLabel(options, value) {
  for (const o of options) {
    if (o.group) {
      const hit = o.options.find((x) => String(x.value) === String(value));
      if (hit) return hit.label;
    } else if (String(o.value) === String(value)) return o.label;
  }
  return value;
}

function filtersSummary() {
  const opts = filterOptions();
  const f = state.filters;
  const el = document.querySelector("#filters-toggle span");
  const text = [flatLabel(opts.sources, f.sources), flatLabel(opts.tier, f.tier), flatLabel(opts.platform, f.platform), flatLabel(opts.patch, f.patch)].join(" · ");
  if (el) el.textContent = text.split(" · ").slice(1).join(" · ");
  const btn = document.getElementById("filters-toggle");
  if (btn) btn.title = `Filtros: ${text}`;
}

function hasValue(options, value) {
  return options.some((o) => (o.group ? o.options.some((x) => String(x.value) === String(value)) : String(o.value) === String(value)));
}

function renderFilters() {
  const opts = filterOptions();
  const f = state.filters;
  // Un filtro guardado que ya no existe (un parche que salió de la lista, el histórico borrado…) vuelve a su valor por
  // defecto: si no, el desplegable salía en blanco y las consultas iban con un valor que el servidor ya no conoce.
  if (state.meta) {
    const fixes = Object.fromEntries(Object.keys(opts).filter((k) => !hasValue(opts[k], f[k])).map((k) => [k, DEFAULT_FILTERS[k]]));
    if (Object.keys(fixes).length) setFilters(fixes);
  }
  queueMicrotask(filtersSummary);
  const make = (key, label, title) =>
    h("div.filter", { title }, h("label", label), select(opts[key], f[key], (v) => setFilters({ [key]: v }), "select select-sm"));
  mount(
    document.getElementById("filters"),
    make("sources", "Fuente", "OP.GG y Lolalytics (webs) y tu propia base de datos"),
    make("tier", "Elo", "Rango de las partidas analizadas"),
    make("platform", "Región", "Global o una región concreta"),
    make("patch", "Parche", "Actual + anterior suma más muestra; el anterior ya está completo"),
  );
}

// ------------------------------------------------------------------ estado
async function refreshStatus() {
  try {
    const status = await api.get("/api/system/status");
    state.status = status;
    const pill = document.getElementById("key-pill");
    let text = keyPill(pill, status.key);
    if (status.crawler?.running) text += " · recolectando";
    pill.lastChild.textContent = text;
    renderProfilePill(status.tracker);
    renderDataPill(status.warmer);
  } catch {
    /* el servidor puede estar arrancando */
  }
}

/** Pone el color y el aviso de la píldora de la API key según su estado y devuelve su texto. */
function keyPill(pill, key) {
  pill.className = "status-pill";
  if (key.configured && key.status === "valid") return validKeyPill(pill, key);
  if (key.configured && key.status === "invalid" && key.rechecking) {
    pill.classList.add("warn");
    pill.title = "Riot aún no acepta la key nueva; se vuelve a comprobar sola.";
    return "Key activándose…";
  }
  if (key.configured && key.status === "invalid") {
    pill.classList.add("bad");
    pill.title = key.detail || "La key ha caducado o no es válida.";
    return "Key rechazada";
  }
  pill.classList.add("warn");
  if (key.configured) return "Key sin validar";
  pill.title = "Sin key solo funcionan OP.GG y tus datos guardados.";
  return "Sin API key";
}

/** Píldora de una key válida: las horas que le quedan (las development keys caducan a las 24 h). */
function validKeyPill(pill, key) {
  pill.classList.add("ok");
  const left = key.age_hours !== null ? 24 - key.age_hours : null;
  const counting = left !== null && left > 0; // sin edad conocida (o ya pasadas las 24 h) no se cuentan las horas
  if (!counting) {
    pill.title = `Key ${key.masked} válida.`;
    return "Riot API OK";
  }
  pill.title = `Key ${key.masked} válida. Las development keys caducan a las 24 h de generarlas.`;
  return `Riot API · ${left < 1 ? "< 1" : Math.round(left)} h`;
}

function renderProfilePill(tracker) {
  const el = document.getElementById("profile-pill");
  if (!el || !tracker) return;
  const p = tracker.profile;
  el.classList.remove("hidden");
  el.classList.toggle("empty", !p);
  if (!p) {
    mount(el, icon("user"), h("span", "Guardar mi perfil"));
    el.title = "Guarda tu Riot ID para que tus partidas se recojan y analicen solas";
    return;
  }
  const extra = profileExtra(tracker, p);
  mount(el, p.profile_icon != null ? h("img", { src: dd.profileIcon(p.profile_icon), alt: "" }) : icon("user"), h("span.name", p.game_name), extra);
  el.title = `${p.game_name}#${p.tag_line} · Mi progreso${tracker.in_game ? " (estás en partida: se analizará al terminar)" : ""}`;
}

/** Indicador junto a tu nombre: descargando partidas, en partida o cuenta sin vincular con Riot. */
function profileExtra(tracker, p) {
  if (tracker.status === "syncing") return h("span.spinner.sm", { title: "Descargando partidas" });
  if (tracker.in_game) return h("span.pill.pill-warn", "en partida");
  return p.linked ? null : h("span.dot.warn");
}

function renderDataPill(w) {
  const el = document.getElementById("data-pill");
  if (!el || !w) return;
  el.classList.toggle("hidden", !w.enabled);
  if (!w.enabled) return;
  const essential = w.essential_pct ?? 100;
  const all = w.all_pct ?? 100;
  let cls = "ok";
  let text = "Datos al día";
  let title = "Todo lo que usa la web está descargado y fresco.";
  if (w.paused) {
    cls = "warn";
    text = w.pause_reason === "in_game" ? "En partida: descargas en pausa" : "Precarga en pausa";
    title = w.pause_reason === "in_game" ? "Mientras juegas no se descarga nada para no quitarte ancho de banda. Sigue sola al terminar." : "Reanúdala en Datos y API.";
  } else if (essential < 100) {
    cls = "busy";
    text = `Preparando datos ${essential} %`;
    title = "Descargando en segundo plano lo esencial (tier lists y counters). Las vistas ya funcionan con lo que hay.";
  } else if (all < 100) {
    text = `Datos al día · extras ${all} %`;
    title = "Lo esencial está listo. Siguen bajando extras (sinergias, guías de matchups populares…).";
  }
  el.className = `status-pill data-pill ${cls}`;
  mount(el, cls === "busy" ? h("span.spinner.sm") : h("span.dot"), h("span", text));
  el.title = title;
}

// ------------------------------------------------------------------ datos pendientes
// Si una vista se ha servido sin algún dato externo (aún descargándose), se
// recarga sola a los pocos segundos sin perder la posición ni interrumpirte.
let pendingTimer = null;
let pendingTries = 0;
const MAX_PENDING_TRIES = 8;
function busyUser() {
  const a = document.activeElement;
  return !!document.querySelector(".modal-backdrop") || (a && a !== document.body && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName));
}
function onPending() {
  const chip = document.getElementById("pending-chip");
  if (pendingTries >= MAX_PENDING_TRIES) {
    chip?.classList.add("hidden"); // ya se reintentó bastante: el resto llegará con la precarga
    return;
  }
  chip?.classList.remove("hidden");
  if (pendingTimer) return;
  const routeAtSchedule = location.hash;
  pendingTimer = setTimeout(async function retry() {
    pendingTimer = null;
    if (location.hash !== routeAtSchedule || !current.module) {
      // La vista cambió su URL sin navegar (replaceState de las pestañas de rol…) y ya pidió lo suyo: sin esto el
      // chip se quedaba girando para siempre.
      chip?.classList.add("hidden");
      return;
    }
    if (current.module.skipSoftReload?.()) {
      chip?.classList.add("hidden"); // la vista no se puede rehacer sin cortarte (p. ej. el visor reproduciendo)
      return;
    }
    if (busyUser()) {
      pendingTimer = setTimeout(retry, 2500);
      return;
    }
    pendingTries += 1;
    chip?.classList.add("hidden");
    const y = main.scrollTop;
    try {
      if (current.module.onFilters) await current.module.onFilters();
      else await route({ keepPending: true });
    } finally {
      requestAnimationFrame(() => (main.scrollTop = y));
    }
  }, 3500);
}

// ------------------------------------------------------------------ logs
const logList = document.getElementById("log-list");
function addLog(entry) {
  const time = new Date(entry.ts * 1000).toLocaleTimeString("es-ES", { hour12: false });
  logList.append(h(`div.log-line.log-${entry.kind}`, h("span.log-ts", time), h("span.log-src", entry.source), h("span.log-msg", entry.message)));
  while (logList.children.length > 400) logList.firstChild.remove();
  logList.scrollTop = logList.scrollHeight;
}

async function setupLogs() {
  const drawer = document.getElementById("drawer");
  const toggle = document.getElementById("log-toggle");
  toggle.append(icon("activity"));
  toggle.onclick = () => drawer.classList.toggle("open");
  document.getElementById("log-close").onclick = () => drawer.classList.remove("open");
  document.getElementById("log-clear").onclick = () => logList.replaceChildren();
  try {
    const { logs } = await api.get("/api/system/logs/recent", { limit: 120 });
    logs.forEach(addLog);
  } catch {
    /* ignore */
  }
  const source = new EventSource("/api/system/logs");
  source.onmessage = (event) => {
    try {
      addLog(JSON.parse(event.data));
    } catch {
      /* ignore */
    }
  };
}

// ------------------------------------------------------------------ router
function parseHash() {
  const decode = (p) => {
    try {
      return decodeURIComponent(p);
    } catch {
      return p; // un enlace mal formado (%E0…) no puede tumbar el router
    }
  };
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decode).filter((p, i) => p || i === 0);
  let id = parts[0] || "draft";
  id = ALIASES[id] ? ALIASES[id] : id;
  const params = parts.slice(1);
  if (parts[0] === "campeon") params.unshift("campeon");
  return { id: VIEWS.some((v) => v.id === id) ? id : "draft", params };
}

let routeToken = 0;
async function route({ keepPending = false } = {}) {
  const token = ++routeToken; // si llega otra navegación mientras carga esta, gana la última
  const { id, params } = parseHash();
  const view = VIEWS.find((v) => v.id === id);
  if (!keepPending) {
    pendingTries = 0;
    clearTimeout(pendingTimer);
    pendingTimer = null;
    document.getElementById("pending-chip")?.classList.add("hidden");
  }
  document.body.classList.remove("filters-open", "nav-open");
  closePickers();
  document.querySelectorAll(".modal-backdrop").forEach((el) => el.remove());
  highlightNav(id);
  document.body.classList.toggle("no-filters", !!view.noFilters);
  document.getElementById("view-title").textContent = view.label;
  document.title = `${view.label} · Tempu Drafter`;
  try {
    const module = await view.load();
    if (token !== routeToken) return;
    if (current.module?.destroy) current.module.destroy();
    current = { id, module, params };
    const container = h("div.view");
    mount(main, container);
    main.scrollTop = 0;
    await module.render(container, params);
  } catch (err) {
    console.error(err);
    mount(main, h("div.view", errorBox(err)));
  }
}

// Al entrar en la selección de campeones del cliente de LoL se abre el Draft (una vez por selección: si te vas a otra
// vista, no se te vuelve a llevar).
const champSelectsSeen = new Set();
let watchingChampSelect = false;
async function watchChampSelect() {
  if (watchingChampSelect) return; // también con la pestaña en segundo plano: estarás mirando el cliente
  watchingChampSelect = true;
  try {
    const s = await api.get("/api/draft/live");
    const id = s.active && s.draft ? String(s.draft.game_id ?? "sin-id") : null;
    if (id && !champSelectsSeen.has(id)) {
      champSelectsSeen.add(id);
      if (current.id !== "draft") location.hash = "#/draft";
    }
  } catch {
    // sin servidor o sin cliente: nada que hacer
  } finally {
    watchingChampSelect = false;
  }
}

async function boot() {
  renderSidebar();
  void setupLogs(); // trata sus errores (sin servidor, el panel de actividad se queda vacío)
  try {
    const [meta, opgg] = await Promise.all([api.get("/api/meta"), api.get("/api/opgg/status").catch(() => null)]);
    setMeta(meta);
    state.opgg = opgg;
  } catch (err) {
    mount(main, h("div.view", errorBox(err)));
    return;
  }
  renderFilters();
  onFilters(() => {
    filtersSummary();
    if (current.module?.onFilters) current.module.onFilters();
    else void route(); // trata sus errores (los pinta en la vista)
  });
  // Cerrar el panel de filtros al pulsar fuera de él.
  document.addEventListener("click", (e) => {
    if (!document.body.classList.contains("filters-open")) return;
    if (e.target.closest("#filters, #filters-toggle")) return;
    document.body.classList.remove("filters-open");
  });
  document.getElementById("key-pill").onclick = () => (location.hash = "#/datos");
  document.getElementById("data-pill").onclick = () => (location.hash = "#/datos");
  const ft = document.getElementById("filters-toggle");
  ft.append(icon("filter"), h("span", "Filtros"));
  ft.onclick = () => document.body.classList.toggle("filters-open");
  window.addEventListener("tempu:pending", onPending);
  window.addEventListener("hashchange", () => route());
  void refreshStatus(); // trata sus errores (el servidor puede estar arrancando)
  setInterval(refreshStatus, 15000);
  setInterval(watchChampSelect, 3000);
  window.addEventListener("tempu:status", refreshStatus); // p. ej. al guardar o quitar tu perfil
  void route();
  // Data Dragon puede tardar la primera vez (o no haber internet al abrir): se reintenta solo hasta que llegue.
  if (!state.champList.length) {
    const retryMeta = async () => {
      try {
        const meta = await api.get("/api/meta");
        if (meta.ddragon?.champions?.length) {
          setMeta(meta);
          renderFilters();
          void route();
          return;
        }
      } catch {
        /* servidor arrancando o sin conexión */
      }
      setTimeout(retryMeta, 10000);
    };
    setTimeout(retryMeta, 2500);
  }
}

// Este módulo es el punto de entrada y nadie lo importa: esperar al arranque no retrasa nada más.
await boot();
