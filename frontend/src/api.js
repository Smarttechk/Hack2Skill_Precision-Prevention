// Small wrapper around the Python API (/api). Every page loads its data through these helpers.
import { useEffect, useState } from "react";

// The sign-in lives in an HttpOnly cookie set by the server: page scripts never see the token.
let onSignedOut = () => {};
export const whenSignedOut = (fn) => { onSignedOut = fn; };

const OUT_OF_DATE = "The server is running older code. Restart it (python server.py) and try again.";

// Open mode only (no accounts): look at the app as one doctor or one hospital's admin. The server ignores it once accounts exist.
let previewAs = "";
export const setPreviewAs = (value) => { previewAs = value || ""; };

async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(previewAs ? { "X-Preview": previewAs } : {}) },
    credentials: "same-origin",
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 401 && path !== "/login") onSignedOut();   // session ended (time-out, password change, removed account)
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    if (res.status === 404 && /Unknown API address|Not Found/i.test(detail)) detail = OUT_OF_DATE;
    throw new Error(detail);
  }
  // a web page instead of data: an older server that does not know this address yet
  if (!(res.headers.get("content-type") || "").includes("json")) throw new Error(OUT_OF_DATE);
  return res.json();
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: "POST", body }),
};

// useApi("/doctor/list?...") -> { data, error, loading, reload }
export function useApi(path, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) { setState({ data: null, error: null, loading: false }); return; }
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    api.get(path)
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error) => alive && setState({ data: null, error, loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

export const pct = (x, digits = 0) => (x === null || x === undefined || typeof x !== "number" ? "-" : `${(x * 100).toFixed(digits)}%`);
