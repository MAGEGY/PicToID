const $ = (s) => document.querySelector(s);
let imageId = null;
let pollTimer = null;
let shownWarnings = new Set();

// Optional access key: open the app as ?key=YOURKEY when PICFINDER_KEY is set server-side
const KEY = new URLSearchParams(location.search).get("key") || "";
const api = (p) => KEY ? `${p}${p.includes("?") ? "&" : "?"}key=${encodeURIComponent(KEY)}` : p;

const dz = $("#dropzone");
const fileInput = $("#fileInput");

dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("keydown", (e) => e.key === "Enter" && fileInput.click());
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", (e) => {
  e.preventDefault(); dz.classList.remove("drag");
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => fileInput.files.length && handleFile(fileInput.files[0]));

async function handleFile(file) {
  setStatus("Uploading…");
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(api("/api/upload"), { method: "POST", body: fd });
  if (!res.ok) return setStatus(await errText(res), true);
  const { id, url } = await res.json();
  imageId = id;

  $("#preview").src = api(url);
  $("#preview").hidden = false;
  $("#dropLabel").style.display = "none";
  $("#options").hidden = false;
  $("#results").hidden = false;
  $("#engineList").innerHTML = "";
  $("#foundList").innerHTML = "";
  shownWarnings = new Set();
  $("#descCard").innerHTML = '<span class="muted">generating…</span>';
  $("#metaCard").innerHTML = '<span class="muted">analyzing…</span>';
  setStatus("Analyzing…");

  loadMeta();
  loadDescription();
  startSearch();
}

async function loadMeta() {
  const res = await fetch(api(`/api/analyze/${imageId}`));
  if (!res.ok) { $("#metaCard").textContent = await errText(res); return; }
  const m = await res.json();
  const rows = [
    ["Format", m.format], ["Size", `${m.width}×${m.height} (${m.megapixels} MP)`],
    ["Color mode", m.mode],
  ];
  const exif = m.exif || {};
  for (const [k, v] of Object.entries(exif)) rows.push([k, fmt(v)]);
  if (m.gps?.maps_url)
    rows.push(["GPS", `<a href="${m.gps.maps_url}" target="_blank">${m.gps.latitude}, ${m.gps.longitude}</a>`]);
  else rows.push(["GPS", "not embedded"]);
  if (!m.has_exif) rows.push(["Note", "No EXIF metadata — likely stripped (screenshot, messaging app, or export)."]);

  let html = "<dl class='kv'>" + rows.map(([k, v]) => `<dt>${k}</dt><dd>${v ?? "—"}</dd>`).join("") + "</dl>";
  if (m.dominant_colors?.length)
    html += `<div class="swatches">${m.dominant_colors.map(c => `<div class="swatch" style="background:${c}" title="${c}"></div>`).join("")}</div>`;
  $("#metaCard").innerHTML = html;

  const row = $("#facesRow");
  row.innerHTML = "";
  $("#facesTitle").hidden = !(m.faces || []).length;
  for (const f of m.faces || []) {
    const img = document.createElement("img");
    img.src = api(f.url);
    img.title = "Reverse-search this face";
    img.onclick = () => {
      imageId = f.id;
      $("#engineList").innerHTML = "";
      $("#foundList").innerHTML = "";
      startSearch();
    };
    row.appendChild(img);
  }
}

async function loadDescription() {
  const res = await fetch(api(`/api/describe/${imageId}`));
  const d = await res.json();
  $("#descCard").innerHTML = d.description
    ? `${esc(d.description)}<div class="muted small" style="margin-top:8px">via ${esc(d.model)}</div>`
    : `<span class="muted">${esc(d.error || "No description available.")}</span>`;
}

// ---------- background search ----------

async function startSearch() {
  clearTimeout(pollTimer);
  shownWarnings = new Set();
  $("#engineList").innerHTML = "";
  $("#foundList").innerHTML = "";
  const res = await fetch(api("/api/search"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_id: imageId, publish: $("#publishToggle").checked }),
  });
  if (!res.ok) return setStatus(await errText(res), true);
  const { job_id } = await res.json();
  pollJob(job_id);
}

function pollJob(jobId) {
  fetch(api(`/api/search/${jobId}`))
    .then((r) => r.json())
    .then((job) => {
      renderJob(job);
      const last = (job.steps || []).slice(-1)[0];
      if (job.status === "running") {
        setStatus(`Working… ${last || ""}`);
        pollTimer = setTimeout(() => pollJob(jobId), 1200);
      } else {
        setStatus(job.status === "done" ? "Done." : "Search failed.", job.status === "error");
      }
    })
    .catch(() => { pollTimer = setTimeout(() => pollJob(jobId), 2500); });
}

function renderJob(job) {
  renderEngines({ engines: job.engines, found: job.found });
  (job.warnings || []).forEach((w) => {
    if (!shownWarnings.has(w)) { shownWarnings.add(w); setStatus(w, true, true); }
  });
}

function renderEngines(data) {
  const list = $("#engineList");
  list.innerHTML = "";
  for (const e of data.engines || []) {
    const div = document.createElement("div");
    div.className = "engine";
    div.innerHTML = `<div><div class="name">${e.name}</div><div class="note">${e.note || ""}</div></div>
      <a class="open" href="${e.url}" target="_blank" rel="noopener">Open →</a>`;
    list.appendChild(div);
  }
  if (!list.children.length) list.innerHTML = '<div class="card muted">No engine links yet…</div>';

  const found = $("#foundList");
  found.innerHTML = "";
  for (const r of data.found || []) {
    const div = document.createElement("div");
    div.className = "engine";
    div.innerHTML = `<div><div class="name">${esc(r.host)}</div><div class="note">${esc(r.url)}</div></div>
      <a class="open" href="${esc(r.url)}" target="_blank" rel="noopener">Open →</a>`;
    found.appendChild(div);
  }
  if (!found.children.length && data.engines?.length)
    found.innerHTML = '<div class="card muted">No links extracted yet — check the Lens result page.</div>';
}

$("#searchBtn").addEventListener("click", () => imageId && startSearch());
$("#publishToggle").addEventListener("change", () => imageId && startSearch());

function setStatus(msg, isWarn = false, append = false) {
  const el = $("#status");
  if (!msg && !append) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  const line = document.createElement("div");
  if (isWarn) line.className = "warn";
  line.textContent = msg;
  if (append) el.appendChild(line); else el.innerHTML = "", el.appendChild(line);
}

async function errText(res) {
  try { return (await res.json()).detail || res.statusText; } catch { return res.statusText; }
}
function fmt(v) {
  if (v == null) return "—";
  if (typeof v === "object") return esc(JSON.stringify(v));
  return esc(String(v));
}
function esc(s) {
  return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
