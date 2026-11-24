// Datos y API: API key, precarga en segundo plano, recolector por servidores,
// fuentes externas, límites de la API, base de datos y modelo de draft.
import { api } from "../api.js";
import { calibrationChart } from "../charts.js";
import { champPicker } from "../components/picker.js";
import { dd, platformLabel, ROLE_LABEL, ROLES, state, TIER_LABEL } from "../state.js";
import { champImg, collapsible, errorBox, fmt, h, icon, kpi, latest, loading, modal, mount, multiSelect, notice, rankImg, roleIcon, select, toast } from "../ui.js";

let root = null;
let timer = null;
let sections = {};
let crawlerForm = null; // configuración que estás editando (sobrevive a los refrescos)
let crawlerFormFor = null; // "running" | "stopped": el formulario solo se reconstruye al cambiar de estado
let runningSig = null;
let resultsMode = null; // modo de los resultados que se ven (null = ninguno: no se está buscando por campeones)
let limitsNow = {}; // uso de la API por host (para avisar si otra app usa tu misma key)

const ALL_TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"];
const APEX = ["MASTER", "GRANDMASTER", "CHALLENGER"];
const STATUS_LABEL = {
  starting: "Arrancando",
  running: "Recolectando",
  searching: "Buscando jugadores",
  idle: "En espera",
  waiting_key: "Esperando API key",
  paused_game: "En pausa: estás en partida",
  stopped: "Detenido",
};
const SEARCH_LABEL = {
  starting: "arrancando",
  running: "en marcha",
  idle: "al día (todo el ranking revisado)",
  waiting_key: "esperando API key",
  paused_game: "en pausa mientras juegas",
  stopped: "detenida",
};
const HISTORY_LABEL = {
  queued: "historial en cola",
  loading: "bajando historial",
  done: "historial completo",
  share: "historial completo (más atrás no lo jugaba)",
  capped: "tope alcanzado (más cuando acabe la búsqueda)",
};
const REGION_GROUPS = [
  { id: "europe", label: "Europa" },
  { id: "americas", label: "América" },
  { id: "asia", label: "Asia" },
  { id: "sea", label: "SEA / Oceanía" },
];

export function destroy() {
  root = null;
  clearInterval(timer);
}

export async function render(container) {
  root = container;
  crawlerForm = null;
  crawlerFormFor = null;
  resultsMode = undefined; // distinto de null: el primer refresco siempre pinta (o vacía) la sección de resultados
  keyRendered = null;
  sourcesSig = null;
  limitsSig = null;
  otpsSig = null;
  otpsAt = 0;
  sections = {
    key: h("div"),
    warmer: h("div"),
    crawlerForm: h("div"),
    crawlerStatus: h("div"),
    otps: h("div"),
    results: h("div"),
    sources: h("div"),
    limits: h("div"),
    db: h("div"),
    model: h("div"),
  };
  mount(
    container,
    h("div.section-head", h("div", h("h2", "Datos y API"), h("p", "Tu API key de Riot, lo que se descarga solo en segundo plano, el recolector de partidas y el estado de cada fuente de datos."))),
    h(
      "div.grid.grid-2",
      h("section.card", h("h3.card-title", icon("key"), "API key de Riot"), sections.key),
      h("section.card", h("h3.card-title", icon("refresh"), "Datos en segundo plano"), sections.warmer),
    ),
    h(
      "section.card",
      { style: { marginTop: "16px" } },
      h("h3.card-title", icon("database"), "Recolector de partidas"),
      h("div.grid.crawler-grid", sections.crawlerForm, sections.crawlerStatus),
      sections.results,
    ),
    h(
      "div.stack",
      { style: { marginTop: "16px" } },
      collapsible("datos.fuentes", [icon("globe"), "Fuentes de datos (OP.GG, Lolalytics, Riot)"], sections.sources, { summary: "estado y caché de cada web" }),
      collapsible("datos.limites", [icon("activity"), "Límite de la API de Riot"], sections.limits, { summary: "uso en tiempo real" }),
      collapsible("datos.bd", [icon("database"), "Base de datos"], sections.db, { summary: "partidas, jugadores y tamaño" }),
      collapsible("datos.modelo", [icon("chart"), "Modelo de draft"], sections.model, { summary: "calibración y backtest" }),
    ),
  );
  Object.values(sections).forEach((s) => mount(s, loading()));
  await refresh();
  clearInterval(timer);
  timer = setInterval(() => root && !interacting() && refresh({ timer: true }), 2500);
}

/** Eligiendo en un desplegable o escribiendo en la vista: el refresco espera (repintar cerraría el desplegable). */
function interacting() {
  const a = document.activeElement;
  return !!a && a !== document.body && root?.contains(a) && /^(SELECT|INPUT|TEXTAREA)$/.test(a.tagName);
}

/** Un refresco cada vez y se pinta siempre lo que llega. Antes, una respuesta que llegaba cuando ya había salido otra
 *  petición se tiraba: con el servidor cargado (cada una tardando más de 2,5 s) se tiraban todas y la vista se quedaba
 *  congelada (el número de partidas parado, «Detener» sin «Deteniendo…»). El del temporizador se salta si hay uno en
 *  marcha; uno pedido a mano (tras pulsar un botón) se hace en cuanto acaba el que está en marcha. */
let refreshing = false;
let refreshAgain = false;
let sourcesLoading = false;
async function refresh({ timer = false } = {}) {
  if (refreshing) {
    if (!timer) refreshAgain = true;
    return;
  }
  refreshing = true;
  try {
    await refreshOnce();
  } finally {
    refreshing = false;
    if (refreshAgain && root) {
      refreshAgain = false;
      refresh();
    }
  }
}

async function refreshOnce() {
  try {
    // Las fuentes, aparte (una cada vez): si tardan, no retrasan el estado del recolector.
    if (!sourcesLoading) {
      sourcesLoading = true;
      api
        .get("/api/sources/status")
        .then((sources) => root && sources && renderSources(sources))
        .catch(() => null)
        .finally(() => (sourcesLoading = false));
    }
    const status = await api.get("/api/system/status");
    if (!root) return;
    state.status = status;
    // No se repinta una sección mientras el usuario está escribiendo o eligiendo en ella.
    const busy = (el) => el.contains(document.activeElement) && document.activeElement !== document.body;
    if (!busy(sections.key)) renderKey(status.key);
    renderWarmer(status.warmer);
    limitsNow = status.ratelimit || {};
    renderCrawler(status.crawler, status.key);
    renderLimits(status.ratelimit);
    loadOtps(status.crawler);
    loadLive();
    renderDb(status);
    if (!busy(sections.model)) renderModel(status.model);
  } catch (err) {
    mount(sections.key, errorBox(err));
  }
}

// ------------------------------------------------------------------ key
let keyRendered = null;

function keyResultToast(res, okText) {
  const reason = res.check?.reason || "";
  if (res.valid) toast(okText, "success");
  else if (res.valid === false && res.key?.rechecking)
    toast(`Riot aún no acepta la key (${reason}). Si la acabas de generar puede tardar unos minutos en activarse: la vuelvo a comprobar sola.`, "error", 10000);
  else if (res.valid === false) toast(`Riot rechaza la key: ${reason}`, "error", 10000);
  else toast(`No se pudo comprobar con Riot (no es un rechazo de la key): ${reason}`, "error", 10000);
}

function renderKey(key) {
  const signature = JSON.stringify([key.status, key.masked, Math.round(key.age_hours || 0), key.rechecking, key.detail]);
  if (keyRendered === signature && sections.key.childElementCount) return;
  keyRendered = signature;
  const input = h("input.input", { placeholder: "RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", type: "password", autocomplete: "off", spellcheck: "false", "aria-label": "API key de Riot" });
  const save = async () => {
    if (!input.value.trim()) {
      input.focus();
      return toast("Pega primero la key (empieza por RGAPI-)", "error");
    }
    try {
      const res = await api.post("/api/system/riot-key", { key: input.value.trim() });
      keyResultToast(res, "API key guardada y válida");
      input.value = "";
      input.blur();
      window.dispatchEvent(new Event("tempu:status"));
      keyRendered = null;
      refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  };
  const validate = async () => {
    try {
      const res = await api.post("/api/system/validate-key");
      keyResultToast(res, "La key funciona");
      keyRendered = null;
      refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  };
  input.addEventListener("keydown", (e) => e.key === "Enter" && save());
  const statusPill = {
    valid: h("span.pill.pill-good.pill-dot", "Válida"),
    invalid: h("span.pill.pill-bad.pill-dot", "Caducada o inválida"),
    unknown: h("span.pill.pill-warn.pill-dot", "Sin validar"),
    missing: h("span.pill.pill-warn.pill-dot", "Sin configurar"),
  }[key.status];
  const remaining = key.age_hours !== null ? 24 - key.age_hours : null;
  let ageText = null;
  if (remaining !== null && remaining > 0) ageText = `desde ${fmt.ago(key.loaded_at)} · una development key dura 24 h (quedan ~${remaining < 1 ? "menos de 1" : fmt.num(remaining, 0)} h)`;
  else if (remaining !== null && key.status === "valid") ageText = `desde ${fmt.ago(key.loaded_at)} · sigue funcionando pasadas 24 h (las personal keys no caducan)`;
  else if (remaining !== null) ageText = `desde ${fmt.ago(key.loaded_at)} · una development key caduca a las 24 h: si Riot la rechaza, genera otra`;
  mount(
    sections.key,
    h("div.row-wrap", statusPill, key.masked ? h("code.small.muted", key.masked) : null, ageText ? h("span.small.muted", ageText) : null),
    key.detail ? h("div.small.bad", { style: { marginTop: "6px" } }, key.detail) : null,
    key.rechecking
      ? notice("Si la acabas de generar, Riot puede tardar unos minutos en activarla. Se vuelve a comprobar sola cada poco tiempo; no hace falta que hagas nada.", "info")
      : key.status === "invalid"
        ? h("p.small.muted", { style: { marginTop: "6px" } }, "Comprueba que has copiado la key completa y la última que generaste (al regenerar, la anterior deja de valer).")
        : null,
    h("div.input-group", { style: { marginTop: "12px" } }, input, h("button.btn.btn-primary", { onclick: save }, "Guardar"), key.configured ? h("button.btn", { onclick: validate }, "Validar") : null),
    h("p.small.muted", { style: { marginTop: "10px" } }, "Consíguela en ", h("a", { href: "https://developer.riotgames.com/", target: "_blank", rel: "noopener" }, "developer.riotgames.com"), ". Se guarda en tu .env local y se recarga en caliente: no hace falta reiniciar. Sin key siguen funcionando el draft, los matchups y la tier list con OP.GG y Lolalytics."),
  );
}

// ------------------------------------------------------------------ precarga
const PAUSE_TEXT = { in_game: "En pausa: estás en partida (sigue sola al terminar)", manual: "En pausa (la has pausado tú)" };

function renderWarmer(w) {
  if (!w) return mount(sections.warmer, h("div.small.muted", "Sin información."));
  if (!w.enabled) {
    return mount(sections.warmer, notice("La precarga está desactivada (PRELOAD_ENABLED=0 en .env): los datos se descargarán al abrir cada vista.", "warn"));
  }
  const plan = w.plan || {};
  const lanes = Object.entries(w.lanes || {}).filter(([name, l]) => name !== "demand" || l.pending || l.current);
  const bar = (label, pct, sub) =>
    h("div.usage", h("span.small", label), h("div.track", h("span", { style: { width: `${pct}%` } })), h("span.small.right", sub ?? `${pct} %`));
  const pauseBtn = w.pause_reason === "manual"
    ? h("button.btn.btn-sm", { onclick: () => setPaused(false) }, icon("play"), "Reanudar")
    : h("button.btn.btn-ghost.btn-sm", { onclick: () => setPaused(true), title: "Detener las descargas de fondo hasta que la reanudes" }, icon("pause"), "Pausar");
  mount(
    sections.warmer,
    h(
      "div.row-wrap",
      w.paused ? h("span.pill.pill-warn.pill-dot", PAUSE_TEXT[w.pause_reason] || "En pausa") : w.essential_pct < 100 ? h("span.pill.pill-gold.pill-dot", "Descargando lo esencial") : h("span.pill.pill-good.pill-dot", "Todo listo para usar"),
      h("span.spacer"),
      pauseBtn,
      h("button.btn.btn-ghost.btn-sm", { onclick: replan, title: "Revisar ya qué falta y reintentar lo que falló" }, icon("refresh"), "Revisar ahora"),
    ),
    h(
      "div.col",
      { style: { marginTop: "12px", gap: "6px" } },
      bar("Esencial", w.essential_pct, `${fmt.int(plan.essential_done || 0)}/${fmt.int(plan.essential_total || 0)}`),
    ),
    plan.phase ? h("div.tiny.muted", { style: { marginTop: "6px" } }, plan.phase) : null,
    h("div", { style: { marginTop: "10px" } }, collapsible(
      "datos.precarga.detalle",
      [icon("activity"), "Detalle por web"],
      h(
        "div.col",
        { style: { gap: "8px" } },
        bar("Todo", w.all_pct, `${fmt.int(plan.done || 0)}/${fmt.int(plan.total || 0)}`),
    h(
      "div.lanes",
      lanes.map(([name, l]) =>
        h(
          "div.lane",
          h("div.row", h("b.small", l.label), h("span.spacer"), h("span.tiny.muted", `${fmt.int(l.done)} hechas${l.pending ? ` · ${fmt.int(l.pending)} en cola` : ""}${l.rate_per_min ? ` · ${fmt.int(l.rate_per_min)}/min` : ""}`)),
          h("div.tiny.faint.ellipsis", l.current || (l.pending ? "esperando turno…" : "al día")),
          l.errors ? h("div.tiny.warn", `${l.errors} errores (se reintentan solos)`) : null,
        ),
      ),
    ),
    h(
      "p.tiny.muted",
      { style: { marginTop: "10px" } },
      "Se descargan solas, a ritmo tranquilo y por orden de importancia: tus campeones y matchups, después tier lists y counters con los filtros que usas, y al final sinergias y guías. Así cada vista abre al instante. Mientras juegas (cliente de LoL abierto en este ordenador) se pausa para no quitarte ancho de banda.",
    ),
    w.scopes?.length
      ? h("div.tiny.faint", "Filtros que se mantienen al día: ", w.scopes.map((s) => `${s.tier.replace("_plus", "+")} · ${s.platform === "all" ? "global" : platformLabel(s.platform)} · ${s.patch === "all" ? "actual + anterior" : s.patch}`).join(" | "))
      : null,
      ),
      { cls: "flat", summary: `${fmt.int(plan.done || 0)} de ${fmt.int(plan.total || 0)} descargas · ${lanes.length} webs` },
    )),
  );
}

async function setPaused(paused) {
  try {
    await api.post("/api/warmer/pause", { paused });
    toast(paused ? "Descargas en segundo plano en pausa" : "Descargas reanudadas", "success");
    refresh();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function replan() {
  try {
    await api.post("/api/warmer/replan");
    toast("Revisando qué falta por descargar…");
    refresh();
  } catch (err) {
    toast(err.message, "error");
  }
}

// ------------------------------------------------------------------ recolector
function renderCrawler(c, key) {
  const running = !!c.running;
  const mode = running ? "running" : "stopped";
  if (!crawlerForm) crawlerForm = JSON.parse(JSON.stringify(c.config));
  const sig = JSON.stringify([c.stored, c.rate_per_min, c.rate_recent, c.stopping, c.paused, c.autostart, key.configured]);
  if (crawlerFormFor !== mode) {
    crawlerFormFor = mode;
    runningSig = sig;
    mount(sections.crawlerForm, crawlerPanel(c, key));
  } else if (running && sig !== runningSig) {
    runningSig = sig;
    mount(sections.crawlerForm, crawlerPanel(c, key));
  }
  renderCrawlerStatus(c, key);
  // Resultados: solo con el recolector en marcha (al cargar o parado no se enseña nada viejo). La gráfica por elo, siempre;
  // la búsqueda (servidores, jugadores encontrados, GitHub), solo buscando por campeones o líneas.
  const resultsFor = running ? (hasTargets(c.config) ? c.config.focus : "ranking") : null;
  if (resultsFor !== resultsMode) {
    resultsMode = resultsFor;
    otpsSig = null;
    otpsAt = 0;
    eloSig = null;
    mount(
      sections.results,
      resultsFor
        ? h(
            "div",
            { style: { marginTop: "14px" } },
            (eloBox = h("div.elo-chart-box", eloChartShell())),
            resultsFor !== "ranking"
              ? collapsible("datos.otps", [icon("crown"), RESULTS_TITLE[resultsFor]], sections.otps, { summary: "por servidor, jugadores encontrados y subida a GitHub", cls: "flat", open: true })
              : null,
          )
        : null,
    );
  }
}

const MASTERY_STEPS = [10_000, 20_000, 50_000, 100_000, 200_000, 300_000, 500_000, 1_000_000, 2_000_000];
const OTP_DEPTHS = [
  { value: "30", label: "Últimos 30 días" },
  { value: "90", label: "Últimos 90 días" },
  { value: "180", label: "Últimos 180 días" },
  { value: "season", label: "Temporada actual" },
  { value: "365", label: "Último año" },
  { value: "all", label: "Todo lo que guarde Riot" },
];
const PER_OTP = [10, 20, 30, 50, 100].map((n) => ({ value: n, label: `sus ${n} partidas más recientes` })).concat([{ value: 0, label: "todas sus partidas" }]);
const PER_PLAYER = [10, 20, 30, 50, 100].map((n) => ({ value: n, label: `sus ${n} partidas más recientes` }));
const RECRAWL = [3, 6, 12, 24, 48, 72, 168].map((n) => ({ value: n, label: n < 24 ? `${n} horas` : n === 168 ? "semana" : `${n / 24} días` }));
const QUEUES = [
  { value: "solo", label: "Solo SoloQ" },
  { value: "flex", label: "SoloQ y Flex" },
];
const PLAYERS = [
  { value: "games", label: "Cualquiera que lo juegue", hint: "Toda partida en la que alguien juegue ese campeón en esa línea, sea quien sea: más datos de sus builds y de todos sus matchups." },
  { value: "specialist", label: "Especialistas", hint: "Jugadores que lo dominan: con el mínimo de maestría y, si quieres, que la mayoría de sus partidas sean con él. Para aprender de ellos y compararte." },
];
// Especialistas: qué parte de sus partidas tiene que ser con ese campeón (se comprueba con sus partidas, no con la
// maestría, que es de toda la vida de la cuenta). 0 = da igual («mucha maestría»).
const SHARES = [
  { value: 0, label: "Da igual cuánto lo juegue", short: "juegue lo que juegue" },
  { value: 0.25, label: "Al menos 1 de cada 4 partidas", short: "≥ 25 % de sus partidas" },
  { value: 0.5, label: "La mitad o más (OTP)", short: "≥ 50 % de sus partidas (OTP)" },
  { value: 0.75, label: "3 de cada 4 o más (OTP estricto)", short: "≥ 75 % de sus partidas" },
];
const MASTERY_DEFAULT = { games: 10_000, otp: 100_000, mastery: 100_000 };
const isSpecialist = (focus) => focus === "otp" || focus === "mastery";
/** Cómo se llama a los jugadores encontrados según lo que buscas («OTPs» solo si buscas OTPs). */
const noun = (focus, n = 2) => (focus === "otp" ? (n === 1 ? "OTP" : "OTPs") : n === 1 ? "jugador" : "jugadores");
const RESULTS_TITLE = { otp: "OTPs encontrados", mastery: "Jugadores con mucha maestría encontrados", games: "Jugadores encontrados" };
const depthLabel = (v) => (OTP_DEPTHS.find((d) => d.value === String(v)) || OTP_DEPTHS[3]).label;
const hasTargets = (cfg) => !!(cfg?.champions?.length || cfg?.roles?.length);
const linesText = (roles) => roles.map((r) => ROLE_LABEL[r]).join(" y ");
const listText = (items, max = 4) => (items.length <= max ? items.join(", ") : `${items.slice(0, max).join(", ")} y ${items.length - max} más`);

let openRow = null; // fila de filtros abierta (solo una a la vez)
const poolCache = {};

/** Campeones que entran con «líneas enteras» (de la tier list; se pide una vez por combinación de líneas). */
async function linePool(roles) {
  const key = roles.join(",");
  if (!poolCache[key]) poolCache[key] = api.get("/api/crawler/role-pool", { roles: key }).then((r) => r.champions || []);
  return poolCache[key];
}

// ------------------------------------------------------------------ resúmenes (lo que se ve sin abrir)
function serversText(platforms) {
  const all = state.meta?.platforms || [];
  if (all.length && platforms.length === all.length) return `Todos (${all.length})`;
  return platforms.length ? listText(platforms.map((p) => platformLabel(p))) : "Ninguno";
}
function tiersText(tiers) {
  const idx = tiers.map((t) => ALL_TIERS.indexOf(t)).sort((a, b) => a - b);
  if (!idx.length) return "Ninguno";
  if (idx.length === ALL_TIERS.length) return "Todos";
  const contiguous = idx.every((v, i) => !i || v === idx[i - 1] + 1);
  if (contiguous && idx.length > 2) return `${TIER_LABEL[ALL_TIERS[idx[0]]]} – ${TIER_LABEL[ALL_TIERS[idx.at(-1)]]}`;
  return listText(idx.map((i) => TIER_LABEL[ALL_TIERS[i]]));
}
function champsText(cfg, poolSize = null) {
  const parts = [];
  if (cfg.roles?.length) parts.push(`${cfg.roles.length > 1 ? "Líneas" : "Línea"} ${linesText(cfg.roles).toLowerCase()} completa${cfg.roles.length > 1 ? "s" : ""}${poolSize ? ` (${poolSize} campeones)` : ""}`);
  const names = (cfg.champions || []).map((id) => {
    const roles = (cfg.champion_roles || {})[String(id)] || [];
    return `${dd.name(id)}${roles.length ? ` (${roles.map((r) => ROLE_LABEL[r].toLowerCase()).join("/")})` : ""}`;
  });
  if (names.length) parts.push(listText(names, 3));
  return parts.join(" + ") || "Todos";
}
function playersText(cfg) {
  if (!hasTargets(cfg)) return "Cualquiera";
  const points = `≥ ${fmt.compact(cfg.min_mastery || MASTERY_DEFAULT[cfg.focus] || 0)} puntos`;
  if (cfg.focus === "games") return `Cualquiera que lo juegue · ${points}`;
  const share = cfg.focus === "otp" ? SHARES.find((x) => x.value === Number(cfg.min_share ?? 0.5)) : null;
  return `Especialistas · ${points}${share ? ` · ${share.short}` : ""}`;
}
function gamesText(cfg) {
  const queues = cfg.include_flex ? "SoloQ y Flex" : "Solo SoloQ";
  const per = hasTargets(cfg)
    ? cfg.games_per_otp ? `Primero ${cfg.games_per_otp} de cada jugador` : "Todas las de cada jugador"
    : `Últimas ${cfg.matches_per_player} de cada jugador`;
  const only = hasTargets(cfg) && cfg.focus !== "games" && cfg.only_target_games === false ? " · también con otros campeones" : "";
  return `${per}${only} · ${queues} · nuevas cada ${RECRAWL.find((r) => r.value === Number(cfg.recrawl_hours))?.label || `${cfg.recrawl_hours} h`}`;
}

/** Lo que va a pasar, en una frase. */
const joinY = (items) => (items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} y ${items.at(-1)}`);
const WHEN = { 30: "de los últimos 30 días", 90: "de los últimos 90 días", 180: "de los últimos 180 días", season: "de la temporada actual", 365: "del último año", all: "de todo lo que guarde Riot" };
function sentence(cfg, poolSize, running = false) {
  const verb = running ? "Se están recogiendo" : "Se recogerán";
  const all = state.meta?.platforms || [];
  const servers = all.length && cfg.platforms.length === all.length ? "todos los servidores" : joinY(cfg.platforms.map((p) => platformLabel(p))) || "ningún servidor";
  const idx = cfg.tiers.map((x) => ALL_TIERS.indexOf(x)).sort((a, b) => a - b);
  const contiguous = idx.every((v, i) => !i || v === idx[i - 1] + 1);
  const tiers = !idx.length
    ? "ningún elo"
    : idx.length === ALL_TIERS.length
      ? "cualquier elo"
      : contiguous && idx.length > 1
        ? `${TIER_LABEL[ALL_TIERS[idx[0]]]} a ${TIER_LABEL[ALL_TIERS[idx.at(-1)]]}`
        : joinY(idx.map((i) => TIER_LABEL[ALL_TIERS[i]]));
  const when = WHEN[cfg.otp_depth] || WHEN.season;
  const where = `de ${tiers} en ${servers}`;
  if (!hasTargets(cfg)) return `${verb} partidas de cualquier jugador del ranking ${where}: de cada uno, sus ${cfg.matches_per_player} más recientes ${when}.`;
  const champs = joinY([
    ...(cfg.roles.length ? [`cualquier campeón de ${linesText(cfg.roles).toLowerCase()}${poolSize ? ` (${poolSize})` : ""}`] : []),
    ...cfg.champions.map((id) => dd.name(id)),
  ]);
  if (cfg.focus === "games") return `${verb} todas las partidas ${when} ${where} en las que alguien juegue ${champs}, analizadas minuto a minuto.`;
  const who = cfg.focus === "mastery" ? "jugadores con mucha maestría" : `especialistas (${Math.round(100 * Number(cfg.min_share ?? 0.5))} % o más de sus partidas con él)`;
  const which = cfg.only_target_games !== false ? "solo sus partidas con ese campeón" : "todas sus partidas";
  return `${verb} partidas de ${who} de ${champs}, ${where}: ${which} ${when}, analizadas minuto a minuto.`;
}

// ------------------------------------------------------------------ panel
function crawlerPanel(c, key) {
  const running = !!c.running;
  const cfg = running ? c.config || {} : crawlerForm;
  cfg.champions = cfg.champions || [];
  cfg.roles = cfg.roles || [];
  cfg.champion_roles = cfg.champion_roles || {};
  const list = h("div.filter-list");
  const foot = h("div.col", { style: { gap: "10px" } });
  let poolSize = running ? cfg.role_champions?.length || null : null;

  const rows = () => [
    { id: "champs", label: "Campeones", value: champsText(cfg, poolSize), edit: () => champsEditor(cfg, changed) },
    { id: "players", label: "Jugadores", value: playersText(cfg), edit: () => playersEditor(cfg, changed), muted: !hasTargets(cfg) },
    { id: "servers", label: "Servidores", value: serversText(cfg.platforms), edit: () => serversEditor(cfg, changed), bad: !cfg.platforms.length },
    { id: "tiers", label: "Elos", value: tiersText(cfg.tiers), edit: () => tiersEditor(cfg, changed), bad: !cfg.tiers.length },
    { id: "period", label: "Periodo", value: depthLabel(cfg.otp_depth), edit: () => periodEditor(cfg, changed) },
    { id: "games", label: "Partidas", value: gamesText(cfg), edit: () => gamesEditor(cfg, changed) },
  ];

  const values = {}; // id -> nodos de cada fila: un cambio actualiza su resumen sin rehacer el editor abierto
  const refreshValues = () => {
    for (const r of rows()) {
      const node = values[r.id];
      if (!node) continue;
      node.value.textContent = r.value;
      node.value.className = `filter-value${r.muted ? " muted" : ""}`;
      node.row.classList.toggle("bad", !!r.bad);
    }
  };
  const drawRows = () => {
    if (!rows().some((r) => r.id === openRow)) openRow = null;
    mount(
      list,
      rows().map((r) => {
        const open = !running && openRow === r.id;
        const value = h(`span.filter-value${r.muted ? ".muted" : ""}`, r.value);
        const row = h(
          `button.filter-row${r.bad ? ".bad" : ""}`,
          {
            type: "button",
            disabled: running || null,
            "aria-expanded": open ? "true" : "false",
            title: running ? "Detén el recolector para cambiar los filtros" : null,
            onclick: () => {
              openRow = open ? null : r.id;
              drawRows();
            },
          },
          h("span.filter-label", r.label),
          value,
          running ? null : icon("chevron"),
        );
        values[r.id] = { value, row };
        return h(`div.filter-item${open ? ".open" : ""}`, row, open ? h("div.filter-edit", r.edit()) : null);
      }),
    );
  };
  const drawFoot = () => {
    const reason = !key.configured ? "Configura primero la API key" : !cfg.platforms.length ? "Elige al menos un servidor" : !cfg.tiers.length ? "Elige al menos un elo" : "";
    mount(
      foot,
      h("p.filter-sentence", sentence(cfg, poolSize, running)),
      running
        ? h(
            "div.row-wrap",
            h("button.btn.btn-danger", { onclick: stop, disabled: c.stopping || null }, icon("stop"), c.stopping ? "Deteniendo…" : "Detener"),
            h("span.pill.pill-good.pill-dot", c.paused === "in_game" ? "En pausa mientras juegas" : "En marcha"),
            h(
              "span.small.muted",
              { title: "«Ahora»: últimos 5 minutos. «Media»: desde que arrancó (arrastra el arranque, cuando aún busca jugadores)." },
              `${fmt.int(c.stored)} partidas nuevas`,
              c.rate_recent !== null && c.rate_recent !== undefined ? ` · ahora ${fmt.num(c.rate_recent, 1)}/min` : "",
              c.rate_per_min ? ` · media ${fmt.num(c.rate_per_min, 1)}/min` : "",
            ),
            regionsHint(c),
          )
        : h(
            "div.row-wrap",
            (() => {
              const btn = h("button.btn.btn-primary", { onclick: () => start(btn), disabled: reason ? true : null, title: reason || null }, icon("play"), "Iniciar recolector");
              return btn;
            })(),
            h("span.tiny.muted", reason || "Se reanuda solo cada vez que abras la app, hasta que lo detengas."),
          ),
      running ? h("p.tiny.muted", { style: { margin: 0 } }, "Para cambiar los filtros, detenlo: vuelven con tu última configuración.") : null,
    );
  };
  let poolFor = null;
  function changed({ rebuild = false } = {}) {
    if (rebuild) drawRows(); // el editor abierto tiene que verse de nuevo (p. ej. «Quitar todos»)
    // Cuántos campeones entran con las líneas elegidas (tier list; en caché tras la primera vez).
    const keyRoles = cfg.roles.join(",");
    if (!running && keyRoles !== poolFor) {
      poolFor = keyRoles;
      poolSize = null;
      if (keyRoles) {
        linePool(cfg.roles)
          .then((pool) => {
            if (cfg.roles.join(",") !== keyRoles) return;
            poolSize = pool.length;
            refreshValues();
            drawFoot();
          })
          .catch(() => {});
      }
    }
    refreshValues();
    drawFoot();
  }
  drawRows();
  changed();
  return h("div.col", { style: { gap: "12px" } }, !key.configured ? notice("Necesitas una API key para recoger partidas propias. OP.GG y Lolalytics ya cubren los matchups sin ella.", "warn") : null, list, foot);
}

// ------------------------------------------------------------------ editores (lo que se ve al pulsar una fila)
function choiceList(options, value, onChange) {
  return h(
    "div.choice-list",
    { role: "radiogroup" },
    options.map((o) =>
      h(
        `button.choice${o.value === value ? ".on" : ""}`,
        { type: "button", role: "radio", "aria-checked": o.value === value ? "true" : "false", onclick: () => onChange(o.value) },
        h("span.choice-dot"),
        h("span.col", { style: { gap: "2px" } }, h("b.small", o.label), o.hint ? h("span.tiny.muted", o.hint) : null),
      ),
    ),
  );
}
const editHint = (text) => h("p.tiny.muted", { style: { margin: 0 } }, text);
const inlineRow = (...children) => h("div.row-wrap.small", { style: { gap: "6px" } }, ...children);

function champsEditor(cfg, changed) {
  const preview = h("div.tiny.muted");
  const loadPreview = () => {
    if (!cfg.roles.length) return mount(preview, "");
    const keyRoles = cfg.roles.join(",");
    mount(preview, "Mirando qué campeones se juegan ahí…");
    linePool(cfg.roles)
      .then((champs) => {
        if (cfg.roles.join(",") !== keyRoles) return;
        if (!champs.length) return mount(preview, "Aún no hay tier list de esa línea (OP.GG o Lolalytics): vuelve a probar en un momento.");
        const shown = champs.slice(0, 14);
        mount(
          preview,
          h("div.row-wrap", { style: { gap: "4px" }, title: champs.map((ch) => dd.name(ch)).join(", ") }, `${champs.length} campeones (de más a menos jugados):`, shown.map((ch) => champImg(ch, "xs")), champs.length > shown.length ? h("span", `+${champs.length - shown.length}`) : null),
        );
      })
      .catch((err) => mount(preview, `No se pudo saber qué campeones entran: ${err.message}`));
  };
  const lines = h("div.role-toggles", { role: "group", "aria-label": "Líneas enteras" });
  const drawLines = () =>
    mount(
      lines,
      ROLES.map((r) =>
        h(
          `button.role-toggle${cfg.roles.includes(r) ? ".on" : ""}`,
          {
            type: "button",
            "aria-pressed": cfg.roles.includes(r) ? "true" : "false",
            onclick: () => {
              cfg.roles = cfg.roles.includes(r) ? cfg.roles.filter((x) => x !== r) : ROLES.filter((x) => x === r || cfg.roles.includes(x));
              drawLines();
              loadPreview();
              changed();
            },
          },
          roleIcon(r),
          h("span", ROLE_LABEL[r]),
        ),
      ),
    );
  drawLines();
  loadPreview();
  return h(
    "div.col",
    { style: { gap: "12px" } },
    editHint("Sin campeones se recogen partidas de todo el ranking. Puedes añadir líneas enteras, campeones sueltos o las dos cosas: se suman."),
    h("div.col", { style: { gap: "6px" } }, h("div.small.muted", "Líneas enteras (todos los campeones que se juegan en ellas, y solo sus partidas en esa línea)"), lines, preview),
    h("div.col", { style: { gap: "6px" } }, h("div.small.muted", "Campeones sueltos (marca su rol si quieres; sin marcar, cualquiera)"), champsPicker(cfg, changed)),
    h(
      "div",
      h(
        "button.btn.btn-ghost.btn-sm",
        {
          type: "button",
          onclick: () => {
            Object.assign(cfg, { champions: [], roles: [], champion_roles: {} });
            openRow = "champs";
            changed({ rebuild: true });
          },
        },
        icon("close"),
        "Quitar todos (volver a todo el ranking)",
      ),
    ),
  );
}

/** Campeones sueltos: buscador y una ficha por campeón con sus roles. */
function champsPicker(cfg, onChange) {
  const chipsEl = h("div.col", { style: { gap: "6px" } });
  const rolesOf = (id) => cfg.champion_roles[String(id)] || [];
  const toggleRole = (id, role) => {
    const now = rolesOf(id);
    const next = now.includes(role) ? now.filter((r) => r !== role) : ROLES.filter((r) => r === role || now.includes(r));
    if (next.length && next.length < ROLES.length) cfg.champion_roles[String(id)] = next;
    else delete cfg.champion_roles[String(id)]; // ninguno o los 5 = cualquier rol
    drawChips();
    onChange();
  };
  const remove = (id) => {
    cfg.champions = cfg.champions.filter((ch) => ch !== id);
    delete cfg.champion_roles[String(id)];
    drawChips();
    onChange();
  };
  const drawChips = () =>
    mount(
      chipsEl,
      cfg.champions.map((id) => {
        const roles = rolesOf(id);
        return h(
          "div.otp-champ",
          h("span.row", { style: { gap: "6px", minWidth: 0 } }, champImg(id, "xs"), h("b.small.ellipsis", dd.name(id))),
          h(
            "div.role-toggles",
            { role: "group", "aria-label": `Rol de ${dd.name(id)}` },
            ROLES.map((r) =>
              h(`button.role-toggle${roles.includes(r) ? ".on" : ""}`, { type: "button", title: ROLE_LABEL[r], "aria-pressed": roles.includes(r) ? "true" : "false", onclick: () => toggleRole(id, r) }, roleIcon(r), h("span", ROLE_LABEL[r])),
            ),
          ),
          h("button.btn.btn-ghost.btn-sm", { type: "button", title: `Quitar ${dd.name(id)}`, onclick: () => remove(id) }, icon("close")),
        );
      }),
    );
  const picker = champPicker({
    placeholder: "Añadir campeón",
    compact: true,
    exclude: () => new Set(cfg.champions),
    onChange: (id) => {
      if (id && !cfg.champions.includes(id)) cfg.champions = [...cfg.champions, id];
      picker.setValue(null);
      drawChips();
      onChange();
    },
  });
  drawChips();
  return h("div.col", { style: { gap: "8px" } }, h("div", { style: { width: "220px", maxWidth: "100%" } }, picker), chipsEl);
}

function playersEditor(cfg, changed) {
  if (!hasTargets(cfg)) return editHint("Sin campeones elegidos se recogen partidas de cualquier jugador del ranking. Elige campeones (fila de arriba) para buscar especialistas o cualquier partida de esos campeones.");
  const box = h("div.col", { style: { gap: "10px" } });
  if (cfg.min_share == null) cfg.min_share = cfg.focus === "mastery" ? 0 : 0.5;
  const setShare = (share) => {
    cfg.min_share = share;
    cfg.focus = share > 0 ? "otp" : "mastery";
  };
  const draw = () =>
    mount(
      box,
      choiceList(PLAYERS, isSpecialist(cfg.focus) ? "specialist" : "games", (v) => {
        if (v === "games") cfg.focus = "games";
        else setShare(cfg.min_share > 0 ? cfg.min_share : 0.5);
        if (!cfg.min_mastery) cfg.min_mastery = MASTERY_DEFAULT[cfg.focus];
        draw();
        changed();
      }),
      inlineRow(
        "Con al menos",
        select(MASTERY_STEPS.map((n) => ({ value: n, label: `${fmt.compact(n)} puntos` })), cfg.min_mastery || MASTERY_DEFAULT[cfg.focus], (v) => {
          cfg.min_mastery = Number(v);
          changed();
        }, "select select-sm"),
        "de maestría con él (entre sus 3 campeones con más puntos)",
      ),
      isSpecialist(cfg.focus)
        ? inlineRow(
            "Y que juegue con él",
            select(SHARES.map((x) => ({ value: x.value, label: x.label })), cfg.focus === "otp" ? Number(cfg.min_share) : 0, (v) => {
              setShare(Number(v));
              changed();
            }, "select select-sm"),
            "",
          )
        : null,
      editHint(
        cfg.focus === "games"
          ? "Riot no deja buscar partidas por campeón: se buscan jugadores del ranking que lo tengan entre sus 3 más jugados (con ese mínimo de puntos) y se guarda toda partida suya en la que alguien lo juegue en esa línea."
          : "Riot no deja buscar por campeón: se mira la maestría de cada jugador del ranking (1 llamada por jugador, se recuerda 7 días) y la tabla de los mejores de cada campeón de OP.GG. La maestría es de toda la vida de la cuenta, así que cuánto lo juega se comprueba con sus partidas: tras revisar 20, si no llega, se deja de seguirle y sus partidas cuentan como de un jugador normal, no de OTP.",
      ),
    );
  draw();
  return box;
}

function serversEditor(cfg, changed) {
  const platforms = state.meta?.platforms || [];
  return h(
    "div.col",
    { style: { gap: "8px" } },
    editHint("Cada región de Riot (Europa, América, Asia, SEA) se recoge en paralelo. Pulsa el nombre de una región para marcarla entera."),
    multiSelect({
      items: platforms.map((p) => ({ value: p.id, label: p.label, title: p.name, group: p.region })),
      groups: REGION_GROUPS,
      selected: cfg.platforms,
      noun: "servidores",
      presets: [{ label: "Solo EUW", values: ["euw1"] }],
      onChange: (v) => {
        cfg.platforms = v;
        changed();
      },
    }).el,
  );
}

function tiersEditor(cfg, changed) {
  return multiSelect({
    items: ALL_TIERS.map((t) => ({ value: t, label: TIER_LABEL[t] })),
    selected: cfg.tiers,
    noun: "elos",
    presets: [
      { label: "Esmeralda+", values: ALL_TIERS.slice(ALL_TIERS.indexOf("EMERALD")) },
      { label: "Diamante+", values: ALL_TIERS.slice(ALL_TIERS.indexOf("DIAMOND")) },
      { label: "Ápice", values: APEX },
    ],
    onChange: (v) => {
      cfg.tiers = v;
      changed();
    },
  }).el;
}

function periodEditor(cfg, changed) {
  const box = h("div.col", { style: { gap: "10px" } });
  const draw = () =>
    mount(
      box,
      choiceList(OTP_DEPTHS, String(cfg.otp_depth || "season"), (v) => {
        cfg.otp_depth = v;
        draw();
        changed();
      }),
      editHint(
        hasTargets(cfg)
          ? "Cuentan los jugadores que han jugado ese campeón en este periodo, y se bajan sus partidas de este mismo periodo."
          : "La primera vez que se mira a un jugador, sus partidas de este periodo (las más recientes; ver «Partidas»). Después, solo las nuevas.",
      ),
    );
  draw();
  return box;
}

function gamesEditor(cfg, changed) {
  const sel = (options, value, onPick) => select(options, value, (v) => {
    onPick(v);
    changed();
  }, "select select-sm");
  return h(
    "div.col",
    { style: { gap: "10px" } },
    hasTargets(cfg)
      ? h(
          "div.col",
          { style: { gap: "4px" } },
          inlineRow("Primero, de cada jugador,", sel(PER_OTP, cfg.games_per_otp ?? 30, (v) => (cfg.games_per_otp = Number(v)))),
          editHint("Variedad antes que cantidad: primero un jugador de cada campeón, luego al menos 3 por campeón en cada elo y, cuando ya no aparezcan jugadores nuevos, el resto de sus partidas del periodo."),
        )
      : inlineRow("De cada jugador,", sel(PER_PLAYER, cfg.matches_per_player, (v) => (cfg.matches_per_player = Number(v)))),
    hasTargets(cfg) && cfg.focus !== "games"
      ? h(
          "label.check",
          { title: "Desmarcado, también se guardan sus partidas con otros campeones (sirven para las estadísticas generales, no para aprender de ese campeón)." },
          h("input", {
            type: "checkbox",
            checked: cfg.only_target_games !== false,
            onchange: (e) => {
              cfg.only_target_games = e.target.checked;
              changed();
            },
          }),
          "Solo sus partidas con ese campeón (y en esa línea)",
        )
      : null,
    inlineRow("Colas:", sel(QUEUES, cfg.include_flex ? "flex" : "solo", (v) => (cfg.include_flex = v === "flex"))),
    inlineRow("Buscar sus partidas nuevas cada", sel(RECRAWL, Number(cfg.recrawl_hours), (v) => (cfg.recrawl_hours = Number(v)))),
  );
}

/** Con la key gratuita, cada región (Europa, América, Asia, SEA) tiene su propio cupo: con una sola, ~25 partidas/min. */
function regionsHint(c) {
  const regions = Object.keys(c.workers || {});
  if (regions.length !== 1) return null;
  return h(
    "div.tiny.muted",
    { style: { flexBasis: "100%" } },
    `Va al máximo de la key en una sola región (100 peticiones cada 2 min; cada partida con su timeline son 2). Cada región tiene su propio cupo: añade servidores de ${REGION_GROUPS.filter((r) => r.id !== regions[0]).map((r) => r.label).join(", ")} para recoger hasta 4 veces más rápido.`,
  );
}

/** Lo que se envía al pulsar «Iniciar». */
function startPayload() {
  const cfg = { ...crawlerForm };
  if (!hasTargets(cfg)) cfg.focus = "otp"; // sin campeones da igual: se recoge todo el ranking
  if (cfg.focus === "games") cfg.only_target_games = true;
  delete cfg.save_details; // siempre activo: no se elige
  delete cfg.role_champions;
  delete cfg.season_version;
  return cfg;
}

/** « (N/min)»: jugadores revisados por minuto en esa región (todas sus plataformas a la vez). */
function searchRate(w) {
  const minutes = w.started_at ? (Date.now() / 1000 - w.started_at) / 60 : 0;
  return minutes >= 1 && w.mastery_checked ? ` (${fmt.num(w.mastery_checked / minutes, 0)}/min)` : "";
}

function renderCrawlerStatus(c, key) {
  // Solo con el recolector en marcha: parado, los números de la última vez confunden.
  const workers = c.running ? Object.values(c.workers || {}) : [];
  const focus = hasTargets(c.config) ? c.config.focus : null;
  mount(
    sections.crawlerStatus,
    h(
      "div.col",
      workers.length
        ? workers.map((w) =>
            h(
              "div.worker",
              h("div.row", h("b", { style: { textTransform: "capitalize" } }, w.region), h(`span.pill.${w.status === "running" ? "pill-good" : w.status === "waiting_key" || w.status === "paused_game" ? "pill-warn" : ""}`, STATUS_LABEL[w.status] || w.status), h("span.spacer"), h("span.tiny.muted", w.platform ? `${platformLabel(w.platform)} · ${TIER_LABEL[w.tier] || w.tier || ""}` : "")),
              // Lo esencial a la vista; el resto, en «Detalle».
              h(
                "div.small",
                { style: { marginTop: "4px" } },
                w.search
                  ? `${fmt.int(w.otps_known)} ${noun(focus, w.otps_known)} · ${fmt.int(w.stored)} partidas nuevas${w.rate_recent ? ` (${fmt.num(w.rate_recent, 1)}/min)` : ""} · ${fmt.int(w.mastery_checked)} revisados${searchRate(w)}`
                  : `${fmt.int(w.stored)} partidas guardadas · ${fmt.int(w.players)} jugadores`,
              ),
              sharedKeyLine(w),
              w.last_error ? h("div.tiny.warn", w.last_error) : null,
              collapsible(
                `datos.recolector.detalle.${w.region}`,
                "Detalle",
                h(
                  "div.col",
                  { style: { gap: "2px" } },
                  h("div.tiny.muted", `${fmt.int(w.stored)} guardadas · ${fmt.int(w.duplicates)} repetidas · ${fmt.int(w.remakes)} remakes · ${fmt.int(w.players)} jugadores · ${fmt.int(w.seeds_added)} jugadores del ranking`),
                  w.search
                    ? h("div.tiny.muted", `${noun(focus)[0].toUpperCase()}${noun(focus).slice(1)}: ${fmt.int(w.otps_known)} encontrados · ${fmt.int(w.otps_complete)} con todas sus partidas del periodo · búsqueda ${SEARCH_LABEL[w.search] || w.search}`)
                    : null,
                  w.mastery_checked || w.search
                    ? h("div.tiny.muted", `Revisados: ${fmt.int(w.mastery_checked)} jugadores del ranking · ${fmt.int(w.targets_found)} ${noun(focus)} nuevos · ${fmt.int(w.target_games)} partidas con esos campeones${w.other_champ_skipped ? ` · ${fmt.int(w.other_champ_skipped)} descartadas (otro campeón)` : ""}`)
                    : null,
                  w.details ? h("div.tiny.muted", `${fmt.int(w.details)} partidas analizadas minuto a minuto`) : null,
                ),
                { cls: "flat" },
              ),
            ),
          )
        : h("div.small.muted", key.configured ? "Sin actividad todavía: revisa los filtros y pulsa «Iniciar recolector»." : "Sin actividad."),
      h("p.tiny.muted", { title: "Coste con la key gratuita: ~1 llamada por partida nueva (2 buscando por campeones: también su análisis minuto a minuto). Los jugadores salen de las ligas oficiales (205 por llamada) y se descartan partidas repetidas, remakes y colas que no son ranked. Se pausa solo mientras juegas." }, "Cuánto cuesta y cuándo se pausa ⓘ"),
    ),
  );
}

async function start(btn) {
  btn.disabled = true;
  try {
    await api.post("/api/crawler/start", startPayload());
    toast("Recolector en marcha", "success");
    crawlerFormFor = null;
    refresh();
  } catch (err) {
    toast(err.message, "error");
    btn.disabled = false;
  }
}

async function stop(ev) {
  // Al momento, sin esperar al servidor: los hilos acaban lo que tienen entre manos (una descarga, guardar una partida).
  const btn = ev?.currentTarget;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Deteniendo…";
  }
  toast("Deteniendo el recolector: termina la partida que tiene entre manos…");
  try {
    await api.post("/api/crawler/stop");
    refresh();
  } catch (err) {
    toast(err.message, "error");
    if (btn) btn.disabled = false;
  }
}

// ------------------------------------------------------------------ OTPs encontrados
/** « de jungla o mid» si ese campeón se busca solo en algunos roles. */
const rolesText = (champRoles, champ) => {
  const roles = (champRoles || {})[String(champ)] || [];
  return roles.length ? ` de ${roles.map((r) => ROLE_LABEL[r].toLowerCase()).join(" o ")}` : "";
};
const OTP_PAGE = 25;
const OTP_SORTS = [
  { value: "games", label: "Más partidas" },
  { value: "elo", label: "Más elo" },
  { value: "winrate", label: "Mejor winrate (≥ 5)" },
  { value: "points", label: "Más maestría" },
  { value: "name", label: "Nombre" },
];
const riotId = (p) => (p.game_name ? `${p.game_name}#${p.tag_line}` : null);
const eloText = (p) => `${TIER_LABEL[p.tier] || p.tier || "—"}${p.division && !["MASTER", "GRANDMASTER", "CHALLENGER"].includes(p.tier) ? ` ${p.division}` : ""}${p.lp != null ? ` · ${fmt.int(p.lp)} LP` : ""}`;
const wrClass = (wr) => (wr === null ? "muted" : wr >= 55 ? "good" : wr < 47 ? "bad" : "");

/** Barra con el reparto de partidas por rol. */
function roleBar(roles, total) {
  const entries = Object.entries(roles || {}).filter(([, n]) => n > 0);
  if (!entries.length || !total) return null;
  return h(
    "div.otp-rolebar",
    { title: entries.map(([r, n]) => `${ROLE_LABEL[r] || r}: ${fmt.pct((100 * n) / total, 0)}`).join(" · ") },
    entries.map(([r, n]) => h(`span.seg.role-${r.toLowerCase()}`, { style: { flexGrow: n } })),
  );
}

let otpsAt = 0;
let otpsSig = null;
let otpsLoading = false;
async function loadOtps(c) {
  const box = sections.otps;
  const details = box.closest("details");
  // Una cada vez: con el servidor cargado tarda más de 10 s y se iban acumulando varias a la vez (lo frenaban más).
  if (!details?.open || otpsLoading || Date.now() - otpsAt < 10000) return;
  otpsAt = Date.now();
  if (!c?.running || !hasTargets(c?.config)) return; // sin búsqueda en marcha, la sección ni se muestra
  let data;
  otpsLoading = true;
  try {
    data = await api.get("/api/crawler/otps", { limit: 1 });
  } catch (err) {
    return mount(box, errorBox(err));
  } finally {
    otpsLoading = false;
    otpsAt = Date.now();
  }
  const sig = JSON.stringify([data.found, data.complete, data.games, data.by_champion, data.details, data.funnel?.total]);
  if (sig === otpsSig) return;
  otpsSig = sig;
  mount(
    box,
    h(
      "div.col",
      { style: { gap: "10px" } },
      h(
        "div.row-wrap",
        h(
          "div.tiny.muted",
          { style: { flex: "1 1 auto" } },
          `${fmt.int(data.found)} ${noun(data.focus, data.found)} · ${fmt.int(data.games || 0)} partidas con esos campeones · `,
          data.searching
            ? h(
                "span",
                { title: "Variedad antes que cantidad: mientras siguen apareciendo jugadores nuevos, de cada uno solo se bajan sus primeras partidas; el resto de cada historial, cuando termine la búsqueda." },
                `${fmt.int(data.capped || 0)} ya con sus ${data.games_per_otp} primeras partidas (el resto de cada historial, al terminar la búsqueda) ⓘ`,
              )
            : `${fmt.int(data.complete)} con todas sus partidas del periodo`,
        ),
        h("a.btn.btn-sm", { href: "#/otps" }, icon("crown"), "Ver todas las partidas analizadas"),
      ),
      otpFunnel(data),
      ((liveSig = null), (filesBox = h("div", otpFilesBar(data)))),
    ),
  );
}

/** Partidas analizadas y subida a GitHub cada 2,5 s con una petición ligera (la lista de OTPs tarda más y se pide cada
 *  10 s: con ella el número avanzaba a saltos de 20-80). Una petición cada vez: si tarda, la siguiente espera. */
let filesBox = null;
let liveBusy = false;
let liveSig = null;
async function loadLive() {
  if ((!filesBox?.isConnected && !eloBox?.isConnected) || liveBusy) return;
  liveBusy = true;
  try {
    const details = await api.get("/api/crawler/otps/live");
    const sig = JSON.stringify(details);
    if (sig !== liveSig && filesBox?.isConnected) {
      liveSig = sig;
      mount(filesBox, otpFilesBar({ details }));
    }
    if (eloBox?.isConnected) updateEloChart(eloBox, details.autopush, resultsMode !== "ranking");
  } catch {
    // sin conexión un momento: el siguiente refresco lo intenta otra vez
  } finally {
    liveBusy = false;
  }
}

// ------------------------------------------------------------------ gráfica por elo
/** Partidas por elo desde la última subida a GitHub (vuelve a 0 al subir): barras con el icono de cada elo y el eje Y
 *  con el máximo arriba y sus cuartos. Se construye una vez; cada refresco solo cambia alturas y números (se ve subir). */
const ELO_ORDER = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"];
let eloBox = null;
let eloSig = null;

function eloChartShell() {
  return h(
    "div.elo-chart",
    h("div.elo-chart-head", h("b.elo-chart-title", "Partidas analizadas por elo"), h("span.tiny.muted.elo-chart-sub", "desde la última subida a GitHub")),
    h(
      "div.elo-chart-body",
      h("div.elo-chart-y", [4, 3, 2, 1, 0].map((q) => h("span", { "data-q": q }, "0"))),
      h(
        "div.elo-chart-plot",
        [4, 3, 2, 1].map((q) => h("div.elo-chart-grid", { style: { bottom: `${q * 25}%` } })),
        h(
          "div.elo-chart-bars",
          ELO_ORDER.map((t) =>
            h("div.elo-chart-col", { "data-tier": t, title: TIER_LABEL[t] || t }, h("span.elo-chart-n", "0"), h("div.elo-chart-bar", { style: { height: "0%" } })),
          ),
        ),
      ),
    ),
    h("div.elo-chart-x", h("span"), h("div.elo-chart-logos", ELO_ORDER.map((t) => h("div.elo-chart-logo", { title: TIER_LABEL[t] || t }, rankImg(t, "elo-chart-img"))))),
  );
}

function updateEloChart(box, autopush, analyzed) {
  const counts = ((autopush || {}).tiers || {})[analyzed ? "analizadas" : "guardadas"] || {};
  const sig = JSON.stringify([analyzed, counts]);
  if (sig === eloSig) return;
  eloSig = sig;
  const max = Math.max(0, ...ELO_ORDER.map((t) => counts[t] || 0));
  const total = ELO_ORDER.reduce((n, t) => n + (counts[t] || 0), 0);
  const title = box.querySelector(".elo-chart-title");
  if (title) title.textContent = `Partidas ${analyzed ? "analizadas" : "guardadas"} por elo: ${fmt.int(total)}`;
  const sub = box.querySelector(".elo-chart-sub");
  if (sub) sub.textContent = analyzed ? "desde la última subida a GitHub (vuelve a 0 al subir)" : "todo el ranking: sin análisis minuto a minuto · desde la última subida a GitHub";
  box.querySelectorAll(".elo-chart-y span").forEach((el) => {
    el.textContent = fmt.int(Math.round((max * Number(el.dataset.q)) / 4));
  });
  box.querySelectorAll(".elo-chart-col").forEach((col) => {
    const n = counts[col.dataset.tier] || 0;
    col.querySelector(".elo-chart-bar").style.height = max ? `${(100 * n) / max}%` : "0%";
    col.querySelector(".elo-chart-n").textContent = n ? fmt.int(n) : "";
    col.classList.toggle("top", !!max && n === max);
  });
}

/** Embudo de la búsqueda: de los jugadores revisados, cuántos pasan cada filtro (para ver por qué salen pocos OTPs). */
function otpFunnel(data) {
  const f = data.funnel;
  if (!f?.total || !f.total.checked) return null;
  const champs = data.lines?.length ? `un campeón de ${linesText(data.lines).toLowerCase()}` : (data.champions || []).map((c) => dd.name(c)).join(" / ");
  const label = {
    checked: "revisados",
    champ: `con ${champs} entre sus 3 más jugados`,
    points: `con ≥ ${fmt.compact(data.min_mastery)} puntos`,
    active: `jugado en el periodo (${depthLabel(data.otp_depth).toLowerCase()})${data.focus === "otp" ? " = candidatos" : ` = ${noun(data.focus)}`}`,
    share: `comprobados con sus partidas: ≥ ${Math.round(100 * (data.min_share ?? 0.5))} % con él = ${noun(data.focus)}`,
  };
  const last = f.stages.at(-1);
  const t = f.total;
  const steps = f.stages.map((k, i) => {
    const prev = i ? t[f.stages[i - 1]] : null;
    const pct = prev ? ` (${fmt.pct((100 * t[k]) / prev, prev && t[k] / prev < 0.1 ? 1 : 0)})` : "";
    return h("div.funnel-step", h("b", fmt.int(t[k])), h("span.tiny.muted", ` ${label[k]}${pct}`));
  });
  const rows = Object.entries(f.platforms || {}).map(([p, b]) =>
    h("tr", h("td", platformLabel(p)), h("td.num", fmt.int(b.seeds)), h("td.num", fmt.int(b.checked)), h("td.num", fmt.int(b.last_hour)), h("td.num", fmt.int(b.champ)), h("td.num", h("b", fmt.int(b[last] ?? b.active)))),
  );
  return h(
    "div.otp-export",
    h(
      "div.col",
      { style: { gap: "6px", minWidth: 0, flex: "1 1 auto" } },
      h("div.small", h("b", "Búsqueda: "), `${fmt.int(t.last_hour)} jugadores revisados en la última hora`),
      h("div.funnel", steps),
      collapsible(
        "datos.otps.embudo",
        [icon("chart"), "Por servidor"],
        h(
          "div.table-wrap",
          h("table.table.tiny", h("thead", h("tr", h("th", "Servidor"), h("th.num", "En ranking"), h("th.num", "Revisados"), h("th.num", "Última hora"), h("th.num", "Con el campeón"), h("th.num", noun(data.focus)[0].toUpperCase() + noun(data.focus).slice(1)))), h("tbody", rows)),
        ),
        { cls: "flat" },
      ),
    ),
  );
}

/** Dónde y cuánto micro/macro se ha guardado (se escribe solo: una carpeta por campeón, rol y OTP). */
function otpFilesBar(data) {
  const d = data.details || { games: 0, players: 0, timelines: 0, tiers: {} };
  const tiers = Object.entries(d.tiers || {});
  const rebuilding = d.files?.status === "running";
  return h(
    "div.otp-export",
    h(
      "div",
      { style: { minWidth: 0 } },
      h(
        "div.small",
        h("b", "Analizadas minuto a minuto: "),
        d.games ? `${fmt.int(d.games)} partidas de ${fmt.int(d.players)} jugadores (${fmt.int(d.otp_games || 0)} de OTPs)` : "aún ninguna partida",
        d.games && d.timelines < d.games ? h("span.muted", ` (${fmt.int(d.games - d.timelines)} sin timeline)`) : null,
      ),
      tiers.length ? h("div.row-wrap.tiny", { style: { gap: "4px", marginTop: "4px" } }, tiers.map(([t, n]) => h("span.chip-muted", `${TIER_LABEL[t] || t}: ${fmt.int(n)}`))) : null,
      h(
        "div.tiny.muted",
        { style: { marginTop: "4px" } },
        rebuilding
          ? `Reorganizando la carpeta… ${fmt.int(d.files.done || 0)} de ${fmt.int(d.files.total || 0)} partidas`
          : "Se guarda solo al bajar cada partida: una carpeta por campeón → rol → jugador (perfil.json y partidas.jsonl.gz, con el elo de cada partida) y un resumen.json por campeón, por rol y por elo, que se rehace solo. Las de OTPs y las de otros jugadores van por separado: las comparaciones con OTPs solo usan las de OTPs.",
      ),
      d.path ? h("div.tiny.muted.otp-path", h("b", "OTPs: "), d.path) : null,
      d.players_path ? h("div.tiny.muted.otp-path", h("b", "Otros jugadores: "), d.players_path) : null,
      autopushLine(d.autopush),
    ),
  );
}

/** Subida automática a GitHub: cuántas partidas faltan para la siguiente y cómo fue la última. */
function autopushLine(p) {
  if (!p) return null;
  if (!p.enabled) return h("div.tiny.muted", { style: { marginTop: "4px" } }, h("b", "GitHub: "), "subida automática desactivada (AUTO_PUSH_GAMES=0).");
  const left = Math.max(0, p.every - (p.pending || 0));
  const step = p.running ? p.step : null;
  const parts = [
    p.running
      ? step
        ? `subiendo · paso ${step.n} de ${step.of}: ${step.label}${step.detail ? ` (${step.detail})` : ""} · desde hace ${fmt.int(Math.max(0, Date.now() / 1000 - step.at))} s`
        : "subiendo los datos ahora…"
      : `${fmt.int(p.pending || 0)} de ${fmt.int(p.every)} partidas nuevas (se suben solas en ${fmt.int(left)})`,
  ];
  if (!p.running && p.last_push_s) parts.push(`la última tardó ${fmt.int(p.last_push_s)} s`);
  if (p.last_push_at) parts.push(`última subida ${fmt.ago(p.last_push_at)}`);
  if (p.last_result === "nothing") parts.push("la última vez no había nada nuevo en data/ (sin campeones elegidos, las partidas solo van a la base de datos)");
  return h(
    "div.tiny.muted",
    { style: { marginTop: "4px" } },
    h("b", "GitHub: "),
    parts.join(" · "),
    p.git_ok !== false && !p.running && (p.pending || 0) > 0
      ? h(
          "button.btn.btn-ghost.btn-sm",
          {
            style: { marginLeft: "8px" },
            title: "Commit «datos» y push ahora, sin esperar a llegar al límite",
            onclick: async (ev) => {
              ev.currentTarget.disabled = true;
              try {
                await api.post("/api/crawler/otps/push");
                toast("Subiendo los datos a GitHub…", "success");
              } catch (err) {
                toast(`No se pudo subir: ${err.message || err}`, "error", 8000);
              }
            },
          },
          icon("upload"),
          "Subir ahora",
        )
      : null,
    p.git_ok === false ? h("div.tiny.bad", `No se podrá subir: ${p.git}`) : p.git ? h("div.tiny.faint", p.git) : null,
    p.last_error ? h("div.tiny.bad", { title: p.last_error }, `La última subida falló: ${p.last_error.slice(0, 160)}`) : null,
  );
}

function otpChampCard(ch, data) {
  const wr = ch.games ? (100 * ch.wins) / ch.games : null;
  const wanted = ch.roles_wanted || [];
  const searching = !ch.found;
  return h(
    "button.otp-card",
    { type: "button", disabled: searching || null, onclick: () => openOtpChamp(ch, data), title: searching ? `Aún no hay ${noun(data.focus)} de este campeón` : `Ver los ${noun(data.focus)} de ${dd.name(ch.champ)}` },
    h(
      "div.otp-card-head",
      champImg(ch.champ, "lg"),
      h(
        "div",
        { style: { minWidth: 0 } },
        h("div.otp-card-name", dd.name(ch.champ)),
        h("div.tiny.muted.row", { style: { gap: "4px" } }, wanted.length ? [wanted.map((r) => roleIcon(r)), wanted.map((r) => ROLE_LABEL[r]).join(" / ")] : "Cualquier rol"),
      ),
    ),
    searching
      ? h("div.small.muted", "Buscando… se revisa la maestría del ranking al ritmo de tu API key.")
      : [
          h(
            "div.otp-card-stats",
            h("div", h("b", fmt.int(ch.found)), h("span", noun(data.focus, ch.found))),
            h("div", h("b", fmt.int(ch.games)), h("span", "partidas")),
            h("div", h(`b.${wrClass(wr)}`, wr === null ? "—" : fmt.pct(wr, 0)), h("span", "winrate")),
          ),
          roleBar(ch.roles, ch.games),
          h(
            "div.tiny.muted",
            `Con todas sus partidas del periodo: ${fmt.int(ch.complete)}/${fmt.int(ch.found)}`,
            ch.best ? ` · mejor elo: ${eloText(ch.best)}` : "",
          ),
          ch.leaders?.length && ch.leaders[0].total
            ? h(
                "div.otp-card-leaders",
                ch.leaders.filter((l) => l.total).map((l) => h("span.tiny", h("b", riotId(l) || "Sin nombre"), h("span.muted", ` · ${fmt.int(l.total)}`))),
              )
            : null,
          h("div.otp-card-cta", "Ver jugadores", icon("chevron")),
        ],
  );
}

/** Ventana con todos los OTPs de un campeón: búsqueda, filtros, orden y paginado. */
function openOtpChamp(ch, data) {
  const f = { q: "", platform: "", sort: "games" };
  let loaded = [];
  let matched = 0;
  const wr = ch.games ? (100 * ch.wins) / ch.games : null;
  const list = h("div.otp-list");
  const countEl = h("span.tiny.muted");
  const moreBtn = h("button.btn.btn-sm", { type: "button", onclick: () => load(false) }, "Ver más");
  const platforms = Object.keys(ch.platforms || {});
  let searchTimer = null;
  const search = h("input.input.input-sm", {
    type: "search",
    placeholder: "Buscar jugador…",
    "aria-label": "Buscar jugador",
    oninput: (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => ((f.q = e.target.value), load(true)), 250);
    },
  });
  const body = h(
    "div.col",
    { style: { gap: "14px" } },
    h(
      "div.kpis",
      kpi(noun(data.focus, ch.found).replace(/^./, (x) => x.toUpperCase()), fmt.int(ch.found), `${fmt.int(ch.with_games)} con partidas guardadas`),
      kpi("Partidas", fmt.int(ch.games), rolesText(data.champion_roles, ch.champ) ? `solo${rolesText(data.champion_roles, ch.champ)}` : "en cualquier rol"),
      kpi("Winrate", wr === null ? "—" : fmt.pct(wr, 1), ch.games ? `${fmt.int(ch.wins)} V · ${fmt.int(ch.games - ch.wins)} D` : null, wrClass(wr)),
      kpi("Historial", `${fmt.int(ch.complete)}/${fmt.int(ch.found)}`, "completos"),
      ch.best ? kpi("Mejor elo", TIER_LABEL[ch.best.tier] || ch.best.tier || "—", ch.best.lp != null ? `${fmt.int(ch.best.lp)} LP · ${platformLabel(ch.best.platform)}` : platformLabel(ch.best.platform)) : null,
    ),
    Object.keys(ch.roles || {}).length
      ? h(
          "div.otp-roles-line",
          roleBar(ch.roles, ch.games),
          h("div.row-wrap.tiny.muted", { style: { gap: "4px 12px" } }, Object.entries(ch.roles).map(([r, n]) => h("span.row", { style: { gap: "4px" } }, roleIcon(r), `${ROLE_LABEL[r] || r} ${fmt.pct((100 * n) / ch.games, 0)}`))),
        )
      : null,
    h(
      "div.otp-filters",
      search,
      platforms.length > 1
        ? select([{ value: "", label: "Todos los servidores" }, ...platforms.map((p) => ({ value: p, label: `${platformLabel(p)} (${ch.platforms[p]})` }))], "", (v) => ((f.platform = v), load(true)), "select select-sm")
        : null,
      select(OTP_SORTS, f.sort, (v) => ((f.sort = v), load(true)), "select select-sm"),
      countEl,
    ),
    list,
    h("div.row", { style: { justifyContent: "center" } }, moreBtn),
  );
  modal(h("div.row", { style: { gap: "12px" } }, champImg(ch.champ, "lg"), h("div", h("h3", `${noun(data.focus).replace(/^./, (x) => x.toUpperCase())} de ${dd.name(ch.champ)}`), h("div.tiny.muted", `${platforms.map(platformLabel).join(", ") || "—"} · historial: ${depthLabel(data.otp_depth).toLowerCase()}`))), body, { wide: true });

  async function load(reset) {
    if (reset) loaded = [];
    moreBtn.disabled = true;
    if (reset) mount(list, loading());
    const fresh = latest("datos.otps.modal");
    let res;
    try {
      res = await api.get("/api/crawler/otps", { champ: ch.champ, limit: OTP_PAGE, offset: loaded.length, sort: f.sort, q: f.q || undefined, platform: f.platform || undefined });
    } catch (err) {
      return mount(list, errorBox(err));
    }
    if (!fresh()) return;
    loaded = [...loaded, ...res.players];
    matched = res.matched;
    mount(list, loaded.length ? loaded.map((p, i) => otpPlayerRow(p, i + 1)) : h("div.small.muted", { style: { padding: "12px 0" } }, "Ningún jugador coincide con la búsqueda."));
    countEl.textContent = `${fmt.int(loaded.length)} de ${fmt.int(matched)}`;
    moreBtn.hidden = loaded.length >= matched;
    moreBtn.disabled = false;
  }
  load(true);
}

function otpPlayerRow(p, rank) {
  const name = riotId(p);
  const wr = p.total ? (100 * p.wins) / p.total : null;
  const historyText = p.history === "loading" && p.history_checked ? `${HISTORY_LABEL.loading} · ${fmt.int(p.history_checked)} revisadas` : HISTORY_LABEL[p.history] || "";
  const mainRole = Object.keys(p.roles || {})[0];
  return h(
    "div.otp-player",
    h("span.otp-rank", rank),
    h(
      "div.otp-player-id",
      name
        ? h("a.ellipsis", { href: `#/jugador/${p.platform}/${encodeURIComponent(p.game_name)}/${encodeURIComponent(p.tag_line)}`, onclick: () => document.querySelector(".modal-backdrop")?.remove() }, name)
        : h("span.muted", "Nombre aún desconocido"),
      h("div.tiny.muted", `${platformLabel(p.platform)} · ${eloText(p)} · ${fmt.compact(p.points)} pts`),
    ),
    h(
      "div.otp-player-games",
      h("div.small", h("b", fmt.int(p.total)), h("span.muted", p.total === 1 ? " partida" : " partidas"), wr !== null ? h(`span.${wrClass(wr)}`, ` · ${fmt.pct(wr, 0)}`) : null),
      wr !== null ? h("div.otp-wr", h("span", { style: { width: `${wr}%` } })) : h("div.tiny.muted", "partidas en cola"),
    ),
    h("div.otp-player-role", mainRole ? [roleIcon(mainRole), h("span.tiny.muted", ROLE_LABEL[mainRole] || mainRole)] : h("span.tiny.muted", "—")),
    h(
      "div.otp-form",
      { "aria-label": "Últimas partidas" },
      (p.games || []).map((g) =>
        h(`a.otp-dot.${g.win ? "win" : "loss"}`, {
          href: `#/partida/${g.match_id}`,
          onclick: () => document.querySelector(".modal-backdrop")?.remove(),
          title: `${g.win ? "Victoria" : "Derrota"} ${g.kills}/${g.deaths}/${g.assists}${g.role ? ` · ${ROLE_LABEL[g.role] || g.role}` : ""} · ${fmt.date(g.game_start)} · ${fmt.duration(g.duration)}`,
        }),
      ),
    ),
    h(
      `span.otp-history.${p.history === "done" || p.history === "share" ? "good" : "muted"}`,
      { title: historyText },
      p.history === "done" || p.history === "share" ? "Completo" : p.history === "capped" ? "En su tope" : p.history === "loading" ? "Bajando" : "En cola",
    ),
  );
}

// ------------------------------------------------------------------ fuentes
let opggForm = { tier: "emerald_plus", region: "global" };
let sourcesSig = null;
const SOURCE_INFO = {
  opgg: { label: "OP.GG", what: "Counters, tier list, guías de matchup, builds y sinergias (web + API oficial para apps)." },
  lolalytics: { label: "Lolalytics", what: "Segunda fuente de tier list y counters: se combina con OP.GG sin contar dos veces las mismas partidas." },
  patchnotes: { label: "Notas de parche (Riot)", what: "Cambios de campeones y objetos de las últimas versiones, con mejoras y debilitaciones." },
};

function renderSources(src) {
  const sig = JSON.stringify(Object.values(src).map((s) => [s.enabled, s.stats?.requests, s.stats?.errors, s.stats?.circuit_open_for > 0, Object.values(s.cache || {}).map((c) => c.entries)]));
  if (sig === sourcesSig) return;
  sourcesSig = sig;
  const rows = Object.entries(src).map(([name, s]) => {
    const info = SOURCE_INFO[name] || { label: name, what: "" };
    const entries = Object.values(s.cache || {}).reduce((acc, c) => acc + (c.entries || 0), 0);
    const latest = Math.max(0, ...Object.values(s.cache || {}).map((c) => c.latest || 0));
    return h(
      "div.source-row",
      h("div", h("div.row", h("b", info.label), s.enabled ? h("span.pill.pill-good.pill-dot", "Activa") : h("span.pill.pill-warn.pill-dot", "Desactivada")), h("div.tiny.muted", info.what)),
      h("div.small", `${fmt.int(entries)} en caché`, latest ? h("div.tiny.muted", `actualizado ${fmt.ago(latest)}`) : null),
      h(
        "div.small",
        `${fmt.int(s.stats?.requests || 0)} descargas`,
        s.stats?.circuit_open_for > 0
          ? h("div.tiny.warn", { title: s.stats.last_error || "" }, `En pausa ${fmt.duration(s.stats.circuit_open_for)}: la web pidió esperar (se usa lo guardado)`)
          : s.stats?.errors
            ? h("div.tiny.warn", { title: s.stats.last_error || "" }, `${s.stats.errors} errores`)
            : h("div.tiny.muted", "sin errores"),
      ),
    );
  });
  const opgg = state.opgg;
  const sync = opgg?.sync || {};
  const running = sync.status === "running";
  mount(
    sections.sources,
    h("div.col", rows),
    h("div.divider"),
    h("div.small", h("b", "Descarga completa de OP.GG para otro elo o región")),
    h("p.tiny.muted", "No hace falta para los filtros que usas (se precargan solos). Útil si vas a consultar un ámbito nuevo sin conexión."),
    opgg
      ? h(
          "div.row-wrap",
          select((opgg.tiers || []).map((t) => ({ value: t, label: t.replace("_plus", "+").replace(/^./, (ch) => ch.toUpperCase()) })), opggForm.tier, (v) => (opggForm.tier = v), "select select-sm"),
          select((opgg.regions || []).map((r) => ({ value: r.id, label: r.label })), opggForm.region, (v) => (opggForm.region = v), "select select-sm"),
          running
            ? h("button.btn.btn-danger.btn-sm", { onclick: async () => { await api.post("/api/opgg/sync/stop"); refreshOpgg(); } }, icon("stop"), "Parar")
            : h("button.btn.btn-sm", { onclick: startOpggSync }, icon("download"), "Descargar"),
          running || sync.status === "done" ? h("span.small.muted", `${sync.done || 0}/${sync.total || 0} páginas${running ? "" : " · completado"}`) : null,
        )
      : h("span.small.muted", "OP.GG no disponible."),
  );
}

async function refreshOpgg() {
  try {
    state.opgg = await api.get("/api/opgg/status");
    sourcesSig = null;
  } catch {
    /* sin OP.GG */
  }
}

async function startOpggSync() {
  try {
    await api.post("/api/opgg/sync", opggForm);
    toast("Descargando OP.GG en segundo plano…");
    refreshOpgg();
  } catch (err) {
    toast(err.message, "error");
  }
}

// ------------------------------------------------------------------ límites
const FOREIGN_MIN = 10; // peticiones ajenas en 2 min para avisar (por debajo puede ser ruido)

/** Otra app (p. ej. otra copia de Tempu Drafter) está usando la misma key en los hosts de este hilo del recolector. */
function sharedKeyLine(w) {
  const hosts = [w.region, ...(w.platforms || [])];
  const [host, info] = hosts.map((x) => [x, limitsNow[x]]).filter(([, i]) => i && i.foreign >= FOREIGN_MIN).sort((a, b) => b[1].foreign - a[1].foreign)[0] || [];
  if (!info) return null;
  return h("div.tiny.warn", `Otra app está usando tu misma API key (~${fmt.int(info.foreign)} peticiones en 2 min en ${host}): os repartís el cupo de Riot y el recolector va más lento.`);
}

const REGION_NAME = { europe: "Europa", americas: "América", asia: "Asia", sea: "SEA / Oceanía" };
const regionOfHost = (host) => (REGION_NAME[host] ? host : state.meta?.platforms?.find((p) => p.id === host)?.region || "otros");
/** Uso de la ventana larga (2 min) de un servidor, de 0 a 1. */
const hostLoad = (info) => {
  const w = [...(info.windows || [])].sort((x, y) => y.seconds - x.seconds)[0];
  return w ? w.used / Math.max(1, w.limit) : 0;
};
let limitsSig = null;

/** Una tarjeta por región con lo justo (uso y estado); el detalle de cada servidor, al pulsarla. */
function renderLimits(limits) {
  const hosts = Object.entries(limits || {});
  const sig = JSON.stringify(hosts.map(([k, i]) => [k, Math.round(hostLoad(i) * 20), i.blocked_for > 0, (i.foreign || 0) >= FOREIGN_MIN]));
  if (sig === limitsSig) return;
  limitsSig = sig;
  if (!hosts.length) return mount(sections.limits, h("div.small.muted", "Sin peticiones a Riot todavía en esta sesión."));
  const regions = {};
  for (const [host, info] of hosts) (regions[regionOfHost(host)] ||= []).push([host, info]);
  const shared = hosts.filter(([, i]) => (i.foreign || 0) >= FOREIGN_MIN);
  mount(
    sections.limits,
    shared.length
      ? notice(
          `Otra app está usando tu misma API key (${shared.map(([host]) => host).join(", ")}). Riot da un solo cupo por key: si tienes Tempu Drafter abierto en otro ordenador, ciérralo o usa una key distinta en cada uno.`,
          "warn",
        )
      : null,
    h(
      "div.limit-regions",
      Object.entries(regions)
        .sort((x, y) => Object.keys(REGION_NAME).indexOf(x[0]) - Object.keys(REGION_NAME).indexOf(y[0]))
        .map(([region, list]) => {
          const load = Math.max(...list.map(([, i]) => hostLoad(i)));
          const waiting = list.some(([, i]) => i.blocked_for > 0);
          const requests = list.reduce((n, [, i]) => n + (i.requests || 0), 0);
          const [label, tone] = waiting ? ["Esperando a Riot", "warn"] : load >= 0.9 ? ["Al límite", "warn"] : load >= 0.5 ? ["Con uso", "good"] : ["Libre", ""];
          return h(
            "button.limit-region",
            { type: "button", onclick: () => limitsDetail(region, list) },
            h("div.row", h("b", REGION_NAME[region] || region), h("span.spacer"), h(`span.pill${tone ? `.pill-${tone}` : ""}`, label)),
            h("div.track", h("span", { style: { width: `${Math.round(load * 100)}%` } })),
            h("div.tiny.muted", `${fmt.int(requests)} ${requests === 1 ? "petición" : "peticiones"} · ${list.length} ${list.length === 1 ? "servidor" : "servidores"}`),
          );
        }),
    ),
    h("p.tiny.muted", { style: { marginTop: "8px", marginBottom: 0 } }, "La barra es el servidor más ocupado de cada región en los últimos 2 minutos. Pulsa una región para ver cada servidor."),
  );
}

function limitsDetail(region, list) {
  modal(
    `Límite de la API · ${REGION_NAME[region] || region}`,
    h(
      "div.col",
      { style: { gap: "12px" } },
      list
        .sort((x, y) => hostLoad(y[1]) - hostLoad(x[1]))
        .map(([host, info]) =>
          h(
            "div.limit-host",
            h(
              "div.row",
              h("b.small", REGION_NAME[host] ? `${REGION_NAME[host]} (partidas y cuentas)` : `${platformLabel(host)} (${host})`),
              h("span.spacer"),
              h("span.tiny.muted", `${fmt.int(info.requests)} ${info.requests === 1 ? "petición" : "peticiones"}${info.foreign ? ` · ${fmt.int(info.foreign)} de otra app` : ""}${info.rate_limited ? ` · ${info.rate_limited} × 429` : ""}`),
              info.blocked_for ? h("span.pill.pill-warn", `espera ${info.blocked_for}s`) : null,
            ),
            info.windows.map((w) => h("div.usage", h("span.tiny.muted", `${w.limit} / ${w.seconds}s`), h("div.track", h("span", { style: { width: `${Math.min(100, (w.used / w.limit) * 100)}%` } })), h("span.tiny.right", `${w.used}`))),
          ),
        ),
      h("p.tiny.muted", { style: { margin: 0 } }, "Límites leídos de las cabeceras de Riot. Cada servidor (euw1, kr…) y cada región (europe, asia…) tienen su propio cupo. El recolector deja libre parte del cupo para tus búsquedas (más si estás usando la web)."),
    ),
  );
}

// ------------------------------------------------------------------ BD
function renderDb(s) {
  const db = s.db;
  const legacy = s.legacy || {};
  mount(
    sections.db,
    h(
      "div.kpis",
      kpi("Partidas propias", fmt.int(db.matches), `${fmt.int(db.stored_matches)} guardadas en total`),
      kpi("Histórico antiguo", fmt.int(db.legacy_matches), legacy.running ? "importando…" : legacy.status === "done" ? "importado" : legacy.status || ""),
      kpi("Jugadores", fmt.compact(db.players), `${fmt.compact(db.seeds)} semillas`),
      kpi("Tamaño", `${fmt.num(db.size_mb, 1)} MB`),
    ),
    h("div.row", { style: { marginTop: "12px" } }, h("button.btn.btn-sm", { onclick: async () => { if (!confirm("¿Reimportar el histórico antiguo? Tarda unos minutos y se hace en segundo plano.")) return; try { await api.post("/api/system/legacy-import", null, { force: true }); toast("Reimportando histórico…"); } catch (err) { toast(err.message, "error"); } } }, icon("refresh"), "Reimportar histórico")),
    h("p.tiny.muted", { style: { marginTop: "8px" } }, `Guías en ${s.guides_dir}`),
  );
}

// ------------------------------------------------------------------ modelo
let backtestRunning = false; // el refresco repinta la sección: sin esto el botón volvía a activarse a medio cálculo

function renderModel(model) {
  const report = model.backtest;
  const priors = model.priors;
  const run = async (btn) => {
    if (backtestRunning) return;
    backtestRunning = true;
    btn.disabled = true;
    btn.textContent = "Calculando…";
    try {
      const res = await api.post("/api/draft/backtest", null, { sample: 3000 });
      if (res.status === "insufficient") toast(`Hacen falta ${res.needed} partidas propias completas (hay ${res.matches}).`, "error", 6000);
      else toast(`Modelo calibrado: α = ${res.alpha}`, "success");
      refresh();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      backtestRunning = false;
      btn.disabled = false;
    }
  };
  const btn = backtestRunning
    ? h("button.btn.btn-sm", { disabled: true }, icon("chart"), "Calculando…")
    : h("button.btn.btn-sm", { onclick: (e) => run(e.currentTarget) }, icon("chart"), "Backtest y calibrar");
  mount(
    sections.model,
    h(
      "dl.kv",
      h("dt", "α (calibración)"), h("dd", fmt.num(model.alpha, 3)),
      h("dt", "Prior campeón"), h("dd", priors ? `${fmt.int(priors.champ)} partidas` : "—"),
      h("dt", "Prior matchup"), h("dd", priors ? `${fmt.int(priors.matchup)} partidas` : "—", priors?.samples?.matchup_opgg ? h("span.tiny.muted", ` (${fmt.int(priors.samples.matchup_opgg)} muestras de OP.GG)`) : null),
      h("dt", "Prior sinergia"), h("dd", priors ? `${fmt.int(priors.synergy)} partidas` : "—"),
    ),
    report
      ? h(
          "div",
          { style: { marginTop: "12px" } },
          h("div.small", `Backtest con ${fmt.int(report.matches)} partidas (${report.sources}): acierto `, h("b", fmt.pct(report.calibrated.accuracy)), ` vs ${fmt.pct(report.baseline.accuracy)} base · log-loss ${report.calibrated.log_loss} (azar ${report.baseline.log_loss})`),
          h("div.row", { style: { justifyContent: "center", marginTop: "8px" } }, calibrationChart(report.calibration)),
          h("div.tiny.muted.center", "Predicho (eje X) vs observado (eje Y): cuanto más cerca de la diagonal, mejor calibrado."),
        )
      : h("p.small.muted", { style: { marginTop: "10px" } }, "Con al menos 200 partidas propias completas puedes validar el modelo: predice cada partida sin usarla (leave-one-out) y ajusta α para que las probabilidades sean realistas."),
    h("div", { style: { marginTop: "10px" } }, btn),
  );
}
