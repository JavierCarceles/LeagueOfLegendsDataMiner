/** Barras de scroll propias (27-11-2026: en Windows salía la gris del sistema, también en el menú con el zoom al 100 %).
 *
 *  La vista y el menú, las que se ven siempre, llevan un núcleo hextech dibujado encima (``HexBar``):
 *  · el pulgar es un cristal tallado con marco de oro, energía turquesa que fluye por dentro, reflejo de cristal y un
 *    sello hextech en el centro; con velocidad se calienta (turquesa → oro → fuego) y se estira un poco;
 *  · el raíl es un canal hundido con cantos de oro y gemas en los extremos; las secciones de la página son runas que
 *    se encienden al pasar el pulgar por encima (y saltan chispas si pasas desplazándote);
 *  · al desplazarte suelta chispas y deja una estela de energía por el canal (más cuanto más rápido); al chocar arriba
 *    o abajo, onda de choque; pinchar el raíl hace Flash (desaparece con un destello y aparece donde pinchas);
 *  · al pasar el ratón por la barra, motas de energía suben por el canal; el rótulo dice en qué sección estás.
 *  Los efectos se pintan en un lienzo que solo trabaja mientras hay algo que animar. El desplazamiento sigue siendo el
 *  nativo (rueda, teclado, táctil): esta barra solo se dibuja y se maneja con el ratón.
 *  El resto de cajas (tablas, listas, ventanas) usan ``::-webkit-scrollbar`` con el mismo oro (styles.css). Al
 *  desplazar cualquier cosa, su barra se enciende un momento (``.is-scrolling``). */

const LIVE_MS = 900; // cuánto sigue encendida tras el último desplazamiento
const MIN_THUMB = 56; // alto mínimo del pulgar (px)
const PAD = 11; // hueco arriba y abajo del canal para sus gemas (px)
const BAR_W = 18; // ancho de la barra (como .hx-bar en styles.css)
const FX_LEFT = 64; // el lienzo de efectos sobresale a la izquierda (las chispas vuelan hacia la página)
const FX_V = 18; // y por arriba y por abajo (ondas de choque)
const MARKS_EVERY_MS = 400; // las secciones se vuelven a medir como mucho cada tanto
const MAX_MARKS = 30;
const MAX_PARTICLES = 160;
const TRAIL_MS = 420;

const RGB = {
  teal: [10, 200, 185],
  cyan: [205, 250, 250],
  gold: [240, 200, 110],
  fire: [255, 128, 36],
  ember: [255, 72, 40],
  flash: [255, 236, 120],
  white: [255, 255, 255],
};

const timers = new WeakMap();
const bars = new Map(); // caja → su HexBar
const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)");

/** Cajas con barra dibujada: selector de la caja y de los títulos de sus secciones (las runas). */
const ENHANCED = [
  ["#main", ".card-title"],
  ["#sidebar", ".nav-section"],
];

export function initScrollbars() {
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });
  for (const [selector, marks] of ENHANCED) {
    const host = document.querySelector(selector);
    if (host) bars.set(host, new HexBar(host, marks));
  }
  window.addEventListener("resize", () => bars.forEach((bar) => bar.schedule(true)), { passive: true });
}

/** Los eventos de scroll no suben, pero se cogen en la fase de captura del documento: uno para toda la app. */
function onScroll(event) {
  const el = event.target === document ? document.scrollingElement : event.target;
  if (!(el instanceof Element)) return;
  if (!el.classList.contains("is-scrolling")) el.classList.add("is-scrolling");
  const bar = bars.get(el);
  bar?.setLive(true);
  bar?.schedule(false);
  clearTimeout(timers.get(el));
  timers.set(
    el,
    setTimeout(() => {
      el.classList.remove("is-scrolling");
      bar?.setLive(false);
    }, LIVE_MS),
  );
}

function node(tag, className, parent) {
  const el = document.createElement(tag);
  el.className = className;
  parent?.appendChild(el);
  return el;
}

const rand = (a, b) => a + Math.random() * (b - a);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

/** Un punto de luz difuminado por color, pintado una vez y reutilizado (mucho más barato que ``shadowBlur``). */
const sprites = {};
function sprite(name) {
  if (sprites[name]) return sprites[name];
  const size = 32;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  const [r, gr, b] = RGB[name];
  const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.18, `rgba(${r},${gr},${b},1)`);
  grad.addColorStop(0.45, `rgba(${r},${gr},${b},0.35)`);
  grad.addColorStop(1, `rgba(${r},${gr},${b},0)`);
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  sprites[name] = c;
  return c;
}

/** El sello del centro del pulgar: hexágono hextech con su gema. */
const SIGIL =
  '<svg class="hx-sigil" viewBox="0 0 12 14" aria-hidden="true">' +
  '<path class="hx-sigil-frame" d="M6 .8 11.2 3.8v6.4L6 13.2.8 10.2V3.8Z"/>' +
  '<path class="hx-sigil-gem" d="M6 3.6 8.6 7 6 10.4 3.4 7Z"/>' +
  '<path class="hx-sigil-ray" d="M6 .8V3.6M6 10.4v2.8"/></svg>';

class HexBar {
  constructor(host, markSelector) {
    this.host = host;
    this.markSelector = markSelector;
    this.marks = []; // [{top, title, y}]: top en px del contenido; y, su runa en la barra
    this.marksAt = 0;
    this.marksTimer = 0;
    this.raf = 0;
    this.full = false;
    this.drag = null;
    this.thumbH = 0;
    this.thumbY = PAD;
    this.trackH = 0;
    this.barH = 0;
    this.dpr = 1;
    this.over = { host: false, bar: false };
    this.peek = null; // runa bajo el ratón
    this.lit = new Set(); // runas encendidas (bajo el pulgar)
    // Efectos
    this.particles = [];
    this.trail = [];
    this.rings = [];
    this.velocity = 0; // px de barra por ms (con signo)
    this.heat = 0;
    this.last = null; // {y, t} última posición del pulgar
    this.fxRaf = 0;
    this.fxAt = 0;
    this.moteAt = 0;
    this.edgeHit = 0; // -1 arriba, 1 abajo (para no repetir la onda mientras sigues empujando)

    host.classList.add("hx-host");
    this.bar = node("div", "hx-bar off");
    this.bar.setAttribute("aria-hidden", "true");
    this.canvas = node("canvas", "hx-fx", this.bar);
    this.ctx = this.canvas.getContext("2d");
    const channel = node("div", "hx-channel", this.bar);
    node("span", "hx-cap top", channel);
    node("span", "hx-cap bottom", channel);
    this.ticks = node("div", "hx-ticks", this.bar);
    this.thumb = node("div", "hx-thumb", this.bar);
    const gem = node("div", "hx-gem", this.thumb);
    const glass = node("div", "hx-glass", gem);
    node("div", "hx-flow", glass);
    node("div", "hx-flow fast", glass);
    node("div", "hx-flow hot", glass);
    node("div", "hx-sheen", glass);
    gem.insertAdjacentHTML("beforeend", SIGIL);
    this.label = node("div", "hx-label", this.bar);
    document.body.appendChild(this.bar);

    this.bindHover();
    this.bindDrag();
    this.bindTrack();
    new ResizeObserver(() => this.schedule(true)).observe(host);
    // El contenido cambia sin que cambie la caja (vistas nuevas, listas que crecen): se vuelve a medir.
    new MutationObserver(() => this.schedule(true)).observe(host, { childList: true, subtree: true });
    this.schedule(true);
  }

  // ------------------------------------------------------------------ eventos

  /** Despierta (más ancha, canal y runas a la vista) con el ratón encima de la caja o de la propia barra. */
  bindHover() {
    const set = (key, value) => {
      this.over[key] = value;
      this.bar.classList.toggle("awake", this.over.host || this.over.bar);
      if (key === "bar" && value) this.wakeFx();
    };
    this.host.addEventListener("pointerenter", () => set("host", true));
    this.host.addEventListener("pointerleave", () => set("host", false));
    this.bar.addEventListener("pointerenter", () => set("bar", true));
    this.bar.addEventListener("pointerleave", () => set("bar", false));
    // La barra está fuera de la caja: la rueda encima de ella desplaza la caja.
    this.bar.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? this.host.clientHeight : 1;
        this.host.scrollBy({ top: event.deltaY * unit, behavior: "auto" });
      },
      { passive: false },
    );
  }

  bindDrag() {
    const thumb = this.thumb;
    thumb.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      thumb.setPointerCapture(event.pointerId);
      this.drag = { y: event.clientY, top: this.host.scrollTop };
      this.host.style.scrollBehavior = "auto"; // la vista tiene desplazamiento suave: al arrastrar, al instante
      this.bar.classList.add("dragging");
      this.setLive(true);
      this.burst(this.thumbY + this.thumbH / 2, 10, ["cyan", "teal"], 0.09);
    });
    thumb.addEventListener("pointermove", (event) => {
      if (!this.drag) return;
      const room = this.trackH - this.thumbH;
      const max = this.host.scrollHeight - this.host.clientHeight;
      if (room > 0) this.host.scrollTop = this.drag.top + ((event.clientY - this.drag.y) * max) / room;
    });
    const end = () => {
      if (!this.drag) return;
      this.drag = null;
      this.host.style.scrollBehavior = "";
      this.bar.classList.remove("dragging");
      this.setLive(this.host.classList.contains("is-scrolling"));
    };
    thumb.addEventListener("pointerup", end);
    thumb.addEventListener("pointercancel", end);
    thumb.addEventListener("lostpointercapture", end);
  }

  /** Pinchar el canal hace Flash hasta ahí; una runa, te lleva a su sección. Al pasar por una runa, su nombre. */
  bindTrack() {
    this.bar.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target.closest(".hx-thumb")) return;
      event.preventDefault();
      const host = this.host;
      const max = host.scrollHeight - host.clientHeight;
      const rune = event.target.closest(".hx-rune");
      if (rune) {
        const mark = this.marks[Number(rune.dataset.i)];
        if (!mark) return;
        this.ring(mark.y, "teal", 16, 520);
        this.burst(mark.y, 12, ["teal", "cyan"], 0.07);
        host.scrollTo({ top: clamp(mark.top - 12, 0, max), behavior: "smooth" });
        return;
      }
      const y = event.clientY - this.bar.getBoundingClientRect().top;
      const room = this.trackH - this.thumbH;
      const ratio = room > 0 ? (y - PAD - this.thumbH / 2) / room : 0;
      this.flash(this.thumbY + this.thumbH / 2, clamp(y, PAD, PAD + this.trackH));
      host.scrollTo({ top: clamp(ratio, 0, 1) * max, behavior: "auto" });
    });
    this.ticks.addEventListener("pointerover", (event) => {
      const rune = event.target.closest(".hx-rune");
      this.peek = rune ? Number(rune.dataset.i) : null;
      this.paintLabel();
    });
    this.ticks.addEventListener("pointerout", () => {
      this.peek = null;
      this.paintLabel();
    });
  }

  setLive(on) {
    const live = on || !!this.drag;
    if (live === this.bar.classList.contains("live")) return;
    this.bar.classList.toggle("live", live);
    this.paintLabel();
    if (live) this.wakeFx();
  }

  // ------------------------------------------------------------------ medidas y posición

  /** Repinta en el siguiente fotograma (``full``: también sitio, tamaño y secciones). */
  schedule(full) {
    this.full ||= full;
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => {
      this.raf = 0;
      const full = this.full;
      this.full = false;
      this.update(full);
    });
  }

  update(full) {
    const host = this.host;
    if (!host.isConnected) return;
    if (full) this.place();
    const view = host.clientHeight;
    const total = host.scrollHeight;
    const scrollable = view > 0 && total > view + 2 && this.trackH > MIN_THUMB;
    this.bar.classList.toggle("off", !scrollable);
    if (!scrollable) return;
    const thumbH = Math.max(MIN_THUMB, Math.round((this.trackH * view) / total));
    if (thumbH !== this.thumbH) {
      this.thumbH = thumbH;
      this.thumb.style.height = `${thumbH}px`;
    }
    const max = total - view;
    const ratio = clamp(host.scrollTop / max, 0, 1);
    this.move(PAD + (this.trackH - thumbH) * ratio, ratio);
    if (full) {
      const wait = MARKS_EVERY_MS - (performance.now() - this.marksAt);
      if (wait <= 0) this.measureMarks();
      else if (!this.marksTimer) {
        // Medida reciente: se repite al acabar la espera (si no, lo que se pinta justo después se quedaba sin runas).
        this.marksTimer = setTimeout(() => {
          this.marksTimer = 0;
          this.schedule(true);
        }, wait);
      }
    }
    this.lightRunes();
    this.paintLabel();
  }

  /** Coloca el pulgar y saca de ahí la velocidad (estela, chispas, calor y ondas al chocar con un extremo). */
  move(y, ratio) {
    const now = performance.now();
    const prev = this.last;
    this.thumbY = y;
    this.last = { y, t: now };
    let stretch = 1;
    if (prev && now > prev.t) {
      const dt = Math.min(64, now - prev.t);
      const v = (y - prev.y) / dt;
      this.velocity = this.velocity * 0.5 + v * 0.5;
      stretch = 1 + Math.min(0.09, Math.abs(this.velocity) * 0.06);
      if (y !== prev.y) {
        this.trail.push({ y: y + this.thumbH / 2, t: now, heat: this.heat });
        this.wakeFx();
      }
      const edge = ratio <= 0 ? -1 : ratio >= 1 ? 1 : 0;
      if (edge && edge !== this.edgeHit && Math.abs(v) > 0.05) this.shock(edge);
      this.edgeHit = edge;
    }
    this.thumb.style.transform = `translate3d(0, ${y}px, 0) scaleY(${stretch.toFixed(3)})`;
  }

  /** La barra encima del borde derecho de la caja (por dentro de su borde), con el lienzo de efectos alrededor. */
  place() {
    const host = this.host;
    const rect = host.getBoundingClientRect();
    const top = rect.top + host.clientTop;
    const right = rect.left + host.clientLeft + host.clientWidth;
    this.barH = host.clientHeight;
    this.trackH = Math.max(0, this.barH - 2 * PAD);
    Object.assign(this.bar.style, { top: `${top}px`, left: `${right - BAR_W}px`, height: `${this.barH}px` });
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = FX_LEFT + BAR_W + 6;
    const h = this.barH + 2 * FX_V;
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
      Object.assign(this.canvas.style, { width: `${w}px`, height: `${h}px`, left: `${-FX_LEFT}px`, top: `${-FX_V}px` });
      this.dpr = dpr;
    }
  }

  /** Runas, como un minimapa: cada sección en su sitio de la página (está a la vista cuando su runa queda bajo el
   *  pulgar). Dos en la misma altura (tarjetas lado a lado) o casi: solo la primera. */
  measureMarks() {
    this.marksAt = performance.now();
    const host = this.host;
    const hostTop = host.getBoundingClientRect().top + host.clientTop;
    const marks = [];
    for (const el of host.querySelectorAll(this.markSelector)) {
      const rect = el.getBoundingClientRect();
      if (!rect.height) continue; // oculta (pestaña cerrada, menú plegado)
      const title = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (!title) continue;
      marks.push({ top: rect.top - hostTop + host.scrollTop, title: title.length > 42 ? `${title.slice(0, 40)}…` : title });
      if (marks.length >= MAX_MARKS) break;
    }
    const total = host.scrollHeight || 1;
    const ticks = this.ticks;
    while (ticks.children.length > marks.length) ticks.lastChild.remove();
    let last = -Infinity;
    marks.forEach((mark, i) => {
      const rune = ticks.children[i] || node("span", "hx-rune", ticks);
      mark.y = PAD + this.trackH * clamp(mark.top / total, 0, 1);
      rune.dataset.i = String(i);
      rune.style.transform = `translate3d(0, ${mark.y}px, 0)`;
      rune.hidden = mark.y - last < 9; // dos casi juntas: solo la primera
      if (!rune.hidden) last = mark.y;
    });
    this.marks = marks;
    this.lit.clear();
    for (const rune of ticks.children) rune.classList.remove("lit");
  }

  /** Runas bajo el pulgar, encendidas; la que se enciende mientras te desplazas suelta chispas. */
  lightRunes() {
    const top = this.thumbY;
    const bottom = this.thumbY + this.thumbH;
    const live = this.bar.classList.contains("live");
    this.marks.forEach((mark, i) => {
      const rune = this.ticks.children[i];
      if (!rune || rune.hidden) return;
      const on = mark.y >= top && mark.y <= bottom;
      if (on === this.lit.has(i)) return;
      rune.classList.toggle("lit", on);
      if (on) {
        this.lit.add(i);
        if (live) this.burst(mark.y, 7, ["teal", "cyan", "gold"], 0.06);
      } else this.lit.delete(i);
    });
  }

  /** El rótulo: la runa bajo el ratón o, mientras te desplazas, la sección en la que estás (junto al pulgar). */
  paintLabel() {
    const label = this.label;
    let mark = null;
    let y = 0;
    if (this.peek != null && this.marks[this.peek]) {
      mark = this.marks[this.peek];
      y = mark.y;
    } else if (this.bar.classList.contains("live") && this.marks.length) {
      const at = this.host.scrollTop + 48;
      for (const m of this.marks) {
        if (m.top <= at) mark = m;
        else break;
      }
      mark ||= this.marks[0];
      y = this.thumbY + this.thumbH / 2;
    }
    label.classList.toggle("show", !!mark);
    if (!mark) return;
    if (label.textContent !== mark.title) label.textContent = mark.title;
    label.style.transform = `translate3d(0, ${y}px, 0) translateY(-50%)`;
  }

  // ------------------------------------------------------------------ efectos

  /** Chispas desde un punto del canal. ``speed`` en px/ms. */
  burst(y, count, colors, speed = 0.08) {
    if (calm?.matches) return;
    const cx = FX_LEFT + BAR_W / 2;
    for (let i = 0; i < count && this.particles.length < MAX_PARTICLES; i++) {
      const angle = rand(0, Math.PI * 2);
      const s = rand(0.3, 1) * speed;
      this.particles.push({
        x: cx + rand(-2, 2),
        y: y + FX_V + rand(-3, 3),
        vx: Math.cos(angle) * s - rand(0, speed * 0.4),
        vy: Math.sin(angle) * s,
        life: 0,
        max: rand(380, 820),
        size: rand(5, 11),
        sprite: colors[Math.floor(Math.random() * colors.length)],
      });
    }
    this.wakeFx();
  }

  ring(y, color, radius, ms) {
    if (calm?.matches) return;
    this.rings.push({ y: y + FX_V, color, radius, max: ms, life: 0 });
    this.wakeFx();
  }

  /** Onda de choque al llegar arriba (-1) o abajo (1). */
  shock(edge) {
    const y = edge < 0 ? PAD - 4 : PAD + this.trackH + 4;
    const hot = this.heat > 0.5;
    this.ring(y, hot ? "fire" : "gold", 22, 520);
    this.ring(y, "cyan", 12, 360);
    this.burst(y, 16, hot ? ["fire", "gold", "ember"] : ["gold", "cyan", "teal"], 0.12);
  }

  /** Flash: destello donde estaba el pulgar y donde aparece. */
  flash(from, to) {
    this.burst(from, 18, ["flash", "white", "gold"], 0.14);
    this.ring(from, "flash", 18, 380);
    this.burst(to, 22, ["flash", "white", "gold"], 0.16);
    this.ring(to, "flash", 24, 480);
    this.bar.classList.remove("flashed");
    void this.bar.offsetWidth; // vuelve a lanzar la animación
    this.bar.classList.add("flashed");
  }

  wakeFx() {
    if (this.fxRaf || calm?.matches) return;
    this.fxAt = performance.now();
    this.fxRaf = requestAnimationFrame((t) => this.fxFrame(t));
  }

  fxFrame(now) {
    this.fxRaf = 0;
    const dt = clamp(now - this.fxAt, 1, 48);
    this.fxAt = now;
    const live = this.bar.classList.contains("live");
    // Calor: sube con la velocidad y baja solo.
    const target = live ? clamp(Math.abs(this.velocity) / 1.1, 0, 1) : 0;
    this.heat += (target - this.heat) * (target > this.heat ? 0.25 : 0.06);
    if (!live) this.velocity *= 0.8;
    this.bar.style.setProperty("--hx-heat", this.heat.toFixed(3));
    this.emit(dt, live, now);
    this.draw(now, dt);
    const busy = this.particles.length || this.rings.length || this.trail.length || this.heat > 0.01 || this.over.bar || live;
    if (busy && !this.bar.classList.contains("off")) this.fxRaf = requestAnimationFrame((t) => this.fxFrame(t));
    else {
      this.heat = 0;
      this.bar.style.setProperty("--hx-heat", "0");
      this.ctx.setTransform(1, 0, 0, 1, 0, 0);
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }
  }

  /** Chispas al moverse (desde el extremo que va detrás, más con velocidad) y motas con el ratón encima. */
  emit(dt, live, now) {
    const speed = Math.abs(this.velocity);
    const cx = FX_LEFT + BAR_W / 2;
    if (live && speed > 0.02) {
      const n = Math.min(5, speed * dt * 0.09 + (Math.random() < 0.5 ? 1 : 0));
      const down = this.velocity > 0;
      const tailY = FX_V + (down ? this.thumbY : this.thumbY + this.thumbH);
      const hot = this.heat > 0.55 ? ["fire", "ember", "gold"] : this.heat > 0.25 ? ["gold", "cyan", "fire"] : ["teal", "cyan", "gold"];
      for (let i = 0; i < n && this.particles.length < MAX_PARTICLES; i++) {
        this.particles.push({
          x: cx + rand(-3, 3),
          y: tailY + rand(-2, 2),
          vx: -rand(0.01, 0.07) - speed * 0.02,
          vy: (down ? -1 : 1) * rand(0.02, 0.09) * (0.6 + speed),
          life: 0,
          max: rand(350, 750) * (0.7 + this.heat * 0.6),
          size: rand(4, 9) * (0.8 + this.heat * 0.5),
          sprite: hot[Math.floor(Math.random() * hot.length)],
        });
      }
    }
    if (this.over.bar && now - this.moteAt > 220 && this.particles.length < MAX_PARTICLES) {
      this.moteAt = now;
      this.particles.push({
        x: cx + rand(-1.5, 1.5),
        y: FX_V + PAD + this.trackH * rand(0.2, 1),
        vx: rand(-0.006, 0.002),
        vy: -rand(0.025, 0.06),
        life: 0,
        max: rand(1100, 1900),
        size: rand(4, 7),
        sprite: Math.random() < 0.75 ? "teal" : "gold",
        mote: true,
      });
    }
  }

  draw(now, dt) {
    const { ctx, canvas } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.globalCompositeOperation = "lighter";
    const cx = FX_LEFT + BAR_W / 2;

    // Estela de energía por el canal: más gruesa y brillante cuanto más reciente.
    this.trail = this.trail.filter((p) => now - p.t < TRAIL_MS);
    ctx.lineCap = "round";
    for (let i = 1; i < this.trail.length; i++) {
      const a = this.trail[i - 1];
      const b = this.trail[i];
      const fade = 1 - (now - b.t) / TRAIL_MS;
      const [r, g, bl] = b.heat > 0.5 ? RGB.fire : b.heat > 0.25 ? RGB.gold : RGB.teal;
      ctx.beginPath();
      ctx.moveTo(cx, a.y + FX_V);
      ctx.lineTo(cx, b.y + FX_V);
      ctx.strokeStyle = `rgba(${r},${g},${bl},${(0.22 * fade).toFixed(3)})`;
      ctx.lineWidth = 7;
      ctx.stroke();
      ctx.strokeStyle = `rgba(${Math.min(255, r + 120)},${Math.min(255, g + 60)},${Math.min(255, bl + 60)},${(0.85 * fade).toFixed(3)})`;
      ctx.lineWidth = 1.6;
      ctx.stroke();
    }

    // Ondas: un anillo que se abre y se apaga.
    this.rings = this.rings.filter((ring) => (ring.life += dt) < ring.max);
    for (const ring of this.rings) {
      const k = ring.life / ring.max;
      const [r, g, b] = RGB[ring.color];
      const radius = ring.radius * (0.25 + k);
      ctx.strokeStyle = `rgba(${r},${g},${b},${((1 - k) * 0.9).toFixed(3)})`;
      ctx.lineWidth = 2.2 * (1 - k) + 0.4;
      ctx.beginPath();
      ctx.ellipse(cx, ring.y, radius, radius * 0.75, 0, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Chispas y motas.
    const alive = [];
    for (const p of this.particles) {
      p.life += dt;
      if (p.life >= p.max) continue;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.mote) p.x += Math.sin((p.life + p.y) / 140) * 0.06;
      else {
        p.vx *= 0.985;
        p.vy = p.vy * 0.985 + 0.00012 * dt; // un poco de peso
      }
      const k = p.life / p.max;
      const size = p.size * (p.mote ? 1 : 1 - k * 0.5);
      ctx.globalAlpha = p.mote ? Math.sin(Math.PI * k) * 0.9 : (1 - k) ** 1.4;
      ctx.drawImage(sprite(p.sprite), p.x - size / 2, p.y - size / 2, size, size);
      alive.push(p);
    }
    ctx.globalAlpha = 1;
    this.particles = alive;
  }
}
