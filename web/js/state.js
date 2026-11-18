// Estado global: metadatos (campeones, plataformas…) y filtros persistentes.

const STORAGE_KEY = "tempu.filters.v2";
const DEFAULT_FILTERS = { sources: "all", tier: "emerald_plus", platform: "all", patch: "all" };

function load(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : { ...fallback };
  } catch {
    return { ...fallback };
  }
}

export function save(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* almacenamiento no disponible */
  }
}

export function loadJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

const listeners = new Set();

export const state = {
  meta: null,
  opgg: null,
  status: null,
  champions: new Map(), // id -> {id, key, name, tags, info}
  champList: [],
  filters: load(STORAGE_KEY, DEFAULT_FILTERS),
};

export function setFilters(patch) {
  Object.assign(state.filters, patch);
  save(STORAGE_KEY, state.filters);
  listeners.forEach((fn) => fn(state.filters));
}

export function onFilters(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function setMeta(meta) {
  state.meta = meta;
  state.champList = meta.ddragon?.champions || [];
  state.champions = new Map(state.champList.map((c) => [c.id, c]));
}

// ------------------------------------------------------------ Data Dragon
const CDN = "https://ddragon.leagueoflegends.com/cdn";

export const dd = {
  version: () => state.meta?.ddragon?.version || "16.23.1",
  champ: (id) => state.champions.get(Number(id)),
  name: (id) => state.champions.get(Number(id))?.name || (id ? `#${id}` : "—"),
  champIcon: (id) => {
    const c = state.champions.get(Number(id));
    return c ? `${CDN}/${dd.version()}/img/champion/${c.key}.png` : "";
  },
  itemIcon: (id) => (id ? `${CDN}/${dd.version()}/img/item/${id}.png` : ""),
  itemName: (id) => state.meta?.ddragon?.items?.[String(id)] || "",
  spellIcon: (id) => {
    const s = state.meta?.ddragon?.spells?.[String(id)];
    return s ? `${CDN}/${dd.version()}/img/spell/${s.key}.png` : "";
  },
  spellName: (id) => state.meta?.ddragon?.spells?.[String(id)]?.name || "",
  runeIcon: (id) => {
    const r = state.meta?.ddragon?.runes?.[String(id)];
    return r?.icon ? `https://ddragon.leagueoflegends.com/cdn/img/${r.icon}` : "";
  },
  runeName: (id) => state.meta?.ddragon?.runes?.[String(id)]?.name || "",
  profileIcon: (id) => (id != null ? `${CDN}/${dd.version()}/img/profileicon/${id}.png` : ""),
};

export const ROLES = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"];
export const ROLE_LABEL = { TOP: "Top", JUNGLE: "Jungla", MID: "Mid", ADC: "ADC", SUPPORT: "Support" };
export const TIER_LABEL = {
  IRON: "Hierro", BRONZE: "Bronce", SILVER: "Plata", GOLD: "Oro", PLATINUM: "Platino", EMERALD: "Esmeralda",
  DIAMOND: "Diamante", MASTER: "Maestro", GRANDMASTER: "Gran Maestro", CHALLENGER: "Aspirante", UNRANKED: "Sin clasificar",
};

export function platformLabel(id) {
  return state.meta?.platforms?.find((p) => p.id === id)?.label || (id || "").toUpperCase();
}
