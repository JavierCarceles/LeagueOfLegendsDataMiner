// OTPs analizados: ranking de campeones, reparto por elo/servidor/rol y la lista filtrable; el detalle, al pulsar.
//
// #/otps            biblioteca
// #/otps/{campeón}  con el campeón ya filtrado (id)
import { api } from "../api.js";
import { dd, loadJSON, platformLabel, ROLE_LABEL, save, TIER_LABEL } from "../state.js";
import { champImg, collapsible, empty, errorBox, fmt, h, icon, kpi, latest, loading, modal, mount, roleIcon, select, wrClass } from "../ui.js";

const PREFS = "tempu.otps.v1";
const PAGE = 30;
const TIER_ORDER = ["CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND", "EMERALD", "PLATINUM", "GOLD", "SILVER", "BRONZE", "IRON"];
const SORTS = [
  { value: "games", label: "Más partidas" },
  { value: "elo", label: "Más elo" },
  { value: "wr", label: "Mejor winrate (≥ 10)" },
  { value: "recent", label: "Jugado más reciente" },
  { value: "name", label: "Nombre" },
];

let root = null;
let lib = null;
let filters = loadJSON(PREFS, { champ: "", role: "", tier: "", platform: "", q: "", sort: "games" });
let listBox = null;
let champSelect = null; // se actualiza al pulsar un campeón del ranking
let countEl = null;
let moreBtn = null;
let loaded = [];

export function destroy() {
  root = null;
}

export async function render(container, args = []) {
  root = container;
  if (args[0] && /^\d+$/.test(args[0])) filters = { ...filters, champ: args[0] };
  mount(root, loading("Cargando los OTPs analizados…"));
  try {
    lib = await api.get("/api/otps/library");
  } catch (err) {
    return mount(root, errorBox(err));
  }
  if (!root) return;
  mount(root, view());
  loadList(true);
}

const tierName = (t) => TIER_LABEL[t] || (t === "?" ? "Sin elo" : t || "—");
const eloText = (p) => `${tierName(p.tier)}${p.division && !["MASTER", "GRANDMASTER", "CHALLENGER"].includes(p.tier) ? ` ${p.division}` : ""}${p.lp != null ? ` · ${fmt.int(p.lp)} LP` : ""}`;
const riotId = (p) => (p.game_name ? `${p.game_name}#${p.tag_line}` : "Nombre aún desconocido");

function setFilters(patch) {
  filters = { ...filters, ...patch };
  save(PREFS, filters);
  if (champSelect) champSelect.value = filters.champ;
  loadList(true);
}

// ------------------------------------------------------------------ vista
function view() {
  const t = lib.totals;
  if (!t.otps) {
    return h(
      "div",
      head(),
      h("section.card", empty("Aún no hay OTPs analizados", "En «Datos y API» elige campeones en el recolector («Buscar por campeones») y arráncalo: cada OTP que encuentre y sus partidas aparecerán aquí.", "crown"), h("div.center", h("a.btn.btn-primary", { href: "#/datos" }, icon("database"), "Ir a Datos y API"))),
    );
  }
  return h(
    "div.stack",
    head(),
    h("div.kpis", kpi("OTPs analizados", fmt.int(t.otps), `${fmt.int(t.players)} jugadores`), kpi("Partidas", fmt.int(t.games), "con micro y macro"), kpi("Campeones", fmt.int(t.champions)), kpi("Servidores", fmt.int(t.platforms))),
    rankingCard(),
    collapsible("otps.reparto", [icon("chart"), "Reparto por elo, servidor y rol"], () => distribution(), {
      summary: Object.entries(lib.tiers).slice(0, 3).map(([k, v]) => `${tierName(k)} ${fmt.int(v.otps)}`).join(" · "),
    }),
    listCard(),
  );
}

function head() {
  return h(
    "div.section-head",
    h("div", h("h2", "OTPs analizados"), h("p", "Jugadores que dominan un campeón y cuyas partidas se han analizado a fondo. Pulsa un campeón para ver solo sus OTPs, y un jugador para ver su detalle.")),
  );
}

/** Ranking de campeones: más OTPs primero; a igualdad, el de mejor elo. */
function rankingCard() {
  const all = lib.champions;
  const box = h("div.otp-rank-list");
  let expanded = false;
  const draw = () =>
    mount(
      box,
      (expanded ? all : all.slice(0, 8)).map((c) =>
        h(
          `button.otp-rank${String(c.champ) === String(filters.champ) ? ".on" : ""}`,
          { type: "button", onclick: () => { setFilters({ champ: String(c.champ) === String(filters.champ) ? "" : String(c.champ) }); draw(); } },
          h("span.otp-rank-n", `${c.rank}`),
          champImg(c.champ, "sm"),
          h("span.otp-rank-name", dd.name(c.champ)),
          h("span.otp-rank-stat", h("b", fmt.int(c.otps)), h("span.tiny.muted", c.otps === 1 ? " OTP" : " OTPs")),
          h("span.otp-rank-stat.hide-sm", h("span.tiny.muted", `${fmt.int(c.games)} partidas`)),
          h("span.chip-muted.hide-sm", { title: "Mejor elo entre sus OTPs" }, tierName(c.best_tier)),
        ),
      ),
      all.length > 8 ? h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => { expanded = !expanded; draw(); } }, expanded ? "Ver menos" : `Ver los ${all.length}`) : null,
    );
  draw();
  return h("section.card", h("h3.card-title", icon("crown"), "Campeones con más OTPs"), box);
}

function bars(entries, label) {
  const max = Math.max(1, ...entries.map(([, v]) => v.otps));
  return h(
    "div.col",
    { style: { gap: "6px" } },
    entries.map(([k, v]) =>
      h(
        "div.otp-bar",
        h("span.small", label(k)),
        h("div.track", h("span", { style: { width: `${(100 * v.otps) / max}%` } })),
        h("span.tiny.right", h("b", fmt.int(v.otps)), h("span.muted", ` · ${fmt.int(v.games)} p.`)),
      ),
    ),
  );
}

function distribution() {
  return h(
    "div.grid.otp-dist",
    h("div", h("div.small", { style: { fontWeight: 650, marginBottom: "8px" } }, "Por elo"), bars(Object.entries(lib.tiers), tierName)),
    h("div", h("div.small", { style: { fontWeight: 650, marginBottom: "8px" } }, "Por servidor"), bars(Object.entries(lib.platforms), platformLabel)),
    h("div", h("div.small", { style: { fontWeight: 650, marginBottom: "8px" } }, "Por rol"), bars(Object.entries(lib.roles), (r) => ROLE_LABEL[r] || "Sin rol")),
  );
}

function listCard() {
  listBox = h("div.otp-list");
  countEl = h("span.tiny.muted");
  moreBtn = h("button.btn.btn-sm", { type: "button", onclick: () => loadList(false) }, "Ver más");
  let searchTimer = null;
  const tiers = Object.keys(lib.tiers).sort((a, b) => TIER_ORDER.indexOf(a) - TIER_ORDER.indexOf(b));
  const bar = h(
    "div.otp-filters",
    h("input.input.input-sm", {
      type: "search", placeholder: "Buscar jugador…", value: filters.q || "", "aria-label": "Buscar jugador",
      oninput: (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => setFilters({ q: e.target.value }), 250); },
    }),
    (champSelect = select([{ value: "", label: "Todos los campeones" }, ...lib.champions.map((c) => ({ value: String(c.champ), label: `${dd.name(c.champ)} (${c.otps})` }))], filters.champ, (v) => setFilters({ champ: v }), "select select-sm")),
    select([{ value: "", label: "Todos los roles" }, ...Object.keys(lib.roles).map((r) => ({ value: r, label: ROLE_LABEL[r] || "Sin rol" }))], filters.role, (v) => setFilters({ role: v }), "select select-sm"),
    select([{ value: "", label: "Todos los elos" }, ...tiers.map((t) => ({ value: t, label: tierName(t) }))], filters.tier, (v) => setFilters({ tier: v }), "select select-sm"),
    select([{ value: "", label: "Todos los servidores" }, ...Object.keys(lib.platforms).map((p) => ({ value: p, label: platformLabel(p) }))], filters.platform, (v) => setFilters({ platform: v }), "select select-sm"),
    select(SORTS, filters.sort, (v) => setFilters({ sort: v }), "select select-sm"),
    countEl,
  );
  return h("section.card", h("h3.card-title", icon("user"), "Todos los OTPs"), h("div.col", { style: { gap: "12px" } }, bar, listBox, h("div.row", { style: { justifyContent: "center" } }, moreBtn)));
}

async function loadList(reset) {
  if (!listBox) return;
  if (reset) {
    loaded = [];
    mount(listBox, loading());
  }
  moreBtn.disabled = true;
  const fresh = latest("otps.list");
  let res;
  try {
    res = await api.get("/api/otps/players", {
      champ: filters.champ || undefined, role: filters.role || undefined, tier: filters.tier || undefined,
      platform: filters.platform || undefined, q: filters.q || undefined, sort: filters.sort, limit: PAGE, offset: loaded.length,
    });
  } catch (err) {
    return mount(listBox, errorBox(err));
  }
  if (!fresh() || !root) return;
  loaded = [...loaded, ...res.players];
  mount(listBox, loaded.length ? loaded.map(row) : h("div.small.muted", { style: { padding: "12px 0" } }, "Ningún OTP con estos filtros."));
  countEl.textContent = `${fmt.int(loaded.length)} de ${fmt.int(res.matched)}`;
  moreBtn.hidden = loaded.length >= res.matched;
  moreBtn.disabled = false;
}

function row(p) {
  return h(
    "button.otp-lib-row",
    { type: "button", onclick: () => openDetail(p) },
    champImg(p.champ, "sm"),
    h("div.otp-player-id", h("div.ellipsis", { style: { fontWeight: 650 } }, riotId(p)), h("div.tiny.muted", `${platformLabel(p.platform)} · ${eloText(p)}`)),
    h("div.otp-player-role", p.role && p.role !== "SIN_ROL" ? [roleIcon(p.role), h("span.tiny.muted", ROLE_LABEL[p.role])] : h("span.tiny.muted", "—")),
    h(
      "div.otp-player-games",
      h("div.small", h("b", fmt.int(p.games)), h("span.muted", p.games === 1 ? " partida" : " partidas"), h(`span.${wrClass(p.wr)}`, ` · ${fmt.pct(p.wr, 0)}`)),
      h("div.otp-wr", h("span", { style: { width: `${p.wr}%` } })),
    ),
    h("span.tiny.muted.hide-sm", fmt.ago(p.last)),
    h("span.replay-go", icon("chevron")),
  );
}

// ------------------------------------------------------------------ detalle de un OTP
const METRIC_LABEL = {
  kda: ["KDA", "higher", 2], deaths: ["Muertes", "lower", 1], kp: ["Participación en kills", "higher", 0, "%"], cs_min: ["CS por minuto", "higher", 2],
  cs10: ["CS a los 10", "higher", 0], gold_min: ["Oro por minuto", "higher", 0], vision_min: ["Visión por minuto", "higher", 2],
  control_wards: ["Pinks compradas", "higher", 1], obj_participation: ["Participación en objetivos", "higher", 0, "%"],
  ganks_pre10: ["Ganks con kill antes del 10", "higher", 2], impact_pre10: ["Jugadas con impacto antes del 10", "higher", 1], first_item_min: ["Minuto del primer objeto", "lower", 1],
};

async function openDetail(p) {
  const body = h("div", loading());
  modal(
    h("div.row", { style: { gap: "12px" } }, champImg(p.champ, "lg"), h("div", h("h3", riotId(p)), h("div.tiny.muted", `${dd.name(p.champ)} · ${ROLE_LABEL[p.role] || "sin rol"} · ${platformLabel(p.platform)} · ${eloText(p)}`))),
    body,
    { wide: true },
  );
  let d;
  try {
    d = await api.get(`/api/otps/players/${encodeURIComponent(p.puuid)}`, { champ: p.champ, role: p.role });
  } catch (err) {
    return mount(body, errorBox(err));
  }
  const status = (key, mine, ref) => {
    const [, better] = METRIC_LABEL[key] || [];
    if (ref == null || mine == null) return "";
    const gap = (mine - ref) / Math.max(Math.abs(ref), 0.5);
    if (Math.abs(gap) < 0.1) return "";
    return (gap > 0) === (better === "higher") ? "good" : "bad";
  };
  const b = d.build || {};
  mount(
    body,
    h(
      "div.col",
      { style: { gap: "14px" } },
      h("div.kpis", kpi("Partidas", fmt.int(d.games_count)), kpi("Winrate", fmt.pct(d.wr, 1), `${fmt.int(d.wins)} V · ${fmt.int(d.games_count - d.wins)} D`, wrClass(d.wr)), kpi("Elo", tierName(d.tier)), d.player?.game_name ? kpi("Perfil", h("a", { href: `#/jugador/${d.player.platform}/${encodeURIComponent(d.player.game_name)}/${encodeURIComponent(d.player.tag_line)}` }, "Ver →")) : null),
      b.first_items?.length || b.skill_max?.length
        ? h(
            "div.small",
            h("b", "Build: "),
            b.first_items?.length ? `empieza con ${b.first_items.map((i) => `${i.name || `objeto ${i.key}`} (${i.games})`).join(", ")}` : "",
            b.skill_max?.length ? ` · sube ${b.skill_max[0].key}` : "",
          )
        : null,
      collapsible(
        "otps.detalle.medias",
        [icon("activity"), d.ref_tier ? `Sus medias frente a los OTPs ${tierName(d.ref_tier)} de ${dd.name(d.champ)}` : "Sus medias"],
        h(
          "div.table-wrap",
          h(
            "table.table",
            h("thead", h("tr", h("th", "Métrica"), h("th.num", "Él"), d.ref_tier ? h("th.num", `OTPs ${tierName(d.ref_tier)}`) : null)),
            h(
              "tbody",
              Object.entries(d.metrics).map(([k, v]) => {
                const [label, , dec, unit] = METRIC_LABEL[k] || [k, "higher", 1];
                const f = (x) => (x == null ? "—" : `${fmt.num(x, dec)}${unit ? ` ${unit}` : ""}`);
                return h("tr", h("td", label), h("td.num", h(`b.${status(k, v.mine, v.ref)}`, f(v.mine))), d.ref_tier ? h("td.num.muted", f(v.ref)) : null);
              }),
            ),
          ),
        ),
        { open: true, cls: "flat" },
      ),
      collapsible(
        "otps.detalle.partidas",
        [icon("film"), "Sus partidas analizadas"],
        h(
          "div.otp-form",
          d.games.map((g) =>
            h(
              `a.chip.otp-game.${g.win ? "win" : "loss"}`,
              { href: `#/partida/${g.match_id}`, title: `${fmt.date(g.game_start)} · ${fmt.duration(g.duration)} · ${tierName(g.tier)}${g.opponent ? ` · vs ${dd.name(g.opponent)}` : ""}` },
              g.opponent ? champImg(g.opponent, "xs") : null,
              `${g.win ? "V" : "D"} ${g.kills ?? "?"}/${g.deaths ?? "?"}/${g.assists ?? "?"}`,
            ),
          ),
        ),
        { cls: "flat", summary: `${fmt.int(d.games.length)} más recientes` },
      ),
    ),
  );
}
