// Analizador de draft: probabilidad de victoria, nota, líneas, sinergias y composición.
import { api, filterParams, isPending } from "../api.js";
import { curveChart, gauge } from "../charts.js";
import { champPicker } from "../components/picker.js";
import { dd, loadJSON, ROLE_LABEL, ROLES, save, state } from "../state.js";
import { patchBadge, champImg, collapsible, confidenceEl, debounce, empty, errorBox, fmt, h, icon, itemImg, loading, modal, mount, notice, roleIcon, runeImg, sourceBadges, toast, verdictEl, wrClass } from "../ui.js";
import { openMatchup } from "./matchups.js";

const STORE = "tempu.draft.v2";
let draft = loadJSON(STORE, { ally: {}, enemy: {} });
let root = null;
let summaryEl = null;
let buildsEl = null;
let laneEls = {};
let bottomEl = null;
let pickers = { ally: {}, enemy: {} };
let lastResult = null;
let seq = 0;
// Recomendaciones automáticas por hueco (sin pulsar nada): caché por draft + filtros.
const recsCache = new Map();
let recsShown = null; // { key, res } del último lote ya recibido

const taken = () => new Set([...Object.values(draft.ally), ...Object.values(draft.enemy)].filter(Boolean));

function persist() {
  save(STORE, draft);
}

export function destroy() {
  root = null;
}

export async function render(container) {
  root = container;
  pickers = { ally: {}, enemy: {} };
  laneEls = {};
  summaryEl = h("div.draft-summary");
  bottomEl = h("div.grid.grid-3", { style: { marginTop: "16px" } });
  buildsEl = h("div", { style: { marginTop: "16px" } });

  const board = h("div.draft-board");
  board.append(
    h("div.team-head.blue", h("span.pill.pill-blue.pill-dot", "Tu equipo")),
    h("div.team-head", { style: { justifyContent: "center", color: "var(--muted)" } }, "Enfrentamiento directo"),
    h("div.team-head.red", h("span.pill.pill-red.pill-dot", "Rival")),
  );
  for (const role of ROLES) {
    board.append(slot("ally", role));
    laneEls[role] = h("div.lane-card.empty", "Elige los campeones");
    board.append(laneEls[role]);
    board.append(slot("enemy", role));
  }

  mount(
    container,
    h(
      "div.section-head",
      h(
        "div",
        h("h2", "Analizador de draft"),
        h(
          "p",
          { title: "Probabilidad de victoria estimada con los enfrentamientos de línea (OP.GG + Lolalytics + tu base de datos + fuerza de cada campeón) y las sinergias entre aliados. Funciona también con el draft a medias. ↑/↓: mejorado o debilitado en el último parche." },
          "Probabilidad de victoria según líneas y sinergias; funciona también con el draft a medias. ⓘ",
        ),
      ),
      h("div.spacer"),
      h("button.btn", { onclick: swapTeams, title: "Intercambiar equipos" }, icon("swap"), "Intercambiar"),
      h("button.btn.btn-ghost", { onclick: clearDraft }, icon("trash"), "Limpiar"),
    ),
    summaryEl,
    board,
    buildsEl,
    bottomEl,
  );
  analyze();
}

export function onFilters() {
  analyze();
}

// Cambios del último parche (notas oficiales de Riot): ↑ mejorado / ↓ debilitado junto a cada campeón.
let patchInfo = null;
const badgeEls = { ally: {}, enemy: {} };
function loadPatchInfo() {
  if (patchInfo) return;
  patchInfo = { patch: null, champions: {} };
  api
    .get("/api/patches")
    .then((res) => {
      patchInfo = res.latest || patchInfo;
      for (const side of ["ally", "enemy"]) for (const role of ROLES) updateBadge(side, role);
    })
    .catch(() => {});
}
function updateBadge(side, role) {
  const el = badgeEls[side][role];
  if (!el) return;
  const champ = draft[side][role];
  const verdict = champ ? patchInfo?.champions?.[String(champ)] : null;
  mount(el, verdict && verdict !== "adjust" ? patchBadge(verdict, patchInfo.patch) : null);
}

function slot(side, role) {
  loadPatchInfo();
  const badge = h("span.slot-patch");
  badgeEls[side][role] = badge;
  const picker = champPicker({
    value: draft[side][role] || null,
    placeholder: `${ROLE_LABEL[role]} ${side === "ally" ? "aliado" : "rival"}`,
    exclude: taken,
    recommend: () => slotRecs(side, role).then((res) => pickerRecs(res, side, role)),
    pool: () => rolePool(role),
    onChange: (id) => {
      if (id) draft[side][role] = id;
      else delete draft[side][role];
      persist();
      updateBadge(side, role);
      analyzeSoon();
    },
  });
  pickers[side][role] = picker;
  queueMicrotask(() => updateBadge(side, role));
  return h(`div.slot.${side === "ally" ? "blue" : "red"}`, h("div.slot-role", { title: ROLE_LABEL[role] }, roleIcon(role)), picker, badge);
}

// ------------------------------------------------------------ recomendaciones
const recsKey = () => JSON.stringify([draft.ally, draft.enemy, state.filters]);
const emptySlots = () => [
  ...ROLES.filter((r) => !draft.ally[r]).map((role) => ({ side: "ally", role })),
  ...ROLES.filter((r) => !draft.enemy[r]).map((role) => ({ side: "enemy", role })),
];

function fetchRecs(key, slots) {
  if (!recsCache.has(key)) {
    const promise = api.post("/api/draft/suggest-batch", { ally: draft.ally, enemy: draft.enemy, slots }, filterParams({ limit: 8 }));
    recsCache.set(key, promise);
    // Si faltaban datos (aún descargándose) no se guarda: la recarga suave y el siguiente cambio vuelven a pedirlo.
    promise.then((data) => isPending(data) && recsCache.get(key) === promise && recsCache.delete(key)).catch(() => recsCache.delete(key));
    while (recsCache.size > 30) recsCache.delete(recsCache.keys().next().value);
  }
  return recsCache.get(key);
}

/** Campeones que se juegan en cada línea (con los filtros actuales): lo que ofrece el desplegable de cada hueco. */
const poolCache = new Map();
function rolePool(role) {
  const key = JSON.stringify(state.filters);
  if (!poolCache.has(key)) {
    const promise = api.get("/api/draft/roles", filterParams()).then((res) => {
      if (isPending(res)) poolCache.delete(key); // faltaban tier lists: se vuelve a pedir la próxima vez
      return res.roles || {};
    });
    promise.catch(() => poolCache.delete(key));
    poolCache.set(key, promise);
  }
  return poolCache.get(key).then((roles) => new Set(roles[role] || []));
}

/** Recomendaciones del hueco: del lote de huecos vacíos o, si está ocupado, de una petición propia. */
function slotRecs(side, role) {
  const key = recsKey();
  const slots = emptySlots();
  const base = slots.length ? fetchRecs(key, slots) : Promise.resolve({ slots: {}, pool: {} });
  return base.then((res) => res.slots[`${side}:${role}`] ? res : fetchRecs(`${key}|${side}:${role}`, [{ side, role }]));
}

function recTitle(s, opp) {
  return opp
    ? `vs ${dd.name(opp)}: ${fmt.pct(s.lane_wr)} en ${fmt.int(s.lane_games)} partidas${s.synergy_pp ? ` · sinergias ${fmt.signed(s.synergy_pp, 1, " pp")}` : ""}`
    : `WR general ${fmt.pct(s.base_wr)}${s.synergy_pp ? ` · sinergias ${fmt.signed(s.synergy_pp, 1, " pp")}` : ""}`;
}

function pickerRecs(res, side, role) {
  const slotRes = res.slots[`${side}:${role}`];
  if (!slotRes) return null;
  const opp = slotRes.opponent;
  const sections = [
    {
      title: side === "ally" ? (opp ? `Recomendados contra ${dd.name(opp)}` : "Recomendados") : "Mejores picks para el rival",
      items: slotRes.best.slice(0, 6).map((s) => ({ champ: s.champ, meta: { text: fmt.signed(s.score_pp, 1, " pp"), cls: s.score_pp >= 0 ? "good" : "bad", title: recTitle(s, opp) } })),
    },
  ];
  const pool = side === "ally" ? (res.pool?.[role] || []).filter((p) => !taken().has(p.champ) || p.champ === draft[side][role]) : [];
  if (pool.length) {
    sections.push({ title: "Tus campeones en este rol", items: pool.slice(0, 5).map((p) => ({ champ: p.champ, meta: { text: `${p.games} p · ${fmt.pct(p.wr, 0)}`, cls: wrClass(p.wr), title: "De tus partidas analizadas en «Mi progreso»" } })) });
  }
  const scores = new Map(Object.entries(slotRes.scores || {}).map(([c, v]) => [Number(c), { text: fmt.signed(v, 1), cls: v >= 0 ? "good" : "bad", title: "Lo que aporta frente a un 50 % (pp)" }]));
  return { sections, scores };
}

function pickRec(role, champ) {
  draft.ally[role] = champ;
  pickers.ally[role].setValue(champ);
  persist();
  analyze();
}

/** Pinta en el centro de cada línea sin aliado los mejores picks (se actualiza solo). */
function refreshRecs() {
  const key = recsKey();
  const slots = emptySlots();
  if (!slots.some((s) => s.side === "ally")) return;
  fetchRecs(key, slots)
    .then((res) => {
      if (!root || key !== recsKey()) return;
      recsShown = { key, res };
      for (const role of ROLES) {
        const box = laneEls[role]?.querySelector(".rec-strip");
        if (box) fillStrip(box, role, res);
      }
    })
    .catch(() => {
      for (const role of ROLES) {
        const box = laneEls[role]?.querySelector(".rec-strip");
        if (box) mount(box, h("span.tiny.faint", "Sin recomendaciones ahora mismo"));
      }
    });
}

function fillStrip(box, role, res) {
  const slotRes = res.slots[`ally:${role}`];
  if (!slotRes || !slotRes.best.length) return mount(box, h("span.tiny.faint", "Sin datos suficientes para recomendar"));
  const pool = new Set((res.pool?.[role] || []).map((p) => p.champ));
  const opp = slotRes.opponent;
  mount(
    box,
    h("span.rec-label", opp ? `Counters` : "Recomendados"),
    h(
      "div.rec-icons",
      slotRes.best.slice(0, 5).map((s) =>
        h(
          `button.rec${pool.has(s.champ) ? ".mine" : ""}`,
          { type: "button", title: `${dd.name(s.champ)} · ${fmt.signed(s.score_pp, 1, " pp")}\n${recTitle(s, opp)}${pool.has(s.champ) ? "\nEstá en tu pool" : ""}`, onclick: () => pickRec(role, s.champ) },
          champImg(s.champ, "sm"),
          h(`span.${s.score_pp >= 0 ? "good" : "bad"}`, fmt.signed(s.score_pp, 1)),
        ),
      ),
    ),
    h("a.tiny", { href: "javascript:void 0", onclick: () => openSuggest("ally", role), title: "Ver la lista completa y los picks a evitar" }, "más"),
  );
}

function stripFor(role) {
  const box = h("div.rec-strip", h("span.rec-label", "Recomendados"), h("div.rec-icons", [0, 1, 2, 3, 4].map(() => h("span.rec.skeleton"))));
  if (recsShown?.key === recsKey()) fillStrip(box, role, recsShown.res);
  return box;
}

function swapTeams() {
  draft = { ally: draft.enemy, enemy: draft.ally };
  persist();
  for (const role of ROLES) {
    pickers.ally[role].setValue(draft.ally[role] || null);
    pickers.enemy[role].setValue(draft.enemy[role] || null);
    updateBadge("ally", role);
    updateBadge("enemy", role);
  }
  analyze();
}

function clearDraft() {
  draft = { ally: {}, enemy: {} };
  persist();
  for (const role of ROLES) {
    pickers.ally[role].setValue(null);
    pickers.enemy[role].setValue(null);
    updateBadge("ally", role);
    updateBadge("enemy", role);
  }
  analyze();
}

const analyzeSoon = debounce(() => analyze(), 250);

async function analyze() {
  if (!root) return;
  const mine = ++seq;
  const picks = Object.keys(draft.ally).length + Object.keys(draft.enemy).length;
  if (!picks) {
    lastResult = null;
    mount(summaryEl, h("div.card", { style: { gridColumn: "1 / -1" } }, empty("Empieza el draft", "Elige campeones en cualquier orden: el análisis se actualiza solo. Los mejores picks de cada hueco salen en el centro de cada línea y arriba de cada selector.", "draft")));
    ROLES.forEach((r) => renderLane(r, null));
    mount(bottomEl);
    mount(buildsEl);
    refreshRecs();
    return;
  }
  refreshRecs();
  summaryEl.style.opacity = "0.6";
  const slow = setTimeout(() => {
    if (mine === seq) toast("Descargando datos de OP.GG para este draft (solo la primera vez)…");
  }, 1500);
  try {
    const result = await api.post("/api/draft/analyze", { ally: draft.ally, enemy: draft.enemy }, filterParams());
    if (mine !== seq || !root) return;
    lastResult = result;
    renderSummary(result);
    result.lanes.forEach((lane) => renderLane(lane.role, lane));
    renderBottom(result);
    loadBuilds(mine);
  } catch (err) {
    if (mine === seq) mount(summaryEl, h("div", { style: { gridColumn: "1 / -1" } }, errorBox(err)));
  } finally {
    clearTimeout(slow);
    summaryEl.style.opacity = "1";
  }
}

// ------------------------------------------------------------------ builds recomendadas
async function loadBuilds(mine) {
  if (!buildsEl) return;
  if (!buildsEl.firstChild) mount(buildsEl, h("section.card", h("h3.card-title", icon("crown"), "Builds recomendadas"), loading("Preparando la build de cada jugador…")));
  let res;
  try {
    res = await api.post("/api/draft/builds", { ally: draft.ally, enemy: draft.enemy }, filterParams());
  } catch (err) {
    if (mine === seq) mount(buildsEl, errorBox(err));
    return;
  }
  if (mine !== seq || !root) return;
  if (!res.ally.length && !res.enemy.length) return mount(buildsEl);
  mount(
    buildsEl,
    h(
      "section.card",
      h("h3.card-title", icon("crown"), "Builds recomendadas", h("span.spacer"), h("span.tiny.muted.hide-sm", "contra su rival de línea y ajustadas al equipo rival · pulsa una para ver el porqué")),
      res.ally.length ? h("div.build-rows", res.ally.map(buildRow)) : h("div.small.muted", "Elige campeones de tu equipo para ver su build."),
      res.enemy.length
        ? h("div", { style: { marginTop: "10px" } }, collapsible("draft.builds.rival", [icon("crown"), "Builds del rival"], () => h("div.build-rows", res.enemy.map(buildRow)), { cls: "flat", summary: `${res.enemy.length} ${res.enemy.length === 1 ? "campeón" : "campeones"} · para saber qué van a construir` }))
        : null,
    ),
  );
}

function buildRow(p) {
  const boots = p.boots;
  const draftPicks = (p.build || []).filter((x) => x.kind === "situational").length + (boots?.situational ? 1 : 0);
  return h(
    "button.build-row",
    { type: "button", onclick: () => openBuild(p) },
    h("span.row", { style: { gap: "6px", minWidth: 0 } }, roleIcon(p.role), champImg(p.champ, "sm"), h("b.small.ellipsis", dd.name(p.champ))),
    h(
      "span.build-items",
      boots ? h(`span${boots.situational ? ".build-swap" : ""}`, { title: `${dd.itemName(boots.item)} · ${boots.reason}` }, itemImg(boots.item)) : null,
      (p.build || []).map((x) => h(`span${x.kind === "situational" ? ".build-swap" : ""}`, { title: `${dd.itemName(x.item)} · ${x.reason}` }, itemImg(x.item))),
      !p.build?.length ? h("span.tiny.muted", "sin datos de build todavía") : null,
    ),
    p.runes?.keystone ? runeImg(p.runes.keystone, 24) : h("span"),
    h("span.tiny.muted.hide-sm", p.opponent ? `vs ${dd.name(p.opponent)}` : ""),
    draftPicks ? h("span.pill.pill-gold", { title: "Objetos elegidos por cómo es el equipo rival (en dorado)" }, `${draftPicks} por el draft`) : h("span"),
  );
}

function openBuild(p) {
  const itemRow = (x, extra = null) =>
    h(
      "div.row",
      { style: { gap: "10px", alignItems: "flex-start" } },
      h(`span${x.kind === "situational" || x.situational ? ".build-swap" : ""}`, itemImg(x.item)),
      h("div", { style: { minWidth: 0 } }, h("div.small", h("b", dd.itemName(x.item) || `Objeto ${x.item}`), extra), h("div.tiny.muted", x.reason)),
    );
  const e = p.enemy || {};
  modal(
    h("div.row", { style: { gap: "10px" } }, champImg(p.champ, "lg"), h("div", h("h3", `Build de ${dd.name(p.champ)}`), h("div.tiny.muted", `${ROLE_LABEL[p.role]}${p.opponent ? ` · contra ${dd.name(p.opponent)}` : ""}${e.phys || e.magic ? ` · daño rival ${fmt.int(e.phys)} % físico / ${fmt.int(e.magic)} % mágico` : ""}`))),
    h(
      "div.col",
      { style: { gap: "16px" } },
      p.build?.length
        ? h(
            "div.col",
            { style: { gap: "10px" } },
            h("div.small", h("b", "Build para este draft"), h("span.tiny.muted", "  · en dorado, lo que cambia por el equipo rival")),
            p.boots ? itemRow({ ...p.boots, kind: p.boots.situational ? "situational" : "" }, p.boots.replaces ? h("span.muted", ` en vez de ${dd.itemName(p.boots.replaces)}`) : null) : null,
            p.build.map((x, i) => itemRow(x, h("span.tiny.muted", ` · ${i + 1}.º objeto`))),
          )
        : h("div.small.muted", "Aún no hay datos de build para este campeón en este rol (se descargan en segundo plano: vuelve a mirar en un momento)."),
      p.runes?.names?.length || p.skills || p.starter?.length
        ? h(
            "div.col",
            { style: { gap: "6px" } },
            p.runes?.names?.length ? h("div.small", h("span.row", { style: { gap: "6px" } }, runeImg(p.runes.keystone, 22), h("span", p.runes.names.map((n, i) => dd.runeName(p.runes.ids?.[i]) || n).join(" · ")))) : null,
            p.starter?.length ? h("div.row", { style: { gap: "6px" } }, h("span.tiny.muted", "Inicio"), h("span.build-items", p.starter.map((i) => itemImg(i)))) : null,
            p.skills ? h("div.small.muted", `Habilidades: ${p.skills}`) : null,
          )
        : null,
      p.team_items?.length ? h("div.col", { style: { gap: "8px" } }, h("div.small", h("b", "Para el equipo (support)")), p.team_items.map((x) => itemRow({ ...x, kind: "situational" }))) : null,
      p.alternatives?.length
        ? collapsible("draft.build.alternativas", [icon("crown"), "Otras opciones"], () => h("div.col", { style: { gap: "8px" } }, p.alternatives.map((x) => itemRow(x))), { cls: "flat", summary: p.alternatives.map((x) => dd.itemName(x.item)).join(" · ") })
        : null,
      p.otp ? h("div.small.muted", `Sus OTPs (${TIER_NAMES[p.otp.tier] || p.otp.tier}, ${fmt.int(p.otp.games)} partidas): ${p.otp.path?.value ? `build más jugada ${p.otp.path.value}` : ""}${p.otp.skill_max?.value ? ` · suben ${p.otp.skill_max.value}` : ""}`) : null,
      h("div.tiny.muted", `Fuentes: ${(p.sources || []).join(", ") || "ninguna todavía"}${p.matchup?.games ? ` · núcleo contra ${dd.name(p.matchup.opponent)}: ${fmt.int(p.matchup.games)} partidas en OP.GG` : ""}`),
    ),
  );
}

const TIER_NAMES = { CHALLENGER: "Aspirante", GRANDMASTER: "Gran Maestro", MASTER: "Maestro", DIAMOND: "Diamante", EMERALD: "Esmeralda", PLATINUM: "Platino" };

// ------------------------------------------------------------------ resumen
function gradeLetter(grade, cls = "grade-letter") {
  return h(`div.${cls}.g-${grade.letter}`, grade.letter);
}

function renderSummary(r) {
  const p = r.win_probability;
  const probCard = h(
    "section.card",
    h("h3.card-title", icon("chart"), "Probabilidad de victoria"),
    h(
      "div.grade-box",
      gauge(p, { label: `IC 90 %: ${fmt.pct(r.interval[0])} – ${fmt.pct(r.interval[1])}`, low: r.interval[0], high: r.interval[1] }),
      h("div.col", { style: { gap: "6px" } }, gradeLetter(r.grade), h("div", { style: { fontWeight: 700 } }, r.grade.label), h("div.row", h("span.tiny.muted", "Confianza"), confidenceEl({ level: r.confidence.level + 1, label: r.confidence.label }))),
    ),
    r.lanes_filled < 5 ? h("div.tiny.muted", { style: { marginTop: "6px" } }, `Draft a medias: ${r.lanes_filled}/5 líneas con ambos campeones.`) : null,
  );

  const team = (side, data, label) =>
    data
      ? h(
          `div.team-strength.${side === "ally" ? "blue" : "red"}`,
          h(`div.mini-grade.g-${data.grade.letter}`, data.grade.letter),
          h("div", { style: { minWidth: 0 } }, h("div", { style: { fontWeight: 700 } }, label), h("div.small.muted", `WR frente a un equipo medio: `, h(`b.${wrClass(data.wr_vs_avg)}`, fmt.pct(data.wr_vs_avg)))),
          h("div.spacer"),
          h("div.right", h("div.tiny.muted", "Sinergias"), h(`div.impact.${data.synergy_pp >= 0 ? "good" : "bad"}`, fmt.signed(data.synergy_pp, 1, " pp"))),
        )
      : h(`div.team-strength.${side === "ally" ? "blue" : "red"}`, h("span.muted", `${label}: sin picks`));

  const teamsCard = h(
    "section.card",
    h("h3.card-title", icon("crown"), "Calificación por equipo"),
    h("div.col", team("ally", r.teams.ally, "Tu equipo"), team("enemy", r.teams.enemy, "Rival")),
    h("div.tiny.muted", { style: { marginTop: "8px", cursor: "help" }, title: "Fuerza de los picks por sí solos (WR de cada campeón en su rol + sinergias), sin contar contra quién juegan." }, "Qué mide ⓘ"),
  );

  const compCard = h("section.card", h("h3.card-title", icon("spark"), "Composición"), compositionBlock(r.composition));
  mount(summaryEl, probCard, teamsCard, compCard);
}

function damageBar(profile) {
  if (!profile) return h("div.dmg-bar");
  return h(
    "div.dmg-bar",
    { title: `Físico ${fmt.pct(profile.phys)} · Mágico ${fmt.pct(profile.magic)} · Verdadero ${fmt.pct(profile.true)}` },
    h("span.dmg-phys", { style: { width: `${profile.phys || 0}%` } }),
    h("span.dmg-magic", { style: { width: `${profile.magic || 0}%` } }),
    h("span.dmg-true", { style: { width: `${profile.true || 0}%` } }),
  );
}

function compositionBlock(comp) {
  const row = (label, c, cls) =>
    c
      ? h(
          "div.stack-sm",
          h("div.row", h(`span.small.${cls}`, label), h("span.spacer"), h("span.tiny.muted", `${fmt.pct(c.damage_profile?.phys, 0)} AD · ${fmt.pct(c.damage_profile?.magic, 0)} AP`)),
          damageBar(c.damage_profile),
          c.frontline !== null || c.cc !== null
            ? h(
                "div.tiny.muted",
                { style: { cursor: "help" }, title: [c.frontline !== null ? `Primera línea ×${fmt.num(c.frontline, 2)}` : null, c.cc !== null ? `Control ×${fmt.num(c.cc, 2)}` : null].filter(Boolean).join(" · ") },
                [c.frontline !== null ? "Primera línea" : null, c.cc !== null ? "control" : null].filter(Boolean).join(" y ") + " ⓘ",
              )
            : null,
          ...(c.warnings || []).map((w) => h("div.tiny.warn", "⚠ ", w)),
        )
      : null;
  const sourceNote = comp.ally?.source === "data" ? "Daño real medido en tus partidas." : comp.ally?.source === "opgg" ? "Tipo de daño según OP.GG (AD/AP)." : "Estimado con los atributos de Data Dragon.";
  return h(
    "div.col",
    row("Tu equipo", comp.ally, "gold"),
    row("Rival", comp.enemy, "bad"),
    h("div.legend", h("span", h("i.dmg-phys"), "Físico"), h("span", h("i.dmg-magic"), "Mágico"), h("span", h("i.dmg-true", { style: { background: "#e7eaf0" } }), "Verdadero")),
    h("div.tiny.faint", { style: { cursor: "help" }, title: sourceNote }, "Fuente del daño ⓘ"),
  );
}

// ------------------------------------------------------------------ líneas
function renderLane(role, lane) {
  const el = laneEls[role];
  if (!el) return;
  el.className = "lane-card";
  const needsAlly = !draft.ally[role];
  if (!lane || lane.kind === "empty") {
    if (needsAlly) {
      mount(el, stripFor(role));
      return;
    }
    el.classList.add("empty");
    mount(el, "Elige los campeones");
    return;
  }
  if (lane.kind === "solo") {
    const who = lane.ally ? "aliado" : "rival";
    mount(
      el,
      h("div.lane-top", h(`span.lane-wr.${wrClass(lane.wr_adj)}`, fmt.pct(lane.wr_adj)), h("span.small.muted", `WR general del ${who} en ${ROLE_LABEL[role]}`)),
      needsAlly ? stripFor(role) : h("div.lane-meta", h("span", "Falta el otro campeón para analizar el enfrentamiento"), h(`span.impact.${lane.impact_pp >= 0 ? "good" : "bad"}`, fmt.signed(lane.impact_pp, 1, " pp"))),
    );
    return;
  }
  const opgg = lane.opgg || {};
  const laneWinner = opgg.lane_advantage ? (opgg.lane_advantage === lane.ally ? "good" : opgg.lane_advantage === lane.enemy ? "bad" : "") : "";
  const playStyle = { aggressive: "agresivo", defensive: "defensivo", even: "igualado" }[opgg.play_style] || opgg.play_style;
  const src = lane.sources || {};
  const detail = [
    lane.ci ? `IC 95 %: ${fmt.pct(lane.ci[0])} – ${fmt.pct(lane.ci[1])}` : null,
    `Esperado solo por la fuerza de cada campeón (log5): ${fmt.pct(lane.expected)}`,
    lane.delta !== undefined ? `Δ ventaja propia del enfrentamiento: ${fmt.signed(lane.delta, 1)}` : null,
    src.opgg ? `OP.GG: ${fmt.pct(src.opgg.wr)} en ${fmt.int(src.opgg.games)} partidas` : null,
    src.own ? `Tu base de datos: ${fmt.pct(src.own.wr)} en ${fmt.int(src.own.games)} partidas` : null,
    !src.opgg && !src.own ? "Fuentes: estimado (sin partidas del enfrentamiento)" : null,
    playStyle ? `Estilo de línea: ${playStyle}` : null,
  ]
    .filter(Boolean)
    .join("\n");
  mount(
    el,
    h(
      "div.lane-top",
      h(`span.lane-wr.${wrClass(lane.wr_adj)}`, { title: detail }, fmt.pct(lane.wr_adj)),
      verdictEl(lane.verdict),
      lane.counter === "counter" ? h("span.pill.pill-good", { title: "Ventaja propia del matchup por encima de lo esperado" }, "Counter") : lane.counter === "countered" ? h("span.pill.pill-bad", "Te counterea") : null,
      h("span.spacer"),
      h(`span.impact.${lane.impact_pp >= 0 ? "good" : "bad"}`, { title: "Cuánto mueve esta línea la probabilidad final" }, fmt.signed(lane.impact_pp, 1, " pp")),
    ),
    h(
      "div.lane-meta",
      opgg.lane_advantage ? h("span", "Línea: ", h(`b.${laneWinner}`, dd.name(opgg.lane_advantage))) : null,
      h("span", { title: detail, style: { cursor: "help" } }, "Detalle ⓘ"),
      h("a", { href: "javascript:void 0", onclick: () => openMatchup(role, lane.ally, lane.enemy) }, "Ver matchup →"),
    ),
  );
}

// ------------------------------------------------------------------ inferior
function renderBottom(r) {
  const synList = (terms, perspective) => {
    const items = terms.filter((t) => t.games > 0).map((t) => ({ ...t, own: (t.wr_adj ?? 0) - (t.expected ?? 0) })).sort((a, b) => Math.abs(b.own) - Math.abs(a.own));
    if (!items.length) return h("div.small.muted", "Sin datos de sinergia todavía.");
    const row = (t) =>
      h(
        "div.row",
        champImg(t.champs[0], "sm"),
        champImg(t.champs[1], "sm"),
        h("span.small", `${dd.name(t.champs[0])} + ${dd.name(t.champs[1])}`),
        h("span.spacer"),
        sourceBadges(t.sources),
        h(`b.small.${t.own >= 0 ? "good" : "bad"}`, { title: `${perspective}: WR de la pareja ${fmt.pct(t.wr_adj)} vs ${fmt.pct(t.expected)} esperado` }, fmt.signed(t.own, 1, " pp")),
      );
    const rest = items.slice(2, 5);
    return h(
      "div.col",
      { style: { gap: "6px" } },
      items.slice(0, 2).map(row),
      rest.length ? h("details.more", h("summary.btn.btn-ghost.btn-sm", `Ver ${rest.length} más`), h("div.col", { style: { gap: "6px", marginTop: "6px" } }, rest.map(row))) : null,
    );
  };
  const synCard = h(
    "section.card",
    h("h3.card-title", icon("link"), "Sinergias"),
    h("div.small.gold", { style: { marginBottom: "6px" } }, "Tu equipo"),
    synList(r.synergies.ally, "Tu equipo"),
    h("div.divider"),
    h("div.small.bad", { style: { marginBottom: "6px" } }, "Rival"),
    synList(r.synergies.enemy, "Rival"),
  );
  const curveCard = h(
    "section.card",
    h("h3.card-title", icon("activity"), "Según la duración"),
    r.duration_curve
      ? [curveChart(r.duration_curve), h("div.tiny.muted", "Probabilidad de victoria de tu equipo si la partida dura ese tiempo. Sube al final = tu composición escala mejor.")]
      : h("div.small.muted", "Completa al menos 4 picks para ver si tu composición es de early o de late."),
  );
  const ev = r.evidence || {};
  const modelCard = collapsible(
    "draft.modelo",
    [icon("info"), "Cómo se calcula"],
    h(
      "div",
      h(
        "dl.kv",
        h("dt", "Fuentes"),
        h("dd", ev.opgg ? `OP.GG (${ev.opgg.tier.replace("_plus", "+")}, ${ev.opgg.region}, parches ${ev.opgg.patches.join(" + ")})` : "Sin OP.GG", ev.own ? " + tu BD" : ""),
        h("dt", "Prior matchups"),
        h("dd", `${fmt.int(r.priors.matchup)} partidas virtuales`),
        h("dt", "Prior sinergias"),
        h("dd", `${fmt.int(r.priors.synergy)} partidas virtuales`),
        h("dt", "Calibración α"),
        h("dd", fmt.num(r.alpha, 3), r.alpha === 1 ? h("span.tiny.muted", " (sin calibrar: haz un backtest en Datos)") : null),
      ),
      h("p.tiny.muted", { style: { marginTop: "10px" } }, "Cada línea usa el WR del enfrentamiento encogido hacia lo esperado por la fuerza de ambos campeones (log5). Con muchas partidas mandan los datos; con pocas, la estimación por estadísticas de campeón."),
    ),
    { summary: "fuentes, priors y calibración" },
  );
  mount(bottomEl, synCard, curveCard, modelCard);
}

// ------------------------------------------------------------------ sugerencias
async function openSuggest(side, role) {
  const sideLabel = side === "ally" ? "tu equipo" : "el rival";
  const m = modal(h("div.row", roleIcon(role), h("h3", `Mejores picks de ${ROLE_LABEL[role]} para ${sideLabel}`)), loading("Calculando picks…"));
  try {
    const res = await api.post("/api/draft/suggest", { ally: draft.ally, enemy: draft.enemy }, filterParams({ role, side, limit: 15 }));
    const opp = res.opponent;
    const list = (items) =>
      h(
        "div.sugg-list",
        items.map((s) =>
          h(
            "div.sugg",
            {
              onclick: () => {
                if (taken().has(s.champ)) return;
                draft[side][role] = s.champ;
                pickers[side][role].setValue(s.champ);
                persist();
                m.close();
                analyze();
              },
            },
            champImg(s.champ),
            h(
              "div",
              h("div", { style: { fontWeight: 650 } }, dd.name(s.champ)),
              h(
                "div.tiny.muted",
                opp ? `vs ${dd.name(opp)}: ${fmt.pct(s.lane_wr)} en ${fmt.compact(s.lane_games)} partidas` : `WR general ${fmt.pct(s.base_wr)}`,
                s.synergy_pp ? ` · sinergias ${fmt.signed(s.synergy_pp, 1, " pp")}` : "",
              ),
            ),
            h("div.right", h(`b.${s.score_pp >= 0 ? "good" : "bad"}`, fmt.signed(s.score_pp, 1, " pp")), h("div.tiny.muted", "vs 50 %")),
          ),
        ),
      );
    mount(
      m.body,
      opp ? notice(h("span", "Ordenado por lo que aporta cada campeón contra ", h("b", dd.name(opp)), " y por sus sinergias con los aliados ya elegidos.")) : notice("Sin rival de línea elegido: se ordena por fuerza general en el rol y sinergias."),
      h("div.grid.grid-2", { style: { marginTop: "12px" } }, h("div", h("div.small.good", { style: { marginBottom: "6px" } }, "Recomendados"), res.best.length ? list(res.best) : h("div.muted", "Sin datos suficientes.")), res.worst.length ? h("div", h("div.small.bad", { style: { marginBottom: "6px" } }, "Evitar"), list(res.worst)) : null),
    );
  } catch (err) {
    mount(m.body, errorBox(err));
  }
}

