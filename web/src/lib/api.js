// REST helper: same-origin cookies plus the CSRF header on every state-changing call.
let csrf = null;
let onUnauthorized = () => {};

export function setCsrf(value) {
  csrf = value;
}

export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : detail?.detail || detail?.error || JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export async function api(method, path, body) {
  const headers = {};
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (method !== "GET" && csrf) headers["X-UL-CSRF"] = csrf;
  const r = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  const isJson = (r.headers.get("content-type") || "").includes("json");
  const data = isJson ? await r.json() : await r.arrayBuffer();
  if (r.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/me") onUnauthorized();
  if (!r.ok) throw new ApiError(r.status, isJson ? data.detail ?? data : r.statusText);
  return data;
}

export const get = (p) => api("GET", p);
export const post = (p, b = {}) => api("POST", p, b);
export const put = (p, b = {}) => api("PUT", p, b);
export const del = (p) => api("DELETE", p);

/** Error message for display; FastAPI 422 details can be nested objects. */
export function errText(e) {
  const d = e?.detail;
  if (d && typeof d === "object") {
    if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
    if (d.errors) return d.errors.join("; ");
    if (d.checks) return d.checks.errors.join(", ");
    if (d.detail) return `${d.error ? d.error + ": " : ""}${d.detail}`;
    if (d.error) return d.error;
  }
  return e?.message || String(e);
}
