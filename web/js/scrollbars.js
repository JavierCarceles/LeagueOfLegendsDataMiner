/** Barras de scroll propias (27-11-2026: en Windows salía la gris del sistema, también en el menú con el zoom al 100 %).
 *
 *  La vista y el menú, las que se ven siempre, llevan un núcleo hextech pintado en un lienzo encima de su caja
 *  (``HexBar``). El pulgar es una joya:
 *  · gema tallada con marco de oro biselado, garras de oro y una gema en cada punta;
 *  · dentro, plasma vivo (calculado punto a punto) que fluye y se arremolina; con velocidad se calienta (turquesa →
 *    oro → fuego) y el pulgar se estira un poco;
 *  · en el centro, un medallón hextech que gira con un orbe que late; al desplazarte saltan rayos del orbe a las
 *    puntas (más cuanto más rápido) y, con el ratón encima, chisporrotea de vez en cuando.
 *  Alrededor: canal hundido con cantos de oro y gemas en los extremos; las secciones de la página son runas que se
 *  encienden al pasar el pulgar (con chispas si pasas desplazándote); estela de energía y chispas al moverse, onda de
 *  choque al llegar arriba o abajo y Flash al pinchar el raíl (desaparece con un destello y aparece donde pinchas). El
 *  rótulo dice en qué sección estás. El plasma no para nunca (en reposo, despacio y a 30 fotogramas por segundo).
 *  El desplazamiento sigue siendo el nativo (rueda, teclado, táctil): esta barra solo se dibuja y se maneja con el
 *  ratón. El resto de cajas (tablas, listas, ventanas) usan ``::-webkit-scrollbar`` en oro (styles.css). Al desplazar
 *  cualquier cosa, su barra se enciende un momento (``.is-scrolling``).
 *
 *  Esta es la piel Hextech; los temas por línea cambian la forma del pulgar, el medallón, lo que suelta y lo que flota
 *  (placa forjada, rama, daga, cartucho, cadena con farol, escudo de hielo, cápsula: scrollbar-skins.js) y su raíl
 *  corre al desplazarte (cinta de balas, cadena, enredadera…: theme-bars.css). */

import { COOL, HOT, MEDALLIONS, RGB, SHAPES, SKIN, THUMBS, applySkin, clamp, drawHitMarker, lerp, mixRgb, paletteAt, rand, rgba, sprite } from "./scrollbar-skins.js";

const LIVE_MS = 900; // cuánto sigue encendida tras el último desplazamiento
const MIN_THUMB = 64; // alto mínimo del pulgar (px)
const PAD = 13; // hueco arriba y abajo del canal para sus gemas y las garras del pulgar (px)
const BAR_W = 22; // ancho de la barra (como .hx-bar en styles.css)
const FX_LEFT = 70; // el lienzo sobresale a la izquierda (las chispas vuelan hacia la página)
const FX_V = 22; // y por arriba y por abajo (ondas de choque, garras)
const WIDTH = { rest: 12, awake: 14, hover: 17 }; // ancho del pulgar según el estado
const MARKS_EVERY_MS = 400; // las secciones se vuelven a medir como mucho cada tanto
const MAX_MARKS = 30;
const MAX_PARTICLES = 170;
const TRAIL_MS = 420;
const PLASMA_W = 10; // resolución del plasma (se escala con suavizado: queda orgánico)

const timers = new WeakMap();
const bars = new Map(); // caja → su HexBar
// Con un tema que no es el estándar las animaciones van siempre, aunque Windows tenga apagados los «Efectos de
// animación» (Chrome lo pasa a la web como prefers-reduced-motion y la barra se quedaba congelada en su PC,
// 27-11-2026): elegir el tema ya es pedirlas. El estándar no lleva esta barra.
const calm = null;

/** Cajas con barra dibujada: selector de la caja y de los títulos de sus secciones (las runas). */
const ENHANCED = [
  ["#main", ".card-title"],
  ["#sidebar", ".nav-section"],
];

export function initScrollbars() {
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });
  window.addEventListener("resize", () => bars.forEach((bar) => bar.schedule(true)), { passive: true });
}

/** Barras de cristal en la vista y el menú, con la piel de cada tema (theme.js); con el estándar, las del sistema. */
export function setHexBars(on, themeId = "hextech") {
  if (on) applySkin(themeId);
  if (on && bars.size) bars.forEach((bar) => bar.wakeFx()); // otro tema: se repinta con su piel
  if (on && !bars.size) {
    for (const [selector, marks] of ENHANCED) {
      const host = document.querySelector(selector);
      if (host) bars.set(host, new HexBar(host, marks));
    }
  } else if (!on && bars.size) {
    bars.forEach((bar) => bar.destroy());
    bars.clear();
  }
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

/** Nombre de una sección: el texto de su título hasta el separador o el primer botón (sin «Todas tus repeticiones» y
 *  demás botones que llevan algunos títulos dentro). */
function markTitle(el) {
  let text = "";
  for (const child of el.childNodes) {
    if (child.nodeType === Node.ELEMENT_NODE && (child.matches(".spacer, button, a, select, input, .btn") || child.querySelector?.("button, a, select"))) break;
    text += child.textContent || "";
  }
  return text.replace(/\s+/g, " ").trim();
}

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
    this.over = { host: false, bar: false, thumb: false };
    this.peek = null; // runa bajo el ratón
    this.lit = new Set(); // runas encendidas (bajo el pulgar)
    // Dibujo y efectos
    this.w = WIDTH.rest; // ancho actual del pulgar (se acerca al de su estado)
    this.stretch = 1;
    this.t = Math.random() * 100; // tiempo del plasma
    this.spin = 0; // giro del medallón
    this.flashAt = -1e9;
    this.particles = [];
    this.trail = [];
    this.rings = [];
    this.bolts = [];
    this.boltAt = 0;
    this.velocity = 0; // px de barra por ms (con signo)
    this.heat = 0;
    this.last = null; // {y, t} última posición del pulgar
    this.fxRaf = 0;
    this.fxAt = 0;
    this.moteAt = 0;
    this.edgeHit = 0; // -1 arriba, 1 abajo (para no repetir la onda mientras sigues empujando)
    this.plasma = document.createElement("canvas");
    this.plasmaCtx = this.plasma.getContext("2d");
    // Para las pieles (scrollbar-skins.js): dónde cae el canal en el lienzo, hacia dónde mira la punta (daga, bala:
    // 1 arriba, -1 abajo; pasa por 0 al girar) y cuándo soltó lo último.
    this.cx = FX_LEFT + BAR_W / 2;
    this.fxv = FX_V;
    this.pad = PAD;
    this.face = 1;
    this.faceWant = 1;
    this.sprayAt = 0;
    this.idleAt = 0;
    this.muzzleAt = -1e9;
    this.hitAt = -1e9;
    this.hitY = 0;
    this.railShift = null;

    this.ac = new AbortController(); // quita todos los escuchadores al cambiar de tema
    host.classList.add("hx-host");
    this.bar = node("div", "hx-bar off");
    this.bar.setAttribute("aria-hidden", "true");
    const channel = node("div", "hx-channel", this.bar);
    node("span", "hx-cap top", channel);
    node("span", "hx-cap bottom", channel);
    this.canvas = node("canvas", "hx-fx", this.bar);
    this.ctx = this.canvas.getContext("2d");
    this.ticks = node("div", "hx-ticks", this.bar);
    this.thumb = node("div", "hx-thumb", this.bar); // solo para cogerlo: se pinta en el lienzo
    this.label = node("div", "hx-label", this.bar);
    document.body.appendChild(this.bar);

    this.bindHover();
    this.bindDrag();
    this.bindTrack();
    this.ro = new ResizeObserver(() => this.schedule(true));
    this.ro.observe(host);
    // El contenido cambia sin que cambie la caja (vistas nuevas, listas que crecen): se vuelve a medir.
    this.mo = new MutationObserver(() => this.schedule(true));
    this.mo.observe(host, { childList: true, subtree: true });
    this.schedule(true);
  }

  /** Se quita entera (tema estándar): la caja vuelve a su barra del sistema. */
  destroy() {
    this.dead = true;
    this.ac.abort();
    this.ro.disconnect();
    this.mo.disconnect();
    cancelAnimationFrame(this.raf);
    cancelAnimationFrame(this.fxRaf);
    clearTimeout(this.marksTimer);
    this.bar.remove();
    this.host.classList.remove("hx-host");
    this.host.style.scrollBehavior = "";
  }

  // ------------------------------------------------------------------ eventos

  /** Despierta (más ancha, canal y runas a la vista, plasma en marcha) con el ratón encima de la caja o de la barra. */
  bindHover() {
    const set = (key, value) => {
      this.over[key] = value;
      this.bar.classList.toggle("awake", this.over.host || this.over.bar);
      this.wakeFx();
    };
    this.host.addEventListener("pointerenter", () => set("host", true), { signal: this.ac.signal });
    this.host.addEventListener("pointerleave", () => set("host", false), { signal: this.ac.signal });
    this.bar.addEventListener("pointerenter", () => set("bar", true), { signal: this.ac.signal });
    this.bar.addEventListener("pointerleave", () => set("bar", false), { signal: this.ac.signal });
    this.thumb.addEventListener("pointerenter", () => set("thumb", true), { signal: this.ac.signal });
    this.thumb.addEventListener("pointerleave", () => set("thumb", false), { signal: this.ac.signal });
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
      this.burst(this.thumbY + this.thumbH / 2, 12, ["cyan", "teal", "white"], 0.1);
      this.ring(this.thumbY + this.thumbH / 2, "cyan", 14, 360);
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
      this.wakeFx();
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
        this.ring(mark.y, "teal", 18, 560);
        this.burst(mark.y, 14, ["teal", "cyan"], 0.08);
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
    if (this.dead) return;
    const live = on || !!this.drag;
    if (live === this.bar.classList.contains("live")) return;
    this.bar.classList.toggle("live", live);
    if (live) SKIN.fx?.start?.(this); // ADC: fogonazo al empezar a desplazarte
    this.paintLabel();
    this.wakeFx();
  }

  // ------------------------------------------------------------------ medidas y posición

  /** Repinta en el siguiente fotograma (``full``: también sitio, tamaño y secciones). */
  schedule(full) {
    this.full ||= full;
    if (this.raf || this.dead) return;
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
    // El raíl de los temas por línea (cinta de balas, cadena, enredadera…) corre al desplazarte.
    const shift = `${(-host.scrollTop * 0.3).toFixed(1)}px`;
    if (shift !== this.railShift) {
      this.railShift = shift;
      this.bar.style.setProperty("--hx-shift", shift);
    }
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
    this.wakeFx();
  }

  /** Coloca el pulgar y saca de ahí la velocidad (estela, chispas, calor y ondas al chocar con un extremo). */
  move(y, ratio) {
    const now = performance.now();
    const prev = this.last;
    this.thumbY = y;
    this.last = { y, t: now };
    if (prev && now > prev.t) {
      const dt = Math.min(64, now - prev.t);
      const v = (y - prev.y) / dt;
      this.velocity = this.velocity * 0.5 + v * 0.5;
      if (y !== prev.y) this.trail.push({ y: y + this.thumbH / 2, t: now, heat: this.heat });
      const edge = ratio <= 0 ? -1 : ratio >= 1 ? 1 : 0;
      if (edge && edge !== this.edgeHit && Math.abs(v) > 0.05) this.shock(edge);
      this.edgeHit = edge;
    }
    this.thumb.style.transform = `translate3d(0, ${y}px, 0)`;
  }

  /** La barra encima del borde derecho de la caja (por dentro de su borde), con el lienzo alrededor. */
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
      const title = markTitle(el);
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
      rune.hidden = mark.y - last < 10; // dos casi juntas: solo la primera
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
        if (live) this.burst(mark.y, 8, ["teal", "cyan", "gold"], 0.07);
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
        size: rand(5, 12),
        sprite: colors[Math.floor(Math.random() * colors.length)],
      });
    }
    this.wakeFx();
  }

  ring(y, color, radius, ms, shape) {
    this.ringAt(y + FX_V, color, radius, ms, shape);
  }

  /** Onda en ``y`` del lienzo (``shape``: «hex» para la del escudo del guardián). */
  ringAt(y, color, radius, ms, shape) {
    if (calm?.matches) return;
    this.rings.push({ y, color, radius, max: ms, life: 0, shape });
    this.wakeFx();
  }

  /** Una partícula más (con tope). */
  add(p) {
    if (this.particles.length < MAX_PARTICLES) this.particles.push(p);
    this.wakeFx();
  }

  /** Extremo de atrás del pulgar en el lienzo (el culote de la bala, el pomo de la daga). */
  tailY() {
    const half = this.thumbH / 2;
    return FX_V + this.thumbY + half + half * Math.sign(this.face || 1);
  }

  /** Onda de choque al llegar arriba (-1) o abajo (1). */
  shock(edge) {
    const y = edge < 0 ? PAD - 4 : PAD + this.trackH + 4;
    const hot = this.heat > 0.5;
    if (SKIN.fx?.shock) {
      SKIN.fx.shock(this, y + FX_V, hot);
      return;
    }
    this.ring(y, hot ? "fire" : "gold", 26, 560);
    this.ring(y, "cyan", 14, 380);
    this.burst(y, 20, hot ? ["fire", "gold", "ember"] : ["gold", "cyan", "teal"], 0.13);
  }

  /** Flash: destello donde estaba el pulgar y donde aparece. */
  flash(from, to) {
    this.burst(from, 20, ["flash", "white", "gold"], 0.15);
    this.ring(from, "flash", 20, 380);
    this.burst(to, 26, ["flash", "white", "gold"], 0.17);
    this.ring(to, "flash", 28, 500);
    this.flashAt = performance.now();
  }

  /** Un rayo quebrado entre dos puntos (coordenadas del lienzo). */
  bolt(x0, y0, x1, y1, color, life) {
    const points = [[x0, y0]];
    const steps = 5 + Math.floor(Math.random() * 4);
    const len = Math.hypot(x1 - x0, y1 - y0) || 1;
    const nx = -(y1 - y0) / len;
    const ny = (x1 - x0) / len;
    for (let i = 1; i < steps; i++) {
      const k = i / steps;
      const jitter = rand(-1, 1) * Math.min(5, len * 0.12) * Math.sin(Math.PI * k);
      points.push([lerp(x0, x1, k) + nx * jitter, lerp(y0, y1, k) + ny * jitter]);
    }
    points.push([x1, y1]);
    this.bolts.push({ points, color, life: 0, max: life });
  }

  /** Rayos del orbe a las puntas o a los lados del cristal (y, con mucho calor, hasta el canal). */
  sparkBolts(count) {
    const cx = FX_LEFT + BAR_W / 2;
    const cy = FX_V + this.thumbY + this.thumbH / 2;
    const half = (this.thumbH * this.stretch) / 2;
    const color = this.heat > 0.55 ? "fire" : this.heat > 0.25 ? "gold" : "cyan";
    for (let i = 0; i < count; i++) {
      const up = Math.random() < 0.5 ? -1 : 1;
      const roll = Math.random();
      let x1;
      let y1;
      if (roll < 0.45) {
        x1 = cx;
        y1 = cy + up * (half - 2); // a una punta
      } else if (roll < 0.85 || this.heat < 0.5) {
        x1 = cx + (Math.random() < 0.5 ? -1 : 1) * (this.w / 2 - 2);
        y1 = cy + up * rand(8, Math.max(10, half - 8)); // a un lado del cristal
      } else {
        x1 = cx + rand(-2, 2);
        y1 = cy + up * (half + rand(10, 40)); // se sale por el canal
      }
      this.bolt(cx, cy, x1, y1, color, rand(80, 170));
    }
  }

  wakeFx() {
    if (this.fxRaf || this.dead) return;
    this.fxAt = performance.now();
    this.fxRaf = requestAnimationFrame((t) => this.fxFrame(t));
  }

  fxFrame(now) {
    // Mientras se pinta el fotograma, lo que se encienda (partículas, ondas) no abre otro bucle: ya sigue este.
    this.fxRaf = -1;
    if (this.dead || this.bar.classList.contains("off")) {
      this.fxRaf = 0;
      return;
    }
    const still = !!calm?.matches;
    const live = this.bar.classList.contains("live");
    const awake = this.over.host || this.over.bar;
    // Lo que flota en reposo (luciérnagas, nieve, ascuas) no cuenta: con eso solo, sigue a 30 fotogramas.
    const busy = this.rings.length || this.trail.length || this.bolts.length || this.heat > 0.01 || this.particles.some((p) => !p.idle);
    // En reposo el plasma sigue fluyendo, pero a 30 fotogramas por segundo (no hace falta más y gasta la mitad).
    if (!still && !awake && !live && !busy && !this.drag && now - this.fxAt < 32) {
      this.fxRaf = requestAnimationFrame((t) => this.fxFrame(t));
      return;
    }
    const dt = clamp(now - this.fxAt, 1, 48);
    this.fxAt = now;
    // Calor: sube con la velocidad y baja solo.
    const target = live ? clamp(Math.abs(this.velocity) / 1.1, 0, 1) : 0;
    this.heat += (target - this.heat) * (target > this.heat ? 0.25 : 0.06);
    if (!live) this.velocity *= 0.8;
    // Ancho y estiramiento, hacia los de su estado.
    const wTarget = this.over.thumb || this.drag ? WIDTH.hover : awake || live ? WIDTH.awake : WIDTH.rest;
    this.w += (wTarget - this.w) * (still ? 1 : 0.22);
    const sTarget = 1 + Math.min(0.09, Math.abs(this.velocity) * 0.06);
    this.stretch += (sTarget - this.stretch) * 0.3;
    // Hacia dónde mira la punta (daga, bala): hacia donde te desplazas; al cambiar, gira (pasa por 0).
    if (live && Math.abs(this.velocity) > 0.03) this.faceWant = this.velocity > 0 ? -1 : 1;
    this.face += (this.faceWant - this.face) * (still ? 1 : 0.16);
    // Tiempo del plasma y giro del medallón: más deprisa al desplazarte o arrastrar.
    if (!still) {
      // Siempre en movimiento (en reposo, despacio); más deprisa con el ratón encima, al desplazarte o al arrastrar.
      const pace = 0.00045 + (awake || live ? 0.0005 : 0) + (live ? 0.0022 + this.heat * 0.004 : 0) + (this.drag ? 0.002 : 0);
      this.t += dt * pace;
      this.spin += dt * (0.0005 + (live ? 0.004 + this.heat * 0.008 : 0) + (this.drag ? 0.004 : 0));
      this.emit(dt, live, awake, now);
    }
    this.draw(now, dt, live, awake);
    const moving = Math.abs(wTarget - this.w) > 0.05 || Math.abs(sTarget - this.stretch) > 0.003 || Math.abs(this.faceWant - this.face) > 0.01;
    // Sin «reducir movimiento», siempre en marcha (el navegador lo para solo con la pestaña oculta).
    this.fxRaf = !still || moving ? requestAnimationFrame((t) => this.fxFrame(t)) : 0;
  }

  /** Chispas al moverse (desde el extremo que va detrás), rayos del orbe y motas con el ratón encima. Los temas por
   *  línea sueltan lo suyo (``SKIN.fx``: hojas, casquillos, burbujas…) y tienen algo flotando siempre. */
  emit(dt, live, awake, now) {
    const speed = Math.abs(this.velocity);
    const cx = FX_LEFT + BAR_W / 2;
    const fx = SKIN.fx;
    if (fx) {
      if (live && speed > 0.02) {
        const down = this.velocity > 0;
        const tailY = FX_V + (down ? this.thumbY : this.thumbY + this.thumbH);
        let n = Math.min(4, speed * dt * 0.07 + (Math.random() < 0.45 ? 1 : 0));
        if (fx.every) {
          n = now - this.sprayAt > fx.every * (1 - Math.min(0.5, speed * 0.4)) ? 1 : 0;
          if (n) this.sprayAt = now;
        }
        for (let i = 0; i < n; i++) for (const p of fx.spray(this, cx + rand(-3, 3), tailY + rand(-2, 2), down ? 1 : -1)) if (p) this.add(p);
      }
      if (SKIN.bolts) this.boltsFrom(now, live, awake);
      const [rest, hover] = fx.idleMs;
      if (now - this.idleAt > (awake || live ? hover : rest) && this.particles.length < MAX_PARTICLES * 0.6) {
        this.idleAt = now;
        const p = fx.idle(this);
        if (p) this.particles.push(Object.assign(p, { idle: true }));
      }
      return;
    }
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
          size: rand(4, 10) * (0.8 + this.heat * 0.5),
          sprite: hot[Math.floor(Math.random() * hot.length)],
        });
      }
    }
    this.boltsFrom(now, live, awake);
    if (this.over.bar && now - this.moteAt > 200 && this.particles.length < MAX_PARTICLES) {
      this.moteAt = now;
      this.particles.push({
        x: cx + rand(-1.5, 1.5),
        y: FX_V + PAD + this.trackH * rand(0.2, 1),
        vx: rand(-0.006, 0.002),
        vy: -rand(0.025, 0.06),
        life: 0,
        max: rand(1100, 1900),
        size: rand(4, 8),
        sprite: Math.random() < 0.75 ? "teal" : "gold",
        mote: true,
      });
    }
  }

  /** Rayos: seguidos al desplazarte o arrastrar; con el ratón encima, un chasquido de vez en cuando. */
  boltsFrom(now, live, awake) {
    const every = this.drag ? 55 : live ? 85 - this.heat * 40 : awake ? rand(900, 2600) : Infinity;
    if (now - this.boltAt > every) {
      this.boltAt = now;
      this.sparkBolts(live || this.drag ? 1 + (this.heat > 0.5 ? 1 : 0) : 1);
    }
  }

  // ------------------------------------------------------------------ dibujo

  draw(now, dt, live, awake) {
    const { ctx, canvas } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.drawTrail(now);
    this.drawThumb(now, live, awake);
    this.drawRings(dt);
    this.drawBolts(dt);
    this.drawParticles(dt);
    drawHitMarker(this, ctx, now);
  }

  drawTrail(now) {
    const ctx = this.ctx;
    const cx = FX_LEFT + BAR_W / 2;
    ctx.globalCompositeOperation = "lighter";
    this.trail = this.trail.filter((p) => now - p.t < TRAIL_MS);
    ctx.lineCap = "round";
    for (let i = 1; i < this.trail.length; i++) {
      const a = this.trail[i - 1];
      const b = this.trail[i];
      const fade = 1 - (now - b.t) / TRAIL_MS;
      const color = b.heat > 0.5 ? RGB.fire : b.heat > 0.25 ? RGB.gold : RGB.teal;
      ctx.beginPath();
      ctx.moveTo(cx, a.y + FX_V);
      ctx.lineTo(cx, b.y + FX_V);
      ctx.strokeStyle = rgba(color, 0.24 * fade);
      ctx.lineWidth = 8;
      ctx.stroke();
      ctx.strokeStyle = rgba(mixRgb(color, RGB.white, 0.5), 0.85 * fade);
      ctx.lineWidth = 1.6;
      ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";
  }

  /** El plasma del cristal: suma de ondas que fluyen hacia arriba y se arremolinan alrededor del orbe. */
  paintPlasma(h, bright) {
    const pw = PLASMA_W;
    const ph = clamp(Math.round(h / 3), 8, 260);
    const c = this.plasma;
    if (c.width !== pw || c.height !== ph) {
      c.width = pw;
      c.height = ph;
      this.plasmaImg = this.plasmaCtx.createImageData(pw, ph);
    }
    const data = this.plasmaImg.data;
    const t = this.t;
    const heat = this.heat;
    const midY = ph / 2;
    let i = 0;
    for (let y = 0; y < ph; y++) {
      const py = y * 3; // en px del pulgar
      for (let x = 0; x < pw; x++) {
        const u = x / (pw - 1) - 0.5;
        const dy = (y - midY) * 3;
        const r = Math.hypot(u * 26, dy * 0.5);
        let v =
          Math.sin(u * 7 + t * 1.3 + py * 0.02) +
          Math.sin(py * 0.075 + t * 2.4) +
          Math.sin((u * 5 + py * 0.045) * 1.7 - t * 1.6) +
          Math.sin(r * 0.32 - t * 3.1);
        v = (v + 4) / 8;
        v = v * v * (3 - 2 * v); // más contraste: venas brillantes
        const edge = 1 - Math.abs(u) * 1.5; // más oscuro en los bordes: profundidad
        v = clamp(v * (0.55 + 0.55 * edge) * bright, 0, 1);
        const cool = paletteAt(COOL, v);
        const col = heat > 0.01 ? mixRgb(cool, paletteAt(HOT, v), heat) : cool;
        data[i++] = col[0];
        data[i++] = col[1];
        data[i++] = col[2];
        data[i++] = 255;
      }
    }
    this.plasmaCtx.putImageData(this.plasmaImg, 0, 0);
  }

  /** Contorno de la gema (puntas arriba y abajo), metido ``inset`` px. */
  gemPath(cx, top, h, w, tip, inset) {
    const ctx = this.ctx;
    const half = w / 2 - inset;
    const t = Math.max(2, tip - inset * 0.7);
    ctx.beginPath();
    ctx.moveTo(cx, top + inset * 1.25);
    ctx.lineTo(cx + half, top + t + inset * 0.3);
    ctx.lineTo(cx + half, top + h - t - inset * 0.3);
    ctx.lineTo(cx, top + h - inset * 1.25);
    ctx.lineTo(cx - half, top + h - t - inset * 0.3);
    ctx.lineTo(cx - half, top + t + inset * 0.3);
    ctx.closePath();
  }

  drawThumb(now, live, awake) {
    const ctx = this.ctx;
    const cx = FX_LEFT + BAR_W / 2;
    const w = this.w;
    const h = this.thumbH * this.stretch;
    const top = FX_V + this.thumbY + (this.thumbH - h) / 2;
    const tip = Math.min(h / 4, w * 0.8);
    const energy = clamp((awake ? 0.45 : 0) + (live ? 0.55 : 0) + (this.drag ? 0.3 : 0), 0, 1);
    const glow = mixRgb(RGB.teal, RGB.fire, this.heat);
    const flash = clamp(1 - (now - this.flashAt) / 420, 0, 1);
    // Los temas por línea tienen su propia forma (placa, rama, daga, cartucho, cadena, escudo, cápsula).
    const paint = THUMBS[SKIN.thumb];
    if (paint) {
      paint(this, { ctx, cx, w, h, top, energy, glow, flash, now, fxv: FX_V });
      return;
    }

    // Halo y sombra.
    ctx.save();
    ctx.shadowColor = rgba(glow, 0.35 + energy * 0.5);
    ctx.shadowBlur = 6 + energy * 12;
    this.gemPath(cx, top, h, w, tip, 0);
    ctx.fillStyle = "#140d03";
    ctx.fill();
    ctx.restore();

    // Marco de oro biselado: de lado a lado (bronce, oro, brillo, oro, bronce) y luz arriba, sombra abajo.
    const frame = ctx.createLinearGradient(cx - w / 2, 0, cx + w / 2, 0);
    [0, 0.2, 0.42, 0.6, 0.84, 1].forEach((at, i) => frame.addColorStop(at, SKIN.frame[i]));
    this.gemPath(cx, top, h, w, tip, 0);
    ctx.fillStyle = frame;
    ctx.fill();
    const shade = ctx.createLinearGradient(0, top, 0, top + h);
    shade.addColorStop(0, "rgba(255,250,235,0.55)");
    shade.addColorStop(0.08, "rgba(255,250,235,0)");
    shade.addColorStop(0.9, "rgba(0,0,0,0)");
    shade.addColorStop(1, "rgba(0,0,0,0.6)");
    ctx.fillStyle = shade;
    ctx.fill();

    // El cristal: plasma, profundidad, reflejo y cortes de gema.
    const inset = w >= 15 ? 2.4 : 2;
    const bright = 0.62 + energy * 0.45;
    this.paintPlasma(h, bright);
    ctx.save();
    this.gemPath(cx, top, h, w, tip, inset);
    ctx.clip();
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(this.plasma, cx - w / 2, top, w, h);
    const depth = ctx.createLinearGradient(cx - w / 2, 0, cx + w / 2, 0);
    depth.addColorStop(0, "rgba(0,0,0,0.55)");
    depth.addColorStop(0.3, "rgba(0,0,0,0)");
    depth.addColorStop(0.7, "rgba(0,0,0,0)");
    depth.addColorStop(1, "rgba(0,0,0,0.65)");
    ctx.fillStyle = depth;
    ctx.fillRect(cx - w / 2, top, w, h);
    const sheen = ctx.createLinearGradient(cx - w / 2, 0, cx + w / 2, 0);
    sheen.addColorStop(0.12, "rgba(255,255,255,0)");
    sheen.addColorStop(0.3, "rgba(255,255,255,0.32)");
    sheen.addColorStop(0.46, "rgba(255,255,255,0)");
    ctx.fillStyle = sheen;
    ctx.fillRect(cx - w / 2, top, w, h);
    // Cortes de gema: uves muy tenues que dan facetas al cristal.
    ctx.strokeStyle = "rgba(255,255,255,0.09)";
    ctx.lineWidth = 0.8;
    for (let y = top + tip + 10; y < top + h - tip - 6; y += 15) {
      ctx.beginPath();
      ctx.moveTo(cx - w / 2, y);
      ctx.lineTo(cx, y + 5);
      ctx.lineTo(cx + w / 2, y);
      ctx.stroke();
    }
    if (flash > 0) {
      ctx.fillStyle = `rgba(255,250,215,${(flash * 0.9).toFixed(3)})`;
      ctx.fillRect(cx - w / 2, top, w, h);
    }
    ctx.restore();
    // Canto interior del cristal.
    this.gemPath(cx, top, h, w, tip, inset);
    ctx.strokeStyle = "rgba(255,238,200,0.4)";
    ctx.lineWidth = 0.7;
    ctx.stroke();

    this.drawProngs(cx, top, h, w, energy);
    this.drawMedallion(cx, top + h / 2, w, energy);
  }

  /** Garras de oro y una gema en cada punta del pulgar. */
  drawProngs(cx, top, h, w, energy) {
    const ctx = this.ctx;
    ctx.lineCap = "round";
    for (const [y, dir] of [[top, -1], [top + h, 1]]) {
      ctx.strokeStyle = SKIN.prong;
      ctx.lineWidth = 1.3;
      for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.moveTo(cx + side * w * 0.3, y - dir * w * 0.55);
        ctx.quadraticCurveTo(cx + side * w * 0.62, y + dir * 1, cx + side * w * 0.42, y + dir * 5);
        ctx.stroke();
      }
      // La gema de la punta.
      const gy = y + dir * 4.5;
      const r = 2.6 + energy * 0.6;
      ctx.save();
      ctx.shadowColor = rgba(mixRgb(RGB.teal, RGB.fire, this.heat), 0.9);
      ctx.shadowBlur = 4 + energy * 6;
      ctx.beginPath();
      ctx.moveTo(cx, gy - r);
      ctx.lineTo(cx + r * 0.8, gy);
      ctx.lineTo(cx, gy + r);
      ctx.lineTo(cx - r * 0.8, gy);
      ctx.closePath();
      const gem = ctx.createLinearGradient(cx - r, gy - r, cx + r, gy + r);
      gem.addColorStop(0, "#ffffff");
      gem.addColorStop(0.4, rgba(mixRgb(RGB.cyan, RGB.gold, this.heat), 1));
      gem.addColorStop(1, rgba(mixRgb(RGB.teal, RGB.fire, this.heat), 1));
      ctx.fillStyle = gem;
      ctx.fill();
      ctx.restore();
      ctx.strokeStyle = "rgba(40,28,8,0.9)";
      ctx.lineWidth = 0.6;
      ctx.stroke();
    }
  }

  /** El medallón del centro, con la forma de cada tema (``SKIN.medallion``): hexágono hextech, escudo, hoja, estrella
   *  arcana, mirilla, ancla, gema o cruz de curación. Metal y placa de la piel; el orbe late y se calienta. */
  drawMedallion(cx, cy, w, energy) {
    const ctx = this.ctx;
    const R = Math.min(9.5, w * 0.5 + 2.8);
    const pulse = 1 + Math.sin(this.t * 6 + this.spin) * 0.12 * (0.4 + energy);
    const glow = mixRgb(RGB.teal, RGB.fire, this.heat);
    const metal = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
    metal.addColorStop(0, SKIN.metal[0]);
    metal.addColorStop(0.45, SKIN.metal[1]);
    metal.addColorStop(1, SKIN.metal[2]);
    const shape = MEDALLIONS[SKIN.medallion] || MEDALLIONS.hex;
    ctx.save();
    ctx.translate(cx, cy);
    // Placa con su sombra, y el canto de metal.
    ctx.save();
    ctx.shadowColor = "rgba(0,0,0,0.7)";
    ctx.shadowBlur = 4;
    shape.outline(ctx, R, this);
    ctx.fillStyle = SKIN.plate;
    ctx.fill();
    ctx.restore();
    shape.outline(ctx, R, this);
    ctx.strokeStyle = metal;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    ctx.stroke();
    // Lo de dentro (líneas, nervios, facetas…) en el color del tema.
    shape.inner?.(ctx, R, this, rgba(glow, 0.55 + energy * 0.4), metal);
    // Puntos de luz y orbe.
    ctx.globalCompositeOperation = "lighter";
    for (const [x, y] of shape.lights?.(R, this) || []) {
      const s = 3.2 + energy * 2;
      ctx.globalAlpha = 0.55 + energy * 0.45;
      ctx.drawImage(sprite(this.heat > 0.5 ? "fire" : "gold"), x - s / 2, y - s / 2, s, s);
    }
    const orbR = R * (shape.orb ?? 0.48) * pulse;
    if (orbR > 0) {
      const [ox, oy] = shape.orbAt?.(R, this) || [0, 0];
      const core = mixRgb(RGB.cyan, RGB.gold, this.heat);
      const orb = ctx.createRadialGradient(ox - orbR * 0.25, oy - orbR * 0.25, 0, ox, oy, orbR * 1.9);
      orb.addColorStop(0, "rgba(255,255,255,1)");
      orb.addColorStop(0.25, rgba(core, 1));
      orb.addColorStop(0.55, rgba(glow, 0.75));
      orb.addColorStop(1, rgba(glow, 0));
      ctx.globalAlpha = 0.75 + energy * 0.25;
      ctx.fillStyle = orb;
      ctx.beginPath();
      ctx.arc(ox, oy, orbR * 1.9, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.restore();
  }

  drawRings(dt) {
    const ctx = this.ctx;
    const cx = FX_LEFT + BAR_W / 2;
    ctx.globalCompositeOperation = "lighter";
    this.rings = this.rings.filter((ring) => (ring.life += dt) < ring.max);
    for (const ring of this.rings) {
      const k = ring.life / ring.max;
      const radius = ring.radius * (0.25 + k);
      ctx.strokeStyle = rgba(RGB[ring.color], (1 - k) * 0.9);
      ctx.lineWidth = 2.4 * (1 - k) + 0.4;
      ctx.beginPath();
      if (ring.shape === "hex") {
        // Escudo del guardián: un hexágono que se abre.
        for (let i = 0; i <= 6; i++) {
          const a = (i * Math.PI) / 3 + Math.PI / 6;
          ctx.lineTo(cx + Math.cos(a) * radius, ring.y + Math.sin(a) * radius * 0.8);
        }
      } else ctx.ellipse(cx, ring.y, radius, radius * 0.75, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";
  }

  drawBolts(dt) {
    const ctx = this.ctx;
    ctx.globalCompositeOperation = "lighter";
    ctx.lineJoin = "round";
    this.bolts = this.bolts.filter((b) => (b.life += dt) < b.max);
    for (const b of this.bolts) {
      const k = b.life / b.max;
      const alpha = k < 0.2 ? 1 : 1 - (k - 0.2) / 0.8;
      ctx.beginPath();
      b.points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.strokeStyle = rgba(RGB[b.color], 0.3 * alpha);
      ctx.lineWidth = 3.2;
      ctx.stroke();
      ctx.strokeStyle = rgba(mixRgb(RGB[b.color], RGB.white, 0.65), 0.95 * alpha);
      ctx.lineWidth = 0.9;
      ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";
  }

  /** Puntos de luz y, en los temas por línea, partículas con forma (hojas, casquillos, copos, corazones…) que giran,
   *  pesan o suben (``g``), se mecen (``sway``) o titilan (``blink``). */
  drawParticles(dt) {
    const ctx = this.ctx;
    ctx.globalCompositeOperation = "lighter";
    const alive = [];
    for (const p of this.particles) {
      p.life += dt;
      if (p.life >= p.max) continue;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.mote) p.x += Math.sin((p.life + p.y) / 140) * 0.06;
      else {
        const drag = p.drag ?? 0.985;
        p.vx *= drag;
        p.vy = p.vy * drag + (p.g ?? 0.00012) * dt; // un poco de peso (o, si es negativo, sube)
      }
      if (p.sway) p.x += Math.sin(p.life * 0.004 + p.phase) * p.sway * dt * 0.06;
      if (p.vr) p.rot += p.vr * dt;
      const k = p.life / p.max;
      const size = p.size * (p.mote || p.shape ? 1 : 1 - k * 0.5);
      let alpha = p.mote ? Math.sin(Math.PI * k) * 0.9 : (1 - k) ** 1.4;
      if (p.blink) alpha *= 0.45 + 0.55 * Math.abs(Math.sin(p.life * 0.006 + p.phase));
      ctx.globalAlpha = alpha;
      if (p.shape) {
        ctx.globalCompositeOperation = p.add ? "lighter" : "source-over";
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        SHAPES[p.shape](ctx, p, size);
        ctx.restore();
        ctx.globalCompositeOperation = "lighter";
      } else ctx.drawImage(sprite(p.sprite), p.x - size / 2, p.y - size / 2, size, size);
      alive.push(p);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    this.particles = alive;
  }
}
