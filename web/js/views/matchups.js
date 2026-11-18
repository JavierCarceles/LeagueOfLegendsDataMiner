// Explorador de enfrentamientos y ficha detallada de cada matchup.
import { api, filterParams } from "../api.js";
import { champPicker } from "../components/picker.js";
import { dd, ROLE_LABEL, state } from "../state.js";
import {
  champ, champImg, confidenceEl, empty, errorBox, fmt, h, icon, itemImg, kpi, loading, modal, mount, notice,
  roleIcon, roleTabs, runeImg, select, sourceBadges, spellImg, table, verdictEl, wrCell, wrClass,
} from "../ui.js";

let root = null;
let params = { role: "TOP", champ: null };
let listEl = null;
let opts = { minGames: 100, sort: "wr_adj" };

export function destroy() {
  root = null;
}

export async function render(container, args = []) {
  root = container;
  params = { role: (args[0] || params.role || "TOP").toUpperCase(), champ: args[1] ? Number(args[1]) || state.champList.find((c) => c.key.toLowerCase() === args[1].toLowerCase())?.id : params.champ };
  listEl = h("div");
  const picker = champPicker({
    value: params.champ,
    placeholder: "Elige un campeón",
    compact: true,
    onChange: (id) => {
      params.champ = id;
      updateHash();
      load();
    },
  });
  mount(
    container,
    h(
      "div.section-head",
      h("div", h("h2", "Matchups"), h("p", "Cada enfrentamiento combina OP.GG, tu base de datos y la fuerza general de ambos campeones. El WR ajustado corrige las muestras pequeñas; Δ es la ventaja propia del matchup.")),
    ),
    h(
      "div.row-wrap",
      { style: { marginBottom: "14px", gap: "12px" } },
      roleTabs(params.role, (r) => {
        params.role = r;
        updateHash();
        render(container, [r, params.champ || ""]);
      }),
      h("div", { style: { width: "260px" } }, picker),
      h("div.filter", h("label", "Mínimo de partidas"), select([0, 50, 100, 500, 1000, 3000].map((v) => ({ value: v, label: v ? `≥ ${fmt.int(v)}` : "Todas" })), opts.minGames, (v) => { opts.minGames = Number(v); load(); }, "select select-sm")),
      h("div.filter", h("label", "Ordenar por"), select([
        { value: "wr_adj", label: "WR ajustado" },
        { value: "delta", label: "Ventaja propia (Δ)" },
        { value: "games", label: "Partidas" },
        { value: "cs10", label: "CS a los 10" },
      ], opts.sort, (v) => { opts.sort = v; load(); }, "select select-sm")),
    ),
    listEl,
  );
  load();
}

export function onFilters() {
  load();
}

function updateHash() {
  const hash = `#/matchups/${params.role}${params.champ ? `/${params.champ}` : ""}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

async function load() {
  if (!root) return;
  if (!params.champ) {
    mount(listEl, loading());
    try {
      const tl = await api.get("/api/champions", filterParams({ role: params.role }));
      const top = tl.champions.filter((c) => c.opgg || c.games).slice(0, 24);
      mount(
        listEl,
        h(
          "section.card",
          h("h3.card-title", roleIcon(params.role), `Campeones más fuertes en ${ROLE_LABEL[params.role]}`),
          h("div.card-sub", "Elige uno para ver todos sus enfrentamientos."),
          h("div.chips", top.map((c) => h("span.chip", { onclick: () => { params.champ = c.champ; updateHash(); render(root, [params.role, c.champ]); } }, champImg(c.champ, "xs"), dd.name(c.champ), h("span.tiny.muted", fmt.pct(c.wr_adj))))),
        ),
      );
    } catch (err) {
      mount(listEl, errorBox(err));
    }
    return;
  }
  mount(listEl, loading("Cargando enfrentamientos…"));
  try {
    const data = await api.get(`/api/matchups/${params.role}/${params.champ}`, filterParams({ min_games: opts.minGames, sort: opts.sort }));
    if (!root) return;
    renderList(data);
  } catch (err) {
    mount(listEl, errorBox(err));
  }
}

function baseCard(data) {
  const b = data.base;
  const s = data.summary || {};
  const good = (s.muy_favorable || 0) + (s.favorable || 0);
  const bad = (s.muy_desfavorable || 0) + (s.desfavorable || 0);
  return h(
    "section.card",
    h(
      "div.row-wrap",
      { style: { gap: "16px" } },
      champImg(data.champ, "xl"),
      h("div", h("div", { style: { fontSize: "22px", fontWeight: 800 } }, dd.name(data.champ)), h("div.row.muted", roleIcon(data.role), ROLE_LABEL[data.role]), h("div.row", { style: { marginTop: "6px" } }, sourceBadges({ opgg: b.opgg?.games ? { games: b.opgg.games, wr: b.opgg.wr } : null, own: b.own }))),
      h("div.spacer"),
      h(
        "div.kpis",
        { style: { flex: "1 1 560px" } },
        kpi("WR ajustado", h(`span.${wrClass(b.wr_adj)}`, fmt.pct(b.wr_adj)), `${fmt.compact(b.games)} partidas`),
        b.opgg ? kpi("OP.GG", fmt.pct(b.opgg.wr), b.opgg.tier ? `Tier ${b.opgg.tier} · #${b.opgg.rank}` : "") : null,
        b.opgg ? kpi("Pick / Ban", `${fmt.pct(b.opgg.pick)}`, `ban ${fmt.pct(b.opgg.ban)}`) : null,
        b.own ? kpi("Tu BD", fmt.pct(b.own.wr), `${fmt.int(b.own.games)} partidas`) : null,
        kpi("Favorables", h("span.good", good), `de ${data.matchups.length}`),
        kpi("Desfavorables", h("span.bad", bad), `de ${data.matchups.length}`),
      ),
    ),
  );
}

function renderList(data) {
  if (!data.matchups.length) {
    mount(listEl, baseCard(data), h("section.card", { style: { marginTop: "16px" } }, empty("Sin enfrentamientos con esos filtros", "Prueba con menos partidas mínimas, otro elo u otro parche.")));
    return;
  }
  const cols = [
    { key: "opp", label: "Rival", sort: (m) => dd.name(m.opp), render: (m) => champ(m.opp, { sub: `WR general ${fmt.pct(m.opp_wr)}` }) },
    { key: "wr_adj", label: "WR ajustado", render: (m) => wrCell(m.wr_adj, null, m.ci) },
    { key: "wr", label: "WR bruto", render: (m) => h(`span.num.${wrClass(m.wr)}`, fmt.pct(m.wr)) },
    { key: "expected", label: "Esperado", render: (m) => h("span.num.muted", { title: "Solo por la fuerza de cada campeón (log5)" }, fmt.pct(m.expected)) },
    { key: "delta", label: "Δ propia", render: (m) => h(`b.num.${m.delta > 0.5 ? "good" : m.delta < -0.5 ? "bad" : "muted"}`, fmt.signed(m.delta, 1)) },
    { key: "verdict", label: "Veredicto", sort: (m) => m.wr_adj, render: (m) => h("div.col", { style: { gap: "2px" } }, verdictEl(m.verdict), m.counter === "counter" ? h("span.tiny.good", "Counter") : m.counter === "countered" ? h("span.tiny.bad", "Te counterea") : null) },
    { key: "games", label: "Partidas", render: (m) => h("div.col", { style: { gap: "3px" } }, h("b.num", fmt.int(m.games)), sourceBadges(m.sources)) },
    { key: "lane", label: "Línea (tu BD)", sort: (m) => m.lane?.score, render: (m) => laneCell(m.lane) },
    { key: "confidence", label: "Fiabilidad", sort: (m) => m.confidence?.level, render: (m) => confidenceEl(m.confidence) },
  ];
  mount(
    listEl,
    baseCard(data),
    h("div", { style: { height: "16px" } }),
    table(cols, data.matchups, { sortKey: opts.sort === "cs10" ? "lane" : opts.sort, onRow: (m) => openMatchup(data.role, data.champ, m.opp) }),
    h("p.tiny.muted", { style: { marginTop: "10px" } }, `Prior: ${fmt.int(data.priors.matchup)} partidas virtuales hacia lo esperado. Pulsa una fila para ver el detalle, la build contra ese rival y la guía.`),
  );
}

function laneCell(lane) {
  if (!lane) return h("span.tiny.faint", "—");
  return h(
    "div.col",
    { style: { gap: "2px" } },
    h(`span.small.${lane.verdict.code === "gana" ? "good" : lane.verdict.code === "pierde" ? "bad" : "muted"}`, lane.verdict.label),
    h("span.tiny.muted", `CS@10 ${fmt.signed(lane.cs10_diff, 1)} · ${fmt.int(lane.dgames)} part.`),
  );
}

// ------------------------------------------------------------------ detalle (modal)
export async function openMatchup(role, a, b) {
  const title = h("div.row", champImg(a, "sm"), h("h3", `${dd.name(a)} vs ${dd.name(b)}`), champImg(b, "sm"), h("span.pill", roleIcon(role), ROLE_LABEL[role]));
  const m = modal(title, loading("Analizando el enfrentamiento…"), { wide: true });
  try {
    const data = await api.get(`/api/matchups/${role}/${a}/${b}`, filterParams());
    mount(m.body, detailView(data, m));
  } catch (err) {
    mount(m.body, errorBox(err));
  }
}

function detailView(d, m) {
  const p = d.pair;
  const opgg = d.opgg_guide;
  const sourceRow = (label, src) =>
    src ? h("tr", h("td", label), h("td.num", fmt.int(src.games)), h("td.num", h(`b.${wrClass(src.wr)}`, fmt.pct(src.wr))), h("td.num.muted", src.ci ? `${fmt.pct(src.ci[0])} – ${fmt.pct(src.ci[1])}` : "—")) : null;

  const summary = h(
    "div.grid.grid-2",
    h(
      "section.card",
      h("h3.card-title", icon("chart"), "Resultado de partida"),
      h(
        "div.row-wrap",
        { style: { gap: "18px" } },
        h("div", h("div.tiny.muted", `WR de ${dd.name(d.champ)}`), h(`div.${wrClass(p.wr_adj)}`, { style: { fontSize: "40px", fontWeight: 900, lineHeight: 1.1 } }, fmt.pct(p.wr_adj)), verdictEl(p.verdict)),
        h(
          "dl.kv",
          h("dt", "WR bruto"), h("dd", fmt.pct(p.wr), p.ci ? h("span.tiny.muted", ` (IC ${fmt.pct(p.ci[0])}–${fmt.pct(p.ci[1])})`) : null),
          h("dt", "Esperado"), h("dd", fmt.pct(p.expected), h("span.tiny.muted", " por fuerza de campeones")),
          h("dt", "Ventaja propia"), h(`dd.${p.delta > 0.5 ? "good" : p.delta < -0.5 ? "bad" : ""}`, fmt.signed(p.delta, 2, " pp")),
          h("dt", "Muestra"), h("dd", fmt.int(p.games), " partidas ", confidenceEl(p.confidence)),
        ),
      ),
      h(
        "table.table",
        { style: { marginTop: "12px" } },
        h("thead", h("tr", h("th", "Fuente"), h("th", "Partidas"), h("th", "WR"), h("th", "IC 95 %"))),
        h("tbody", sourceRow("OP.GG", p.sources.opgg), sourceRow("Tu base de datos", p.sources.own), (d.breakdown.opgg_patch || []).map((r) => h("tr", h("td.muted", `   · parche ${r.patch}`), h("td.num.muted", fmt.int(r.games)), h("td.num.muted", fmt.pct(r.wr)), h("td")))),
      ),
      h("div.row", { style: { marginTop: "10px" } }, h("span.small.muted", `WR general en el rol: ${dd.name(d.champ)} ${fmt.pct(d.a.wr_adj)} · ${dd.name(d.opp)} ${fmt.pct(d.b.wr_adj)}`), h("span.spacer"), h("button.btn.btn-sm", { onclick: () => { m.close(); openMatchup(d.role, d.opp, d.champ); } }, icon("swap"), "Ver desde el rival")),
    ),
    opggCard(d, opgg),
  );

  const lane = p.lane;
  const laneCard = lane
    ? h(
        "section.card",
        h("h3.card-title", icon("activity"), "Fase de líneas (tu base de datos)"),
        h(
          "div.kpis",
          kpi("Veredicto", h(`span.${lane.verdict.code === "gana" ? "good" : lane.verdict.code === "pierde" ? "bad" : ""}`, lane.verdict.label), `${fmt.int(lane.dgames)} partidas con detalle`),
          kpi("CS a los 10", fmt.signed(lane.cs10_diff, 1), "diferencia media"),
          kpi("Gana la línea", fmt.pct(lane.lane_win), "≥20 % oro y XP al 14'"),
          kpi("Pierde la línea", fmt.pct(lane.lane_loss)),
          kpi("Solo kills", `${fmt.num(lane.solo_kills_for, 2)} / ${fmt.num(lane.solo_kills_against, 2)}`, "a favor / en contra"),
          kpi("Placas", `${fmt.num(lane.plates_for, 2)} / ${fmt.num(lane.plates_against, 2)}`, "a favor / en contra"),
          kpi("Oro/min", fmt.signed(lane.gpm_diff, 0), "diferencia en la partida"),
        ),
      )
    : null;

  const breakdownTable = (rows, label, fmtKey = (k) => k) =>
    rows && rows.length
      ? h("div", h("div.small.muted", { style: { margin: "4px 0 6px" } }, label), table(
          [
            { key: "key", label, render: (r) => fmtKey(r.key) },
            { key: "games", label: "Partidas", render: (r) => fmt.int(r.games) },
            { key: "wr", label: "WR", render: (r) => h(`b.${wrClass(r.wr)}`, fmt.pct(r.wr)) },
            { key: "ci", label: "IC 95 %", sort: false, render: (r) => h("span.muted.small", `${fmt.pct(r.ci[0])} – ${fmt.pct(r.ci[1])}`) },
          ],
          rows.slice(0, 12),
          { sortKey: "games" },
        ))
      : null;
  const ownBreak = d.breakdown.tier.length || d.breakdown.patch.length
    ? h(
        "section.card",
        h("h3.card-title", icon("database"), "Desglose de tu base de datos"),
        h("div.grid.grid-3", breakdownTable(d.breakdown.tier, "Elo"), breakdownTable(d.breakdown.patch, "Parche", (k) => (k === "legacy" ? "Histórico" : k)), breakdownTable(d.breakdown.platform, "Región", (k) => (k === "legacy" ? "Histórico" : k.toUpperCase()))),
        d.legacy_note ? h("p.tiny.muted", "El histórico antiguo no guarda parche ni región y ~7 % viene de partidas normales; excluido con el filtro «Sin histórico antiguo».") : null,
      )
    : null;

  const guideBox = h("section.card", h("h3.card-title", icon("book"), "Guía del matchup"), guideBlock(d));
  return h("div.stack", summary, laneCard, ownBreak, guideBox);
}

function opggCard(d, g) {
  if (!g) {
    return h("section.card", h("h3.card-title", icon("swords"), "Cómo jugarlo (OP.GG)"), notice("OP.GG no tiene guía de este enfrentamiento o no está disponible ahora mismo."));
  }
  const style = { aggressive: "Agresivo", defensive: "Defensivo", even: "Igualado" }[g.play_style] || g.play_style || "—";
  const winner = g.lane_advantage ? (g.lane_advantage === d.champ ? "good" : "bad") : "";
  const build = (label, entries, render) =>
    entries && entries.length
      ? h("div.row", { style: { alignItems: "flex-start", gap: "10px" } }, h("span.small.muted", { style: { width: "92px", flex: "none", paddingTop: "4px" } }, label), h("div.col", { style: { gap: "4px" } }, entries.map(render)))
      : null;
  const wrTxt = (e) => (e.games ? h("span.tiny.muted", `${fmt.pct((e.wins / e.games) * 100)} · ${fmt.int(e.games)}`) : null);
  return h(
    "section.card",
    h("h3.card-title", icon("swords"), "Cómo jugarlo (OP.GG)"),
    h(
      "dl.kv",
      h("dt", "Gana la línea"), h(`dd.${winner}`, g.lane_advantage ? dd.name(g.lane_advantage) : "Igualada"),
      h("dt", "Más solo kills"), h("dd", g.solo_kill_advantage ? dd.name(g.solo_kill_advantage) : "—"),
      h("dt", "Estilo recomendado"), h("dd", style),
    ),
    g.tip ? h("div.notice.notice-info", { style: { margin: "12px 0" } }, icon("info"), h("div", g.tip)) : null,
    h(
      "div.col",
      { style: { gap: "8px" } },
      build("Runas", g.runes, (r) => h("div.row", runeImg(r.keystone, 28), runeImg(r.secondary, 20), h("span.small", (r.names || []).slice(0, 4).join(" · ")), wrTxt(r))),
      build("Objetos clave", g.core_items, (e) => h("div.row", h("div.items", (e.ids || []).map(itemImg)), wrTxt(e))),
      build("Botas", g.boots, (e) => h("div.row", h("div.items", (e.ids || []).map(itemImg)), h("span.small", (e.names || []).join(", ")), wrTxt(e))),
      build("Inicio", g.starter_items, (e) => h("div.row", h("div.items", (e.ids || []).map(itemImg)), wrTxt(e))),
      build("Hechizos", g.spells, (e) => h("div.row", h("div.items", (e.ids || []).map(spellImg)), h("span.small", (e.names || []).join(" + ")), wrTxt(e))),
      build("Habilidades", g.skills, (e) => h("div.row-wrap", { style: { gap: "3px" } }, (e.order || []).map((k) => h("span.pill", { style: { padding: "0 6px" } }, k)))),
    ),
    h("p.tiny.faint", { style: { marginTop: "10px" } }, "Builds y consejo del parche en curso según OP.GG (muestras pequeñas al inicio del parche)."),
  );
}

function guideBlock(d) {
  const box = h("div");
  const features = state.status?.features || {};
  const showContent = async () => {
    mount(box, loading("Abriendo guía…"));
    try {
      const g = await api.get(`/api/guides/${d.role}/${d.champ}/${d.opp}`);
      mount(box, h("div.row", h("span.pill.pill-gold", g.source), h("span.tiny.muted", g.path)), h("div.prose", { style: { marginTop: "10px" } }, g.content));
    } catch (err) {
      mount(box, errorBox(err));
    }
  };
  const generate = async () => {
    mount(box, loading("Generando guía con Gemini usando estas estadísticas…"));
    try {
      const g = await api.post("/api/guides/generate", { role: d.role, champ: d.champ, opp: d.opp });
      mount(box, h("div.row", h("span.pill.pill-gold", "IA"), h("span.tiny.muted", g.path)), h("div.prose", { style: { marginTop: "10px" } }, g.content));
    } catch (err) {
      mount(box, errorBox(err), generateBtn());
    }
  };
  const generateBtn = () =>
    features.gemini
      ? h("button.btn.btn-primary", { onclick: generate, style: { marginTop: "10px" } }, icon("spark"), "Generar guía con IA")
      : h("p.small.muted", "Para generar guías con IA añade GEMINI_KEY_1 en .env e instala requirements-coaching.txt.");
  if (d.guide) {
    mount(box, h("div.row", h("span.pill.pill-gold", d.guide.source), h("span.small.muted", d.guide.path), h("span.spacer"), h("button.btn.btn-sm", { onclick: showContent }, icon("eye"), "Leer guía")));
  } else {
    mount(box, h("p.small.muted", "Todavía no hay guía guardada para este enfrentamiento."), generateBtn());
  }
  return box;
}
