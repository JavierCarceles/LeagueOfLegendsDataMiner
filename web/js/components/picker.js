// Selector de campeón con búsqueda (nombre localizado, nombre en inglés o apodo).
// El desplegable mide siempre lo mismo que el botón y puede mostrar arriba
// recomendaciones y tu pool sin que haya que pulsar nada más.
import { dd, state } from "../state.js";
import { champImg, h, icon } from "../ui.js";

const norm = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]/g, "");
const GAP = 6;
const MARGIN = 12;

let openPop = null;
function closePop() {
  if (openPop) {
    openPop.cleanup?.();
    openPop.remove();
    openPop = null;
  }
}
document.addEventListener("mousedown", (e) => {
  if (openPop && !openPop.contains(e.target) && !openPop.owner?.contains(e.target)) closePop();
});
window.addEventListener("resize", closePop);

/** Cierra el desplegable abierto (al cambiar de vista). */
export const closePickers = () => closePop();

/**
 * champPicker({ value, onChange, placeholder, exclude: () => Set<int>, hint: (id) => string, recommend })
 *
 * recommend: () => Promise<{ sections: [{ title, items: [{ champ, meta, cls, title }] }], scores: Map<id, {text, cls}> }> | null
 * pool: () => Promise<Set<id>> | Set<id> | null — campeones que se juegan en esa línea: la lista solo muestra esos; al
 *       buscar por nombre, los demás salen aparte («Poco jugados en esta línea») por si un rival elige algo raro.
 */
export function champPicker({ value = null, onChange, placeholder = "Elegir campeón", exclude = () => new Set(), hint = null, compact = false, recommend = null, pool = null } = {}) {
  let current = value;
  const btn = h("button.picker-btn", { type: "button" });
  const root = h("div.picker", btn);

  const render = () => {
    btn.replaceChildren();
    btn.classList.toggle("filled", !!current);
    if (current) {
      btn.append(champImg(current, compact ? "" : "lg"), h("div", { style: { minWidth: 0 } }, h("div.champ-name", dd.name(current)), hint ? h("div.tiny.muted", hint(current) || "") : null));
    } else {
      btn.append(h("span.champ-img", { style: { display: "grid", placeItems: "center", width: compact ? "32px" : "52px", height: compact ? "32px" : "52px" } }, icon("search")), h("span.ph", placeholder));
    }
  };

  const open = () => {
    closePop();
    const input = h("input.input", { placeholder: "Buscar campeón…", autocomplete: "off", spellcheck: "false" });
    const list = h("div.picker-list");
    const pop = h("div.picker-pop", input, list);
    pop.owner = btn;
    let active = 0;
    let options = [];
    let recs = null; // {sections, scores}
    let recsLoading = !!recommend;
    let poolSet = null; // Set de la línea (null: sin datos → todos)
    const taken = exclude();

    // Mismo ancho que el botón; debajo si cabe, si no encima. Sigue al botón al hacer scroll.
    const place = () => {
      const rect = btn.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) return closePop();
      const width = Math.min(rect.width, window.innerWidth - 2 * MARGIN);
      pop.style.width = `${width}px`;
      pop.style.left = `${Math.max(MARGIN, Math.min(window.innerWidth - width - MARGIN, rect.left))}px`;
      const height = pop.offsetHeight;
      const below = rect.bottom + GAP;
      const fitsBelow = below + height <= window.innerHeight - MARGIN;
      const above = rect.top - GAP - height;
      pop.style.top = `${fitsBelow || above < MARGIN ? below : above}px`;
    };

    const option = (c, i, meta = null, extraCls = "") =>
      h(
        `div.picker-opt${i === active ? ".active" : ""}${taken.has(c) && c !== current ? ".disabled" : ""}${extraCls}`,
        { onclick: () => choose(c), onmouseenter: () => setActive(i), dataset: { index: i } },
        champImg(c, "sm"),
        h("span.picker-name", dd.name(c)),
        meta ? h(`span.meta${meta.cls ? "." + meta.cls : ""}`, { title: meta.title || "" }, meta.text) : hint ? h("span.meta", hint(c) || "") : null,
      );

    const setActive = (i) => {
      if (i === active) return;
      list.querySelector(".picker-opt.active")?.classList.remove("active");
      list.querySelector(`.picker-opt[data-index="${i}"]`)?.classList.add("active");
      active = i;
    };

    const draw = () => {
      const q = norm(input.value);
      const matches = (c) => !q || norm(dd.name(c)).includes(q) || norm(dd.champ(c)?.key).includes(q);
      const all = state.champList.filter((c) => !q || norm(c.name).includes(q) || norm(c.key).includes(q)).map((c) => c.id);
      if (q) all.sort((a, b) => (norm(dd.name(a)).startsWith(q) ? 0 : 1) - (norm(dd.name(b)).startsWith(q) ? 0 : 1));
      options = [];
      const nodes = [];
      if (current) nodes.push(h("div.picker-opt", { onclick: () => choose(null) }, icon("close"), h("span.muted", "Quitar campeón")));
      const scoreOf = (c) => recs?.scores?.get(c) || null;
      if (!q && recsLoading) nodes.push(h("div.picker-loading", h("div.spinner"), "Calculando recomendados…"));
      if (!q && recs) {
        for (const section of recs.sections) {
          const items = section.items.filter((it) => matches(it.champ));
          if (!items.length) continue;
          nodes.push(h("div.picker-section", section.title));
          for (const it of items) {
            nodes.push(option(it.champ, options.length, it.meta, ".picker-rec"));
            options.push(it.champ);
          }
        }
        nodes.push(h("div.picker-section", poolSet && poolSet.size ? "Campeones de esta línea" : "Todos"));
      }
      // Con los campeones de la línea: solo esos; buscando, el resto aparte (para anotar un pick raro del rival).
      const inPool = poolSet && poolSet.size ? all.filter((c) => poolSet.has(c)) : all;
      const others = poolSet && poolSet.size && q ? all.filter((c) => !poolSet.has(c)) : [];
      if (poolSet && poolSet.size && !recs && !q) nodes.push(h("div.picker-section", "Campeones de esta línea"));
      for (const c of inPool.slice(0, q ? 80 : 200)) {
        nodes.push(option(c, options.length, scoreOf(c)));
        options.push(c);
      }
      if (others.length) {
        nodes.push(h("div.picker-section", { title: "Casi nunca se juegan en esta línea: no se recomiendan" }, "Poco jugados en esta línea"));
        for (const c of others.slice(0, 40)) {
          nodes.push(option(c, options.length, null, ".picker-offrole"));
          options.push(c);
        }
      }
      active = Math.min(active, Math.max(0, options.length - 1));
      list.replaceChildren(...nodes);
      list.querySelector(`.picker-opt[data-index="${active}"]`)?.classList.add("active");
    };
    const choose = (id) => {
      if (id && taken.has(id) && id !== current) return;
      current = id;
      render();
      closePop();
      onChange?.(id);
    };
    const scrollActive = () => list.querySelector(".picker-opt.active")?.scrollIntoView({ block: "nearest" });
    input.addEventListener("input", () => { active = 0; draw(); place(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { setActive(Math.min(options.length - 1, active + 1)); scrollActive(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { setActive(Math.max(0, active - 1)); scrollActive(); e.preventDefault(); }
      else if (e.key === "Enter") { const c = options[active]; if (c) choose(c); e.preventDefault(); }
      else if (e.key === "Escape") closePop();
    });
    draw();
    document.body.append(pop);
    openPop = pop;
    place();
    const onScroll = (e) => { if (!pop.contains(e.target)) place(); };
    document.addEventListener("scroll", onScroll, true);
    pop.cleanup = () => document.removeEventListener("scroll", onScroll, true);
    input.focus();

    if (pool) {
      Promise.resolve(pool())
        .then((set) => { poolSet = set instanceof Set ? set : null; })
        .catch(() => { poolSet = null; })
        .finally(() => { if (openPop === pop) { draw(); place(); } });
    }
    if (recommend) {
      Promise.resolve(recommend())
        .then((data) => { recs = data; })
        .catch(() => { recs = null; })
        .finally(() => {
          recsLoading = false;
          if (openPop === pop) { draw(); place(); }
        });
    }
  };

  btn.addEventListener("click", () => (openPop?.owner === btn ? closePop() : open()));
  render();
  root.setValue = (id) => { current = id; render(); };
  root.getValue = () => current;
  return root;
}
