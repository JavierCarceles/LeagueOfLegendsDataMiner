// «Aprende esto»: vídeos gratis de coaches (primero en español), un ejercicio y su propósito,
// para cada punto débil, cada error repetido y cada momento de la repetición.
import { api } from "../api.js";
import { h, icon } from "../ui.js";

let catalog = null;
let loadingCatalog = null;

/** Bloque plegable con el material de un tema (``learn`` viene de la API: label, videos, drill, purpose). */
export function learnBox(learn, { open = false } = {}) {
  if (!learn) return null;
  return h(
    "details.learn",
    { open: open || undefined },
    h("summary", icon("book"), h("span", `Aprende esto: ${learn.label}`)),
    h(
      "div.learn-body",
      h("div.small", h("b", "Ejercicio: "), learn.drill),
      h("div.tiny.muted", learn.purpose),
      h(
        "div.learn-videos",
        (learn.videos || []).map((v) =>
          h(
            "a.learn-video",
            { href: v.url, target: "_blank", rel: "noopener noreferrer", title: `${v.title} (${v.channel})` },
            icon("play"),
            h("span.learn-video-title", v.title),
            h("span.tiny.muted", `${v.channel} · ${v.lang}`),
          ),
        ),
      ),
    ),
  );
}

/** Catálogo completo (una sola petición por sesión de la web). */
export async function learnCatalog() {
  if (catalog) return catalog;
  loadingCatalog ||= api.get("/api/coach/learn").then((data) => (catalog = data)).catch(() => null).finally(() => (loadingCatalog = null));
  return loadingCatalog;
}

/** Tema que corresponde a las etiquetas de un momento (la primera regla que encaje). */
export function learnForTags(cat, tags = []) {
  if (!cat) return null;
  const set = new Set(tags);
  const hit = (cat.by_tag || []).find(([tag]) => set.has(tag));
  return hit ? cat.topics?.[hit[1]] || null : null;
}
