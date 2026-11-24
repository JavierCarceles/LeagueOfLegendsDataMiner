// Estadísticas por región: meta (OP.GG + tu BD) y datos de partida (tu BD).
import { api, filterParams } from "../api.js";
import { dd, platformLabel, ROLE_LABEL, ROLES, state, TIER_LABEL } from "../state.js";
import { collapsible, champImg, empty, errorBox, fmt, h, icon, kpi, loading, mount, notice, rankImg, roleIcon, table, tierBadge, wrClass } from "../ui.js";

let root = null;
let target = "all";

export function destroy() {
  root = null;
}

export async function render(container, args = []) {
  root = container;
  target = (args[0] || (state.filters.platform !== "all" ? state.filters.platform : "all")).toLowerCase();
  void load(); // trata sus errores (los pinta en la vista)
}

export function onFilters() {
  void load();
}

async function load() {
  if (!root) return;
  const body = h("div");
  const platforms = state.meta?.platforms || [];
  mount(
    root,
    h("div.section-head", h("div", h("h2", "Estadísticas por región"), h("p", "Meta de cada región (OP.GG filtrado por región + tu base de datos) y, con tus propias partidas, objetivos, duración y lado del mapa."))),
    h(
      "div.chips",
      { style: { marginBottom: "14px" } },
      h(`span.chip${target === "all" ? ".on" : ""}`, { onclick: () => go("all") }, "Todas"),
      platforms.map((p) => h(`span.chip${target === p.id ? ".on" : ""}`, { onclick: () => go(p.id), title: p.name }, p.label)),
    ),
    body,
  );
  mount(body, loading("Calculando estadísticas (la primera vez por región se descargan datos de OP.GG)…"));
  try {
    const [detail, overview] = await Promise.all([api.get(`/api/regions/${target}`, filterParams()), api.get("/api/regions", filterParams({ platform: "all" }))]);
    if (!root) return;
    mount(body, view(detail, overview));
  } catch (err) {
    mount(body, errorBox(err));
  }
}

function go(id) {
  target = id;
  history.replaceState(null, "", `#/regiones/${id}`);
  void load();
}

/** Partidas, duración, lado azul y rendiciones de tu base de datos (o el aviso de que aún no hay). */
function ownKpis(d) {
  if (!(d.matches > 0)) {
    return notice("Aún no hay partidas propias de esta región. La meta de abajo sale de OP.GG; para objetivos, duración y lado del mapa inicia el recolector en «Datos y API».", "info");
  }
  const blueCi = d.blue_ci ? `IC ${fmt.pct(d.blue_ci[0])}–${fmt.pct(d.blue_ci[1])}` : null;
  return h(
    "div.kpis",
    kpi("Partidas (tu BD)", fmt.int(d.matches), `${fmt.int(d.remakes)} remakes descartadas`),
    kpi("Duración media", `${fmt.num(d.avg_minutes, 1)} min`),
    kpi("WR lado azul", h(`span.${wrClass(d.blue_wr, 0.7)}`, fmt.pct(d.blue_wr)), blueCi),
    kpi("Rendiciones", fmt.pct(d.surrender)),
  );
}

function view(d, overview) {
  const hasOwn = d.matches > 0;
  const regionName = state.meta?.platforms?.find((p) => p.id === target)?.name || "";
  const title = target === "all" ? "Todas las regiones" : `${platformLabel(target)} · ${regionName}`;
  const parts = [
    h("section.card", h("h3.card-title", icon("globe"), title), ownKpis(d)),
    // Meta por rol
    h("h3.card-title", { style: { marginTop: "6px" } }, icon("crown"), "Meta por rol", h("span.spacer"), h("span.tiny.muted", "WR ajustado · OP.GG + tu BD")),
    h(
      "div.grid",
      { class: "roles-5" },
      ROLES.map((role) =>
        h(
          "section.card",
          h("h3.card-title", roleIcon(role), ROLE_LABEL[role]),
          (d.meta[role]?.best || []).length
            ? h(
                "div.col",
                { style: { gap: "6px" } },
                metaBest(d.meta[role].best, d.meta[role].popular || [], role),
              )
            : h("div.small.muted", "Sin datos."),
        ),
      ),
    ),
  ];

  if (hasOwn) {
    parts.push(
      collapsible(
        "regiones.detalle",
        [icon("chart"), "Objetivos, duración y elo (tu BD)"],
        () => ownDetail(d),
        { summary: `objetivos por partida, duración, elo${d.bans.length ? " y baneos" : ""}` },
      ),
    );
  }

  if (overview.platforms.length) {
    parts.push(
      collapsible(
        "regiones.comparativa",
        [icon("database"), "Comparativa de regiones (tu BD)"],
        h(
          "div",
        table(
          [
            { key: "label", label: "Región", render: (p) => h("span", h("b", p.label), h("span.tiny.muted", ` ${p.name}`)) },
            { key: "matches", label: "Partidas", render: (p) => fmt.int(p.matches) },
            { key: "avg_minutes", label: "Duración", render: (p) => `${fmt.num(p.avg_minutes, 1)} min` },
            { key: "blue_wr", label: "WR azul", render: (p) => h(`span.${wrClass(p.blue_wr, 0.7)}`, fmt.pct(p.blue_wr)) },
            { key: "surrender", label: "Rendición", render: (p) => fmt.pct(p.surrender) },
            { key: "main_tier", label: "Elo principal", render: (p) => TIER_LABEL[p.main_tier] || "—" },
            { key: "latest", label: "Última partida", render: (p) => fmt.ago(p.latest) },
          ],
          overview.platforms,
          { sortKey: "matches", onRow: (p) => go(p.platform) },
        ),
        overview.legacy_matches ? h("p.tiny.muted", { style: { marginTop: "8px" } }, `Además tienes ~${fmt.int(overview.legacy_matches)} partidas del histórico antiguo sin desglose por región.`) : null,
        ),
        { summary: `${overview.platforms.length} regiones con partidas` },
      ),
    );
  } else if (!hasOwn) {
    parts.push(h("section.card", empty("Sin partidas propias todavía", "Inicia el recolector para comparar regiones con tus datos.", "database")));
  }
  return h("div.stack", parts);
}

function metaBest(best, popular, role) {
  const row = (c) =>
    h(
      "div.row",
      { style: { cursor: "pointer" }, onclick: () => (location.hash = `#/campeon/${c.champ}/${role}`) },
      champImg(c.champ, "sm"),
      h("span.small", { style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, dd.name(c.champ)),
      h("span.spacer"),
      tierBadge(c.tier),
      h(`b.small.${wrClass(c.wr_adj)}`, fmt.pct(c.wr_adj)),
    );
  const rest = best.slice(4);
  // Lo visible: los 4 mejores. El resto de la lista y los más jugados, tras «Ver más».
  if (!rest.length && !popular.length) return best.map(row);
  const andPopular = popular.length ? " y los más jugados" : "";
  const label = rest.length ? `Ver ${rest.length} más${andPopular}` : "Ver los más jugados";
  return [
    best.slice(0, 4).map(row),
    h(
      "details.more",
      h("summary.btn.btn-ghost.btn-sm", label),
      h(
        "div.col",
        { style: { gap: "6px", marginTop: "6px" } },
        rest.map(row),
        popular.length
          ? [
              rest.length ? h("div.divider") : null,
              h("div.tiny.muted", "Más jugados"),
              h("div.row", { style: { gap: "4px", flexWrap: "wrap" } }, popular.map((c) => champImg(c.champ, "xs"))),
            ]
          : null,
      ),
    ),
  ];
}

function ownDetail(d) {
  return h(
    "div.stack",
    h(
      "div.kpis",
      kpi("Kills/partida", fmt.num(d.per_match.kills, 1)),
      kpi("Dragones", fmt.num(d.per_match.dragons, 2), "por partida"),
      kpi("Barones", fmt.num(d.per_match.barons, 2), "por partida"),
      kpi("Torres", fmt.num(d.per_match.towers, 1), "por partida"),
    ),
    h(
      "div.grid.grid-3",
      h(
        "section.card",
        h("h3.card-title", icon("chart"), "Valor de los objetivos"),
        h("div.card-sub", "WR del equipo que consigue primero cada objetivo."),
        d.objectives.map((o) => h("div.hbar", h("span.small", o.label), h("div.track", h("span", { style: { width: `${Math.max(0, Math.min(100, (o.win_rate - 40) * 2.5))}%` } })), h("b.right", fmt.pct(o.win_rate)))),
      ),
      h(
        "section.card",
        h("h3.card-title", icon("activity"), "Duración de las partidas"),
        d.duration.map((b) => h("div.hbar", h("span.small", b.label), h("div.track", h("span", { style: { width: `${Math.min(100, (b.share || 0) * 3)}%` } })), h("span.right.small", fmt.pct(b.share, 0), h("span.tiny.muted", ` · azul ${fmt.pct(b.blue_wr, 0)}`)))),
      ),
      h(
        "section.card",
        h("h3.card-title", icon("crown"), "Elo de las partidas"),
        d.tiers.map((t) => h("div.hbar", h("span.row.small", rankImg(t.tier, "item-img"), TIER_LABEL[t.tier] || t.tier), h("div.track", h("span", { style: { width: `${t.share || 0}%` } })), h("span.right.small", fmt.pct(t.share, 0)))),
      ),
    ),
    d.bans.length
      ? h(
          "section.card",
          h("h3.card-title", icon("close"), "Más baneados (tu BD)"),
          h("div.row-wrap", { style: { gap: "14px" } }, d.bans.map((b) => h("div.col", { style: { alignItems: "center", gap: "4px" } }, champImg(b.champ), h("span.tiny", fmt.pct(b.rate, 1))))),
        )
      : null,
  );
}
