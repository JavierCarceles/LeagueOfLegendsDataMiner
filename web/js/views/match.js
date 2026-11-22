// Análisis de una partida: equipos, líneas, objetivos, predicción del modelo y timeline.
import { api } from "../api.js";
import { lineChart } from "../charts.js";
import { dd, ROLE_LABEL, TIER_LABEL } from "../state.js";
import { champImg, collapsible, empty, errorBox, fmt, h, icon, itemImg, kpi, loading, mount, roleIcon, spellImg, wrClass } from "../ui.js";

let root = null;

export function destroy() {
  root = null;
}

export async function render(container, args = []) {
  root = container;
  const matchId = (args[0] || "").toUpperCase();
  const input = h("input.input", { placeholder: "ID de partida (p. ej. EUW1_7345678901)", value: matchId });
  const go = () => input.value.trim() && (location.hash = `#/partida/${input.value.trim().toUpperCase()}`);
  input.addEventListener("keydown", (e) => e.key === "Enter" && go());
  const body = h("div");
  mount(
    container,
    h("div.section-head", h("div", h("h2", "Análisis de partida"), h("p", "Marcador completo, comparación línea a línea, objetivos y lo que predecía el modelo de draft. El timeline (oro minuto a minuto) cuesta una llamada extra y se pide solo si lo abres."))),
    h("section.card", h("div.input-group", input, h("button.btn.btn-primary", { onclick: go }, icon("search"), "Analizar"))),
    h("div", { style: { height: "16px" } }),
    body,
  );
  if (!matchId) {
    mount(body, h("section.card", empty("Elige una partida", "Desde el perfil de un jugador pulsa «Analizar partida» o pega aquí su ID.", "match")));
    return;
  }
  mount(body, loading("Cargando partida…"));
  try {
    const m = await api.get(`/api/matches/${matchId}`);
    if (!root) return;
    mount(body, matchView(m));
  } catch (err) {
    mount(body, errorBox(err));
  }
}

function playerRow(p, maxDmg) {
  return h(
    "tr",
    h("td", h("div.row", champImg(p.champ), h("div", h("div", { style: { fontWeight: 650 } }, p.name ? h("a", { href: p.tag ? `#/jugador/${location.hash.split("/")[2]?.split("_")[0]?.toLowerCase() || "euw1"}/${encodeURIComponent(p.name)}/${encodeURIComponent(p.tag)}` : null }, p.name) : dd.name(p.champ)), h("div.tiny.muted", p.role ? ROLE_LABEL[p.role] : "—", ` · nivel ${p.level}`)))),
    h("td", { title: `CS ${p.cs} (${fmt.num(p.cs_min, 1)}/min) · Oro ${fmt.int(p.gold)} · Visión ${p.vision} (${p.control_wards} pinks) · KP ${fmt.pct(p.kp, 0)}`, style: { cursor: "help" } }, h("b", `${p.kills}/${p.deaths}/${p.assists}`), h("div.tiny.muted", `KDA ${fmt.num(p.kda, 2)}`)),
    h("td", h("div.row", h("div.wrbar", { style: { width: "90px" } }, h("div.fill", { style: { width: `${(p.dmg / maxDmg) * 100}%`, background: "var(--gold)" } })), h("span.small", fmt.compact(p.dmg))), h("div.tiny.muted", `${fmt.pct(p.dmg_share, 0)} del equipo`)),
    h("td", h("div.row", { style: { gap: "4px" } }, h("div.items", p.spells.map(spellImg)), h("div.items", p.items.map(itemImg)))),
    h("td", p.score !== null && p.score !== undefined ? h(`span.score.${p.score >= 6.5 ? "hi" : p.score <= 3.5 ? "lo" : ""}`, { title: "Nota 0-10 frente a la media de su rol (5 = media)" }, fmt.num(p.score, 1)) : h("span.faint", "—")),
  );
}

function teamTable(team, players, maxDmg) {
  const side = team.team === 100 ? "blue" : "red";
  return h(
    "section.card",
    h(
      "h3.card-title",
      h(`span.pill.pill-dot.${side === "blue" ? "pill-blue" : "pill-red"}`, side === "blue" ? "Equipo azul" : "Equipo rojo"),
      team.win ? h("span.pill.pill-good", "Victoria") : h("span.pill.pill-bad", "Derrota"),
      h("span.spacer"),
      h("span.small.muted", { title: `${team.kills} kills · ${fmt.compact(team.gold)} oro · ${team.towers} torres · ${team.dragons} dragones · ${team.barons} barones · ${team.grubs} larvas`, style: { cursor: "help" } }, `${team.kills} kills · ${team.towers} torres · ${team.dragons} dragones`),
    ),
    h(
      "div.table-wrap",
      h(
        `table.table.team-table.${side}`,
        h("thead", h("tr", ["Jugador", "KDA", "Daño", "Build", "Nota"].map((c) => h("th", c)))),
        h("tbody", players.map((p) => playerRow(p, maxDmg))),
      ),
    ),
    team.firsts.length ? h("div.row-wrap", { style: { marginTop: "10px" } }, team.firsts.map((f) => h("span.pill.pill-gold", f))) : null,
  );
}

function matchView(m) {
  const maxDmg = Math.max(1, ...m.players.map((p) => p.dmg || 0));
  const blue = m.players.filter((p) => p.team === 100);
  const red = m.players.filter((p) => p.team === 200);
  const byPid = Object.fromEntries(m.players.map((p) => [p.pid, p]));
  const predBox = h("div.kpi", h("div.kpi-label", "Predicción del draft"), h("div.kpi-value.muted", { style: { fontSize: "14px" } }, "Calculando…"));
  const predNote = h("p.tiny.muted");
  if (m.can_predict) {
    api
      .get(`/api/matches/${m.match_id}/prediction`)
      .then(({ prediction: pred }) => {
        if (!pred) return predBox.remove();
        mount(predBox, h("div.kpi-label", "Predicción del draft"), h(`div.kpi-value.${wrClass(pred.blue_win_probability)}`, `Azul ${fmt.pct(pred.blue_win_probability)}`), h("div.kpi-sub", pred.correct ? "✓ acertó" : "✗ falló", ` · nota ${pred.grade.letter}`));
        predNote.textContent = pred.note;
      })
      .catch(() => predBox.remove());
  }
  const tierLabel = TIER_LABEL[m.tier] || m.tier;
  const head = h(
    "section.card",
    h("div.small.muted", { style: { marginBottom: "10px" } }, [m.queue_label, `Parche ${m.patch}${tierLabel ? ` · ${tierLabel}` : ""}`, fmt.date(m.game_start)].filter(Boolean).join(" · ")),
    h(
      "div.kpis",
      kpi("Ganador", m.winner === 100 ? h("span", { style: { color: "#8fcaf2" } }, "Azul") : h("span", { style: { color: "#ff9aa6" } }, "Rojo"), m.surrender ? "por rendición" : ""),
      kpi("Duración", fmt.duration(m.duration)),
      m.can_predict ? predBox : null,
    ),
  );
  const laneCount = m.lanes.length;
  const blueLanes = m.lanes.filter((l) => l.lane_winner === 100).length;
  const redLanes = m.lanes.filter((l) => l.lane_winner === 200).length;
  const lanesSummary = blueLanes > redLanes ? `Azul gana ${blueLanes}/${laneCount} líneas` : redLanes > blueLanes ? `Rojo gana ${redLanes}/${laneCount} líneas` : `Líneas igualadas (${blueLanes}-${redLanes})`;
  const lanes = collapsible(
    "partida.lineas",
    [icon("swords"), "Línea a línea"],
    () => h(
      "div.table-wrap",
      { title: "Diferencias desde el punto de vista del equipo azul. La fase de líneas la gana quien acaba el minuto 14 con ≥20 % más oro y experiencia." },
      h(
        "table.table",
        h("thead", h("tr", ["Rol", "Azul", "Rojo", "Δ CS@10", "Δ Oro", "Δ Daño", "Δ Kills", "Fase de líneas"].map((c) => h("th", c)))),
        h(
          "tbody",
          m.lanes.map((l) =>
            h(
              "tr",
              h("td", h("div.row", roleIcon(l.role), ROLE_LABEL[l.role])),
              h("td", h("div.row", champImg(byPid[l.blue].champ, "sm"), dd.name(byPid[l.blue].champ))),
              h("td", h("div.row", champImg(byPid[l.red].champ, "sm"), dd.name(byPid[l.red].champ))),
              diffCell(l.cs10_diff),
              diffCell(l.gold_diff, 0),
              diffCell(l.dmg_diff, 0),
              diffCell(l.kills_diff, 0),
              h("td", l.lane_winner ? h(`span.pill.${l.lane_winner === 100 ? "pill-blue" : "pill-red"}`, l.lane_winner === 100 ? "Azul" : "Rojo") : h("span.muted", "Igualada")),
            ),
          ),
        ),
      ),
    ),
    { summary: lanesSummary },
  );
  const timelineBox = h("div", h("button.btn", { onclick: () => loadTimeline(m, timelineBox) }, icon("activity"), m.has_timeline ? "Ver timeline" : "Cargar timeline (1 llamada a la API)"));
  return h(
    "div.stack",
    head,
    teamTable(m.teams[0], blue, maxDmg),
    teamTable(m.teams[1], red, maxDmg),
    lanes,
    h("section.card", h("h3.card-title", icon("activity"), "Diferencia de oro"), timelineBox),
    predNote,
  );
}

function diffCell(v, d = 1) {
  if (v === null || v === undefined) return h("td.muted", "—");
  return h(`td.num.${v > 0 ? "good" : v < 0 ? "bad" : ""}`, fmt.signed(v, d));
}

async function loadTimeline(m, box) {
  mount(box, loading("Cargando timeline…"));
  try {
    const t = await api.get(`/api/matches/${m.match_id}/timeline`);
    const labels = { dragon: "Dragón", baron_nashor: "Barón", riftherald: "Heraldo", horde: "Larvas", atakhan: "Atakhan", tower: "Torre", inhibitor: "Inhibidor" };
    const markers = t.events.filter((e) => e.type !== "kill" && e.team).map((e) => ({ x: e.t, team: e.team, label: `${Math.floor(e.t)}' ${labels[e.type] || e.type}${e.sub ? ` (${e.sub.replace("_DRAGON", "").toLowerCase()})` : ""} · ${e.team === 100 ? "azul" : "rojo"}` }));
    const chart = lineChart(t.series.map((s) => ({ x: s.minute, y: s.gold_diff })), { markers });
    const atTable = h(
      "div.table-wrap",
      { style: { marginTop: "12px" } },
      h(
        "table.table",
        h("thead", h("tr", ["Rol", "Oro @10", "Oro @15", "XP @15", "CS @15"].map((c) => h("th", c)))),
        h("tbody", t.lanes.map((l) => h("tr", h("td", h("div.row", roleIcon(l.role), ROLE_LABEL[l.role])), diffCell(l.gold10, 0), diffCell(l.gold15, 0), diffCell(l.xp15, 0), diffCell(l.cs15, 0)))),
      ),
    );
    mount(box, chart, h("div.legend", h("span", h("i", { style: { background: "var(--blue)" } }), "Objetivo azul (arriba)"), h("span", h("i", { style: { background: "var(--red)" } }), "Objetivo rojo (abajo)"), h("span", "Línea: oro azul − oro rojo")), atTable);
  } catch (err) {
    mount(box, errorBox(err));
  }
}
