// Arranque de la aplicación: navegación, filtros globales, estado de la API key y logs.
import { api } from "./api.js";
import { onFilters, setFilters, setMeta, state, TIER_LABEL } from "./state.js";
import { errorBox, h, icon, mount, select } from "./ui.js";

const VIEWS = [
  { id: "draft", label: "Draft", icon: "draft", section: "Análisis", load: () => import("./views/draft.js") },
  { id: "matchups", label: "Matchups", icon: "swords", section: "Análisis", load: () => import("./views/matchups.js") },
  { id: "campeones", label: "Campeones", icon: "crown", section: "Análisis", load: () => import("./views/champions.js") },
  { id: "jugador", label: "Jugador", icon: "user", section: "Estadísticas", load: () => import("./views/player.js") },
  { id: "partida", label: "Partida", icon: "match", section: "Estadísticas", load: () => import("./views/match.js") },
  { id: "regiones", label: "Regiones", icon: "globe", section: "Estadísticas", load: () => import("./views/regions.js") },
  { id: "guias", label: "Guías", icon: "book", section: "Coaching", load: () => import("./views/guides.js") },
  { id: "datos", label: "Datos y API", icon: "database", section: "Sistema", load: () => import("./views/data.js") },
];
const ALIASES = { campeon: "campeones" };

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
      { value: "all", label: "OP.GG + Tu BD" },
      { value: "opgg", label: "Solo OP.GG" },
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

function renderFilters() {
  const opts = filterOptions();
  const f = state.filters;
  const make = (key, label, title) =>
    h("div.filter", { title }, h("label", label), select(opts[key], f[key], (v) => setFilters({ [key]: v }), "select select-sm"));
  mount(
    document.getElementById("filters"),
    make("sources", "Fuente", "De dónde salen las estadísticas"),
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
    const key = status.key;
    pill.className = "status-pill";
    let text = "Sin API key";
    if (key.configured && key.status === "valid") {
      pill.classList.add("ok");
      text = key.age_hours !== null ? `Riot API · ${Math.max(0, Math.round(24 - key.age_hours))} h` : "Riot API OK";
      pill.title = `Key ${key.masked} válida. Las development keys caducan a las 24 h.`;
    } else if (key.configured && key.status === "invalid") {
      pill.classList.add("bad");
      text = "Key caducada";
      pill.title = key.detail || "La key ha caducado o no es válida.";
    } else if (key.configured) {
      pill.classList.add("warn");
      text = "Key sin validar";
    } else {
      pill.classList.add("warn");
      pill.title = "Sin key solo funcionan OP.GG y tus datos guardados.";
    }
    if (status.crawler?.running) text += " · recolectando";
    pill.lastChild.textContent = text;
  } catch {
    /* el servidor puede estar arrancando */
  }
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
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent).filter((p, i) => p || i === 0);
  let id = parts[0] || "draft";
  id = ALIASES[id] ? ALIASES[id] : id;
  const params = parts.slice(1);
  if (parts[0] === "campeon") params.unshift("campeon");
  return { id: VIEWS.some((v) => v.id === id) ? id : "draft", params };
}

async function route() {
  const { id, params } = parseHash();
  const view = VIEWS.find((v) => v.id === id);
  document.querySelectorAll(".modal-backdrop, .picker-pop").forEach((el) => el.remove());
  highlightNav(id);
  document.getElementById("view-title").textContent = view.label;
  document.title = `${view.label} · Tempu Drafter`;
  try {
    const module = await view.load();
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

async function boot() {
  renderSidebar();
  setupLogs();
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
    if (current.module?.onFilters) current.module.onFilters();
    else route();
  });
  document.getElementById("key-pill").onclick = () => (location.hash = "#/datos");
  window.addEventListener("hashchange", route);
  refreshStatus();
  setInterval(refreshStatus, 15000);
  route();
  // Data Dragon puede tardar unos segundos la primera vez.
  if (!state.champList.length) {
    setTimeout(async () => {
      setMeta(await api.get("/api/meta"));
      route();
    }, 2500);
  }
}

boot();
