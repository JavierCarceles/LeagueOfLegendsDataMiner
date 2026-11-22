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
  load();
}

export function onFilters() {
  load();
}

async function load() {
  if (!root) return;
  if (mode === "detail" && champId) return loadDetail();
  const fresh = latest("champions");
  const body = h("div");
  mount(
    root,
    h("div.section-head", h("div", h("h2", "Tier list"), h("p", { title: "Fuerza de cada campeón por rol combinando OP.GG, Lolalytics y tu base de datos. El tier (S+…D) sale del WR ajustado por tamaño de muestra; también se muestra el tier oficial de OP.GG." }, "Fuerza de cada campeón por rol (OP.GG + Lolalytics + tu BD). ⓘ"))),
    h("div.row", { style: { marginBottom: "14px" } }, roleTabs(role, (r) => { role = r; history.replaceState(null, "", `#/campeones/${r}`); load(); })),
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
      { key: "champ", label: "Campeón", sort: (c) => dd.name(c.champ), render: (c) => champ(c.champ, { sub: c.source === "own" ? "solo tu BD" : c.opgg?.role_rate ? `${fmt.num(c.opgg.role_rate, 0)} % de sus partidas en ${ROLE_LABEL[role]}` : null }) },
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
        { style: { marginTop: "10px", cursor: "help" }, title: `OP.GG: ${data.evidence?.opgg ? `${data.evidence.opgg.tier}, ${data.evidence.opgg.region}, parche de referencia ${data.evidence.ref_patches?.[role] || "—"}` : "no disponible"}. Prior de campeón: ${fmt.int(data.priors.champ)} partidas virtuales.` },
        "Fuentes y parámetros ⓘ · pasa el ratón por el WR para ver el desglose",
      ),
    );
  } catch (err) {
    mount(body, errorBox(err));
  }
}

// Texto del tooltip del WR ajustado: el detalle por fuente que antes ocupaba columnas propias.
function wrDetail(c) {
  const parts = [];
  if (c.opgg?.wr != null) parts.push(`OP.GG ${fmt.pct(c.opgg.wr, 2)}`);
  if (c.opgg?.lola_wr != null) parts.push(`Lolalytics ${fmt.pct(c.opgg.lola_wr, 2)}${c.opgg.lola_games ? ` (${fmt.compact(c.opgg.lola_games)} part.)` : ""}`);
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
        b.opgg && b.opgg.wr !== null ? kpi("OP.GG", fmt.pct(b.opgg.wr), b.opgg.tier ? `Tier ${b.opgg.tier} · #${b.opgg.rank}` : "") : null,
        b.opgg?.lola_wr != null ? kpi("Lolalytics", fmt.pct(b.opgg.lola_wr), b.opgg.lola_games ? `${fmt.compact(b.opgg.lola_games)} partidas` : "") : null,
        b.opgg ? kpi("Pick / Ban", fmt.pct(b.opgg.pick), `ban ${fmt.pct(b.opgg.ban)}`) : null,
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

  const muList = (title, items, cls) =>
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
      s.best.length
        ? h(
            "div.col",
            { style: { gap: "6px" } },
            s.best.map((x) => h("div.row", champ(x.champ, { size: "sm" }), h("span.spacer"), sourceBadges(x.sources), h(`b.small.${x.delta >= 0 ? "good" : "bad"}`, fmt.signed(x.delta, 1, " pp")))),
            s.worst.length ? h("div.divider") : null,
            s.worst.map((x) => h("div.row", champ(x.champ, { size: "sm" }), h("span.spacer"), sourceBadges(x.sources), h(`b.small.${x.delta >= 0 ? "good" : "bad"}`, fmt.signed(x.delta, 1, " pp")))),
          )
        : h("div.muted.small", "Sin datos de sinergia."),
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
                    { key: "key", label: { tier: "Elo", patch: "Parche", platform: "Región" }[k], render: (r) => (r.key === "legacy" ? "Histórico" : k === "platform" ? r.key.toUpperCase() : r.key) },
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

