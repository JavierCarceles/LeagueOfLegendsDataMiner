// Cliente HTTP de la API con manejo de errores uniforme.
import { state } from "./state.js";

export class ApiError extends Error {
  constructor(status, message, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function buildUrl(path, params = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  return url;
}

async function request(method, path, { params, body, signal } = {}) {
  const res = await fetch(buildUrl(path, params), {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* respuesta sin cuerpo */
  }
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join(", ") : `Error ${res.status}`;
    throw new ApiError(res.status, message, data?.code);
  }
  return data;
}

/** Parámetros de los filtros globales (fuente, elo, región, parche). */
export function filterParams(extra = {}) {
  const f = state.filters;
  return { sources: f.sources, tier: f.tier, platform: f.platform, patch: f.patch, ...extra };
}

export const api = {
  get: (path, params, opts) => request("GET", path, { params, ...opts }),
  post: (path, body, params, opts) => request("POST", path, { body, params, ...opts }),
  patch: (path, body, params, opts) => request("PATCH", path, { body, params, ...opts }),
  del: (path, params, opts) => request("DELETE", path, { params, ...opts }),
};
