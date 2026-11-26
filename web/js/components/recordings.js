// Grabaciones de la pantalla de tus partidas (app/recorder.py): activar o no, la lista y tus vistazos de cámara.
// Se usa arriba de «Tus repeticiones».
import { api } from "../api.js";
import { collapsible, errorBox, fmt, h, icon, mount, toast } from "../ui.js";

const STATE = {
  grabando: "grabando",
  pendiente: "se analiza cuando no estés jugando",
  analizada: "analizada",
  "sin partida": "no se vio el minimapa",
  corta: "demasiado corta",
  error: "no se pudo analizar",
};

/** Desplegable con el interruptor y las últimas grabaciones; en el título se ve si está grabando. */
export function recordingsSection() {
  const box = h("div");
  const section = collapsible("grabaciones", [icon("film"), "Grabar mis partidas"], box, { summary: "cargando…", cls: "flat" });
  const summaryEl = section.querySelector(".collapse-summary");
  let timer = null;
  let sig = null;

  const load = async () => {
    if (!section.isConnected && timer) {
      clearInterval(timer);
      return;
    }
    let data;
    try {
      data = await api.get("/api/recordings");
    } catch (err) {
      return mount(box, errorBox(err));
    }
    if (summaryEl) summaryEl.textContent = summary(data);
    const next = JSON.stringify([data.activo, data.grabando?.nombre, data.analizando, data.ultimo_error, data.grabaciones.map((r) => [r.nombre, r.estado, r.hay_video])]);
    if (next === sig) return;
    sig = next;
    render(data);
  };

  const toggle = async (btn, value) => {
    btn.disabled = true;
    try {
      await api.post("/api/recordings/enable", { activo: value });
      toast(value ? "Se grabará la pantalla en cuanto empiece tu próxima partida" : "Grabación desactivada");
      sig = null;
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
    btn.disabled = false;
  };

  const render = (data) => {
    const btn = h("button.btn.btn-sm", { type: "button" }, icon(data.activo ? "pause" : "play"), data.activo ? "Desactivar" : "Activar");
    btn.onclick = () => toggle(btn, !data.activo);
    mount(
      box,
      h(
        "div.col",
        { style: { gap: "10px" } },
        h("div.row-wrap", { style: { gap: "10px", alignItems: "center" } }, btn, h("span.small", statusText(data))),
        h("div.tiny.muted", `Al empezar una partida se graba la pantalla y al acabar se mira hacia dónde mueves la cámara. Los vídeos se quedan en tu PC (los ${data.guardados} últimos); solo sube el análisis.`),
        data.grabaciones.length ? h("div.col", { style: { gap: "6px" } }, data.grabaciones.slice(0, 12).map(row)) : null,
      ),
    );
  };

  load();
  timer = setInterval(load, 10_000);
  return section;
}

function summary(data) {
  if (!data.disponible) return "solo en Windows";
  if (data.grabando) return "grabando tu partida";
  if (data.analizando) return "analizando la última grabación";
  return data.activo ? `activada · ${data.grabaciones.length} grabaciones` : "desactivada";
}

function statusText(data) {
  if (!data.disponible) return "La grabación solo funciona en Windows.";
  if (data.grabando) return `Grabando desde hace ${Math.round(data.grabando.segundos / 60)} min.`;
  if (data.analizando) return `Analizando ${data.analizando.nombre}: ${data.analizando.paso}${data.analizando.progreso != null ? ` (${Math.round(data.analizando.progreso * 100)} %)` : ""}.`;
  if (data.ultimo_error) return data.ultimo_error;
  return data.activo ? "Lista: grabará tu próxima partida." : "Desactivada.";
}

function when(rec) {
  return rec.inicio ? new Date(rec.inicio * 1000).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" }) : rec.nombre;
}

function glanceText(v) {
  if (!v || v.antes_del_15 == null) return null;
  const mine = v.antes_del_15;
  const ref = v.elo_alto_antes_del_15;
  const verdict = mine >= ref * 0.9 ? "como en elo alto" : mine >= ref * 0.6 ? "algo menos que en elo alto" : "mucho menos que en elo alto";
  return `Antes del 15 miras otra zona del mapa ${fmt.num(mine, 1)} veces por minuto (elo alto: ${fmt.num(ref, 1)}): ${verdict}.`;
}

function row(rec) {
  const parts = [h("b", when(rec)), rec.campeon ? ` · ${rec.campeon}` : "", rec.duracion ? ` · ${Math.round(rec.duracion / 60)} min` : "", ` · ${STATE[rec.estado] || rec.estado}`];
  const link = rec.hay_video ? h("a.btn.btn-sm", { href: `/api/recordings/${encodeURIComponent(rec.nombre)}/video`, target: "_blank", rel: "noopener" }, icon("play"), "Ver") : null;
  const glance = glanceText(rec.vistazos);
  return h("div.row-wrap", { style: { gap: "8px", alignItems: "center" } }, link, h("div.small", ...parts, glance ? h("div.tiny.muted", glance) : null));
}
