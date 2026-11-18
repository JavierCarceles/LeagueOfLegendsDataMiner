// Guías de matchups: biblioteca, generación con IA, vídeos de YouTube y amigos del cliente.
import { api, filterParams } from "../api.js";
import { champPicker } from "../components/picker.js";
import { dd, ROLE_LABEL, ROLES, state } from "../state.js";
import { champImg, empty, errorBox, fmt, h, icon, loading, modal, mount, notice, roleIcon, select, toast } from "../ui.js";

let root = null;
let jobsTimer = null;

export function destroy() {
  root = null;
  clearInterval(jobsTimer);
}

export async function render(container) {
  root = container;
  if (!state.status) state.status = await api.get("/api/system/status").catch(() => null);
  const library = h("div");
  const top = h("div");
  const tools = h("div.stack");
  mount(
    container,
    h("div.section-head", h("div", h("h2", "Guías de matchups"), h("p", "Guías guardadas en tu carpeta de coachings (de vídeos o generadas con IA a partir de tus estadísticas y de OP.GG)."))),
    h("div.grid", { style: { gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)" } }, h("div.stack", library, top), tools),
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
      mount(box, h("section.card", h("h3.card-title", icon("book"), "Biblioteca"), empty("Todavía no hay guías", `Se guardan en ${tree.root}. Genera una con IA o procesa un vídeo de coaching.`, "book")));
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
                  state.status?.features?.gemini ? h("button.btn.btn-sm", { onclick: (e) => generate(m.role, m.champ, m.opp, e.currentTarget) }, icon("spark"), "IA") : null,
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

async function generate(role, champ, opp, btn) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Generando…";
  }
  try {
    const g = await api.post("/api/guides/generate", { role, champ, opp });
    toast(`Guía generada: ${dd.name(champ)} vs ${dd.name(opp)}`, "success");
    modal(`${dd.name(champ)} vs ${dd.name(opp)}`, h("div.prose", g.content));
    if (root) render(root);
  } catch (err) {
    toast(err.message, "error", 7000);
    if (btn) {
      btn.disabled = false;
      btn.textContent = "IA";
    }
  }
}

function renderTools(box) {
  const features = state.status?.features || {};
  let role = "TOP";
  const a = champPicker({ placeholder: "Tu campeón", compact: true });
  const b = champPicker({ placeholder: "Rival", compact: true });
  const genCard = h(
    "section.card",
    h("h3.card-title", icon("spark"), "Generar guía con IA"),
    features.gemini
      ? h(
          "div.col",
          select(ROLES.map((r) => ({ value: r, label: ROLE_LABEL[r] })), role, (v) => (role = v)),
          a,
          b,
          h("button.btn.btn-primary", { onclick: (e) => (a.getValue() && b.getValue() ? generate(role, a.getValue(), b.getValue(), e.currentTarget) : toast("Elige los dos campeones", "error")) }, icon("spark"), "Generar"),
          h("p.tiny.muted", "Gemini recibe el WR ajustado, la fase de líneas y el consejo de OP.GG del enfrentamiento."),
        )
      : notice("Añade GEMINI_KEY_1 (y opcionalmente _2, _3) en .env e instala requirements-coaching.txt para generar guías."),
  );

  const yt = features.youtube || {};
  const urlInput = h("input.input", { placeholder: "URL de vídeo o playlist de YouTube" });
  const jobsBox = h("div.col", { style: { marginTop: "10px" } });
  const ytCard = h(
    "section.card",
    h("h3.card-title", icon("play"), "Procesar coaching de YouTube"),
    yt.yt_dlp && features.gemini
      ? h(
          "div",
          h("div.input-group", urlInput, h("button.btn.btn-primary", { onclick: async () => { try { await api.post("/api/coaching/youtube", { url: urlInput.value }); toast("Procesando en segundo plano…"); pollJobs(jobsBox); } catch (err) { toast(err.message, "error"); } } }, "Procesar")),
          h("p.tiny.muted", { style: { marginTop: "8px" } }, `Subtítulos ${yt.transcripts ? "✓" : "✗"} · Whisper ${yt.whisper ? "✓" : "✗ (solo vídeos con subtítulos)"} · Resumen LSA ${yt.sumy ? "✓" : "✗"}`),
          jobsBox,
        )
      : notice("Instala las dependencias opcionales (pip install -r requirements-coaching.txt) y configura Gemini para convertir vídeos de coaching en guías."),
  );

  const friendsBox = h("div");
  const lcuCard = h(
    "section.card",
    h("h3.card-title", icon("user"), "Amigos del cliente de LoL"),
    features.lcu
      ? h("div", h("button.btn", { onclick: () => loadFriends(friendsBox) }, icon("refresh"), "Leer amigos"), friendsBox)
      : notice("Instala psutil y abre el cliente de League of Legends para leer tu lista de amigos."),
  );
  mount(box, genCard, ytCard, lcuCard);
  if (yt.yt_dlp) pollJobs(jobsBox);
}

function pollJobs(box) {
  const draw = async () => {
    try {
      const { jobs } = await api.get("/api/coaching/youtube");
      mount(
        box,
        jobs.map((j) =>
          h("div.worker", h("div.row", h(`span.pill.${j.status === "done" ? "pill-good" : j.status === "error" ? "pill-bad" : "pill-warn"}`, j.status === "running" ? "Procesando" : j.status === "done" ? "Terminado" : "Error"), h("span.tiny.muted", `${j.done}/${j.total} vídeos · ${j.saved.length} guías`)), j.errors.slice(-2).map((e) => h("div.tiny.warn", e))),
        ),
      );
      if (!jobs.some((j) => j.status === "running")) clearInterval(jobsTimer);
    } catch {
      clearInterval(jobsTimer);
    }
  };
  clearInterval(jobsTimer);
  draw();
  jobsTimer = setInterval(draw, 4000);
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
