"""Genera web/css/theme-r-*.css, los temas por línea (web/js/theme.js): paleta, letra, esquinas, fondos y, desde
arte.py, cenefa, emblema, blasón, raíl de la barra, bordes y escenas. Los SVG van en data URIs.

Uso (desde la raíz del repo):  python tools/temas/generar.py web/css
No lo usa la app: el CSS generado es el que se sube; esto es para retocarlo y volver a generarlo."""
import random
import sys
from pathlib import Path
from urllib.parse import quote

from arte import ART, ART_CSS

OUT = Path(sys.argv[1])


def svg(body: str, w: float, h: float) -> str:
    raw = f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}'>{body}</svg>"
    raw = " ".join(raw.split())
    return 'url("data:image/svg+xml,' + quote(raw, safe=" '=:/;,.()-_!*~") + '")'


def particles(seed, tile, n, draw):
    rnd = random.Random(seed)
    return "".join(draw(rnd, rnd.uniform(0, tile), rnd.uniform(0, tile)) for _ in range(n))


def glow_dot(color, r_min, r_max, a_min=0.4, a_max=1.0):
    def draw(rnd, x, y):
        r = rnd.uniform(r_min, r_max)
        a = rnd.uniform(a_min, a_max)
        return (f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r * 3:.1f}' fill='url(#g)' opacity='{a * 0.6:.2f}'/>"
                f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r:.1f}' fill='{color}' opacity='{a:.2f}'/>")
    return draw


def glow_defs(color):
    return (f"<defs><radialGradient id='g'><stop offset='0' stop-color='{color}' stop-opacity='0.7'/>"
            f"<stop offset='1' stop-color='{color}' stop-opacity='0'/></radialGradient></defs>")


def star4(cx, cy, r, color, op=1.0):
    k = r * 0.22
    return (f"<path d='M{cx} {cy - r} L{cx + k} {cy - k} L{cx + r} {cy} L{cx + k} {cy + k} L{cx} {cy + r} L{cx - k} {cy + k} "
            f"L{cx - r} {cy} L{cx - k} {cy - k}Z' fill='{color}' opacity='{op:.2f}'/>")


def corners(img, size, inset="0px"):
    """El mismo adorno en las cuatro esquinas (girado con cuatro versiones del SVG)."""
    tl, tr, bl, br = img
    return (f"{tl} top {inset} left {inset} / {size} {size} no-repeat border-box, "
            f"{tr} top {inset} right {inset} / {size} {size} no-repeat border-box, "
            f"{bl} bottom {inset} left {inset} / {size} {size} no-repeat border-box, "
            f"{br} bottom {inset} right {inset} / {size} {size} no-repeat border-box")


def rotations(body, s):
    """Cuatro versiones de un adorno de esquina (arriba-izquierda, arriba-derecha, abajo-izquierda, abajo-derecha)."""
    return tuple(
        svg(f"<g transform='{t}'>{body}</g>", s, s)
        for t in ("", f"translate({s} 0) scale(-1 1)", f"translate(0 {s}) scale(1 -1)", f"translate({s} {s}) scale(-1 -1)")
    )


def write(theme_id, title, comment, tokens: dict, extra: str = ""):
    # El arte de cada tema (arte.py): cenefa, emblema, blasón, raíl de la barra, bordes y escenas animadas.
    tokens = {**tokens, **ART.get(theme_id, {})}
    extra = extra + ART_CSS.get(theme_id, "")
    sel = f':root[data-theme="{theme_id}"]'
    lines = [f"/* {'=' * 69}", f"   Tema {title} (web/js/theme.js). {comment}", f"   {'=' * 69} */", sel + " {"]
    for k, v in tokens.items():
        lines.append(f"  {k}: {v};")
    lines.append("}")
    css = "\n".join(lines) + "\n" + extra.strip() + "\n"
    (OUT / f"theme-{theme_id}.css").write_text(css, encoding="utf-8")
    print(theme_id, len(css))


# ====================================================================== TOP: forja y tanques
tread = svg(
    "<g fill='none' stroke='#9aa4ae' stroke-opacity='0.07' stroke-width='3' stroke-linecap='round'>"
    "<path d='M6 18 L18 6'/><path d='M30 42 L42 30'/><path d='M30 6 L42 18'/><path d='M6 30 L18 42'/></g>",
    48, 48)
embers_def = glow_defs("#ff7a1e")
embers = svg(embers_def + particles(11, 400, 26, glow_dot("#ffb066", 0.6, 1.8)), 400, 400)
embers2 = svg(glow_defs("#ff4a10") + particles(12, 600, 14, glow_dot("#ff8a3a", 1.2, 2.6, 0.3, 0.8)), 600, 600)
rivet = ("<circle cx='9' cy='9' r='3.2' fill='#1b1f23'/><circle cx='9' cy='9' r='2.6' fill='url(#r)'/>"
         "<defs><radialGradient id='r' cx='0.35' cy='0.35'><stop offset='0' stop-color='#f4f6f8'/>"
         "<stop offset='0.5' stop-color='#8a949e'/><stop offset='1' stop-color='#2f353c'/></radialGradient></defs>"
         "<path d='M2 1 H16' stroke='#c3ccd5' stroke-opacity='0.7' stroke-width='1.5'/>"
         "<path d='M1 2 V16' stroke='#c3ccd5' stroke-opacity='0.7' stroke-width='1.5'/>")
top_corners = corners(rotations(rivet, 18), "18px")
top_divider = svg(
    "<defs><linearGradient id='l' x2='1'><stop offset='0' stop-color='#ff8a2a'/><stop offset='1' stop-color='#ff8a2a' stop-opacity='0'/></linearGradient>"
    "<linearGradient id='s' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#eef1f3'/><stop offset='1' stop-color='#5d666f'/></linearGradient></defs>"
    "<path d='M1 1 H13 V7 Q13 11 7 13 Q1 11 1 7 Z' fill='url(#s)' stroke='#1b1f23' stroke-width='0.8'/>"
    "<path d='M7 3 V11 M3 6 H11' stroke='#ff8a2a' stroke-width='1.3'/>"
    "<rect x='18' y='5.5' width='190' height='2.4' fill='url(#l)'/><rect x='18' y='9' width='120' height='0.8' fill='#8a949e' opacity='0.6'/>",
    210, 14)
top_mark = svg("<path d='M1 1 H13 V7 Q13 11 7 13 Q1 11 1 7 Z' fill='#ff8a2a' stroke='#ffd2a8' stroke-width='0.8'/>", 14, 14)
write("r-top", "Top: forja y tanques", "Acero con remaches, chapa estriada de fondo, lava y ascuas que suben.", {
    "--bg": "#0b0d10", "--bg-2": "#101317", "--panel": "#15191e", "--panel-2": "#111418", "--raised": "#1c2127",
    "--line": "#262c33", "--line-2": "#3a424b", "--text": "#eceff2", "--text-2": "#bcc3ca", "--muted": "#8a929b", "--faint": "#5c646c",
    "--gold": "#ff8a2a", "--gold-2": "#ffd2a8", "--gold-soft": "rgba(255, 138, 42, 0.14)", "--blue": "#9fb3c8", "--blue-soft": "rgba(159, 179, 200, 0.14)",
    "--good": "#5fd08a", "--good-soft": "rgba(95, 208, 138, 0.14)", "--bad": "#ff5a4a", "--bad-soft": "rgba(255, 90, 74, 0.14)",
    "--warn": "#ffb340", "--warn-soft": "rgba(255, 179, 64, 0.14)",
    "--t-display": '"Oswald", "Arial Narrow", sans-serif', "--t-display-weight": "700", "--t-display-spacing": "2px",
    "--t-radius": "2px", "--t-accent": "#ff8a2a", "--t-accent-rgb": "255, 138, 42", "--t-accent-2": "#c3ccd5",
    "--t-title": "#eef1f3", "--t-title-grad": "linear-gradient(180deg, #ffffff, #aab4be)",
    "--t-title-shadow": "0 1px 0 #000, 0 -1px 0 rgba(255, 255, 255, 0.14), 0 0 18px rgba(255, 138, 42, 0.18)",
    "--t-section": "#ff8a2a", "--t-edge": "#4a535c",
    "--t-edge-grad": "linear-gradient(90deg, #2a3036, #8a949e 30%, #ff8a2a 50%, #8a949e 70%, #2a3036)",
    "--t-body": f"radial-gradient(1000px 500px at 50% 115%, rgba(255, 106, 20, 0.16), transparent 65%), {tread}, linear-gradient(180deg, #121519, #0b0d10 50%, #08090b)",
    "--t-ambient": embers, "--t-ambient-tile": "400px", "--t-drift-y": "-400px", "--t-ambient-speed": "26s", "--t-ambient-opacity": "0.85",
    "--t-ambient2": embers2, "--t-ambient2-tile": "600px", "--t-drift2-y": "-600px", "--t-drift2-x": "60px", "--t-ambient2-speed": "48s",
    "--t-card-deco": top_corners + ", repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.014) 0 1px, transparent 1px 3px) padding-box",
    "--t-card-fill": "linear-gradient(180deg, #1b2026, #111418)",
    "--t-card-frame": "linear-gradient(180deg, #8a949e, #3a424b 45%, #23282d)",
    "--t-card-shadow": "inset 0 1px 0 rgba(255, 255, 255, 0.06), inset 0 -2px 0 rgba(0, 0, 0, 0.45), 0 14px 32px rgba(0, 0, 0, 0.55)",
    "--t-divider": top_divider, "--t-divider-w": "210px", "--t-divider-h": "14px",
    "--t-btn-fill": "linear-gradient(180deg, #3a424b, #22282e)", "--t-btn-frame": "linear-gradient(180deg, #aab4be, #2a3036)", "--t-btn-color": "#eceff2",
    "--t-btn-hover-fill": "linear-gradient(180deg, #46505a, #262c33)", "--t-btn-hover-frame": "linear-gradient(180deg, #ffd2a8, #ff8a2a)",
    "--t-pri-fill": "linear-gradient(180deg, #ff9a3a, #c2410c)", "--t-pri-frame": "linear-gradient(180deg, #ffe2c4, #7a2a06)", "--t-pri-color": "#1a0d05",
    "--t-pri-glow": "0 0 14px rgba(255, 122, 40, 0.45), inset 0 1px 0 rgba(255, 230, 200, 0.5)", "--t-pri-text-shadow": "0 1px 0 rgba(255, 220, 180, 0.4)",
    "--t-pri-hover-color": "#000",
    "--t-input-bg": "#0d1013", "--t-top-bg": "linear-gradient(180deg, #1b2026, #0f1216)", "--t-table-head": "linear-gradient(180deg, #262c33, #171b20)",
    "--t-nav-mark": top_mark, "--t-brand-clip": "polygon(0 0, 100% 0, 100% 60%, 50% 100%, 0 60%)",
    "--t-brand-bg": "linear-gradient(160deg, #eef1f3, #8a949e 45%, #ff8a2a)", "--t-brand-color": "#1b1f23",
    "--t-bar-a": "linear-gradient(180deg, #dfe4e9, #6b747d)", "--t-bar-b": "linear-gradient(180deg, #ffd2a8, #ff8a2a 45%, #c2410c)",
    "--t-side-a": "#15191d", "--t-side-b": "#0d1013", "--t-side-pattern": tread,
    "--hx-cream": "255, 236, 214", "--hx-gold": "196, 204, 212", "--hx-gold-2": "255, 138, 42", "--hx-bronze": "90, 98, 108",
    "--hx-deep": "40, 45, 52", "--hx-teal": "255, 122, 40", "--hx-teal-2": "255, 214, 160",
}, """
[data-theme="r-top"] .kpi-value { text-shadow: 0 1px 0 #000, 0 -1px 0 rgba(255, 255, 255, 0.12); }
[data-theme="r-top"] .card-title svg { filter: drop-shadow(0 0 6px rgba(255, 122, 40, 0.8)); }
""")

# ====================================================================== JUNGLA: maleza y objetivos
leaf = "M0 -9 Q6 -2 0 9 Q-6 -2 0 -9Z"
foliage = svg(
    "".join(f"<path d='{leaf}' transform='translate({x} {y}) rotate({r}) scale({s})' fill='#5fae5a' opacity='{o}'/>"
            for x, y, r, s, o in [(12, 14, 30, 1.0, 0.05), (52, 40, -40, 0.8, 0.04), (30, 70, 80, 1.1, 0.045), (78, 18, 140, 0.9, 0.04), (70, 74, -20, 0.7, 0.05)]),
    90, 90)
fireflies = svg(glow_defs("#d8ff6a") + particles(21, 420, 20, glow_dot("#efffb0", 0.8, 1.6, 0.5, 1.0)), 420, 420)
falling = svg("".join(
    f"<path d='{leaf}' transform='translate({x:.0f} {y:.0f}) rotate({r}) scale({s:.2f})' fill='{c}' opacity='0.55'/>"
    for (x, y, r, s, c) in [(random.Random(31 + i).uniform(0, 600), random.Random(41 + i).uniform(0, 600), random.Random(51 + i).uniform(0, 360),
                             random.Random(61 + i).uniform(0.5, 0.9), ["#6fdc7a", "#c9a45c", "#3f8f4a"][i % 3]) for i in range(9)]), 600, 600)
sprig = ("<path d='M2 2 Q14 6 30 4' stroke='#7a5a32' stroke-width='1.6' fill='none'/>"
         "<path d='M2 2 Q6 14 4 30' stroke='#7a5a32' stroke-width='1.6' fill='none'/>"
         "<path d='M12 4 Q18 -4 24 4 Q18 9 12 4Z' fill='#6fdc7a'/><path d='M4 12 Q-4 18 4 24 Q9 18 4 12Z' fill='#4fbf5c'/>"
         "<path d='M6 6 Q14 4 15 13 Q7 14 6 6Z' fill='#9be86f'/>"
         "<circle cx='5' cy='5' r='2.2' fill='#ffcf5a'/>")
jg_corners = corners(rotations(sprig, 32), "32px", "-1px")
jg_divider = svg(
    "<path d='M2 7 Q40 1 80 7 T160 7 T220 6' stroke='#5a4024' stroke-width='1.6' fill='none'/>"
    + "".join(f"<path d='{leaf}' transform='translate({x} {y}) rotate({r}) scale(0.7)' fill='{c}'/>"
              for x, y, r, c in [(16, 3, -60, "#6fdc7a"), (48, 10, 120, "#4fbf5c"), (84, 3, -50, "#9be86f"), (122, 11, 110, "#6fdc7a"), (160, 4, -70, "#4fbf5c")])
    + "<circle cx='200' cy='7' r='3' fill='#a46bff'/><circle cx='200' cy='7' r='5.5' fill='#a46bff' opacity='0.3'/>",
    224, 14)
jg_mark = svg(f"<path d='{leaf}' transform='translate(7 7) rotate(35) scale(0.75)' fill='#6fdc7a' stroke='#e8ffd0' stroke-width='0.6'/>", 14, 14)
write("r-jungle", "Jungla: maleza y objetivos", "Musgo y corteza, hojas en las esquinas, luciérnagas y hojas que caen; morado de Barón y ámbar de dragón.", {
    "--bg": "#050d08", "--bg-2": "#08130c", "--panel": "#0c1a11", "--panel-2": "#0a160e", "--raised": "#12251a",
    "--line": "#1b3324", "--line-2": "#2d4a2f", "--text": "#ecf5e2", "--text-2": "#bccdac", "--muted": "#82a07b", "--faint": "#567053",
    "--gold": "#c9a45c", "--gold-2": "#f2deb0", "--gold-soft": "rgba(201, 164, 92, 0.14)", "--blue": "#a46bff", "--blue-soft": "rgba(164, 107, 255, 0.14)",
    "--good": "#6fdc7a", "--good-soft": "rgba(111, 220, 122, 0.14)", "--bad": "#ff6a4a", "--bad-soft": "rgba(255, 106, 74, 0.14)",
    "--warn": "#ffb347", "--warn-soft": "rgba(255, 179, 71, 0.14)",
    "--t-display": '"IM Fell English SC", Georgia, serif', "--t-display-weight": "400", "--t-display-spacing": "1.6px", "--t-display-case": "none",
    "--t-number-font": "var(--font)", "--t-radius": "6px", "--t-accent": "#6fdc7a", "--t-accent-rgb": "111, 220, 122", "--t-accent-2": "#a46bff",
    "--t-title": "#ecf5e2", "--t-title-grad": "linear-gradient(180deg, #f6ffe8, #9be86f)",
    "--t-title-shadow": "0 0 16px rgba(111, 220, 122, 0.3), 0 2px 0 rgba(0, 0, 0, 0.6)",
    "--t-section": "#c9a45c", "--t-edge": "#3d5a2a",
    "--t-edge-grad": "linear-gradient(90deg, #1b3324, #6fdc7a 35%, #c9a45c 50%, #6fdc7a 65%, #1b3324)",
    "--t-body": f"radial-gradient(900px 520px at 20% 0%, rgba(111, 220, 122, 0.1), transparent 60%), radial-gradient(800px 500px at 90% 100%, rgba(164, 107, 255, 0.1), transparent 60%), {foliage}, linear-gradient(180deg, #08160d, #040a06)",
    "--t-ambient": fireflies, "--t-ambient-tile": "420px", "--t-drift-x": "140px", "--t-drift-y": "-420px", "--t-ambient-speed": "70s", "--t-ambient-opacity": "0.9",
    "--t-ambient2": falling, "--t-ambient2-tile": "600px", "--t-drift2-x": "-120px", "--t-drift2-y": "600px", "--t-ambient2-speed": "60s", "--t-ambient2-opacity": "0.35",
    "--t-card-deco": jg_corners + ", radial-gradient(120% 70% at 50% 0%, rgba(111, 220, 122, 0.07), transparent 60%) padding-box",
    "--t-card-fill": "linear-gradient(180deg, rgba(14, 30, 18, 0.96), rgba(6, 14, 9, 0.97))",
    "--t-card-frame": "linear-gradient(180deg, #4a7a3a, #24341a 55%, #4a3420)",
    "--t-card-shadow": "0 14px 32px rgba(0, 0, 0, 0.55), inset 0 0 30px rgba(0, 0, 0, 0.3)",
    "--t-divider": jg_divider, "--t-divider-w": "224px", "--t-divider-h": "14px",
    "--t-btn-fill": "linear-gradient(180deg, #2a3a1f, #17210f)", "--t-btn-frame": "linear-gradient(180deg, #9bc46a, #3d5a2a)", "--t-btn-color": "#ecf5e2",
    "--t-btn-hover-fill": "linear-gradient(180deg, #34492a, #1c2914)", "--t-btn-hover-frame": "linear-gradient(180deg, #e8ffd0, #6fdc7a)",
    "--t-pri-fill": "linear-gradient(180deg, #4fbf5c, #1d5e27)", "--t-pri-frame": "linear-gradient(180deg, #e8ffd0, #4caf50 45%, #1d5e27)", "--t-pri-color": "#f6ffe8",
    "--t-pri-glow": "0 0 14px rgba(111, 220, 122, 0.45), inset 0 1px 0 rgba(230, 255, 210, 0.35)", "--t-pri-text-shadow": "0 1px 2px rgba(0, 40, 0, 0.6)",
    "--t-input-bg": "#061009", "--t-top-bg": "linear-gradient(180deg, #102418, #07110a)", "--t-table-head": "linear-gradient(180deg, #16301f, #0c1a11)",
    "--t-nav-mark": jg_mark, "--t-brand-radius": "50% 0 50% 0", "--t-brand-bg": "linear-gradient(150deg, #e8ffd0, #6fdc7a 45%, #2e7d32)", "--t-brand-color": "#0a1a0c",
    "--t-bar-a": "linear-gradient(180deg, #cfffb0, #6fdc7a 45%, #2e7d32)", "--t-bar-b": "linear-gradient(180deg, #e2c6ff, #a46bff 45%, #5b2bb0)",
    "--t-side-a": "#0c1d12", "--t-side-b": "#06100a", "--t-side-pattern": foliage,
    "--hx-cream": "236, 255, 214", "--hx-gold": "206, 166, 96", "--hx-gold-2": "111, 220, 122", "--hx-bronze": "90, 70, 40",
    "--hx-deep": "40, 30, 16", "--hx-teal": "86, 214, 112", "--hx-teal-2": "206, 255, 176",
})

# ====================================================================== MID: dagas y magia
stars_bg = svg("".join(f"<circle cx='{random.Random(71 + i).uniform(0, 300):.0f}' cy='{random.Random(81 + i).uniform(0, 300):.0f}' r='{random.Random(91 + i).uniform(0.3, 0.9):.1f}' fill='#ffffff' opacity='{random.Random(101 + i).uniform(0.15, 0.5):.2f}'/>" for i in range(40)), 300, 300)
arcane = svg(glow_defs("#b07aff") + particles(111, 420, 14, glow_dot("#e4d2ff", 0.6, 1.4, 0.5, 1.0))
             + "".join(star4(random.Random(121 + i).uniform(10, 410), random.Random(131 + i).uniform(10, 410), random.Random(141 + i).uniform(2.5, 5), ["#b07aff", "#7be0ff", "#ff6ad5"][i % 3], 0.8) for i in range(9)), 420, 420)
runes = svg("<g fill='none' stroke='#9a6bff' stroke-opacity='0.12'>"
            "<circle cx='300' cy='300' r='150' stroke-width='1.2'/><circle cx='300' cy='300' r='132' stroke-dasharray='3 7'/>"
            "<path d='M300 150 L430 375 L170 375Z'/><path d='M300 450 L170 225 L430 225Z'/>"
            "<circle cx='90' cy='520' r='60'/><circle cx='90' cy='520' r='48' stroke-dasharray='2 5'/></g>", 600, 600)
dagger = ("<defs><linearGradient id='b' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#ffffff'/><stop offset='1' stop-color='#8e95b0'/></linearGradient></defs>"
          "<path d='M3 3 L22 13 L13 22 Z' fill='url(#b)' opacity='0.95'/><path d='M3 3 L22 13' stroke='#ffffff' stroke-width='0.6'/>"
          "<path d='M17 25 L25 17' stroke='#b07aff' stroke-width='2.2' stroke-linecap='round'/><circle cx='27' cy='27' r='2.2' fill='#ff6ad5'/>")
mid_corners = corners(rotations(dagger, 30), "30px", "-2px")
mid_divider = svg(
    "<defs><linearGradient id='l' x2='1'><stop offset='0' stop-color='#b07aff'/><stop offset='0.6' stop-color='#ff6ad5' stop-opacity='0.6'/><stop offset='1' stop-color='#ff6ad5' stop-opacity='0'/></linearGradient>"
    "<linearGradient id='b' x2='1'><stop offset='0' stop-color='#ffffff'/><stop offset='1' stop-color='#8e95b0'/></linearGradient></defs>"
    "<path d='M2 7 L34 4.5 L40 7 L34 9.5 Z' fill='url(#b)'/><rect x='40' y='3' width='2.5' height='8' fill='#b07aff'/><rect x='42' y='6' width='9' height='2' fill='#5b4aa0'/>"
    + star4(60, 7, 5.5, "#ff6ad5") + "<rect x='70' y='6.3' width='150' height='1.4' fill='url(#l)'/>",
    220, 14)
mid_mark = svg(star4(7, 7, 6.5, "#d6c2ff"), 14, 14)
write("r-mid", "Mid: dagas y magia", "Plata de hoja y magia arcana: dagas en las esquinas, cielo estrellado, chispas y círculos de runas.", {
    "--bg": "#07051a", "--bg-2": "#0b0822", "--panel": "#120d2c", "--panel-2": "#0e0a24", "--raised": "#1a1440",
    "--line": "#241c4a", "--line-2": "#3a2f6e", "--text": "#f3f0ff", "--text-2": "#c8c0ea", "--muted": "#9189ba", "--faint": "#5e5788",
    "--gold": "#c9cfe6", "--gold-2": "#ffffff", "--gold-soft": "rgba(176, 122, 255, 0.14)", "--blue": "#7be0ff", "--blue-soft": "rgba(123, 224, 255, 0.14)",
    "--good": "#6ff0c0", "--good-soft": "rgba(111, 240, 192, 0.14)", "--bad": "#ff5c8a", "--bad-soft": "rgba(255, 92, 138, 0.14)",
    "--warn": "#ffc35c", "--warn-soft": "rgba(255, 195, 92, 0.14)",
    "--t-display": '"Cormorant SC", Georgia, serif', "--t-display-weight": "700", "--t-display-spacing": "2px", "--t-display-case": "none",
    "--t-radius": "1px", "--t-accent": "#b07aff", "--t-accent-rgb": "176, 122, 255", "--t-accent-2": "#ff6ad5",
    "--t-title": "#f3f0ff", "--t-title-grad": "linear-gradient(180deg, #ffffff, #c9b2ff)",
    "--t-title-shadow": "0 0 16px rgba(176, 122, 255, 0.5), 0 0 2px rgba(255, 106, 213, 0.6)",
    "--t-section": "#9a8acb", "--t-edge": "#4a3f8a",
    "--t-edge-grad": "linear-gradient(90deg, #241c4a, #b07aff 30%, #ff6ad5 50%, #7be0ff 70%, #241c4a)",
    "--t-body": f"radial-gradient(900px 600px at 85% -10%, rgba(176, 122, 255, 0.18), transparent 60%), radial-gradient(800px 600px at 0% 100%, rgba(255, 106, 213, 0.12), transparent 60%), {stars_bg}, linear-gradient(180deg, #0b0824, #05031a)",
    "--t-ambient": arcane, "--t-ambient-tile": "420px", "--t-drift-y": "-420px", "--t-drift-x": "-60px", "--t-ambient-speed": "55s", "--t-ambient-opacity": "0.9",
    "--t-ambient2": runes, "--t-ambient2-tile": "600px", "--t-drift2-x": "600px", "--t-drift2-y": "0px", "--t-ambient2-speed": "160s", "--t-ambient2-opacity": "0.8",
    "--t-card-deco": mid_corners + ", radial-gradient(110% 60% at 50% 0%, rgba(176, 122, 255, 0.1), transparent 60%) padding-box",
    "--t-card-fill": "linear-gradient(180deg, rgba(22, 16, 52, 0.95), rgba(9, 6, 26, 0.97))",
    "--t-card-frame": "linear-gradient(160deg, #e6e9ff, #6a5bd0 35%, #2a2160 65%, #ff6ad5)",
    "--t-card-shadow": "0 14px 34px rgba(0, 0, 0, 0.55), 0 0 22px rgba(176, 122, 255, 0.08)",
    "--t-divider": mid_divider, "--t-divider-w": "220px", "--t-divider-h": "14px",
    "--t-btn-fill": "linear-gradient(180deg, #241c52, #140f2e)", "--t-btn-frame": "linear-gradient(180deg, #e6e9ff, #4a3f8a)", "--t-btn-color": "#ebe6ff",
    "--t-btn-hover-fill": "linear-gradient(180deg, #2e2468, #1a1440)", "--t-btn-hover-frame": "linear-gradient(180deg, #ffffff, #b07aff)",
    "--t-pri-fill": "linear-gradient(180deg, #9a6bff, #4b23b8)", "--t-pri-frame": "linear-gradient(180deg, #ffd6ff, #ff6ad5 40%, #5b2bd0)", "--t-pri-color": "#ffffff",
    "--t-pri-glow": "0 0 16px rgba(176, 122, 255, 0.55), inset 0 0 12px rgba(255, 106, 213, 0.25)", "--t-pri-text-shadow": "0 0 8px rgba(255, 214, 255, 0.7)",
    "--t-input-bg": "#080616", "--t-top-bg": "linear-gradient(180deg, #160f36, #08061a)", "--t-table-head": "linear-gradient(180deg, #1e1748, #120d2c)",
    "--t-nav-mark": mid_mark, "--t-brand-clip": "polygon(50% 0, 100% 50%, 50% 100%, 0 50%)",
    "--t-brand-bg": "linear-gradient(135deg, #ffffff, #a3a9c4 40%, #9a6bff)", "--t-brand-color": "#120d2c",
    "--t-bar-a": "linear-gradient(180deg, #d8f6ff, #7be0ff 45%, #2b7bd0)", "--t-bar-b": "linear-gradient(180deg, #ffd6ff, #ff6ad5 45%, #8a1a7a)",
    "--t-side-a": "#120c2e", "--t-side-b": "#08061a", "--t-side-pattern": stars_bg,
    "--hx-cream": "245, 240, 255", "--hx-gold": "222, 226, 246", "--hx-gold-2": "176, 122, 255", "--hx-bronze": "90, 80, 140",
    "--hx-deep": "40, 30, 80", "--hx-teal": "176, 122, 255", "--hx-teal-2": "128, 222, 255",
})

# ====================================================================== ADC: tiradores y armas
hud = svg("<g stroke='#e2b24c' stroke-opacity='0.05' fill='none'><path d='M0 0.5 H80 M0.5 0 V80'/></g>"
          "<g stroke='#e2b24c' stroke-opacity='0.025'><path d='M0 40.5 H80 M40.5 0 V80'/></g>", 80, 80)
def tracer(rnd, x, y):
    ln = rnd.uniform(30, 70)
    return (f"<line x1='{x:.0f}' y1='{y:.0f}' x2='{x + ln:.0f}' y2='{y - ln * 0.5:.0f}' stroke='url(#t)' stroke-width='{rnd.uniform(0.8, 1.6):.1f}' stroke-linecap='round'/>")
tracers = svg("<defs><linearGradient id='t' x2='1' y2='-0.5'><stop offset='0' stop-color='#ff4a3a' stop-opacity='0'/><stop offset='0.85' stop-color='#ffb070'/>"
              "<stop offset='1' stop-color='#fff4dc'/></linearGradient></defs>" + particles(151, 500, 9, tracer), 500, 500)
casings = svg(glow_defs("#ffcf6a") + particles(161, 600, 12, glow_dot("#ffe6a8", 0.5, 1.2, 0.3, 0.8)), 600, 600)
bracket = "<path d='M1.5 13 V1.5 H13' stroke='#e2b24c' stroke-width='2' fill='none'/><path d='M5 9 V5 H9' stroke='#ec343e' stroke-width='1.2' fill='none'/>"
adc_corners = corners(rotations(bracket, 16), "16px", "3px")
adc_divider = svg(
    "<defs><linearGradient id='c' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#fff0c4'/><stop offset='1' stop-color='#a87b2c'/></linearGradient>"
    "<linearGradient id='l' x2='1'><stop offset='0' stop-color='#ec343e'/><stop offset='1' stop-color='#ec343e' stop-opacity='0'/></linearGradient></defs>"
    "<rect x='2' y='4' width='14' height='6' rx='1' fill='url(#c)'/><path d='M16 4 H20 Q26 7 20 10 H16Z' fill='#c9cfd6'/>"
    "<rect x='30' y='6.3' width='190' height='1.4' fill='url(#l)'/>"
    "<g fill='none' stroke='#e2b24c' stroke-width='1'><circle cx='214' cy='7' r='5'/><path d='M214 0 V4 M214 10 V14 M207 7 H211 M217 7 H221'/></g>",
    224, 14)
adc_mark = svg("<g fill='none' stroke='#ec343e' stroke-width='1.4'><circle cx='7' cy='7' r='4.2'/><path d='M7 0.5 V3.5 M7 10.5 V13.5 M0.5 7 H3.5 M10.5 7 H13.5'/></g><circle cx='7' cy='7' r='1.2' fill='#fff0c4'/>", 14, 14)
write("r-adc", "ADC: tiradores y armas", "Metal de cañón y latón: corchetes de mira en las esquinas, rejilla táctica, trazadoras y fogonazo.", {
    "--bg": "#0b0b0d", "--bg-2": "#101013", "--panel": "#16161a", "--panel-2": "#121215", "--raised": "#1e1e23",
    "--line": "#2a2a30", "--line-2": "#4a3a22", "--text": "#f5f1e8", "--text-2": "#ccc5b6", "--muted": "#918a7c", "--faint": "#5f5a51",
    "--gold": "#e2b24c", "--gold-2": "#fff0c4", "--gold-soft": "rgba(226, 178, 76, 0.14)", "--blue": "#6cc6ff", "--blue-soft": "rgba(108, 198, 255, 0.14)",
    "--good": "#7ddc6f", "--good-soft": "rgba(125, 220, 111, 0.14)", "--bad": "#ff4040", "--bad-soft": "rgba(255, 64, 64, 0.14)",
    "--warn": "#ffb030", "--warn-soft": "rgba(255, 176, 48, 0.14)",
    "--t-display": '"Rajdhani", "Arial Narrow", sans-serif', "--t-display-weight": "700", "--t-display-spacing": "2.5px",
    "--t-radius": "2px", "--t-accent": "#ec343e", "--t-accent-rgb": "236, 52, 62", "--t-accent-2": "#e2b24c",
    "--t-title": "#f5f1e8", "--t-title-grad": "linear-gradient(180deg, #fff6dc, #e2b24c)",
    "--t-title-shadow": "0 0 14px rgba(236, 52, 62, 0.3), 0 2px 0 rgba(0, 0, 0, 0.7)",
    "--t-section": "#e2b24c", "--t-edge": "#5a4524",
    "--t-edge-grad": "linear-gradient(90deg, #2a2a30, #e2b24c 30%, #ec343e 50%, #e2b24c 70%, #2a2a30)",
    "--t-body": f"radial-gradient(1000px 600px at 50% -20%, rgba(236, 52, 62, 0.1), transparent 60%), {hud}, linear-gradient(180deg, #121215, #09090b)",
    "--t-ambient": tracers, "--t-ambient-tile": "500px", "--t-drift-x": "500px", "--t-drift-y": "-250px", "--t-ambient-speed": "14s", "--t-ambient-opacity": "0.55",
    "--t-ambient2": casings, "--t-ambient2-tile": "600px", "--t-drift2-y": "600px", "--t-ambient2-speed": "70s", "--t-ambient2-opacity": "0.5",
    "--t-card-deco": adc_corners + ", repeating-linear-gradient(180deg, rgba(255, 255, 255, 0.018) 0 1px, transparent 1px 3px) padding-box",
    "--t-card-fill": "linear-gradient(180deg, #1b1b20, #111114)",
    "--t-card-frame": "linear-gradient(180deg, #4a4a54, #222227 60%, #3a2c14)",
    "--t-card-shadow": "0 14px 32px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.04)",
    "--t-divider": adc_divider, "--t-divider-w": "224px", "--t-divider-h": "14px",
    "--t-btn-fill": "linear-gradient(180deg, #2a2a31, #18181c)", "--t-btn-frame": "linear-gradient(180deg, #e2b24c, #6a4a14)", "--t-btn-color": "#f5f1e8",
    "--t-btn-hover-fill": "linear-gradient(180deg, #34343c, #1e1e23)", "--t-btn-hover-frame": "linear-gradient(180deg, #fff0c4, #ec343e)",
    "--t-pri-fill": "linear-gradient(180deg, #ff5040, #b3141f)", "--t-pri-frame": "linear-gradient(180deg, #ffd0a0, #ff4a3a 40%, #6a0a10)", "--t-pri-color": "#ffffff",
    "--t-pri-glow": "0 0 16px rgba(236, 52, 62, 0.5), inset 0 1px 0 rgba(255, 210, 170, 0.4)", "--t-pri-text-shadow": "0 1px 2px rgba(60, 0, 0, 0.7)",
    "--t-input-bg": "#0c0c0e", "--t-top-bg": "linear-gradient(180deg, #1b1b20, #0e0e11)", "--t-table-head": "linear-gradient(180deg, #24242a, #16161a)",
    "--t-nav-mark": adc_mark, "--t-brand-clip": "polygon(32% 0, 68% 0, 100% 32%, 100% 100%, 0 100%, 0 32%)",
    "--t-brand-bg": "linear-gradient(180deg, #fff0c4, #e2b24c 45%, #8a6420)", "--t-brand-color": "#2a1c08",
    "--t-bar-a": "linear-gradient(180deg, #ffe7b0, #e2b24c 45%, #8a6420)", "--t-bar-b": "linear-gradient(180deg, #ffb0a8, #ec343e 45%, #7a0a12)",
    "--t-side-a": "#151518", "--t-side-b": "#0c0c0e", "--t-side-pattern": hud,
    "--hx-cream": "255, 240, 196", "--hx-gold": "232, 192, 104", "--hx-gold-2": "236, 52, 62", "--hx-bronze": "106, 74, 20",
    "--hx-deep": "60, 40, 10", "--hx-teal": "236, 52, 62", "--hx-teal-2": "255, 204, 126",
}, """
[data-theme="r-adc"] .kpi-value { font-variant-numeric: tabular-nums; }
""")

# ====================================================================== SUPPORT ENGAGE: cadenas, anclas y farol
rays = ("linear-gradient(100deg, transparent 20%, rgba(44, 222, 152, 0.05) 25%, transparent 32%), "
        "linear-gradient(80deg, transparent 55%, rgba(44, 222, 152, 0.04) 60%, transparent 66%)")
def bubble(rnd, x, y):
    r = rnd.uniform(1.5, 5)
    return f"<circle cx='{x:.0f}' cy='{y:.0f}' r='{r:.1f}' fill='none' stroke='#9fffe0' stroke-opacity='{rnd.uniform(0.15, 0.4):.2f}' stroke-width='0.8'/><circle cx='{x - r * 0.35:.1f}' cy='{y - r * 0.35:.1f}' r='{r * 0.25:.1f}' fill='#ffffff' opacity='0.35'/>"
bubbles = svg(particles(171, 420, 22, bubble), 420, 420)
souls = svg(glow_defs("#2cdc98") + particles(181, 600, 10, glow_dot("#c8ffe8", 1.0, 2.2, 0.3, 0.8)), 600, 600)
link = ("<rect x='1' y='2' width='12' height='6' rx='3' fill='none' stroke='#9aa8b0' stroke-width='1.6'/>"
        "<rect x='11' y='3.6' width='12' height='2.8' rx='1.4' fill='none' stroke='#6d7e84' stroke-width='1.4'/>")
chain = svg(link, 22, 10)
hook = ("<path d='M4 2 V18 Q4 26 12 26 Q18 26 19 20' stroke='#9aa8b0' stroke-width='2.2' fill='none' stroke-linecap='round'/>"
        "<path d='M19 20 L16 18 L21 16Z' fill='#dfe7ea'/><circle cx='4' cy='3' r='2.4' fill='none' stroke='#dfe7ea' stroke-width='1.2'/>")
eng_corners = (f"{chain} top -1px center / 22px 10px repeat-x border-box, "
               + corners(rotations(hook, 28), "28px", "2px"))
eng_divider = svg(
    "<g fill='none' stroke='#9aa8b0' stroke-width='1.6' stroke-linecap='round'><circle cx='8' cy='2.8' r='2'/><path d='M8 4.8 V13 M4 7 H12 M2 10 Q3 14 8 13.5 Q13 14 14 10'/></g>"
    + "".join(f"<rect x='{20 + i * 14}' y='5' width='12' height='5' rx='2.5' fill='none' stroke='{['#9aa8b0', '#6d7e84'][i % 2]}' stroke-width='1.3'/>" for i in range(12))
    + "<circle cx='196' cy='7.5' r='3.5' fill='#2cdc98'/><circle cx='196' cy='7.5' r='6.5' fill='#2cdc98' opacity='0.25'/>",
    206, 15)
eng_mark = svg("<path d='M4 1 V9 Q4 13 8 13 Q11 13 11.5 10' stroke='#2cdc98' stroke-width='1.8' fill='none' stroke-linecap='round'/><path d='M11.5 10 L10 9 L12.6 8Z' fill='#c8ffe8'/>", 14, 14)
write("r-engage", "Support engage: cadenas, anclas y farol", "Rell, Pyke, Nautilus, Thresh: casco de hierro, cadena arriba, garfios en las esquinas, burbujas y almas del farol; rojo de Pyke de acento.", {
    "--bg": "#03090c", "--bg-2": "#050e12", "--panel": "#08161b", "--panel-2": "#071217", "--raised": "#0d2027",
    "--line": "#14303a", "--line-2": "#2b3f46", "--text": "#e6f3f0", "--text-2": "#acc7c2", "--muted": "#72928d", "--faint": "#4a6662",
    "--gold": "#9aa8b0", "--gold-2": "#dfe7ea", "--gold-soft": "rgba(44, 222, 152, 0.12)", "--blue": "#2cdc98", "--blue-soft": "rgba(44, 222, 152, 0.14)",
    "--good": "#2cdc98", "--good-soft": "rgba(44, 222, 152, 0.14)", "--bad": "#e8284a", "--bad-soft": "rgba(232, 40, 74, 0.14)",
    "--warn": "#e8b04a", "--warn-soft": "rgba(232, 176, 74, 0.14)",
    "--t-display": '"Pirata One", Georgia, serif', "--t-display-weight": "400", "--t-display-spacing": "1.5px", "--t-display-case": "none",
    "--t-number-font": "var(--font)", "--t-radius": "3px", "--t-accent": "#2cdc98", "--t-accent-rgb": "44, 222, 152", "--t-accent-2": "#e8284a",
    "--t-title": "#e6f3f0", "--t-title-grad": "linear-gradient(180deg, #ffffff, #9fffe0)",
    "--t-title-shadow": "0 0 16px rgba(44, 222, 152, 0.4), 0 2px 0 rgba(0, 0, 0, 0.7)",
    "--t-section": "#6d7e84", "--t-edge": "#3d4b50",
    "--t-edge-grad": "linear-gradient(90deg, #14303a, #9aa8b0 30%, #2cdc98 50%, #9aa8b0 70%, #14303a)",
    "--t-body": f"{rays}, radial-gradient(900px 600px at 50% -20%, rgba(44, 222, 152, 0.12), transparent 60%), radial-gradient(700px 500px at 100% 100%, rgba(232, 40, 74, 0.07), transparent 60%), linear-gradient(180deg, #06161b, #020608 70%)",
    "--t-ambient": bubbles, "--t-ambient-tile": "420px", "--t-drift-y": "-420px", "--t-ambient-speed": "30s", "--t-ambient-opacity": "0.8",
    "--t-ambient2": souls, "--t-ambient2-tile": "600px", "--t-drift2-x": "-80px", "--t-drift2-y": "-600px", "--t-ambient2-speed": "80s", "--t-ambient2-opacity": "0.6",
    "--t-card-deco": eng_corners + ", radial-gradient(90% 60% at 50% 0%, rgba(44, 222, 152, 0.08), transparent 60%) padding-box",
    "--t-card-fill": "linear-gradient(180deg, rgba(9, 28, 34, 0.95), rgba(3, 11, 14, 0.97))",
    "--t-card-frame": "linear-gradient(180deg, #8fa0a8, #2a3438 50%, #1a2226)",
    "--t-card-shadow": "0 14px 32px rgba(0, 0, 0, 0.6), inset 0 0 0 1px rgba(0, 0, 0, 0.4)",
    "--t-divider": eng_divider, "--t-divider-w": "206px", "--t-divider-h": "15px",
    "--t-btn-fill": "linear-gradient(180deg, #1c2a2f, #0e171a)", "--t-btn-frame": "linear-gradient(180deg, #b6c4ca, #2a3438)", "--t-btn-color": "#e6f3f0",
    "--t-btn-hover-fill": "linear-gradient(180deg, #223338, #10191c)", "--t-btn-hover-frame": "linear-gradient(180deg, #c8ffe8, #2cdc98)",
    "--t-pri-fill": "linear-gradient(180deg, #1f9e70, #0b5a3e)", "--t-pri-frame": "linear-gradient(180deg, #c8ffe8, #2cdc98 40%, #0b5a3e)", "--t-pri-color": "#effff9",
    "--t-pri-glow": "0 0 16px rgba(44, 222, 152, 0.5), inset 0 0 12px rgba(200, 255, 232, 0.2)", "--t-pri-text-shadow": "0 0 8px rgba(160, 255, 220, 0.6)",
    "--t-input-bg": "#030b0e", "--t-top-bg": "linear-gradient(180deg, #0c2027, #040c0f)", "--t-table-head": "linear-gradient(180deg, #10262d, #08161b)",
    "--t-nav-mark": eng_mark, "--t-brand-clip": "circle(50%)",
    "--t-brand-bg": "radial-gradient(circle at 40% 35%, #effff9, #2cdc98 40%, #0b5a3e)", "--t-brand-color": "#02150e",
    "--t-bar-a": "linear-gradient(180deg, #c8ffe8, #2cdc98 45%, #0b5a3e)", "--t-bar-b": "linear-gradient(180deg, #ffb0be, #e8284a 45%, #6a0a1a)",
    "--t-side-a": "#08171c", "--t-side-b": "#030a0c", "--t-side-pattern": "linear-gradient(transparent, transparent)",
    "--hx-cream": "223, 231, 234", "--hx-gold": "152, 168, 178", "--hx-gold-2": "44, 222, 152", "--hx-bronze": "60, 74, 80",
    "--hx-deep": "26, 34, 38", "--hx-teal": "44, 222, 152", "--hx-teal-2": "172, 255, 212",
})

# ====================================================================== SUPPORT GUARDIÁN: escudos, gemas y hielo
aurora = ("radial-gradient(60% 40% at 20% 0%, rgba(122, 255, 200, 0.1), transparent 70%), "
          "radial-gradient(50% 35% at 70% 5%, rgba(255, 112, 216, 0.09), transparent 70%), "
          "radial-gradient(70% 45% at 50% 0%, rgba(122, 202, 255, 0.12), transparent 70%)")
flake = svg("<g stroke='#cfe8ff' stroke-opacity='0.06' stroke-width='1' fill='none'><path d='M20 8 V32 M9.6 14 L30.4 26 M9.6 26 L30.4 14'/>"
            "<path d='M20 8 L17 11 M20 8 L23 11 M20 32 L17 29 M20 32 L23 29'/></g>", 64, 64)
snow = svg(glow_defs("#ffffff") + particles(191, 420, 30, glow_dot("#ffffff", 0.6, 1.6, 0.4, 0.95)), 420, 420)
gems = svg("".join(star4(random.Random(201 + i).uniform(10, 590), random.Random(211 + i).uniform(10, 590), random.Random(221 + i).uniform(3, 6), ["#ff70d8", "#7ac8ff", "#ffffff"][i % 3], 0.7) for i in range(12)), 600, 600)
shard = ("<defs><linearGradient id='i' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#ffffff'/><stop offset='1' stop-color='#7ac8ff' stop-opacity='0.5'/></linearGradient></defs>"
         "<path d='M1 1 L14 4 L6 7Z' fill='url(#i)'/><path d='M1 1 L4 14 L7 6Z' fill='url(#i)'/><path d='M1 1 L18 11 L11 18Z' fill='url(#i)' opacity='0.8'/>"
         "<path d='M10 10 L13.5 13.5' stroke='#ff70d8' stroke-width='1.6'/>")
gd_corners = corners(rotations(shard, 24), "24px", "-1px")
gd_divider = svg(
    "<defs><linearGradient id='s' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#ffffff'/><stop offset='1' stop-color='#6f93b4'/></linearGradient>"
    "<linearGradient id='l' x2='1'><stop offset='0' stop-color='#7ac8ff'/><stop offset='1' stop-color='#7ac8ff' stop-opacity='0'/></linearGradient></defs>"
    "<path d='M2 1 H18 V8 Q18 13 10 16 Q2 13 2 8Z' fill='url(#s)' stroke='#2e4a6e' stroke-width='0.8'/>"
    "<path d='M10 4 L13.5 8 L10 12.5 L6.5 8Z' fill='#ff70d8'/><path d='M10 4 L13.5 8 L10 8Z' fill='#ffd0f2'/>"
    "<rect x='24' y='7.3' width='190' height='1.5' fill='url(#l)'/><rect x='24' y='10.5' width='110' height='0.8' fill='#ff70d8' opacity='0.5'/>",
    216, 17)
gd_mark = svg("<path d='M2 1 H12 V6 Q12 10 7 13 Q2 10 2 6Z' fill='#bfe3ff' stroke='#ffffff' stroke-width='0.6'/><path d='M7 3.5 L9.2 6.3 L7 9 L4.8 6.3Z' fill='#ff70d8'/>", 14, 14)
write("r-guardian", "Support guardián: escudos, gemas y hielo", "Taric y Braum: plata de hielo, aurora, esquirlas de hielo con gema, nieve que cae y destellos de gema.", {
    "--bg": "#050b14", "--bg-2": "#08101c", "--panel": "#0c1828", "--panel-2": "#0a1422", "--raised": "#12223a",
    "--line": "#1a2e48", "--line-2": "#2e4a6e", "--text": "#f0f8ff", "--text-2": "#c0d6ec", "--muted": "#839dba", "--faint": "#56708e",
    "--gold": "#a9cbe8", "--gold-2": "#ffffff", "--gold-soft": "rgba(122, 202, 255, 0.14)", "--blue": "#7ac8ff", "--blue-soft": "rgba(122, 202, 255, 0.14)",
    "--good": "#7af0c8", "--good-soft": "rgba(122, 240, 200, 0.14)", "--bad": "#ff6a8a", "--bad-soft": "rgba(255, 106, 138, 0.14)",
    "--warn": "#ffc86a", "--warn-soft": "rgba(255, 200, 106, 0.14)",
    "--t-display": '"Marcellus SC", Georgia, serif', "--t-display-weight": "400", "--t-display-spacing": "2px", "--t-display-case": "none",
    "--t-radius": "6px", "--t-card-border-w": "2px", "--t-accent": "#7ac8ff", "--t-accent-rgb": "122, 202, 255", "--t-accent-2": "#ff70d8",
    "--t-title": "#f0f8ff", "--t-title-grad": "linear-gradient(180deg, #ffffff, #9fd6ff)",
    "--t-title-shadow": "0 0 14px rgba(122, 202, 255, 0.5), 0 0 2px rgba(255, 255, 255, 0.8)",
    "--t-section": "#8fb4d4", "--t-edge": "#4a6e94",
    "--t-edge-grad": "linear-gradient(90deg, #1a2e48, #e8f6ff 30%, #ff70d8 50%, #e8f6ff 70%, #1a2e48)",
    "--t-body": f"{aurora}, {flake}, linear-gradient(180deg, #0a1628, #04080f)",
    "--t-ambient": snow, "--t-ambient-tile": "420px", "--t-drift-x": "60px", "--t-drift-y": "420px", "--t-ambient-speed": "40s", "--t-ambient-opacity": "0.8",
    "--t-ambient2": gems, "--t-ambient2-tile": "600px", "--t-drift2-x": "0px", "--t-drift2-y": "600px", "--t-ambient2-speed": "110s", "--t-ambient2-opacity": "0.5",
    "--t-card-deco": gd_corners + ", radial-gradient(100% 60% at 50% 0%, rgba(122, 202, 255, 0.09), transparent 60%) padding-box",
    "--t-card-fill": "linear-gradient(180deg, rgba(16, 32, 54, 0.95), rgba(6, 12, 24, 0.97))",
    "--t-card-frame": "linear-gradient(180deg, #f2fbff, #6f93b4 35%, #2e4a6e 70%, #a6c8e6)",
    "--t-card-shadow": "0 14px 32px rgba(0, 0, 0, 0.55), inset 0 0 18px rgba(122, 202, 255, 0.08)",
    "--t-divider": gd_divider, "--t-divider-w": "216px", "--t-divider-h": "17px",
    "--t-btn-fill": "linear-gradient(180deg, rgba(40, 70, 110, 0.92), rgba(18, 34, 58, 0.96))", "--t-btn-frame": "linear-gradient(180deg, #f2fbff, #4a6e94)", "--t-btn-color": "#f0f8ff",
    "--t-btn-hover-fill": "linear-gradient(180deg, rgba(50, 86, 132, 0.95), rgba(22, 42, 70, 0.98))", "--t-btn-hover-frame": "linear-gradient(180deg, #ffffff, #ff70d8)",
    "--t-pri-fill": "linear-gradient(180deg, #8fd2ff, #2b6cb0)", "--t-pri-frame": "linear-gradient(180deg, #ffffff, #7ac8ff 40%, #1d4a80)", "--t-pri-color": "#ffffff",
    "--t-pri-glow": "0 0 16px rgba(122, 202, 255, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.5)", "--t-pri-text-shadow": "0 1px 2px rgba(10, 40, 80, 0.7)",
    "--t-input-bg": "#060d18", "--t-top-bg": "linear-gradient(180deg, #102038, #060c16)", "--t-table-head": "linear-gradient(180deg, #16294a, #0c1828)",
    "--t-nav-mark": gd_mark, "--t-brand-clip": "polygon(0 0, 100% 0, 100% 60%, 50% 100%, 0 60%)",
    "--t-brand-bg": "linear-gradient(160deg, #ffffff, #9fd6ff 45%, #ff70d8)", "--t-brand-color": "#0c1828",
    "--t-bar-a": "linear-gradient(180deg, #e8f6ff, #7ac8ff 45%, #2b6cb0)", "--t-bar-b": "linear-gradient(180deg, #ffe0f6, #ff70d8 45%, #8a2a7a)",
    "--t-side-a": "#0c1a2c", "--t-side-b": "#060c16", "--t-side-pattern": flake,
    "--hx-cream": "240, 250, 255", "--hx-gold": "204, 226, 246", "--hx-gold-2": "122, 202, 255", "--hx-bronze": "60, 88, 116",
    "--hx-deep": "24, 38, 56", "--hx-teal": "122, 202, 255", "--hx-teal-2": "232, 248, 255",
})

# ====================================================================== SUPPORT PEEL: escudos y curas
def bokeh(rnd, x, y):
    c = ["#70ffc0", "#ffa8e6", "#b8a0ff", "#ffe6a8"][int(rnd.uniform(0, 4))]
    return f"<circle cx='{x:.0f}' cy='{y:.0f}' r='{rnd.uniform(14, 40):.0f}' fill='{c}' opacity='{rnd.uniform(0.04, 0.09):.2f}'/>"
bokeh_bg = svg(particles(231, 700, 14, bokeh), 700, 700)
sparkles = svg(glow_defs("#ffffff") + "".join(
    star4(random.Random(241 + i).uniform(10, 410), random.Random(251 + i).uniform(10, 410), random.Random(261 + i).uniform(2.5, 5.5), ["#70ffc0", "#ffa8e6", "#ffe6a8", "#ffffff"][i % 4], 0.85)
    for i in range(16)) + particles(271, 420, 12, glow_dot("#ffffff", 0.5, 1.1, 0.4, 0.9)), 420, 420)
hearts = svg("".join(
    f"<path d='M0 3 C0 -1 5 -1 5 3 C5 -1 10 -1 10 3 C10 7 5 9 5 11 C5 9 0 7 0 3Z' transform='translate({random.Random(281 + i).uniform(0, 580):.0f} {random.Random(291 + i).uniform(0, 580):.0f}) scale({random.Random(301 + i).uniform(0.6, 1.1):.2f})' fill='{['#ffa8e6', '#70ffc0'][i % 2]}' opacity='0.45'/>"
    for i in range(8)), 600, 600)
spark_corner = (star4(7, 7, 6.5, "#ffffff") + star4(16, 4, 3, "#70ffc0", 0.9) + star4(4, 16, 3, "#ffa8e6", 0.9))
pl_corners = corners(rotations(spark_corner, 22), "22px", "2px")
pl_divider = svg(
    "<defs><linearGradient id='l' x2='1'><stop offset='0' stop-color='#70ffc0'/><stop offset='0.5' stop-color='#ffa8e6'/><stop offset='1' stop-color='#ffa8e6' stop-opacity='0'/></linearGradient></defs>"
    "<circle cx='8' cy='8' r='7' fill='#70ffc0' opacity='0.25'/><path d='M6 2.5 H10 V6 H13.5 V10 H10 V13.5 H6 V10 H2.5 V6 H6Z' fill='#e6fff4'/>"
    + star4(24, 5, 3.5, "#ffa8e6") + "<rect x='30' y='7.3' width='180' height='1.6' rx='0.8' fill='url(#l)'/>",
    212, 16)
pl_mark = svg("<path d='M5 1.5 H9 V5 H12.5 V9 H9 V12.5 H5 V9 H1.5 V5 H5Z' fill='#70ffc0' stroke='#e6fff4' stroke-width='0.6'/>", 14, 14)
write("r-peel", "Support peel: escudos y curas", "Janna, Lulu, Soraka, Sona: nácar y luz, menta de curación y rosa, destellos en las esquinas, burbujas de escudo y corazones.", {
    "--bg": "#0b0a1a", "--bg-2": "#100e24", "--panel": "#161333", "--panel-2": "#12102b", "--raised": "#1f1b46",
    "--line": "#2a2556", "--line-2": "#4a3f80", "--text": "#fdfaff", "--text-2": "#dbd1f2", "--muted": "#a196c8", "--faint": "#6c6396",
    "--gold": "#ffe6a8", "--gold-2": "#fffaf0", "--gold-soft": "rgba(112, 255, 192, 0.13)", "--blue": "#70ffc0", "--blue-soft": "rgba(112, 255, 192, 0.14)",
    "--good": "#70ffc0", "--good-soft": "rgba(112, 255, 192, 0.14)", "--bad": "#ff7aa8", "--bad-soft": "rgba(255, 122, 168, 0.14)",
    "--warn": "#ffd27a", "--warn-soft": "rgba(255, 210, 122, 0.14)",
    "--t-display": '"Comfortaa", "Nunito", sans-serif', "--t-display-weight": "700", "--t-display-spacing": "0.6px", "--t-display-case": "none",
    "--t-radius": "14px", "--t-btn-radius": "999px", "--t-pill-radius": "999px", "--t-nav-radius": "12px", "--t-img-radius": "50%",
    "--t-accent": "#70ffc0", "--t-accent-rgb": "112, 255, 192", "--t-accent-2": "#ffa8e6",
    "--t-title": "#fdfaff", "--t-title-grad": "linear-gradient(90deg, #b8fff0, #ffd6f2)",
    "--t-title-shadow": "0 0 16px rgba(255, 168, 230, 0.45), 0 0 4px rgba(112, 255, 192, 0.4)",
    "--t-section": "#b8a0ff", "--t-edge": "#5a4f96",
    "--t-edge-grad": "linear-gradient(90deg, #2a2556, #70ffc0 30%, #fffaf0 50%, #ffa8e6 70%, #2a2556)",
    "--t-body": f"radial-gradient(900px 600px at 10% 0%, rgba(112, 255, 192, 0.12), transparent 60%), radial-gradient(900px 600px at 100% 100%, rgba(255, 168, 230, 0.14), transparent 60%), {bokeh_bg}, linear-gradient(180deg, #15123a, #0a0919)",
    "--t-ambient": sparkles, "--t-ambient-tile": "420px", "--t-drift-x": "40px", "--t-drift-y": "-420px", "--t-ambient-speed": "45s", "--t-ambient-opacity": "0.85",
    "--t-ambient2": hearts, "--t-ambient2-tile": "600px", "--t-drift2-x": "-60px", "--t-drift2-y": "-600px", "--t-ambient2-speed": "95s", "--t-ambient2-opacity": "0.45",
    "--t-card-deco": pl_corners + ", radial-gradient(100% 60% at 50% 0%, rgba(255, 168, 230, 0.09), transparent 60%) padding-box",
    "--t-card-fill": "linear-gradient(180deg, rgba(30, 26, 66, 0.94), rgba(14, 12, 34, 0.96))",
    "--t-card-frame": "linear-gradient(135deg, #fffaf0, #c8b6e8 35%, #70ffc0 65%, #ffa8e6)",
    "--t-card-shadow": "0 14px 34px rgba(0, 0, 0, 0.5), inset 0 0 26px rgba(112, 255, 192, 0.07), 0 0 24px rgba(255, 168, 230, 0.06)",
    "--t-divider": pl_divider, "--t-divider-w": "212px", "--t-divider-h": "16px",
    "--t-btn-fill": "linear-gradient(180deg, #2a2558, #1a1740)", "--t-btn-frame": "linear-gradient(135deg, #fffaf0, #a894cc)", "--t-btn-color": "#fdfaff",
    "--t-btn-hover-fill": "linear-gradient(180deg, #332d6a, #1f1b4c)", "--t-btn-hover-frame": "linear-gradient(135deg, #70ffc0, #ffa8e6)",
    "--t-pri-fill": "linear-gradient(135deg, #70ffc0, #5ad0ff 50%, #ffa8e6)", "--t-pri-frame": "linear-gradient(135deg, #ffffff, #ffffff)", "--t-pri-color": "#1a1238",
    "--t-pri-glow": "0 0 18px rgba(112, 255, 192, 0.45), 0 0 10px rgba(255, 168, 230, 0.3)", "--t-pri-text-shadow": "0 1px 0 rgba(255, 255, 255, 0.5)",
    "--t-pri-hover-color": "#0d0820",
    "--t-input-bg": "#0d0b22", "--t-top-bg": "linear-gradient(180deg, #1c1846, #0d0b22)", "--t-table-head": "linear-gradient(180deg, #221d52, #161333)",
    "--t-nav-mark": pl_mark, "--t-brand-radius": "50%",
    "--t-brand-bg": "radial-gradient(circle at 35% 30%, #ffffff, #70ffc0 45%, #ffa8e6)", "--t-brand-color": "#1a1238",
    "--t-bar-a": "linear-gradient(180deg, #d8fff0, #70ffc0 45%, #2aa878)", "--t-bar-b": "linear-gradient(180deg, #ffe0f6, #ffa8e6 45%, #b05aa0)",
    "--t-side-a": "#16133a", "--t-side-b": "#0b0a1a", "--t-side-pattern": "linear-gradient(transparent, transparent)",
    "--hx-cream": "255, 250, 240", "--hx-gold": "255, 236, 182", "--hx-gold-2": "112, 255, 192", "--hx-bronze": "120, 104, 160",
    "--hx-deep": "50, 40, 80", "--hx-teal": "112, 255, 192", "--hx-teal-2": "255, 192, 236",
})
