// Repeticiones (.rofl) de este ordenador: dónde está la carpeta, cuántas hay y cambiarla.
// Se usa arriba de «Tus repeticiones».
import { api } from "../api.js";
import { collapsible, errorBox, fmt, h, icon, mount, toast } from "../ui.js";

const FOUND_BY = {
  client: "la que usa el cliente de LoL",
  default: "la carpeta por defecto",
  scan: "encontrada buscando en el disco",
  manual: "elegida por ti",
};

/** ``Set`` con las partidas (``EUW1_123…``) que tienen su .rofl en este ordenador (vacío si no se puede saber). */
export async function localReplayIds() {
  try {
    const data = await api.get("/api/replay/local", { limit: 1 });
    return new Set(data.match_ids || []);
  } catch {
    return new Set();
  }
}

/** Desplegable con la carpeta de repeticiones: en el título se ve ya dónde está y cuántas hay. */
export function roflSection() {
  const box = h("div");
  const section = collapsible("repeticiones.rofl", [icon("film"), "Repeticiones .rofl de este ordenador"], box, {
    summary: "buscando la carpeta…",
    cls: "flat",
  });
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
      data = await api.get("/api/replay/local", { limit: 12 });
    } catch (err) {
      return mount(box, errorBox(err));
    }
    if (summaryEl) summaryEl.textContent = folderSummary(data);
    const next = JSON.stringify([data.status, data.path, data.files, data.scanned, data.recent?.map((r) => r.path)]);
    if (next === sig || box.contains(document.activeElement)) return;
    sig = next;
    render(data);
  };

  const rescan = async (btn, path) => {
    btn.disabled = true;
    try {
      await api.post("/api/replay/local/rescan", { path: path || null });
      toast(path ? "Carpeta guardada" : "Buscando la carpeta de repeticiones…");
      sig = null;
      setTimeout(load, 800);
    } catch (err) {
      toast(err.message, "error");
    }
    btn.disabled = false;
  };

  const render = (data) => {
    const pathInput = h("input.input.input-sm", {
      type: "text",
      placeholder: "Pega aquí otra carpeta (opcional)",
      "aria-label": "Carpeta de repeticiones",
      style: { flex: "1 1 240px", minWidth: 0 },
    });
    const status = folderStatus(data);
    mount(
      box,
      h(
        "div.col",
        { style: { gap: "10px" } },
        status,
        h("div.small", h("b", fmt.int(data.files || 0)), ` ${data.files === 1 ? "repetición" : "repeticiones"} en la carpeta · las partidas que la tienen llevan la etiqueta «.rofl en tu PC»`),
        data.recent?.length
          ? h(
              "div.row-wrap",
              { style: { gap: "6px" } },
              data.recent.map((r) =>
                h(
                  r.match_id ? "a.chip" : "span.chip",
                  r.match_id ? { href: `#/repeticiones/${r.match_id}`, title: r.path } : { title: r.path },
                  r.match_id || r.path.split(/[\\/]/).pop(),
                  h("span.tiny.muted", replayMeta(r)),
                ),
              ),
            )
          : null,
        h(
          "div.row-wrap",
          { style: { gap: "8px" } },
          pathInput,
          h("button.btn.btn-sm", { type: "button", onclick: (e) => rescan(e.currentTarget, pathInput.value.trim()) }, icon("refresh"), "Usar esa carpeta o buscar de nuevo"),
        ),
        h(
          "p.tiny.muted",
          { style: { margin: 0 } },
          String.raw`Se busca sola al abrir la app: la que usa el cliente de LoL, la de por defecto (Documentos\League of Legends\Replays) o, si no, por todo el disco. Se recuerda para este sistema operativo y se revisa cada 3 minutos. De cada .rofl se lee la cabecera (parche, duración y estadísticas finales); el resto va cifrado y solo lo reproduce el cliente del juego.`,
        ),
      ),
    );
  };

  void load(); // trata sus errores (los pinta en el desplegable)
  timer = setInterval(load, 5000);
  return section;
}

/** Texto del título del desplegable: buscando en el disco, cuántas hay y dónde, o que no hay carpeta. */
function folderSummary(data) {
  if (data.status === "scanning") return `buscando en el disco… (${fmt.int(data.scanned || 0)} carpetas)`;
  return data.path ? `${fmt.int(data.files || 0)} en ${data.path}` : "no se ha encontrado ninguna carpeta";
}

/** Estado de la carpeta: buscándola en el disco, la carpeta encontrada (y cómo) o que no hay ninguna repetición. */
function folderStatus(data) {
  if (data.status === "scanning") {
    const at = data.at ? ` · ${data.at}` : "";
    return h("div.small", h("span.pill.pill-warn.pill-dot", "Buscando en el disco"), h("span.tiny.muted", ` ${fmt.int(data.scanned || 0)} carpetas revisadas${at}`));
  }
  if (data.path) return h("div.small.otp-path", h("b", "Carpeta: "), data.path, h("span.tiny.muted", ` · ${FOUND_BY[data.found_by] || data.found_by || ""}`));
  const text = data.status === "starting" ? "Buscando…" : "No hay ninguna repetición (.rofl) en este ordenador. Descarga una desde el historial del cliente de LoL y aparecerá aquí.";
  return h("div.small.muted", text);
}

/** Detalle de una repetición reciente: el parche (mayor.menor) y la duración. */
function replayMeta(r) {
  const version = r.game_version ? r.game_version.split(".").slice(0, 2).join(".") : "?";
  const length = r.game_length ? ` · ${fmt.duration(r.game_length / 1000)}` : "";
  return ` · ${version}${length}`;
}
