// Mi progreso: tu perfil guardado, tus partidas recogidas solas, qué haces mal,
// propósitos para llegar a clasificatorias y una repetición 2D de cada partida.
import { api } from "../api.js";
import { lineChart } from "../charts.js";
import { DEATH_KINDS, deathMap, pathMap } from "../components/minimap.js";
import { dd, loadJSON, ROLE_LABEL, save, state, TIER_LABEL } from "../state.js";
import { champImg, collapsible, empty, errorBox, fmt, h, icon, itemImg, kpi, loading, modal, mount, notice, roleIcon, select, toast, wrClass } from "../ui.js";

const PREFS = "tempu.progreso.v1";
let prefs = loadJSON(PREFS, { queue: "all", role: "all", last: 30 });
let root = null;
let timer = null;
let headEl = null;
let bodyEl = null;
let lastCount = null;
let mode = null; // "setup" | "summary" | "game"

const CAMP = { blue: "azul", gromp: "Gromp", wolves: "lobos", raptors: "aves", red: "rojo", krugs: "Krugs" };

export function destroy() {
  root = null;
  clearInterval(timer);
}

export async function render(container, args = []) {
  root = container;
  clearInterval(timer);
  if (args[0]) {
    // La partida se ve ahora en «Repeticiones» (mapa 2D con cada error).
    location.replace(`#/repeticiones/${encodeURIComponent(args[0].toUpperCase())}`);
    return;
  }
  mode = null;
  lastCount = null;
  headEl = h("div");
  bodyEl = h("div");
  mount(
    container,
    h(
      "div.section-head",
      h(
        "div",
        h("h2", "Mi progreso"),
        h("p", "Tus partidas se recogen y analizan solas, sin grabar repeticiones: cómo mueres, si te pilla el jungla rival, en qué pierdes el tiempo, tu pathing… y propósitos para saber cuándo estás listo para clasificatorias."),
      ),
    ),
    headEl,
    bodyEl,
  );
  mount(bodyEl, loading("Cargando tu progreso…"));
  await refresh(true);
  timer = setInterval(() => root && mode !== "game" && refresh(false), 5000);
}

const busy = (el) => el && el.contains(document.activeElement) && document.activeElement !== document.body;

async function refresh(full) {
  let data;
  try {
    data = await api.get("/api/profile");
  } catch (err) {
    if (full) mount(bodyEl, errorBox(err));
    return;
  }
  if (!root || mode === "game") return;
  if (!data.profile) {
    if (mode !== "setup") {
      mode = "setup";
      mount(headEl);
      mount(bodyEl, setupView(data));
    }
    return;
  }
  if (mode === "setup") full = true;
  mode = "summary";
  if (!busy(headEl)) mount(headEl, profileCard(data));
  const c = data.tracker.counts;
  const count = c.ready + c.partial + c.pending;
  if ((full || count !== lastCount) && !busy(bodyEl)) {
    lastCount = count;
    await loadSummary(data);
  }
}

async function loadSummary(data) {
  try {
    const s = await api.get("/api/coach/summary", { queue: prefs.queue, role: prefs.role, last: prefs.last });
    if (!root || mode !== "summary") return;
    mount(bodyEl, summaryView(s, data));
  } catch (err) {
    mount(bodyEl, errorBox(err));
  }
}

// ------------------------------------------------------------------ perfil
function field(label, control) {
  return h("div.field", h("label", label), control);
}

function profileForm(data, { onSaved } = {}) {
  const p = data.profile;
  const platforms = (state.meta?.platforms || []).map((x) => ({ value: x.id, label: `${x.label} · ${x.name}` }));
  let platform = p?.platform || (platforms.some((x) => x.value === state.filters.platform) ? state.filters.platform : "euw1");
  let target = p?.target_tier || "PLATINUM";
  let backfill = p?.settings?.backfill || 50;
  const riot = h("input.input", { placeholder: "Nombre#TAG", value: p ? `${p.game_name}#${p.tag_line}` : "", autocomplete: "off", spellcheck: "false" });
  const platformSel = select(platforms, platform, (v) => (platform = v));
  const targetSel = select((data.targets || []).map((t) => ({ value: t, label: TIER_LABEL[t] || t })), target, (v) => (target = v));
  const backfillSel = select([20, 50, 100, 200].map((n) => ({ value: n, label: `Últimas ${n}` })), backfill, (v) => (backfill = Number(v)));

  const detectBtn = h("button.btn", { type: "button", title: "Lee tu Riot ID del cliente de LoL abierto en este ordenador" }, icon("user"), "Detectar del cliente");
  detectBtn.onclick = async () => {
    detectBtn.disabled = true;
    try {
      const { account } = await api.get("/api/profile/detect");
      riot.value = `${account.game_name}#${account.tag_line}`;
      if (account.platform && platforms.some((x) => x.value === account.platform)) {
        platform = account.platform;
        platformSel.value = account.platform;
      }
      toast("Cuenta detectada en el cliente de LoL", "success");
    } catch (err) {
      toast(`${err.message}. Abre el cliente de LoL o escribe tu Riot ID.`, "error", 6000);
    } finally {
      detectBtn.disabled = false;
    }
  };
  const saveBtn = h("button.btn.btn-primary", { type: "button" }, icon("check"), p ? "Guardar cambios" : "Guardar mi perfil");
  const submit = async () => {
    if (!riot.value.includes("#")) {
      riot.focus();
      riot.style.borderColor = "var(--bad)";
      return toast("Escribe tu Riot ID completo: Nombre#TAG", "error");
    }
    saveBtn.disabled = true;
    try {
      const res = await api.post("/api/profile", { riot_id: riot.value.trim(), platform, target_tier: target, backfill });
      toast(res.note || "Perfil guardado: tus partidas se recogerán solas", res.note ? "info" : "success", res.note ? 9000 : 4200);
      window.dispatchEvent(new Event("tempu:status"));
      onSaved?.(res);
    } catch (err) {
      toast(err.message, "error", 7000);
    } finally {
      saveBtn.disabled = false;
    }
  };
  saveBtn.onclick = submit;
  riot.addEventListener("keydown", (e) => e.key === "Enter" && submit());
  return h(
    "div.profile-form",
    field("Tu Riot ID", h("div.input-group", riot, detectBtn)),
    h("div.grid.grid-3", field("Región", platformSel), field("Elo objetivo", targetSel), field("Partidas a recuperar", backfillSel)),
    h("div.row", saveBtn, h("span.tiny.muted", "El elo objetivo es contra qué nivel se comparan tus estadísticas.")),
  );
}

function setupView(data) {
  const key = state.status?.key;
  const noKey = !key?.configured || key.status === "invalid";
  return h(
    "div.grid.coach-setup",
    h(
      "section.card",
      h("h3.card-title", icon("user"), "Guarda tu perfil"),
      h("p.muted", { style: { marginTop: 0 } }, "Así la app sabe quién eres, te sigue partida a partida y te compara con el elo al que quieres llegar."),
      noKey ? notice(h("span", "Sin una API key válida el perfil se guarda igual y empieza a recoger partidas en cuanto la pegues en ", h("a", { href: "#/datos" }, "Datos y API"), "."), "warn") : null,
      h("div", { style: { height: "12px" } }),
      profileForm(data, { onSaved: () => refresh(true) }),
    ),
    h(
      "section.card",
      h("h3.card-title", icon("info"), "Cómo funciona"),
      h(
        "ul.how",
        h("li", h("b", "Sin grabar nada. "), "Riot guarda de cada partida la posición de los 10 jugadores minuto a minuto y todos los eventos con su coordenada (kills, wards, objetivos, compras). Con eso se reconstruye la partida."),
        h("li", h("b", "Automático. "), "Cada 3 minutos se buscan tus partidas nuevas de la Grieta (normales, clasificatorias, Swiftplay y Clash). Si el cliente de LoL está abierto aquí, la partida se analiza en cuanto termina."),
        h("li", h("b", "Qué detecta. "), "Ganks del jungla rival estando adelantado o sin ward, muertes antes de objetivos (y si ganasteis la pelea o sacasteis algo a cambio), oro que te daba para tu build y no gastaste, objetivos regalados frente a buenos cambios al otro lado del mapa, tu build y su orden, minutos sin farmear, pathing de jungla, visión y fase de líneas."),
        h("li", h("b", "Propósitos. "), "Elige 2-3 propósitos y la app comprueba en cada partida si los cumples. Cuando los cumplas de forma constante, te avisa de que estás listo para clasificatorias."),
      ),
    ),
  );
}

function trackerStatus(t, p) {
  const c = t.counts || {};
  const analyzed = `${fmt.int(c.ready + c.partial + c.pending)} partidas analizadas`;
  let cls = "ok";
  let text = "Al día";
  let sub = analyzed;
  if (!p.linked && t.status !== "syncing") {
    cls = "warn";
    text = "Pendiente de vincular con Riot";
    sub = h("span", "Necesita una API key válida: ", h("a", { href: "#/datos" }, "Datos y API"));
  } else if (t.status === "syncing") {
    cls = "busy";
    text = t.queue ? `Descargando tus partidas… (quedan ${t.queue})` : "Buscando partidas nuevas…";
  } else if (t.status === "waiting_key") {
    cls = "warn";
    text = "En pausa: falta una API key válida";
    sub = h("span", "Pega una nueva en ", h("a", { href: "#/datos" }, "Datos y API"), " y sigue sola");
  } else if (t.status === "error") {
    cls = "bad";
    text = t.last_error || "Error";
  } else if (t.status === "paused") {
    cls = "warn";
    text = "Seguimiento en pausa";
  } else if (t.in_game) {
    cls = "busy";
    text = "Estás en partida: la analizo al terminar";
  } else if (t.waiting_after_game) {
    cls = "busy";
    text = "Partida terminada: esperando a que Riot la publique";
  } else if (t.status === "idle") {
    const next = t.next_check ? Math.max(0, Math.round((t.next_check * 1000 - Date.now()) / 60000)) : null;
    sub = `${analyzed}${t.last_check ? ` · comprobado ${fmt.ago(t.last_check)}` : ""}${next !== null ? ` · siguiente en ${next || "<1"} min` : ""}`;
  } else if (t.status === "starting" || t.status === "stopped") {
    cls = "warn";
    text = "Arrancando el seguimiento…";
  }
  return h("div.tracker", h(`span.status-dot.${cls}`), h("div", h("div.small", { style: { fontWeight: 650 } }, text), h("div.tiny.muted", sub)));
}

function profileCard(data) {
  const p = data.profile;
  const t = data.tracker;
  const targetSel = select((data.targets || []).map((x) => ({ value: x, label: TIER_LABEL[x] || x })), p.target_tier, async (v) => {
    try {
      await api.patch("/api/profile", { target_tier: v });
      toast(`Ahora te comparas con ${TIER_LABEL[v] || v}`, "success");
      refresh(true);
    } catch (err) {
      toast(err.message, "error");
    }
  }, "select select-sm");
  return h(
    "section.card.profile-card",
    h(
      "div.profile-head",
      p.profile_icon != null ? h("img.profile-icon.sm", { src: dd.profileIcon(p.profile_icon), alt: "" }) : h("div.profile-icon.sm.placeholder", icon("user")),
      h(
        "div",
        h("div.pname", p.game_name, h("span.muted", ` #${p.tag_line}`)),
        h("div.row-wrap.small.muted", h("span.pill", p.platform_label), p.summoner_level ? h("span", `Nivel ${p.summoner_level}`) : null, h("a.tiny", { href: `#/jugador/${p.platform}/${encodeURIComponent(p.game_name)}/${encodeURIComponent(p.tag_line)}` }, "Ver perfil público →")),
      ),
      h("div.spacer"),
      trackerStatus(t, p),
      h("div.filter", h("label", "Elo objetivo"), targetSel),
      h(
        "div.row",
        h("button.btn.btn-sm", { onclick: syncNow, title: "Comprobar ahora si hay partidas nuevas" }, icon("refresh"), "Buscar ahora"),
        h("button.btn.btn-ghost.btn-sm", { onclick: () => editProfile(data), title: "Cambiar cuenta, región o partidas a recuperar" }, icon("edit"), "Editar"),
      ),
    ),
  );
}

async function syncNow() {
  try {
    await api.post("/api/coach/sync");
    toast("Buscando partidas nuevas…");
    setTimeout(() => root && refresh(false), 1200);
  } catch (err) {
    toast(err.message, "error");
  }
}

function editProfile(data) {
  const m = modal("Tu perfil", h("div"));
  const removeBtn = (purge) =>
    h(
      `button.btn.btn-sm${purge ? ".btn-danger" : ".btn-ghost"}`,
      {
        onclick: async () => {
          const text = purge ? "¿Quitar el perfil y borrar todas tus partidas analizadas y propósitos? No se puede deshacer." : "¿Dejar de seguir esta cuenta? Tus partidas analizadas se conservan.";
          if (!confirm(text)) return;
          try {
            await api.del("/api/profile", { purge });
            window.dispatchEvent(new Event("tempu:status"));
            m.close();
            toast(purge ? "Perfil y partidas borrados" : "Perfil quitado");
            mode = null;
            refresh(true);
          } catch (err) {
            toast(err.message, "error");
          }
        },
      },
      icon("trash"),
      purge ? "Quitar y borrar partidas" : "Dejar de seguir",
    );
  mount(
    m.body,
    profileForm(data, { onSaved: () => { m.close(); lastCount = null; refresh(true); } }),
    h("div.divider"),
    h("div.row-wrap", h("span.small.muted", "Si guardas otra cuenta, empieza un historial nuevo (el anterior no se borra)."), h("span.spacer"), removeBtn(false), removeBtn(true)),
  );
}

// ------------------------------------------------------------------ resumen
function tabs(items, value, onChange) {
  return h(
    "div.tabs",
    items.map((it) => h(`button.tab${it.value === value ? ".active" : ""}`, { onclick: () => onChange(it.value) }, it.icon || null, it.label)),
  );
}

function setPrefs(patch) {
  prefs = { ...prefs, ...patch };
  save(PREFS, prefs);
  mount(bodyEl, loading("Recalculando…"));
  loadSummary();
}

function filtersBar(s) {
  const sources = Object.values(s.refs_source || {});
  return h(
    "div.row-wrap.coach-filters",
    tabs([{ value: "all", label: "Todas" }, { value: "normal", label: "Normales" }, { value: "ranked", label: "Clasificatorias" }], prefs.queue, (v) => setPrefs({ queue: v })),
    tabs([{ value: "all", label: "Todos los roles" }, ...(s.all_roles || []).map((r) => ({ value: r, label: ROLE_LABEL[r], icon: roleIcon(r) }))], prefs.role, (v) => setPrefs({ role: v })),
    select([10, 20, 30, 50, 100, 200].map((n) => ({ value: n, label: `Últimas ${n}` })), prefs.last, (v) => setPrefs({ last: Number(v) }), "select select-sm"),
    h("span.spacer"),
    h("span.tiny.muted", `Comparado con ${TIER_LABEL[s.target_tier] || s.target_tier} · referencia ${sources.includes("db") ? "de tu base de datos" : "orientativa"}`),
  );
}

function summaryView(s, data) {
  if (!s.games) {
    const t = data?.tracker || {};
    const linked = s.profile?.linked ?? data?.profile?.linked;
    const waiting = t.status === "syncing" || (t.counts && !t.counts.ready);
    const [title, text] = !linked
      ? ["Esperando tu API key de Riot", "En cuanto pegues una key válida en «Datos y API» se vinculará tu cuenta y empezarán a llegar tus partidas, sin hacer nada más."]
      : waiting
        ? ["Buscando tus partidas…", "La primera vez se descargan tus últimas partidas de la Grieta con su timeline: tarda unos minutos por el límite de la API de Riot. Irán apareciendo aquí solas."]
        : ["No hay partidas con estos filtros", "Prueba con otra cola, otro rol o más partidas."];
    return h("div.stack", filtersBar(s), h("section.card", empty(title, text, linked ? "match" : "key")));
  }
  const d = s.deaths || {};
  const hoard = metricRow(s, "gold_hoard_min");
  const idle = metricRow(s, "idle_pct");
  const builds = buildsCard(s);
  const champs = championsCard(s);
  const metricsCount = (s.metrics || []).length;
  const worse = (s.metrics || []).filter((m) => m.status === "bad").length;
  return h(
    "div.stack",
    filtersBar(s),
    h("div.grid.coach-top", readinessCard(s), kpisCard(s), goalsCard(s)),
    weaknessesCard(s),
    gamesCard(s),
    collapsible("progreso.muertes", [icon("map"), "Dónde y cómo mueres"], () => deathsCard(s), { open: true, summary: `${d.total ?? 0} muertes · ${d.early_total ?? 0} antes del 14` }),
    s.jungle ? collapsible("progreso.jungla", [icon("map"), "Tu pathing de jungla (minutos 1-8)"], () => jungleCard(s), { open: true, summary: `${s.jungle.games} ${s.jungle.games === 1 ? "partida" : "partidas"}` }) : null,
    collapsible("progreso.tiempo", [icon("clock"), "En qué pierdes el tiempo"], () => timeCard(s), {
      summary: [idle?.value != null ? `${fmt.num(idle.value, 0)} % de minutos vacíos` : null, hoard?.value != null ? `${fmt.num(hoard.value, 1)} min/partida con oro útil` : null].filter(Boolean).join(" · ") || null,
    }),
    builds ? collapsible("progreso.builds", [icon("crown"), "Tus builds frente a OP.GG"], builds, { summary: `${(s.builds || []).length} campeones` }) : null,
    collapsible("progreso.metricas", [icon("activity"), "Todas tus estadísticas frente a la referencia"], () => metricsCard(s), { summary: `${metricsCount} métricas · ${worse} por debajo` }),
    champs ? collapsible("progreso.campeones", [icon("crown"), "Tus campeones"], champs, { summary: `${(s.champions || []).length} campeones` }) : null,
  );
}

function ring(score, threshold, provisional = false) {
  const value = Math.max(0, Math.min(100, score ?? 0));
  const r = 44;
  const c = 2 * Math.PI * r;
  // Provisional (faltan partidas): gris, sin colores de veredicto.
  const color = score === null || score === undefined || provisional ? "var(--line-2)" : value >= threshold ? "var(--good)" : value >= threshold - 20 ? "var(--gold)" : "var(--bad)";
  const el = h("div.ring");
  el.innerHTML = `<svg viewBox="0 0 110 110"><circle cx="55" cy="55" r="${r}" fill="none" stroke="var(--line)" stroke-width="10"/><circle cx="55" cy="55" r="${r}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round" stroke-dasharray="${(value / 100) * c} ${c}" transform="rotate(-90 55 55)"/></svg>`;
  el.append(h("div.ring-value", h("b", score === null || score === undefined ? "—" : String(score)), h("small", provisional ? "provisional" : "/ 100")));
  if (provisional) el.title = "Nota provisional: aún no hay partidas suficientes para valorarlo con fiabilidad.";
  return el;
}

function readinessCard(s) {
  const r = s.readiness;
  const provisional = r.score !== null && r.score !== undefined && (r.missing_games || 0) > 0;
  return h(
    "section.card",
    h("h3.card-title", icon("trophy"), "¿Listo para clasificatorias?"),
    h(
      "div.ready-box",
      ring(r.score, r.threshold, provisional),
      h(
        "div.col",
        { style: { gap: "6px" } },
        r.ready ? h("span.pill.pill-good", "¡Sí! Puedes empezar") : provisional ? h("span.pill", "Faltan partidas") : h("span.pill.pill-warn", "Todavía no"),
        h("div.small", r.message),
        h("div.tiny.muted", `Fundamentos ${r.fundamentals !== null ? fmt.num(r.fundamentals, 1) + "/10" : "—"} · propósitos cumplidos ${r.goals_rate !== null ? r.goals_rate + " %" : "— (añade alguno)"} · últimas ${r.games} partidas`),
      ),
    ),
  );
}

function metricRow(s, key) {
  return (s.metrics || []).find((m) => m.key === key) || null;
}

function valueText(m, v = m?.value) {
  if (!m || v === null || v === undefined) return "—";
  return `${fmt.num(v, m.decimals)}${m.unit ? (m.unit === "%" ? " %" : m.unit) : ""}`;
}

function kpisCard(s) {
  const k = (key, label = null) => {
    const m = metricRow(s, key);
    if (!m) return null;
    return kpi(label || m.label, h(`span.${m.status === "good" ? "good" : m.status === "bad" ? "bad" : ""}`, valueText(m)), `ref. ${valueText(m, m.ref)}`);
  };
  return h(
    "section.card",
    h("h3.card-title", icon("chart"), `Tus últimas ${s.games} partidas`),
    h(
      "div.kpis",
      kpi("Winrate", h(`span.${wrClass(s.wr)}`, fmt.pct(s.wr, 0)), `${s.wins}V ${s.games - s.wins}D`),
      k("deaths_pre14", "Muertes antes del 14"),
      k("cs_min", "CS/min"),
      k("vision_min", "Visión/min"),
      k("kp", "Part. en kills"),
      k("idle_pct", "Minutos vacíos"),
    ),
  );
}

function goalsCard(s) {
  const goals = s.goals || [];
  const list = goals.length
    ? goals.map((g) =>
        h(
          "div.goal",
          h(
            "div.row",
            h("span.goal-label", g.label, g.role ? h("span.tiny.muted", ` (${ROLE_LABEL[g.role]})`) : null),
            h("span.spacer"),
            g.rate !== null ? h(`b.small.${g.rate >= 70 ? "good" : g.rate >= 40 ? "gold" : "bad"}`, `${g.rate} %`) : h("span.tiny.muted", "sin datos"),
            h("button.btn.btn-ghost.btn-icon.btn-sm", { title: "Quitar propósito", onclick: () => removeGoal(g) }, icon("close")),
          ),
          h(
            "div.goal-dots",
            [...g.history].reverse().map((x) => h(`a.gd.${x.ok ? "ok" : "ko"}`, { href: `#/repeticiones/${x.match_id}`, title: `${dd.name(x.champ)} · ${fmt.num(x.value, 1)} · ${x.ok ? "cumplido" : "no cumplido"}` })),
            g.streak > 1 ? h("span.tiny.good", `${g.streak} seguidas`) : null,
          ),
        ),
      )
    : [h("p.small.muted", { style: { margin: "0 0 8px" } }, "Ponte 2 o 3 propósitos concretos. En cada partida se comprueba si los cumples; los puntos débiles de abajo te sugieren cuáles.")];
  return h(
    "section.card",
    h("h3.card-title", icon("target"), "Propósitos", h("span.spacer"), h("button.btn.btn-sm", { onclick: () => addGoalModal(s) }, "Añadir")),
    h("div.col", { style: { gap: "10px" } }, list),
  );
}

async function addGoal(goal) {
  try {
    await api.post("/api/coach/goals", goal);
    toast(`Propósito añadido: ${goal.label}`, "success");
    loadSummary();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function removeGoal(g) {
  try {
    await api.del(`/api/coach/goals/${g.id}`);
    loadSummary();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function addGoalModal(s) {
  const m = modal("Nuevo propósito", loading());
  try {
    const { catalog } = await api.get("/api/coach/goals");
    let index = 0;
    const target = h("input.input", { type: "number", step: "any", value: catalog[0].target, style: { width: "110px" } });
    const roles = [{ value: "", label: "Cualquier rol" }, ...["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"].map((r) => ({ value: r, label: ROLE_LABEL[r] }))];
    let role = catalog[0].role || "";
    const roleSel = select(roles, role, (v) => (role = v));
    const preview = h("div.small.muted");
    const update = () => {
      const c = catalog[index];
      const value = Number(target.value);
      preview.textContent = `Se comprobará en cada partida: ${c.metric_label} ${c.op === "<=" ? "≤" : "≥"} ${fmt.num(value, Number.isInteger(value) ? 0 : 2)}`;
    };
    const choose = select(catalog.map((c, i) => ({ value: i, label: c.label })), 0, (v) => {
      index = Number(v);
      target.value = catalog[index].target;
      role = catalog[index].role || "";
      roleSel.value = role;
      update();
    });
    target.addEventListener("input", update);
    update();
    mount(
      m.body,
      h(
        "div.col",
        field("Propósito", choose),
        h("div.row-wrap", field("Objetivo", target), field("Rol", roleSel)),
        preview,
        h(
          "div.row",
          h(
            "button.btn.btn-primary",
            {
              onclick: async () => {
                const c = catalog[index];
                const value = Number(target.value);
                await addGoal({ metric: c.metric, op: c.op, target: value, role: role || null, label: c.template.replace("{t}", fmt.num(value, Number.isInteger(value) ? 0 : 2)) });
                m.close();
              },
            },
            icon("check"),
            "Añadir propósito",
          ),
        ),
      ),
    );
  } catch (err) {
    mount(m.body, errorBox(err));
  }
}

function weaknessesCard(s) {
  const active = new Set((s.goals || []).map((g) => `${g.metric}|${g.op}`));
  const items = s.weaknesses || [];
  const TOP = 3;
  return h(
    "section.card",
    h("h3.card-title", icon("alert"), "Qué haces mal (ordenado por lo que más te cuesta)"),
    items.length
      ? weakList(items, TOP, (w) =>
            h(
              `div.weak.${w.level}`,
              h("div.weak-sev", { title: `Gravedad ${fmt.num(w.severity, 0)}/100` }, h("span", { style: { height: `${Math.max(8, w.severity)}%` } })),
              h(
                "div.weak-body",
                h(
                  "div.row-wrap",
                  h("span.pill", w.category_label),
                  h("b", w.title),
                  w.trend?.improving === true ? h("span.pill.pill-good", { title: `Últimas: ${w.trend.recent} · antes: ${w.trend.older}` }, "Mejorando") : null,
                  w.trend?.improving === false ? h("span.pill.pill-bad", { title: `Últimas: ${w.trend.recent} · antes: ${w.trend.older}` }, "Empeorando") : null,
                ),
                h("div.small", { style: { marginTop: "4px" } }, w.detail),
                h("div.weak-tip", icon("spark"), h("span", w.tip)),
              ),
              w.goal
                ? active.has(`${w.goal.metric}|${w.goal.op}`)
                  ? h("span.pill.pill-gold", icon("check"), "Propósito activo")
                  : h("button.btn.btn-sm", { onclick: () => addGoal(w.goal), title: w.goal.label }, icon("target"), "Ponérmelo de propósito")
                : null,
            ),
        )
      : (s.weakness_sample ?? s.games) < (s.min_games_weakness ?? 5)
        ? notice(`Aún no hay muestra suficiente para señalar puntos débiles fiables: hacen falta al menos ${s.min_games_weakness ?? 5} partidas válidas del mismo rol (sin Swiftplay ni desconexiones) y llevas ${s.weakness_sample ?? 0}. Se calculan solas a medida que juegues.`, "info")
        : notice(`No hay puntos débiles claros frente a ${TIER_LABEL[s.target_tier] || s.target_tier} en estas ${s.reliable_games ?? s.games} partidas. Sube tu elo objetivo para exigirte más.`, "info"),
  );
}

/** Los ``top`` primeros a la vista y el resto en un desplegable. */
function weakList(items, top, render) {
  const first = items.slice(0, top).map(render);
  const rest = items.slice(top);
  return h(
    "div.col",
    { style: { gap: "10px" } },
    first,
    rest.length
      ? h("details.more", h("summary.btn.btn-ghost.btn-sm", `Ver ${rest.length} ${rest.length === 1 ? "punto débil más" : "puntos débiles más"}`), h("div.col", { style: { gap: "10px", marginTop: "10px" } }, rest.map(render)))
      : null,
  );
}

function tagBar(label, value, total, text, tone) {
  return h("div.tagbar", h("div.row", h("span.small", label), h("span.spacer"), h("b.small", text)), h(`div.tagbar-track.${tone}`, h("span", { style: { width: `${Math.min(100, (value / Math.max(1, total)) * 100)}%` } })));
}

function deathsCard(s) {
  const d = s.deaths || { points: [], tags: {} };
  const kinds = {};
  for (const p of d.points) {
    const k = p.tags.includes("gank") || p.tags.includes("jungler") ? "gank" : p.tags.includes("enemy_jungle") ? "invade" : p.tags.includes("solo") ? "solo" : p.tags.includes("isolated") ? "isolated" : "fight";
    kinds[k] = (kinds[k] || 0) + 1;
  }
  const context = ["fight_won", "fight_even", "traded", "game_end"];
  const tagRows = Object.entries(d.tags || {})
    .filter(([k]) => k !== "early" && !context.includes(k))
    .slice(0, 7)
    .map(([, t]) => tagBar(t.label, t.count, d.total, `${t.count} · ${fmt.pct((t.count / Math.max(1, d.total)) * 100, 0)}`, "bad"));
  const mitigated = context.map((k) => d.tags?.[k]).filter((t) => t?.count);
  const contextLine = mitigated.length
    ? h("div.tiny.muted", { style: { marginTop: "6px" } }, `Con contexto (pesan menos): ${mitigated.map((t) => `${t.label.toLowerCase()} ${t.count}`).join(" · ")}.`)
    : null;
  return h(
    "section.card",
    h("h3.card-title", icon("map"), "Dónde y cómo mueres", h("span.spacer"), h("span.tiny.muted", `${d.total} muertes · ${d.early_total} antes del 14`)),
    h(
      "div.map-split",
      deathMap(d.points, { onPick: (p) => (location.hash = `#/repeticiones/${p.match_id}`) }),
      h(
        "div.col",
        { style: { gap: "4px", minWidth: 0 } },
        h("div.legend.map-legend", Object.entries(DEATH_KINDS).map(([k, label]) => h("span", h(`i.death-${k}`), `${label}${kinds[k] ? ` (${kinds[k]})` : ""}`))),
        h("div.tiny.faint", "Siempre desde tu lado: tu base abajo a la izquierda. Pulsa un punto para abrir esa partida."),
        h("div.divider"),
        tagRows.length ? tagRows : h("span.small.muted", "Sin muertes registradas."),
        contextLine,
      ),
    ),
  );
}

function jungleCard(s) {
  const j = s.jungle;
  const starts = j.starts.slice(0, 3).map((st) => h("div.small", `Empiezas en ${CAMP[st.camp] || st.camp}${st.side ? ` (lado ${st.side === "top" ? "del barón" : "del dragón"})` : ""}: `, h("b", `${st.games} de ${j.games}`)));
  const presence = ["top", "mid", "bot"].map((z) => tagBar(z.toUpperCase(), j.presence[z] || 0, 100, `${j.presence[z] || 0} %`, "gold"));
  return h(
    "section.card",
    h("h3.card-title", icon("map"), "Tu pathing de jungla (minutos 1-8)", h("span.spacer"), h("span.tiny.muted", `${j.games} partidas`)),
    h(
      "div.map-split",
      pathMap(j.paths),
      h(
        "div.col",
        { style: { gap: "6px", minWidth: 0 } },
        h("div.legend", h("span", h("i", { style: { background: "var(--good)" } }), "Victoria"), h("span", h("i", { style: { background: "var(--bad)" } }), "Derrota")),
        h("div.tiny.faint", "Los números son los minutos de tu última partida. Siempre desde tu lado."),
        h("div.divider"),
        starts,
        j.first_gank_min !== null ? h("div.small", "Primera kill en línea, de media al ", h("b", `${fmt.num(j.first_gank_min, 1)}'`)) : h("div.small.warn", "No sueles conseguir kills en línea antes del 14."),
        h("div.small.muted", { style: { marginTop: "6px" } }, "Dónde pasas el early (minutos 2-14):"),
        presence,
      ),
    ),
  );
}

function timeCard(s) {
  const pick = ["idle_pct", "gold_hoard_min", "time_dead_pct", "deaths_unspent_gold", "obj_lost_dead", "obj_lost_far"].map((k) => metricRow(s, k)).filter(Boolean);
  return h(
    "section.card",
    h("h3.card-title", icon("clock"), "En qué pierdes el tiempo"),
    h("div.kpis", pick.map((m) => kpi(m.label, h(`span.${m.status === "good" ? "good" : m.status === "bad" ? "bad" : ""}`, valueText(m)), `ref. ${valueText(m, m.ref)}`))),
    h(
      "p.tiny.muted",
      { style: { marginTop: "10px" } },
      "Minuto vacío: desde el 15, un minuto entero sin farmear (menos de 3 CS), sin hacer daño a campeones, sin poner wards, sin volver a base y sin participar en kills, torres ni objetivos (los minutos de desconexión no cuentan). Oro útil: el que te daba para una pieza de tu siguiente objeto y con hueco para comprarla; ahorrar con el inventario lleno o la build terminada no es un error. Objetivo regalado: el rival lo coge, tú estás lejos y tu equipo no saca nada parecido a cambio.",
    ),
  );
}

function buildNote(n) {
  const cls = n.ok === true ? "good" : n.ok === false ? "bad" : "muted";
  return h(`li.${cls}`, h("span.mark", n.ok === true ? "✓" : n.ok === false ? "✗" : "•"), h("span", n.text), n.items?.length ? h("span.items", n.items.map((id) => itemImg(id))) : null);
}

function buildsCard(s) {
  const rows = s.builds || [];
  if (!rows.length) return null;
  return h(
    "section.card",
    h("h3.card-title", icon("crown"), "Tus builds", h("span.spacer"), h("span.tiny.muted", "Por campeón, frente a la build más jugada en OP.GG")),
    h(
      "div.col",
      { style: { gap: "10px" } },
      rows.map((r) =>
        h(
          "div.build-row",
          h("div.row", { style: { minWidth: 0 } }, champImg(r.champ), h("div", { style: { minWidth: 0 } }, h("b", dd.name(r.champ)), h("div.tiny.muted", `${ROLE_LABEL[r.role] || "—"} · ${r.games} partidas`))),
          h(
            "div",
            h("div.tiny.muted", "Tu núcleo de build"),
            r.core ? h("div.items", r.core.ids.map((id) => itemImg(id))) : r.first_item ? h("div.items", itemImg(r.first_item.id)) : h("span.small.muted", "—"),
            r.first_item ? h("div.tiny.muted", `1.er objeto: ${dd.itemName(r.first_item.id) || "?"} (${r.first_item.games} de ${r.games})`) : null,
          ),
          h(
            "div",
            h("div.tiny.muted", "Primer objeto"),
            h("b", r.first_item_min !== null && r.first_item_min !== undefined ? `min ${fmt.num(r.first_item_min, 1)}` : "—"),
            r.skill_max?.length ? h("div.tiny.muted", `Habilidades ${r.skill_max.join(" > ")}`) : null,
          ),
          r.compare
            ? h("div", h("div.tiny.muted", "Más jugada (OP.GG)"), h("div.items", r.compare.core.map((id) => itemImg(id))), h("ul.build-notes", r.compare.notes.slice(0, 3).map(buildNote)))
            : h("div.tiny.muted", "Sin build de referencia de OP.GG todavía: se descarga sola."),
        ),
      ),
    ),
  );
}

function divergeBar(m) {
  const dev = Math.max(-1, Math.min(1, -(m.dev || 0))); // positivo = mejor que la referencia
  const w = Math.abs(dev) * 50;
  return h("div.track", h("span", { style: dev >= 0 ? { left: "50%", width: `${w}%`, background: "var(--good)" } : { left: `${50 - w}%`, width: `${w}%`, background: "var(--bad)" } }));
}

function metricsCard(s) {
  const groups = {};
  for (const m of s.metrics || []) (groups[m.category] ||= []).push(m);
  const order = Object.keys(s.categories || {});
  return h(
    "section.card",
    h("h3.card-title", icon("activity"), "Todas tus estadísticas frente a la referencia"),
    h(
      "div.metric-groups",
      order
        .filter((c) => groups[c]?.length)
        .map((c) =>
          h(
            "div.metric-group",
            h("div.metric-group-title", s.categories[c]),
            groups[c].map((m) =>
              h(
                "div.diverge.metric-line",
                h("span.small", { title: `${m.games} partidas` }, m.label),
                divergeBar(m),
                h(`b.small.right.${m.status === "good" ? "good" : m.status === "bad" ? "bad" : ""}`, { title: `Tú ${valueText(m)} · referencia ${valueText(m, m.ref)}${m.trend ? ` · últimas ${m.trend.recent} vs antes ${m.trend.older}` : ""}` }, valueText(m), m.trend?.improving === true ? " ↑" : m.trend?.improving === false ? " ↓" : ""),
              ),
            ),
          ),
        ),
    ),
    h("p.tiny.muted", { style: { marginTop: "8px" } }, "Barra verde: mejor que la referencia de tu elo objetivo; roja: peor. ↑/↓: tus últimas partidas frente a las anteriores."),
  );
}

function chips(g) {
  const out = [];
  if (g.deaths_to_jungler_pre14) out.push(h("span.chip-bad", `${g.deaths_to_jungler_pre14}× jungla rival`));
  else if (g.deaths_ganked) out.push(h("span.chip-bad", `${g.deaths_ganked}× gank`));
  if (g.deaths_pre14 >= 2) out.push(h("span.chip-bad", `${g.deaths_pre14} muertes antes del 14`));
  if (g.obj_lost_dead) out.push(h("span.chip-bad", "objetivo perdido muerto"));
  if (g.obj_lost_far) out.push(h("span.chip-bad", g.obj_lost_far > 1 ? `${g.obj_lost_far} objetivos regalados` : "objetivo regalado"));
  if (g.gold_hoard_min >= 1) out.push(h("span.chip-warn", `${fmt.num(g.gold_hoard_min, 1)} min con oro útil`));
  if (g.idle_pct >= 25) out.push(h("span.chip-warn", `${fmt.num(g.idle_pct, 0)} % vacío`));
  if (g.obj_trades) out.push(h("span.chip-good", g.obj_trades > 1 ? `${g.obj_trades} buenos cambios` : "buen cambio"));
  if (g.obj_fair_trades) out.push(h("span.chip-muted", g.obj_fair_trades > 1 ? `${g.obj_fair_trades} cambios justos` : "cambio justo"));
  if (g.reliable === false) out.push(h("span.chip-muted", { title: "Swiftplay o con alguien desconectado 3 min o más: no cuenta en tus estadísticas" }, "no cuenta"));
  if (g.afk_min >= 1) out.push(h("span.chip-muted", `AFK ${fmt.num(g.afk_min, 0)} min`));
  if (!g.has_timeline) out.push(h("span.chip-muted", "esperando timeline"));
  return out;
}

export function scorePill(score) {
  if (score === null || score === undefined) return h("span.score", "—");
  return h(`span.score.${score >= 6.5 ? "hi" : score <= 3.5 ? "lo" : ""}`, { title: "Nota de fundamentos 0-10 (5 = como la referencia de tu elo objetivo)" }, fmt.num(score, 1));
}

function gamesCard(s) {
  return h(
    "section.card",
    h("h3.card-title", icon("match"), "Tus partidas", h("span.spacer"), h("a.tiny", { href: "#/repeticiones" }, "Todas tus repeticiones →")),
    h(
      "div.col",
      { style: { gap: "6px" } },
      (s.recent || []).map((g) =>
        h(
          `a.game-row.${g.win ? "win" : "loss"}`,
          { href: `#/repeticiones/${g.match_id}` },
          h("div.bar"),
          h("div.row", { style: { minWidth: 0 } }, champImg(g.champ), h("div", { style: { minWidth: 0 } }, h("div", { style: { fontWeight: 700 } }, dd.name(g.champ)), h("div.tiny.muted", `${g.role ? ROLE_LABEL[g.role] : "—"} · ${g.queue_label}`))),
          h("div", h("div.kda", `${g.kills} / ${g.deaths} / ${g.assists}`), h("div.tiny", { class: g.win ? "good" : "bad" }, g.win ? "Victoria" : "Derrota", h("span.muted", ` · ${fmt.duration(g.duration)}`))),
          h("div.tiny.muted", `${fmt.num(g.cs_min, 1)} CS/min`, h("br"), `KP ${fmt.pct(g.kp, 0)}`),
          h("div.row-wrap.game-chips", chips(g)),
          h("div.right", scorePill(g.score), h("div.tiny.muted", fmt.ago(g.game_start))),
        ),
      ),
    ),
  );
}

function championsCard(s) {
  if (!s.champions?.length) return null;
  return h(
    "section.card",
    h("h3.card-title", icon("crown"), "Tus campeones"),
    h(
      "div.table-wrap",
      h(
        "table.table",
        h("thead", h("tr", ["Campeón", "Rol", "Partidas", "WR", "KDA", "Nota"].map((c) => h("th", c)))),
        h(
          "tbody",
          s.champions.slice(0, 12).map((c) =>
            h(
              "tr",
              h("td", h("div.row", champImg(c.champ, "sm"), dd.name(c.champ))),
              h("td", c.role ? h("div.row", roleIcon(c.role), ROLE_LABEL[c.role]) : "—"),
              h("td.num", c.games),
              h("td", h(`b.${wrClass(c.wr)}`, fmt.pct(c.wr, 0))),
              h("td.num", fmt.num(c.kda, 2)),
              h("td", scorePill(c.score)),
            ),
          ),
        ),
      ),
    ),
  );
}

// ------------------------------------------------------------------ partida (piezas que reutiliza «Repeticiones»)
function minuteText(v) {
  return v !== null && v !== undefined ? `min ${fmt.num(v, 1)}` : "—";
}

export function buildCard(a) {
  const b = a.build;
  if (!b) {
    return a.items_known === false ? notice("Sin datos de objetos (Data Dragon no estaba disponible al analizarla): la build y el oro útil se calcularán al volver a analizarla.", "info") : null;
  }
  const cmp = a.build_compare;
  const steps = (b.order || []).map((o) => h("div.build-step", { title: dd.itemName(o.item) }, itemImg(o.item), h("span.tiny.muted", fmt.duration(o.t))));
  const backs = (b.backs || []).map((x) =>
    h(
      "div.build-back",
      { title: `${fmt.int(x.gold)} de oro${x.dead ? " (comprando muerto)" : ""}` },
      h("span.tiny.muted", fmt.duration(x.t)),
      h("div.items", x.items.map((id) => itemImg(id))),
      h("span.tiny.faint", fmt.int(x.gold)),
    ),
  );
  const healers = b.anti_heal?.healers || [];
  const dt = b.damage_taken || {};
  const ref = a.refs?.first_item_min;
  return h(
    "section.card",
    h("h3.card-title", icon("crown"), "Tu build", h("span.spacer"), h("span.tiny.muted", "Objetos terminados en el orden en que los compraste")),
    steps.length ? h("div.build-order", steps) : h("div.small.muted", "No llegaste a terminar ningún objeto."),
    h(
      "div.kpis",
      { style: { marginTop: "12px" } },
      kpi("Primer objeto", minuteText(b.first_item_min), ref ? `ref. min ${fmt.num(ref, 1)}` : null),
      kpi("Segundo objeto", minuteText(b.second_item_min)),
      kpi("Botas mejoradas", minuteText(b.boots_min)),
      kpi("Habilidades", b.skill_max?.length ? b.skill_max.join(" > ") : "—", b.r_delays?.length ? `R tarde ${b.r_delays.length} ${b.r_delays.length === 1 ? "vez" : "veces"}` : "R siempre a tiempo"),
    ),
    cmp
      ? h(
          "div.build-ref",
          h(
            "div.row-wrap",
            h("span.small", h("b", "Más jugada en OP.GG")),
            h("div.items", cmp.core.map((id) => itemImg(id))),
            cmp.boots?.length ? h("span.row", h("span.tiny.muted", "botas"), itemImg(cmp.boots[0])) : null,
            cmp.skill_max?.length ? h("span.tiny.muted", `habilidades ${cmp.skill_max.join(" > ")}`) : null,
          ),
          h("ul.build-notes", cmp.notes.map(buildNote)),
        )
      : null,
    h(
      "div.row-wrap.small",
      { style: { marginTop: "10px", gap: "6px 16px" } },
      h("span", "Daño recibido: ", h("b", `${dt.physical ?? 0} % físico`), ` · ${dt.magic ?? 0} % mágico · ${dt.true ?? 0} % verdadero`),
      healers.length
        ? h("span", "Curación rival: ", healers.slice(0, 3).map((x, i) => h("span", i ? " · " : "", champImg(x.champ, "xs"), ` ${fmt.int(x.per_min)}/min`)), b.anti_heal.has ? h("span.good", " · llevaste Heridas graves") : h("span.bad", " · sin Heridas graves"))
        : null,
    ),
    backs.length ? h("details.build-backs", h("summary.small", `Compras por vuelta a base (${backs.length})`), h("div.build-backs-list", backs)) : null,
  );
}

/** Gráficas de oro de una partida: ventaja frente a tu rival y oro sin gastar cada minuto. */
export function goldCharts(a) {
  const byPid = Object.fromEntries((a.players || []).map((p) => [p.pid, p]));
  const opp = byPid[a.opponent];
  const s = a.series;
  const out = [];
  if (s && opp) {
    const points = s.minute.map((x, i) => ({ x, y: (s.gold[i] ?? 0) - (s.opp_gold[i] ?? 0) })).filter((p) => p.y !== null);
    const markers = [
      ...(a.deaths || []).map((d) => ({ x: d.t / 60, team: 200, label: `${fmt.duration(d.t)} · mueres` })),
      ...(a.replay?.kills || []).filter((k) => k[1] === a.me).map((k) => ({ x: k[0] / 60, team: 100, label: `${fmt.duration(k[0])} · matas a ${dd.name(byPid[k[2]]?.champ)}` })),
    ];
    out.push(h("div", h("div.small", { style: { fontWeight: 650, marginBottom: "6px" } }, `Tu oro frente a ${dd.name(opp.champ)}`), lineChart(points, { height: 220, markers }), h("div.legend", h("span", h("i", { style: { background: "var(--blue)" } }), "Tus kills (arriba)"), h("span", h("i", { style: { background: "var(--red)" } }), "Tus muertes (abajo)"), h("span", "Línea: tu oro − el de tu rival"))));
  }
  if (s) {
    const caption = s.hoard
      ? "Naranja: te llegaba para una pieza de tu siguiente objeto, tenías hueco y no volviste (descontando peleas y objetivos). Gris claro: ahorro para un objeto completo con el inventario lleno o la build terminada, que no es un error."
      : "Por encima de la línea (1.500) estás paseando oro que no pelea: vuelve a base cuando puedas.";
    out.push(h("div", { style: { marginTop: out.length ? "16px" : 0 } }, h("div.small", { style: { fontWeight: 650, marginBottom: "6px" } }, "Oro sin gastar cada minuto"), goldBars(s), h("p.tiny.muted", caption)));
  }
  return out.length ? h("div", out) : h("div.small.muted", "Sin datos de oro por minuto.");
}

export function goldBars(s) {
  const values = s.current_gold.map((v) => v ?? 0);
  const max = Math.max(2000, ...values);
  const hoard = s.hoard || null; // análisis con inventario: minutos con oro útil sin gastar
  const wrap = h("div.gold-bars");
  values.forEach((v, i) => {
    const hpct = (v / max) * 100;
    const hot = hoard ? !!hoard[i] : v >= 1500;
    const saving = hoard && !hot && v >= 1500 && (s.useful_gold?.[i] ?? 0) < 1000;
    let title = `${Math.round(s.minute[i])}' · ${fmt.int(v)} de oro`;
    if (hot && hoard) title += ` · te llegaba para una compra de ${fmt.int(s.useful_gold?.[i])} de tu build`;
    if (saving) title += " · ahorrando: no te llegaba para nada que cupiera en tu inventario";
    wrap.append(h(`span${hot ? ".hot" : saving ? ".saving" : ""}`, { style: { height: `${hpct}%` }, title }));
  });
  if (!hoard) wrap.append(h("i.line", { style: { bottom: `${(1500 / max) * 100}%` } }));
  return wrap;
}

export function jungleGame(a) {
  const j = a.jungle;
  const steps = (j.path || []).map((p) => h("span.chip", `${p.minute}' ${p.camp ? CAMP[p.camp] + (p.camp_team !== a.team ? " (rival)" : "") : p.zone.startsWith("jungle") ? "jungla" : p.zone.replace("base_100", "base").replace("base_200", "base").replace("river", "río")}`));
  const m = a.metrics;
  return h(
    "section.card",
    h("h3.card-title", icon("map"), "Tu ruta de jungla"),
    h("div.chips", steps),
    h(
      "div.kpis",
      { style: { marginTop: "12px" } },
      kpi("Ganks con kill antes del 10", m.ganks_pre10 ?? "—", m.first_gank_min ? `primera al ${fmt.num(m.first_gank_min, 1)}'` : "ninguna en línea"),
      kpi("CS de jungla @10", m.jungle_cs10 ?? "—", m.jungle_cs_diff10 !== null && m.jungle_cs_diff10 !== undefined ? `Δ ${fmt.signed(m.jungle_cs_diff10, 0)} vs su jungla` : null),
      kpi("Minutos invadiendo", m.invade_min ?? 0, `${m.enemy_jungle_cs ?? 0} campamentos rivales`),
      kpi("Ganks rivales lejos de ti", m.enemy_ganks_far ?? 0, m.mirror_pct !== null && m.mirror_pct !== undefined ? `mismo lado que su jungla ${m.mirror_pct} %` : null),
    ),
  );
}

function devOf(info, value, ref) {
  const scale = Math.max(Math.abs(ref), info.floor ?? 0.5);
  return info.better === "lower" ? (value - ref) / scale : (ref - value) / scale;
}

export function gameMetrics(a) {
  const info = a.metric_info || [];
  const groups = {};
  for (const mi of info) {
    if (mi.roles && !mi.roles.includes(a.role)) continue;
    const value = a.metrics?.[mi.key];
    if (value === null || value === undefined) continue;
    const ref = a.refs?.[mi.key];
    (groups[mi.category_label] ||= []).push({ mi, value, ref });
  }
  const unit = (mi) => (mi.unit === "%" ? " %" : mi.unit || "");
  return h(
    "section.card",
    h("h3.card-title", icon("chart"), `Esta partida frente a ${TIER_LABEL[a.target_tier] || a.target_tier}`),
    h(
      "div.metric-groups",
      Object.entries(groups).map(([label, rows]) =>
        h(
          "div.metric-group",
          h("div.metric-group-title", label),
          rows.map(({ mi, value, ref }) => {
            const dev = ref !== undefined ? devOf(mi, value, ref) : 0;
            const cls = ref === undefined ? "" : dev <= -0.05 ? "good" : dev >= 0.15 ? "bad" : "";
            return h("div.row.metric-line", h("span.small", mi.label), h("span.spacer"), ref !== undefined ? h("span.tiny.muted", `ref. ${fmt.num(ref, mi.decimals)}${unit(mi)}`) : null, h(`b.small.${cls}`, `${fmt.num(value, mi.decimals)}${unit(mi)}`));
          }),
        ),
      ),
    ),
  );
}
