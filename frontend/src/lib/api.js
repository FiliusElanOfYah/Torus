import axios from "axios";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND}/api`;

const SESSION_KEY = "torus.session_id";

export function getSessionId() {
  return localStorage.getItem(SESSION_KEY) || "";
}

function setSessionId(id) {
  localStorage.setItem(SESSION_KEY, id);
}

export async function ensureSession() {
  let id = getSessionId();
  if (id) {
    try {
      const r = await axios.get(`${API}/sessions/${id}`);
      return r.data;
    } catch {
      /* fallthrough: create new */
    }
  }
  const r = await axios.post(`${API}/sessions`);
  setSessionId(r.data.id);
  return r.data;
}

export async function getSession() {
  const id = getSessionId();
  if (!id) return ensureSession();
  const r = await axios.get(`${API}/sessions/${id}`);
  return r.data;
}

export async function updateProfile(profile) {
  const id = getSessionId();
  const r = await axios.patch(`${API}/sessions/${id}/profile`, { profile });
  return r.data;
}

export async function toggleStep(step_id, verified) {
  const id = getSessionId();
  const r = await axios.post(`${API}/sessions/${id}/steps`, { step_id, verified });
  return r.data;
}

export async function toggleOpsec(item_id, checked) {
  const id = getSessionId();
  const r = await axios.post(`${API}/sessions/${id}/opsec`, { item_id, checked });
  return r.data;
}

export async function getPhases() {
  const r = await axios.get(`${API}/phases`);
  return r.data;
}

export async function getPhase(number) {
  const r = await axios.get(`${API}/phases/${number}`);
  return r.data;
}

export async function getDaemons() {
  const r = await axios.get(`${API}/daemons`);
  return r.data;
}

export async function getProgress() {
  const id = getSessionId();
  const r = await axios.get(`${API}/sessions/${id}/progress`);
  return r.data;
}

export function phaseBundleUrl(number) {
  return `${API}/bundle/phase/${number}`;
}

export function fullBundleUrl() {
  return `${API}/bundle/mesh`;
}
