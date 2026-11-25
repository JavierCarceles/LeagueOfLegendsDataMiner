// Vídeos de YouTube: el recolector de datos de coaches (enlaces de vídeo, lista o canal, y búsquedas por rol, tema
// o campeón en español e inglés). Solo recoge: lo que se dice y lo que pasa en el minimapa se sube a GitHub para
// analizarlo después.
import { api } from "../api.js";
import { state } from "../state.js";
import { collapsible, empty, errorBox, fmt, h, icon, kpi, loading, mount, notice, select, toast } from "../ui.js";

const ROLE_OPTS = [
  { value: "", label: "Cualquier rol" },
  { value: "top", label: "Top" },
  { value: "jungla", label: "Jungla" },
  { value: "mid", label: "Mid" },
  { value: "adc", label: "ADC" },
  { value: "support", label: "Support" },
];
const TOPIC_OPTS = [
  { value: "", label: "Cualquier tema" },
  { value: "macro", label: "Macro" },
  { value: "oleadas", label: "Oleadas" },
  { value: "vision", label: "Visión" },
  { value: "objetivos", label: "Objetivos" },
  { value: "early", label: "Early game" },
  { value: "rutas", label: "Rutas de jungla" },
  { value: "teamfight", label: "Teamfights" },
  { value: "fase_lineas", label: "Fase de líneas" },
  { value: "coaching", label: "Coaching / VOD review" },
  { value: "mentalidad", label: "Mentalidad" },
];
const LANG_OPTS = [
  { value: "es,en", label: "Español e inglés" },
  { value: "es", label: "Solo español" },
  { value: "en", label: "Solo inglés" },
];
const N_OPTS = [5, 10, 20, 40].map((n) => ({ value: String(n), label: `${n} por idioma` }));
const LIMIT_OPTS = [
  { value: "20", label: "Últimos 20" },
  { value: "50", label: "Últimos 50" },
  { value: "200", label: "Últimos 200" },
  { value: "", label: "Todos" },
];
const STATE_LABEL = { pendiente: "En cola", procesando: "Procesando", hecho: "Recogido", error: "Error", descartado: "Descartado" };
const STATE_PILL = { pendiente: "pill", procesando: "pill-gold", hecho: "pill-good", error: "pill-bad", descartado: "pill-warn" };
const PAUSE_LABEL = { manual: "En pausa", en_partida: "En pausa mientras juegas", youtube_frena: "YouTube está frenando: espero 30 min" };
const SOURCE_LABEL = { video: "Vídeo", lista: "Lista", canal: "Canal", busqueda: "Búsqueda" };
const PRESETS = [
  { rol: "jungla", tema: "rutas", label: "Rutas de jungla" },
  { rol: "jungla", tema: "macro", label: "Macro de jungla" },
  { rol: "support", tema: "vision", label: "Visión de support" },
  { rol: "", tema: "oleadas", label: "Control de oleadas" },
  { rol: "", tema: "objetivos", label: "Objetivos" },
  { rol: "", tema: "coaching", label: "VOD reviews" },
];
const POLL_MS = 4000;

const form = { texto: "", rol: "", tema: "", campeon: "", idiomas: "es,en", por_idioma: "10", limite: "50" };
let root = null;
let timer = null;
let boxes = null;
let lastCounts = "";

export function destroy() {
  root = null;
  clearTimeout(timer);
  timer = null;
}

export async function render(container) {
  root = container;
  lastCounts = "";
  boxes = { status: h("div", loading()), list: h("div") };
  mount(
    container,
    h(
      "div.section-head",
      h(
        "div",
        h("h2", "Vídeos de coaches"),
        h("p", "Pega un vídeo, una lista o un canal de YouTube, o busca por rol, tema o campeón. Se recoge todo lo que se dice y, si es una partida, el minuto de juego y lo que pasa en el minimapa; todo se sube a GitHub para analizarlo después."),
      ),
    ),
    h("div.stack", addCard(), boxes.status, boxes.list),
  );
  await poll();
}

// ------------------------------------------------------------------ añadir
function labeled(text, control) {
  return h("label.video-field", h("span.small.muted", text), control);
}

function champInput() {
  const list = h("datalist#video-champs", (state.champList || []).map((c) => h("option", { value: c.name })));
  const input = h("input.input", {
    list: "video-champs",
    placeholder: "Opcional",
    value: form.campeon,
    oninput: (e) => {
      form.campeon = e.target.value;
    },
  });
  return h("span", input, list);
}

function addCard() {
  const input = h("input.input", {
    placeholder: "Enlace de vídeo, lista o canal… o qué buscar (p. ej. «lux support»)",
    value: form.texto,
    oninput: (e) => {
      form.texto = e.target.value;
    },
    onkeydown: (e) => {
      if (e.key === "Enter") void submit(button, input);
    },
  });
  const button = h("button.btn.btn-primary", { type: "button", onclick: () => void submit(button, input) }, icon("download"), "Añadir");
  const presets = PRESETS.map((p) =>
    h("button.chip", { type: "button", title: `Buscar y añadir: ${p.label}`, onclick: () => void submit(button, input, { texto: "", rol: p.rol, tema: p.tema }) }, icon("search"), p.label),
  );
  return h(
    "section.card",
    h("h3.card-title", icon("film"), "Añadir vídeos"),
    h("div.input-group", input, button),
    h(
      "div.video-options",
      labeled("Rol", select(ROLE_OPTS, form.rol, (v) => {
        form.rol = v;
      })),
      labeled("Tema", select(TOPIC_OPTS, form.tema, (v) => {
        form.tema = v;
      })),
      labeled("Campeón", champInput()),
      labeled("Idioma", select(LANG_OPTS, form.idiomas, (v) => {
        form.idiomas = v;
      })),
      labeled("Resultados", select(N_OPTS, form.por_idioma, (v) => {
        form.por_idioma = v;
      })),
      labeled("De un canal o lista", select(LIMIT_OPTS, form.limite, (v) => {
        form.limite = v;
      })),
    ),
    h("div.small.muted.video-presets-title", "Búsquedas rápidas (con el campeón e idioma de arriba):"),
    h("div.chips", presets),
    h(
      "p.small.muted.video-help",
      "Un enlace de canal trae sus últimos vídeos; una búsqueda, los primeros resultados en cada idioma (sin shorts). Lo descargado para mirar los frames va a una carpeta temporal que se borra al terminar cada vídeo. Mientras juegas, se pausa solo.",
    ),
  );
}

function addedText(sources) {
  const n = sources?.length || 0;
  if (!n) return "Nada que añadir";
  if (sources[0].tipo === "busqueda") return n === 1 ? `Buscando «${sources[0].valor}»…` : `Buscando en ${n} idiomas…`;
  return `${SOURCE_LABEL[sources[0].tipo] || "Enlace"} añadido: se procesa en segundo plano`;
}

async function submit(button, input, override = {}) {
  const data = { ...form, ...override };
  if (!data.texto.trim() && !data.rol && !data.tema && !data.campeon.trim()) {
    toast("Escribe un enlace o qué buscar, o elige un rol o un tema", "error");
    return;
  }
  button.disabled = true;
  try {
    const res = await api.post("/api/videos/add", {
      texto: data.texto,
      rol: data.rol,
      tema: data.tema,
      campeon: data.campeon,
      idiomas: data.idiomas.split(","),
      por_idioma: Number(data.por_idioma),
      limite: data.limite ? Number(data.limite) : null,
    });
    toast(addedText(res.fuentes), "success");
    if (!override.rol && !override.tema) {
      form.texto = "";
      input.value = "";
    }
    lastCounts = "";
    await poll();
  } catch (err) {
    toast(err.message || String(err), "error");
  } finally {
    button.disabled = false;
  }
}

// ------------------------------------------------------------------ estado
async function poll() {
  clearTimeout(timer);
  if (!root) return;
  try {
    const status = await api.get("/api/videos/status");
    if (!root) return;
    mount(boxes.status, statusCard(status));
    const counts = JSON.stringify(status.cuentas);
    if (counts !== lastCounts) {
      lastCounts = counts;
      const list = await api.get("/api/videos/list", { limit: 500 });
      if (root) mount(boxes.list, listCards(list));
    }
  } catch (err) {
    if (root) mount(boxes.status, errorBox(err));
  }
  if (root) timer = setTimeout(() => void poll(), POLL_MS);
}

function toolsNotice(tools) {
  if (tools?.estado === "instalando") return notice(`Instalando las herramientas de vídeo: ${tools.detalle || "yt-dlp, OpenCV y Deno"}. Solo la primera vez.`, "info");
  if (tools?.estado === "error") return notice(tools.detalle || "No se pudieron instalar las herramientas de vídeo (se reintenta solo).", "bad");
  return null;
}

function stateLine(s) {
  if (s.pausa) {
    const until = s.hasta ? ` (hasta ${fmt.date(s.hasta)})` : "";
    return h("span.pill.pill-warn", icon("pause"), PAUSE_LABEL[s.pausa] + until);
  }
  if (s.actual) return h("span.pill.pill-gold.pill-dot", "Trabajando");
  if (s.activo) return h("span.pill.pill-good.pill-dot", "Esperando vídeos");
  return h("span.pill", "Parado");
}

function currentLine(current) {
  if (!current) return null;
  const pct = current.progreso === null || current.progreso === undefined ? "" : ` · ${Math.round(current.progreso * 100)}%`;
  const what = current.tipo === "fuente" ? `«${current.fuente?.valor || ""}»` : current.titulo || current.id;
  return h("div.video-current", icon("activity"), h("b", what), h("span.muted", ` — ${current.paso || ""}${pct}`));
}

function statusCard(s) {
  const c = s.cuentas || {};
  const paused = s.pausa === "manual";
  const toggle = h(
    "button.btn.btn-sm",
    {
      type: "button",
      title: paused ? "Seguir recogiendo vídeos" : "Pausar: lo recogido se sube a GitHub (el vídeo a medias, al acabar)",
      onclick: async () => {
        try {
          await api.post("/api/videos/pause", { pausado: !paused });
          await poll();
        } catch (err) {
          toast(err.message || String(err), "error");
        }
      },
    },
    icon(paused ? "play" : "pause"),
    paused ? "Reanudar" : "Pausar",
  );
  return h(
    "section.card",
    h("h3.card-title.video-head", icon("activity"), "Recolector", h("span.spacer"), stateLine(s), toggle),
    toolsNotice(s.herramientas),
    h(
      "div.kpis",
      kpi("En cola", fmt.int((c.pendiente || 0) + (c.procesando || 0))),
      kpi("Recogidos", fmt.int(c.hecho || 0)),
      kpi("Con error", fmt.int(c.error || 0)),
      kpi("Descartados", fmt.int(c.descartado || 0), "privados, borrados…"),
      kpi("Fuentes por leer", fmt.int(c.fuentes_pendientes || 0)),
    ),
    currentLine(s.actual),
    equipmentLine(s.equipo),
    s.ultimo_error ? h("p.small.muted", "Último aviso: ", s.ultimo_error) : null,
  );
}

/** «Equipo: 28 hilos de CPU · el último vídeo usó 19 (70 % de la CPU libre) · gráfica NVIDIA GeForce RTX 4070 SUPER». */
function equipmentLine(e) {
  if (!e) return null;
  const share = Math.round((e.parte || 0.7) * 100);
  const used = e.hilos ? `el último vídeo usó ${e.hilos} (el ${share} % de la CPU libre)` : `cada vídeo usa el ${share} % de la CPU libre al empezar`;
  const gpu = e.gpu ? `gráfica ${e.gpu.nombre}` : "sin gráfica NVIDIA";
  return h("p.small.muted.video-equipment", icon("monitor"), `Equipo: ${e.nucleos} hilos de CPU · ${used} · ${gpu}`);
}

// ------------------------------------------------------------------ listas
function pills(v) {
  const r = v.resumen || {};
  const out = [];
  if (r.transcripcion) out.push(h("span.pill.pill-blue", { title: r.transcripcion }, `Texto · ${(r.idioma || "?").toUpperCase()}`));
  else if (v.estado === "hecho") out.push(h("span.pill.pill-warn", "Sin texto"));
  if (r.partida) {
    const champ = r.campeon ? ` · ${r.campeon}` : "";
    out.push(h("span.pill.pill-gold", { title: `${fmt.int(r.reloj)} lecturas del reloj, ${fmt.int(r.camara)} de la cámara` }, `Partida ${fmt.duration(r.segundos_partida)}${champ}`));
  }
  return out;
}

function actions(v) {
  const out = [];
  if (v.estado === "error" || v.estado === "descartado") {
    out.push(
      h("button.btn.btn-ghost.btn-sm", { type: "button", title: "Volver a intentarlo", onclick: () => void act(api.post(`/api/videos/${v.id}/retry`)) }, icon("refresh")),
    );
  }
  if (v.estado !== "procesando") {
    out.push(h("button.btn.btn-ghost.btn-sm", { type: "button", title: "Quitar de la lista (y lo recogido)", onclick: () => void act(api.del(`/api/videos/${v.id}`)) }, icon("trash")));
  }
  return out;
}

async function act(promise) {
  try {
    await promise;
    lastCounts = "";
    await poll();
  } catch (err) {
    toast(err.message || String(err), "error");
  }
}

function videoRow(v) {
  return h(
    "div.video-row",
    h(
      "div.video-main",
      h("a.video-title", { href: `https://www.youtube.com/watch?v=${v.id}`, target: "_blank", rel: "noopener" }, v.titulo || v.resumen?.titulo || v.id),
      h("div.small.muted", [v.resumen?.canal || v.canal, fmt.duration(v.duracion || v.resumen?.duracion)].filter((x) => x && x !== "—").join(" · ")),
      v.error ? h("div.small.video-error", v.error) : null,
    ),
    h("div.video-pills", h(`span.pill.${STATE_PILL[v.estado] || "pill"}`, STATE_LABEL[v.estado] || v.estado), pills(v)),
    h("div.video-actions", actions(v)),
  );
}

function group(id, title, items, open) {
  if (!items.length) return null;
  return collapsible(`videos.${id}`, title, () => h("div.video-list", items.map(videoRow)), { open, summary: `${fmt.int(items.length)} vídeos` });
}

function sourceRow(s) {
  const label = SOURCE_LABEL[s.tipo] || s.tipo;
  const detail = s.estado === "pendiente" ? "por leer" : `${fmt.int(s.videos)} vídeos, ${fmt.int(s.nuevos)} nuevos`;
  return h(
    "div.video-row",
    h("div.video-main", h("b", `${label}: `, s.valor), h("div.small.muted", `${detail} · ${fmt.ago(s.alta)}`), s.error ? h("div.small.video-error", s.error) : null),
  );
}

function listCards(data) {
  const all = data.videos || [];
  if (!all.length && !(data.fuentes || []).length) {
    return h("section.card", empty("Todavía no hay vídeos", "Pega arriba un enlace de YouTube o busca por rol, tema o campeón.", "film"));
  }
  const queued = all.filter((v) => v.estado === "pendiente" || v.estado === "procesando");
  const done = all.filter((v) => v.estado === "hecho");
  const failed = all.filter((v) => v.estado === "error" || v.estado === "descartado");
  return h(
    "div.stack",
    group("cola", [icon("clock"), "En cola"], queued, true),
    group("hechos", [icon("check"), "Recogidos"], done, false),
    group("errores", [icon("alert"), "Con error o descartados"], failed, false),
    (data.fuentes || []).length
      ? collapsible("videos.fuentes", [icon("link"), "Fuentes añadidas"], () => h("div.video-list", data.fuentes.map(sourceRow)), { summary: `${fmt.int(data.fuentes.length)}` })
      : null,
  );
}
