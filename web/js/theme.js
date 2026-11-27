/** Tema de la página entera (27-11-2026): «Estándar», el de siempre (por defecto), o «Hextech», todo con la estética
 *  del cliente del juego (oro y turquesa, marcos con esquinas, sus barras de scroll de cristal…). Se elige en el menú
 *  de tu perfil (arriba a la derecha).
 *
 *  Va en ``<html data-theme="…">``: cada tema es un fichero de CSS que solo actúa con su valor (``theme-<id>.css``), así
 *  que añadir uno nuevo es añadir su fichero y su línea en ``THEMES``. Se guarda en la app (``/api/ui/theme``: vale
 *  aunque abras otro navegador) y en este navegador, para ponerlo antes de pintar nada (el script del ``<head>`` de
 *  index.html) y que no se vea un instante el otro. */
import { api } from "./api.js";

export const THEMES = [
  { id: "standard", label: "Estándar", hint: "El aspecto de siempre: sobrio y limpio." },
  { id: "hextech", label: "Hextech", hint: "Como el cliente del juego: oro, turquesa y cristal hextech." },
];
export const DEFAULT_THEME = "standard";
const STORAGE_KEY = "tempu.theme";
const listeners = new Set();

const valid = (id) => THEMES.some((t) => t.id === id);

export function currentTheme() {
  const id = document.documentElement.dataset.theme;
  return valid(id) ? id : DEFAULT_THEME;
}

/** Aplica un tema a la página entera. ``save``: también en la app (si no, solo aquí: el que viene de la app). */
export function setTheme(id, { save = true } = {}) {
  if (!valid(id)) id = DEFAULT_THEME;
  const changed = id !== document.documentElement.dataset.theme;
  document.documentElement.dataset.theme = id;
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // sin almacenamiento (modo privado): vale con el de la app
  }
  if (save) api.post("/api/ui/theme", { theme: id }).catch(() => {});
  if (changed) listeners.forEach((fn) => fn(id));
}

/** El guardado en la app manda (por si lo cambiaste desde otro navegador). */
export function syncTheme(fromApp) {
  if (valid(fromApp) && fromApp !== currentTheme()) setTheme(fromApp, { save: false });
}

/** Avisa cuando cambia (y una vez al apuntarse, con el actual). */
export function onTheme(fn) {
  listeners.add(fn);
  fn(currentTheme());
  return () => listeners.delete(fn);
}
