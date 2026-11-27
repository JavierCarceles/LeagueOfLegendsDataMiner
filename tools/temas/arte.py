"""Arte de los temas por línea: cenefa bajo la barra de arriba, emblema de las secciones, blasón del menú, bordes de
tarjeta, raíl de la barra de scroll y escenas animadas. Cada pieza es un SVG en data URI (o CSS). Lo usa generar.py;
``preview_html()`` saca una hoja con todas las piezas para verlas de un vistazo."""
import math
import random
from urllib.parse import quote


def raw(body: str, w: float, h: float) -> str:
    return f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}'>{body}</svg>"


def uri(body: str, w: float, h: float) -> str:
    s = " ".join(raw(body, w, h).split())
    return 'url("data:image/svg+xml,' + quote(s, safe=" '=:/;,.()-_!*~") + '")'


def f(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def lg(id_, stops, x2=0, y2=1, x1=0, y1=0):
    st = "".join(f"<stop offset='{o}' stop-color='{c}'" + (f" stop-opacity='{a}'" if a is not None else "") + "/>" for o, c, a in stops)
    return f"<linearGradient id='{id_}' x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}'>{st}</linearGradient>"


def rg(id_, stops, cx=0.5, cy=0.5, r=0.5):
    st = "".join(f"<stop offset='{o}' stop-color='{c}'" + (f" stop-opacity='{a}'" if a is not None else "") + "/>" for o, c, a in stops)
    return f"<radialGradient id='{id_}' cx='{cx}' cy='{cy}' r='{r}'>{st}</radialGradient>"


def S(*stops):
    """Paradas: (offset, color) o (offset, color, opacidad)."""
    return [(s[0], s[1], s[2] if len(s) > 2 else None) for s in stops]


def star4(cx, cy, r, fill, op=1.0, k=0.22):
    q = r * k
    return (f"<path d='M{f(cx)} {f(cy - r)} L{f(cx + q)} {f(cy - q)} L{f(cx + r)} {f(cy)} L{f(cx + q)} {f(cy + q)} L{f(cx)} {f(cy + r)} "
            f"L{f(cx - q)} {f(cy + q)} L{f(cx - r)} {f(cy)} L{f(cx - q)} {f(cy - q)}Z' fill='{fill}' opacity='{op}'/>")


def leaf_path(x, y, ang, length, width):
    """Hoja nacida en x,y que apunta a ``ang`` (grados, 0 = arriba)."""
    a = math.radians(ang)
    ux, uy = math.sin(a), -math.cos(a)  # hacia la punta
    px, py = -uy, ux  # de lado
    tx, ty = x + ux * length, y + uy * length
    c1 = (x + ux * length * 0.45 + px * width, y + uy * length * 0.45 + py * width)
    c2 = (x + ux * length * 0.45 - px * width, y + uy * length * 0.45 - py * width)
    return f"M{f(x)} {f(y)} Q{f(c1[0])} {f(c1[1])} {f(tx)} {f(ty)} Q{f(c2[0])} {f(c2[1])} {f(x)} {f(y)}Z"


def bez(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def gear(cx, cy, r_out, r_in, teeth):
    pts = []
    for i in range(teeth * 4):
        a = i * math.pi * 2 / (teeth * 4)
        r = r_out if i % 4 in (1, 2) else r_in
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    return "M" + " L".join(f"{f(x)} {f(y)}" for x, y in pts) + "Z"


def poly(points):
    return "M" + " L".join(f"{f(x)} {f(y)}" for x, y in points) + "Z"


ART = {}  # tema → variables
ART_CSS = {}  # tema → CSS extra
PREVIEW = {}  # tema → [(nombre, svg crudo)]


def put(theme, name, body, w, h, opacity=None):
    PREVIEW.setdefault(theme, []).append((name, raw(body, w, h)))
    return uri(body if opacity is None else f"<g opacity='{opacity}'>{body}</g>", w, h)


# ============================================================================================ TOP: forja y tanques
STEEL = S((0, "#f4f6f8"), (0.45, "#8a949e"), (1, "#2f353c"))
RIVET = rg("r", S((0, "#ffffff"), (0.5, "#8a949e"), (1, "#22272c")), 0.35, 0.35, 0.6)
MOLTEN = rg("m", S((0, "#ffd27a", 0.95), (0.35, "#ff7a1e", 0.6), (1, "#ff4a10", 0)))

def _top():
    t = "r-top"
    # Cenefa: banda de acero con remaches y dientes de oruga de tanque, con gotas de lava bajo algunos.
    body = "<defs>" + lg("p", S((0, "#7b848d"), (0.4, "#2f353c"), (1, "#15181b"))) + lg("t", S((0, "#454d56"), (1, "#0f1214"))) + RIVET + MOLTEN + "</defs>"
    body += "<rect width='120' height='8' fill='url(#p)'/><rect y='0.4' width='120' height='0.8' fill='#dfe4e9' opacity='0.55'/><rect y='8' width='120' height='1' fill='#000' opacity='0.65'/>"
    for i in range(4):
        x = i * 30 + 6
        body += f"<path d='M{x} 8.6 H{x + 18} L{x + 15.5} 16 Q{x + 9} 17.6 {x + 2.5} 16 Z' fill='url(#t)' stroke='#000' stroke-opacity='0.6' stroke-width='0.6'/>"
        body += f"<path d='M{x + 1.2} 9.4 H{x + 16.8}' stroke='#aab4be' stroke-opacity='0.55' stroke-width='0.7'/>"
        body += f"<circle cx='{x + 9}' cy='12.2' r='1.7' fill='url(#r)'/>"
        if i % 2 == 0:
            body += f"<ellipse cx='{x + 9}' cy='19.5' rx='4.5' ry='3.5' fill='url(#m)'/><path d='M{x + 9} 16.4 Q{x + 10.4} 18.6 {x + 9} 19.6 Q{x + 7.6} 18.6 {x + 9} 16.4Z' fill='#ffd27a'/>"
    for x in (0, 30, 60, 90, 120):
        body += f"<circle cx='{x}' cy='4' r='2.1' fill='url(#r)'/>"
    valance = put(t, "cenefa", body, 120, 22)
    # Emblema: escudo de acero con banda de forja y martillos cruzados.
    e = "<defs>" + lg("s", STEEL) + lg("o", S((0, "#ffe2c4"), (0.45, "#ff8a2a"), (1, "#a8340a"))) + lg("w", S((0, "#8a5a2e"), (1, "#3a2410")), 1, 0) + RIVET + MOLTEN + "</defs>"
    for ang in (-42, 42):
        e += (f"<g transform='rotate({ang} 24 24)'><rect x='22.6' y='6' width='2.8' height='38' rx='1' fill='url(#w)' stroke='#120a04' stroke-width='0.6'/>"
              f"<rect x='14.5' y='3' width='19' height='9' rx='1.6' fill='url(#s)' stroke='#111' stroke-width='0.8'/><rect x='15.5' y='4' width='17' height='1.4' fill='#fff' opacity='0.6'/></g>")
    e += "<circle cx='24' cy='27' r='16' fill='url(#m)' opacity='0.55'/>"
    e += "<path d='M12 14 H36 V27 Q36 38 24 44 Q12 38 12 27 Z' fill='url(#s)' stroke='#15181b' stroke-width='1.2'/>"
    e += "<path d='M15 17 H33 V27 Q33 36 24 40.5 Q15 36 15 27 Z' fill='#1b1f23'/>"
    e += "<path d='M22.4 17.5 H25.6 V40 H22.4Z M15.5 22.4 H32.5 V25.6 H15.5Z' fill='url(#o)'/><path d='M24 18 V39 M16 24 H32' stroke='#fff1d8' stroke-width='0.6' opacity='0.7'/>"
    e += "".join(f"<circle cx='{x}' cy='{y}' r='1.3' fill='url(#r)'/>" for x, y in ((16.8, 18.6), (31.2, 18.6), (17.5, 33), (30.5, 33)))
    emblem = put(t, "emblema", e, 48, 48)
    # Blasón del menú: yunque con martillo, engranaje alrededor y chispas.
    c = "<defs>" + lg("s", STEEL) + lg("o", S((0, "#ffd2a8"), (1, "#ff6a14"))) + MOLTEN + "</defs>"
    c += f"<path d='{gear(110, 110, 100, 90, 18)}' fill='none' stroke='#8a949e' stroke-width='3'/>"
    c += "<circle cx='110' cy='110' r='80' fill='none' stroke='#5d666f' stroke-width='1.5'/><circle cx='110' cy='110' r='74' fill='none' stroke='#5d666f' stroke-width='0.8' stroke-dasharray='3 6'/>"
    c += "<ellipse cx='110' cy='150' rx='70' ry='26' fill='url(#m)' opacity='0.7'/>"
    c += "<path d='M24 108 Q46 102 60 102 H184 V118 H160 Q148 120 148 132 V144 Q148 150 156 153 L168 159 V168 H52 V159 L64 153 Q72 150 72 144 V132 Q72 120 60 118 H58 Q38 118 24 108 Z' fill='url(#s)' stroke='#14171a' stroke-width='2'/>"
    c += "<path d='M30 107 Q48 104 60 104 H182' stroke='#ffffff' stroke-opacity='0.55' stroke-width='1.6' fill='none'/>"
    c += "<g transform='rotate(-32 120 70)'><rect x='116' y='36' width='8' height='70' rx='3' fill='#6a4020' stroke='#1a0e04' stroke-width='1.5'/><rect x='92' y='26' width='56' height='22' rx='3' fill='url(#s)' stroke='#14171a' stroke-width='2'/></g>"
    rnd = random.Random(5)
    for _ in range(10):
        x, y = rnd.uniform(70, 160), rnd.uniform(70, 100)
        c += star4(x, y, rnd.uniform(2.5, 6), rnd.choice(["#ffd27a", "#ff8a2a", "#fff1d8"]), 0.95)
    crest = put(t, "blasón", c, 220, 220, opacity=0.2)
    # Raíl de la barra: viga de acero con tornillos y una veta de lava.
    rl = "<defs>" + lg("g", S((0, "#3a424b"), (0.25, "#14171a"), (0.75, "#14171a"), (1, "#3a424b")), 1, 0) + RIVET + "</defs>"
    rl += "<rect width='10' height='28' fill='url(#g)'/><rect x='4.4' width='1.2' height='28' fill='#ff7a1e' opacity='0.55'/>"
    rl += "<rect x='0.6' width='0.8' height='28' fill='#c3ccd5' opacity='0.4'/><rect x='8.6' width='0.8' height='28' fill='#c3ccd5' opacity='0.25'/>"
    rl += "<circle cx='5' cy='6' r='1.8' fill='url(#r)'/><rect x='1' y='19' width='8' height='1.2' fill='#000' opacity='0.6'/><rect x='1' y='20.2' width='8' height='0.6' fill='#9aa4ae' opacity='0.4'/>"
    rail = put(t, "raíl", rl, 10, 28)
    # Borde de tarjeta: costura de chapa con remaches arriba.
    sb = "<defs>" + RIVET + "</defs><rect width='56' height='0.8' fill='#dfe4e9' opacity='0.3'/><rect y='5.4' width='56' height='0.8' fill='#000' opacity='0.45'/><circle cx='28' cy='3' r='1.4' fill='url(#r)'/>"
    seam = put(t, "costura", sb, 56, 7)
    gear_big = uri(f"<path d='{gear(260, 260, 250, 222, 24)}' fill='none' stroke='#9aa4ae' stroke-width='5'/><circle cx='260' cy='260' r='190' fill='none' stroke='#9aa4ae' stroke-width='3'/>"
                   + "".join(f"<rect x='254' y='80' width='12' height='120' rx='3' fill='#9aa4ae' transform='rotate({a} 260 260)'/>" for a in range(0, 360, 60))
                   + "<circle cx='260' cy='260' r='46' fill='none' stroke='#9aa4ae' stroke-width='10'/>", 520, 520)
    gear_small = uri(f"<path d='{gear(130, 130, 124, 106, 14)}' fill='none' stroke='#ff8a2a' stroke-width='4'/><circle cx='130' cy='130' r='30' fill='none' stroke='#ff8a2a' stroke-width='8'/>", 260, 260)
    ART[t] = {
        "--t-valance": valance, "--t-valance-w": "120px", "--t-valance-h": "22px",
        "--t-emblem": emblem, "--t-emblem-faint": uri(f"<g opacity='0.2'>{e}</g>", 48, 48), "--t-crest": crest, "--t-seam": seam,
        "--hx-rail": f"{rail} center top / 10px 28px repeat-y", "--hx-rail-w": "10px", "--hx-rail-radius": "1px",
        "--hx-rune-clip": "polygon(30% 0, 70% 0, 100% 30%, 100% 70%, 70% 100%, 30% 100%, 0 70%, 0 30%)",
        "--hx-cap-rot": "0deg", "--hx-cap-clip": "polygon(30% 0, 70% 0, 100% 30%, 100% 70%, 70% 100%, 30% 100%, 0 70%, 0 30%)",
        "--t-splash-opacity": "0.42",
    }
    ART_CSS[t] = f"""
[data-theme="r-top"] .t-scene.a {{ inset: auto 0 0 var(--sidebar); height: 46vh; background: radial-gradient(60% 70% at 50% 110%, rgba(255, 106, 20, 0.3), transparent 70%); animation: t-breathe 4.5s ease-in-out infinite alternate; }}
[data-theme="r-top"] .t-scene.b {{ width: 520px; height: 520px; right: -170px; bottom: -190px; background: {gear_big} center / contain no-repeat, {gear_small} 4% 6% / 190px no-repeat; opacity: 0.08; animation: t-spin 150s linear infinite; }}
[data-theme="r-top"] .card {{ --t-card-edge: var(--t-seam) left top 2px / 56px 7px repeat-x padding-box, var(--t-seam) left bottom 1px / 56px 7px repeat-x padding-box; }}
"""

# ============================================================================================ JUNGLA: maleza y objetivos
def _jungle():
    t = "r-jungle"
    greens = ["#4fbf5c", "#6fdc7a", "#9be86f", "#3f9a46"]
    body = "<defs>" + lg("l", S((0, "#1f6a2a"), (1, "#9be86f"))) + rg("f", S((0, "#f4ffb0", 1), (0.3, "#d8ff6a", 0.7), (1, "#d8ff6a", 0))) + "</defs>"
    moss = "M0 0 H300 V4"
    for i in range(20, 0, -1):
        x = i * 15
        moss += f" Q{x - 7.5} {7 if i % 2 else 5.5} {x - 15} {4 if i % 3 else 5}"
    body += f"<path d='{moss} V0Z' fill='#2c5a24'/><path d='M0 1 H300' stroke='#7ac85a' stroke-opacity='0.5' stroke-width='1'/>"
    for x in range(6, 300, 13):
        h2 = 5 + (x * 7) % 6
        body += f"<path d='M{x - 3} 4 Q{x} {h2 + 3} {x + 3} 4Z' fill='#356b2a' opacity='0.9'/>"
    vines = [(14, 22, 5), (44, 34, -6), (78, 15, 4), (108, 40, 7), (142, 26, -5), (176, 36, 6), (206, 18, -4), (238, 31, 5), (270, 23, -6), (290, 12, 3)]
    rnd = random.Random(7)
    for k, (x, ln, sw) in enumerate(vines):
        p0, p1, p2, p3 = (x, 3), (x + sw, ln * 0.35), (x - sw, ln * 0.7), (x + sw * 0.4, ln)
        body += f"<path d='M{p0[0]} {p0[1]} C{f(p1[0])} {f(p1[1])} {f(p2[0])} {f(p2[1])} {f(p3[0])} {f(p3[1])}' stroke='#3c6e2c' stroke-width='1.7' fill='none' stroke-linecap='round'/>"
        for j, tt in enumerate((0.18, 0.36, 0.54, 0.72, 0.9)):
            px, py = bez(p0, p1, p2, p3, tt)
            if py > ln - 2:
                continue
            ang = (65 if j % 2 else -65) + rnd.uniform(-15, 15) + 180
            body += f"<path d='{leaf_path(px, py, ang, rnd.uniform(6, 9.5), 3.2)}' fill='{greens[(k + j) % 4]}'/>"
        if k % 2 == 0:
            body += f"<circle cx='{f(p3[0])}' cy='{f(p3[1] + 1.5)}' r='5' fill='url(#f)'/><circle cx='{f(p3[0])}' cy='{f(p3[1] + 1.5)}' r='1.4' fill='#fff8c8'/>"
        else:
            body += f"<circle cx='{f(p3[0])}' cy='{f(p3[1] + 1)}' r='1.8' fill='#b07aff'/>"
    valance = put(t, "cenefa", body, 300, 42)
    # Emblema: el ojo de Barón en una corona de hojas.
    e = "<defs>" + lg("l", S((0, "#2e7d32"), (1, "#b6f08a"))) + rg("p", S((0, "#f0d6ff"), (0.45, "#a46bff"), (1, "#3a0a6a"))) + rg("g", S((0, "#a46bff", 0.6), (1, "#a46bff", 0))) + "</defs>"
    e += "<circle cx='24' cy='24' r='22' fill='url(#g)'/><circle cx='24' cy='24' r='15.5' fill='#0c1a11' stroke='#c9a45c' stroke-width='1.4'/>"
    for k in range(7):
        a = 200 + k * 20
        x, y = 24 + math.cos(math.radians(a)) * 17, 24 + math.sin(math.radians(a)) * 17
        e += f"<path d='{leaf_path(x, y, a - 90 + 60, 7.5, 2.8)}' fill='url(#l)'/>"
        a2 = 340 - k * 20
        x, y = 24 + math.cos(math.radians(a2)) * 17, 24 + math.sin(math.radians(a2)) * 17
        e += f"<path d='{leaf_path(x, y, a2 + 90 - 60, 7.5, 2.8)}' fill='url(#l)'/>"
    e += "<path d='M12 24 Q24 13 36 24 Q24 35 12 24Z' fill='url(#p)' stroke='#1a0630' stroke-width='0.8'/>"
    e += "<ellipse cx='24' cy='24' rx='1.8' ry='6.5' fill='#12001f'/><circle cx='21.5' cy='21.5' r='1.2' fill='#fff' opacity='0.8'/>"
    emblem = put(t, "emblema", e, 48, 48)
    # Blasón: corona de hojas, zarpazos y el ojo de Barón.
    c = "<defs>" + lg("l", S((0, "#2e7d32"), (1, "#b6f08a"))) + rg("p", S((0, "#f0d6ff"), (0.45, "#a46bff"), (1, "#3a0a6a"))) + rg("g", S((0, "#a46bff", 0.5), (1, "#a46bff", 0))) + "</defs>"
    c += "<circle cx='110' cy='110' r='100' fill='url(#g)'/>"
    for k in range(22):
        a = k * 360 / 22
        x, y = 110 + math.cos(math.radians(a)) * 82, 110 + math.sin(math.radians(a)) * 82
        c += f"<path d='{leaf_path(x, y, a + 90 + 35, 26, 9)}' fill='url(#l)' opacity='0.9'/>"
    for dx in (-22, 0, 22):
        c += f"<path d='M{70 + dx} 60 Q{100 + dx} 110 {140 + dx} 162' stroke='#c9a45c' stroke-width='7' stroke-linecap='round' fill='none' opacity='0.55'/>"
    c += "<path d='M58 110 Q110 64 162 110 Q110 156 58 110Z' fill='url(#p)' stroke='#1a0630' stroke-width='3'/>"
    c += "<ellipse cx='110' cy='110' rx='8' ry='30' fill='#12001f'/><circle cx='98' cy='98' r='5' fill='#fff' opacity='0.7'/>"
    crest = put(t, "blasón", c, 220, 220, opacity=0.2)
    rl = "<defs>" + lg("g", S((0, "#2a1a0c"), (0.5, "#140d06"), (1, "#2a1a0c")), 1, 0) + "</defs><rect width='10' height='36' fill='url(#g)'/>"
    rl += "<path d='M5 0 Q8 9 5 18 Q2 27 5 36' stroke='#4f9a3a' stroke-width='1.6' fill='none'/>"
    rl += f"<path d='{leaf_path(6.4, 6, 50, 5, 2)}' fill='#6fdc7a'/><path d='{leaf_path(3.6, 24, -50, 5, 2)}' fill='#4fbf5c'/>"
    rail = put(t, "raíl", rl, 10, 36)
    vb = "<path d='M0 3 Q17.5 0.5 35 3 T70 3 T105 3 T140 3' stroke='#3c6e2c' stroke-width='1.3' fill='none'/>"
    for x, a, c2 in ((12, 60, "#6fdc7a"), (40, 120, "#4fbf5c"), (78, 55, "#9be86f"), (110, 125, "#6fdc7a")):
        vb += f"<path d='{leaf_path(x, 3, a, 6, 2.2)}' fill='{c2}' opacity='0.9'/>"
    vine = put(t, "enredadera", vb, 140, 10)
    rays = "repeating-linear-gradient(112deg, transparent 0 80px, rgba(210, 255, 170, 0.05) 80px 120px, transparent 120px 230px, rgba(210, 255, 170, 0.035) 230px 250px, transparent 250px 340px)"
    fog = uri("".join(f"<ellipse cx='{x}' cy='{y}' rx='{rx}' ry='{ry}' fill='#bfe8b0' opacity='{o}'/>" for x, y, rx, ry, o in
                      ((150, 230, 260, 70, 0.05), (520, 250, 300, 60, 0.06), (900, 225, 280, 80, 0.045), (1150, 255, 200, 50, 0.05))), 1200, 300)
    ART[t] = {
        "--t-valance": valance, "--t-valance-w": "300px", "--t-valance-h": "42px",
        "--t-emblem": emblem, "--t-emblem-faint": uri(f"<g opacity='0.2'>{e}</g>", 48, 48), "--t-crest": crest, "--t-vine": vine,
        "--hx-rail": f"{rail} center top / 10px 36px repeat-y", "--hx-rail-w": "10px", "--hx-rail-radius": "5px",
        "--hx-rune-clip": "polygon(50% 0, 80% 30%, 85% 60%, 50% 100%, 15% 60%, 20% 30%)",
        "--hx-cap-rot": "0deg", "--hx-cap-clip": "polygon(50% 0, 85% 35%, 50% 100%, 15% 35%)",
        "--t-splash-opacity": "0.45",
    }
    ART_CSS[t] = f"""
[data-theme="r-jungle"] .t-scene.a {{ inset: 0 0 0 var(--sidebar); background: {rays}; -webkit-mask-image: linear-gradient(170deg, #000, transparent 70%); mask-image: linear-gradient(170deg, #000, transparent 70%); animation: t-rays 14s ease-in-out infinite alternate; }}
[data-theme="r-jungle"] .t-scene.b {{ inset: auto -1200px 0 0; height: 300px; background: {fog} left bottom / 1200px 300px repeat-x; animation: t-slide 90s linear infinite; --t-slide: 1200px; }}
[data-theme="r-jungle"] .card {{ --t-card-edge: var(--t-vine) left 6px top 1px / 140px 10px repeat-x border-box; }}
"""

# ============================================================================================ MID: dagas y magia
def _mid():
    t = "r-mid"
    SILVER = S((0, "#ffffff"), (0.5, "#a3a9c4"), (1, "#4a4f68"))
    body = "<defs>" + lg("s", SILVER, 1, 0) + lg("v", S((0, "#b07aff", 0), (0.5, "#b07aff"), (1, "#ff6ad5", 0)), 1, 0) + rg("g", S((0, "#ff6ad5", 0.8), (1, "#b07aff", 0))) + "</defs>"
    body += "<rect y='1.5' width='220' height='1.8' fill='#a3a9c4'/><rect y='1.5' width='220' height='0.6' fill='#ffffff' opacity='0.85'/><rect y='3.3' width='220' height='0.5' fill='#2c3046'/><rect y='5' width='220' height='0.7' fill='#b07aff' opacity='0.6'/>"
    for x in range(10, 220, 20):
        body += f"<path d='M{x} 2.3 l2 1.6 l-2 1.6 l-2 -1.6Z' fill='#d6c2ff'/>"
    for x in (55, 165):
        body += "".join(f"<ellipse cx='{x}' cy='{6.5 + i * 2.6}' rx='0.9' ry='1.4' fill='none' stroke='#c9cfe6' stroke-width='0.6'/>" for i in range(3))
        body += f"<rect x='{x - 4.5}' y='13.6' width='9' height='1.8' rx='0.9' fill='#b07aff'/><rect x='{x - 0.9}' y='10' width='1.8' height='3.8' fill='#3b2c6e'/>"
        body += f"<path d='M{x - 2.2} 15.4 L{x + 2.2} 15.4 L{x} 32 Z' fill='url(#s)'/><path d='M{x} 15.6 V29' stroke='#b07aff' stroke-width='0.7'/>"
        body += f"<circle cx='{x}' cy='9' r='1.2' fill='#ff6ad5'/>"
    for x in (110, 210):
        body += f"<path d='M{x} 5.6 V11' stroke='#c9cfe6' stroke-width='0.5'/><circle cx='{x}' cy='16' r='7' fill='url(#g)'/>" + star4(x, 16, 5.5, "#f3e8ff") + star4(x, 16, 2.4, "#ff6ad5")
    valance = put(t, "cenefa", body, 220, 34)
    e = "<defs>" + lg("s", SILVER, 1, 0) + rg("g", S((0, "#ff6ad5", 0.7), (1, "#b07aff", 0))) + "</defs>"
    e += "<circle cx='24' cy='24' r='22' fill='url(#g)'/><circle cx='24' cy='24' r='17' fill='none' stroke='#b07aff' stroke-width='0.9' stroke-dasharray='2 2.5'/>"
    for ang in (-40, 40):
        e += (f"<g transform='rotate({ang} 24 24)'><path d='M21.5 5 L26.5 5 L24 -1 Z' fill='url(#s)' transform='translate(0 0)'/>"
              f"<path d='M21.6 6 H26.4 L25.6 30 H22.4 Z' fill='url(#s)' stroke='#2c3046' stroke-width='0.5'/><path d='M24 7 V28' stroke='#b07aff' stroke-width='0.9'/>"
              f"<path d='M17 30 Q24 33 31 30 L31 32 Q24 35 17 32 Z' fill='#c9cfe6' stroke='#2c3046' stroke-width='0.5'/><rect x='22.7' y='33' width='2.6' height='8' fill='#3b2c6e'/>"
              f"<path d='M24 41 l2 2.5 l-2 2.5 l-2 -2.5Z' fill='#ff6ad5'/></g>")
    e += star4(24, 24, 9, "#f3e8ff") + star4(24, 24, 4, "#ff6ad5")
    emblem = put(t, "emblema", e, 48, 48)
    c = "<defs>" + lg("s", SILVER, 1, 0) + "</defs><g fill='none' stroke='#b07aff'>"
    c += "<circle cx='110' cy='110' r='100' stroke-width='2'/><circle cx='110' cy='110' r='92' stroke-width='1' stroke-dasharray='4 6'/><circle cx='110' cy='110' r='58' stroke-width='1.5'/>"
    c += f"<path d='{poly([(110 + math.cos(math.radians(a)) * 92, 110 + math.sin(math.radians(a)) * 92) for a in (-90, 30, 150)])}' stroke-width='1.5'/>"
    c += f"<path d='{poly([(110 + math.cos(math.radians(a)) * 92, 110 + math.sin(math.radians(a)) * 92) for a in (90, 210, 330)])}' stroke-width='1.5'/></g>"
    for k in range(12):
        a = math.radians(k * 30)
        c += star4(110 + math.cos(a) * 75, 110 + math.sin(a) * 75, 4, "#d6c2ff")
    for ang in (-35, 35):
        c += (f"<g transform='rotate({ang} 110 110)'><path d='M102 20 L118 20 L116 140 H104 Z' fill='url(#s)' stroke='#2c3046' stroke-width='1.5'/><path d='M110 4 L118 20 H102 Z' fill='url(#s)'/>"
              f"<path d='M84 140 Q110 152 136 140 V148 Q110 160 84 148 Z' fill='#c9cfe6'/><rect x='105' y='152' width='10' height='34' fill='#3b2c6e'/></g>")
    c += star4(110, 110, 24, "#f3e8ff") + star4(110, 110, 10, "#ff6ad5")
    crest = put(t, "blasón", c, 220, 220, opacity=0.2)
    rl = "<defs>" + lg("g", S((0, "#2a2160"), (0.5, "#0e0a24"), (1, "#2a2160")), 1, 0) + "</defs><rect width='10' height='66' fill='url(#g)'/>"
    rl += "<rect x='1' width='0.8' height='66' fill='#c9cfe6' opacity='0.5'/><rect x='8.2' width='0.8' height='66' fill='#c9cfe6' opacity='0.5'/>"
    rl += "<path d='M5 7 l2.4 3 l-2.4 3 l-2.4 -3Z' fill='#b07aff'/><path d='M3 28 H7 M5 26 V32 M3 31 L7 31' stroke='#ff6ad5' stroke-width='0.9' fill='none'/>"
    rl += "<circle cx='5' cy='50' r='2.2' fill='none' stroke='#7be0ff' stroke-width='0.9'/><path d='M5 46.5 V53.5' stroke='#7be0ff' stroke-width='0.7'/>"
    rail = put(t, "raíl", rl, 10, 66)
    gb = "".join(f"<path d='M{x} 3 l1.6 2 l-1.6 2 l-1.6 -2Z' fill='#b07aff' opacity='0.6'/>" for x in (8, 40)) + "<path d='M20 5 H28 M24 3 V7' stroke='#ff6ad5' stroke-width='0.7' opacity='0.55'/><circle cx='56' cy='5' r='1' fill='#7be0ff' opacity='0.6'/>"
    glyphs = put(t, "runas", gb, 64, 9)
    circle = uri("<g fill='none' stroke='#b07aff'>"
                 "<circle cx='350' cy='350' r='340' stroke-width='2'/><circle cx='350' cy='350' r='322' stroke-width='1' stroke-dasharray='6 10'/>"
                 "<circle cx='350' cy='350' r='220' stroke-width='1.5'/>"
                 f"<path d='{poly([(350 + math.cos(math.radians(a)) * 322, 350 + math.sin(math.radians(a)) * 322) for a in (-90, -18, 54, 126, 198)])}' stroke-width='1.2'/>"
                 f"<path d='M{350} {28} L{350} {672} M{28} {350} L{672} {350}' stroke-width='0.6'/></g>"
                 + "".join(star4(350 + math.cos(math.radians(a)) * 270, 350 + math.sin(math.radians(a)) * 270, 9, "#d6c2ff") for a in range(0, 360, 30)), 700, 700)
    inner = uri("<g fill='none' stroke='#ff6ad5'><circle cx='200' cy='200' r='190' stroke-width='1.5' stroke-dasharray='2 8'/><circle cx='200' cy='200' r='150' stroke-width='1'/>"
                f"<path d='{poly([(200 + math.cos(math.radians(a)) * 150, 200 + math.sin(math.radians(a)) * 150) for a in (-90, 30, 150)])}' stroke-width='1'/>"
                f"<path d='{poly([(200 + math.cos(math.radians(a)) * 150, 200 + math.sin(math.radians(a)) * 150) for a in (90, 210, 330)])}' stroke-width='1'/></g>", 400, 400)
    ART[t] = {
        "--t-valance": valance, "--t-valance-w": "220px", "--t-valance-h": "34px",
        "--t-emblem": emblem, "--t-emblem-faint": uri(f"<g opacity='0.2'>{e}</g>", 48, 48), "--t-crest": crest, "--t-glyphs": glyphs,
        "--hx-rail": f"{rail} center top / 10px 66px repeat-y", "--hx-rail-w": "10px", "--hx-rail-radius": "1px",
        "--hx-rune-clip": "polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%)",
        "--hx-cap-rot": "0deg", "--hx-cap-clip": "polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%)",
        "--t-splash-opacity": "0.45",
    }
    ART_CSS[t] = f"""
[data-theme="r-mid"] .t-scene.a {{ width: 760px; height: 760px; right: -220px; top: -160px; background: {circle} center / contain no-repeat; opacity: 0.16; animation: t-spin 240s linear infinite; }}
[data-theme="r-mid"] .t-scene.b {{ width: 440px; height: 440px; right: -60px; top: 0; background: {inner} center / contain no-repeat; opacity: 0.14; animation: t-spin 160s linear infinite reverse; }}
[data-theme="r-mid"] .card {{ --t-card-edge: var(--t-glyphs) left 10px bottom 2px / 64px 9px repeat-x padding-box; }}
"""

# ============================================================================================ ADC: tiradores y armas
def _adc():
    t = "r-adc"
    BRASS = S((0, "#6a4a14"), (0.3, "#fff0c4"), (0.55, "#d9ad55"), (1, "#4a3410"))
    COPPER = S((0, "#5a1a0e"), (0.35, "#ffb08a"), (0.6, "#c8452a"), (1, "#4a0e06"))
    body = "<defs>" + lg("b", BRASS, 1, 0) + lg("c", COPPER, 1, 0) + lg("l", S((0, "#5a4028"), (1, "#22170c"))) + "</defs>"
    body += "<rect width='12' height='7' fill='url(#l)'/><path d='M0 1.3 H12 M0 5.8 H12' stroke='#c9a45c' stroke-opacity='0.55' stroke-width='0.5' stroke-dasharray='1.6 1.4'/>"
    body += "<rect x='3' y='5.4' width='6' height='10' rx='0.6' fill='url(#b)' stroke='#2a1c06' stroke-width='0.4'/><rect x='3' y='7.5' width='6' height='0.5' fill='#3a2608' opacity='0.6'/>"
    body += "<path d='M3 15.4 Q3 21 6 22.4 Q9 21 9 15.4 Z' fill='url(#c)' stroke='#2a0804' stroke-width='0.4'/>"
    body += "<rect x='1.6' y='3.2' width='8.8' height='4.8' rx='1' fill='#3a2818' stroke='#1a1008' stroke-width='0.4'/><rect x='1.6' y='3.6' width='8.8' height='0.6' fill='#7a5a38' opacity='0.6'/>"
    valance = put(t, "cenefa", body, 12, 23)
    e = "<defs>" + lg("b", BRASS, 1, 0) + rg("g", S((0, "#ec343e", 0.7), (1, "#ec343e", 0))) + "</defs>"
    e += "<circle cx='24' cy='24' r='22' fill='url(#g)'/><circle cx='24' cy='24' r='16' fill='#0e0e11' stroke='url(#b)' stroke-width='2.4'/>"
    e += "<circle cx='24' cy='24' r='7' fill='none' stroke='#ec343e' stroke-width='1.3'/>"
    e += "<path d='M24 3 V14 M24 34 V45 M3 24 H14 M34 24 H45' stroke='#e2b24c' stroke-width='1.8' stroke-linecap='round'/>"
    e += "".join(f"<circle cx='{24 + d}' cy='24' r='0.9' fill='#fff0c4'/><circle cx='24' cy='{24 + d}' r='0.9' fill='#fff0c4'/>" for d in (-11, 11))
    e += "<circle cx='24' cy='24' r='1.8' fill='#ff4a3a'/><circle cx='24' cy='24' r='0.8' fill='#fff'/>"
    emblem = put(t, "emblema", e, 48, 48)
    c = "<defs>" + lg("b", BRASS, 1, 0) + lg("c", COPPER, 1, 0) + "</defs><g fill='none' stroke='#e2b24c'>"
    c += "<circle cx='110' cy='96' r='86' stroke-width='5'/><circle cx='110' cy='96' r='70' stroke-width='1.2'/><circle cx='110' cy='96' r='30' stroke='#ec343e' stroke-width='2.5'/>"
    c += "<path d='M110 10 V60 M110 132 V182 M24 96 H74 M146 96 H196' stroke-width='3'/></g>"
    c += "".join(f"<rect x='{110 + d - 1.5}' y='93' width='3' height='6' fill='#e2b24c'/><rect x='107' y='{96 + d - 1.5}' width='6' height='3' fill='#e2b24c'/>" for d in (-60, -48, 48, 60))
    for ang in (-28, 28):
        c += (f"<g transform='rotate({ang} 110 200)'><rect x='103' y='150' width='14' height='40' rx='1.5' fill='url(#b)' stroke='#2a1c06' stroke-width='1.2'/>"
              f"<path d='M103 150 Q103 128 110 122 Q117 128 117 150 Z' fill='url(#c)' stroke='#2a0804' stroke-width='1.2'/><rect x='101' y='188' width='18' height='5' fill='#7a561a'/></g>")
    c += "<circle cx='110' cy='96' r='5' fill='#ff4a3a'/>"
    crest = put(t, "blasón", c, 220, 220, opacity=0.2)
    rl = "<defs>" + lg("b", BRASS, 1, 0) + lg("c", COPPER, 1, 0) + "</defs><rect width='10' height='7' fill='#2a2018'/>"
    rl += "<rect x='0.5' y='1.2' width='6.6' height='4.6' rx='0.5' fill='url(#b)'/><path d='M7.1 1.2 Q10 2.4 10 3.5 Q10 4.6 7.1 5.8Z' fill='url(#c)'/><rect x='0.5' y='1.2' width='1' height='4.6' fill='#4a3410'/>"
    rail = put(t, "raíl", rl, 10, 7)
    rb = "<path d='M0 0.5 H7 M0 6.5 H3.5' stroke='#e2b24c' stroke-width='1' opacity='0.45'/>"
    ruler = put(t, "regla", rb, 8, 12)
    sweep = "conic-gradient(from 0deg, rgba(236, 52, 62, 0.16), rgba(236, 52, 62, 0) 22%, transparent 100%)"
    rings = uri("<g fill='none' stroke='#e2b24c'>" + "".join(f"<circle cx='300' cy='300' r='{r}' stroke-width='{w}' stroke-opacity='{o}'/>" for r, w, o in ((290, 2, 0.5), (220, 1, 0.4), (150, 1, 0.4), (80, 1, 0.5))) + "<path d='M300 0 V600 M0 300 H600' stroke-width='0.8' stroke-opacity='0.35'/></g>", 600, 600)
    ART[t] = {
        "--t-valance": valance, "--t-valance-w": "12px", "--t-valance-h": "23px",
        "--t-emblem": emblem, "--t-emblem-faint": uri(f"<g opacity='0.2'>{e}</g>", 48, 48), "--t-crest": crest, "--t-ruler": ruler,
        "--hx-rail": f"{rail} center top / 10px 7px repeat-y", "--hx-rail-w": "10px", "--hx-rail-radius": "1px",
        "--hx-rune-clip": "circle(50%)",
        "--hx-cap-rot": "0deg", "--hx-cap-clip": "circle(50%)",
        "--t-splash-opacity": "0.42",
    }
    ART_CSS[t] = f"""
[data-theme="r-adc"] .t-scene.a {{ width: 620px; height: 620px; right: -200px; bottom: -220px; border-radius: 50%; background: {sweep}; animation: t-spin 9s linear infinite; }}
[data-theme="r-adc"] .t-scene.b {{ width: 620px; height: 620px; right: -200px; bottom: -220px; background: {rings} center / contain no-repeat; opacity: 0.18; }}
[data-theme="r-adc"] .card {{ --t-card-edge: repeating-linear-gradient(180deg, rgba(226, 178, 76, 0.5) 0 1px, transparent 1px 24px) left 4px top 30px / 8px calc(100% - 60px) no-repeat padding-box, repeating-linear-gradient(180deg, rgba(226, 178, 76, 0.32) 0 1px, transparent 1px 12px) left 4px top 30px / 4px calc(100% - 60px) no-repeat padding-box; }}
"""

# ============================================================================================ SUPPORT ENGAGE
def _engage():
    t = "r-engage"
    IRON = S((0, "#dfe7ea"), (0.5, "#7f8f96"), (1, "#2a3438"))
    body = "<defs>" + lg("i", IRON) + rg("g", S((0, "#c8ffe8", 0.9), (0.35, "#2cdc98", 0.5), (1, "#2cdc98", 0))) + "</defs>"
    # Cadena en guirnalda (catenaria) con eslabones a lo largo.
    def chain_y(x):
        return 2 + 14 * (1 - ((x - 130) / 130) ** 2)
    for k, x in enumerate(range(0, 261, 7)):
        y = chain_y(x)
        slope = math.degrees(math.atan2(chain_y(x + 1) - y, 1))
        if k % 2 == 0:
            body += f"<ellipse cx='{x}' cy='{f(y)}' rx='3.6' ry='1.9' fill='none' stroke='#9aa8b0' stroke-width='1.2' transform='rotate({f(slope)} {x} {f(y)})'/>"
        else:
            body += f"<rect x='{x - 3}' y='{f(y - 0.6)}' width='6' height='1.2' rx='0.6' fill='#6d7e84' transform='rotate({f(slope)} {x} {f(y)})'/>"
    for x in (0, 260):
        body += (f"<path d='M{x} 3 V17 Q{x} 25 {x - 6} 24 Q{x - 9.5} 22.5 {x - 9} 18.5' stroke='#b6c4ca' stroke-width='2' fill='none' stroke-linecap='round'/>"
                 f"<path d='M{x - 10.6} 19.6 L{x - 9} 14.6 L{x - 7.4} 19.4Z' fill='#dfe7ea'/><circle cx='{x}' cy='3' r='2.4' fill='none' stroke='#dfe7ea' stroke-width='1.2'/>")
    lx, ly = 130, 16
    body += f"<circle cx='{lx}' cy='{ly + 12}' r='14' fill='url(#g)'/><path d='M{lx} {ly} V{ly + 3}' stroke='#9aa8b0' stroke-width='1'/>"
    body += f"<path d='M{lx - 4.5} {ly + 6} L{lx - 2} {ly + 3} H{lx + 2} L{lx + 4.5} {ly + 6}Z' fill='url(#i)'/><rect x='{lx - 4}' y='{ly + 6}' width='8' height='9' rx='1.5' fill='#2cdc98' opacity='0.85'/>"
    body += f"<path d='M{lx - 4} {ly + 6} V{ly + 15} M{lx} {ly + 6} V{ly + 15} M{lx + 4} {ly + 6} V{ly + 15}' stroke='#1a2226' stroke-width='0.8'/><path d='M{lx} {ly + 8} Q{lx + 1.8} {ly + 11} {lx} {ly + 13} Q{lx - 1.8} {ly + 11} {lx} {ly + 8}Z' fill='#effff9'/>"
    body += f"<path d='M{lx - 4.5} {ly + 15} H{lx + 4.5} L{lx + 2} {ly + 18} H{lx - 2}Z' fill='url(#i)'/>"
    valance = put(t, "cenefa", body, 260, 40)
    def anchor(cx, cy, s, stroke):
        return (f"<g transform='translate({cx} {cy}) scale({s})' fill='none' stroke='{stroke}' stroke-linecap='round' stroke-linejoin='round'>"
                "<circle cx='0' cy='-15' r='3.4' stroke-width='2.2'/><path d='M0 -11.5 V16' stroke-width='3'/><path d='M-8 -7 H8' stroke-width='2.6'/>"
                "<path d='M-14 5 Q-12 17 0 17 Q12 17 14 5' stroke-width='2.8'/></g>"
                f"<g transform='translate({cx} {cy}) scale({s})' fill='{stroke}'><path d='M-14 1.5 L-17.5 8 L-10.5 7Z'/><path d='M14 1.5 L17.5 8 L10.5 7Z'/></g>")
    e = "<defs>" + lg("i", IRON, 1, 1) + rg("g", S((0, "#2cdc98", 0.7), (1, "#2cdc98", 0))) + "</defs>"
    e += "<circle cx='24' cy='24' r='22' fill='url(#g)'/>" + anchor(24, 24, 1.05, "url(#i)")
    e += "".join(f"<ellipse cx='{f(10 + i * 4.2)}' cy='{f(36 - i * 4.2)}' rx='2.4' ry='1.3' fill='none' stroke='#2cdc98' stroke-width='0.9' transform='rotate(-45 {f(10 + i * 4.2)} {f(36 - i * 4.2)})'/>" for i in range(7))
    emblem = put(t, "emblema", e, 48, 48)
    c = "<defs>" + lg("i", IRON, 1, 1) + rg("g", S((0, "#2cdc98", 0.6), (1, "#2cdc98", 0))) + "</defs>"
    c += "<circle cx='110' cy='110' r='100' fill='url(#g)'/>"
    c += "".join(f"<ellipse cx='{f(110 + math.cos(math.radians(a)) * 92)}' cy='{f(110 + math.sin(math.radians(a)) * 92)}' rx='7' ry='3.6' fill='none' stroke='#9aa8b0' stroke-width='2.2' transform='rotate({a + 90} {f(110 + math.cos(math.radians(a)) * 92)} {f(110 + math.sin(math.radians(a)) * 92)})'/>" for a in range(0, 360, 12))
    c += anchor(110, 112, 4.4, "url(#i)")
    c += "<path d='M40 40 Q70 60 86 96' stroke='#dfe7ea' stroke-width='5' fill='none' stroke-linecap='round'/><path d='M86 96 L78 92 L92 110Z' fill='#dfe7ea'/>"
    crest = put(t, "blasón", c, 220, 220, opacity=0.2)
    rl = "<defs>" + lg("r", S((0, "#6a5236"), (0.5, "#c9a878"), (1, "#4a3620")), 1, 0) + "</defs><rect x='2' width='6' height='8' fill='url(#r)'/>"
    rl += "<path d='M2 2 L8 -1 M2 6 L8 3 M2 10 L8 7' stroke='#2a1c0e' stroke-width='1' opacity='0.7'/><path d='M2 3 L8 0 M2 7 L8 4' stroke='#f0dcb0' stroke-width='0.5' opacity='0.5'/>"
    rail = put(t, "raíl", rl, 10, 8)
    rays = "repeating-linear-gradient(98deg, transparent 0 70px, rgba(120, 255, 210, 0.06) 70px 110px, transparent 110px 190px, rgba(120, 255, 210, 0.04) 190px 205px, transparent 205px 300px)"
    ART[t] = {
        "--t-valance": valance, "--t-valance-w": "260px", "--t-valance-h": "40px",
        "--t-emblem": emblem, "--t-emblem-faint": uri(f"<g opacity='0.2'>{e}</g>", 48, 48), "--t-crest": crest,
        "--hx-rail": f"{rail} center top / 10px 8px repeat-y", "--hx-rail-w": "10px", "--hx-rail-radius": "3px",
        "--hx-rune-clip": "circle(50%)",
        "--hx-cap-rot": "0deg", "--hx-cap-clip": "circle(50%)",
        "--t-splash-opacity": "0.45",
    }
    ART_CSS[t] = f"""
[data-theme="r-engage"] .t-scene.a {{ inset: -10% 0 0 var(--sidebar); background: {rays}; -webkit-mask-image: linear-gradient(180deg, #000, transparent 75%); mask-image: linear-gradient(180deg, #000, transparent 75%); transform-origin: 50% 0; animation: t-sway 12s ease-in-out infinite alternate; }}
[data-theme="r-engage"] .t-scene.b {{ width: 640px; height: 640px; right: 4%; top: -60px; background: radial-gradient(circle at 50% 78%, rgba(44, 222, 152, 0.2), rgba(44, 222, 152, 0.06) 24%, transparent 46%); transform-origin: 50% -40%; animation: t-pendulum 7s ease-in-out infinite alternate; }}
"""

# ============================================================================================ SUPPORT GUARDIÁN
def _guardian():
    t = "r-guardian"
    ICE = S((0, "#ffffff", 0.95), (0.5, "#bfe3ff", 0.75), (1, "#7ac8ff", 0.35))
    body = "<defs>" + lg("i", ICE) + "</defs>"
    snow = "M0 0 H220 V3"
    for i in range(22, 0, -1):
        x = i * 10
        snow += f" Q{x - 5} {6.5 if i % 2 else 5} {x - 10} {3.5 if i % 3 else 4.5}"
    rnd = random.Random(19)
    x = 4
    while x < 216:
        wd = rnd.uniform(3.5, 7)
        ln = rnd.uniform(6, 26)
        body += f"<path d='M{f(x - wd / 2)} 3 L{f(x + wd / 2)} 3 L{f(x + rnd.uniform(-0.8, 0.8))} {f(ln)} Z' fill='url(#i)'/><path d='M{f(x - wd / 2 + 0.6)} 3.4 L{f(x)} {f(ln * 0.8)}' stroke='#ffffff' stroke-width='0.5' opacity='0.8'/>"
        if ln > 21:
            body += star4(x, ln + 1.5, 3.2, rnd.choice(["#ff70d8", "#ffffff"]))
        x += rnd.uniform(6, 13)
    body += f"<path d='{snow} V0Z' fill='#f4faff'/><path d='M0 1 H220' stroke='#ffffff' stroke-width='1' opacity='0.8'/>"
    valance = put(t, "cenefa", body, 220, 30)
    SIL = S((0, "#ffffff"), (0.5, "#a9cbe8"), (1, "#3c5874"))
    e = "<defs>" + lg("s", SIL, 1, 1) + lg("f", S((0, "#bfe3ff"), (1, "#1d4a80"))) + lg("p", S((0, "#ffd0f2"), (0.5, "#ff70d8"), (1, "#8a2a7a")), 1, 1) + rg("g", S((0, "#7ac8ff", 0.6), (1, "#7ac8ff", 0))) + "</defs>"
    e += "<circle cx='24' cy='24' r='22' fill='url(#g)'/>"
    e += "<path d='M9 8 Q24 4 39 8 V24 Q39 37 24 45 Q9 37 9 24Z' fill='url(#s)' stroke='#18283a' stroke-width='1'/>"
    e += "<path d='M12.5 11 Q24 8 35.5 11 V24 Q35.5 34.5 24 41 Q12.5 34.5 12.5 24Z' fill='url(#f)'/>"
    e += "<g stroke='#ffffff' stroke-width='0.7' opacity='0.5'><path d='M24 9 V41 M13 17 L35 33 M35 17 L13 33'/></g>"
    e += "<path d='M24 15 L31 22 L24 33 L17 22Z' fill='url(#p)' stroke='#4a0a40' stroke-width='0.7'/><path d='M24 15 L31 22 H17Z' fill='#ffe0f6' opacity='0.8'/>"
    e += "<path d='M9.5 8.6 Q24 3 38.5 8.6 Q34 10.8 24 8.4 Q14 10.8 9.5 8.6Z' fill='#ffffff'/>"
    emblem = put(t, "emblema", e, 48, 48)
    c = "<defs>" + lg("s", SIL, 1, 1) + lg("p", S((0, "#ffd0f2"), (0.5, "#ff70d8"), (1, "#8a2a7a")), 1, 1) + "</defs>"
    for k in range(6):
        a = k * 60
        c += f"<g transform='rotate({a} 110 110)' stroke='#cfe8ff' stroke-width='4' stroke-linecap='round' fill='none'><path d='M110 110 V10'/><path d='M110 40 L94 26 M110 40 L126 26 M110 66 L98 56 M110 66 L122 56'/></g>"
    c += "<path d='M60 52 Q110 38 160 52 V112 Q160 160 110 190 Q60 160 60 112Z' fill='url(#s)' stroke='#18283a' stroke-width='3'/>"
    hexes = ""
    for row in range(7):
        for col in range(5):
            hx = 72 + col * 19 + (9.5 if row % 2 else 0)
            hy = 66 + row * 16
            hexes += f"<path d='{poly([(hx + math.cos(math.radians(a)) * 9, hy + math.sin(math.radians(a)) * 9) for a in range(30, 390, 60)])}'/>"
    c += f"<g fill='none' stroke='#3c5874' stroke-width='1.2' opacity='0.7'>{hexes}</g>"
    c += "<path d='M110 80 L136 106 L110 146 L84 106Z' fill='url(#p)' stroke='#4a0a40' stroke-width='2'/><path d='M110 80 L136 106 H84Z' fill='#ffe0f6' opacity='0.7'/>"
    crest = put(t, "blasón", c, 220, 220, opacity=0.2)
    rl = "<defs>" + lg("g", S((0, "#bfe3ff", 0.55), (0.5, "#2e4a6e", 0.6), (1, "#bfe3ff", 0.55)), 1, 0) + "</defs><rect width='10' height='30' fill='url(#g)'/>"
    rl += "<path d='M5 4 L8 9 L5 15 L2 9Z' fill='none' stroke='#ffffff' stroke-width='0.7' opacity='0.85'/><path d='M5 4 L5 15 M2 9 H8' stroke='#ffffff' stroke-width='0.4' opacity='0.5'/>"
    rl += "<path d='M2 21 L5 24 L8 21' stroke='#ffffff' stroke-width='0.6' fill='none' opacity='0.5'/><circle cx='5' cy='27' r='0.9' fill='#ff70d8'/>"
    rail = put(t, "raíl", rl, 10, 30)
    ib = "<defs>" + lg("i", ICE) + "</defs>" + "".join(f"<path d='M{x - 2} 0 L{x + 2} 0 L{x} {ln}Z' fill='url(#i)'/>" for x, ln in ((6, 7), (15, 4), (27, 9), (40, 5), (52, 8), (63, 4)))
    icicles = put(t, "carámbanos", ib, 70, 10)
    aurora = ("radial-gradient(50% 30% at 30% 20%, rgba(122, 255, 200, 0.16), transparent 70%), "
              "radial-gradient(40% 26% at 62% 14%, rgba(255, 112, 216, 0.12), transparent 70%), "
              "radial-gradient(60% 34% at 85% 26%, rgba(122, 202, 255, 0.16), transparent 70%)")
    honey = uri("".join(f"<path d='{poly([(x + math.cos(math.radians(a)) * 26, y + math.sin(math.radians(a)) * 26) for a in range(30, 390, 60)])}' fill='none' stroke='#bfe3ff' stroke-width='1.4'/>"
                        for x, y in ((26, 22.5), (71, 22.5), (48.5, 61.5), (93.5, 61.5), (3.5, 61.5))), 90, 78)
    ART[t] = {
        "--t-valance": valance, "--t-valance-w": "220px", "--t-valance-h": "30px",
        "--t-emblem": emblem, "--t-emblem-faint": uri(f"<g opacity='0.2'>{e}</g>", 48, 48), "--t-crest": crest, "--t-icicles": icicles,
        "--hx-rail": f"{rail} center top / 10px 30px repeat-y", "--hx-rail-w": "10px", "--hx-rail-radius": "2px",
        "--hx-rune-clip": "polygon(50% 0, 100% 50%, 50% 100%, 0 50%)",
        "--hx-cap-rot": "0deg", "--hx-cap-clip": "polygon(50% 0, 100% 50%, 50% 100%, 0 50%)",
        "--t-splash-opacity": "0.45",
    }
    ART_CSS[t] = f"""
[data-theme="r-guardian"] .t-scene.a {{ inset: -5% -30% 30% calc(var(--sidebar) - 10%); background: {aurora}; animation: t-aurora 18s ease-in-out infinite alternate; }}
[data-theme="r-guardian"] .t-scene.b {{ width: 560px; height: 100vh; right: 0; top: 0; background: {honey} 0 0 / 90px 78px; --t-o: 0.08; -webkit-mask-image: radial-gradient(closest-side at 70% 40%, #000, transparent); mask-image: radial-gradient(closest-side at 70% 40%, #000, transparent); animation: t-breathe 6s ease-in-out infinite alternate; }}
[data-theme="r-guardian"] .card {{ --t-card-edge: var(--t-icicles) left 14px top 1px / 70px 10px repeat-x padding-box; }}
"""

# ============================================================================================ SUPPORT PEEL
def _peel():
    t = "r-peel"
    colors = ["#70ffc0", "#ffa8e6", "#fffaf0", "#b8a0ff"]
    body = "<path d='M0 3 Q52.5 13 105 3 Q157.5 13 210 3' stroke='#fffaf0' stroke-opacity='0.75' stroke-width='0.8' fill='none'/>"
    k = 0
    for x in range(9, 210, 21):
        y = 3 + 5 * (1 - ((x % 105 - 52.5) / 52.5) ** 2)
        col = colors[k % 4]
        body += f"<path d='M{x - 5} {f(y)} L{x + 5} {f(y)} L{x} {f(y + 12)}Z' fill='{col}' opacity='0.9'/><path d='M{x - 5} {f(y)} L{x} {f(y + 12)}' stroke='#ffffff' stroke-width='0.6' opacity='0.7'/>"
        if k % 2:
            body += star4(x + 10.5, y + 7, 3.2, "#ffffff", 0.95)
        k += 1
    valance = put(t, "cenefa", body, 210, 24)
    e = "<defs>" + lg("h", S((0, "#b8fff0"), (0.5, "#70ffc0"), (1, "#ffa8e6")), 1, 1) + rg("g", S((0, "#ffa8e6", 0.6), (1, "#70ffc0", 0))) + "</defs>"
    e += "<circle cx='24' cy='24' r='22' fill='url(#g)'/><circle cx='24' cy='24' r='17' fill='none' stroke='#fffaf0' stroke-width='0.8' stroke-dasharray='1.5 2.5' opacity='0.8'/>"
    for side in (-1, 1):
        e += f"<path d='M{24 + side * 9} 20 Q{24 + side * 20} 10 {24 + side * 22} 18 Q{24 + side * 18} 18 {24 + side * 19} 23 Q{24 + side * 14} 22 {24 + side * 13} 26Z' fill='#fffaf0' opacity='0.9'/>"
    e += "<path d='M24 38 C10 29 12 16 24 22 C36 16 38 29 24 38Z' fill='url(#h)' stroke='#ffffff' stroke-width='0.8'/>"
    e += "<path d='M22.6 23.5 H25.4 V26.3 H28.2 V29.1 H25.4 V31.9 H22.6 V29.1 H19.8 V26.3 H22.6Z' fill='#ffffff'/>"
    e += star4(36, 10, 3.4, "#fff8c8") + star4(11, 36, 2.6, "#ffffff")
    emblem = put(t, "emblema", e, 48, 48)
    c = "<defs>" + lg("h", S((0, "#b8fff0"), (0.5, "#70ffc0"), (1, "#ffa8e6")), 1, 1) + lg("y", S((0, "#fffaf0"), (1, "#ffd27a")), 1, 1) + "</defs>"
    pts = []
    for i in range(10):
        a = math.radians(-90 + i * 36)
        r = 92 if i % 2 == 0 else 40
        pts.append((110 + math.cos(a) * r, 108 + math.sin(a) * r))
    c += f"<path d='{poly(pts)}' fill='url(#y)' opacity='0.9' stroke='#ffffff' stroke-width='2'/>"
    c += "<path d='M110 150 C64 118 72 70 110 92 C148 70 156 118 110 150Z' fill='url(#h)' stroke='#ffffff' stroke-width='2.5'/>"
    c += "<path d='M30 170 Q70 150 110 176 Q150 150 190 170 L180 186 Q150 168 110 192 Q70 168 40 186Z' fill='#ffa8e6' opacity='0.85'/>"
    rnd = random.Random(23)
    for _ in range(9):
        c += star4(rnd.uniform(20, 200), rnd.uniform(20, 200), rnd.uniform(3, 7), rnd.choice(["#ffffff", "#70ffc0", "#ffa8e6"]))
    crest = put(t, "blasón", c, 220, 220, opacity=0.2)
    rl = "<defs>" + lg("r", S((0, "#70ffc0"), (0.5, "#fffaf0"), (1, "#ffa8e6")), 1, 0) + "</defs><rect x='1' width='8' height='26' rx='4' fill='url(#r)' opacity='0.55'/>"
    rl += star4(5, 7, 3.4, "#ffffff") + "<path d='M5 22 C2.6 20.4 3 18 5 19 C7 18 7.4 20.4 5 22Z' fill='#ff7ac8'/>"
    rail = put(t, "raíl", rl, 10, 26)
    sp = star4(6, 3, 2.6, "#ffffff", 0.75) + "<circle cx='20' cy='3' r='0.9' fill='#70ffc0' opacity='0.7'/><circle cx='32' cy='3' r='0.9' fill='#ffa8e6' opacity='0.7'/>" + star4(44, 3, 1.8, "#fff8c8", 0.7)
    sparkle = put(t, "destellos", sp, 52, 6)
    orbs = uri("".join(f"<circle cx='{x}' cy='{y}' r='{r}' fill='{c2}' opacity='{o}'/>" for x, y, r, c2, o in
                       ((120, 140, 60, "#70ffc0", 0.07), (420, 380, 90, "#ffa8e6", 0.06), (760, 160, 70, "#b8a0ff", 0.07), (640, 700, 110, "#70ffc0", 0.05),
                        (220, 620, 80, "#ffa8e6", 0.06), (860, 520, 50, "#fffaf0", 0.05))), 900, 900)
    halo = uri("".join(star4(260 + math.cos(math.radians(a)) * 230, 260 + math.sin(math.radians(a)) * 230, 7 if a % 60 else 12, "#ffffff", 0.8) for a in range(0, 360, 20))
               + "<circle cx='260' cy='260' r='230' fill='none' stroke='#fffaf0' stroke-width='1' stroke-dasharray='2 10' opacity='0.6'/>", 520, 520)
    ART[t] = {
        "--t-valance": valance, "--t-valance-w": "210px", "--t-valance-h": "24px",
        "--t-emblem": emblem, "--t-emblem-faint": uri(f"<g opacity='0.2'>{e}</g>", 48, 48), "--t-crest": crest, "--t-sparkle": sparkle,
        "--hx-rail": f"{rail} center top / 10px 26px repeat-y", "--hx-rail-w": "10px", "--hx-rail-radius": "5px",
        "--hx-rune-clip": "polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%)",
        "--hx-cap-rot": "0deg", "--hx-cap-clip": "circle(50%)",
        "--t-splash-opacity": "0.45",
    }
    ART_CSS[t] = f"""
[data-theme="r-peel"] .t-scene.a {{ inset: 0 0 -900px var(--sidebar); background: {orbs} 0 0 / 900px 900px; animation: t-rise 80s linear infinite; --t-rise: -900px; }}
[data-theme="r-peel"] .t-scene.b {{ width: 520px; height: 520px; right: -120px; top: -140px; background: {halo} center / contain no-repeat; opacity: 0.35; animation: t-spin 120s linear infinite; }}
[data-theme="r-peel"] .card {{ --t-card-edge: var(--t-sparkle) left 18px top 3px / 52px 6px repeat-x padding-box; }}
"""


for build in (_top, _jungle, _mid, _adc, _engage, _guardian, _peel):
    build()


def preview_html():
    out = ["<!doctype html><meta charset=utf-8><style>body{background:#0b0d10;color:#ddd;font:12px sans-serif;margin:10px}"
           ".t{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start;margin:6px 0 18px}figure{margin:0;background:#151a20;padding:6px}"
           "figure.wide{flex-basis:100%}.rep{height:60px}</style>"]
    for theme, items in PREVIEW.items():
        out.append(f"<h3>{theme}</h3><div class=t>")
        for name, svg_raw in items:
            data = "data:image/svg+xml," + quote(" ".join(svg_raw.split()), safe=" '=:/;,.()-_!*~")
            if name in ("cenefa",):
                out.append(f"<figure class=wide><div class=rep style=\"background:url(&quot;{data}&quot;) left top repeat-x;background-size:auto;zoom:2\"></div><figcaption>{name}</figcaption></figure>")
            elif name in ("raíl",):
                out.append(f"<figure><div style=\"width:10px;height:200px;background:url(&quot;{data}&quot;) repeat-y;zoom:3\"></div><figcaption>{name}</figcaption></figure>")
            elif name in ("costura", "enredadera", "runas", "regla", "carámbanos", "destellos"):
                out.append(f"<figure><div style=\"width:300px;height:14px;background:url(&quot;{data}&quot;) repeat;zoom:2\"></div><figcaption>{name}</figcaption></figure>")
            else:
                out.append(f"<figure><img src=\"{data}\" style='width:{'220' if name == 'blasón' else '96'}px'><figcaption>{name}</figcaption></figure>")
        out.append("</div>")
    return "".join(out)
