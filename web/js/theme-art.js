/** Arte de fondo de los temas por línea (theme.js): detrás de la página, el splash de una skin de esa línea (se van
 *  turnando con un fundido y un zoom lento), dos capas de escena animadas (cada tema dice cómo son en su CSS:
 *  engranajes, rayos de luz, círculo arcano, radar, farol, aurora, destellos) y la cenefa que cuelga bajo la barra
 *  de arriba. Los splash son los de Riot (Data Dragon), como los iconos de campeón: sin conexión no salen y la
 *  página se queda con el resto. */

const SPLASH = "https://ddragon.leagueoflegends.com/cdn/img/champion/splash/";
const ROTATE_MS = 50_000;

/** Skins por tema: [campeón_número, encuadre (dónde mirar del splash)]. */
const ART = {
  // Top, tanques: la forja de Ornn y los reinos mecha.
  "r-top": [["Ornn_0", "60% 28%"], ["Garen_22", "62% 20%"], ["Sion_5", "60% 25%"], ["Malphite_6", "58% 30%"]],
  // Jungla: Hecarim del bosque Antiguo, el de siempre, y Kindred del Florecer Espiritual.
  "r-jungle": [["Hecarim_5", "58% 30%"], ["Hecarim_0", "60% 30%"], ["Kindred_3", "55% 25%"]],
  // Mid, dagas y magia: Syndra, Katarina y Ahri.
  "r-mid": [["Syndra_6", "60% 25%"], ["Katarina_10", "62% 25%"], ["Ahri_27", "55% 20%"]],
  // ADC, tiradores: el Salvaje Oeste de Jhin y Lucian, y Miss Fortune Diosa de las Armas.
  "r-adc": [["Jhin_1", "60% 22%"], ["MissFortune_16", "58% 25%"], ["Lucian_8", "62% 22%"]],
  // Engage: Thresh, Pyke, Nautilus y Rell.
  "r-engage": [["Thresh_0", "60% 25%"], ["Pyke_0", "58% 25%"], ["Nautilus_0", "60% 30%"], ["Rell_0", "60% 25%"]],
  // Guardián: Braum y Taric.
  "r-guardian": [["Braum_0", "55% 30%"], ["Taric_9", "60% 25%"], ["Taric_0", "58% 25%"]],
  // Peel: guardianas estelares.
  "r-peel": [["Janna_7", "60% 25%"], ["Lulu_6", "58% 30%"], ["Soraka_7", "60% 25%"], ["Sona_35", "58% 25%"]],
};

let stage = null;
let layers = [];
let valance = null;
let timer = 0;
let current = null;
let index = 0;
let wanted = 0; // para no pintar un splash que llega tarde de otro tema

function el(tag, className, parent) {
  const node = document.createElement(tag);
  node.className = className;
  node.setAttribute("aria-hidden", "true");
  parent?.appendChild(node);
  return node;
}

/** Pone (o quita) el arte del tema: los que no son por línea no llevan. */
export function setThemeArt(themeId) {
  const list = ART[themeId];
  clearInterval(timer);
  if (!list) {
    current = null;
    return; // el CSS ya esconde el escenario fuera de los temas por línea
  }
  if (!stage) {
    stage = el("div", "t-stage");
    layers = [el("i", "t-splash", stage), el("i", "t-splash", stage)];
    el("i", "t-scene a", stage);
    el("i", "t-scene b", stage);
    document.body.prepend(stage);
  }
  if (!valance?.isConnected) {
    const topbar = document.querySelector(".topbar");
    if (topbar) valance = el("i", "t-valance", topbar);
  }
  if (current !== themeId) {
    current = themeId;
    index = Math.floor(Math.random() * list.length);
    layers.forEach((layer) => layer.classList.remove("on"));
    show(list[index]);
  }
  timer = setInterval(next, ROTATE_MS);
}

function next() {
  if (!current || document.hidden) return;
  const list = ART[current];
  index = (index + 1) % list.length;
  show(list[index]);
}

/** Carga el splash y, cuando está, funde a él (la otra capa se apaga a la vez). */
function show([id, at]) {
  const ticket = ++wanted;
  const img = new Image();
  img.decoding = "async";
  img.onload = () => {
    if (ticket !== wanted || !stage) return;
    const [shown, hidden] = layers;
    hidden.style.backgroundImage = `url("${img.src}")`;
    hidden.style.backgroundPosition = at;
    hidden.classList.add("on");
    shown.classList.remove("on");
    layers = [hidden, shown];
  };
  img.src = `${SPLASH}${id}.jpg`;
}
