// Panel de datos: API key, OP.GG, recolector, límites de la API, base de datos y modelo.
import { api } from "../api.js";
import { calibrationChart } from "../charts.js";
import { platformLabel, state, TIER_LABEL } from "../state.js";
import { errorBox, fmt, h, icon, kpi, loading, mount, notice, select, toast } from "../ui.js";

let root = null;
let timer = null;
let sections = {};
let crawlerForm = null;

const ALL_TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"];
const STATUS_LABEL = { starting: "Arrancando", running: "Recolectando", idle: "En espera", waiting_key: "Esperando API key", stopped: "Detenido" };

export function destroy() {
  root = null;
  clearInterval(timer);
}

export async function render(container) {
  root = container;
  sections = { key: h("div"), opgg: h("div"), crawler: h("div"), limits: h("div"), db: h("div"), model: h("div") };
  crawlerForm = null;
  mount(
    container,
    h("div.section-head", h("div", h("h2", "Datos y API"), h("p", "Configura tu API key de Riot, sincroniza OP.GG, recoge partidas propias y calibra el modelo de draft."))),
    h(
      "div.grid.grid-2",
      h("section.card", h("h3.card-title", icon("key"), "API key de Riot"), sections.key),
      h("section.card", h("h3.card-title", icon("globe"), "OP.GG"), sections.opgg),
    ),
    h("section.card", { style: { marginTop: "16px" } }, h("h3.card-title", icon("database"), "Recolector de partidas (Riot API)"), sections.crawler),
    h(
      "div.grid.grid-3",
      { style: { marginTop: "16px" } },
      h("section.card", h("h3.card-title", icon("activity"), "Límite de la API"), sections.limits),
      h("section.card", h("h3.card-title", icon("database"), "Base de datos"), sections.db),
      h("section.card", h("h3.card-title", icon("chart"), "Modelo de draft"), sections.model),
    ),
  );
  Object.values(sections).forEach((s) => mount(s, loading()));
  await refresh();
  clearInterval(timer);
  timer = setInterval(() => root && refresh(), 2500);
}

async function refresh() {
  try {
    const [status, opgg] = await Promise.all([api.get("/api/system/status"), api.get("/api/opgg/status")]);
    if (!root) return;
    state.status = status;
    state.opgg = opgg;
    // No se repinta una sección mientras el usuario está escribiendo o eligiendo en ella.
    const busy = (el) => el.contains(document.activeElement) && document.activeElement !== document.body;
    if (!busy(sections.key)) renderKey(status.key);
    if (!busy(sections.opgg)) renderOpgg(opgg);
    if (!busy(sections.crawler)) renderCrawler(status.crawler, status.key);
    renderLimits(status.ratelimit);
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
  const input = h("input.input", { placeholder: "RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", type: "password", autocomplete: "off", spellcheck: "false" });
  const save = async () => {
    try {
      const res = await api.post("/api/system/riot-key", { key: input.value.trim() });
      keyResultToast(res, "API key guardada y válida");
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
  const remaining = key.age_hours !== null ? Math.max(0, 24 - key.age_hours) : null;
  mount(
    sections.key,
    h("div.row-wrap", statusPill, key.masked ? h("code.small.muted", key.masked) : null, remaining !== null ? h("span.small.muted", `cargada ${fmt.ago(key.loaded_at)} · una development key dura 24 h (quedan ~${fmt.num(remaining, 0)} h)`) : null),
    key.detail ? h("div.small.bad", { style: { marginTop: "6px" } }, key.detail) : null,
    key.rechecking
      ? notice("Si la acabas de generar, Riot puede tardar unos minutos en activarla. Se vuelve a comprobar sola cada poco tiempo; no hace falta que hagas nada.", "info")
      : key.status === "invalid"
        ? h("p.small.muted", { style: { marginTop: "6px" } }, "Comprueba que has copiado la key completa y la última que generaste (al regenerar, la anterior deja de valer).")
        : null,
    h("div.input-group", { style: { marginTop: "12px" } }, input, h("button.btn.btn-primary", { onclick: save }, "Guardar"), key.configured ? h("button.btn", { onclick: validate }, "Validar") : null),
    h("p.small.muted", { style: { marginTop: "10px" } }, "Consíguela en ", h("a", { href: "https://developer.riotgames.com/", target: "_blank", rel: "noopener" }, "developer.riotgames.com"), ". Se guarda en tu .env local y se recarga en caliente: no hace falta reiniciar. Sin key siguen funcionando el draft, los matchups y la tier list con OP.GG."),
  );
}

// ------------------------------------------------------------------ OP.GG
let opggForm = { tier: "emerald_plus", region: "global" };
function renderOpgg(o) {
  const sync = o.sync || {};
  const running = sync.status === "running";
  const cache = o.cache || {};
  const progress = sync.total ? Math.round((sync.done / sync.total) * 100) : 0;
  mount(
    sections.opgg,
    h(
      "div.kpis",
      kpi("Parches", (o.versions || []).join(" · ") || "—", "actual · anterior"),
      kpi("Counters", fmt.int(cache.counters?.entries || 0), "páginas en caché"),
      kpi("Tier lists", fmt.int(cache.tier?.entries || 0)),
      kpi("Guías / sinergias", `${fmt.int(cache.guide?.entries || 0)} / ${fmt.int(cache.synergy?.entries || 0)}`, "MCP oficial"),
    ),
    h(
      "div.row-wrap",
      { style: { marginTop: "12px" } },
      select(o.tiers.map((t) => ({ value: t, label: t.replace("_plus", "+").replace(/^./, (c) => c.toUpperCase()) })), opggForm.tier, (v) => (opggForm.tier = v), "select select-sm"),
      select(o.regions.map((r) => ({ value: r.id, label: r.label })), opggForm.region, (v) => (opggForm.region = v), "select select-sm"),
      running
        ? h("button.btn.btn-danger.btn-sm", { onclick: async () => { await api.post("/api/opgg/sync/stop"); refresh(); } }, icon("stop"), "Parar")
        : h(
            "button.btn.btn-primary.btn-sm",
            {
              onclick: async () => {
                try {
                  await api.post("/api/opgg/sync", opggForm);
                  toast("Sincronizando OP.GG en segundo plano…");
                  refresh();
                } catch (err) {
                  toast(err.message, "error");
                }
              },
            },
            icon("refresh"),
            "Sincronizar todo",
          ),
    ),
    running || sync.status === "done"
      ? h(
          "div",
          { style: { marginTop: "10px" } },
          h("div.usage", h("span.small", running ? "Descargando" : "Completado"), h("div.track", h("span", { style: { width: `${progress}%` } })), h("span.small.right", `${sync.done}/${sync.total}`)),
        )
      : null,
    h("p.small.muted", { style: { marginTop: "10px" } }, "Los datos se descargan solos cuando hacen falta y se guardan en caché (6 h el parche actual, 7 días el anterior). «Sincronizar todo» precarga los counters de todos los campeones (~10 min) para que todo responda al instante. ", o.stats?.errors ? h("span.warn", `${o.stats.errors} errores (${o.stats.last_error || ""})`) : null),
  );
}

// ------------------------------------------------------------------ recolector
function renderCrawler(c, key) {
  const running = c.running;
  if (!crawlerForm) crawlerForm = { ...c.config };
  const cfg = crawlerForm;
  const chip = (label, on, toggle, title) => h(`span.chip${on ? ".on" : ""}`, { onclick: toggle, title }, label);
  const toggleIn = (list, value) => (list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  const platforms = state.meta?.platforms || [];
  const form = h(
    "div.col",
    h("div", h("div.small.muted", { style: { marginBottom: "6px" } }, "Regiones (cada región de Riot va en paralelo)"), h("div.chips", platforms.map((p) => chip(p.label, cfg.platforms.includes(p.id), () => { cfg.platforms = toggleIn(cfg.platforms, p.id); renderCrawler(c, key); }, p.name)))),
    h("div", h("div.small.muted", { style: { marginBottom: "6px" } }, "Elos de los jugadores semilla (ligas oficiales)"), h("div.chips", ALL_TIERS.map((t) => chip(TIER_LABEL[t], cfg.tiers.includes(t), () => { cfg.tiers = toggleIn(cfg.tiers, t); renderCrawler(c, key); })))),
    h(
      "div.row-wrap",
      { style: { gap: "16px" } },
      h("label.check", h("input", { type: "checkbox", checked: cfg.include_flex, onchange: (e) => (cfg.include_flex = e.target.checked) }), "Incluir Flex"),
      h("label.row.small", "Primera vez: últimos", h("input.input", { type: "number", min: 1, max: 60, value: cfg.days, style: { width: "70px" }, onchange: (e) => (cfg.days = Number(e.target.value)) }), "días"),
      h("label.row.small", "Partidas por jugador", h("input.input", { type: "number", min: 5, max: 100, value: cfg.matches_per_player, style: { width: "70px" }, onchange: (e) => (cfg.matches_per_player = Number(e.target.value)) })),
      h("label.row.small", "Revisitar cada", h("input.input", { type: "number", min: 1, max: 168, value: cfg.recrawl_hours, style: { width: "70px" }, onchange: (e) => (cfg.recrawl_hours = Number(e.target.value)) }), "h"),
    ),
  );
  const start = async () => {
    try {
      await api.post("/api/crawler/start", cfg);
      toast("Recolector en marcha", "success");
      refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  };
  const stop = async () => {
    await api.post("/api/crawler/stop");
    toast("Deteniendo el recolector…");
    refresh();
  };
  const workers = Object.values(c.workers || {});
  mount(
    sections.crawler,
    !key.configured ? notice("Necesitas una API key para recoger partidas propias. OP.GG ya cubre los matchups sin ella.", "warn") : null,
    h(
      "div.grid",
      { style: { gridTemplateColumns: "minmax(0, 1.2fr) minmax(0, 1fr)", marginTop: key.configured ? 0 : "12px" } },
      h(
        "div",
        running ? h("div.small.muted", "Configuración en uso: para cambiarla, detén el recolector.") : form,
        h(
          "div.row",
          { style: { marginTop: "12px" } },
          running ? h("button.btn.btn-danger", { onclick: stop }, icon("stop"), c.stopping ? "Deteniendo…" : "Detener") : h("button.btn.btn-primary", { onclick: start, disabled: !key.configured }, icon("play"), "Iniciar recolector"),
          running ? h("span.small.muted", `${fmt.int(c.stored)} partidas nuevas`, c.rate_per_min ? ` · ${fmt.num(c.rate_per_min, 1)}/min` : "") : null,
        ),
        h("p.tiny.muted", { style: { marginTop: "10px" } }, "Coste con la key gratuita: ~1 llamada por partida nueva. Las semillas salen de las ligas oficiales (205 jugadores por llamada) y se descartan partidas repetidas, remakes y colas que no son ranked."),
      ),
      h(
        "div.col",
        workers.length
          ? workers.map((w) =>
              h(
                "div.worker",
                h("div.row", h("b", { style: { textTransform: "capitalize" } }, w.region), h(`span.pill.${w.status === "running" ? "pill-good" : w.status === "waiting_key" ? "pill-warn" : ""}`, STATUS_LABEL[w.status] || w.status), h("span.spacer"), h("span.tiny.muted", w.platform ? `${platformLabel(w.platform)} · ${TIER_LABEL[w.tier] || w.tier || ""}` : "")),
                h("div.tiny.muted", { style: { marginTop: "4px" } }, `${fmt.int(w.stored)} guardadas · ${fmt.int(w.duplicates)} repetidas · ${fmt.int(w.remakes)} remakes · ${fmt.int(w.players)} jugadores · ${fmt.int(w.seeds_added)} semillas`),
                w.last_error ? h("div.tiny.warn", w.last_error) : null,
              ),
            )
          : h("div.small.muted", "Sin actividad todavía."),
      ),
    ),
  );
}

// ------------------------------------------------------------------ límites
function renderLimits(limits) {
  const hosts = Object.entries(limits || {});
  if (!hosts.length) return mount(sections.limits, h("div.small.muted", "Sin peticiones a Riot todavía en esta sesión."));
  mount(
    sections.limits,
    h(
      "div.col",
      hosts.map(([host, info]) =>
        h(
          "div",
          h("div.row", h("b.small", host), h("span.spacer"), h("span.tiny.muted", `${fmt.int(info.requests)} peticiones${info.rate_limited ? ` · ${info.rate_limited} × 429` : ""}`), info.blocked_for ? h("span.pill.pill-warn", `espera ${info.blocked_for}s`) : null),
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
      kpi("Histórico antiguo", fmt.int(db.legacy_matches), legacy.status === "done" ? "importado" : legacy.status || ""),
      kpi("Jugadores", fmt.compact(db.players), `${fmt.compact(db.seeds)} semillas`),
      kpi("Tamaño", `${fmt.num(db.size_mb, 1)} MB`),
    ),
    h("div.row", { style: { marginTop: "12px" } }, h("button.btn.btn-sm", { onclick: async () => { try { await api.post("/api/system/legacy-import", null, { force: true }); toast("Reimportando histórico…"); } catch (err) { toast(err.message, "error"); } } }, icon("refresh"), "Reimportar histórico")),
    h("p.tiny.muted", { style: { marginTop: "8px" } }, `Guías en ${s.guides_dir}`),
  );
}

// ------------------------------------------------------------------ modelo
function renderModel(model) {
  const report = model.backtest;
  const priors = model.priors;
  const run = async (btn) => {
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
      btn.disabled = false;
    }
  };
  const btn = h("button.btn.btn-sm", { onclick: (e) => run(e.currentTarget) }, icon("chart"), "Backtest y calibrar");
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
