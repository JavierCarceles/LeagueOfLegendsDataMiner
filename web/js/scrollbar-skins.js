/** Pieles de las barras de scroll dibujadas (scrollbars.js: ``HexBar``), una por tema (theme.js).
 *
 *  Cada piel trae sus colores (plasma en calma y caliente, metal, marco), la forma de su pulgar (``thumb``), su
 *  medallón y sus efectos (``fx``: lo que suelta al desplazarte, lo que flota en reposo, el choque con los extremos):
 *  · Hextech: gema tallada con garras de oro y medallón hexagonal (la de scrollbars.js).
 *  · Top: placa de acero forjada con remaches, ranura y rejilla de lava; chispas de yunque y ascuas.
 *  · Jungla: rama con corteza, musgo y hojas que se mecen (y se pliegan con el viento al desplazarte); savia verde por
 *    la grieta (morada de Barón con velocidad); hojas que caen y luciérnagas.
 *  · Mid: daga de plata con su vaceo lleno de magia, guarda con estrella arcana, empuñadura y pomo; la punta va hacia
 *    donde te desplazas (gira al cambiar), deja sombras como clones y suelta chispas arcanas.
 *  · ADC: cartucho de latón con su bala hacia donde te desplazas; fogonazo y humo al empezar, casquillos que saltan,
 *    trazadora y marca de impacto al chocar con un extremo.
 *  · Engage: cadena de eslabones con el farol de las almas en medio, argolla arriba y garfio abajo; burbujas y almas.
 *  · Guardián: escudo de torre con canto de plata, cara de hielo facetada, cristales y gema de Taric; nieve y esquirlas.
 *  · Peel: cápsula de cristal de dos colores (cura y escudo) con corazones y cruces flotando dentro y burbuja de
 *    escudo alrededor; corazones, cruces y destellos.
 *  Todo se dibuja en el lienzo de la barra; los raíles, runas y tapas van en CSS (theme-bars.css y cada tema). */

export const rand = (a, b) => a + Math.random() * (b - a);
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const lerp = (a, b, k) => a + (b - a) * k;
export const rgba = ([r, g, b], a) => `rgba(${r | 0},${g | 0},${b | 0},${a.toFixed(3)})`;
export const mixRgb = (a, b, k) => [lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)];
const pick = (list) => list[Math.floor(Math.random() * list.length)];
const TAU = Math.PI * 2;

/** Color de una paleta en ``v`` (0 → 1). */
export function paletteAt(stops, v) {
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const [v0, c0] = stops[i - 1];
      const [v1, c1] = stops[i];
      return mixRgb(c0, c1, (v - v0) / (v1 - v0 || 1));
    }
  }
  return stops[stops.length - 1][1];
}

/** Degradado lineal con sus paradas (colores repartidos o ``[posición, color]``). */
function lin(ctx, x0, y0, x1, y1, stops) {
  const g = ctx.createLinearGradient(x0, y0, x1, y1);
  stops.forEach((s, i) => (Array.isArray(s) ? g.addColorStop(s[0], s[1]) : g.addColorStop(i / (stops.length - 1), s)));
  return g;
}

export const RGB = {
  teal: [10, 200, 185],
  cyan: [205, 250, 250],
  gold: [240, 200, 110],
  fire: [255, 128, 36],
  ember: [255, 72, 40],
  flash: [255, 236, 120],
  white: [255, 255, 255],
};
// Paletas del plasma (valor 0 → 1): energía hextech y, con calor, fuego.
export let COOL = [[0, [1, 9, 16]], [0.38, [3, 58, 78]], [0.66, [10, 196, 182]], [0.86, [160, 250, 240]], [1, [255, 255, 255]]];
export let HOT = [[0, [22, 6, 2]], [0.38, [120, 32, 10]], [0.66, [255, 128, 36]], [0.86, [255, 214, 120]], [1, [255, 252, 230]]];

/** Partícula con sus valores por defecto: ``shape`` (de SHAPES) o un punto de luz (``sprite``); ``g``, peso (negativo:
 *  sube); ``sway``, vaivén de lado; ``vr``, giro; ``add``, se suma a la luz (si no, se pinta encima, como el humo). */
const P = (o) => ({ life: 0, rot: rand(0, TAU), vr: 0, phase: rand(0, TAU), add: true, ...o });

/** Punto al azar del canal (para lo que flota en reposo). */
const onRail = (bar) => bar.fxv + bar.pad + bar.trackH * rand(0.04, 0.96);

/* --------------------------------------------------------------------------------------------- pieles */

/** Piel de la barra según el tema: colores con nombre (los efectos usan «teal» para el color propio, «cyan» para su
 *  luz, «gold» para el metal, «fire» y «ember» para el calor), plasma en calma y caliente, marco de lado a lado,
 *  metal y placa del medallón, forma del pulgar, medallón, si suelta rayos y sus efectos. */
export const SKINS = {
  hextech: {
    rgb: { teal: [10, 200, 185], cyan: [205, 250, 250], gold: [240, 200, 110], fire: [255, 128, 36], ember: [255, 72, 40] },
    cool: COOL,
    hot: HOT,
    frame: ["#3a2c0e", "#c89b3c", "#f6ecd4", "#c8aa6e", "#785a28", "#2e220a"],
    metal: ["#fbf1d8", "#c89b3c", "#6b4f1f"],
    plate: "rgba(3,16,24,0.92)",
    prong: "#d7b56d",
    medallion: "hex",
    thumb: "gem",
    bolts: true,
  },
  // Top: forja y tanques. Placa de acero con remaches y lava dentro (al rojo blanco con velocidad).
  "r-top": {
    rgb: { teal: [255, 118, 30], cyan: [255, 208, 140], gold: [196, 204, 212], fire: [255, 244, 210], ember: [255, 70, 20] },
    cool: [[0, [14, 6, 4]], [0.38, [96, 26, 8]], [0.66, [236, 96, 22]], [0.86, [255, 194, 96]], [1, [255, 246, 222]]],
    hot: [[0, [40, 10, 4]], [0.38, [206, 64, 12]], [0.66, [255, 172, 44]], [0.86, [255, 236, 156]], [1, [255, 255, 255]]],
    frame: ["#14171a", "#5d666f", "#e3e7ea", "#9aa4ae", "#3a4148", "#101315"],
    metal: ["#eef1f3", "#8a949e", "#2f353c"],
    plate: "rgba(18,20,24,0.95)",
    medallion: "shield",
    thumb: "plate",
    fx: {
      idleMs: [520, 170],
      idle: (bar) => P({ x: bar.cx + rand(-3, 3), y: onRail(bar), vx: rand(-0.012, 0.004), vy: -rand(0.025, 0.06), max: rand(1200, 2000), size: rand(4, 8), sprite: pick(["teal", "teal", "cyan"]), mote: true }),
      spray: (bar, x, y, dir) => [
        P({ x, y, vx: -rand(0.01, 0.07), vy: -dir * rand(0.02, 0.09), max: rand(400, 800), size: rand(4, 9), sprite: pick(["teal", "cyan", "gold"]) }),
        Math.random() < 0.45 && P({ x, y, vx: -rand(0.05, 0.22), vy: -dir * rand(0.04, 0.2) - 0.05, max: rand(180, 360), size: rand(5, 10), shape: "streak", col: RGB.flash, g: 0.0005 }),
      ],
      shock: (bar, y) => {
        bar.ringAt(y, "teal", 24, 520);
        for (let i = 0; i < 18; i++) {
          const a = rand(-Math.PI, 0) * (y > bar.fxv + bar.barH / 2 ? 1 : -1);
          const s = rand(0.12, 0.32);
          bar.add(P({ x: bar.cx, y, vx: Math.cos(a) * s - 0.06, vy: Math.sin(a) * s, max: rand(200, 420), size: rand(6, 12), shape: "streak", col: pick([RGB.flash, RGB.cyan, RGB.white]), g: 0.0006 }));
        }
      },
    },
  },
  // Jungla: rama con musgo y hojas, savia verde; con velocidad, morado de Barón.
  "r-jungle": {
    rgb: { teal: [86, 214, 112], cyan: [206, 255, 176], gold: [206, 166, 96], fire: [166, 86, 255], ember: [255, 142, 52] },
    cool: [[0, [2, 12, 6]], [0.38, [12, 62, 30]], [0.66, [64, 192, 92]], [0.86, [196, 255, 164]], [1, [250, 255, 236]]],
    hot: [[0, [10, 3, 18]], [0.38, [62, 20, 112]], [0.66, [152, 72, 255]], [0.86, [222, 172, 255]], [1, [255, 246, 255]]],
    frame: ["#1e1309", "#5a3d22", "#a8844f", "#6b4a2a", "#3d2a14", "#140d06"],
    metal: ["#e2d2a2", "#8a6a3a", "#3a2a14"],
    plate: "rgba(6,16,8,0.93)",
    medallion: "leaf",
    thumb: "branch",
    fx: {
      idleMs: [430, 150],
      idle: (bar) => P({ x: bar.cx + rand(-6, 4), y: onRail(bar), vx: rand(-0.02, 0.008), vy: rand(-0.025, 0.01), max: rand(1600, 2600), size: rand(5, 9), sprite: "cyan", mote: true, blink: true }),
      spray: (bar, x, y, dir) => [
        Math.random() < 0.55 && P({ x, y, vx: -rand(0.01, 0.06), vy: -dir * rand(0.01, 0.05), g: 0.00004, drag: 0.99, sway: 0.025, vr: rand(-0.005, 0.005), max: rand(900, 1500), size: rand(5, 8), shape: "leaf", col: pick([[111, 220, 122], [155, 232, 111], [201, 164, 92], [70, 170, 80]]), add: false }),
        P({ x, y, vx: -rand(0.01, 0.05), vy: -dir * rand(0.02, 0.07), max: rand(350, 700), size: rand(4, 8), sprite: pick(["teal", "cyan", "fire"]) }),
      ],
      shock: (bar, y) => {
        bar.ringAt(y, "teal", 22, 520);
        for (let i = 0; i < 12; i++) bar.add(P({ x: bar.cx, y, vx: rand(-0.1, 0.02), vy: rand(-0.08, 0.08), g: 0.00005, drag: 0.985, sway: 0.02, vr: rand(-0.008, 0.008), max: rand(800, 1300), size: rand(5, 8), shape: "leaf", col: pick([[111, 220, 122], [155, 232, 111], [201, 164, 92]]), add: false }));
      },
    },
  },
  // Mid: daga de plata y magia arcana (violeta → magenta).
  "r-mid": {
    rgb: { teal: [150, 92, 255], cyan: [128, 222, 255], gold: [222, 226, 246], fire: [255, 72, 204], ember: [255, 124, 240] },
    cool: [[0, [8, 4, 20]], [0.38, [52, 20, 112]], [0.66, [142, 82, 255]], [0.86, [232, 144, 255]], [1, [255, 242, 255]]],
    hot: [[0, [20, 2, 16]], [0.38, [122, 10, 92]], [0.66, [255, 62, 192]], [0.86, [255, 182, 242]], [1, [255, 255, 255]]],
    frame: ["#1f2230", "#7b82a0", "#f2f4ff", "#a3a9c4", "#4a4f68", "#1a1c28"],
    blade: ["#2c3046", "#f4f6ff", "#aeb4d4", "#5d6388", "#d4d9f2", "#ffffff", "#3c4058"],
    metal: ["#ffffff", "#a8aec8", "#4a4f68"],
    plate: "rgba(10,6,24,0.93)",
    medallion: "star",
    thumb: "blade",
    bolts: true,
    fx: {
      idleMs: [380, 130],
      idle: (bar) => P({ x: bar.cx + rand(-5, 5), y: onRail(bar), vx: rand(-0.01, 0.004), vy: rand(-0.012, 0.004), max: rand(900, 1500), size: rand(4, 7), shape: "star4", col: pick([RGB.teal, RGB.cyan, RGB.fire, RGB.white]), vr: rand(-0.003, 0.003), mote: true, blink: true }),
      spray: (bar, x, y, dir) => [
        P({ x, y, vx: -rand(0.02, 0.09), vy: -dir * rand(0.02, 0.1), max: rand(450, 850), size: rand(4, 8), shape: "star4", col: pick([RGB.teal, RGB.cyan, RGB.fire]), vr: rand(-0.01, 0.01) }),
        P({ x, y, vx: -rand(0.01, 0.06), vy: -dir * rand(0.02, 0.07), max: rand(350, 700), size: rand(4, 8), sprite: pick(["teal", "cyan", "fire"]) }),
      ],
      shock: (bar, y) => {
        bar.ringAt(y, "fire", 24, 520);
        bar.ringAt(y, "cyan", 14, 380);
        for (let i = 0; i < 16; i++) {
          const a = rand(0, TAU);
          const s = rand(0.05, 0.15);
          bar.add(P({ x: bar.cx, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, max: rand(400, 750), size: rand(5, 9), shape: "star4", col: pick([RGB.teal, RGB.cyan, RGB.fire]), vr: rand(-0.01, 0.01) }));
        }
      },
    },
  },
  // ADC: cartucho de latón, energía carmesí y fogonazo.
  "r-adc": {
    rgb: { teal: [236, 52, 62], cyan: [255, 204, 126], gold: [232, 192, 104], fire: [255, 242, 176], ember: [255, 92, 40] },
    cool: [[0, [12, 2, 4]], [0.38, [92, 10, 20]], [0.66, [232, 42, 52]], [0.86, [255, 172, 112]], [1, [255, 242, 212]]],
    hot: [[0, [26, 12, 2]], [0.38, [170, 90, 12]], [0.66, [255, 196, 64]], [0.86, [255, 242, 172]], [1, [255, 255, 255]]],
    frame: ["#3a2608", "#a87b2c", "#fff0c4", "#d9ad55", "#7a561a", "#2a1c06"],
    bullet: ["#4a120a", "#ffb08a", "#e0603e", "#9a2a16", "#3a0a06"],
    metal: ["#fff0c4", "#c69a45", "#4a3410"],
    plate: "rgba(14,10,8,0.93)",
    medallion: "reticle",
    thumb: "cartridge",
    fx: {
      idleMs: [900, 320],
      idle: (bar) => P({ x: bar.cx + rand(-2, 2), y: onRail(bar), vx: rand(-0.006, 0.002), vy: -rand(0.01, 0.03), max: rand(900, 1500), size: rand(3, 6), sprite: pick(["teal", "cyan"]), mote: true, blink: true }),
      every: 95, // los casquillos salen a ritmo de cadencia, no a cada fotograma
      spray: (bar, x, y, dir) => [
        P({ x: bar.cx - 4, y, vx: -rand(0.07, 0.15), vy: -rand(0.05, 0.13), g: 0.0007, drag: 0.992, vr: rand(-0.025, 0.025), max: rand(500, 850), size: rand(5.5, 7), shape: "casing", col: RGB.gold, add: false }),
        P({ x, y, vx: -rand(0.02, 0.08), vy: -dir * rand(0.02, 0.08), max: rand(250, 500), size: rand(4, 8), sprite: pick(["teal", "cyan", "fire"]) }),
      ],
      // Fogonazo y humo por detrás de la bala al empezar a desplazarte.
      start: (bar) => {
        bar.muzzleAt = performance.now();
        for (let i = 0; i < 5; i++) bar.add(P({ x: bar.cx + rand(-3, 3), y: bar.tailY(), vx: rand(-0.03, 0.01), vy: -rand(0.005, 0.03), g: -0.00001, max: rand(600, 1000), size: rand(8, 14), shape: "puff", col: [150, 140, 132], add: false }));
      },
      shock: (bar, y) => {
        bar.hitAt = performance.now();
        bar.hitY = y;
        bar.ringAt(y, "fire", 20, 380);
        for (let i = 0; i < 14; i++) {
          const a = rand(0, TAU);
          const s = rand(0.1, 0.28);
          bar.add(P({ x: bar.cx, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, max: rand(150, 320), size: rand(5, 10), shape: "streak", col: pick([RGB.fire, RGB.cyan, RGB.white]), g: 0.0004 }));
        }
      },
    },
  },
  // Support engage: hierro de barco, verde del farol de almas; con velocidad, rojo de Pyke.
  "r-engage": {
    rgb: { teal: [44, 222, 152], cyan: [172, 255, 212], gold: [152, 168, 178], fire: [232, 42, 72], ember: [255, 92, 92] },
    cool: [[0, [1, 8, 8]], [0.38, [4, 52, 46]], [0.66, [32, 202, 142]], [0.86, [162, 255, 202]], [1, [236, 255, 246]]],
    hot: [[0, [14, 2, 4]], [0.38, [92, 8, 20]], [0.66, [222, 32, 62]], [0.86, [255, 142, 152]], [1, [255, 236, 236]]],
    frame: ["#0d1416", "#3d4b50", "#b6c4ca", "#6d7e84", "#2a3438", "#0a1012"],
    metal: ["#dfe7ea", "#7f8f96", "#2a3438"],
    plate: "rgba(2,12,14,0.93)",
    medallion: null,
    thumb: "chain",
    fx: {
      idleMs: [420, 150],
      idle: (bar) =>
        Math.random() < 0.6
          ? P({ x: bar.cx + rand(-4, 4), y: onRail(bar), vx: rand(-0.012, 0.004), vy: -rand(0.015, 0.04), sway: 0.012, max: rand(1500, 2400), size: rand(5, 9), sprite: pick(["teal", "cyan"]), mote: true })
          : P({ x: bar.cx + rand(-3, 3), y: onRail(bar), vx: rand(-0.008, 0.004), vy: -rand(0.02, 0.05), g: -0.00001, sway: 0.015, max: rand(1200, 2000), size: rand(3, 6), shape: "bubble", col: [159, 255, 224] }),
      spray: (bar, x, y, dir) => [
        P({ x, y, vx: -rand(0.005, 0.04), vy: -dir * rand(0.01, 0.04) - rand(0.02, 0.05), g: -0.00002, sway: 0.02, max: rand(700, 1300), size: rand(3, 7), shape: "bubble", col: [159, 255, 224] }),
        P({ x, y, vx: -rand(0.01, 0.05), vy: -dir * rand(0.02, 0.06), max: rand(400, 800), size: rand(4, 9), sprite: pick(["teal", "cyan", "fire"]) }),
      ],
      shock: (bar, y, hot) => {
        bar.ringAt(y, hot ? "fire" : "teal", 28, 620);
        bar.ringAt(y, "cyan", 16, 420);
        for (let i = 0; i < 16; i++) bar.add(P({ x: bar.cx + rand(-4, 4), y, vx: rand(-0.08, 0.03), vy: rand(-0.1, 0.02), g: -0.00003, sway: 0.02, max: rand(700, 1200), size: rand(3, 8), shape: "bubble", col: [159, 255, 224] }));
      },
    },
  },
  // Support guardián: hielo del Freljord y gema de Taric.
  "r-guardian": {
    rgb: { teal: [122, 202, 255], cyan: [232, 248, 255], gold: [204, 226, 246], fire: [255, 112, 216], ember: [202, 142, 255] },
    cool: [[0, [4, 10, 20]], [0.38, [20, 62, 112]], [0.66, [112, 192, 255]], [0.86, [212, 242, 255]], [1, [255, 255, 255]]],
    hot: [[0, [16, 4, 16]], [0.38, [102, 20, 92]], [0.66, [255, 102, 212]], [0.86, [255, 202, 242]], [1, [255, 255, 255]]],
    frame: ["#142233", "#6f93b4", "#f2fbff", "#a6c8e6", "#3c5874", "#101c2a"],
    metal: ["#ffffff", "#a9cbe8", "#3c5874"],
    plate: "rgba(4,12,24,0.9)",
    medallion: "gem",
    thumb: "shield",
    fx: {
      idleMs: [360, 130],
      idle: (bar) => P({ x: bar.cx + rand(-8, 4), y: bar.fxv + bar.pad + bar.trackH * rand(0, 0.7), vx: rand(-0.008, 0.004), vy: rand(0.012, 0.03), g: 0, drag: 1, sway: 0.012, vr: rand(-0.002, 0.002), max: rand(1800, 2800), size: rand(4, 7), shape: "flake", col: [235, 248, 255] }),
      spray: (bar, x, y, dir) => [
        Math.random() < 0.5 && P({ x, y, vx: -rand(0.02, 0.08), vy: -dir * rand(0.02, 0.08), max: rand(400, 750), size: rand(4, 7), shape: "shard", col: [200, 235, 255], vr: rand(-0.012, 0.012) }),
        P({ x, y, vx: -rand(0.01, 0.06), vy: -dir * rand(0.02, 0.07), max: rand(400, 800), size: rand(4, 7), shape: "star4", col: pick([RGB.white, RGB.teal, RGB.fire]), vr: rand(-0.006, 0.006) }),
      ],
      shock: (bar, y) => {
        bar.ringAt(y, "cyan", 26, 600, "hex");
        bar.ringAt(y, "fire", 15, 420, "hex");
        for (let i = 0; i < 14; i++) {
          const a = rand(0, TAU);
          const s = rand(0.05, 0.16);
          bar.add(P({ x: bar.cx, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, max: rand(400, 750), size: rand(4, 8), shape: "shard", col: [214, 240, 255], vr: rand(-0.015, 0.015) }));
        }
      },
    },
  },
  // Support peel: escudos y curas. Nácar, menta de curación y rosa; escudo dorado con velocidad.
  "r-peel": {
    rgb: { teal: [112, 255, 192], cyan: [255, 192, 236], gold: [255, 236, 182], fire: [255, 226, 122], ember: [192, 162, 255] },
    cool: [[0, [10, 8, 24]], [0.38, [40, 92, 122]], [0.66, [112, 255, 192]], [0.86, [255, 202, 242]], [1, [255, 255, 255]]],
    hot: [[0, [20, 14, 4]], [0.38, [122, 92, 32]], [0.66, [255, 222, 122]], [0.86, [255, 250, 212]], [1, [255, 255, 255]]],
    frame: ["#3a2f4a", "#a894cc", "#fffaf0", "#f2d9a8", "#7a6aa0", "#2e2440"],
    metal: ["#ffffff", "#e8d2ff", "#7a6aa0"],
    plate: "rgba(16,14,36,0.9)",
    medallion: "heart",
    thumb: "capsule",
    fx: {
      idleMs: [340, 120],
      idle: (bar) =>
        Math.random() < 0.65
          ? P({ x: bar.cx + rand(-6, 5), y: onRail(bar), vx: rand(-0.008, 0.004), vy: rand(-0.012, 0.004), max: rand(900, 1500), size: rand(4, 7), shape: "star4", col: pick([RGB.teal, RGB.cyan, RGB.gold, RGB.white]), vr: rand(-0.003, 0.003), mote: true, blink: true })
          : P({ x: bar.cx + rand(-4, 3), y: onRail(bar), vx: rand(-0.01, 0.004), vy: -rand(0.015, 0.035), g: -0.00001, sway: 0.015, max: rand(1300, 2000), size: rand(4, 6), shape: pick(["heart", "plus"]), col: pick([RGB.teal, RGB.cyan]) }),
      spray: (bar, x, y, dir) => [
        P({ x, y, vx: -rand(0.01, 0.06), vy: -dir * rand(0.01, 0.04) - rand(0.01, 0.04), g: -0.00002, sway: 0.02, vr: rand(-0.004, 0.004), max: rand(700, 1300), size: rand(4, 7), shape: pick(["heart", "plus", "star4"]), col: pick([RGB.teal, RGB.cyan, RGB.gold]) }),
        P({ x, y, vx: -rand(0.01, 0.05), vy: -dir * rand(0.02, 0.06), max: rand(350, 700), size: rand(4, 8), sprite: pick(["teal", "cyan", "gold"]) }),
      ],
      shock: (bar, y) => {
        bar.ringAt(y, "teal", 26, 620);
        bar.ringAt(y, "cyan", 16, 460);
        for (let i = 0; i < 14; i++) {
          const a = rand(0, TAU);
          const s = rand(0.04, 0.12);
          bar.add(P({ x: bar.cx, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, g: -0.00002, max: rand(600, 1000), size: rand(4, 7), shape: pick(["heart", "plus", "star4"]), col: pick([RGB.teal, RGB.cyan, RGB.gold]), vr: rand(-0.006, 0.006) }));
        }
      },
    },
  },
};
export let SKIN = SKINS.hextech;

/** Pone la piel de un tema a todas las barras (los puntos de luz se vuelven a pintar con sus colores). */
export function applySkin(themeId) {
  SKIN = SKINS[themeId] || SKINS.hextech;
  Object.assign(RGB, SKIN.rgb);
  COOL = SKIN.cool;
  HOT = SKIN.hot;
  for (const key of Object.keys(sprites)) delete sprites[key];
}

/** Un punto de luz difuminado por color, pintado una vez y reutilizado (mucho más barato que ``shadowBlur``). */
const sprites = {};
export function sprite(name) {
  if (sprites[name]) return sprites[name];
  const size = 32;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.18, rgba(RGB[name], 1));
  grad.addColorStop(0.45, rgba(RGB[name], 0.35));
  grad.addColorStop(1, rgba(RGB[name], 0));
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  sprites[name] = c;
  return c;
}

/* --------------------------------------------------------------------------------------------- medallones */

export function polygon(ctx, points) {
  ctx.beginPath();
  points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
}
const ring = (n, radius, angle) => Array.from({ length: n }, (_, k) => [Math.cos(angle + (k * TAU) / n) * radius, Math.sin(angle + (k * TAU) / n) * radius]);

/** Formas del medallón, centradas en 0,0 (``outline`` deja el trazo listo para rellenar y perfilar). ``bar.spin``
 *  gira con el tiempo y más deprisa al desplazarte; ``bar.t`` es el tiempo del plasma. */
export const MEDALLIONS = {
  // Hextech: hexágono que gira, otro dentro al revés y luces en los vértices.
  hex: {
    outline: (ctx, R, bar) => polygon(ctx, ring(6, R, bar.spin)),
    inner: (ctx, R, bar, color) => {
      polygon(ctx, ring(6, R * 0.62, -bar.spin * 1.6 + Math.PI / 6));
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.8;
      ctx.stroke();
    },
    lights: (R, bar) => ring(6, R, bar.spin),
  },
  // Top: escudo de forja con su banda y un remache de lava; se mece un poco.
  shield: {
    outline: (ctx, R, bar) => {
      ctx.rotate(Math.sin(bar.spin) * 0.08);
      ctx.beginPath();
      ctx.moveTo(-R * 0.9, -R * 1.0);
      ctx.quadraticCurveTo(0, -R * 0.78, R * 0.9, -R * 1.0);
      ctx.lineTo(R * 0.9, -R * 0.1);
      ctx.quadraticCurveTo(R * 0.82, R * 0.75, 0, R * 1.2);
      ctx.quadraticCurveTo(-R * 0.82, R * 0.75, -R * 0.9, -R * 0.1);
      ctx.closePath();
    },
    inner: (ctx, R, bar, color, metal) => {
      ctx.beginPath();
      ctx.moveTo(-R * 0.78, -R * 0.42);
      ctx.lineTo(R * 0.78, -R * 0.42);
      ctx.strokeStyle = metal;
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, -R * 0.42);
      ctx.lineTo(0, R * 1.05);
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.8;
      ctx.stroke();
    },
    lights: (R) => [[-R * 0.62, -R * 0.72], [R * 0.62, -R * 0.72]],
    orb: 0.36,
    orbAt: (R) => [0, R * 0.2],
  },
  // Jungla: hoja con su nervio y venas que se mece con el viento; una luciérnaga la ronda.
  leaf: {
    outline: (ctx, R, bar) => {
      ctx.rotate(Math.sin(bar.spin * 0.7) * 0.22);
      ctx.beginPath();
      ctx.moveTo(0, -R * 1.25);
      ctx.quadraticCurveTo(R * 1.05, -R * 0.1, 0, R * 1.2);
      ctx.quadraticCurveTo(-R * 1.05, -R * 0.1, 0, -R * 1.25);
      ctx.closePath();
    },
    inner: (ctx, R, bar, color) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(0, -R * 1.05);
      ctx.lineTo(0, R * 1.05);
      for (const y of [-0.5, -0.05, 0.4]) {
        ctx.moveTo(0, R * (y + 0.25));
        ctx.lineTo(R * 0.42, R * y);
        ctx.moveTo(0, R * (y + 0.25));
        ctx.lineTo(-R * 0.42, R * y);
      }
      ctx.stroke();
    },
    orb: 0.3,
    orbAt: (R, bar) => [Math.cos(bar.spin * 2.2) * R * 1.15, Math.sin(bar.spin * 2.2) * R * 0.8],
  },
  // Mid: estrella arcana de ocho puntas que gira y un círculo de runas que gira al revés.
  star: {
    outline: (ctx, R, bar) => {
      const points = [];
      for (let k = 0; k < 16; k++) {
        const a = bar.spin + (k * Math.PI) / 8;
        const r = k % 2 ? R * 0.46 : R * (k % 4 ? 1.0 : 1.18);
        points.push([Math.cos(a) * r, Math.sin(a) * r]);
      }
      polygon(ctx, points);
    },
    inner: (ctx, R, bar, color) => {
      ctx.save();
      ctx.rotate(-bar.spin * 1.4);
      ctx.setLineDash([1.6, 1.8]);
      ctx.beginPath();
      ctx.arc(0, 0, R * 1.42, 0, TAU);
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.7;
      ctx.stroke();
      ctx.restore();
    },
    lights: (R, bar) => ring(4, R * 1.18, bar.spin),
    orb: 0.42,
  },
  // ADC: mirilla. Aro con cuatro marcas que se cierra al disparar (desplazarte) y punto rojo en el centro.
  reticle: {
    outline: (ctx, R, bar) => {
      ctx.rotate(bar.spin * 0.5);
      ctx.beginPath();
      ctx.arc(0, 0, R * 0.95, 0, TAU);
    },
    inner: (ctx, R, bar, color, metal) => {
      const close = 1 - 0.25 * Math.min(1, Math.abs(bar.velocity) * 2);
      ctx.strokeStyle = metal;
      ctx.lineWidth = 1.3;
      ctx.beginPath();
      for (let k = 0; k < 4; k++) {
        const a = (k * Math.PI) / 2;
        ctx.moveTo(Math.cos(a) * R * 0.45 * close, Math.sin(a) * R * 0.45 * close);
        ctx.lineTo(Math.cos(a) * R * 1.35, Math.sin(a) * R * 1.35);
      }
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(0, 0, R * 0.55 * close, 0, TAU);
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.8;
      ctx.stroke();
    },
    orb: 0.22,
  },
  // Support guardián: gema facetada (Taric) con un destello que la recorre.
  gem: {
    outline: (ctx, R) => polygon(ctx, [[-R * 0.55, -R * 0.62], [R * 0.55, -R * 0.62], [R * 1.0, -R * 0.12], [0, R * 1.15], [-R * 1.0, -R * 0.12]]),
    inner: (ctx, R, bar, color) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      ctx.moveTo(-R * 1.0, -R * 0.12);
      ctx.lineTo(R * 1.0, -R * 0.12);
      ctx.moveTo(-R * 0.55, -R * 0.62);
      ctx.lineTo(-R * 0.25, -R * 0.12);
      ctx.lineTo(0, R * 1.15);
      ctx.lineTo(R * 0.25, -R * 0.12);
      ctx.lineTo(R * 0.55, -R * 0.62);
      ctx.moveTo(-R * 0.25, -R * 0.12);
      ctx.lineTo(0, -R * 0.62);
      ctx.lineTo(R * 0.25, -R * 0.12);
      ctx.stroke();
    },
    lights: (R, bar) => [[Math.sin(bar.spin * 1.3) * R * 0.8, -R * 0.4 + Math.cos(bar.spin * 1.3) * R * 0.3]],
    orb: 0.3,
    orbAt: (R) => [0, -R * 0.05],
  },
  // Support peel: cruz de curación en un círculo, con destellos que la rondan.
  heart: {
    outline: (ctx, R) => {
      ctx.beginPath();
      ctx.arc(0, 0, R * 0.98, 0, TAU);
    },
    inner: (ctx, R, bar, color) => {
      const a = R * 0.2;
      const b = R * 0.62;
      polygon(ctx, [[-a, -b], [a, -b], [a, -a], [b, -a], [b, a], [a, a], [a, b], [-a, b], [-a, a], [-b, a], [-b, -a], [-a, -a]]);
      ctx.fillStyle = color;
      ctx.fill();
    },
    lights: (R, bar) => ring(3, R * 1.3, bar.spin * 1.5),
    orb: 0.3,
  },
};

/* --------------------------------------------------------------------------------------------- partículas con forma */

function heartPath(ctx, s) {
  ctx.beginPath();
  ctx.moveTo(0, s * 0.42);
  ctx.bezierCurveTo(-s * 0.62, 0, -s * 0.5, -s * 0.5, 0, -s * 0.18);
  ctx.bezierCurveTo(s * 0.5, -s * 0.5, s * 0.62, 0, 0, s * 0.42);
  ctx.closePath();
}
function star4Path(ctx, s) {
  const k = s * 0.16;
  polygon(ctx, [[0, -s / 2], [k, -k], [s / 2, 0], [k, k], [0, s / 2], [-k, k], [-s / 2, 0], [-k, -k]]);
}
function plusPath(ctx, s) {
  const a = s * 0.14;
  const b = s * 0.45;
  polygon(ctx, [[-a, -b], [a, -b], [a, -a], [b, -a], [b, a], [a, a], [a, b], [-a, b], [-a, a], [-b, a], [-b, -a], [-a, -a]]);
}

/** Cómo se pinta cada partícula con forma (en su sitio, girada ``p.rot``, con su color ``p.col``). */
export const SHAPES = {
  leaf(ctx, p, s) {
    ctx.beginPath();
    ctx.moveTo(0, s * 0.5);
    ctx.quadraticCurveTo(s * 0.34, 0, 0, -s * 0.5);
    ctx.quadraticCurveTo(-s * 0.34, 0, 0, s * 0.5);
    ctx.fillStyle = rgba(p.col, 1);
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(0, s * 0.5);
    ctx.lineTo(0, -s * 0.38);
    ctx.strokeStyle = rgba(mixRgb(p.col, [0, 0, 0], 0.45), 0.9);
    ctx.lineWidth = 0.5;
    ctx.stroke();
  },
  star4(ctx, p, s) {
    star4Path(ctx, s);
    ctx.fillStyle = rgba(p.col, 1);
    ctx.fill();
    star4Path(ctx, s * 0.45);
    ctx.fillStyle = "#fff";
    ctx.fill();
  },
  heart(ctx, p, s) {
    heartPath(ctx, s);
    ctx.fillStyle = rgba(p.col, 1);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(-s * 0.18, -s * 0.12, s * 0.1, 0, TAU);
    ctx.fillStyle = "rgba(255,255,255,0.8)";
    ctx.fill();
  },
  plus(ctx, p, s) {
    plusPath(ctx, s);
    ctx.fillStyle = rgba(p.col, 1);
    ctx.fill();
  },
  flake(ctx, p, s) {
    ctx.strokeStyle = rgba(p.col, 1);
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    for (let k = 0; k < 3; k++) {
      const a = (k * Math.PI) / 3;
      const dx = (Math.cos(a) * s) / 2;
      const dy = (Math.sin(a) * s) / 2;
      ctx.moveTo(-dx, -dy);
      ctx.lineTo(dx, dy);
    }
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.1, 0, TAU);
    ctx.fillStyle = "#fff";
    ctx.fill();
  },
  shard(ctx, p, s) {
    polygon(ctx, [[0, -s / 2], [s * 0.22, s * 0.1], [0, s / 2], [-s * 0.16, 0]]);
    ctx.fillStyle = rgba(p.col, 0.9);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.lineWidth = 0.5;
    ctx.stroke();
  },
  bubble(ctx, p, s) {
    ctx.beginPath();
    ctx.arc(0, 0, s / 2, 0, TAU);
    ctx.strokeStyle = rgba(p.col, 0.85);
    ctx.lineWidth = 0.8;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(-s * 0.16, -s * 0.16, s * 0.12, 0, TAU);
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fill();
  },
  casing(ctx, p, s) {
    const w = s * 0.38;
    const h = s;
    ctx.fillStyle = lin(ctx, -w / 2, 0, w / 2, 0, ["#6a4a14", "#fff0c4", "#c69a45", "#4a3410"]);
    ctx.fillRect(-w / 2, -h / 2, w, h);
    ctx.fillStyle = "#3a2608";
    ctx.fillRect(-w / 2 - 0.4, h / 2 - 1.2, w + 0.8, 1.2);
    ctx.fillStyle = "rgba(20,10,4,0.9)";
    ctx.fillRect(-w / 2 + 0.6, -h / 2, w - 1.2, 0.9);
  },
  // Chispa de yunque o de impacto: un trazo en la dirección en la que va.
  streak(ctx, p, s) {
    const len = Math.hypot(p.vx, p.vy) || 1;
    const k = s / len;
    ctx.rotate(-p.rot); // no gira con la partícula: sigue su velocidad
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(-p.vx * k, -p.vy * k);
    ctx.strokeStyle = rgba(p.col, 1);
    ctx.lineWidth = 1.1;
    ctx.lineCap = "round";
    ctx.stroke();
  },
  puff(ctx, p, s) {
    ctx.beginPath();
    ctx.arc(0, 0, s / 2, 0, TAU);
    ctx.fillStyle = rgba(p.col, 0.16);
    ctx.fill();
  },
};

/* --------------------------------------------------------------------------------------------- pulgares */

/** El plasma de la piel dentro de un trazo (``path`` lo deja hecho), estirado a ``x``/``width`` y todo el alto. */
function plasmaIn(bar, g, path, x, width, top = g.top, height = g.h) {
  const { ctx } = g;
  ctx.save();
  path();
  ctx.clip();
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(bar.plasma, x, top, width, height);
  ctx.fillStyle = lin(ctx, x, 0, x + width, 0, [[0, "rgba(0,0,0,0.5)"], [0.32, "rgba(0,0,0,0)"], [0.68, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0.55)"]]);
  ctx.fillRect(x, top, width, height);
  if (g.flash > 0) {
    ctx.fillStyle = `rgba(255,250,215,${(g.flash * 0.9).toFixed(3)})`;
    ctx.fillRect(x, top, width, height);
  }
  ctx.restore();
}

/** Halo del pulgar (sombra difuminada del color del tema). */
function halo(g, path, extra = 0) {
  const { ctx, energy } = g;
  ctx.save();
  ctx.shadowColor = rgba(g.glow, 0.25 + energy * 0.45 + extra);
  ctx.shadowBlur = 6 + energy * 12 + extra * 20;
  path();
  ctx.fillStyle = "#07080a";
  ctx.fill();
  ctx.restore();
}

/** Luz arriba y sombra abajo sobre lo que esté trazado. */
function bevel(g, top, h) {
  const { ctx } = g;
  ctx.fillStyle = lin(ctx, 0, top, 0, top + h, [[0, "rgba(255,255,255,0.45)"], [0.07, "rgba(255,255,255,0)"], [0.9, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0.6)"]]);
  ctx.fill();
}

function rivet(ctx, x, y, r) {
  const grad = ctx.createRadialGradient(x - r * 0.35, y - r * 0.35, 0, x, y, r);
  grad.addColorStop(0, "#ffffff");
  grad.addColorStop(0.45, SKIN.metal[1]);
  grad.addColorStop(1, SKIN.metal[2]);
  ctx.beginPath();
  ctx.arc(x, y, r, 0, TAU);
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.strokeStyle = "rgba(0,0,0,0.75)";
  ctx.lineWidth = 0.5;
  ctx.stroke();
}

/** Gema pequeña (rombo) con brillo: pomo de la daga, guarda, engastes del escudo. */
function smallGem(ctx, x, y, r, color, energy) {
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const s = r * (4 + energy * 3);
  ctx.globalAlpha = 0.5 + energy * 0.4;
  ctx.drawImage(sprite(color), x - s / 2, y - s / 2, s, s);
  ctx.restore();
  polygon(ctx, [[x, y - r], [x + r * 0.78, y], [x, y + r], [x - r * 0.78, y]]);
  ctx.fillStyle = lin(ctx, x - r, y - r, x + r, y + r, ["#ffffff", rgba(RGB.cyan, 1), rgba(RGB[color], 1)]);
  ctx.fill();
  ctx.strokeStyle = "rgba(10,6,20,0.8)";
  ctx.lineWidth = 0.5;
  ctx.stroke();
}

/** Formas del pulgar por tema. Reciben la barra y ``g``: lienzo, centro, ancho, alto (ya estirado), arriba (en el
 *  lienzo), energía (0 → 1: ratón encima, desplazándote, arrastrando), color de brillo, destello del Flash y ahora. */
export const THUMBS = {
  /* Top: placa de acero forjada. Chaflanes, canto biselado, hierro cepillado, ranura central y rejilla por las que
     asoma la lava, remaches, abrazaderas en los extremos y escudo en el centro; con velocidad, al rojo. */
  plate(bar, g) {
    const { ctx, cx, w, h, top, energy } = g;
    const heat = bar.heat;
    const x0 = cx - w / 2;
    const x1 = cx + w / 2;
    const y1 = top + h;
    const mid = top + h / 2;
    const octagon = (i) => {
      const c = Math.max(1.2, 3.4 - i * 0.5);
      polygon(ctx, [[x0 + i + c, top + i], [x1 - i - c, top + i], [x1 - i, top + i + c], [x1 - i, y1 - i - c], [x1 - i - c, y1 - i], [x0 + i + c, y1 - i], [x0 + i, y1 - i - c], [x0 + i, top + i + c]]);
    };
    bar.paintPlasma(h, 0.7 + energy * 0.4 + heat * 0.2);
    halo(g, () => octagon(0), heat * 0.3);
    octagon(0);
    ctx.fillStyle = lin(ctx, x0, 0, x1, 0, SKIN.frame);
    ctx.fill();
    bevel(g, top, h);
    // Placa de hierro cepillado.
    octagon(2.1);
    ctx.fillStyle = lin(ctx, x0, 0, x1, 0, ["#0c0e10", "#2c3238", "#1c2024", "#0e1012"]);
    ctx.fill();
    ctx.save();
    octagon(2.1);
    ctx.clip();
    ctx.beginPath();
    for (let y = top + 3; y < y1 - 2; y += 2.5) {
      ctx.moveTo(x0, y);
      ctx.lineTo(x1, y);
    }
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.lineWidth = 0.6;
    ctx.stroke();
    // Ranura y rejilla de la forja: por ahí asoma la lava.
    const sw = Math.max(3.2, w * 0.28);
    const holes = new Path2D();
    if (h > 34) holes.roundRect(cx - sw / 2, top + 7, sw, h - 14, sw / 2);
    for (let y = top + 10; y < y1 - 9; y += 12) if (Math.abs(y - mid) > 12) holes.rect(x0 + 3.2, y, w - 6.4, 1.5);
    ctx.save();
    ctx.clip(holes);
    ctx.drawImage(bar.plasma, x0, top, w, h);
    ctx.globalCompositeOperation = "lighter";
    ctx.fillStyle = rgba(mixRgb(RGB.teal, RGB.fire, heat), 0.12 + heat * 0.4 + energy * 0.1);
    ctx.fillRect(x0, top, w, h);
    ctx.restore();
    ctx.strokeStyle = "rgba(0,0,0,0.85)";
    ctx.lineWidth = 0.8;
    ctx.stroke(holes);
    if (g.flash > 0) {
      ctx.fillStyle = `rgba(255,240,210,${(g.flash * 0.85).toFixed(3)})`;
      ctx.fillRect(x0, top, w, h);
    }
    ctx.restore();
    // Al rojo: el acero se calienta.
    if (heat > 0.02) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      octagon(0);
      ctx.fillStyle = rgba(RGB.ember, heat * 0.3);
      ctx.fill();
      ctx.restore();
    }
    // Remaches en las esquinas y a los lados.
    for (const y of [top + 4.2, y1 - 4.2]) for (const x of [x0 + 3.6, x1 - 3.6]) rivet(ctx, x, y, 1.35);
    for (let y = top + 34; y < y1 - 28; y += 44) if (Math.abs(y - mid) > 14) for (const x of [x0 + 2.4, x1 - 2.4]) rivet(ctx, x, y, 1.05);
    // Abrazaderas de los extremos.
    for (const y of [top - 1.8, y1 - 1.6]) {
      ctx.fillStyle = lin(ctx, x0 - 2, 0, x1 + 2, 0, SKIN.frame);
      ctx.fillRect(x0 - 2, y, w + 4, 3.4);
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillRect(x0 - 2, y + 2.8, w + 4, 0.6);
      rivet(ctx, x0 - 0.6, y + 1.7, 0.9);
      rivet(ctx, x1 + 0.6, y + 1.7, 0.9);
    }
    bar.drawMedallion(cx, mid, w, energy);
  },

  /* Jungla: rama viva. Corteza con vetas y nudos, musgo, grieta con savia que fluye (morada con velocidad), hojas a los
     lados que se mecen y se pliegan con el viento al desplazarte, brotes arriba y raíces abajo. */
  branch(bar, g) {
    const { ctx, cx, w, h, top, energy } = g;
    const r = w / 2;
    const y1 = top + h;
    const mid = top + h / 2;
    const seg = Math.max(4, Math.round((bar.thumbH - w) / 7));
    const wob = (i, k) => Math.sin(i * 1.9 + k) * 0.65;
    const outline = (inset) => {
      const rr = r - inset;
      const span = h - 2 * r;
      ctx.beginPath();
      ctx.moveTo(cx, top + inset);
      ctx.quadraticCurveTo(cx + rr, top + inset, cx + rr + wob(0, 1), top + r);
      for (let i = 1; i <= seg; i++) ctx.lineTo(cx + rr + wob(i, 1), top + r + (span * i) / seg);
      ctx.quadraticCurveTo(cx + rr, y1 - inset, cx, y1 - inset);
      ctx.quadraticCurveTo(cx - rr, y1 - inset, cx - rr + wob(seg, 4), y1 - r);
      for (let i = seg - 1; i >= 0; i--) ctx.lineTo(cx - rr + wob(i, 4), top + r + (span * i) / seg);
      ctx.quadraticCurveTo(cx - rr, top + inset, cx, top + inset);
      ctx.closePath();
    };
    bar.paintPlasma(h, 0.68 + energy * 0.45);
    halo(g, () => outline(0));
    outline(0);
    ctx.fillStyle = lin(ctx, cx - r, 0, cx + r, 0, SKIN.frame);
    ctx.fill();
    ctx.save();
    outline(0);
    ctx.clip();
    // Vetas de la corteza.
    for (const [dx, color, width] of [[0, "rgba(20,11,4,0.6)", 0.9], [0.8, "rgba(255,226,170,0.1)", 0.5]]) {
      ctx.beginPath();
      for (const k of [-0.62, -0.18, 0.38, 0.72]) {
        for (let y = top; y <= y1; y += 3) {
          const x = cx + dx + k * r + Math.sin((y - top) * 0.13 + k * 9) * 0.9;
          if (y === top) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
      }
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.stroke();
    }
    // Nudos y musgo.
    for (let j = 0, y = top + 16; y < y1 - 8; j++, y += 29) {
      const side = ((j * 7) % 3) - 1;
      if (Math.abs(y - mid) < 12) continue;
      ctx.beginPath();
      ctx.ellipse(cx + side * r * 0.5, y, 1.6, 2.6, 0, 0, TAU);
      ctx.strokeStyle = "rgba(24,13,5,0.8)";
      ctx.lineWidth = 0.8;
      ctx.stroke();
      ctx.beginPath();
      ctx.ellipse(cx - side * r * 0.55, y + 9, 2.4, 3.8, 0.3, 0, TAU);
      ctx.fillStyle = "rgba(104,180,72,0.55)";
      ctx.fill();
      ctx.beginPath();
      ctx.ellipse(cx - side * r * 0.6, y + 8, 1.1, 1.6, 0, 0, TAU);
      ctx.fillStyle = "rgba(190,255,140,0.35)";
      ctx.fill();
    }
    ctx.beginPath();
    ctx.ellipse(cx, top + 2.5, r * 0.95, 3, 0, 0, TAU);
    ctx.fillStyle = "rgba(96,170,70,0.7)";
    ctx.fill();
    ctx.restore();
    // La grieta con savia.
    const cw = Math.max(2.4, w * 0.22);
    const c0 = top + r + 2;
    const c1 = y1 - r - 2;
    if (c1 - c0 > 8) {
      const crack = () => {
        ctx.beginPath();
        for (let y = c0; y <= c1; y += 4) ctx.lineTo(cx - cw / 2 + Math.sin((y - top) * 0.11) * 1.3, y);
        for (let y = c1; y >= c0; y -= 4) ctx.lineTo(cx + cw / 2 + Math.sin((y - top) * 0.11) * 1.3 + Math.sin((y - top) * 0.37) * 0.35, y);
        ctx.closePath();
      };
      plasmaIn(bar, g, crack, cx - w / 2, w);
      crack();
      ctx.strokeStyle = "rgba(18,10,3,0.9)";
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
    // Hojas a los lados.
    const wind = clamp(bar.velocity * 0.8, -0.45, 0.45);
    for (let j = 0, y = top + 13; y < y1 - 9; j++, y += 23) {
      if (Math.abs(y - mid) < 13) continue;
      const side = j % 2 ? 1 : -1;
      const a = side * (0.95 - wind) + Math.sin(bar.t * 2.2 + j * 1.7) * (0.12 + energy * 0.1);
      leaf(ctx, cx + side * (r - 0.6), y, a, 6.5 + (j % 3) * 1.3, 2.9, energy);
    }
    // Brote arriba y raíces abajo.
    for (const [a, len] of [[-0.55, 5.5], [0.5, 5], [0.02, 7]]) leaf(ctx, cx, top + 1.5, a + Math.sin(bar.t * 2 + a) * 0.08, len, 2.4, energy);
    ctx.strokeStyle = "#4a321a";
    ctx.lineWidth = 1.1;
    ctx.lineCap = "round";
    ctx.beginPath();
    for (const k of [-1, 0, 1]) {
      ctx.moveTo(cx + k * 2, y1 - 1.5);
      ctx.quadraticCurveTo(cx + k * 3.2, y1 + 2, cx + k * 4.6, y1 + 4.2);
    }
    ctx.stroke();
    bar.drawMedallion(cx, mid, w, energy);
  },

  /* Mid: daga. Hoja de plata biselada con su vaceo lleno de magia y runas grabadas, destello que la recorre, guarda en
     media luna con la estrella arcana, empuñadura con tiras y pomo con gema. La punta mira hacia donde te desplazas
     (al cambiar, gira) y deja sombras atrás como clones. */
  blade(bar, g) {
    const { ctx, cx, w, h, top, energy } = g;
    const s = Math.sign(bar.face || 1) * Math.max(0.12, Math.abs(bar.face));
    const H = h / 2;
    const cy = top + H;
    const tipLen = Math.min(18, h * 0.22);
    const hilt = Math.min(22, h * 0.3);
    const guardY = H - hilt;
    const bw = w / 2;
    const bladePath = () => polygon(ctx, [[0, -H], [bw * 0.92, -H + tipLen], [bw, guardY - 2], [-bw, guardY - 2], [-bw * 0.92, -H + tipLen]]);
    bar.paintPlasma(h, 0.65 + energy * 0.45);
    // Sombras: siluetas de la daga donde estaba hace un momento.
    if (bar.trail.length > 1) {
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      let lastT = Infinity;
      let n = 0;
      for (let i = bar.trail.length - 1; i >= 0 && n < 3; i--) {
        const p = bar.trail[i];
        const age = (g.now - p.t) / 420;
        if (age >= 1) break;
        if (g.now - p.t < 45 || lastT - p.t < 60) continue;
        lastT = p.t;
        n++;
        ctx.save();
        ctx.translate(cx, p.y + g.fxv);
        ctx.scale(1, s);
        bladePath();
        ctx.fillStyle = rgba(mixRgb(RGB.teal, RGB.fire, 0.4), (1 - age) * 0.22);
        ctx.fill();
        ctx.fillRect(-bw - 4, guardY - 2, w + 8, 3);
        ctx.restore();
      }
      ctx.restore();
    }
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(1, s);
    const shift = { ...g, top: -H };
    halo(shift, bladePath);
    // Acero biselado.
    bladePath();
    ctx.fillStyle = lin(ctx, -bw, 0, bw, 0, SKIN.blade);
    ctx.fill();
    // El vaceo con la magia, y runas grabadas a los lados.
    const fw = Math.max(2.4, w * 0.26);
    const f0 = -H + tipLen + 2;
    const f1 = guardY - 6;
    if (f1 - f0 > 8) {
      const fuller = () => {
        ctx.beginPath();
        ctx.roundRect(-fw / 2, f0, fw, f1 - f0, fw / 2);
      };
      plasmaIn(bar, shift, fuller, -fw / 2 - 1, fw + 2);
      fuller();
      ctx.strokeStyle = "rgba(20,14,46,0.85)";
      ctx.lineWidth = 0.7;
      ctx.stroke();
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      ctx.fillStyle = rgba(RGB.cyan, 0.25 + energy * 0.6);
      const gx = fw / 2 + (bw - fw / 2) * 0.48;
      for (let y = f0 + 5, k = 0; y < f1 - 3; y += 9, k++) {
        const x = k % 2 ? gx : -gx;
        polygon(ctx, [[x, y - 1.6], [x + 0.9, y], [x, y + 1.6], [x - 0.9, y]]);
        ctx.fill();
      }
      ctx.restore();
    }
    // Destello que recorre la hoja.
    ctx.save();
    bladePath();
    ctx.clip();
    const gy = -H - 20 + ((bar.t * 0.32) % 1) * (h + 40);
    ctx.globalCompositeOperation = "lighter";
    ctx.fillStyle = lin(ctx, 0, gy - 9, 0, gy + 9, [[0, "rgba(255,255,255,0)"], [0.5, `rgba(255,255,255,${(0.35 + energy * 0.3).toFixed(3)})`], [1, "rgba(255,255,255,0)"]]);
    ctx.fillRect(-bw, gy - 9, w, 18);
    if (g.flash > 0) {
      ctx.fillStyle = `rgba(255,250,235,${(g.flash * 0.9).toFixed(3)})`;
      ctx.fillRect(-bw, -H, w, h);
    }
    ctx.restore();
    bladePath();
    ctx.strokeStyle = "rgba(16,12,36,0.9)";
    ctx.lineWidth = 0.7;
    ctx.stroke();
    // Empuñadura con tiras y pomo.
    const gw = Math.max(3, w * 0.34);
    const g0 = guardY + 2;
    const g1 = H - 5.5;
    if (g1 > g0) {
      ctx.fillStyle = lin(ctx, -gw / 2, 0, gw / 2, 0, ["#140d2a", "#3b2c6e", "#1c1438"]);
      ctx.fillRect(-gw / 2, g0, gw, g1 - g0);
      ctx.beginPath();
      for (let y = g0 + 1; y < g1; y += 2.6) {
        ctx.moveTo(-gw / 2, y);
        ctx.lineTo(gw / 2, y + 1.5);
      }
      ctx.strokeStyle = "rgba(170,140,255,0.55)";
      ctx.lineWidth = 0.7;
      ctx.stroke();
    }
    // Guarda en media luna (las puntas hacia la hoja).
    ctx.beginPath();
    ctx.moveTo(-bw - 5, guardY - 4.6);
    ctx.quadraticCurveTo(-bw - 2, guardY - 0.6, -bw, guardY - 1.2);
    ctx.lineTo(bw, guardY - 1.2);
    ctx.quadraticCurveTo(bw + 2, guardY - 0.6, bw + 5, guardY - 4.6);
    ctx.lineTo(bw + 3.6, guardY + 1.6);
    ctx.quadraticCurveTo(bw, guardY + 2.6, 0, guardY + 2.6);
    ctx.quadraticCurveTo(-bw, guardY + 2.6, -bw - 3.6, guardY + 1.6);
    ctx.closePath();
    ctx.fillStyle = lin(ctx, -bw - 5, 0, bw + 5, 0, SKIN.frame);
    ctx.fill();
    ctx.strokeStyle = "rgba(14,10,30,0.85)";
    ctx.lineWidth = 0.6;
    ctx.stroke();
    ctx.restore();
    smallGem(ctx, cx, cy + (H - 2.8) * s, 3.2, "fire", energy);
    bar.drawMedallion(cx, cy + guardY * s, w * 0.62, energy);
  },

  /* ADC: cartucho. Bala de cobre con la punta encendida hacia donde te desplazas (gira al cambiar), cuello engastado,
     vaina de latón con hombro, ranura con la energía carmesí, anillos grabados, garganta y culote; mirilla en medio.
     Al empezar a desplazarte, fogonazo por detrás. */
  cartridge(bar, g) {
    const { ctx, cx, w, h, top, energy } = g;
    const s = Math.sign(bar.face || 1) * Math.max(0.12, Math.abs(bar.face));
    const H = h / 2;
    const cy = top + H;
    const bw = w / 2;
    const tipLen = Math.min(20, h * 0.3);
    const neck = -H + tipLen;
    const rim = H - 4;
    const bullet = () => {
      ctx.beginPath();
      ctx.moveTo(0, -H);
      ctx.quadraticCurveTo(bw * 0.86, -H + tipLen * 0.3, bw * 0.78, neck + 0.5);
      ctx.lineTo(-bw * 0.78, neck + 0.5);
      ctx.quadraticCurveTo(-bw * 0.86, -H + tipLen * 0.3, 0, -H);
      ctx.closePath();
    };
    const casing = () =>
      polygon(ctx, [
        [-bw * 0.82, neck], [bw * 0.82, neck], [bw * 0.82, neck + 5], [bw, neck + 8], [bw, rim - 2.6], [bw * 0.8, rim - 1.3], [bw * 0.8, rim],
        [bw + 0.8, rim + 0.6], [bw + 0.8, H], [-bw - 0.8, H], [-bw - 0.8, rim + 0.6], [-bw * 0.8, rim], [-bw * 0.8, rim - 1.3], [-bw, rim - 2.6],
        [-bw, neck + 8], [-bw * 0.82, neck + 5],
      ]);
    bar.paintPlasma(h, 0.65 + energy * 0.45);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(1, s);
    const shift = { ...g, top: -H };
    halo(shift, casing);
    // Vaina de latón.
    casing();
    ctx.fillStyle = lin(ctx, -bw, 0, bw, 0, SKIN.frame);
    ctx.fill();
    bevel(shift, neck, H - neck);
    const sw = Math.max(2.4, w * 0.22);
    const s0 = neck + 12;
    const s1 = rim - 8;
    if (s1 - s0 > 8) {
      const slot = () => {
        ctx.beginPath();
        ctx.roundRect(-sw / 2, s0, sw, s1 - s0, sw / 2);
      };
      plasmaIn(bar, shift, slot, -sw / 2 - 1, sw + 2);
      slot();
      ctx.strokeStyle = "rgba(40,24,4,0.9)";
      ctx.lineWidth = 0.7;
      ctx.stroke();
    }
    ctx.beginPath();
    for (const y of [neck + 9.5, rim - 6]) {
      ctx.moveTo(-bw, y);
      ctx.lineTo(bw, y);
    }
    ctx.strokeStyle = "rgba(60,36,6,0.75)";
    ctx.lineWidth = 0.8;
    ctx.stroke();
    casing();
    ctx.strokeStyle = "rgba(40,24,4,0.85)";
    ctx.lineWidth = 0.6;
    ctx.stroke();
    // La bala, con la punta al rojo.
    bullet();
    ctx.fillStyle = lin(ctx, -bw, 0, bw, 0, SKIN.bullet);
    ctx.fill();
    ctx.strokeStyle = "rgba(40,6,2,0.85)";
    ctx.lineWidth = 0.6;
    ctx.stroke();
    ctx.fillStyle = "rgba(30,14,2,0.9)";
    ctx.fillRect(-bw * 0.82, neck, bw * 1.64, 1.2);
    ctx.globalCompositeOperation = "lighter";
    const tipGlow = 8 + energy * 10 + bar.heat * 8;
    ctx.globalAlpha = 0.5 + energy * 0.5;
    ctx.drawImage(sprite(bar.heat > 0.5 ? "fire" : "teal"), -tipGlow / 2, -H - tipGlow / 2 + 2, tipGlow, tipGlow);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    if (g.flash > 0) {
      casing();
      ctx.fillStyle = `rgba(255,250,220,${(g.flash * 0.85).toFixed(3)})`;
      ctx.fill();
    }
    ctx.restore();
    bar.drawMedallion(cx, cy + ((neck + 8 + rim) / 2) * s, w * 0.8, energy);
    // Fogonazo por detrás del culote al empezar a desplazarte.
    const k = (g.now - (bar.muzzleAt || -1e9)) / 170;
    if (k < 1) {
      const by = cy + (H + 5) * s;
      ctx.save();
      ctx.translate(cx, by);
      ctx.globalCompositeOperation = "lighter";
      const big = (1 - k) * (12 + energy * 4);
      for (let i = 0; i < 10; i++) {
        const a = (i * TAU) / 10 + k;
        const len = big * (i % 2 ? 0.45 : 1);
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(Math.cos(a - 0.12) * 2, Math.sin(a - 0.12) * 2);
        ctx.lineTo(Math.cos(a) * len, Math.sin(a) * len * (a > 0 && a < Math.PI ? 1.4 : 1) * s);
        ctx.lineTo(Math.cos(a + 0.12) * 2, Math.sin(a + 0.12) * 2);
        ctx.closePath();
        ctx.fillStyle = rgba(i % 2 ? RGB.fire : RGB.cyan, 0.9 * (1 - k));
        ctx.fill();
      }
      const core = 10 * (1 - k) + 4;
      ctx.drawImage(sprite("fire"), -core, -core, core * 2, core * 2);
      ctx.restore();
    }
  },

  /* Engage: cadena. Eslabones de hierro (de frente y de canto) que se mecen, el farol de las almas en medio (jaula,
     cristal con el plasma verde, llama de alma que tiembla), argolla arriba y garfio abajo; con velocidad, al rojo
     de Pyke. */
  chain(bar, g) {
    const { ctx, cx, w, h, top, energy } = g;
    const heat = bar.heat;
    const y1 = top + h;
    const mid = top + h / 2;
    const LH = clamp(h * 0.42, 18, 30);
    const LW = w + 2;
    const lTop = mid - LH / 2;
    const lBot = mid + LH / 2;
    const L = 12;
    const S = 8;
    bar.paintPlasma(h, 0.7 + energy * 0.45);
    const iron = lin(ctx, cx - w / 2, 0, cx + w / 2, 0, SKIN.frame);
    const hotIron = rgba(mixRgb(RGB.fire, RGB.ember, 0.4), heat * 0.75);
    // Halo del farol, que late.
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    const hs = 30 + energy * 22 + Math.sin(bar.t * 5) * 4;
    ctx.globalAlpha = 0.3 + energy * 0.45;
    ctx.drawImage(sprite(heat > 0.5 ? "fire" : "teal"), cx - hs / 2, mid - hs / 2, hs, hs);
    ctx.restore();
    // Eslabones.
    const links = [];
    for (let y = lTop - 3 - L / 2, i = 0; y - L / 2 > top + 4; y -= S, i++) links.push([y, i]);
    for (let y = lBot + 3 + L / 2, i = 0; y + L / 2 < y1 - 9; y += S, i++) links.push([y, i]);
    const lw = Math.max(7, w * 0.72);
    const sway = (i) => Math.sin(bar.t * 1.6 + i * 0.6) * 0.5 * (0.3 + energy);
    for (const pass of [1, 0]) {
      for (const [y, i] of links) {
        if (i % 2 !== pass) continue;
        const x = cx + sway(i);
        ctx.beginPath();
        if (pass) ctx.roundRect(x - 1.5, y - L / 2, 3, L, 1.5);
        else ctx.roundRect(x - lw / 2, y - L / 2, lw, L, lw / 2);
        if (pass) {
          ctx.fillStyle = iron;
          ctx.fill();
          ctx.strokeStyle = "rgba(0,0,0,0.75)";
          ctx.lineWidth = 0.5;
          ctx.stroke();
        } else {
          ctx.strokeStyle = "rgba(0,0,0,0.85)";
          ctx.lineWidth = 2.9;
          ctx.stroke();
          ctx.strokeStyle = iron;
          ctx.lineWidth = 2;
          ctx.stroke();
          if (heat > 0.02) {
            ctx.strokeStyle = hotIron;
            ctx.stroke();
          }
          ctx.beginPath();
          ctx.roundRect(x - lw / 2 + 0.6, y - L / 2 + 0.6, lw - 1.2, L - 1.2, lw / 2);
          ctx.strokeStyle = "rgba(255,255,255,0.28)";
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }
    }
    // Argolla de arriba.
    ctx.beginPath();
    ctx.arc(cx, top + 5, w * 0.32, Math.PI, TAU);
    ctx.moveTo(cx - w * 0.42, top + 5);
    ctx.lineTo(cx + w * 0.42, top + 5);
    ctx.strokeStyle = "rgba(0,0,0,0.8)";
    ctx.lineWidth = 2.6;
    ctx.stroke();
    ctx.strokeStyle = iron;
    ctx.lineWidth = 1.7;
    ctx.stroke();
    // Garfio de abajo (hacia la página), con su lengüeta.
    const hy = y1 - 8;
    ctx.beginPath();
    ctx.moveTo(cx, hy - 4);
    ctx.lineTo(cx, hy + 1);
    ctx.quadraticCurveTo(cx, hy + 8.5, cx - 5, hy + 7.5);
    ctx.quadraticCurveTo(cx - 8.6, hy + 6, cx - 8, hy + 1.5);
    ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(0,0,0,0.85)";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.strokeStyle = iron;
    ctx.lineWidth = 2;
    ctx.stroke();
    polygon(ctx, [[cx - 9.8, hy + 2.8], [cx - 8, hy - 2.6], [cx - 6.4, hy + 2.4]]);
    ctx.fillStyle = SKIN.metal[0];
    ctx.fill();
    // El farol: tapa, cristal con el alma, barrotes y base.
    const lx0 = cx - LW / 2;
    const lx1 = cx + LW / 2;
    polygon(ctx, [[lx0 + 0.5, lTop + 4.5], [cx - 2.6, lTop], [cx + 2.6, lTop], [lx1 - 0.5, lTop + 4.5]]);
    ctx.fillStyle = iron;
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.8)";
    ctx.lineWidth = 0.6;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, lTop - 1.6, 2.3, 0, TAU);
    ctx.strokeStyle = iron;
    ctx.lineWidth = 1.3;
    ctx.stroke();
    const glass = () => {
      ctx.beginPath();
      ctx.roundRect(lx0 + 1.4, lTop + 4.5, LW - 2.8, LH - 9, 2.5);
    };
    plasmaIn(bar, g, glass, lx0, LW, lTop, LH);
    // La llama del alma.
    ctx.save();
    glass();
    ctx.clip();
    ctx.globalCompositeOperation = "lighter";
    const fl = 1 + Math.sin(bar.t * 9) * 0.08 + Math.sin(bar.t * 23) * 0.05;
    const fy = mid + 2;
    ctx.beginPath();
    ctx.moveTo(cx, fy - 7 * fl);
    ctx.quadraticCurveTo(cx + 3.6, fy - 1, cx, fy + 3.5);
    ctx.quadraticCurveTo(cx - 3.6, fy - 1, cx, fy - 7 * fl);
    ctx.fillStyle = rgba(mixRgb(RGB.cyan, RGB.white, 0.5), 0.75 + energy * 0.25);
    ctx.fill();
    if (heat > 0.02) {
      ctx.fillStyle = rgba(RGB.fire, heat * 0.5);
      ctx.fillRect(lx0, lTop, LW, LH);
    }
    ctx.restore();
    ctx.beginPath();
    for (const x of [lx0 + 1.4, cx, lx1 - 1.4]) {
      ctx.moveTo(x, lTop + 4.5);
      ctx.lineTo(x, lBot - 4.5);
    }
    ctx.strokeStyle = "rgba(10,16,18,0.9)";
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.strokeStyle = "rgba(220,235,240,0.35)";
    ctx.lineWidth = 0.4;
    ctx.stroke();
    polygon(ctx, [[lx0 + 0.5, lBot - 4.5], [lx1 - 0.5, lBot - 4.5], [cx + 2.8, lBot], [cx - 2.8, lBot]]);
    ctx.fillStyle = iron;
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.8)";
    ctx.lineWidth = 0.6;
    ctx.stroke();
  },

  /* Guardián: escudo de torre. Canto de plata, cara de hielo con el plasma de escarcha y facetas, destellos que
     titilan, nieve en el borde de arriba, gemas engastadas y cristales de hielo que le crecen; la gema de Taric en
     el centro. */
  shield(bar, g) {
    const { ctx, cx, w, h, top, energy } = g;
    const x0 = cx - w / 2;
    const x1 = cx + w / 2;
    const y1 = top + h;
    const mid = top + h / 2;
    const point = Math.min(16, h * 0.22);
    const path = (i) => {
      ctx.beginPath();
      ctx.moveTo(x0 + i, top + 3 + i);
      ctx.quadraticCurveTo(cx, top - 1.5 + i * 1.3, x1 - i, top + 3 + i);
      ctx.lineTo(x1 - i, y1 - point);
      ctx.quadraticCurveTo(x1 - i, y1 - point * 0.35 - i * 0.3, cx, y1 - i * 1.4);
      ctx.quadraticCurveTo(x0 + i, y1 - point * 0.35 - i * 0.3, x0 + i, y1 - point);
      ctx.closePath();
    };
    bar.paintPlasma(h, 0.72 + energy * 0.4);
    halo(g, () => path(0));
    path(0);
    ctx.fillStyle = lin(ctx, x0, 0, x1, 0, SKIN.frame);
    ctx.fill();
    bevel(g, top, h);
    plasmaIn(bar, g, () => path(2.3), x0, w);
    ctx.save();
    path(2.3);
    ctx.clip();
    // Escarcha arriba y facetas desde la gema.
    ctx.fillStyle = lin(ctx, 0, top, 0, top + Math.min(40, h * 0.4), ["rgba(255,255,255,0.3)", "rgba(255,255,255,0)"]);
    ctx.fillRect(x0, top, w, h);
    ctx.beginPath();
    const fan = [[x0, top + 4], [cx, top], [x1, top + 4], [x0, mid - h * 0.22], [x1, mid - h * 0.22], [x0, mid + h * 0.18], [x1, mid + h * 0.18], [cx, y1]];
    for (const [x, y] of fan) {
      ctx.moveTo(cx, mid);
      ctx.lineTo(x, y);
    }
    ctx.strokeStyle = "rgba(255,255,255,0.13)";
    ctx.lineWidth = 0.7;
    ctx.stroke();
    // Destellos que titilan en el hielo.
    ctx.globalCompositeOperation = "lighter";
    for (let j = 0; j < Math.floor(h / 16); j++) {
      const a = Math.max(0, Math.sin(bar.t * 3 + j * 2.3)) ** 4;
      if (a < 0.05) continue;
      const x = cx + Math.sin(j * 12.9898) * (w / 2 - 3);
      const y = top + 6 + ((j * 37) % Math.max(1, h - 12));
      ctx.save();
      ctx.translate(x, y);
      star4Path(ctx, 3.4 + energy * 1.5);
      ctx.fillStyle = `rgba(255,255,255,${(a * 0.9).toFixed(3)})`;
      ctx.fill();
      ctx.restore();
    }
    ctx.restore();
    path(2.3);
    ctx.strokeStyle = "rgba(232,248,255,0.45)";
    ctx.lineWidth = 0.7;
    ctx.stroke();
    // Nieve en el borde de arriba.
    ctx.beginPath();
    ctx.moveTo(x0 - 0.5, top + 3.4);
    ctx.quadraticCurveTo(cx, top - 2.4, x1 + 0.5, top + 3.4);
    ctx.quadraticCurveTo(x1 - 2, top + 5.6, cx + 2, top + 3.6);
    ctx.quadraticCurveTo(cx - 1, top + 6, x0 + 2.5, top + 4.8);
    ctx.closePath();
    ctx.fillStyle = "rgba(250,253,255,0.92)";
    ctx.fill();
    // Gemas engastadas en el canto y cristales de hielo que crecen hacia fuera.
    for (let y = top + 18, k = 0; y < y1 - point - 6; y += 26, k++) {
      if (Math.abs(y - mid) < 14) continue;
      const side = k % 2 ? 1 : -1;
      const x = side > 0 ? x1 : x0;
      polygon(ctx, [[x, y - 2.6], [x + side * (4 + (k % 3)), y - 0.6], [x, y + 1.6]]);
      ctx.fillStyle = lin(ctx, x, y, x + side * 5, y, ["rgba(200,235,255,0.95)", "rgba(255,255,255,0.95)"]);
      ctx.fill();
      ctx.strokeStyle = "rgba(60,100,140,0.7)";
      ctx.lineWidth = 0.4;
      ctx.stroke();
      smallGem(ctx, side > 0 ? x0 + 1.2 : x1 - 1.2, y + 8, 1.5, "fire", energy * 0.5);
    }
    bar.drawMedallion(cx, mid, w, energy);
  },

  /* Peel: cápsula de cristal. Mitad menta (cura) y mitad rosa (escudo) con el plasma dentro, corazones, cruces y
     estrellas que suben flotando, brillo de cristal, anillo de nácar en la juntura y una burbuja de escudo alrededor
     que respira. */
  capsule(bar, g) {
    const { ctx, cx, w, h, top, energy } = g;
    const r = w / 2;
    const x0 = cx - r;
    const y1 = top + h;
    const mid = top + h / 2;
    const path = (i) => {
      ctx.beginPath();
      ctx.roundRect(x0 + i, top + i, w - 2 * i, h - 2 * i, Math.max(1, r - i));
    };
    bar.paintPlasma(h, 0.75 + energy * 0.35);
    // Burbuja de escudo.
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    const breathe = Math.sin(bar.t * 4) * 0.8;
    ctx.beginPath();
    ctx.ellipse(cx, mid, r + 4 + breathe, h / 2 + 4 + breathe, 0, 0, TAU);
    ctx.strokeStyle = rgba(RGB.teal, 0.16 + energy * 0.35);
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.strokeStyle = rgba(RGB.cyan, 0.08 + energy * 0.2);
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.restore();
    halo(g, () => path(0));
    path(0);
    ctx.fillStyle = "rgba(24,18,52,0.9)";
    ctx.fill();
    plasmaIn(bar, g, () => path(1.3), x0, w);
    ctx.save();
    path(1.3);
    ctx.clip();
    ctx.globalCompositeOperation = "lighter";
    ctx.fillStyle = rgba(RGB.teal, 0.2);
    ctx.fillRect(x0, top, w, h / 2);
    ctx.fillStyle = rgba(RGB.cyan, 0.24);
    ctx.fillRect(x0, mid, w, h / 2);
    // Lo que flota dentro.
    const n = Math.max(2, Math.floor(h / 20));
    for (let k = 0; k < n; k++) {
      const phase = (k * 0.37 + bar.t * 0.11 * (1 + (k % 3) * 0.3)) % 1;
      const y = y1 - 5 - phase * (h - 10);
      const x = cx + Math.sin(k * 2.7 + bar.t * 1.5) * r * 0.35;
      const a = Math.sin(phase * Math.PI) * (0.5 + energy * 0.45);
      ctx.save();
      ctx.translate(x, y);
      ctx.globalAlpha = a;
      const shape = ["heart", "plus", "star4"][k % 3];
      SHAPES[shape](ctx, { col: k % 2 ? [255, 255, 255] : RGB.gold }, 3.6 + (k % 2) * 0.8);
      ctx.restore();
    }
    ctx.globalAlpha = 1;
    // Brillo del cristal.
    ctx.globalCompositeOperation = "source-over";
    ctx.beginPath();
    ctx.roundRect(x0 + 1.8, top + 3, w * 0.24, h - 6, w * 0.12);
    ctx.fillStyle = lin(ctx, 0, top, 0, y1, [[0, "rgba(255,255,255,0.7)"], [0.3, "rgba(255,255,255,0.18)"], [0.7, "rgba(255,255,255,0.1)"], [1, "rgba(255,255,255,0.45)"]]);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(cx + r * 0.3, top + r * 0.9, r * 0.22, r * 0.4, 0, 0, TAU);
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.fill();
    ctx.fillStyle = lin(ctx, x0, 0, x0 + w, 0, [[0.6, "rgba(20,10,40,0)"], [1, "rgba(20,10,40,0.45)"]]);
    ctx.fillRect(x0, top, w, h);
    ctx.restore();
    // Anillo de nácar en la juntura y canto.
    ctx.save();
    path(0);
    ctx.clip();
    ctx.fillStyle = lin(ctx, x0, 0, x0 + w, 0, SKIN.frame);
    ctx.fillRect(x0, mid - 1.6, w, 3.2);
    ctx.restore();
    path(0);
    ctx.strokeStyle = lin(ctx, x0, top, x0 + w, y1, SKIN.frame);
    ctx.lineWidth = 1.3;
    ctx.stroke();
    path(1.1);
    ctx.strokeStyle = "rgba(255,255,255,0.3)";
    ctx.lineWidth = 0.5;
    ctx.stroke();
    bar.drawMedallion(cx, mid, w, energy);
  },
};

/** Hoja con su nervio, nacida en ``x, y`` y girada ``a`` (0: hacia arriba). */
function leaf(ctx, x, y, a, len, wid, energy) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(a);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.quadraticCurveTo(wid, -len * 0.45, 0, -len);
  ctx.quadraticCurveTo(-wid, -len * 0.45, 0, 0);
  ctx.closePath();
  ctx.fillStyle = lin(ctx, 0, 0, 0, -len, ["#1f6a2a", energy > 0.4 ? "#7ee06a" : "#5cbf55", "#b6f08a"]);
  ctx.fill();
  ctx.strokeStyle = "rgba(10,40,14,0.75)";
  ctx.lineWidth = 0.45;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, -0.5);
  ctx.lineTo(0, -len * 0.82);
  ctx.strokeStyle = "rgba(226,255,190,0.55)";
  ctx.lineWidth = 0.4;
  ctx.stroke();
  ctx.restore();
}

/** Marca de impacto (ADC) al chocar con un extremo: una X que se abre y se apaga. */
export function drawHitMarker(bar, ctx, now) {
  const k = (now - (bar.hitAt || -1e9)) / 300;
  if (k >= 1) return;
  const y = bar.hitY;
  const gap = 3 + k * 4;
  const len = 4.5;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = `rgba(255,255,255,${(1 - k).toFixed(3)})`;
  ctx.lineWidth = 1.5;
  ctx.lineCap = "round";
  ctx.beginPath();
  for (const [dx, dy] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
    ctx.moveTo(bar.cx + dx * gap, y + dy * gap);
    ctx.lineTo(bar.cx + dx * (gap + len), y + dy * (gap + len));
  }
  ctx.stroke();
  ctx.restore();
}
