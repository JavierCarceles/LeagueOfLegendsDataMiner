// Repeticiones: tus partidas reconstruidas en 2D con cada error marcado.
//
// #/repeticiones            lista de partidas (filtros y recuento de errores)
// #/repeticiones/{matchId}  visor: mapa + línea de tiempo + panel de errores
import { api } from "../api.js";
import { learnBox, learnCatalog, learnForTags } from "../components/learn.js";
import { createReplay, SEV_LABEL } from "../components/replay.js";
import { trackPos } from "../components/minimap.js";
import { localReplayIds, roflSection } from "../components/rofl.js";
import { dd, loadJSON, ROLE_LABEL, save, TIER_LABEL } from "../state.js";
import { champImg, champText, collapsible, debounce, empty, errorBox, fmt, h, icon, kpi, latest, loading, mount, notice, roleIcon, select, toast } from "../ui.js";
import { buildCard, FLAG_CHIP, gameMetrics, goldCharts, jungleGame, scorePill } from "./coach.js";

const PREFS = "tempu.repeticiones.v1";
let prefs = loadJSON(PREFS, { queue: "all", role: "all", result: "all", sort: "recent", sev: ["high", "medium", "low", "good", "info"], kind: "all" });
// Preferencias guardadas antes de existir «Informativo»: si se veía todo, se sigue viendo todo.
if (["high", "medium", "low", "good"].every((x) => prefs.sev?.includes(x)) && !prefs.sev.includes("info")) prefs.sev = [...prefs.sev, "info"];
let root = null;
let player = null;
let clientTimer = null;
let clientOpen = null; // null = aún no se sabe; false = el cliente de LoL no está abierto aquí
let learnCat = null; // «Aprende esto»: catálogo de vídeos y ejercicios por tema

const KIND_LABEL = {
  death: "Muertes",
  objective: "Objetivos",
  trade: "Cambios de mapa",
  gold: "Oro sin gastar",
  build: "Build",
  jungle: "Jungla",
  afk: "Desconexión",
  lane: "Fase de líneas",
  idle: "Tiempo vacío",
  vision: "Visión",
};
const TAG_LABEL = {
  gank: ["Gank", "bad"],
  jungler: ["Jungla rival", "bad"],
  solo: ["1 contra 1", ""],
  overextended: ["Adelantado", "bad"],
  no_ward: ["Sin ward 2 min", "bad"],
  unspent_gold: ["Oro sin gastar", "bad"],
  before_objective: ["Antes de un objetivo", "bad"],
  enemy_jungle: ["En su jungla", "bad"],
  isolated: ["Aislado", "bad"],
  executed: ["Torre o súbditos", ""],
  fight_won: ["Pelea ganada", "good"],
  fight_even: ["Pelea igualada", ""],
  traded: ["Tu equipo sacó algo", "good"],
  game_end: ["Empujón final", "good"],
  far: ["Lejos del objetivo", ""],
  afk: ["Antes de una desconexión", ""],
  fair_trade: ["Cambio justo", ""],
  fight_outnumbered: ["Con uno menos", "bad"],
  collapsed: ["Cazado en su mitad", "bad"],
  baron_buff: ["Con el barón", "bad"],
  bounty: ["Recompensa regalada", "bad"],
  duel_behind: ["Duelo en desventaja", "bad"],
  no_prio: ["Sin prioridad", ""],
  late_recall: ["Base a destiempo", "bad"],
  late: ["Llegada tarde", "bad"],
  unconverted: ["Ventaja sin convertir", "bad"],
  baron_wasted: ["Barón sin aprovechar", "bad"],
  pink: ["Pink sin usar", ""],
  team: ["De equipo", ""],
  // Frente a los OTPs de tu campeón (Diamante o más) y de partida entera: los que más separan elos.
  skill_order: ["Orden de habilidades", "bad"],
  first_back: ["Primera compra", "bad"],
  item_tempo: ["Ritmo de objetos", "bad"],
  slow_clear: ["Despeje lento", "bad"],
  no_pinks: ["Sin pinks", "bad"],
  few_wards: ["Pocos wards", "bad"],
  no_sweep: ["Sin quitar wards", "bad"],
  low_kp: ["Poca participación", "bad"],
};
// Consejos genéricos según lo que se detectó (no afirman nada que el análisis no haya visto).
const TIPS = [
  [["gank", "no_ward"], "Sin visión no ves venir al jungla: pon un ward en el río o en el arbusto lateral antes de adelantarte, sobre todo cuando no sabes dónde está."],
  [["jungler"], "Cuando no sepas dónde está el jungla rival, juega como si estuviera cerca de tu línea: no te pases de la mitad sin visión."],
  [["overextended"], "Estabas más cerca de su torre que de la tuya. Si tu oleada no está empujando, colócate detrás de ella o vuelve hacia tu torre."],
  [["no_ward"], "Usa el trinket o un ward de control antes de pelear o de empujar: casi todas las muertes evitables empiezan sin visión."],
  [["unspent_gold"], "Moriste con oro para una pieza de tu build. Vuelve a base en cuanto te llegue (y la oleada lo permita): peleabas con menos estadísticas de las que podías tener."],
  [["fight_outnumbered"], "Con un aliado muerto desde antes de la pelea, el objetivo se pelea con uno menos: cédelo o cámbialo al otro lado del mapa (su jungla, una torre) y espera a estar completos."],
  [["before_objective"], "Tu muerte dejó al equipo en inferioridad justo antes de un objetivo que cogió el rival. Antes de un dragón o barón, prioriza no morir y llegar a tiempo."],
  [["collapsed"], "Al otro lado del río y sin aliados cerca, en cuanto desaparecen varios rivales del mapa vienen a por ti: farmea esa línea solo con visión y retírate antes."],
  [["baron_buff"], "Con la mejora del barón, morir solo regala su ventaja: asedia con tu equipo o presiona una línea que los demás puedan cubrir."],
  [["duel_behind"], "El rival te sacaba nivel, un objeto o la definitiva: en esa ventana no aceptes el 1 contra 1. Retrocede, farmea seguro y pelea cuando subas tú antes de nivel o cierres tu objeto."],
  [["enemy_jungle"], "En la jungla rival entra solo con visión y sabiendo dónde están sus jugadores."],
  [["isolated"], "Lejos de tu equipo y sin nadie cerca: a partir de la fase media, separarte solo tiene sentido si presionas una línea con visión o si tu equipo aprovecha al otro lado."],
  [["solo"], "Revisa el intercambio: ¿tenías ventaja de nivel, de objetos o de oleada? Si no, evita el 1 contra 1."],
  [["executed"], "Sal antes de quedarte sin vida bajo la torre o entre súbditos."],
];

/** Con el visor abierto no se recarga sola la vista: reiniciaría la reproducción y la revisión guiada. */
export function skipSoftReload() {
  return !!player;
}

export function destroy() {
  root = null;
  player?.destroy();
  player = null;
  clearInterval(clientTimer);
}

export async function render(container, args = []) {
  destroy();
  root = container;
  if (!learnCat) learnCatalog().then((cat) => (learnCat = cat));
  if (args[0]) return renderViewer(container, args[0].toUpperCase(), Number(args[1]) || null);
  return renderList(container);
}

function setPrefs(patch) {
  prefs = { ...prefs, ...patch };
  save(PREFS, prefs);
}

// ================================================================== lista
async function renderList(container) {
  const body = h("div");
  mount(
    container,
    h(
      "div.section-head",
      h("div", h("h2", "Tus repeticiones"), h("p", "Cada partida reconstruida en 2D con el timeline de Riot y tus errores marcados en la línea de tiempo: pulsa una para verla, error a error.")),
    ),
    h("div", { style: { marginBottom: "14px" } }, roflSection()),
    body,
  );
  mount(body, loading("Cargando tus partidas…"));
  let profile;
  try {
    profile = await api.get("/api/profile");
  } catch (err) {
    return mount(body, errorBox(err));
  }
  if (!root) return;
  if (!profile.profile) {
    return mount(
      body,
      h("section.card", empty("Primero guarda tu perfil", "En «Mi progreso» escribe tu Riot ID (o detéctalo del cliente). Tus partidas se descargarán y analizarán solas y aparecerán aquí.", "user"), h("div.center", h("a.btn.btn-primary", { href: "#/progreso" }, icon("target"), "Ir a Mi progreso"))),
    );
  }
  await loadList(body, profile);
}

async function loadList(body, profile) {
  let data;
  let local;
  try {
    [data, local] = await Promise.all([api.get("/api/coach/games", { queue: prefs.queue, role: prefs.role, limit: 300 }), localReplayIds()]);
  } catch (err) {
    return mount(body, errorBox(err));
  }
  if (!root) return;
  let games = data.games || [];
  const roles = [...new Set(games.map((g) => g.role).filter(Boolean))];
  if (prefs.result !== "all") games = games.filter((g) => (prefs.result === "win" ? g.win : !g.win));
  const issues = (g) => (g.issues?.high || 0) * 3 + (g.issues?.medium || 0);
  if (prefs.sort === "errors") games = [...games].sort((x, y) => issues(y) - issues(x) || y.game_start - x.game_start);
  else if (prefs.sort === "score") games = [...games].sort((x, y) => (x.score ?? 99) - (y.score ?? 99));

  const refresh = (patch) => {
    setPrefs(patch);
    mount(body, loading("Filtrando…"));
    loadList(body, profile);
  };
  const filters = h(
    "div.row-wrap.coach-filters",
    tabs([{ value: "all", label: "Todas" }, { value: "normal", label: "Normales" }, { value: "ranked", label: "Clasificatorias" }], prefs.queue, (v) => refresh({ queue: v })),
    select([{ value: "all", label: "Todos los roles" }, ...roles.map((r) => ({ value: r, label: ROLE_LABEL[r] }))], prefs.role, (v) => refresh({ role: v }), "select select-sm"),
    select([{ value: "all", label: "Victorias y derrotas" }, { value: "win", label: "Solo victorias" }, { value: "loss", label: "Solo derrotas" }], prefs.result, (v) => refresh({ result: v }), "select select-sm"),
    select([{ value: "recent", label: "Más recientes" }, { value: "errors", label: "Más errores graves" }, { value: "score", label: "Peor nota primero" }], prefs.sort, (v) => refresh({ sort: v }), "select select-sm"),
    h("span.spacer"),
    h("span.tiny.muted", `${games.length} partidas`),
  );
  if (!games.length) {
    const t = profile.tracker || {};
    const linked = profile.profile?.linked;
    const [title, text] = !linked
      ? ["Esperando tu API key de Riot", "En cuanto haya una key válida en «Datos y API» se vincula tu cuenta y llegan tus partidas solas."]
      : t.status === "syncing"
        ? ["Descargando tus partidas…", "La primera vez se descargan tus últimas partidas con su timeline. Irán apareciendo aquí."]
        : ["No hay partidas con estos filtros", "Cambia la cola, el rol o el resultado."];
    return mount(body, h("div.stack", filters, h("section.card", empty(title, text, "film"))));
  }
  // 20 a la vista; el resto, de 20 en 20 al pulsar «Ver más».
  const list = h("div.replay-list");
  let shown = 0;
  const more = h("button.btn.btn-sm", { type: "button" });
  const showMore = () => {
    games.slice(shown, shown + 20).forEach((g) => list.append(gameCard(g, local.has(g.match_id))));
    shown = Math.min(games.length, shown + 20);
    more.hidden = shown >= games.length;
    more.textContent = `Ver más (${games.length - shown})`;
  };
  more.onclick = showMore;
  showMore();
  mount(body, h("div.stack", filters, list, h("div.row", { style: { justifyContent: "center" } }, more)));
}

function tabs(items, value, onChange) {
  return h("div.tabs", items.map((it) => h(`button.tab${it.value === value ? ".active" : ""}`, { onclick: () => onChange(it.value) }, it.label)));
}

// Con texto (no solo color): en móvil no hay tooltip que explique qué es cada número.
const SEV_SHORT = { high: ["grave", "graves"], medium: ["a mejorar", "a mejorar"], low: ["leve", "leves"], good: ["acierto", "aciertos"] };

function sevCount(n, sev) {
  if (!n) return null;
  const [one, many] = SEV_SHORT[sev] || ["", ""];
  return h(`span.sev-count.sev-${sev}`, { title: SEV_LABEL[sev] }, `${n} ${n === 1 ? one : many}`);
}

function gameCard(g, hasRofl = false) {
  const i = g.issues || {};
  return h(
    `a.replay-card.${g.win ? "win" : "loss"}`,
    { href: `#/repeticiones/${g.match_id}` },
    h("div.bar"),
    h("div.row", { style: { minWidth: 0 } }, champImg(g.champ), h("div", { style: { minWidth: 0 } }, h("div.ellipsis", { style: { fontWeight: 700 } }, dd.name(g.champ)), h("div.tiny.muted.ellipsis", `${g.role ? ROLE_LABEL[g.role] : "—"} · ${g.queue_label}`))),
    h("div", h("div.kda", `${g.kills ?? "—"} / ${g.deaths ?? "—"} / ${g.assists ?? "—"}`), h("div.tiny", { class: g.win ? "good" : "bad" }, g.win ? "Victoria" : "Derrota", h("span.muted", ` · ${fmt.duration(g.duration)}`))),
    g.opponent_champ ? h("div.row.tiny.muted.hide-sm", "vs ", champImg(g.opponent_champ, "xs"), h("span.ellipsis", dd.name(g.opponent_champ))) : h("div.hide-sm"),
    h(
      "div.row-wrap.replay-issues",
      g.has_timeline ? [sevCount(i.high, "high"), sevCount(i.medium, "medium"), sevCount(i.low, "low"), sevCount(i.good, "good")] : h("span.chip-muted", "esperando timeline"),
      [...new Set(g.flags || [])].filter((f) => FLAG_CHIP[f]).map((f) => h("span.chip-bad", { title: "Frente a los OTPs de tu campeón (Diamante o más): de lo que separa elos" }, FLAG_CHIP[f])),
      g.has_timeline && !i.high && !i.medium ? h("span.tiny.good", "sin errores importantes") : null,
      hasRofl ? h("span.chip-good", { title: "La repetición (.rofl) está en tu ordenador: se abre en el cliente sin descargarla" }, ".rofl en tu PC") : null,
    ),
    h("div.right", scorePill(g.score), h("div.tiny.muted", fmt.ago(g.game_start))),
    h("span.replay-go", icon("play")),
  );
}

// ================================================================== visor
async function renderViewer(container, matchId, startAt = null) {
  const fresh = latest("replays.viewer");
  const body = h("div");
  mount(container, h("div.row", { style: { marginBottom: "12px" } }, h("a.btn.btn-ghost.btn-sm", { href: "#/repeticiones" }, "← Tus repeticiones"), h("span.spacer"), h("a.btn.btn-ghost.btn-sm", { href: "#/progreso" }, icon("target"), "Mi progreso")), body);
  mount(body, loading("Cargando la repetición…"));
  let a;
  try {
    a = await api.get(`/api/coach/games/${matchId}`);
  } catch (err) {
    return mount(body, errorBox(err));
  }
  if (!root || !fresh()) return; // otra partida abierta mientras cargaba esta
  document.title = `${dd.name(a.champ)} · Repetición · Tempu Drafter`;
  clientOpen = null;
  api
    .get(`/api/replay/${a.match_id}/client`)
    .then((st) => (clientOpen = !!st.client_open))
    .catch(() => (clientOpen = null));
  mount(body, viewer(a, startAt));
}

function headCard(a) {
  const byPid = Object.fromEntries((a.players || []).map((p) => [p.pid, p]));
  const opp = byPid[a.opponent];
  const ej = byPid[a.enemy_jungler];
  const m = a.metrics || {};
  const counts = { high: 0, medium: 0, low: 0, good: 0 };
  for (const mo of a.moments || []) counts[mo.severity] = (counts[mo.severity] || 0) + 1;
  return h(
    "section.card",
    h(
      "div.profile-head.replay-head",
      champImg(a.champ, "xl"),
      h(
        "div",
        { style: { minWidth: 0 } },
        h("div", { style: { fontSize: "22px", fontWeight: 800 } }, dd.name(a.champ), " ", a.win ? h("span.pill.pill-good", "Victoria") : h("span.pill.pill-bad", "Derrota")),
        h("div.row-wrap.small.muted", a.role ? h("span.row", roleIcon(a.role), ROLE_LABEL[a.role]) : null, h("span", a.queue_label), h("span", fmt.duration(a.duration)), h("span", fmt.date(a.game_start)), h("span", `Parche ${a.patch}`)),
        h(
          "div.row-wrap.small",
          { style: { marginTop: "6px" } },
          opp ? h("span.row", "Contra ", champImg(opp.champ, "xs"), h("b", dd.name(opp.champ))) : null,
          ej && a.role !== "JUNGLE" ? h("span.row.muted", "· jungla rival ", champImg(ej.champ, "xs"), dd.name(ej.champ)) : null,
        ),
      ),
      h("div.spacer"),
      h(
        "div.kpis.replay-kpis",
        kpi("KDA", `${m.kills}/${m.deaths}/${m.assists}`, m.kp !== null && m.kp !== undefined ? `KP ${fmt.pct(m.kp, 0)}` : null),
        kpi("CS/min", fmt.num(m.cs_min, 1), m.cs10 !== undefined && m.cs10 !== null ? `CS@10 ${m.cs10}` : null),
        kpi("Errores", h("span", h("span.bad", String(counts.high)), " · ", h("span.warn", String(counts.medium))), "graves · a mejorar"),
        kpi("Nota", scorePill(a.score), `vs ${TIER_LABEL[a.target_tier] || a.target_tier}`),
      ),
    ),
  );
}

function viewer(a, startAt = null) {
  const head = headCard(a);
  if (!a.has_timeline) {
    return h(
      "div.stack",
      head,
      notice("Riot aún no ha publicado el timeline de esta partida (a veces tarda unos minutos o, en partidas antiguas, ya no está). Se vuelve a pedir solo; mientras tanto solo hay estadísticas generales.", "warn"),
      collapsible("rep.metricas", [icon("chart"), "Estadísticas de la partida"], () => gameMetrics(a), { open: true }),
    );
  }
  const all = [...(a.moments || [])].sort((x, y) => x.t - y.t);
  const kinds = [...new Set(all.map((m) => m.kind))];
  if (prefs.kind !== "all" && !kinds.includes(prefs.kind)) prefs.kind = "all";
  const visible = () => all.filter((m) => prefs.sev.includes(m.severity) && (prefs.kind === "all" || m.kind === prefs.kind));

  const detailEl = h("div.moment-detail");
  const listEl = h("div.moments-list");
  const filtersEl = h("div.moment-filters");
  let current = null;
  const items = new Map();

  const drawList = () => {
    const list = visible();
    items.clear();
    if (!list.length) {
      mount(listEl, h("div.small.muted", { style: { padding: "12px 4px" } }, all.length ? "Ningún momento con estos filtros." : "Ningún error claro en esta partida. ¡Bien jugado!"));
      return;
    }
    mount(
      listEl,
      list.map((m) => {
        const item = h(
          `button.moment-item.sev-${m.severity}${m === current ? ".selected" : ""}`,
          { type: "button", onclick: () => player.select(m) },
          h("span.moment-time", fmt.duration(Math.max(1, m.t))),
          h("span.sev-dot.sev-" + m.severity),
          h("div", { style: { minWidth: 0, textAlign: "left" } }, h("div.small.moment-title", champText(m.title)), h("div.tiny.muted", `${KIND_LABEL[m.kind] || m.kind} · ${SEV_LABEL[m.severity] || ""}`)),
        );
        items.set(m, item);
        return item;
      }),
    );
  };

  const drawFilters = () => {
    const counts = {};
    for (const m of all) if (prefs.kind === "all" || m.kind === prefs.kind) counts[m.severity] = (counts[m.severity] || 0) + 1;
    const sevBtn = (sev) =>
      h(
        `button.chip.sev-chip.sev-${sev}${prefs.sev.includes(sev) ? ".on" : ""}`,
        {
          type: "button",
          "aria-pressed": prefs.sev.includes(sev) ? "true" : "false",
          onclick: () => {
            const next = prefs.sev.includes(sev) ? prefs.sev.filter((s) => s !== sev) : [...prefs.sev, sev];
            setPrefs({ sev: next });
            apply();
          },
        },
        h("span.sev-dot.sev-" + sev),
        `${SEV_LABEL[sev]} ${counts[sev] || 0}`,
      );
    mount(
      filtersEl,
      h("div.chips", ["high", "medium", "low", "good", "info"].map(sevBtn)),
      h(
        "div.row",
        select([{ value: "all", label: "Todas las categorías" }, ...kinds.map((k) => ({ value: k, label: KIND_LABEL[k] || k }))], prefs.kind, (v) => {
          setPrefs({ kind: v });
          apply();
        }, "select select-sm"),
        h("span.spacer"),
        h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => { setPrefs({ sev: ["high", "medium", "low", "good", "info"], kind: "all" }); apply(); } }, "Ver todo"),
      ),
    );
  };

  const apply = () => {
    drawFilters();
    drawList();
    player.setMoments(visible());
  };

  const onSelect = (m) => {
    current = m;
    for (const [mo, el] of items) el.classList.toggle("selected", mo === m);
    const el = items.get(m);
    if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    // En pantallas estrechas el mapa queda arriba: si no se ve, se lleva a la vista.
    const mapEl = player?.el?.querySelector(".replay-map");
    if (m && mapEl && window.innerWidth <= 1000) {
      const box = mapEl.getBoundingClientRect();
      if (box.bottom < 80 || box.top > window.innerHeight - 80) mapEl.scrollIntoView({ block: "start", behavior: "smooth" });
    }
    mount(detailEl, m ? momentDetail(a, m, player) : h("div.small.muted", "Pulsa un error de la lista o de la barra para verlo en el mapa."));
  };
  const onTime = (t) => {
    for (const [m, el] of items) el.classList.toggle("passed", m.t <= t);
  };

  player = createReplay(a, { moments: visible(), onSelect, onTime });
  const goTo = (m) => {
    const mo = all.find((x) => x.kind === m.kind && Math.abs(x.t - m.t) < 0.5);
    if (mo && visible().includes(mo)) player.select(mo);
    else player.seek(Math.max(0, m.t - 5));
    player.el?.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  drawFilters();
  drawList();
  if (startAt !== null) {
    // Viene de «Errores que repites»: directo a ese error (o a unos segundos antes de ese minuto).
    const near = all.find((m) => Math.abs(m.t - startAt) <= 3);
    if (near && visible().includes(near)) player.select(near);
    else player.seek(Math.max(0, startAt - 5));
  }
  mount(detailEl, h("div.small.muted", "Pulsa ▶ para reproducir: se para sola en cada error. También puedes pulsar un error de la lista o de la barra."));

  const afk = (a.afk || []).reduce((acc, x) => acc + (x.end - x.start), 0);
  const afkNotice = afk >= 60 ? notice(`Estuviste ${fmt.duration(afk)} sin moverte (desconexión o AFK). Esos minutos no cuentan como minutos vacíos ni como oro sin gastar.`, "warn") : null;
  const reliableNotice = a.reliable === false
    ? notice(a.queue === 480 ? "Partida de Swiftplay: empieza con más nivel y oro, así que no se compara con tus referencias ni cuenta en tus estadísticas (los errores se muestran igual)." : "Alguien estuvo desconectado 3 minutos o más: esta partida no cuenta en tus estadísticas ni en tu nota.", "info")
    : null;
  const itemsNotice = a.items_reliable === false
    ? notice("Esta partida tiene objetos que no están en el catálogo actual (otra temporada o cambios de objetos): el análisis de oro y build se omite para no darte avisos falsos.", "info")
    : null;
  const panel = h(
    "section.card.moments-panel",
    h("h3.card-title", icon("flag"), "Errores y aciertos", h("span.spacer"), h("span.tiny.muted", `${all.length} momentos`)),
    filtersEl,
    detailEl,
    listEl,
  );
  return h(
    "div.stack",
    head,
    afkNotice,
    reliableNotice,
    itemsNotice,
    h("div.grid.replay-main", h("section.card.replay-card-main", h("h3.card-title", icon("play"), "Repetición 2D", h("span.spacer"), h("span.tiny.muted", "Reconstruida con el timeline de Riot")), player.el), panel),
    clientReplayCard(a),
    collapsible("rep.revision", [icon("edit"), "Revisión guiada: 3 momentos y 3 preguntas"], () => reviewCard(a, goTo), { summary: "método de Mauro Garih · se guarda solo" }),
    a.jungle ? collapsible("rep.jungla.v2", [icon("map"), "Tu ruta de jungla"], () => jungleGame(a), { summary: "campamentos y ganks" }) : null,
    collapsible("rep.build", [icon("crown"), "Tu build"], () => buildCard(a) || h("div.small.muted", "Sin datos de build."), { summary: "orden, compras y comparación con OP.GG" }),
    collapsible("rep.oro", [icon("activity"), "Oro y ventaja"], () => goldCharts(a), { summary: "frente a tu rival y oro sin gastar" }),
    collapsible("rep.metricas", [icon("chart"), `Todas las estadísticas frente a ${TIER_LABEL[a.target_tier] || a.target_tier}`], () => gameMetrics(a), { summary: "por categorías" }),
  );
}

// ------------------------------------------------------------ revisión guiada
function reviewCard(a, goTo) {
  const box = h("div.review", loading("Preparando la revisión…"));
  api
    .get(`/api/coach/games/${a.match_id}/review`)
    .then((r) => {
      if (!r.moments?.length) return mount(box, h("div.small.muted", "En esta partida no hay errores que revisar."));
      const answers = { ...(r.answers || {}) };
      const total = r.moments.length * r.questions.length;
      const progress = h("span.tiny.muted");
      const count = () => (progress.textContent = `${Object.values(answers).filter((v) => v && v.trim()).length} de ${total} respuestas`);
      count();
      mount(
        box,
        h(
          "div.row-wrap",
          h("p.tiny.muted", { style: { margin: 0, flex: "1 1 260px" } }, "Para cada momento, responde a las 3 preguntas antes de ver la solución del análisis. Lo que escribas se guarda solo en tu base de datos local."),
          progress,
        ),
        r.moments.map((m) =>
          h(
            "div.review-moment",
            h(
              "div.row-wrap",
              h(`span.sev.${m.severity}`, SEV_LABEL[m.severity] || ""),
              h("span.mono.small", fmt.duration(Math.max(1, m.t))),
              h("b.small", { style: { flex: "1 1 200px", minWidth: 0 } }, champText(m.title)),
              h("button.btn.btn-sm.btn-ghost", { type: "button", onclick: () => goTo(m) }, icon("play"), "Ver en el mapa"),
            ),
            r.questions.map((q) => {
              const key = `${Number(m.t).toFixed(1)}|${q.id}`;
              const status = h("span.tiny.muted.review-status");
              const save = debounce(async (value) => {
                try {
                  await api.put(`/api/coach/games/${a.match_id}/review`, { t: m.t, question: q.id, answer: value });
                  status.textContent = value.trim() ? "Guardado" : "";
                } catch (err) {
                  status.textContent = "No se pudo guardar";
                  toast(err.message, "error");
                }
              }, 800);
              const area = h("textarea.review-answer", {
                rows: 2,
                maxlength: 2000,
                placeholder: q.hint,
                oninput: (e) => {
                  answers[key] = e.target.value;
                  count();
                  status.textContent = "Guardando…";
                  save(e.target.value);
                },
              });
              area.value = answers[key] || "";
              return h("label.review-q", h("span.small", q.label, status), area);
            }),
          ),
        ),
      );
    })
    .catch((err) => mount(box, errorBox(err)));
  return box;
}

// ------------------------------------------------------------ detalle
function nearby(a, m) {
  const r = a.replay;
  if (!r || m.x === null || m.x === undefined) return null;
  const players = a.players || [];
  const me = players.find((p) => p.pid === a.me);
  const close = { allies: [], enemies: [] };
  for (const p of players) {
    if (p.pid === a.me) continue;
    const pos = trackPos(r.tracks[String(p.pid)] || { a: [], dead: [] }, m.t);
    if (!pos || pos.dead || pos.x === null || pos.x === undefined) continue;
    const d = Math.hypot(pos.x - m.x, pos.y - m.y);
    if (d <= 2500) (p.team === me?.team ? close.allies : close.enemies).push(p);
  }
  return close;
}

/** Valor del último minuto cerrado antes de t (nivel u oro total de un jugador). */
function frameAt(a, key, pid, t) {
  const f = a.replay?.frames;
  const arr = f?.[key]?.[String(pid)];
  if (!f?.t?.length || !arr) return null;
  let i = 0;
  while (i < f.t.length - 1 && f.t[i + 1] <= t) i++;
  return arr[i] ?? null;
}

function levelsLine(a, death, players) {
  if (!a.replay?.frames || !death) return null;
  const me = { pid: a.me, champ: players[a.me]?.champ };
  const myLevel = frameAt(a, "level", a.me, death.t);
  const myGold = frameAt(a, "gold", a.me, death.t);
  if (myLevel === null) return null;
  const rivals = (death.enemies || []).map((pid) => ({ pid, champ: players[pid]?.champ, level: frameAt(a, "level", pid, death.t), gold: frameAt(a, "gold", pid, death.t) }));
  const worse = rivals.filter((r) => r.level !== null && r.level - myLevel >= 2);
  const k = (v) => (v === null || v === undefined ? "—" : `${fmt.num(v / 1000, 1)}k`);
  return h(
    "div.small",
    { style: { marginTop: "6px" } },
    h("span.muted", "Nivel y oro (al cierre del minuto anterior): "),
    h("b", `tú nivel ${myLevel} · ${k(myGold)}`),
    rivals.map((r) => h(`span${r.level !== null && r.level - myLevel >= 2 ? ".bad" : ""}`, ` · ${dd.name(r.champ)} nivel ${r.level ?? "—"} · ${k(r.gold)}`)),
    worse.length ? h("div.tiny.bad", "Peleaste con 2 o más niveles de desventaja.") : null,
  );
}

const ELO_NAMES = { BRONZE: "Hierro-Bronce", GOLD: "Plata-Oro", PLATINUM: "Platino", EMERALD: "Esmeralda", DIAMOND: "Diamante", MASTER: "Maestro", CHALLENGER: "Aspirante" };

/** «Partidas con este error: Platino 60 % · … · Aspirante 47 %» y si es de lo que separa elos. */
function eloLine(m) {
  const e = m.elo;
  const tiers = Object.entries(e?.tiers || {});
  if (!tiers.length) return null;
  return h(
    "div.tiny.muted.elo-moment",
    h("span", `Partidas ${e.scope === "champ" ? "de los OTPs de tu campeón" : "de tu rol"} con este error: `),
    tiers.map(([t, pct], i) => [i ? " · " : "", h("b", `${ELO_NAMES[t] || t} ${Math.round(pct)} %`)]),
    e.rises
      ? h("div", "Los mejores lo hacen más (juegan con más presión): no es lo que te separa de subir.")
      : e.separates === false
      ? h("div", "Lo cometen igual en todos los elos: cuenta, pero no es lo que te separa de subir.")
      : e.separates
        ? h("div.bad", "Los mejores lo cometen mucho menos: es de lo que separa elos.")
        : null,
  );
}

function tipFor(m) {
  if (m.severity === "good") return "Bien hecho: repite esta decisión.";
  if (m.kind === "afk") return "No cuenta como error de juego: es tiempo sin moverte (desconexión o AFK).";
  if (m.kind !== "death") return null;
  const tags = new Set(m.tags || []);
  if (tags.has("game_end")) return "Morir en el empujón final que gana la partida no es un error.";
  if (tags.has("fight_won")) return "Caíste, pero tu equipo ganó la pelea: suele ser un buen intercambio.";
  const tip = TIPS.find(([need]) => need.every((t) => tags.has(t)));
  if (!tip) return tags.has("traded") ? "Tu equipo sacó algo a cambio, así que esta muerte pesa menos." : null;
  return tags.has("traded") ? `Tu equipo sacó algo a cambio, así que pesa menos. Aun así: ${tip[1].charAt(0).toLowerCase()}${tip[1].slice(1)}` : tip[1];
}

function momentDetail(a, m, player) {
  const players = Object.fromEntries((a.players || []).map((p) => [p.pid, p]));
  const death = m.kind === "death" ? (a.deaths || []).find((d) => Math.abs(d.t - m.t) < 1) : null;
  const near = nearby(a, m);
  const tip = tipFor(m);
  const tags = (m.tags || []).filter((t) => TAG_LABEL[t]);
  return h(
    `div.moment-card.sev-${m.severity}`,
    h("div.row-wrap", h(`span.sev.${m.severity}`, SEV_LABEL[m.severity] || ""), h("span.pill", KIND_LABEL[m.kind] || m.kind), h("span.mono.small", fmt.duration(Math.max(1, m.t)))),
    h("div", { style: { fontWeight: 700, marginTop: "6px" } }, champText(m.title)),
    m.detail ? h("div.small", { style: { marginTop: "4px" } }, champText(m.detail)) : null,
    tip ? h("div.weak-tip", { style: { marginTop: "10px" } }, icon("spark"), h("span", tip)) : null,
    eloLine(m),
    // Lo esencial (qué pasó y qué hacer) a la vista; quién te mató, niveles, quién había cerca y vídeos, al pulsar.
    h(
      "details.more",
      { style: { marginTop: "8px" } },
      h("summary.btn.btn-ghost.btn-sm", "Más contexto"),
      h(
        "div.col",
        { style: { gap: "6px", marginTop: "6px" } },
          tags.length ? h("div.row-wrap", { style: { marginTop: "8px", gap: "4px" } }, tags.map((t) => h(`span.tag-chip.${TAG_LABEL[t][1] || "neutral"}`, TAG_LABEL[t][0]))) : null,
          death
            ? h(
                "div.row-wrap.small",
                { style: { marginTop: "8px" } },
                h("span.muted", "Te mataron: "),
                death.enemies?.length ? death.enemies.map((pid) => h("span.row", { style: { gap: "4px" } }, champImg(players[pid]?.champ, "xs"), dd.name(players[pid]?.champ))) : h("span", "torre o súbditos"),
                death.fight?.size > 1 ? h("span.muted", ` · pelea: ${death.fight.enemies} rivales caídos, ${death.fight.allies} aliados caídos`) : null,
              )
            : null,
          levelsLine(a, death, players),
          near && (near.allies.length || near.enemies.length || m.kind === "death")
            ? h(
                "div.small.muted",
                { style: { marginTop: "6px" } },
                `Cerca en ese momento (≈2.500 u., posiciones aproximadas): ${near.allies.length} ${near.allies.length === 1 ? "aliado" : "aliados"}`,
                near.allies.length ? ` (${near.allies.map((p) => dd.name(p.champ)).join(", ")})` : "",
                ` y ${near.enemies.length} ${near.enemies.length === 1 ? "rival" : "rivales"}`,
                near.enemies.length ? ` (${near.enemies.map((p) => dd.name(p.champ)).join(", ")})` : "",
                ".",
              )
            : null,
          m.severity !== "good" && m.kind !== "afk" ? learnBox(learnForTags(learnCat, m.tags || [])) : null,
      ),
    ),
    h(
      "div.row-wrap",
      { style: { marginTop: "10px" } },
      h("button.btn.btn-sm", { type: "button", onclick: () => { player.select(m); player.play(); } }, icon("play"), "Ver desde 8 s antes"),
      h(
        "button.btn.btn-sm.btn-ghost",
        {
          type: "button",
          disabled: clientOpen === false || null,
          onclick: () => watchInClient(a, m.t),
          title: clientOpen === false ? "Abre el cliente de League of Legends en este ordenador para ver la repetición real" : "Abre la repetición real en el cliente de LoL y salta a este momento",
        },
        icon("monitor"),
        "Ver en el cliente de LoL",
      ),
    ),
  );
}

// ------------------------------------------------------- repetición real
const CLIENT_STATE = {
  no_client: ["El cliente de LoL no está abierto en este ordenador", "warn"],
  checking: ["Comprobando si la repetición está disponible…", ""],
  download: ["Disponible para descargar", ""],
  downloading: ["Descargando…", ""],
  watch: ["Descargada: lista para ver", "good"],
  incompatible: ["De otro parche: el cliente ya no puede reproducirla", "warn"],
  missingOrExpired: ["Riot ya no la tiene (caducada)", "warn"],
  lost: ["Riot ya no la tiene", "warn"],
  error: ["El cliente dio un error con esta repetición", "bad"],
  retryDownload: ["Falló la descarga: se puede reintentar", "warn"],
  unsupported: ["El cliente no permite repeticiones de este modo", "warn"],
};

function clientReplayCard(a) {
  const status = h("div");
  const card = collapsible(
    "rep.cliente",
    [icon("monitor"), "Repetición real en el cliente de LoL"],
    h(
      "div.col",
      h("p.small.muted", { style: { margin: 0 } }, "Con el cliente de LoL abierto en este ordenador puedes abrir la repetición de verdad (.rofl) y saltar al momento exacto de cada error con el botón «Ver en el cliente de LoL». Solo existen para partidas del parche actual y Riot limita las descargas (50 al día), así que solo se descarga la que pidas."),
      status,
    ),
    { summary: "abrir y saltar al momento exacto" },
  );
  const load = async () => {
    if (!card.isConnected || !card.open) return;
    try {
      const s = await api.get(`/api/replay/${a.match_id}/client`);
      renderClientStatus(status, a, s);
    } catch (err) {
      mount(status, errorBox(err));
    }
  };
  card.addEventListener("toggle", load);
  clearInterval(clientTimer);
  clientTimer = setInterval(load, 4000);
  setTimeout(load, 50);
  return card;
}

function renderClientStatus(el, a, s) {
  clientOpen = !!s.client_open;
  const [label, tone] = CLIENT_STATE[s.state] || [s.state || "Desconocido", ""];
  const job = s.job && s.job.match_id === a.match_id ? s.job : null;
  mount(
    el,
    h("div.row-wrap", h(`span.pill.pill-dot${tone === "good" ? ".pill-good" : tone === "warn" ? ".pill-warn" : tone === "bad" ? ".pill-bad" : ""}`, label), s.progress ? h("span.small.muted", `${Math.round(s.progress)} %`) : null),
    s.detail ? h("div.small.muted", s.detail) : null,
    job ? h("div.small", { style: { marginTop: "6px" } }, job.message) : null,
    s.client_open
      ? h(
          "div.row-wrap",
          { style: { marginTop: "8px" } },
          s.state === "download" || s.state === "retryDownload"
            ? h("button.btn.btn-sm", { type: "button", onclick: () => clientAction(a, "download") }, icon("download"), "Descargar repetición")
            : null,
          ["download", "retryDownload", "watch", "downloading"].includes(s.state)
            ? h("button.btn.btn-sm.btn-primary", { type: "button", onclick: () => watchInClient(a, player?.time() ?? 0) }, icon("play"), "Abrir en el momento actual")
            : null,
        )
      : null,
    s.client_open && s.replay_api === false
      ? h(
          "div.notice.notice-info",
          { style: { marginTop: "10px" } },
          icon("info"),
          h(
            "div",
            "Para saltar solo al segundo exacto, el juego necesita la opción «EnableReplayApi=1» en su archivo de configuración (game.cfg). Sin ella la repetición se abre igual, pero tendrás que buscar el minuto a mano.",
            h("div", { style: { marginTop: "8px" } }, h("button.btn.btn-sm", { type: "button", onclick: enableReplayApi }, "Activar el salto automático"), h("span.tiny.muted", " (se guarda una copia de game.cfg; se aplica al abrir la siguiente repetición)")),
          ),
        )
      : null,
  );
}

async function clientAction(a, action) {
  try {
    const res = await api.post(`/api/replay/${a.match_id}/client/${action}`);
    toast(res.message || "Hecho", "success");
  } catch (err) {
    toast(err.message, "error", 8000);
  }
}

async function watchInClient(a, t) {
  try {
    const res = await api.post(`/api/replay/${a.match_id}/client/watch`, { t: Math.max(0, Math.round(t - 10)) });
    toast(res.message || "Abriendo la repetición en el cliente…", "success", 7000);
  } catch (err) {
    toast(err.message, "error", 9000);
  }
}

async function enableReplayApi() {
  if (!confirm("¿Activar EnableReplayApi=1 en el game.cfg de League of Legends? Se guarda una copia del archivo original.")) return;
  try {
    const res = await api.post("/api/replay/client/enable-api");
    toast(res.message || "Activado", "success", 7000);
  } catch (err) {
    toast(err.message, "error", 9000);
  }
}
