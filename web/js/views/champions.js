// Tier list por rol y ficha completa de campeón.
import { api, filterParams } from "../api.js";
import { dd, ROLE_LABEL, ROLES, state } from "../state.js";
import { champ, champImg, collapsible, empty, errorBox, fmt, h, icon, kpi, latest, loading, mount, notice, patchChanges, roleIcon, roleTabs, sourceBadges, table, tierBadge, wrCell, wrClass } from "../ui.js";
import { openMatchup } from "./matchups.js";

let root = null;
let role = "TOP";
let mode = "list";
let champId = null;

export function destroy() {
  root = null;
}

export async function render(container, args = []) {
  root = container;
  if (args[0] === "campeon") {
    mode = "detail";
    champId = Number(args[1]) || state.champList.find((c) => c.key.toLowerCase() === String(args[1] || "").toLowerCase())?.id || null;
    role = ROLES.includes((args[2] || "").toUpperCase()) ? args[2].toUpperCase() : null;
  } else {
    mode = "list";
    role = ROLES.includes((args[0] || "").toUpperCase()) ? args[0].toUpperCase() : role || "TOP";
  }
  void load(); // trata sus errores (los pinta en la vista)
}

export function onFilters() {
  void load();
}

async function load() {
  if (!root) return;
  if (mode === "detail" && champId) return loadDetail();
  const fresh = latest("champions");
  const body = h("div");
  mount(
    root,
    h("div.section-head", h("div", h("h2", "Tier list"), h("p", { title: "Fuerza de cada campeón por rol combinando OP.GG, Lolalytics y tu base de datos. El tier (S+…D) sale del WR ajustado por tamaño de muestra; también se muestra el tier oficial de OP.GG." }, "Fuerza de cada campeón por rol (OP.GG + Lolalytics + tu BD). ⓘ"))),
    h("div.row", { style: { marginBottom: "14px" } }, roleTabs(role, (r) => { role = r; history.replaceState(null, "", `#/campeones/${r}`); void load(); })),
    body,
  );
  mount(body, loading("Cargando campeones…"));
  try {
    const data = await api.get("/api/champions", filterParams({ role }));
    if (!root || !fresh()) return;
    const rows = data.champions;
    if (!rows.length) return mount(body, h("section.card", empty("Sin datos para estos filtros", "Prueba otro elo, región o parche.")));
    const cols = [
      { key: "rank", label: "#", sort: false, class: "rank", render: (_, i) => i + 1 },
      { key: "champ", label: "Campeón", sort: (c) => dd.name(c.champ), render: (c) => champ(c.champ, { sub: champSub(c) }) },
      { key: "tier", label: "Tier", sort: (c) => c.wr_adj, render: (c) => h("div.row", tierBadge(c.tier), c.opgg?.tier ? h("span.opgg-tier", { title: "Tier de OP.GG (1 = mejor)" }, `T${c.opgg.tier}`) : null) },
      { key: "wr_adj", label: "WR ajustado", render: (c) => h("div", { title: wrDetail(c) }, wrCell(c.wr_adj)) },
      { key: "pick", label: "Pick / Ban", sort: (c) => c.opgg?.pick, render: (c) => h("div.col", { style: { gap: "1px" } }, h("span.num", fmt.pct(c.opgg?.pick)), c.opgg?.ban != null ? h("span.tiny.muted", `ban ${fmt.pct(c.opgg.ban)}`) : null) },
      { key: "counters", label: "Le cuesta contra", sort: false, render: (c) => h("div.row", { style: { gap: "4px" } }, (c.opgg?.counters || []).slice(0, 3).map((x) => champImg(x.champ, "xs"))) },
    ];
    mount(
      body,
      table(cols, rows, { sortKey: "wr_adj", onRow: (c) => (location.hash = `#/campeon/${c.champ}/${role}`) }),
      h(
        "p.tiny.muted",
        { style: { marginTop: "10px", cursor: "help" }, title: sourcesTitle(data) },
        "Fuentes y parámetros ⓘ · pasa el ratón por el WR para ver el desglose",
      ),
    );
  } catch (err) {
    mount(body, errorBox(err));
  }
}

/** Subtítulo de un campeón en la tier list: si solo sale de tu BD o qué parte de sus partidas juega en el rol. */
function champSub(c) {
  if (c.source === "own") return "solo tu BD";
  return c.opgg?.role_rate ? `${fmt.num(c.opgg.role_rate, 0)} % de sus partidas en ${ROLE_LABEL[role]}` : null;
}

/** Tooltip de «Fuentes y parámetros»: de dónde salen los datos de OP.GG y el prior de campeón. */
function sourcesTitle(data) {
  const ev = data.evidence;
  const opgg = ev?.opgg ? `${ev.opgg.tier}, ${ev.opgg.region}, parche de referencia ${ev.ref_patches?.[role] || "—"}` : "no disponible";
  return `OP.GG: ${opgg}. Prior de campeón: ${fmt.int(data.priors.champ)} partidas virtuales.`;
}

// Texto del tooltip del WR ajustado: el detalle por fuente que antes ocupaba columnas propias.
function wrDetail(c) {
  const parts = [];
  if (c.opgg?.wr != null) parts.push(`OP.GG ${fmt.pct(c.opgg.wr, 2)}`);
  if (c.opgg?.lola_wr != null) {
    const games = c.opgg.lola_games ? ` (${fmt.compact(c.opgg.lola_games)} part.)` : "";
    parts.push(`Lolalytics ${fmt.pct(c.opgg.lola_wr, 2)}${games}`);
  }
  if (c.own) parts.push(`tu BD ${fmt.pct(c.own.wr)} (${fmt.compact(c.own.games)} part.)`);
  if (c.own?.kda != null) parts.push(`KDA ${fmt.num(c.own.kda, 2)}`);
  return parts.length ? parts.join(" · ") : "Sin desglose por fuente";
}

async function loadDetail() {
  const fresh = latest("champions");
  mount(root, loading("Cargando campeón…"));
  try {
    const d = await api.get(`/api/champions/${champId}`, filterParams(role ? { role } : {}));
    if (!root || !fresh()) return;
    role = d.role;
    mount(root, detailView(d));
  } catch (err) {
    mount(root, errorBox(err));
  }
}

function detailView(d) {
  const b = d.base;
  const head = h(
    "section.card",
    h(
      "div.row-wrap",
      { style: { gap: "18px" } },
      champImg(d.champ, "xl"),
      h(
        "div",
        h("div", { style: { fontSize: "26px", fontWeight: 800 } }, dd.name(d.champ)),
        h("div.muted", dd.champ(d.champ)?.title || ""),
        h("div.row-wrap", { style: { marginTop: "8px" } }, (dd.champ(d.champ)?.tags || []).map((t) => h("span.pill", t)), d.damage_type ? h("span.pill.pill-gold", `Daño ${d.damage_type}`) : null),
      ),
      h("div.spacer"),
      h(
        "div.kpis",
        { style: { flex: "1 1 420px" } },
        kpi("WR ajustado", h(`span.${wrClass(b.wr_adj)}`, fmt.pct(b.wr_adj)), `${fmt.compact(b.games)} partidas`),
        sourceKpis(b.opgg),
      ),
    ),
    h(
      "div.row-wrap",
      { style: { marginTop: "14px" } },
      d.roles.map((r) =>
        h(
          `button.tab${r.role === d.role ? ".active" : ""}`,
          { onclick: () => (location.hash = `#/campeon/${d.champ}/${r.role}`) },
          roleIcon(r.role),
          ROLE_LABEL[r.role],
          h("span.tiny.muted", r.opgg_role_rate !== null ? `${fmt.num(r.opgg_role_rate, 0)} %` : `${fmt.compact(r.own_games)} part.`),
        ),
      ),
      h("span.spacer"),
      h("a.btn.btn-sm", { href: `#/matchups/${d.role}/${d.champ}` }, icon("swords"), "Todos los matchups"),
    ),
  );

  const muList = (title, items) =>
    h(
      "section.card",
      h("h3.card-title", title),
      items.length
        ? h(
            "div.col",
            { style: { gap: "6px" } },
            items.map((m) =>
              h(
                "div.row",
                { style: { cursor: "pointer" }, onclick: () => openMatchup(d.role, d.champ, m.opp) },
                champ(m.opp, { size: "sm" }),
                h("span.spacer"),
                sourceBadges(m.sources),
                h(`b.num.${wrClass(m.wr_adj)}`, { style: { width: "58px", textAlign: "right" } }, fmt.pct(m.wr_adj)),
              ),
            ),
          )
        : h("div.muted.small", "Sin datos."),
    );

  const synergyCards = Object.entries(d.synergies).map(([partnerRole, s]) =>
    h(
      "section.card",
      h("h3.card-title", roleIcon(partnerRole), `Con ${ROLE_LABEL[partnerRole]}`),
      s.best.length ? synergyList(s) : h("div.muted.small", "Sin datos de sinergia."),
    ),
  );

  // Resumen de una línea para la sección de sinergias: la mejor pareja entre todos los roles.
  const bestSyn = Object.values(d.synergies)
    .flatMap((s) => s.best || [])
    .reduce((acc, x) => (x.delta != null && (!acc || x.delta > acc.delta) ? x : acc), null);
  const synergySummary = bestSyn ? `mejor: ${dd.name(bestSyn.champ)} (${fmt.signed(bestSyn.delta, 1, " pp")})` : "sin datos de sinergia";

  const own = d.own?.dgames ? d.own : null;
  const curve = own?.duration_curve;
  const breakdown = ["tier", "patch", "platform"].filter((k) => d.breakdown[k]?.length);

  // Cambios recientes del campeón (notas de parche oficiales): se cargan aparte, sin retrasar la ficha.
  const patchEl = h("div");
  api
    .get(`/api/patches/champion/${d.champ}`)
    .then((res) => {
      if (res.changes?.length && patchEl.isConnected) mount(patchEl, h("section.card", h("h3.card-title", icon("spark"), "Cambios recientes (notas de parche de Riot)"), patchChanges(res.changes, { max: 3 })));
    })
    .catch(() => {});
  return h(
    "div.stack",
    head,
    patchEl,
    h("div.grid.grid-3", muList(h("span.good", "Mejores enfrentamientos"), d.matchups.best), muList(h("span.bad", "Peores enfrentamientos"), d.matchups.worst), muList("Más jugados", d.matchups.common)),
    synergyCards.length ? collapsible("campeon.sinergias", [icon("link"), "Sinergias (Δ sobre lo esperado)"], () => h("div.grid.grid-4", synergyCards), { summary: synergySummary }) : null,
    own
      ? collapsible(
          "campeon.tubd",
          [icon("chart"), "Tus estadísticas (tu BD)"],
          () =>
            h(
              "div.stack",
              h(
                "div.kpis",
                kpi("KDA", fmt.num(own.kda, 2), `${fmt.num(own.kills)} / ${fmt.num(own.deaths)} / ${fmt.num(own.assists)}`),
                kpi("CS/min", fmt.num(own.cs_min, 1), `CS@10 ${fmt.num(own.cs10)}`),
                kpi("Daño/min", fmt.int(own.dmg_min), `${fmt.pct(own.dmg_share)} del equipo`),
              ),
              h(
                "div.grid.grid-2",
                h(
                  "section.card",
                  h("h3.card-title", icon("activity"), "WR según duración (tu BD)"),
                  curve ? h("div.col", curve.map((c) => h("div.hbar", h("span", c.label), h("div.track", h("span", { style: { width: `${Math.max(0, Math.min(100, ((c.wr_adj ?? 50) - 40) * 5))}%` } })), h(`b.right.${wrClass(c.wr_adj)}`, fmt.pct(c.wr_adj))))) : h("div.muted.small", "Sin datos."),
                ),
                h(
                  "section.card",
                  h("h3.card-title", icon("chart"), "Perfil (tu BD)"),
                  h(
                    "div.kpis",
                    kpi("Oro/min", fmt.int(own.gold_min)),
                    kpi("Visión/min", fmt.num(own.vision_min, 2)),
                    kpi("Part. kills", fmt.pct(own.kp)),
                    kpi("Gana línea", fmt.pct(own.lane_win), `pierde ${fmt.pct(own.lane_loss)}`),
                    kpi("Solo kills", fmt.num(own.solo_kills, 2), "por partida"),
                    kpi("Duración", own.avg_minutes ? `${fmt.num(own.avg_minutes, 1)} min` : "—"),
                  ),
                ),
              ),
            ),
          { summary: `${fmt.int(own.dgames)} partidas · KDA ${fmt.num(own.kda, 2)}` },
        )
      : notice("Las métricas detalladas (KDA, CS, daño, fase de líneas) aparecen cuando el recolector descarga partidas con tu API key."),
    breakdown.length
      ? collapsible(
          "campeon.desglose",
          [icon("database"), "Desglose de tu base de datos"],
          () =>
            h(
              "div.grid.grid-3",
              breakdown.map((k) =>
                table(
                  [
                    { key: "key", label: { tier: "Elo", patch: "Parche", platform: "Región" }[k], render: (r) => breakdownKey(r, k) },
                    { key: "games", label: "Partidas", render: (r) => fmt.int(r.games) },
                    { key: "wr", label: "WR", render: (r) => h(`b.${wrClass(r.wr)}`, fmt.pct(r.wr)) },
                  ],
                  d.breakdown[k].slice(0, 10),
                  { sortKey: "games" },
                ),
              ),
            ),
          { summary: `por ${breakdown.map((k) => ({ tier: "elo", patch: "parche", platform: "región" })[k]).join(", ")}` },
        )
      : null,
  );
}

/** KPIs de las webs en la cabecera del campeón: OP.GG, Lolalytics y pick/ban (solo los que tienen dato). */
function sourceKpis(opgg) {
  if (!opgg) return [];
  const opggSub = opgg.tier ? `Tier ${opgg.tier} · #${opgg.rank}` : "";
  const lolaSub = opgg.lola_games ? `${fmt.compact(opgg.lola_games)} partidas` : "";
  return [
    opgg.wr !== null ? kpi("OP.GG", fmt.pct(opgg.wr), opggSub) : null,
    opgg.lola_wr != null ? kpi("Lolalytics", fmt.pct(opgg.lola_wr), lolaSub) : null,
    kpi("Pick / Ban", fmt.pct(opgg.pick), `ban ${fmt.pct(opgg.ban)}`),
  ];
}

/** Fila de una sinergia: el campeón, sus fuentes y cuánto suma o resta sobre lo esperado. */
function synergyRow(x) {
  return h("div.row", champ(x.champ, { size: "sm" }), h("span.spacer"), sourceBadges(x.sources), h(`b.small.${x.delta >= 0 ? "good" : "bad"}`, fmt.signed(x.delta, 1, " pp")));
}

/** Sinergias con un rol: las mejores, un separador y las peores. */
function synergyList(s) {
  return h("div.col", { style: { gap: "6px" } }, s.best.map(synergyRow), s.worst.length ? h("div.divider") : null, s.worst.map(synergyRow));
}

/** Clave de una fila del desglose de tu BD: «Histórico», la región en mayúsculas o el valor tal cual. */
function breakdownKey(r, k) {
  if (r.key === "legacy") return "Histórico";
  return k === "platform" ? r.key.toUpperCase() : r.key;
}

