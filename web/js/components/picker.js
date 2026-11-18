// Selector de campeón con búsqueda (nombre localizado, nombre en inglés o apodo).
import { dd, state } from "../state.js";
import { champImg, h, icon } from "../ui.js";

const norm = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]/g, "");

let openPop = null;
function closePop() {
  if (openPop) {
    openPop.remove();
    openPop = null;
  }
}
document.addEventListener("mousedown", (e) => {
  if (openPop && !openPop.contains(e.target) && !e.target.closest?.(".picker-btn")) closePop();
});
window.addEventListener("resize", closePop);

/**
 * champPicker({ value, onChange, placeholder, exclude: () => Set<int>, hint: (id) => string })
 */
export function champPicker({ value = null, onChange, placeholder = "Elegir campeón", exclude = () => new Set(), hint = null, compact = false } = {}) {
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
    const rect = btn.getBoundingClientRect();
    const input = h("input.input", { placeholder: "Buscar campeón…", autocomplete: "off", spellcheck: "false" });
    const list = h("div.picker-list");
    const pop = h("div.picker-pop", input, list);
    const width = 300;
    pop.style.left = `${Math.min(window.innerWidth - width - 12, Math.max(12, rect.left))}px`;
    const below = rect.bottom + 6;
    pop.style.top = below + 360 > window.innerHeight ? `${Math.max(12, rect.top - 366)}px` : `${below}px`;
    let active = 0;
    let options = [];
    const taken = exclude();

    const draw = () => {
      const q = norm(input.value);
      options = state.champList.filter((c) => !q || norm(c.name).includes(q) || norm(c.key).includes(q));
      if (q) options.sort((a, b) => (norm(a.name).startsWith(q) ? 0 : 1) - (norm(b.name).startsWith(q) ? 0 : 1));
      active = Math.min(active, Math.max(0, options.length - 1));
      list.replaceChildren(
        ...(current ? [h("div.picker-opt", { onclick: () => choose(null) }, icon("close"), h("span.muted", "Quitar campeón"))] : []),
        ...options.slice(0, 80).map((c, i) =>
          h(
            `div.picker-opt${i === active ? ".active" : ""}${taken.has(c.id) && c.id !== current ? ".disabled" : ""}`,
            { onclick: () => choose(c.id), onmouseenter: () => { active = i; } },
            champImg(c.id, "sm"),
            h("span", c.name),
            hint ? h("span.meta", hint(c.id) || "") : null,
          ),
        ),
      );
    };
    const choose = (id) => {
      current = id;
      render();
      closePop();
      onChange?.(id);
    };
    input.addEventListener("input", () => { active = 0; draw(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { active = Math.min(options.length - 1, active + 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); draw(); e.preventDefault(); }
      else if (e.key === "Enter") { const c = options[active]; if (c && !(taken.has(c.id) && c.id !== current)) choose(c.id); e.preventDefault(); }
      else if (e.key === "Escape") closePop();
    });
    draw();
    document.body.append(pop);
    openPop = pop;
    input.focus();
  };

  btn.addEventListener("click", () => (openPop ? closePop() : open()));
  render();
  root.setValue = (id) => { current = id; render(); };
  root.getValue = () => current;
  return root;
}
