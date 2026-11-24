// Perfil de jugador: rango, resumen, campeones, comparativa con la media y partidas.
import { api } from "../api.js";
import { dd, loadJSON, platformLabel, ROLE_LABEL, save, state, TIER_LABEL as TIER } from "../state.js";
import { champ, champImg, collapsible, debounce, empty, errorBox, fmt, h, icon, itemImg, kpi, latest, loading, modal, mount, rankImg, roleIcon, runeImg, select, spellImg, table, wrClass } from "../ui.js";

const RECENT = "tempu.players.recent";
let root = null;
let resultEl = null;
let query = { platform: "euw1", name: "", tag: "", queue: "solo", count: 20 };

export function destroy() {
  root = null;
}

export async function render(container, args = []) {
  root = container;
  if (args.length >= 3) {
    query = { ...query, platform: args[0].toLowerCase(), name: args[1], tag: args[2] };
  } else if (state.filters.platform !== "all" && state.meta?.platforms?.some((p) => p.id === state.filters.platform)) {
    query.platform = state.filters.platform;
  }
  resultEl = h("div");
  mount(
    container,
    h("div.section-head", h("div", h("h2", "Perfil de jugador"), h("p", "Estadísticas de sus últimas partidas y comparación con la media de tu base de datos para el mismo campeón, rol y elo. Las partidas ya descargadas no gastan cuota de la API."))),
    searchCard(),
    h("div", { style: { height: "16px" } }),
    resultEl,
  );
  if (query.name && query.tag) void load(); // trata sus errores (los pinta en el resultado)
  else mount(resultEl, recentBlock());
}

function searchCard() {
  const input = h("input.input", { placeholder: "Nombre#TAG (p. ej. Faker#KR1)", value: query.name ? `${query.name}#${query.tag}` : "", autocomplete: "off" });
  const suggestions = h("div.chips", { style: { marginTop: "8px" } });
  const platforms = (state.meta?.platforms || []).map((p) => ({ value: p.id, label: `${p.label} · ${p.name}` }));
  const submit = () => {
    const [name, tag] = input.value.split("#");
    if (!name || !tag) {
      input.focus();
      input.style.borderColor = "var(--bad)";
      return;
    }
    query.name = name.trim();
    query.tag = tag.trim();
    location.hash = `#/jugador/${query.platform}/${encodeURIComponent(query.name)}/${encodeURIComponent(query.tag)}`;
  };
  const suggest = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 3) return suggestions.replaceChildren();
    try {
      const { results } = await api.get("/api/players/search", { q });
      suggestions.replaceChildren(
        ...results.map((r) =>
          h("span.chip", { onclick: () => { query.platform = r.platform || query.platform; input.value = `${r.game_name}#${r.tag_line}`; submit(); } }, `${r.game_name}#${r.tag_line}`, h("span.tiny.muted", suggestionSub(r))),
        ),
      );
    } catch {
      /* sin sugerencias */
    }
  }, 250);
  input.addEventListener("input", suggest);
  input.addEventListener("keydown", (e) => e.key === "Enter" && submit());
  return h(
    "section.card",
    h(
      "div.row-wrap",
      { style: { gap: "10px" } },
      h("div", { style: { flex: "1 1 320px" } }, input),
      select(platforms, query.platform, (v) => (query.platform = v)),
      select(
        [
          { value: "solo", label: "Solo/Dúo" },
          { value: "flex", label: "Flexible" },
          { value: "ranked", label: "Clasificatorias" },
          { value: "all", label: "Todas (Grieta)" },
        ],
        query.queue,
        (v) => (query.queue = v),
      ),
      select([10, 20, 30, 40].map((n) => ({ value: n, label: `${n} partidas` })), query.count, (v) => (query.count = Number(v))),
      h("button.btn.btn-primary", { onclick: submit }, icon("search"), "Buscar"),
    ),
    suggestions,
  );
}

// Región y, si se sabe, elo de una sugerencia de búsqueda.
function suggestionSub(r) {
  const tier = r.tier ? ` · ${r.tier}` : "";
  return `${platformLabel(r.platform)}${tier}`;
}

function recentBlock() {
  const recent = loadJSON(RECENT, []);
  if (!recent.length) return h("section.card", empty("Busca un Riot ID", "Necesitas tu API key de Riot configurada en «Datos y API».", "user"));
  return h(
    "section.card",
    h("h3.card-title", icon("refresh"), "Búsquedas recientes"),
    h(
      "div.chips",
      recent.map((r) => h("span.chip", { onclick: () => (location.hash = `#/jugador/${r.platform}/${encodeURIComponent(r.name)}/${encodeURIComponent(r.tag)}`) }, r.icon != null ? h("img", { src: dd.profileIcon(r.icon), width: 18, height: 18, style: { borderRadius: "50%" } }) : null, `${r.name}#${r.tag}`, h("span.tiny.muted", platformLabel(r.platform)))),
    ),
  );
}

async function load() {
  const fresh = latest("player");
  mount(resultEl, loading("Consultando a Riot (respetando el límite de la API)…"));
  try {
    const p = await api.get(`/api/players/${query.platform}/${encodeURIComponent(query.name)}/${encodeURIComponent(query.tag)}`, { queue: query.queue, count: query.count });
    if (!root || !fresh()) return;
    const recent = loadJSON(RECENT, []).filter((r) => !(r.name.toLowerCase() === p.account.game_name.toLowerCase() && r.tag.toLowerCase() === p.account.tag_line.toLowerCase()));
    recent.unshift({ name: p.account.game_name, tag: p.account.tag_line, platform: p.platform, icon: p.summoner.icon });
    save(RECENT, recent.slice(0, 10));
    mount(resultEl, profileView(p));
  } catch (err) {
    mount(resultEl, errorBox(err));
  }
}

function rankCard(label, r) {
  if (!r) return h("div.rank-card", rankImg(null), h("div", h("div.tiny.muted", label), h("div", { style: { fontWeight: 700 } }, "Sin clasificar")));
  return h(
    "div.rank-card",
    rankImg(r.tier),
    h(
      "div",
      h("div.tiny.muted", label),
      h("div", { style: { fontWeight: 800, fontSize: "16px" } }, `${TIER[r.tier] || r.tier} ${["MASTER", "GRANDMASTER", "CHALLENGER"].includes(r.tier) ? "" : r.division}`),
      h("div.small", `${fmt.int(r.lp)} LP · `, h(`b.${wrClass(r.wr)}`, fmt.pct(r.wr)), h("span.muted", ` (${r.wins}V ${r.losses}D)`), r.hot_streak ? h("span.pill.pill-warn", { style: { marginLeft: "6px" } }, "Racha") : null),
    ),
  );
}

function profileView(p) {
  const s = p.summary;
  const head = h(
    "section.card",
    h(
      "div.profile-head",
      h("img.profile-icon", { src: dd.profileIcon(p.summoner.icon), alt: "" }),
      h(
        "div",
        h("div", { style: { fontSize: "24px", fontWeight: 800 } }, p.account.game_name, h("span.muted", ` #${p.account.tag_line}`)),
        h("div.row-wrap.muted", h("span.pill", platformLabel(p.platform)), p.summoner.level ? h("span", `Nivel ${p.summoner.level}`) : null, h("span.tiny", { title: `${p.fetch.downloaded} partidas descargadas ahora · ${p.fetch.from_cache} ya estaban guardadas`, style: { cursor: "help" } }, `${(p.fetch.downloaded || 0) + (p.fetch.from_cache || 0)} partidas`)),
        p.note ? h("div.small.warn", { style: { marginTop: "4px" } }, p.note) : null,
      ),
      h("div.spacer"),
      rankCard("Solo/Dúo", p.ranks.solo),
      rankCard("Flexible", p.ranks.flex),
    ),
  );
  if (!s.games) return h("div.stack", head, h("section.card", empty("Sin partidas en esta cola", "Prueba con otra cola o más partidas.")));

  const summary = h(
    "section.card",
    h("h3.card-title", icon("chart"), `Últimas ${s.games} partidas`, h("span.spacer"), h("div.form-dots", p.form.map((w) => h(`span.${w ? "w" : "l"}`, w ? "V" : "D"))), streakPill(p.streak)),
    h(
      "div.kpis",
      kpi("Winrate", h(`span.${wrClass(s.wr)}`, fmt.pct(s.wr)), `${s.wins}V ${s.losses}D`),
      kpi("KDA", fmt.num(s.kda, 2), `${fmt.num(s.kills)} / ${fmt.num(s.deaths)} / ${fmt.num(s.assists)}`),
      kpi("CS/min", fmt.num(s.cs_min, 1), s.cs10 !== null ? `CS@10 ${fmt.num(s.cs10)}` : null),
      kpi("Visión/min", fmt.num(s.vision_min, 2), `${fmt.num(s.control_wards, 1)} pinks/partida`),
    ),
    h(
      "details.more",
      { style: { marginTop: "10px" } },
      h("summary.btn.btn-ghost.btn-sm", "Más estadísticas (8)"),
      h(
        "div.kpis",
        { style: { marginTop: "10px" } },
        kpi("Δ CS@10", h(`span.${(s.cs10_diff ?? 0) >= 0 ? "good" : "bad"}`, fmt.signed(s.cs10_diff, 1)), "vs su rival de línea"),
        kpi("Gana la línea", fmt.pct(s.lane_win), `la pierde ${fmt.pct(s.lane_loss)}`),
        kpi("Oro/min", fmt.int(s.gold_min)),
        kpi("Daño/min", fmt.int(s.dmg_min), s.dmg_share !== null ? `${fmt.pct(s.dmg_share)} del equipo` : null),
        kpi("Part. kills", fmt.pct(s.kp)),
        kpi("Solo kills", fmt.num(s.solo_kills, 2), `placas ${fmt.num(s.plates, 2)}`),
        kpi("Primera sangre", fmt.pct(s.first_blood)),
        kpi("Duración", `${fmt.num(s.avg_minutes, 1)} min`),
      ),
    ),
  );

  const roles = h(
    "section.card",
    h("h3.card-title", icon("draft"), "Roles"),
    h("div.col", p.roles.map((r) => h("div.hbar", h("span.row", roleIcon(r.role), ROLE_LABEL[r.role] || "Sin rol"), h("div.track", h("span", { style: { width: `${(r.games / s.games) * 100}%` } })), h("span.right", `${r.games} · `, h(`b.${wrClass(r.wr)}`, fmt.pct(r.wr, 0)))))),
  );

  const champCol = { key: "champ", label: "Campeón", sort: (c) => dd.name(c.champ), render: (c) => champ(c.champ, { size: "sm", sub: ROLE_LABEL[c.main_role] || "" }) };
  const baseCols = [
    { key: "games", label: "Part.", render: (c) => c.games },
    { key: "wr", label: "WR", render: (c) => h(`b.${wrClass(c.wr)}`, fmt.pct(c.wr, 0)) },
    { key: "kda", label: "KDA", render: (c) => fmt.num(c.kda, 2) },
  ];
  const extraCols = [
    { key: "cs_min", label: "CS/min", render: (c) => fmt.num(c.cs_min, 1) },
    { key: "dmg_min", label: "Daño/min", render: (c) => fmt.int(c.dmg_min) },
    { key: "cs10_diff", label: "Δ CS@10", render: (c) => h(`span.${(c.cs10_diff ?? 0) >= 0 ? "good" : "bad"}`, fmt.signed(c.cs10_diff, 1)) },
  ];
  const champTip = (c) => `CS/min ${fmt.num(c.cs_min, 1)} · Daño/min ${fmt.int(c.dmg_min)} · Δ CS@10 ${fmt.signed(c.cs10_diff, 1)}`;
  const topChamps = [...p.champions].sort((a, b) => (b.games || 0) - (a.games || 0)).slice(0, 8);
  const pool = h(
    "section.card",
    h("h3.card-title", icon("crown"), "Campeones"),
    table(
      [{ ...champCol, render: (c) => h("div", { title: champTip(c) }, champCol.render(c)) }, ...baseCols],
      topChamps,
      { sortKey: "games" },
    ),
    h(
      "button.btn.btn-ghost.btn-sm",
      { style: { marginTop: "8px" }, onclick: () => modal(`Campeones (${p.champions.length})`, table([champCol, ...baseCols, ...extraCols], p.champions, { sortKey: "games" }), { wide: true }) },
      p.champions.length > topChamps.length ? `Ver todos (${p.champions.length})` : "Ver tabla completa",
    ),
  );

  const bench = benchCard(p.benchmarks);
  const matches = h("section.card", h("h3.card-title", icon("match"), "Partidas"), h("div.col", { style: { gap: "6px" } }, p.matches.map(matchRow)));
  return h("div.stack", head, summary, h("div.grid.player-split", roles, pool), bench, matches);
}

// Racha actual: «3 victorias seguidas» / «2 derrotas seguidas» (sin racha, nada).
function streakPill(streak) {
  if (!streak) return null;
  const won = streak > 0;
  return h(`span.pill.${won ? "pill-good" : "pill-bad"}`, `${Math.abs(streak)} ${won ? "victorias" : "derrotas"} seguidas`);
}

// Comparativa con la media del mismo campeón y rol (sin datos, no hay tarjeta).
function benchCard(benchmarks) {
  if (!benchmarks.length) return null;
  const plural = benchmarks.length === 1 ? "campeón" : "campeones";
  return collapsible(
    "jugador.comparativa",
    [icon("chart"), "Comparado con la media (mismo campeón y rol)"],
    h(
      "div.grid.grid-3",
      benchmarks.map((b) =>
        h(
          "div",
          h("div.row", { style: { marginBottom: "8px" } }, champImg(b.champ, "sm"), h("b", dd.name(b.champ)), h("span.tiny.muted", benchSub(b))),
          b.metrics.map((m) => {
            const pct = Math.max(-50, Math.min(50, m.diff_pct));
            return h(
              "div.diverge",
              h("span.small", m.label),
              h("div.track", h("span", { style: pct >= 0 ? { left: "50%", width: `${pct}%`, background: "var(--good)" } : { left: `${50 + pct}%`, width: `${-pct}%`, background: "var(--bad)" } })),
              h(`b.small.right.${m.diff_pct >= 0 ? "good" : "bad"}`, { title: `${fmt.num(m.player, 2)} vs ${fmt.num(m.avg, 2)}` }, fmt.signed(m.diff_pct, 0, " %")),
            );
          }),
        ),
      ),
    ),
    { summary: `${benchmarks.length} ${plural}` },
  );
}

// «Mid · media de 1,2 K partidas en Diamante» (el elo, solo si la media es de un elo concreto).
function benchSub(b) {
  const tier = b.tier ? ` en ${TIER[b.tier] || b.tier}` : "";
  return `${ROLE_LABEL[b.role]} · media de ${fmt.compact(b.sample)} partidas${tier}`;
}

function matchRow(m) {
  const detail = h("div.match-detail.hidden");
  const [cls, resultCls, resultText] = matchResult(m);
  const roleName = m.role ? ROLE_LABEL[m.role] : "—";
  const row = h(
    `div.match-row.${cls}`,
    { onclick: () => detail.classList.toggle("hidden") },
    h("div.bar"),
    h(
      "div.row",
      champImg(m.champ, "lg"),
      h(
        "div",
        { title: `${roleName} · ${m.queue_label} · ${fmt.duration(m.duration)}` },
        h("div", { style: { fontWeight: 700 } }, dd.name(m.champ)),
        h("div.tiny", { class: resultCls }, resultText, h("span.muted", ` · ${fmt.ago(m.game_start)}`)),
      ),
    ),
    h("div", { title: `KDA ${fmt.num(m.kda, 2)} · KP ${fmt.pct(m.kp, 0)} · ${m.cs} CS (${fmt.num(m.cs_min, 1)}/min)` }, h("div.kda", `${m.kills} / ${m.deaths} / ${m.assists}`)),
    h("div.row", h("div.items", m.items.map(itemImg)), m.opponent ? h("span.tiny.muted", { style: { marginLeft: "8px" } }, "vs ", champImg(m.opponent.champ, "xs")) : null),
    h("div"), // hueco de la antigua columna de mini-equipos (ahora en el detalle) para no descuadrar la rejilla
    h("div.muted", "⌄"),
  );
  mount(detail, matchDetail(m, roleName));
  return h("div", row, detail);
}

// Resultado de la partida: [clase de la fila, clase del texto, texto].
function matchResult(m) {
  if (m.remake) return ["remake", "muted", "Remake"];
  if (m.win) return ["win", "good", "Victoria"];
  return ["loss", "bad", "Derrota"];
}

// Detalle desplegable de una partida: cifras, mini-equipos, hechizos, runas y enlace al análisis.
function matchDetail(m, roleName) {
  return [
    h(
      "div.kpis",
      kpi("KDA", fmt.num(m.kda, 2), `KP ${fmt.pct(m.kp, 0)}`),
      kpi("CS", m.cs, `${fmt.num(m.cs_min, 1)}/min`),
      kpi("Oro", fmt.int(m.gold), `${fmt.int(m.gold_min)}/min`),
      kpi("Daño", fmt.int(m.dmg), `${fmt.pct(m.dmg_share)} del equipo`),
      kpi("Visión", m.vision, `${m.wards} wards · ${m.control_wards} pinks`),
      kpi("CS@10", fmt.num(m.cs10, 0), m.cs10_diff !== null ? `Δ ${fmt.signed(m.cs10_diff, 0)}` : null),
      kpi("Fase de líneas", lanePhase(m)),
      kpi("Solo kills", m.solo_kills ?? "—", `placas ${m.plates ?? "—"}`),
    ),
    h(
      "div.mini-teams",
      { style: { marginTop: "10px", maxWidth: "460px" } },
      [0, 1, 2, 3, 4].flatMap((i) => [m.team[i], m.enemies[i]]).map((t) => miniTeamCell(t, m)),
    ),
    h(
      "div.row-wrap",
      { style: { marginTop: "10px", gap: "14px" } },
      h("div.row", h("span.tiny.muted", "Hechizos"), h("div.items", m.spells.map(spellImg))),
      m.runes?.length ? h("div.row", h("span.tiny.muted", "Runas"), runeImg(m.runes[0], 26), runeImg(m.runes[2], 18)) : null,
      h("span.tiny.muted", `${roleName} · ${m.queue_label} · ${fmt.duration(m.duration)} · Parche ${m.patch} · ${m.tier}`),
      h("span.spacer"),
      h("a.btn.btn-sm", { href: `#/partida/${m.match_id}`, onclick: (e) => e.stopPropagation() }, icon("eye"), "Analizar partida"),
    ),
  ];
}

// Fase de líneas de la partida: ganada, perdida o igualada.
function lanePhase(m) {
  if (m.lane_adv > 0) return h("span.good", "Ganada");
  if (m.opp_lane_adv > 0) return h("span.bad", "Perdida");
  return "Igualada";
}

// Casilla de un jugador en los mini-equipos (resaltada si es el propio jugador; hueco vacío si falta).
function miniTeamCell(t, m) {
  if (!t) return h("div");
  const me = t.puuid && t.name === m.team.find((x) => x.champ === m.champ)?.name ? ".me" : "";
  return h(`div${me}`, champImg(t.champ, "xs"), h("span", t.name || "—"));
}
