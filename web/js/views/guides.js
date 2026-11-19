// Guías de matchups: biblioteca de guías guardadas, enfrentamientos frecuentes sin guía y amigos del cliente.
import { api, filterParams } from "../api.js";
import { dd, ROLE_LABEL, ROLES, state } from "../state.js";
import { champImg, empty, errorBox, fmt, h, icon, loading, modal, mount, notice, roleIcon } from "../ui.js";

let root = null;

export function destroy() {
  root = null;
}

export async function render(container) {
  root = container;
  if (!state.status) state.status = await api.get("/api/system/status").catch(() => null);
  const library = h("div");
  const top = h("div");
  const tools = h("div.stack");
  mount(
    container,
    h("div.section-head", h("div", h("h2", "Guías de matchups"), h("p", "Guías guardadas en tu carpeta de coachings y los enfrentamientos que más juegas sin guía todavía."))),
    h("div.grid.guides-split", h("div.stack", library, top), tools),
  );
  mount(library, loading());
  renderTools(tools);
  loadLibrary(library);
  loadTop(top);
}

async function loadLibrary(box) {
  try {
    const tree = await api.get("/api/guides");
    if (!root) return;
    const roles = Object.entries(tree.roles || {});
    if (!roles.length) {
      mount(box, h("section.card", h("h3.card-title", icon("book"), "Biblioteca"), empty("Todavía no hay guías", `Se leen de ${tree.root}: guarda ahí tus guías como «Campeón_vs_Rival.txt» y aparecerán aquí.`, "book")));
      return;
    }
    mount(
      box,
      h(
        "section.card",
        h("h3.card-title", icon("book"), `Biblioteca · ${tree.count} guías`),
        roles.map(([role, champs]) =>
          h(
            "div",
            { style: { marginBottom: "12px" } },
            h("div.row.small.muted", { style: { margin: "6px 0" } }, ROLES.includes(role) ? roleIcon(role) : null, ROLE_LABEL[role] || "General"),
            h(
              "div.chips",
              champs.flatMap((c) =>
                c.guides.map((g) =>
                  h("span.chip", { onclick: () => openGuide(role, c.champ, g.opp) }, champImg(c.champ, "xs"), dd.name(c.champ), h("span.muted", "vs"), champImg(g.opp, "xs"), dd.name(g.opp), h(`span.tiny.${g.source === "IA" ? "gold" : "good"}`, g.source)),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  } catch (err) {
    mount(box, errorBox(err));
  }
}

async function openGuide(role, champ, opp) {
  const m = modal(`${dd.name(champ)} vs ${dd.name(opp)}`, loading());
  try {
    const g = await api.get(`/api/guides/${ROLES.includes(role) ? role : "TOP"}/${champ}/${opp}`);
    mount(m.body, h("div.row", h("span.pill.pill-gold", g.source), h("span.tiny.muted", g.path)), h("div.prose", { style: { marginTop: "12px" } }, g.content));
  } catch (err) {
    mount(m.body, errorBox(err));
  }
}

async function loadTop(box) {
  mount(box, loading());
  try {
    const data = await api.get("/api/top-matchups", filterParams({ guide: "without", limit: 6 }));
    if (!root) return;
    const rows = ROLES.flatMap((r) => (data[r] || []).map((m) => ({ ...m, role: r })));
    mount(
      box,
      h(
        "section.card",
        h("h3.card-title", icon("spark"), "Enfrentamientos frecuentes sin guía"),
        rows.length
          ? h(
              "div.col",
              { style: { gap: "6px" } },
              rows.slice(0, 18).map((m) =>
                h(
                  "div.row",
                  roleIcon(m.role),
                  champImg(m.champ, "sm"),
                  h("span.small", `${dd.name(m.champ)} vs ${dd.name(m.opp)}`),
                  champImg(m.opp, "sm"),
                  h("span.spacer"),
                  h("span.tiny.muted", `${fmt.compact(m.games)} partidas`),
                ),
              ),
            )
          : h("div.small.muted", "Sin datos propios todavía."),
      ),
    );
  } catch (err) {
    mount(box, errorBox(err));
  }
}

function renderTools(box) {
  const features = state.status?.features || {};
  const friendsBox = h("div");
  const lcuCard = h(
    "section.card",
    h("h3.card-title", icon("user"), "Amigos del cliente de LoL"),
    features.lcu
      ? h("div", h("button.btn", { onclick: () => loadFriends(friendsBox) }, icon("refresh"), "Leer amigos"), friendsBox)
      : notice("Abre el cliente de League of Legends en este ordenador para leer tu lista de amigos."),
  );
  mount(box, lcuCard);
}

async function loadFriends(box) {
  mount(box, loading("Leyendo el cliente…"));
  try {
    const { friends } = await api.get("/api/lcu/friends");
    const platform = state.filters.platform !== "all" ? state.filters.platform : "euw1";
    mount(
      box,
      h("div.chips", { style: { marginTop: "10px" } }, friends.map((f) => (f.tag_line ? h("a.chip", { href: `#/jugador/${platform}/${encodeURIComponent(f.game_name)}/${encodeURIComponent(f.tag_line)}` }, `${f.game_name}#${f.tag_line}`) : h("span.chip", f.game_name)))),
      h("p.tiny.muted", { style: { marginTop: "6px" } }, `Pulsa un amigo para ver su perfil (${platform.toUpperCase()}).`),
    );
  } catch (err) {
    mount(box, errorBox(err));
  }
}
