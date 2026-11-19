// Datos y API: API key, precarga en segundo plano, recolector por servidores,
// fuentes externas, límites de la API, base de datos y modelo de draft.
import { api } from "../api.js";
import { calibrationChart } from "../charts.js";
import { champPicker } from "../components/picker.js";
import { dd, platformLabel, state, TIER_LABEL } from "../state.js";
import { champImg, collapsible, errorBox, fmt, h, icon, kpi, latest, loading, mount, multiSelect, notice, select, toast } from "../ui.js";

let root = null;
let timer = null;
let sections = {};
let crawlerForm = null; // configuración que estás editando (sobrevive a los refrescos)
let crawlerFormFor = null; // "running" | "stopped": el formulario solo se reconstruye al cambiar de estado
let runningSig = null;
let limitsNow = {}; // uso de la API por host (para avisar si otra app usa tu misma key)

const ALL_TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"];
const APEX = ["MASTER", "GRANDMASTER", "CHALLENGER"];
const STATUS_LABEL = {
  starting: "Arrancando",
  running: "Recolectando",
  searching: "Buscando OTPs",
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
  keyRendered = null;
  sourcesSig = null;
  otpsSig = null;
  otpsAt = 0;
  sections = {
    key: h("div"),
    warmer: h("div"),
    crawlerForm: h("div"),
    crawlerStatus: h("div"),
    otps: h("div"),
    sources: h("div"),
    limits: h("div"),
    db: h("div"),
    model: h("div"),
  };
  mount(
    container,
    h("div.section-head", h("div", h("h2", "Datos y API"), h("p", "Tu API key de Riot, lo que se descarga solo en segundo plano, el recolector de partidas por servidores y el estado de cada fuente de datos."))),
    h(
      "div.grid.grid-2",
      h("section.card", h("h3.card-title", icon("key"), "API key de Riot"), sections.key),
      h("section.card", h("h3.card-title", icon("refresh"), "Datos en segundo plano"), sections.warmer),
    ),
    h(
      "section.card",
      { style: { marginTop: "16px" } },
      h("h3.card-title", icon("database"), "Recolector de partidas por servidores"),
      h("div.grid.crawler-grid", sections.crawlerForm, sections.crawlerStatus),
      h("div", { style: { marginTop: "14px" } }, collapsible("datos.otps", [icon("crown"), "OTPs encontrados"], sections.otps, { summary: "jugadores y sus partidas con tus campeones", cls: "flat" })),
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
  timer = setInterval(() => root && !interacting() && refresh(), 2500);
}

/** Eligiendo en un desplegable o escribiendo en la vista: el refresco espera (repintar cerraría el desplegable). */
function interacting() {
  const a = document.activeElement;
  return !!a && a !== document.body && root?.contains(a) && /^(SELECT|INPUT|TEXTAREA)$/.test(a.tagName);
}

async function refresh() {
  const fresh = latest("datos.refresh"); // un refresco lento no pisa a uno más nuevo
  try {
    const [status, sources] = await Promise.all([api.get("/api/system/status"), api.get("/api/sources/status").catch(() => null)]);
    if (!root || !fresh()) return;
    state.status = status;
    // No se repinta una sección mientras el usuario está escribiendo o eligiendo en ella.
    const busy = (el) => el.contains(document.activeElement) && document.activeElement !== document.body;
    if (!busy(sections.key)) renderKey(status.key);
    renderWarmer(status.warmer);
    limitsNow = status.ratelimit || {};
    renderCrawler(status.crawler, status.key);
    if (sources) renderSources(sources);
    renderLimits(status.ratelimit);
    loadOtps(status.crawler);
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
      bar("Todo", w.all_pct, `${fmt.int(plan.done || 0)}/${fmt.int(plan.total || 0)}`),
    ),
    plan.phase ? h("div.tiny.muted", { style: { marginTop: "6px" } }, plan.phase) : null,
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
  const sig = JSON.stringify([c.stored, c.rate_per_min, c.stopping, c.paused, c.autostart, key.configured]);
  if (crawlerFormFor !== mode) {
    crawlerFormFor = mode;
    runningSig = sig;
    mount(sections.crawlerForm, running ? runningSummary(c) : crawlerFormView(key));
  } else if (running && sig !== runningSig) {
    runningSig = sig;
    mount(sections.crawlerForm, runningSummary(c));
  }
  renderCrawlerStatus(c, key);
}

function crawlerFormView(key) {
  const cfg = crawlerForm;
  const platforms = state.meta?.platforms || [];
  const startBtn = h("button.btn.btn-primary", { onclick: () => start(startBtn) }, icon("play"), "Iniciar recolector");
  const updateStart = () => {
    const ok = cfg.platforms.length && cfg.tiers.length && key.configured;
    startBtn.disabled = !ok;
    startBtn.title = !key.configured ? "Configura primero la API key" : !cfg.platforms.length ? "Elige al menos un servidor" : !cfg.tiers.length ? "Elige al menos un elo" : "";
  };
  const servers = multiSelect({
    items: platforms.map((p) => ({ value: p.id, label: p.label, title: p.name, group: p.region })),
    groups: REGION_GROUPS,
    selected: cfg.platforms,
    noun: "servidores",
    presets: [{ label: "Solo EUW", values: ["euw1"] }],
    onChange: (v) => {
      cfg.platforms = v;
      updateStart();
    },
  });
  const tiers = multiSelect({
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
      updateStart();
    },
  });
  const num = (label, keyName, min, max, suffix = null) =>
    h(
      "label.row.small",
      label,
      h("input.input", {
        type: "number",
        min,
        max,
        value: cfg[keyName],
        style: { width: "76px" },
        onchange: (e) => {
          const v = Number(e.target.value);
          cfg[keyName] = Number.isFinite(v) ? Math.max(min, Math.min(max, v)) : cfg[keyName];
          e.target.value = cfg[keyName];
        },
      }),
      suffix,
    );
  updateStart();
  return h(
    "div.col",
    !key.configured ? notice("Necesitas una API key para recoger partidas propias. OP.GG y Lolalytics ya cubren los matchups sin ella.", "warn") : null,
    h("div", h("div.small", { style: { fontWeight: 650, marginBottom: "6px" } }, "Servidores"), h("div.tiny.muted", { style: { marginBottom: "8px" } }, "Cada región de Riot (Europa, América, Asia, SEA) va en paralelo. Pulsa el nombre de un grupo para marcarlo o quitarlo entero."), servers.el),
    h("div.divider"),
    h("div", h("div.small", { style: { fontWeight: 650, marginBottom: "6px" } }, "Elo de los jugadores"), tiers.el),
    h("div.divider"),
    championSection(cfg),
    h("div.divider"),
    collapsible(
      "datos.recolector.avanzado",
      [icon("edit"), "Opciones avanzadas"],
      h(
        "div.row-wrap",
        { style: { gap: "12px 18px" } },
        h("label.check", h("input", { type: "checkbox", checked: cfg.include_flex, onchange: (e) => (cfg.include_flex = e.target.checked) }), "Incluir Flex"),
        num("Primera vez: últimos", "days", 1, 60, "días"),
        num("Partidas por jugador", "matches_per_player", 5, 100),
        num("Revisitar cada", "recrawl_hours", 1, 168, "h"),
        h("p.tiny.muted", { style: { margin: 0, flexBasis: "100%" } }, "«Primera vez» y «Partidas por jugador» son del modo normal. Buscando OTPs no hay tope: se usa «Historial de cada OTP»."),
      ),
      { summary: `${cfg.matches_per_player} partidas/jugador · ${cfg.days} días · revisita ${cfg.recrawl_hours} h`, cls: "flat" },
    ),
    h("div.row-wrap", startBtn, h("span.tiny.muted", "Al iniciarlo, se reanuda solo cada vez que abras la app hasta que lo detengas.")),
  );
}

const MASTERY_STEPS = [100_000, 300_000, 500_000, 1_000_000, 2_000_000];
const OTP_DEPTHS = [
  { value: "season", label: "Temporada actual" },
  { value: "90", label: "Últimos 90 días" },
  { value: "180", label: "Últimos 180 días" },
  { value: "365", label: "Último año" },
  { value: "all", label: "Todo lo que guarde Riot" },
];
const depthLabel = (v) => (OTP_DEPTHS.find((d) => d.value === String(v)) || OTP_DEPTHS[0]).label;

/** Búsqueda por campeones: OTPs o jugadores con mucha maestría de los campeones elegidos. */
function championSection(cfg) {
  cfg.champions = cfg.champions || [];
  const chipsEl = h("div.chips");
  const drawChips = () =>
    mount(
      chipsEl,
      cfg.champions.length
        ? cfg.champions.map((id) =>
            h(
              "button.chip.on",
              { type: "button", title: "Quitar", onclick: () => { cfg.champions = cfg.champions.filter((c) => c !== id); drawChips(); } },
              champImg(id, "xs"),
              dd.name(id),
              icon("close"),
            ),
          )
        : h("span.small.muted", "Ningún campeón: se recoge todo el ranking (modo normal)."),
    );
  const picker = champPicker({
    placeholder: "Añadir campeón",
    compact: true,
    exclude: () => new Set(cfg.champions),
    onChange: (id) => {
      if (id && !cfg.champions.includes(id) && cfg.champions.length < 10) cfg.champions = [...cfg.champions, id];
      picker.setValue(null);
      drawChips();
    },
  });
  drawChips();
  return h(
    "div.col",
    h("div.small", { style: { fontWeight: 650 } }, "Buscar por campeones (OTPs)"),
    h("div.tiny.muted", "Elige campeones y solo se recogen partidas de jugadores del ranking (servidores y elos de arriba) que sean OTPs de ellos o tengan muchísima maestría. Ejemplo: KR + Aspirante/Gran Maestro + Ahri."),
    h("div.row-wrap", h("div", { style: { width: "220px" } }, picker), h("span.tiny.muted", "hasta 10")),
    chipsEl,
    h(
      "div.row-wrap",
      { style: { gap: "10px 16px" } },
      h(
        "label.row.small",
        "Quién cuenta",
        select(
          [
            { value: "otp", label: "OTPs (es su campeón principal)" },
            { value: "mastery", label: "Mucha maestría (entre sus 3 primeros)" },
          ],
          cfg.focus || "otp",
          (v) => (cfg.focus = v),
          "select select-sm",
        ),
      ),
      h(
        "label.row.small",
        "Mínimo",
        select(MASTERY_STEPS.map((n) => ({ value: n, label: `${fmt.compact(n)} puntos` })), cfg.min_mastery || 300_000, (v) => (cfg.min_mastery = Number(v)), "select select-sm"),
      ),
      h(
        "label.row.small",
        { title: "Hasta dónde se baja el historial de cada OTP. La temporada actual es lo comparable: el año anterior tenía otro mapa y otros objetos." },
        "Historial de cada OTP",
        select(OTP_DEPTHS, String(cfg.otp_depth || "season"), (v) => (cfg.otp_depth = v), "select select-sm"),
      ),
      h("label.check", h("input", { type: "checkbox", checked: cfg.only_target_games !== false, onchange: (e) => (cfg.only_target_games = e.target.checked) }), "Solo sus partidas con esos campeones"),
    ),
    h("p.tiny.muted", { style: { margin: 0 } }, "Sin tope de partidas por jugador: de cada OTP se baja su historial entero hasta la fecha elegida, 100 partidas por petición. Primero lo reciente de todos los OTPs y luego lo más antiguo; sus partidas nuevas van siempre antes. Si más atrás ya casi no jugaba esos campeones, se deja de bajar para no gastar llamadas."),
    h("p.tiny.muted", { style: { margin: 0 } }, "La API de Riot no tiene un ranking de maestría por campeón: se mira la maestría de cada jugador del ranking (1 llamada por jugador, se recuerda 7 días) en paralelo a la descarga, porque Riot cuenta aparte el servidor y la región. OTP = su campeón con más puntos es uno de los elegidos y tiene al menos 1,5 veces los del segundo."),
  );
}

function runningSummary(c) {
  const cfg = c.config || {};
  const tierText = (cfg.tiers || []).map((t) => TIER_LABEL[t] || t).join(", ");
  return h(
    "div.col",
    h("div.row-wrap", h("span.pill.pill-good.pill-dot", c.paused === "in_game" ? "En pausa mientras juegas" : "En marcha"), c.autostart ? h("span.pill", { title: "Seguirá al volver a abrir la app" }, icon("refresh"), "Se reanuda solo") : null),
    h("div.small", h("b", "Servidores: "), (cfg.platforms || []).map((p) => platformLabel(p)).join(", ") || "—"),
    h("div.small", h("b", "Elos: "), tierText || "—"),
    cfg.champions?.length
      ? h(
          "div.small.row-wrap",
          h("b", cfg.focus === "mastery" ? "Mucha maestría con: " : "OTPs de: "),
          cfg.champions.map((id) => h("span.row", { style: { gap: "4px" } }, champImg(id, "xs"), dd.name(id))),
          h("span.tiny.muted", `≥ ${fmt.compact(cfg.min_mastery)} puntos${cfg.only_target_games ? " · solo partidas con ellos" : ""}`),
        )
      : null,
    h(
      "div.tiny.muted",
      cfg.champions?.length
        ? `${cfg.include_flex ? "SoloQ + Flex" : "Solo SoloQ"} · historial de cada OTP: ${depthLabel(cfg.otp_depth).toLowerCase()}, sin tope de partidas · partidas nuevas cada ${cfg.recrawl_hours} h`
        : `${cfg.include_flex ? "SoloQ + Flex" : "Solo SoloQ"} · ${cfg.matches_per_player} partidas por jugador · revisita cada ${cfg.recrawl_hours} h`,
    ),
    h(
      "div.row-wrap",
      { style: { marginTop: "6px" } },
      h("button.btn.btn-danger", { onclick: stop, disabled: c.stopping || null }, icon("stop"), c.stopping ? "Deteniendo…" : "Detener"),
      h("span.small.muted", `${fmt.int(c.stored)} partidas nuevas`, c.rate_per_min ? ` · ${fmt.num(c.rate_per_min, 1)}/min` : ""),
    ),
    h("p.tiny.muted", "Para cambiar servidores o elos, detenlo: el formulario vuelve con tu última configuración."),
  );
}

function renderCrawlerStatus(c, key) {
  const workers = Object.values(c.workers || {});
  mount(
    sections.crawlerStatus,
    h(
      "div.col",
      workers.length
        ? workers.map((w) =>
            h(
              "div.worker",
              h("div.row", h("b", { style: { textTransform: "capitalize" } }, w.region), h(`span.pill.${w.status === "running" ? "pill-good" : w.status === "waiting_key" || w.status === "paused_game" ? "pill-warn" : ""}`, STATUS_LABEL[w.status] || w.status), h("span.spacer"), h("span.tiny.muted", w.platform ? `${platformLabel(w.platform)} · ${TIER_LABEL[w.tier] || w.tier || ""}` : "")),
              h("div.tiny.muted", { style: { marginTop: "4px" } }, `${fmt.int(w.stored)} guardadas · ${fmt.int(w.duplicates)} repetidas · ${fmt.int(w.remakes)} remakes · ${fmt.int(w.players)} jugadores · ${fmt.int(w.seeds_added)} semillas`),
              w.search
                ? h("div.tiny.muted", `OTPs: ${fmt.int(w.otps_known)} encontrados · ${fmt.int(w.otps_complete)} con el historial completo · búsqueda ${SEARCH_LABEL[w.search] || w.search}`)
                : null,
              w.mastery_checked || w.search
                ? h("div.tiny.muted", `Maestría revisada: ${fmt.int(w.mastery_checked)} jugadores · ${fmt.int(w.targets_found)} nuevos · ${fmt.int(w.target_games)} partidas con tus campeones${w.other_champ_skipped ? ` · ${fmt.int(w.other_champ_skipped)} descartadas (otro campeón)` : ""}`)
                : null,
              sharedKeyLine(w),
              w.last_error ? h("div.tiny.warn", w.last_error) : null,
            ),
          )
        : h("div.small.muted", key.configured ? "Sin actividad todavía: elige servidores y elos y pulsa «Iniciar recolector»." : "Sin actividad."),
      h("p.tiny.muted", "Coste con la key gratuita: ~1 llamada por partida nueva. Las semillas salen de las ligas oficiales (205 jugadores por llamada) y se descartan partidas repetidas, remakes y colas que no son ranked. Se pausa solo mientras juegas."),
    ),
  );
}

async function start(btn) {
  btn.disabled = true;
  try {
    await api.post("/api/crawler/start", crawlerForm);
    toast("Recolector en marcha", "success");
    crawlerFormFor = null;
    refresh();
  } catch (err) {
    toast(err.message, "error");
    btn.disabled = false;
  }
}

async function stop() {
  try {
    await api.post("/api/crawler/stop");
    toast("Deteniendo el recolector…");
    refresh();
  } catch (err) {
    toast(err.message, "error");
  }
}

// ------------------------------------------------------------------ OTPs encontrados
let otpsAt = 0;
let otpsSig = null;
async function loadOtps(c) {
  const box = sections.otps;
  const details = box.closest("details");
  if (!details?.open || Date.now() - otpsAt < 10000) return;
  otpsAt = Date.now();
  if (!c?.config?.champions?.length) {
    otpsSig = null;
    return mount(box, h("div.small.muted", "Elige campeones en «Buscar por campeones (OTPs)» y arranca el recolector: aquí aparecerán los OTPs que encuentre y sus partidas."));
  }
  let data;
  try {
    data = await api.get("/api/crawler/otps", { limit: 80 });
  } catch (err) {
    return mount(box, errorBox(err));
  }
  const sig = JSON.stringify([data.found, data.complete, data.games, data.players.map((p) => [p.platform, p.champ, p.points, p.total, p.history, p.history_checked, p.games.length])]);
  if (sig === otpsSig) return;
  otpsSig = sig;
  if (!data.players.length) {
    return mount(box, h("div.small.muted", "Todavía ninguno: se está revisando la maestría de los jugadores del ranking (1 llamada por jugador, al ritmo de tu API key)."));
  }
  const shown = data.players.length < data.found ? ` (se muestran los ${data.players.length} con más partidas)` : "";
  mount(
    box,
    h(
      "div.col",
      { style: { gap: "8px" } },
      h("div.tiny.muted", `${fmt.int(data.found)} OTPs encontrados · ${fmt.int(data.games || 0)} partidas suyas con tus campeones · ${fmt.int(data.complete)} con el historial completo · historial: ${depthLabel(data.otp_depth).toLowerCase()}${shown}`),
      data.players.map((p) => {
        const name = p.game_name ? `${p.game_name}#${p.tag_line}` : "Nombre aún desconocido";
        const wr = p.total ? (100 * p.wins) / p.total : null;
        const historyText = p.history === "loading" && p.history_checked ? `${HISTORY_LABEL.loading} (${fmt.int(p.history_checked)} revisadas)` : HISTORY_LABEL[p.history] || "";
        return h(
          "div.otp-row",
          h(
            "div.row",
            { style: { minWidth: 0 } },
            champImg(p.champ, "sm"),
            h(
              "div",
              { style: { minWidth: 0 } },
              p.game_name
                ? h("a.ellipsis", { href: `#/jugador/${p.platform}/${encodeURIComponent(p.game_name)}/${encodeURIComponent(p.tag_line)}`, style: { fontWeight: 650, display: "block" } }, name)
                : h("div.muted", name),
              h("div.tiny.muted", `${platformLabel(p.platform)} · ${TIER_LABEL[p.tier] || p.tier || "—"}${p.lp != null ? ` ${p.lp} LP` : ""} · ${fmt.compact(p.points)} puntos con ${dd.name(p.champ)}`),
              h(
                "div.tiny",
                h("b", `${fmt.int(p.total)} ${p.total === 1 ? "partida" : "partidas"} con ${dd.name(p.champ)}`),
                wr !== null ? h("span.muted", ` · ${fmt.pct(wr)} WR`) : null,
                p.share !== null && p.share !== undefined ? h("span.muted", { title: "De sus partidas revisadas, cuántas jugó con los campeones elegidos" }, ` · ${fmt.pct(100 * p.share, 0)} de sus partidas`) : null,
                historyText ? h(`span.${p.history === "loading" || p.history === "queued" ? "muted" : "good"}`, ` · ${historyText}`) : null,
              ),
            ),
          ),
          h(
            "div.row-wrap",
            { style: { gap: "4px" } },
            p.games.length
              ? p.games.map((g) =>
                  h(
                    `a.chip.otp-game.${g.win ? "win" : "loss"}`,
                    { href: `#/partida/${g.match_id}`, title: `${fmt.date(g.game_start)} · ${fmt.duration(g.duration)}` },
                    `${g.win ? "V" : "D"} ${g.kills}/${g.deaths}/${g.assists}`,
                  ),
                )
              : h("span.tiny.muted", "partidas en cola"),
          ),
        );
      }),
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

function renderLimits(limits) {
  const hosts = Object.entries(limits || {});
  if (!hosts.length) return mount(sections.limits, h("div.small.muted", "Sin peticiones a Riot todavía en esta sesión."));
  const shared = hosts.filter(([, i]) => (i.foreign || 0) >= FOREIGN_MIN);
  mount(
    sections.limits,
    shared.length
      ? notice(
          `Otra app está usando tu misma API key ahora mismo (${shared.map(([host, i]) => `${host}: ~${fmt.int(i.foreign)}`).join(" · ")} peticiones en 2 min). Riot da un solo cupo por key: si tienes Tempu Drafter abierto en otro ordenador, ciérralo o usa una key distinta en cada uno.`,
          "warn",
        )
      : null,
    h(
      "div.grid.grid-auto",
      hosts.map(([host, info]) =>
        h(
          "div",
          h("div.row", h("b.small", host), h("span.spacer"), h("span.tiny.muted", `${fmt.int(info.requests)} ${info.requests === 1 ? "petición" : "peticiones"}${info.foreign ? ` · ${fmt.int(info.foreign)} de otra app` : ""}${info.rate_limited ? ` · ${info.rate_limited} × 429` : ""}`), info.blocked_for ? h("span.pill.pill-warn", `espera ${info.blocked_for}s`) : null),
          info.windows.map((w) => h("div.usage", h("span.tiny.muted", `${w.limit} / ${w.seconds}s`), h("div.track", h("span", { style: { width: `${Math.min(100, (w.used / w.limit) * 100)}%` } })), h("span.tiny.right", `${w.used}`))),
        ),
      ),
    ),
    h("p.tiny.muted", { style: { marginTop: "8px" } }, "Límites leídos de las cabeceras de Riot. El recolector deja libre un 15 % para tus búsquedas."),
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
