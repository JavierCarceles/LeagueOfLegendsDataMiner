/** Tema de la página entera (27-11-2026): «Estándar», el de siempre (por defecto); «Hextech», con la estética del
 *  cliente del juego; o uno por línea (Top, Jungla, Mid, ADC y los tres de Support: Engage, Guardián y Peel). Se elige
 *  en el menú de tu perfil (arriba a la derecha).
 *
 *  Va en ``<html data-theme="…">``: cada tema es un fichero de CSS que solo actúa con su valor (``theme-<id>.css``), así
 *  que añadir uno nuevo es añadir su fichero, su línea en ``THEMES``, su id en el script del ``<head>`` y en
 *  ``UI_THEMES`` (app/api/system.py). Los de línea («r-…») comparten la base theme-roles.css y la barra de cristal
 *  lleva la piel de cada uno (scrollbars.js: ``SKINS``). Se guarda en la app (``/api/ui/theme``: vale
 *  aunque abras otro navegador) y en este navegador, para ponerlo antes de pintar nada (el script del ``<head>`` de
 *  index.html) y que no se vea un instante el otro. */
import { api } from "./api.js";

export const THEMES = [
  { id: "standard", group: "general", label: "Estándar", hint: "El aspecto de siempre: sobrio y limpio." },
  { id: "hextech", group: "general", label: "Hextech", hint: "Como el cliente del juego: oro, turquesa y cristal hextech." },
  { id: "r-top", group: "line", label: "Top", hint: "Tanques: acero con remaches, forja y ascuas." },
  { id: "r-jungle", group: "line", label: "Jungla", hint: "Maleza y objetivos: musgo, hojas, luciérnagas y Barón." },
  { id: "r-mid", group: "line", label: "Mid", hint: "Dagas y magia: plata, runas y chispas arcanas." },
  { id: "r-adc", group: "line", label: "ADC", hint: "Tiradores: latón, mira, trazadoras y fogonazo." },
  { id: "r-engage", group: "support", label: "Engage", hint: "Rell, Pyke, Naut, Thresh: cadenas, anclas y farol." },
  { id: "r-guardian", group: "support", label: "Guardián", hint: "Taric y Braum: escudos, gemas y hielo." },
  { id: "r-peel", group: "support", label: "Peel", hint: "Escudos y curas: nácar, menta, destellos y corazones." },
];
export const THEME_GROUPS = [
  { id: "general", label: "General" },
  { id: "line", label: "Por línea" },
  { id: "support", label: "Support" },
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
