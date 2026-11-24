// Explorador de enfrentamientos y ficha detallada de cada matchup.
import { api, filterParams, isPending } from "../api.js";
import { champPicker } from "../components/picker.js";
import { dd, ROLE_LABEL, state } from "../state.js";
import { champ, champImg, collapsible, confidenceEl, empty, errorBox, fmt, h, icon, itemImg, kpi, latest, loading, modal, mount, notice, patchChanges, roleIcon, roleTabs, runeImg, select, sourceBadges, spellImg, table, verdictEl, wrCell, wrClass } from "../ui.js";

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
      void load(); // trata sus errores (los pinta en la lista)
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
        rerender(container, [r, params.champ || ""]);
      }),
      h("div", { style: { width: "260px" } }, picker),
      h("div.filter", h("label", "Mínimo de partidas"), select([0, 50, 100, 500, 1000, 3000].map((v) => ({ value: v, label: v ? `≥ ${fmt.int(v)}` : "Todas" })), opts.minGames, (v) => { opts.minGames = Number(v); void load(); }, "select select-sm")),
      h("div.filter", h("label", "Ordenar por"), select([
        { value: "wr_adj", label: "WR ajustado" },
        { value: "delta", label: "Ventaja propia (Δ)" },
        { value: "games", label: "Partidas" },
        { value: "cs10", label: "CS a los 10" },
      ], opts.sort, (v) => { opts.sort = v; void load(); }, "select select-sm")),
    ),
    listEl,
  );
  void load();
}

export function onFilters() {
  void load();
}

// Vuelve a pintar la vista (otro rol u otro campeón); si algo falla, el error se ve en la propia vista.
function rerender(container, args) {
  render(container, args).catch((err) => mount(container, errorBox(err)));
}

function updateHash() {
  const champPart = params.champ ? `/${params.champ}` : "";
  const hash = `#/matchups/${params.role}${champPart}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

async function load() {
  if (!root) return;
  const fresh = latest("matchups");
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
          h("div.chips", top.map((c) => h("span.chip", { onclick: () => { params.champ = c.champ; updateHash(); rerender(root, [params.role, c.champ]); } }, champImg(c.champ, "xs"), dd.name(c.champ), h("span.tiny.muted", fmt.pct(c.wr_adj))))),
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
    if (!root || !fresh()) return;
    renderList(data);
  } catch (err) {
    mount(listEl, errorBox(err));
  }
}

// Pone un tooltip (title) a un nodo ya creado; sin texto, lo deja igual.
function withTip(el, tip) {
  if (tip) el.title = tip;
  return el;
}

// Línea de OP.GG del tooltip: WR y, si los da, tier y puesto.
function opggTip(o) {
  const tier = o.tier ? ` · Tier ${o.tier} · #${o.rank}` : "";
  return `OP.GG: ${fmt.pct(o.wr)}${tier}`;
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
        // OP.GG, pick/ban y tu BD van en la sub-línea y el tooltip del WR ajustado.
        withTip(
          kpi(
            "WR ajustado",
            h(`span.${wrClass(b.wr_adj)}`, fmt.pct(b.wr_adj)),
            [
              `${fmt.compact(b.games)} partidas`,
              b.opgg?.tier ? `Tier ${b.opgg.tier}` : null,
              b.own ? `tu BD ${fmt.pct(b.own.wr)}` : null,
            ].filter(Boolean).join(" · ") + (b.opgg || b.own ? " ⓘ" : ""),
          ),
          [
            b.opgg ? opggTip(b.opgg) : null,
            b.opgg ? `Pick ${fmt.pct(b.opgg.pick)} · ban ${fmt.pct(b.opgg.ban)}` : null,
            b.own ? `Tu BD: ${fmt.pct(b.own.wr)} en ${fmt.int(b.own.games)} partidas` : null,
          ].filter(Boolean).join("\n"),
        ),
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
  // Vista limpia: Rival, WR ajustado, Veredicto y Partidas. El resto (WR bruto, esperado, Δ, línea,
  // fiabilidad) va en el tooltip de la fila y, completo, en el detalle al pulsarla.
  const rowTip = (m) =>
    [
      `WR bruto ${fmt.pct(m.wr)}`,
      `Esperado ${fmt.pct(m.expected)} (solo por la fuerza de cada campeón, log5)`,
      `Δ propia ${fmt.signed(m.delta, 1)}`,
      m.lane ? `Línea (tu BD): ${m.lane.verdict.label} · CS@10 ${fmt.signed(m.lane.cs10_diff, 1)} · ${fmt.int(m.lane.dgames)} part.` : null,
      m.confidence ? `Fiabilidad: ${m.confidence.label}` : null,
      "Pulsa para ver el detalle",
    ].filter(Boolean).join("\n");
  const cols = [
    { key: "opp", label: "Rival", sort: (m) => dd.name(m.opp), render: (m) => h("div", { title: rowTip(m) }, champ(m.opp, { sub: `WR general ${fmt.pct(m.opp_wr)}` })) },
    { key: "wr_adj", label: "WR ajustado", render: (m) => wrCell(m.wr_adj, null, m.ci) },
    { key: "verdict", label: "Veredicto", sort: (m) => m.wr_adj, render: (m) => h("div.col", { style: { gap: "2px" }, title: rowTip(m) }, verdictEl(m.verdict), counterTag(m.counter)) },
    { key: "games", label: "Partidas", render: (m) => h("div.col", { style: { gap: "3px" } }, h("b.num", fmt.int(m.games)), sourceBadges(m.sources)) },
  ];
  // «Δ propia» y «CS a los 10» ya no son columnas: la API devuelve la lista ordenada y se respeta ese orden.
  const sortKey = cols.some((c) => c.key === opts.sort) ? opts.sort : null;
  mount(
    listEl,
    baseCard(data),
    h("div", { style: { height: "16px" } }),
    table(cols, data.matchups, { sortKey, onRow: (m) => openMatchup(data.role, data.champ, m.opp) }),
    h(
      "p.tiny.muted",
      { style: { marginTop: "10px" }, title: `Prior: ${fmt.int(data.priors.matchup)} partidas virtuales hacia lo esperado. Pasa el ratón por un rival para ver WR bruto, esperado, Δ, línea y fiabilidad; pulsa una fila para ver el detalle, la build contra ese rival y la guía.` },
      "Pulsa una fila para ver el detalle ⓘ",
    ),
  );
}

// «Counter» / «Te counterea» bajo el veredicto (si no hay counter claro, nada).
function counterTag(counter) {
  if (counter === "counter") return h("span.tiny.good", "Counter");
  if (counter === "countered") return h("span.tiny.bad", "Te counterea");
  return null;
}

// ------------------------------------------------------------------ detalle (modal)
export async function openMatchup(role, a, b) {
  const title = h("div.row", champImg(a, "sm"), h("h3", `${dd.name(a)} vs ${dd.name(b)}`), champImg(b, "sm"), h("span.pill", roleIcon(role), ROLE_LABEL[role]));
  const m = modal(title, loading("Analizando el enfrentamiento…"), { wide: true });
  try {
    const load = async (tries = 0) => {
      const data = await api.get(`/api/matchups/${role}/${a}/${b}`, filterParams());
      if (!m.body.isConnected) return;
      mount(m.body, detailView(data, m));
      // Algún dato (guía, counters) aún se descargaba: se completa solo.
      if (isPending(data) && tries < 6) setTimeout(() => m.body.isConnected && load(tries + 1).catch(() => {}), 3500);
    };
    await load();
  } catch (err) {
    mount(m.body, errorBox(err));
  }
}

function detailView(d, m) {
  const web = d.web_sources || {};
  // Si las dos webs dicen cosas distintas con muestra suficiente, se avisa: mejor prudencia que un falso «favorable».
  const disagree = web.opgg && web.lolalytics && web.opgg.games >= 300 && web.lolalytics.games >= 300 && Math.abs(web.opgg.wr - web.lolalytics.wr) >= 3;
  const summary = h("div.grid.grid-2", resultCard(d, m, web), opggCard(d, d.opgg_guide));
  const lane = laneCard(d.pair.lane);
  const ownBreak = ownBreakCard(d);
  const guideBox = h("section.card", h("h3.card-title", icon("book"), "Guía del matchup"), guideBlock(d));
  const changes = patchCard(d);
  const disagreeNotice = disagree
    ? notice(`OP.GG (${fmt.pct(web.opgg.wr)}) y Lolalytics (${fmt.pct(web.lolalytics.wr)}) no coinciden en este enfrentamiento: el resultado combinado es más incierto de lo normal.`, "warn")
    : null;
  return h(
    "div.stack",
    disagreeNotice,
    summary,
    changes,
    lane,
    ownBreak ? collapsible("matchup.desglose", [icon("database"), "Desglose de tu base de datos"], ownBreak, { summary: "por elo, parche y región" }) : null,
    guideBox,
  );
}

// Tarjeta «Resultado de partida»: WR ajustado, bruto, esperado, ventaja propia, muestra y desglose por fuente.
function resultCard(d, m, web) {
  const p = d.pair;
  return h(
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
        h("dt", "Ventaja propia"), h(`dd.${deltaClass(p.delta)}`, fmt.signed(p.delta, 2, " pp")),
        h("dt", "Muestra"), h("dd", fmt.int(p.games), " partidas ", confidenceEl(p.confidence)),
      ),
    ),
    h(
      "div",
      { style: { marginTop: "12px" } },
      collapsible(
        "matchup.fuentes",
        [icon("database"), "Por fuente y parche"],
        () => sourcesTable(d, web),
        { cls: "flat", summary: sourcesSummary(web, p.sources) },
      ),
    ),
    h("div.row", { style: { marginTop: "10px" } }, h("span.small.muted", `WR general en el rol: ${dd.name(d.champ)} ${fmt.pct(d.a.wr_adj)} · ${dd.name(d.opp)} ${fmt.pct(d.b.wr_adj)}`), h("span.spacer"), h("button.btn.btn-sm", { onclick: () => { m.close(); void openMatchup(d.role, d.opp, d.champ); } }, icon("swap"), "Ver desde el rival")),
  );
}

// Color de la ventaja propia: solo si pasa de ±0,5 pp.
function deltaClass(delta) {
  if (delta > 0.5) return "good";
  if (delta < -0.5) return "bad";
  return "";
}

// Tabla «Por fuente y parche»: las webs (combinadas y por separado), tu BD y las webs por parche.
function sourcesTable(d, web) {
  const sources = d.pair.sources;
  const both = web.opgg && web.lolalytics;
  return h(
    "table.table",
    h("thead", h("tr", h("th", "Fuente"), h("th", "Partidas"), h("th", "WR"), h("th", "IC 95 %"))),
    h(
      "tbody",
      both ? sourceRow("Webs (combinadas)", sources.opgg) : null,
      sourceRow(both ? "· OP.GG" : "OP.GG", web.opgg || (web.lolalytics ? null : sources.opgg)),
      sourceRow(both ? "· Lolalytics" : "Lolalytics", web.lolalytics),
      sourceRow("Tu base de datos", sources.own),
      (d.breakdown.opgg_patch || []).map((r) => h("tr", h("td.muted", `   · ${r.source === "lolalytics" ? "Lolalytics" : "OP.GG"} parche ${r.patch}`), h("td.num.muted", fmt.int(r.games)), h("td.num.muted", fmt.pct(r.wr)), h("td"))),
    ),
  );
}

// Una fila de la tabla de fuentes (sin datos de esa fuente, no hay fila).
function sourceRow(label, src) {
  if (!src) return null;
  const ci = src.ci ? `${fmt.pct(src.ci[0])} – ${fmt.pct(src.ci[1])}` : "—";
  return h("tr", h("td", label), h("td.num", fmt.int(src.games)), h("td.num", h(`b.${wrClass(src.wr)}`, fmt.pct(src.wr))), h("td.num.muted", ci));
}

// Resumen del plegable de fuentes: las que hay (o, si no hay ninguna, lo que contiene).
function sourcesSummary(web, sources) {
  const names = [web.opgg ? "OP.GG" : null, web.lolalytics ? "Lolalytics" : null, !web.opgg && !web.lolalytics && sources.opgg ? "OP.GG" : null, sources.own ? "tu BD" : null];
  return names.filter(Boolean).join(" · ") || "partidas, WR e IC 95 %";
}

// Fase de líneas según tu base de datos (sin datos de la línea, no hay tarjeta).
function laneCard(lane) {
  if (!lane) return null;
  return h(
    "section.card",
    h("h3.card-title", icon("activity"), "Fase de líneas (tu base de datos)"),
    h(
      "div.kpis",
      kpi("Veredicto", h(`span.${laneVerdictClass(lane.verdict.code)}`, lane.verdict.label), `${fmt.int(lane.dgames)} partidas con detalle`),
      kpi("CS a los 10", fmt.signed(lane.cs10_diff, 1), "diferencia media"),
      kpi("Gana la línea", fmt.pct(lane.lane_win), "≥20 % oro y XP al 14'"),
    ),
    h(
      "div",
      { style: { marginTop: "12px" } },
      collapsible(
        "matchup.linea.mas",
        [icon("activity"), "Más datos de la línea"],
        () =>
          h(
            "div.kpis",
            kpi("Pierde la línea", fmt.pct(lane.lane_loss)),
            kpi("Solo kills", `${fmt.num(lane.solo_kills_for, 2)} / ${fmt.num(lane.solo_kills_against, 2)}`, "a favor / en contra"),
            kpi("Placas", `${fmt.num(lane.plates_for, 2)} / ${fmt.num(lane.plates_against, 2)}`, "a favor / en contra"),
            kpi("Oro/min", fmt.signed(lane.gpm_diff, 0), "diferencia en la partida"),
          ),
        { cls: "flat", summary: "pierde la línea, solo kills, placas y oro/min" },
      ),
    ),
  );
}

// Color del veredicto de la línea.
function laneVerdictClass(code) {
  if (code === "gana") return "good";
  if (code === "pierde") return "bad";
  return "";
}

// Tabla de tu BD por elo, parche o región (sin filas, no se pinta).
function breakdownTable(rows, label, fmtKey = (k) => k) {
  if (!rows?.length) return null;
  const cols = [
    { key: "key", label, render: (r) => fmtKey(r.key) },
    { key: "games", label: "Partidas", render: (r) => fmt.int(r.games) },
    { key: "wr", label: "WR", render: (r) => h(`b.${wrClass(r.wr)}`, fmt.pct(r.wr)) },
    { key: "ci", label: "IC 95 %", sort: false, render: (r) => h("span.muted.small", `${fmt.pct(r.ci[0])} – ${fmt.pct(r.ci[1])}`) },
  ];
  return h("div", h("div.small.muted", { style: { margin: "4px 0 6px" } }, label), table(cols, rows.slice(0, 12), { sortKey: "games" }));
}

// Desglose de tu BD por elo, parche y región (sin datos por elo ni por parche, no hay tarjeta).
function ownBreakCard(d) {
  const bd = d.breakdown;
  if (!bd.tier.length && !bd.patch.length) return null;
  return h(
    "section.card",
    h("h3.card-title", icon("database"), "Desglose de tu base de datos"),
    h("div.grid.grid-3", breakdownTable(bd.tier, "Elo"), breakdownTable(bd.patch, "Parche", (k) => (k === "legacy" ? "Histórico" : k)), breakdownTable(bd.platform, "Región", (k) => (k === "legacy" ? "Histórico" : k.toUpperCase()))),
    d.legacy_note ? h("p.tiny.muted", "El histórico antiguo no guarda parche ni región y ~7 % viene de partidas normales; excluido con el filtro «Sin histórico antiguo».") : null,
  );
}

// Cambios recientes de los dos campeones según las notas de parche de Riot (sin cambios, no hay tarjeta).
function patchCard(d) {
  const changes = d.patch_changes || {};
  const changeBlocks = [d.champ, d.opp]
    .map((c) => (changes[String(c)]?.length ? h("div", h("div.row", champImg(c, "xs"), h("b.small", dd.name(c))), patchChanges(changes[String(c)])) : null))
    .filter(Boolean);
  if (!changeBlocks.length) return null;
  const changedChamps = [d.champ, d.opp].filter((c) => changes[String(c)]?.length);
  const verb = changedChamps.length === 1 ? "tiene cambios" : "tienen cambios";
  return collapsible(
    "matchup.cambios",
    [icon("spark"), "Cambios recientes (notas de parche de Riot)"],
    h("section.card", h("h3.card-title", icon("spark"), "Cambios recientes (notas de parche de Riot)"), h("div.grid.grid-2", changeBlocks)),
    { summary: `${changedChamps.map((c) => dd.name(c)).join(" y ")} ${verb}` },
  );
}

function opggCard(d, g) {
  if (!g) {
    return h("section.card", h("h3.card-title", icon("swords"), "Cómo jugarlo (OP.GG)"), notice("OP.GG no tiene guía de este enfrentamiento o no está disponible ahora mismo."));
  }
  const style = { aggressive: "Agresivo", defensive: "Defensivo", even: "Igualado" }[g.play_style] || g.play_style || "—";
  const winner = laneWinnerClass(g.lane_advantage, d.champ);
  const build = (label, entries, render) =>
    entries?.length
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
    ),
    [g.boots, g.starter_items, g.spells, g.skills].some((x) => x?.length)
      ? h(
          "div",
          { style: { marginTop: "10px" } },
          collapsible(
            "matchup.build",
            [icon("crown"), "Build completa"],
            () =>
              h(
                "div.col",
                { style: { gap: "8px" } },
                build("Botas", g.boots, (e) => h("div.row", h("div.items", (e.ids || []).map(itemImg)), h("span.small", (e.names || []).join(", ")), wrTxt(e))),
                build("Inicio", g.starter_items, (e) => h("div.row", h("div.items", (e.ids || []).map(itemImg)), wrTxt(e))),
                build("Hechizos", g.spells, (e) => h("div.row", h("div.items", (e.ids || []).map(spellImg)), h("span.small", (e.names || []).join(" + ")), wrTxt(e))),
                build("Habilidades", g.skills, (e) => h("div.row-wrap", { style: { gap: "3px" } }, (e.order || []).map((k) => h("span.pill", { style: { padding: "0 6px" } }, k)))),
              ),
            { cls: "flat", summary: "botas, inicio, hechizos y habilidades" },
          ),
        )
      : null,
    h("p.tiny.faint", { style: { marginTop: "10px" } }, "Builds y consejo del parche en curso según OP.GG (muestras pequeñas al inicio del parche)."),
  );
}

// Color de «Gana la línea» (OP.GG): verde si la gana el campeón propio, rojo si el rival; sin ventaja, ninguno.
function laneWinnerClass(advantage, own) {
  if (!advantage) return "";
  return advantage === own ? "good" : "bad";
}

function guideBlock(d) {
  const box = h("div");
  const showContent = async () => {
    mount(box, loading("Abriendo guía…"));
    try {
      const g = await api.get(`/api/guides/${d.role}/${d.champ}/${d.opp}`);
      mount(box, h("div.row", h("span.pill.pill-gold", g.source), h("span.tiny.muted", g.path)), h("div.prose", { style: { marginTop: "10px" } }, g.content));
    } catch (err) {
      mount(box, errorBox(err));
    }
  };
  if (d.guide) {
    mount(box, h("div.row", h("span.pill.pill-gold", d.guide.source), h("span.small.muted", d.guide.path), h("span.spacer"), h("button.btn.btn-sm", { onclick: showContent }, icon("eye"), "Leer guía")));
  } else {
    mount(box, h("p.small.muted", "Todavía no hay guía guardada para este enfrentamiento."));
  }
  return box;
}
