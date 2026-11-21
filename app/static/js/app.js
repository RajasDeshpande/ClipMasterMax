import { Preview, setFonts, setBadWords, chunkWords, cssFont, ASPECTS, unlockAudio, isSwear } from "./preview.js";

// ============================================================ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));
const fmt = (t, dec = 1) => {
  if (t == null || isNaN(t)) return "--:--";
  t = Math.max(0, t); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const ss = s.toFixed(dec).padStart(dec ? 3 + dec : 2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
};
const getPath = (o, p) => p.split(".").reduce((a, k) => a?.[k], o);
const setPath = (o, p, v) => { const ks = p.split("."); let a = o; ks.slice(0, -1).forEach(k => { a[k] ??= {}; a = a[k]; }); a[ks.at(-1)] = v; };
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function api(method, url, body, raw = false) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch(url, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  if (raw) return r;
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.className = "toast", err ? 6000 : 2600);
}
const fail = (e) => toast(e.message || String(e), true);

function modal(html) {
  $("#modalBody").innerHTML = html; $("#modal").classList.remove("hidden");
  return $("#modalBody");
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modalBody").innerHTML = ""; }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal" || e.target.hasAttribute("data-close")) closeModal(); });

// ============================================================ global state
const S = { sys: null, presets: null, settings: null, view: null, cleanup: [] };
// Listen on the page container, and stop listening when the user navigates away
// (otherwise handlers from a previously opened clip keep firing and edit the wrong clip).
function onView(type, fn) {
  const v = $("#view");
  v.addEventListener(type, fn);
  S.cleanup.push(() => v.removeEventListener(type, fn));
}

// ============================================================ jobs
const Jobs = {
  watch: new Map(), // id -> {job, cb}
  add(job, cb) { this.watch.set(job.id, { job, cb }); this.render(); this.tick(); },
  async tick() {
    clearTimeout(this._t);
    for (const [id, w] of this.watch) {
      try {
        const j = await api("GET", `/api/jobs/${id}`);
        w.job = j;
        if (["done", "error", "cancelled"].includes(j.status)) {
          if (!w.finished) {
            w.finished = Date.now();
            if (j.status === "error") toast(`${j.label} failed: ${j.error}`, true);
            try { w.cb?.(j); } catch (e) { console.error(e); }
          }
          if (Date.now() - w.finished > (j.status === "error" ? 15000 : 4000)) this.watch.delete(id);
        }
      } catch { this.watch.delete(id); }
    }
    this.render();
    if (this.watch.size) this._t = setTimeout(() => this.tick(), 700);
  },
  render() {
    $("#jobs").innerHTML = [...this.watch.values()].map(({ job: j }) => `
      <div class="job ${j.status}">
        <div class="t"><span>${j.status === "running" ? '<span class="spin"></span> ' : j.status === "done" ? "✅ " : j.status === "error" ? "⚠️ " : "⏳ "}${esc(j.label)}</span>
        ${["queued", "running"].includes(j.status) ? `<button class="btn xs ghost" data-cancel="${j.id}">Cancel</button>` : ""}</div>
        <div class="progress"><i style="width:${j.progress || 0}%"></i></div>
        <div class="m">${esc(j.status === "error" ? j.error : j.message)}</div>
      </div>`).join("");
  },
  forClip(cid) { return [...this.watch.values()].map(w => w.job).filter(j => j.clip === cid && ["queued", "running"].includes(j.status)); },
};
$("#jobs").addEventListener("click", e => {
  const id = e.target.dataset.cancel;
  if (id) api("POST", `/api/jobs/${id}/cancel`).then(() => Jobs.tick());
});

// ============================================================ boot
async function boot() {
  try {
    [S.presets, S.settings] = await Promise.all([api("GET", "/api/presets"), api("GET", "/api/settings")]);
    setFonts(S.presets.fonts); setBadWords(S.presets.bad_words);
    loadSys();
  } catch (e) { fail(e); }
  window.addEventListener("hashchange", route);
  route();
}
async function loadSys() {
  S.sys = await api("GET", "/api/system");
  const y = S.sys.youtube;
  $("#sys").innerHTML = `
    <span class="chip ${S.sys.cuda ? "ok" : "no"}" title="Speech AI device">${S.sys.cuda ? "⚡ GPU AI" : "CPU AI"} · ${esc(S.sys.whisper_default)}</span>
    <span class="chip ${S.sys.nvenc ? "ok" : "no"}" title="Video encoder">${S.sys.nvenc ? "⚡ NVENC export" : "CPU export"}</span>
    <span class="chip ${y.connected ? "ok" : ""}" title="YouTube upload">${y.connected ? "▶ " + esc(y.channel?.title || "YouTube") : "YouTube not linked"}</span>
    <span class="chip" id="igChip" title="Instagram Reels">📸 …</span>`;
  api("GET", "/api/instagram/status").then(ig => {
    const c = $("#igChip"); if (!c) return;
    c.className = "chip" + (ig.connected ? " ok" : ""); c.textContent = ig.connected ? "📸 @" + (ig.username || "Instagram") : "Instagram not linked";
  }).catch(() => {});
}
async function updReviewBadge() {
  try {
    const n = (await api("GET", "/api/posts")).filter(p => p.status === "review").length;
    const b = $("#reviewCount"); b.textContent = n; b.classList.toggle("hidden", !n);
  } catch {}
}
setInterval(updReviewBadge, 10000); setTimeout(updReviewBadge, 1500);
$("#btnSettings").onclick = () => openSettings();
$("#btnStorage").onclick = () => openStorage();

// ============================================================ STORAGE (manual cleanup only)
const GB = (b) => b >= 1073741824 ? (b / 1073741824).toFixed(2) + " GB" : (b / 1048576).toFixed(b > 104857600 ? 0 : 1) + " MB";
async function openStorage() {
  let R;
  try { R = await api("GET", "/api/storage"); } catch (e) { return fail(e); }
  const T = R.totals;
  const rows = [
    ["uploaded_exports", "✅ Clips already uploaded to YouTube", T.uploaded_exports, "Finished MP4s that are already on your channel. Safest thing to clear."],
    ["exports", "🎬 All exported clips", T.exports, "Every rendered MP4, .srt and thumbnail. Download what you need first. You can re-render any clip later."],
    ["sources", "📼 Imported source videos", T.sources, "The original long gameplay videos — usually the biggest. Clips, captions & settings are kept, but you can't edit or re-render them afterwards."],
    ["work", "🧹 Temporary working files", T.work, "Preview frames and render scratch files. Always safe."],
    ["models", "🧠 AI speech models", T.models, "Needed for captions. Only clear if you're really short on space — they re-download (~3 GB) next time."],
  ];
  const m = modal(`
    <h2>💾 Free up disk space</h2>
    <p class="muted small">ClipMasterMax is using <b style="color:var(--accent)">${GB(R.total)}</b> · ${GB(R.free)} free on this drive. Nothing is ever deleted automatically — you choose here.</p>
    <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">
      ${rows.map(([k, label, b, desc]) => `
        <div class="card" style="padding:12px 14px;display:flex;gap:12px;align-items:center">
          <div class="grow"><div><b>${label}</b> <span class="chip" style="margin-left:6px">${GB(b)}</span></div><div class="muted small">${desc}</div></div>
          <button class="btn sm ${k === "models" || k === "sources" ? "danger" : ""}" data-clear="${k}" ${b ? "" : "disabled"}>Clear</button>
        </div>`).join("")}
    </div>
    ${R.projects.length ? `<h3 style="margin-top:18px">Per project</h3>
    <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px">
      ${R.projects.map(p => `
        <div class="row small" style="background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:8px 10px">
          <span class="grow" style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><b>${esc(p.name)}</b> <span class="muted">· ${p.clips} clips${p.status === "archived" ? " · source cleared" : ""}</span></span>
          <span class="muted">source ${GB(p.source)}</span><button class="btn xs danger" data-pclear="sources" data-p="${p.id}" ${p.source ? "" : "disabled"}>Clear</button>
          <span class="muted">exports ${GB(p.exports)}</span><button class="btn xs danger" data-pclear="exports" data-p="${p.id}" ${p.exports ? "" : "disabled"}>Clear</button>
        </div>`).join("")}
    </div>` : ""}`);
  const msgs = {
    sources: "Delete the imported source videos? Your clips, captions and settings stay, but those projects can't be edited or re-rendered anymore.",
    exports: "Delete exported clip files (MP4, .srt, thumbnails)? Make sure you've downloaded or uploaded what you need. You can re-render later as long as the source video is kept.",
    models: "Delete the AI speech models? They'll download again (~3 GB) the next time you generate captions.",
  };
  m.onclick = async e => {
    const b = e.target.closest("[data-clear],[data-pclear]"); if (!b) return;
    const what = b.dataset.clear || b.dataset.pclear, project = b.dataset.p || null;
    if (msgs[what] && !confirm(msgs[what])) return;
    b.disabled = true; b.innerHTML = '<span class="spin"></span>';
    try {
      const r = await api("POST", "/api/storage/clear", { what, project });
      toast(`Freed ${GB(r.freed)} 🧹`); openStorage();
      if (location.hash.length > 2) route();
    } catch (er) { fail(er); b.disabled = false; b.textContent = "Clear"; }
  };
}

// ============================================================ caption choice (shared by Make / Auto-make / Auto Pilot)
const CAP_LANGS = [["en", "English"], ["hinglish", "Hinglish (bhai kya hua)"], ["hi", "हिंदी (Devanagari)"], ["hi2en", "Hindi → English"], ["auto", "Auto-detect"]];
const CAP_LANG_MAP = { en: ["en", "devanagari"], hi: ["hi", "devanagari"], hinglish: ["hi", "hinglish"], hi2en: ["hi", "english"], auto: ["auto", "devanagari"] };
function capPrefs() {
  try { return { preset: "beast", lang: "en", ...(JSON.parse(localStorage.getItem("cm_cap") || "{}")) }; }
  catch { return { preset: "beast", lang: "en" }; }
}
function saveCapPrefs(p) { try { localStorage.setItem("cm_cap", JSON.stringify(p)); } catch {} }
function capPatch(preset, lang) {
  const out = {};
  const pr = S.presets?.captions?.[preset];
  if (pr) out.style = clone(pr.style);
  const m = CAP_LANG_MAP[lang];
  if (m) out.captions = { language: m[0], hindi_mode: m[1], enabled: true };
  return out;
}
function capChoiceHTML(id) {
  const cp = capPrefs();
  return `<div class="row wrap" style="gap:8px">
    <select class="input" id="${id}Style" style="flex:1;min-width:150px" title="Caption style">${Object.entries(S.presets?.captions || {}).map(([k, p]) => `<option value="${k}" ${k === cp.preset ? "selected" : ""}>🎨 ${esc(p.label)}</option>`).join("")}</select>
    <select class="input" id="${id}Lang" style="flex:1;min-width:150px" title="Caption language">${CAP_LANGS.map(([k, l]) => `<option value="${k}" ${k === cp.lang ? "selected" : ""}>🗣 ${esc(l)}</option>`).join("")}</select>
  </div>`;
}
function bindCapChoice(id) {
  const upd = () => saveCapPrefs({ preset: $(`#${id}Style`).value, lang: $(`#${id}Lang`).value });
  $(`#${id}Style`).onchange = upd; $(`#${id}Lang`).onchange = upd;
}

// ============================================================ AUTO PILOT
async function openAutopilot(P, onDone) {
  const [yt, ig] = await Promise.all([api("GET", "/api/youtube/status").catch(() => ({})), api("GET", "/api/instagram/status").catch(() => ({}))]);
  const now = new Date(Date.now() + 15 * 60000); now.setSeconds(0, 0);
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const m = modal(`
    <h2>🤖 Auto Pilot</h2>
    <p class="muted small">Finds the best moments, captions them, adds game pop-ups, renders, writes titles &amp; hashtags, and posts. Sit back 😎</p>
    <div class="grid2">
      <div><label class="f">How many clips</label><select class="input" id="apCount">${[3, 5, 8, 10].map(n => `<option ${n === 5 ? "selected" : ""}>${n}</option>`).join("")}</select></div>
      <div><label class="f">Clip length</label><select class="input" id="apLen">${[[15, "15s"], [20, "20s"], [30, "30s"], [45, "45s"], [59, "59s"], [90, "90s"]].map(([s, l]) => `<option value="${s}" ${s === 30 ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    </div>
    <label class="f">Captions</label>${capChoiceHTML("ap")}
    <label class="switch" style="margin-top:6px"><span>💥 Game text pop-ups<div class="muted small">Reads the game screen (WASTED, MISSION PASSED, VICTORY…) and pops it up big</div></span><input type="checkbox" id="apGame" checked></label>
    <label class="switch"><span>📈 Progress bar + punch zoom</span><input type="checkbox" id="apFx" checked></label>
    <div class="sep"></div>
    <h3>Post to</h3>
    <label class="switch"><span>▶ YouTube Shorts ${yt.connected ? `<span class="muted small">(${esc(yt.channel?.title || "connected")})</span>` : '<span class="muted small">— not connected</span>'}</span><input type="checkbox" id="apYT" ${yt.connected ? "checked" : "disabled"}></label>
    <label class="switch"><span>📸 Instagram Reels ${ig.connected ? `<span class="muted small">(@${esc(ig.username || "")})</span>` : '<span class="muted small">— not connected</span>'}</span><input type="checkbox" id="apIG" ${ig.connected ? "checked" : "disabled"}></label>
    ${!yt.connected || !ig.connected ? `<p class="small"><a href="#" id="apSettings">Connect accounts in Settings →</a> (you can also just make the clips now and post later)</p>` : ""}
    <label class="switch"><span>👀 Let me review before anything is posted<div class="muted small">Recommended. Approve each post in 📤 Posts. Turn off for fully hands-free.</div></span><input type="checkbox" id="apReview" checked></label>
    <div class="grid2">
      <div><label class="f">When</label><select class="input" id="apWhen"><option value="now">As soon as ready</option><option value="spread">Spread out (1 at a time)</option></select></div>
      <div id="apSpread" class="hidden"><label class="f">Every</label><select class="input" id="apEvery">${[1, 2, 3, 4, 6, 8, 12, 24].map(h => `<option value="${h}" ${h === 4 ? "selected" : ""}>${h} hour${h > 1 ? "s" : ""}</option>`).join("")}</select></div>
    </div>
    <div id="apStartBox" class="hidden"><label class="f">First post at</label><input class="input" type="datetime-local" id="apStart" value="${local}"></div>
    <div class="row" style="margin-top:16px"><button class="btn primary big grow" id="apGo">🚀 Start Auto Pilot</button></div>`);
  bindCapChoice("ap");
  $("#apSettings", m)?.addEventListener("click", e => { e.preventDefault(); closeModal(); openSettings(); });
  $("#apWhen", m).onchange = e => { const s = e.target.value === "spread"; $("#apSpread", m).classList.toggle("hidden", !s); $("#apStartBox", m).classList.toggle("hidden", !s); };
  $("#apGo", m).onclick = async () => {
    const cp = capPrefs(), platforms = [];
    if ($("#apYT", m).checked) platforms.push("youtube");
    if ($("#apIG", m).checked) platforms.push("instagram");
    const spread = $("#apWhen", m).value === "spread";
    const body = {
      count: +$("#apCount", m).value, length: +$("#apLen", m).value, preset: cp.preset, language: cp.lang,
      game_text: $("#apGame", m).checked, platforms, review: $("#apReview", m).checked, privacy: "public",
      every_hours: spread ? +$("#apEvery", m).value : 0,
      start_at: spread ? Math.floor(new Date($("#apStart", m).value).getTime() / 1000) : 0,
      effects: $("#apFx", m).checked ? { progress_bar: true, punch_zoom: true } : {},
    };
    try {
      const j = await api("POST", `/api/projects/${P.id}/autopilot`, body);
      closeModal();
      toast("🤖 Auto Pilot started — you can keep working");
      Jobs.add(j, jj => {
        onDone?.();
        if (jj.status === "done") {
          const r = jj.result;
          toast(r.posts.length ? (r.review ? `✅ ${r.clips.length} clips ready — review them in 📤 Posts` : `✅ ${r.clips.length} clips made, posting now`) : `✅ ${r.clips.length} clips made`);
          if (r.posts.length) location.hash = "#/posts";
        }
      });
    } catch (e) { fail(e); }
  };
}

// ============================================================ POSTS (review queue + history)
const PLAT = { youtube: { icon: "▶", name: "YouTube", cls: "yt" }, instagram: { icon: "📸", name: "Instagram", cls: "ig" } };
const toLocalInput = (sec) => { const d = new Date(sec * 1000); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };
async function viewPosts() {
  const v = $("#view");
  v.innerHTML = `<div class="phead"><a class="btn sm ghost" href="#/">← Projects</a><h2 class="grow">📤 Posts</h2>
    <span class="muted small" id="postAcc"></span></div><div id="postsBox"><div class="empty">Loading…</div></div>`;
  const acc = await Promise.all([api("GET", "/api/youtube/status").catch(() => ({})), api("GET", "/api/instagram/status").catch(() => ({}))]);
  $("#postAcc").innerHTML = `${acc[0].connected ? `<span class="chip ok">▶ ${esc(acc[0].channel?.title || "YouTube")}</span>` : '<span class="chip">▶ YouTube not connected</span>'}
    ${acc[1].connected ? `<span class="chip ok">📸 @${esc(acc[1].username || "")}${acc[1].quota_total ? ` · ${acc[1].quota_used ?? 0}/${acc[1].quota_total} today` : ""}</span>` : '<span class="chip">📸 Instagram not connected</span>'}
    <button class="btn xs" id="postSet">⚙ Accounts</button>`;
  $("#postSet").onclick = () => openSettings();
  const render = async () => {
    let posts;
    try { posts = await api("GET", "/api/posts"); } catch (e) { return fail(e); }
    if (!$("#postsBox")) return;  // navigated away meanwhile
    const groups = [
      ["review", "👀 Waiting for your OK", posts.filter(p => p.status === "review")],
      ["upcoming", "⏳ Queued & scheduled", posts.filter(p => ["queued", "scheduled", "posting"].includes(p.status))],
      ["failed", "⚠️ Failed", posts.filter(p => p.status === "failed")],
      ["posted", "✅ Posted", posts.filter(p => p.status === "posted")],
    ];
    if (!posts.length) {
      $("#postsBox").innerHTML = `<div class="empty">Nothing here yet.<br>Open a project and press <b>🤖 Auto Pilot</b>, or post a rendered clip from its <b>🚀 Export</b> tab.</div>`;
      return;
    }
    $("#postsBox").innerHTML = groups.filter(g => g[2].length).map(([k, title, list]) => `
      <h3 style="margin:18px 0 8px">${title} <span class="muted small">(${list.length})</span></h3>
      <div class="posts">${list.map(p => postCard(p)).join("")}</div>`).join("");
  };
  function postCard(p) {
    const pl = PLAT[p.platform];
    const editable = ["review", "failed", "queued", "scheduled"].includes(p.status);
    const when = p.when ? new Date(p.when * 1000).toLocaleString() : "as soon as approved";
    const body = p.platform === "instagram"
      ? `<label class="f">Reel caption</label><textarea class="input" data-f="caption" rows="5" ${editable ? "" : "disabled"}>${esc(p.caption)}</textarea>`
      : `<label class="f">Title <span class="muted">${(p.title || "").length}/100</span></label><input class="input" data-f="title" maxlength="100" value="${esc(p.title)}" ${editable ? "" : "disabled"}>
         <details><summary class="muted small" style="cursor:pointer;margin-top:6px">Description &amp; tags</summary>
         <textarea class="input" data-f="description" rows="4" ${editable ? "" : "disabled"}>${esc(p.description)}</textarea>
         <input class="input" data-f="tags" style="margin-top:6px" value="${esc((p.tags || []).join(", "))}" ${editable ? "" : "disabled"}></details>`;
    return `<div class="post card" data-id="${p.id}">
      <div class="pthumb" style="background-image:url('/api/projects/${p.project}/clips/${p.clip}/thumb')"><span class="plat ${pl.cls}">${pl.icon} ${pl.name}</span></div>
      <div class="pbody">
        <div class="row"><b class="grow" style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.clip_title)}</b>
          <span class="muted small">${esc(p.project_name || "")}</span></div>
        ${body}
        <div class="row wrap" style="margin-top:10px;gap:6px">
          ${p.status === "posted" ? `<a class="btn sm primary" target="_blank" href="${esc(p.result?.url || "#")}">Open ↗</a>
              ${p.result?.studio ? `<a class="btn sm" target="_blank" href="${esc(p.result.studio)}">YouTube Studio ↗</a>` : ""}
              <span class="muted small">${p.result?.publish_at ? "goes public " + new Date(p.result.publish_at).toLocaleString() : (p.result?.privacy ? "· " + esc(p.result.privacy) : "")}</span>`
            : p.status === "posting" ? `<span class="spin"></span><span class="muted small">Posting…</span>`
            : `<button class="btn sm pink" data-act="now">🚀 ${p.status === "failed" ? "Retry now" : "Post now"}</button>
               <input class="input" type="datetime-local" data-f="when" style="width:auto" value="${p.when ? toLocalInput(p.when) : ""}" title="Schedule">
               <button class="btn sm" data-act="schedule">🗓 Schedule</button>
               ${p.rendered ? `<a class="btn sm" target="_blank" href="/api/projects/${p.project}/clips/${p.clip}/output">▶ Watch</a>` : ""}
               <a class="btn sm" href="#/p/${p.project}/c/${p.clip}">✏️ Edit clip</a>
               <span class="grow"></span><button class="btn sm danger" data-act="del">✕</button>`}
        </div>
        ${p.status === "scheduled" ? `<div class="muted small" style="margin-top:6px">🗓 ${esc(when)}${p.platform === "instagram" ? " — keep ClipMasterMax running at that time" : " — YouTube publishes it even if your PC is off"}</div>` : ""}
        ${p.status === "queued" ? `<div class="muted small" style="margin-top:6px">⏳ In the queue…</div>` : ""}
        ${p.error ? `<div class="note warn" style="margin-top:8px">${esc(p.error)}</div>` : ""}
      </div></div>`;
  }
  const box = $("#postsBox");
  let alive = true;
  S.cleanup.push(() => { alive = false; });
  // one pending save per post+field (so quick edits to different fields/cards are all kept)
  const pending = new Map();
  const queueSave = (id, f, val) => {
    const key = id + "|" + f, old = pending.get(key);
    if (old) clearTimeout(old.timer);
    const entry = { id, patch: { [f]: val } };
    entry.timer = setTimeout(() => flush(key), 500);
    pending.set(key, entry);
  };
  async function flush(key) {
    const e = pending.get(key); if (!e) return;
    clearTimeout(e.timer); pending.delete(key);
    try { await api("PATCH", `/api/posts/${e.id}`, e.patch); } catch (er) { fail(er); }
  }
  const flushPost = (id) => Promise.all([...pending.keys()].filter(k => k.startsWith(id + "|")).map(flush));
  box.oninput = e => {
    const card = e.target.closest(".post"), f = e.target.dataset.f;
    if (!card || !f || f === "when") return;
    const val = f === "tags" ? e.target.value.split(",").map(s => s.trim()).filter(Boolean) : e.target.value;
    queueSave(card.dataset.id, f, val);
  };
  box.onclick = async e => {
    const b = e.target.closest("[data-act]"); if (!b || b.disabled) return;
    const card = b.closest(".post"), id = card.dataset.id;
    const btns = $$("[data-act]", card);
    btns.forEach(x => x.disabled = true);
    try {
      await flushPost(id);  // never post old text
      if (b.dataset.act === "del") { if (!confirm("Remove this post from the queue? (The video file stays.)")) return; await api("DELETE", `/api/posts/${id}`); }
      if (b.dataset.act === "now") { await api("POST", `/api/posts/${id}/approve`, { now: true }); toast("Posting… 🚀"); }
      if (b.dataset.act === "schedule") {
        const v2 = $('[data-f="when"]', card).value; if (!v2) return toast("Pick a date & time first", true);
        const when = Math.floor(new Date(v2).getTime() / 1000);
        if (when < Date.now() / 1000 + 60) return toast("Pick a time in the future", true);
        await api("POST", `/api/posts/${id}/approve`, { when }); toast("Scheduled 🗓");
      }
      await render();
    } catch (er) { fail(er); }
    finally { btns.forEach(x => { if (x.isConnected) x.disabled = false; }); }
  };
  await render();
  if (!alive) return;   // user already left the page
  const iv = setInterval(() => { if (alive && document.body.contains(box) && !pending.size && !box.querySelector("input:focus, textarea:focus")) render(); }, 5000);
  S.cleanup.push(() => clearInterval(iv));
}

function route() {
  S.cleanup.forEach(f => f()); S.cleanup = [];
  const h = location.hash.replace(/^#\/?/, "").split("/");
  if (h[0] === "posts") return viewPosts();
  if (h[0] === "p" && h[1] && h[2] === "c" && h[3]) return viewEditor(h[1], h[3]);
  if (h[0] === "p" && h[1]) return viewProject(h[1]);
  return viewHome();
}

const gameOptions = (sel) => Object.entries(S.presets?.games || {}).map(([k, g]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(g.name)}</option>`).join("");

// ============================================================ HOME
async function viewHome() {
  const v = $("#view");
  v.innerHTML = `
  <section class="hero">
    <div class="hero-text">
      <h1>Turn gameplay into <span>viral clips</span> in minutes.</h1>
      <p>Drop a long gameplay video or stream. The AI finds the hype moments, adds word-perfect English &amp; Hindi captions, reframes it for Shorts/Reels and exports it upload-ready.</p>
      <div class="feat">
        <span class="chip accent">🔥 Auto hype detection</span><span class="chip accent">🎯 Word-level captions</span>
        <span class="chip pink">🇮🇳 Hindi · Hinglish · English</span><span class="chip">📱 9:16 · 1:1 · 16:9</span>
        <span class="chip">🎥 Facecam split</span><span class="chip">💥 Punch zoom</span><span class="chip">🎵 Music ducking</span>
        <span class="chip">🤬 Swear bleep</span><span class="chip">▶ YouTube upload</span>
      </div>
    </div>
    <div class="card">
      <h3>New project</h3>
      <label class="f">Game</label>
      <select class="input" id="game">${gameOptions("gta6")}</select>
      <label class="f">Upload your gameplay</label>
      <div class="drop" id="drop"><div class="ico">⬆️</div><b>Drop video here</b> or click to browse<div class="muted small">MP4, MOV, MKV, WEBM · any length</div></div>
      <input type="file" id="file" accept="video/*,.mkv" hidden>
      <div id="upProg" class="hidden" style="margin-top:10px"><div class="row small"><span class="grow" id="upTxt">Uploading…</span></div><div class="progress"><i id="upBar"></i></div></div>
      <div class="or">or paste a link</div>
      <div class="row"><input class="input grow" id="url" placeholder="https://youtube.com/watch?v=…  (your own videos / streams)"><button class="btn primary" id="go">Import</button></div>
      <p class="muted small" style="margin:8px 0 0">Only import videos you own or have permission to re-use. Reuploading other creators' videos gets copyright claims and is not monetizable.</p>
    </div>
  </section>
  <div class="row" style="margin-bottom:12px"><h2 class="grow">Your projects</h2><button class="btn sm ghost" id="openExports">📁 Open exports folder</button></div>
  <div class="pgrid" id="plist"><div class="empty">Loading…</div></div>`;

  const drop = $("#drop"), file = $("#file");
  drop.onclick = () => file.click();
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); };
  file.onchange = () => file.files[0] && upload(file.files[0]);
  $("#go").onclick = async () => {
    const url = $("#url").value.trim(); if (!url) return toast("Paste a link first", true);
    try {
      const r = await api("POST", "/api/projects/url", { url, game: $("#game").value });
      Jobs.add(r.job); location.hash = `#/p/${r.project.id}`;
    } catch (e) { fail(e); }
  };
  // NB: must not return false — an on* handler returning false blocks the key (that broke Ctrl+V / typing)
  $("#url").onkeydown = e => { if (e.key === "Enter") $("#go").click(); };
  $("#openExports").onclick = () => api("POST", "/api/open-folder", {}).catch(fail);

  function upload(f) {
    const fd = new FormData(); fd.append("file", f); fd.append("name", f.name.replace(/\.[^.]+$/, "")); fd.append("game", $("#game").value);
    const x = new XMLHttpRequest(); x.open("POST", "/api/projects/upload");
    $("#upProg").classList.remove("hidden");
    x.upload.onprogress = e => { if (e.lengthComputable) { const p = e.loaded / e.total * 100; $("#upBar").style.width = p + "%"; $("#upTxt").textContent = `Uploading ${f.name} — ${p.toFixed(0)}%`; } };
    x.onload = () => {
      if (x.status >= 300) { let m = x.statusText; try { m = JSON.parse(x.responseText).detail; } catch {} $("#upProg").classList.add("hidden"); return toast(m, true); }
      const r = JSON.parse(x.responseText); Jobs.add(r.job); location.hash = `#/p/${r.project.id}`;
    };
    x.onerror = () => toast("Upload failed", true);
    x.send(fd);
  }

  try {
    const list = await api("GET", "/api/projects");
    $("#plist").innerHTML = list.length ? list.map(p => `
      <div class="pcard" data-id="${p.id}">
        <div class="th" style="background-image:url('/api/projects/${p.id}/thumb')"><span class="dur">${fmt(p.duration, 0)}</span></div>
        <div class="bd"><div class="nm">${esc(p.name)}</div>
        <div class="muted small">${esc(S.presets.games[p.game]?.name || p.game)} · ${p.clips} clips · ${p.status === "ready" ? "ready" : esc(p.status)}</div></div>
        <button class="btn xs danger del" data-del="${p.id}">Delete</button>
      </div>`).join("") : `<div class="empty">No projects yet — drop your first gameplay video above 🎮</div>`;
    $("#plist").onclick = async e => {
      const del = e.target.dataset.del;
      if (del) {
        e.stopPropagation();
        if (!confirm("Delete this project and its clips? (Exported files in the exports folder are kept.)")) return;
        await api("DELETE", `/api/projects/${del}`).catch(fail); return viewHome();
      }
      const c = e.target.closest(".pcard"); if (c) location.hash = `#/p/${c.dataset.id}`;
    };
  } catch (e) { fail(e); }
}

// ============================================================ PROJECT
async function viewProject(pid) {
  const v = $("#view");
  let P;
  try { P = await api("GET", `/api/projects/${pid}`); } catch (e) { v.innerHTML = `<div class="empty">Project not found. <a href="#/">Back</a></div>`; return; }

  if (P.status === "archived") {
    v.innerHTML = `<div class="phead"><a class="btn sm ghost" href="#/">← Projects</a><h2 class="grow">${esc(P.name)}</h2></div>
      <div class="note warn" style="margin-bottom:14px">📦 The source video of this project was deleted to free disk space. Clips, captions and YouTube links are kept, but clips can't be edited or re-rendered. Import the video again to make new clips.</div>
      <div class="clips" style="max-height:none">${P.clips.map((c, i) => `<div class="card" style="padding:12px">
        <b>#${i + 1} ${esc(c.title || c.meta?.title || "Clip")}</b> <span class="muted small">${fmt(c.start)} → ${fmt(c.end)}</span>
        ${c.upload ? ` · <a target="_blank" href="${c.upload.url}">${c.upload.url}</a>` : ""}
        ${c.render ? ` · <a href="/api/projects/${pid}/clips/${c.id}/output?download=1">⬇ MP4</a>` : ""}
        ${c.words ? `<div class="muted small" style="margin-top:4px">${esc(c.words.map(w => w.w).join(" ").slice(0, 220))}…</div>` : ""}</div>`).join("") || '<div class="empty">No clips.</div>'}</div>`;
    return;
  }
  if (P.status !== "ready") {
    v.innerHTML = `<div class="card" style="max-width:560px;margin:60px auto;text-align:center">
      <h2>${P.status === "error" ? "⚠️ Import failed" : "Preparing your video…"}</h2>
      <p class="muted">${esc(P.status === "error" ? P.status_msg : (P.status_msg || "Downloading / analyzing. Hype moments are detected automatically."))}</p>
      ${P.status === "error" ? `<a class="btn" href="#/">Back</a>` : `<div class="progress" style="margin-top:14px"><i id="impBar" style="width:5%"></i></div><p class="muted small" id="impMsg"></p>`}
    </div>`;
    if (P.status !== "error") {
      const iv = setInterval(async () => {
        try {
          const js = await api("GET", `/api/jobs?project=${pid}`);
          const j = js.find(x => x.kind === "import");
          if (j) { $("#impBar") && ($("#impBar").style.width = j.progress + "%"); $("#impMsg") && ($("#impMsg").textContent = j.message); }
          const p2 = await api("GET", `/api/projects/${pid}`);
          if (p2.status !== P.status || p2.status === "ready" || p2.status === "error") { if (p2.status === "ready" || p2.status === "error") { clearInterval(iv); viewProject(pid); } }
        } catch {}
      }, 1200);
      S.cleanup.push(() => clearInterval(iv));
    }
    return;
  }

  const dur = P.source.duration;
  let sel = null, mIn = null, activeClip = null, stopAt = null;
  const checked = new Set();

  v.innerHTML = `
  <div class="phead">
    <a class="btn sm ghost" href="#/">← Projects</a>
    <input class="title-in" id="pname" value="${esc(P.name)}">
    <select class="input" id="pgame" style="width:auto">${gameOptions(P.game)}</select>
    <span class="chip">${P.source.width}×${P.source.height} · ${Math.round(P.source.fps)}fps · ${fmt(dur, 0)}</span>
  </div>
  <div class="pgrid2">
    <div>
      <div class="player">
        <video id="vid" src="/api/projects/${pid}/media" preload="auto"></video>
        <div class="timeline" id="tl"><canvas id="tlc"></canvas></div>
        <div class="transport">
          <button class="btn sm" id="pp">▶ Play</button>
          <span class="tc" id="tc">0:00.0 / ${fmt(dur)}</span>
          <span class="grow"></span>
          <button class="btn sm" id="mIn" title="Mark in (I)">[ In <span class="kbd">I</span></button>
          <button class="btn sm" id="mOut" title="Mark out (O)">Out ] <span class="kbd">O</span></button>
          <button class="btn sm primary" id="addSel" disabled>＋ Add clip</button>
        </div>
      </div>
      <p class="muted small" style="margin:8px 4px">The glowing curve is the AI's hype meter (loudness spikes, gunfire, explosions, screaming). Drag on it to select a range, click to jump. <span class="kbd">Space</span> play · <span class="kbd">←</span><span class="kbd">→</span> seek 2s</p>
    </div>
    <div>
      <div class="card" style="margin-bottom:12px">
        <div class="row wrap">
          <h3 class="grow">🔥 AI Highlights</h3>
          <select class="input" id="hlLen" style="width:auto">
            ${[[15, "15s"], [20, "20s"], [30, "30s"], [45, "45s"], [59, "59s (Shorts max)"], [90, "90s"], [180, "3 min"]].map(([s, l]) => `<option value="${s}" ${s === 30 ? "selected" : ""}>${l}</option>`).join("")}
          </select>
          <select class="input" id="hlCount" style="width:auto">${[5, 8, 12, 20].map(n => `<option ${n === 8 ? "selected" : ""}>${n}</option>`).join("")}</select>
          <button class="btn primary sm" id="findHl">Find</button>
        </div>
        <div class="row wrap" style="margin-top:10px">
          <button class="btn sm" id="deep" title="Transcribes the whole video so highlights also use what was said (bro, no way, bhai…). Makes captions instant afterwards.">🧠 ${P.has_transcript ? "Speech scanned ✓" : "Deep scan speech"}</button>
          <span class="muted small grow">${P.has_transcript ? "Highlights use voice reactions too." : "Optional: smarter picks using voice reactions."}</span>
        </div>
        <div class="sep" style="margin:12px 0"></div>
        <div class="small muted" style="font-weight:700;margin-bottom:6px">💬 Captions for ⚡ Make &amp; Auto-make</div>
        ${capChoiceHTML("pc")}
        <button class="btn pink full" id="autoPilot" style="margin-top:12px">🤖 Auto Pilot: clip, caption, render &amp; post</button>
      </div>
      <div class="row" style="margin-bottom:8px">
        <label class="row small muted"><input type="checkbox" class="ck" id="ckAll"> Select all</label>
        <span class="grow"></span>
        <button class="btn sm pink" id="makeSel" disabled>⚡ Auto-make selected</button>
      </div>
      <div class="clips" id="clips"></div>
    </div>
  </div>`;

  const vid = $("#vid"), tl = $("#tl"), tlc = $("#tlc"), ctx = tlc.getContext("2d");
  $("#pname").onchange = e => api("PATCH", `/api/projects/${pid}`, { name: e.target.value }).catch(fail);
  $("#pgame").onchange = e => api("PATCH", `/api/projects/${pid}`, { game: e.target.value }).then(r => P.game = r.game).catch(fail);

  // ---- timeline
  function drawTL() {
    const dpr = devicePixelRatio || 1, w = tl.clientWidth, h = tl.clientHeight;
    if (tlc.width !== Math.round(w * dpr)) { tlc.width = Math.round(w * dpr); tlc.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    const X = t => t / dur * w;
    // clips
    P.clips.forEach((c, i) => {
      ctx.fillStyle = c.id === activeClip ? "rgba(198,255,61,.22)" : "rgba(46,230,255,.10)";
      ctx.fillRect(X(c.start), 0, Math.max(2, X(c.end) - X(c.start)), h);
      ctx.fillStyle = c.id === activeClip ? "#c6ff3d" : "rgba(46,230,255,.6)";
      ctx.fillRect(X(c.start), 0, Math.max(2, X(c.end) - X(c.start)), 3);
      ctx.font = "bold 10px Segoe UI"; ctx.fillText(String(i + 1), X(c.start) + 3, 14);
    });
    // curve
    const cv = P.curve || [];
    if (cv.length) {
      const g = ctx.createLinearGradient(0, h, 0, 0); g.addColorStop(0, "rgba(46,230,255,.15)"); g.addColorStop(.6, "rgba(198,255,61,.55)"); g.addColorStop(1, "rgba(255,46,136,.9)");
      ctx.beginPath(); ctx.moveTo(0, h);
      cv.forEach((y, i) => ctx.lineTo(i / (cv.length - 1) * w, h - 4 - y * (h - 20)));
      ctx.lineTo(w, h); ctx.closePath(); ctx.fillStyle = g; ctx.fill();
    }
    if (sel) { ctx.fillStyle = "rgba(255,255,255,.18)"; ctx.fillRect(X(sel[0]), 0, X(sel[1]) - X(sel[0]), h); ctx.strokeStyle = "#fff"; ctx.strokeRect(X(sel[0]) + .5, .5, X(sel[1]) - X(sel[0]), h - 1); }
    if (mIn != null) { ctx.fillStyle = "#ffb020"; ctx.fillRect(X(mIn) - 1, 0, 2, h); }
    ctx.fillStyle = "#fff"; ctx.fillRect(X(vid.currentTime) - 1, 0, 2, h);
  }
  let drag = null;
  const tAt = e => Math.max(0, Math.min(dur, (e.clientX - tl.getBoundingClientRect().left) / tl.clientWidth * dur));
  tl.onmousedown = e => { drag = { t0: tAt(e), x0: e.clientX }; };
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
  function onMove(e) { if (!drag) return; if (Math.abs(e.clientX - drag.x0) > 4) { const t = tAt(e); sel = [Math.min(drag.t0, t), Math.max(drag.t0, t)]; updSel(); drawTL(); } }
  function onUp(e) { if (!drag) return; if (Math.abs(e.clientX - drag.x0) <= 4) { vid.currentTime = tAt(e); stopAt = null; } drag = null; drawTL(); }
  S.cleanup.push(() => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); });
  function updSel() { $("#addSel").disabled = !(sel && sel[1] - sel[0] >= 1); $("#addSel").textContent = sel ? `＋ Add clip (${(sel[1] - sel[0]).toFixed(1)}s)` : "＋ Add clip"; }

  // ---- transport
  const pp = $("#pp");
  pp.onclick = () => vid.paused ? vid.play() : vid.pause();
  vid.onplay = () => pp.textContent = "⏸ Pause"; vid.onpause = () => pp.textContent = "▶ Play";
  let raf;
  const loop = () => { $("#tc").textContent = `${fmt(vid.currentTime)} / ${fmt(dur)}`; if (stopAt != null && vid.currentTime >= stopAt) { vid.pause(); stopAt = null; } drawTL(); raf = requestAnimationFrame(loop); };
  loop(); S.cleanup.push(() => cancelAnimationFrame(raf));
  window.addEventListener("resize", drawTL); S.cleanup.push(() => window.removeEventListener("resize", drawTL));
  $("#mIn").onclick = () => { mIn = vid.currentTime; sel = null; updSel(); toast(`In point ${fmt(mIn)}`); };
  $("#mOut").onclick = () => { if (mIn == null) return toast("Set the In point first (I)", true); const t = vid.currentTime; sel = [Math.min(mIn, t), Math.max(mIn, t)]; mIn = null; updSel(); };
  $("#addSel").onclick = async () => {
    if (!sel) return;
    try { const c = await api("POST", `/api/projects/${pid}/clips`, { start: sel[0], end: sel[1] }); sel = null; updSel(); await reload(); toast("Clip added"); activeClip = c.id; }
    catch (e) { fail(e); }
  };
  const onKey = e => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.code === "Space") { e.preventDefault(); pp.click(); }
    else if (e.key === "i" || e.key === "I") $("#mIn").click();
    else if (e.key === "o" || e.key === "O") $("#mOut").click();
    else if (e.key === "ArrowLeft") vid.currentTime = Math.max(0, vid.currentTime - 2);
    else if (e.key === "ArrowRight") vid.currentTime = Math.min(dur, vid.currentTime + 2);
  };
  window.addEventListener("keydown", onKey); S.cleanup.push(() => window.removeEventListener("keydown", onKey));

  // ---- highlights
  $("#findHl").onclick = async () => {
    const b = $("#findHl"); b.disabled = true; b.innerHTML = '<span class="spin"></span>';
    try { const r = await api("POST", `/api/projects/${pid}/highlights`, { length: +$("#hlLen").value, count: +$("#hlCount").value, replace: true }); P = r.project; renderClips(); drawTL(); toast(`Found ${r.added} hype moments 🔥`); }
    catch (e) { fail(e); } finally { b.disabled = false; b.textContent = "Find"; }
  };
  $("#deep").onclick = async () => {
    if (P.has_transcript && !confirm("Speech already scanned. Scan again?")) return;
    try { const j = await api("POST", `/api/projects/${pid}/deepscan`, {}); Jobs.add(j, async jj => { if (jj.status === "done") { toast("Speech scanned — click Find to re-rank highlights"); viewProject(pid); } }); }
    catch (e) { fail(e); }
  };

  // ---- clips list
  async function reload() { P = await api("GET", `/api/projects/${pid}`); renderClips(); drawTL(); }
  function renderClips() {
    const box = $("#clips");
    if (!P.clips.length) { box.innerHTML = `<div class="empty">No clips yet. Click <b>Find</b> for AI picks, or drag on the timeline.</div>`; return; }
    box.innerHTML = P.clips.map((c, i) => {
      const sc = c.score ?? null, cls = sc == null ? "" : sc >= 70 ? "" : sc >= 45 ? "mid" : "low";
      return `<div class="ccard ${c.id === activeClip ? "active" : ""}" data-id="${c.id}">
        <input type="checkbox" class="ck" data-ck="${c.id}" ${checked.has(c.id) ? "checked" : ""}>
        <div class="th" data-play="${c.id}" style="background-image:url('/api/projects/${pid}/clips/${c.id}/thumb')">${sc != null ? `<span class="score ${cls}">${sc}</span>` : ""}</div>
        <div style="min-width:0">
          <div class="nm">#${i + 1} ${esc(c.title || (c.reasons?.[0] ? c.reasons[0] : "Clip"))}</div>
          <div class="meta">${fmt(c.start)} → ${fmt(c.end)} · ${(c.end - c.start).toFixed(1)}s
            <span class="status-dots"><i class="${c.words ? "on" : ""}">CC</i><i class="${c.render ? "on" : ""}">MP4</i><i class="${c.upload ? "on" : ""}">YT</i></span></div>
          ${c.reasons?.length ? `<div class="tags">${c.reasons.map(r => `<span>${esc(r)}</span>`).join("")}</div>` : ""}
          <div class="acts">
            <button class="btn xs" data-play="${c.id}">▶ Watch</button>
            <a class="btn xs primary" href="#/p/${pid}/c/${c.id}">✏️ Edit</a>
            <button class="btn xs" data-quick="${c.id}" title="Captions + render with your default style">⚡ Make</button>
            ${c.render ? `<a class="btn xs" href="/api/projects/${pid}/clips/${c.id}/output?download=1">⬇</a>` : ""}
            <button class="btn xs danger" data-del="${c.id}">✕</button>
          </div>
        </div></div>`;
    }).join("");
    updMake();
  }
  function updMake() { $("#makeSel").disabled = !checked.size; $("#makeSel").textContent = `⚡ Auto-make ${checked.size || ""} selected`; }
  $("#clips").onclick = async e => {
    const d = e.target.dataset;
    if (d.ck) { e.target.checked ? checked.add(d.ck) : checked.delete(d.ck); updMake(); return; }
    if (d.play) { const c = P.clips.find(x => x.id === d.play); activeClip = c.id; vid.currentTime = c.start; stopAt = c.end; vid.play(); renderClips(); return; }
    if (d.del) { await api("DELETE", `/api/projects/${pid}/clips/${d.del}`).catch(fail); checked.delete(d.del); return reload(); }
    if (d.quick) return makeClips([d.quick]);
  };
  $("#ckAll").onchange = e => { P.clips.forEach(c => e.target.checked ? checked.add(c.id) : checked.delete(c.id)); renderClips(); };
  $("#makeSel").onclick = () => makeClips([...checked]);
  bindCapChoice("pc");
  $("#autoPilot").onclick = () => openAutopilot(P, () => reload());
  async function makeClips(ids) {
    try {
      const cp = capPrefs();
      const r = await api("POST", `/api/projects/${pid}/batch`, { clips: ids, captions: true, settings: capPatch(cp.preset, cp.lang) });
      r.jobs.forEach(j => Jobs.add(j, jj => { if (jj.status === "done" && jj.kind === "render") reload(); }));
      toast(`Making ${ids.length} clip${ids.length > 1 ? "s" : ""}… you can keep working`);
    } catch (e) { fail(e); }
  }
  renderClips(); drawTL();
}

// ============================================================ EDITOR
const F = {
  range: (k, label, min, max, step, unit = "") => `<label class="f">${label}</label><div class="range"><input type="range" data-k="${k}" min="${min}" max="${max}" step="${step}"><span class="val" data-v="${k}" data-unit="${unit}"></span></div>`,
  sw: (k, label, hint = "") => `<label class="switch"><span>${label}${hint ? `<div class="muted small">${hint}</div>` : ""}</span><input type="checkbox" data-k="${k}"></label>`,
  sel: (k, label, opts) => `<label class="f">${label}</label><select class="input" data-k="${k}">${opts.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select>`,
  color: (k, label) => `<label><input type="color" data-k="${k}">${label}</label>`,
  seg: (k, opts) => `<div class="seg" data-seg="${k}">${opts.map(([v, l]) => `<button data-val="${v}">${l}</button>`).join("")}</div>`,
  text: (k, label, ph = "") => `<label class="f">${label}</label><input class="input" data-k="${k}" placeholder="${esc(ph)}">`,
};

// sound effects (served by /api/sfx/{name}; names come from /api/presets "sounds")
const SFX_LIST = [["boom", "💥 Boom"], ["horn", "📯 Air horn"], ["whoosh", "💨 Whoosh"], ["ding", "🔔 Ding"], ["bruh", "😐 Bruh"],
  ["dolphin", "🐬 Dolphin"], ["beep", "📢 Beep"], ["high", "📢 High beep"]];
const SFX_COLOR = { boom: "#ff8a3d", horn: "#ffd23d", whoosh: "#2ee6ff", ding: "#c6ff3d", bruh: "#b78bff", dolphin: "#3dd6ff", beep: "#ff2e88", high: "#ff6ec7" };
function sfxList() {
  const avail = S.presets?.sounds, known = new Set(SFX_LIST.map(x => x[0]));
  const extra = Object.entries(avail || {}).filter(([k]) => !known.has(k)).map(([k, l]) => [k, "🔊 " + l]);
  return [...SFX_LIST.filter(([k]) => !avail || k in avail), ...extra];
}
const sfxLabel = (k) => (sfxList().find(x => x[0] === k) || [k, k === "mute" ? "🔇 Mute only" : k])[1];

async function viewEditor(pid, cid) {
  const v = $("#view");
  let P;
  try { P = await api("GET", `/api/projects/${pid}`); } catch (e) { v.innerHTML = `<div class="empty">Not found. <a href="#/">Back</a></div>`; return; }
  let C = P.clips.find(c => c.id === cid);
  if (!C) { location.hash = `#/p/${pid}`; return; }
  const idx = P.clips.indexOf(C);
  let st = clone(C.settings), words = clone(C.words || []);
  let selWord = -1;
  const pres = S.presets;
  // older clips: fill in sound-effect defaults
  st.effects ??= {};
  st.effects.bleep_sound ??= "beep";
  st.effects.hype_sfx ??= "none";
  if (st.effects.sfx_volume == null || isNaN(+st.effects.sfx_volume)) st.effects.sfx_volume = 0.8;
  if (!Array.isArray(st.effects.sfx_marks)) st.effects.sfx_marks = [];
  st.effects.sfx_marks = st.effects.sfx_marks.filter(m => m && m.sound && !isNaN(+m.t)).sort((a, b) => a.t - b.t);
  const bleepOpts = [...sfxList().filter(([k]) => k === "beep" || k === "high"), ...sfxList().filter(([k]) => k !== "beep" && k !== "high"), ["mute", "🔇 Mute only"]];

  v.innerHTML = `
  <div class="phead">
    <a class="btn sm ghost" href="#/p/${pid}">← ${esc(P.name).slice(0, 40)}</a>
    <input class="title-in" id="ctitle" value="${esc(C.title)}" placeholder="Clip title (used for file name & YouTube)">
    <button class="btn sm" id="prevC" ${idx <= 0 ? "disabled" : ""}>‹ Prev</button>
    <span class="muted small">Clip ${idx + 1}/${P.clips.length}</span>
    <button class="btn sm" id="nextC" ${idx >= P.clips.length - 1 ? "disabled" : ""}>Next ›</button>
  </div>
  <div class="egrid">
    <div class="stage-wrap">
      <div class="stage" id="stage"><canvas id="cv"></canvas><div class="playov" id="playov"><span>▶</span></div></div>
      <video id="ev" src="/api/projects/${pid}/media" preload="auto" style="display:none" playsinline></video>
      <div class="scrub" id="scrub"><canvas id="scv"></canvas></div>
      <div class="trim">
        <div class="box"><div class="lbl"><span>START</span><span><button class="btn xs" data-nudge="start,-0.1">−.1</button> <button class="btn xs" data-nudge="start,0.1">+.1</button> <button class="btn xs" data-set="start" title="Set to playhead">⤓</button></span></div><div class="v" id="tStart"></div></div>
        <div class="box"><div class="lbl"><span>END</span><span><button class="btn xs" data-nudge="end,-0.1">−.1</button> <button class="btn xs" data-nudge="end,0.1">+.1</button> <button class="btn xs" data-set="end" title="Set to playhead">⤓</button></span></div><div class="v" id="tEnd"></div></div>
      </div>
      <div class="cutbox">
        <div class="row"><b>✂ Cut out parts</b><span class="muted small grow" id="cutInfo" style="text-align:right"></span></div>
        <div class="row wrap" style="gap:6px;margin-top:6px">
          <button class="btn xs" id="cutIn" title="Start a cut at the playhead (key: [ )">[ Cut from here</button>
          <button class="btn xs" id="cutOut" title="End the cut at the playhead (key: ] )">Cut to here ]</button>
          <button class="btn xs" id="cutSil" title="Remove pauses where nobody talks (needs captions)">🤫 Remove silences</button>
          <button class="btn xs" id="cutUndo" title="Undo last cut change">↶ Undo</button>
        </div>
        <div id="cutList" class="cutlist"></div>
        <div class="muted small" style="margin-top:4px">Tip: <b>Shift + drag</b> on the bar above to cut a part. Cut parts are skipped in the preview and removed from the video.</div>
      </div>
      <div class="row"><span class="tc grow" id="etc"></span><button class="btn sm" id="exact" title="Render one frame through the real export engine">🔍 Exact HD frame</button></div>
    </div>
    <div>
      <div class="tabs" id="tabs">
        <button data-tab="cap" class="on">💬 Captions</button><button data-tab="sty">🎨 Style</button>
        <button data-tab="lay">📐 Layout</button><button data-tab="fx">✨ Effects</button><button data-tab="exp">🚀 Export</button>
      </div>

      <section class="pane on" data-pane="cap"><div class="card">
        <div class="row"><h3 class="grow">AI captions</h3>${F.sw("captions.enabled", "Show captions")}</div>
        <div class="grid2">
          <div><label class="f">Spoken language</label>${F.seg("captions.language", [["en", "English"], ["hi", "Hindi"], ["auto", "Auto-detect"]])}</div>
          <div><label class="f">Hindi captions as</label>${F.seg("captions.hindi_mode", [["devanagari", "हिंदी"], ["hinglish", "Hinglish"], ["english", "English"]])}</div>
        </div>
        <div class="grid2">
          <div>${F.sel("captions.model", "Accuracy", [["auto", "Auto (best for your PC)"], ["large-v3", "Max accuracy (large-v3)"], ["turbo", "Fast (large-v3-turbo)"], ["medium", "Medium"], ["small", "Small (CPU friendly)"]])}</div>
          <div>${F.text("captions.hotwords", "Names / slang to recognise", "e.g. Lucia, Jason, my gamer tag")}</div>
        </div>
        <div class="row" style="margin-top:14px">
          <button class="btn primary big" id="genCap">✨ ${words.length ? "Re-generate captions" : "Generate captions"}</button>
          <span class="muted small grow" id="capInfo"></span>
        </div>
        <div class="sep"></div>
        <div class="row"><h3 class="grow">Transcript</h3><span class="muted small">Click = jump · Double-click = edit · <span style="border-bottom:2px solid var(--warn)">underlined</span> = AI unsure</span></div>
        <div class="wtools">
          <button class="btn xs" data-shift="-0.05">⏪ Captions earlier 50ms</button>
          <button class="btn xs" data-shift="0.05">Captions later 50ms ⏩</button>
          <button class="btn xs" id="findRep">🔁 Find &amp; replace</button>
          <button class="btn xs danger" id="delWord" disabled>✕ Delete word</button>
          <a class="btn xs" href="/api/projects/${pid}/clips/${cid}/srt">⬇ .srt</a>
        </div>
        <div class="words" id="words"></div>
      </div></section>

      <section class="pane" data-pane="sty"><div class="card">
        <h3>Caption style presets</h3><p class="muted small" style="margin:0 0 10px">Pick one, then fine-tune below. Everything is live in the preview.</p>
        <div class="presets" id="presets"></div>
        <div class="sep"></div>
        <div class="grid2">
          <div>${F.sel("style.font", "Font", Object.entries(pres.fonts).map(([k, f]) => [k, f.label + (f.devanagari ? " · हिंदी ✓" : "")]))}</div>
          <div>${F.sel("style.highlight_mode", "Active word effect", [["color", "Colour highlight"], ["grow", "Colour + grow"], ["dim", "Dim other words"], ["karaoke", "Karaoke sweep"], ["none", "None"]])}</div>
          <div>${F.range("style.size", "Text size", 36, 200, 1, "px")}</div>
          <div>${F.range("style.position", "Vertical position", 0.08, 0.95, 0.01, "%")}</div>
          <div>${F.range("style.max_words", "Words per caption", 1, 10, 1)}</div>
          <div>${F.range("style.max_chars_line", "Max letters per line", 8, 40, 1)}</div>
          <div>${F.range("style.outline", "Outline thickness", 0, 16, 0.5, "px")}</div>
          <div>${F.range("style.shadow", "Shadow", 0, 14, 0.5, "px")}</div>
          <div>${F.range("style.blur", "Glow / softness", 0, 8, 0.5)}</div>
          <div>${F.sel("style.animation", "Pop-in animation", [["pop", "Pop"], ["bounce", "Bounce"], ["fade", "Fade"], ["slide", "Slide up"], ["none", "None"]])}</div>
        </div>
        <label class="f">Colours</label>
        <div class="colors">${F.color("style.color", "Text")}${F.color("style.highlight", "Active word")}${F.color("style.outline_color", "Outline")}${F.color("style.shadow_color", "Shadow")}${F.color("style.box_color", "Box")}</div>
        <div class="grid2" style="margin-top:6px">
          <div>${F.sw("style.uppercase", "ALL CAPS")}${F.sw("style.strip_punct", "Remove punctuation", "Cleaner look")}</div>
          <div>${F.sw("style.box", "Background box")}${F.sw("style.censor", "Censor swear words", "f*** — keeps ads on (English + Hindi)")}</div>
        </div>
        ${F.range("style.box_opacity", "Box opacity", 0, 1, 0.05)}
        <div class="row wrap" style="margin-top:14px">
          <button class="btn sm" id="saveDef">⭐ Save as my default</button>
          <button class="btn sm" id="applyAll">Apply style to all clips</button>
        </div>
      </div></section>

      <section class="pane" data-pane="lay"><div class="card">
        <h3>Format</h3>
        <label class="f">Aspect ratio</label>${F.seg("layout.aspect", [["9:16", "9:16 Shorts/Reels/TikTok"], ["1:1", "1:1 Square"], ["4:5", "4:5 Insta feed"], ["16:9", "16:9 YouTube"]])}
        <label class="f">Framing</label>${F.seg("layout.mode", [["blur", "Blur fill"], ["crop", "Crop to fill"], ["fit", "Fit (black bars)"], ["facecam", "Facecam + gameplay"]])}
        <div class="grid2">
          <div>${F.range("layout.zoom", "Gameplay zoom", 0.6, 3.2, 0.01, "x")}</div>
          <div>${F.range("layout.crop_x", "Horizontal focus (left ↔ right)", 0, 1, 0.01, "%")}</div>
          <div id="fgY">${F.range("layout.fg_y", "Gameplay vertical position", 0, 1, 0.01, "%")}</div>
        </div>
        <div id="camBox" class="hidden">
          <div class="sep"></div><h3>Facecam area (in the original video)</h3>
          <p class="muted small" style="margin:0">Find your webcam box in the source video and match it with these sliders. It's shown on top, gameplay underneath.</p>
          <div class="grid2">
            <div>${F.range("layout.facecam.x", "Cam left", 0, 0.95, 0.005, "%")}</div><div>${F.range("layout.facecam.y", "Cam top", 0, 0.95, 0.005, "%")}</div>
            <div>${F.range("layout.facecam.w", "Cam width", 0.05, 1, 0.005, "%")}</div><div>${F.range("layout.facecam.h", "Cam height", 0.05, 1, 0.005, "%")}</div>
            <div>${F.range("layout.cam_ratio", "Cam share of screen", 0.2, 0.6, 0.01, "%")}</div>
          </div>
        </div>
        <div class="note" style="margin-top:14px">💡 For GTA-style gameplay, <b>Blur fill</b> with zoom ~1.3–1.6 looks great on Shorts. <b>Crop to fill</b> + horizontal focus works best when the action is centred.</div>
      </div></section>

      <section class="pane" data-pane="fx"><div class="card">
        <h3>Hook title</h3><p class="muted small" style="margin:0">Big text in the first seconds. The #1 trick for retention on Shorts.</p>
        ${F.text("effects.hook_text", "Hook text", "e.g. GTA 6 cops are BROKEN 💀 / ये क्या हो गया 😱")}
        <div class="grid3"><div>${F.range("effects.hook_duration", "Show for", 0, 10, 0.5, "s")}</div><div>${F.range("effects.hook_position", "Position", 0.05, 0.9, 0.01, "%")}</div>
          <div class="colors" style="margin-top:28px">${F.color("effects.hook_color", "Text")}${F.color("effects.hook_bg", "Box")}</div></div>
        <div class="sep"></div>
        <div class="row"><h3 class="grow">💬 Text pop-ups</h3><button class="btn sm primary" id="popScan" title="Reads the game's screen (WASTED, MISSION PASSED, VICTORY, BOOYAH…) and adds pop-ups automatically. Works offline.">🔍 Find game moments</button></div>
        <p class="muted small" style="margin:2px 0 8px">Big text that pops on screen for a moment — “WASTED 💀”, “NO WAY 😱”, “+5 STARS 🚨”. Add your own at the playhead, or let the app read the game screen.</p>
        <div class="row wrap" style="gap:6px">
          <input class="input grow" id="popText" placeholder="Type pop-up text, e.g. BRO WHAT 😭" maxlength="40" style="min-width:180px">
          <input type="color" id="popColor" value="#FFE500" title="Text colour">
          <select class="input" id="popDur" style="width:auto" title="How long it stays">${[1, 1.5, 2, 3].map(s => `<option value="${s}" ${s === 1.5 ? "selected" : ""}>${s}s</option>`).join("")}</select>
          <button class="btn sm" id="popAdd">＋ Add at playhead</button>
        </div>
        <div class="row wrap" style="gap:5px;margin-top:6px">${["WASTED 💀", "NO WAY 😱", "BRO WHAT 😭", "LET'S GO 🔥", "EZ 😎", "RIP 💀", "OOPS 🤡", "CLUTCH 🧊"].map(t => `<button class="btn xs" data-popq="${esc(t)}">${esc(t)}</button>`).join("")}</div>
        <div id="popList" class="cutlist" style="max-height:200px"></div>
        <div class="sep"></div>
        <h3>Motion &amp; branding</h3>
        ${F.sw("effects.punch_zoom", "💥 Punch zoom on hype moments", "Auto zoom-in on explosions, gunshots, screams")}
        ${F.range("effects.punch_strength", "Punch strength", 0.03, 0.3, 0.01)}
        ${F.sw("effects.progress_bar", "Progress bar", "Keeps viewers watching to the end")}
        <div class="colors" style="max-width:200px">${F.color("effects.bar_color", "Bar colour")}</div>
        ${F.text("effects.watermark", "Watermark / channel handle", "@YourChannel")}
        <div class="sep"></div>
        <h3>Audio</h3>
        ${F.sw("effects.normalize", "Loudness normalize (-14 LUFS)", "YouTube's standard. Consistent, punchy volume")}
        ${F.sw("effects.bleep", "🤬 Bleep swear words in audio", "Needs captions. Helps keep videos advertiser-friendly")}
        <div id="bleepBox">
          <label class="f">Bleep sound <span class="muted" id="bleepInfo"></span></label>
          <div class="seg sfxseg" data-seg="effects.bleep_sound">${bleepOpts.map(([k, l]) => `<span class="sfxopt"><button data-val="${k}">${l}</button>${k !== "mute" ? `<button class="sfxplay" data-sfx-play="${k}" title="Listen">▶</button>` : ""}</span>`).join("")}</div>
        </div>
        ${F.sw("effects.fade", "Smooth fade in/out")}
        <label class="f">Background music</label>
        <div class="row"><select class="input grow" data-k="effects.music" id="musicSel"></select><button class="btn sm" id="musicUp">⬆ Add</button><input type="file" id="musicFile" accept="audio/*" hidden></div>
        ${F.range("effects.music_volume", "Music volume", 0, 0.6, 0.01)}
        ${F.sw("effects.duck", "Auto-duck music under game audio & voice")}
        <p class="muted small">Only use music you have rights to (YouTube Audio Library is free). Copyrighted songs get your video claimed.</p>
        <div class="sep"></div>
        <h3>🔊 Sound effects</h3>
        <p class="muted small" style="margin:0">Meme sounds on top of your gameplay. Made in-app, so no copyright claims.</p>
        <div class="grid2">
          <div>${F.sel("effects.hype_sfx", "Auto sound on hype moments", [["none", "None"], ...sfxList()])}<div class="muted small" id="hypeInfo" style="margin-top:4px"></div></div>
          <div>${F.range("effects.sfx_volume", "SFX volume", 0, 1.5, 0.05, "%")}</div>
        </div>
        <label class="f">Soundboard · tap to drop a sound at the playhead</label>
        <div class="sboard" id="sboard">${sfxList().map(([k, l]) => `<button class="btn sm" data-sfx-add="${k}" style="--sc:${SFX_COLOR[k] || "#ff2e88"}">${l}</button>`).join("")}</div>
        <div class="sfxmarks" id="sfxMarks"></div>
      </div></section>

      <section class="pane" data-pane="exp"><div class="card">
        <h3>Export</h3>
        <div class="grid3">
          <div>${F.sel("export.quality", "Quality", [["high", "High (recommended)"], ["max", "Max"], ["fast", "Fast draft"]])}</div>
          <div>${F.sel("export.fps", "Frame rate", [["source", "Same as source (≤60)"], ["60", "60 fps"], ["30", "30 fps"]])}</div>
          <div>${F.sel("export.encoder", "Encoder", [["auto", "Auto (GPU if available)"], ["nvenc", "NVIDIA GPU"], ["x264", "CPU x264"]])}</div>
        </div>
        <div class="row" style="margin-top:16px"><button class="btn primary big" id="renderBtn">🚀 Render video</button><span class="grow"></span></div>
        <div id="renderProg" class="hidden" style="margin-top:12px"><div class="progress"><i></i></div><div class="muted small" style="margin-top:4px"></div></div>
        <div id="result" class="result" style="margin-top:14px"></div>
      </div>
      <div class="card" style="margin-top:14px">
        <div class="row"><h3 class="grow">📈 Title, description &amp; tags</h3><button class="btn sm" id="genMeta">✨ Suggest</button></div>
        <div id="metaBox"><p class="muted small">Click Suggest for viral title ideas, an SEO description and tags.</p></div>
      </div>
      <div class="card" style="margin-top:14px">
        <div class="row"><h3 class="grow">🖼 Thumbnail</h3><button class="btn sm" id="thumbBtn">Make from current frame</button></div>
        <p class="muted small" style="margin:4px 0 0">Uses the paused frame + your hook text (or title). Long videos only — YouTube doesn't allow custom Shorts thumbnails.</p>
        <div id="thumbBox" style="margin-top:10px"></div>
      </div>
      <div class="card" style="margin-top:14px" id="ytCard"></div>
      </section>
    </div>
  </div>`;

  // ------------------------------------------------ preview engine
  const ev = $("#ev"), stage = $("#stage");
  const pv = new Preview($("#cv"), ev);
  pv.set(C, st, words, P.source);
  pv.start();
  ev.addEventListener("loadeddata", () => { if (ev.currentTime < C.start || ev.currentTime > C.end) ev.currentTime = C.start; pv.draw(); });
  ev.addEventListener("seeked", () => pv.draw());
  ev.onplay = () => stage.classList.add("playing"); ev.onpause = () => stage.classList.remove("playing");
  $("#playov").onclick = () => { unlockAudio(); if (ev.paused) { if (ev.currentTime < C.start || ev.currentTime >= C.end - 0.05) ev.currentTime = C.start; ev.play(); } else ev.pause(); };
  const onResize = () => { pv.resize(); drawScrub(); };
  window.addEventListener("resize", onResize);
  S.cleanup.push(() => { pv.stop(); ev.pause(); window.removeEventListener("resize", onResize); });
  document.fonts.ready.then(() => pv.draw());

  const onKey = e => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.code === "Space") { e.preventDefault(); $("#playov").click(); }
    if (e.key === "ArrowLeft") ev.currentTime = Math.max(C.start, ev.currentTime - 1);
    if (e.key === "ArrowRight") ev.currentTime = Math.min(C.end, ev.currentTime + 1);
  };
  window.addEventListener("keydown", onKey); S.cleanup.push(() => window.removeEventListener("keydown", onKey));

  // ------------------------------------------------ scrubber (clip ±8s context)
  const scrub = $("#scrub"), scv = $("#scv"), sctx = scv.getContext("2d");
  const span = () => { const pad = Math.max(4, (C.end - C.start) * 0.25); return [Math.max(0, C.start - pad), Math.min(P.source.duration, C.end + pad)]; };
  function drawScrub() {
    const dpr = devicePixelRatio || 1, w = scrub.clientWidth, h = scrub.clientHeight;
    if (scv.width !== Math.round(w * dpr)) { scv.width = Math.round(w * dpr); scv.height = Math.round(h * dpr); }
    sctx.setTransform(dpr, 0, 0, dpr, 0, 0); sctx.clearRect(0, 0, w, h);
    const [a, b] = span(), X = t => (t - a) / (b - a) * w;
    sctx.fillStyle = "rgba(198,255,61,.14)"; sctx.fillRect(X(C.start), 0, X(C.end) - X(C.start), h);
    const eff = st.effects || {};
    words.forEach(wd => {
      const x = X(C.start + wd.s);
      sctx.fillStyle = eff.bleep && isSwear(wd.w) ? "#ff4d5e" : "rgba(255,255,255,.35)";
      sctx.fillRect(x, h - 14, Math.max(1, X(C.start + wd.e) - x), 8);
    });
    // hype moments that get the auto sound (little triangles on top)
    if (eff.hype_sfx && eff.hype_sfx !== "none") {
      sctx.fillStyle = "rgba(46,230,255,.9)";
      (C.hype || C.punch || []).forEach(p => { const x = X(C.start + p); sctx.beginPath(); sctx.moveTo(x - 4, 0); sctx.lineTo(x + 4, 0); sctx.lineTo(x, 6); sctx.fill(); });
    }
    // text pop-ups: little rounded tags at the bottom
    (eff.text_pops || []).forEach(p => {
      const x = X(C.start + +p.t); sctx.fillStyle = p.color || "#FFE500";
      sctx.fillRect(x - 1, h - 20, 2, 14); sctx.fillRect(x - 4, h - 22, 8, 5);
    });
    // soundboard markers
    (eff.sfx_marks || []).forEach(m => {
      const x = X(C.start + +m.t); sctx.fillStyle = SFX_COLOR[m.sound] || "#ff2e88";
      sctx.fillRect(x - 1, 8, 2, h - 24); sctx.beginPath(); sctx.arc(x, 9, 3.5, 0, Math.PI * 2); sctx.fill();
    });
    // cut-out parts: red hatched blocks
    (C.cuts || []).forEach(c => {
      const x0 = X(C.start + c.s), x1 = X(C.start + c.e);
      sctx.fillStyle = "rgba(255,77,94,.38)"; sctx.fillRect(x0, 0, x1 - x0, h);
      sctx.save(); sctx.beginPath(); sctx.rect(x0, 0, x1 - x0, h); sctx.clip();
      sctx.strokeStyle = "rgba(255,77,94,.85)"; sctx.lineWidth = 1.5;
      for (let xx = x0 - h; xx < x1; xx += 7) { sctx.beginPath(); sctx.moveTo(xx, h); sctx.lineTo(xx + h, 0); sctx.stroke(); }
      sctx.restore();
    });
    if (cutDrag) { const x0 = X(Math.min(cutDrag.a, cutDrag.b)), x1 = X(Math.max(cutDrag.a, cutDrag.b)); sctx.fillStyle = "rgba(255,77,94,.5)"; sctx.fillRect(x0, 0, x1 - x0, h); }
    if (cutStart != null) { sctx.fillStyle = "#ffb020"; sctx.fillRect(X(C.start + cutStart) - 1.5, 0, 3, h); }
    sctx.fillStyle = "#c6ff3d"; [C.start, C.end].forEach(t => { sctx.fillRect(X(t) - 2, 0, 4, h); });
    sctx.fillStyle = "#fff"; sctx.fillRect(X(ev.currentTime) - 1, 0, 2, h);
  }
  let sdrag = null, cutDrag = null, cutStart = null;
  scrub.onmousedown = e => {
    const [a, b] = span(), w = scrub.clientWidth, x = e.clientX - scrub.getBoundingClientRect().left, t = a + x / w * (b - a);
    if (e.shiftKey) { cutDrag = { a: Math.max(C.start, Math.min(C.end, t)), b: Math.max(C.start, Math.min(C.end, t)) }; sdrag = "cut"; return; }
    const near = (tt) => Math.abs((tt - a) / (b - a) * w - x) < 8;
    sdrag = near(C.start) ? "start" : near(C.end) ? "end" : "seek";
    if (sdrag === "seek") ev.currentTime = t;
  };
  const sMove = e => {
    if (!sdrag) return;
    const [a, b] = span(), t = a + (e.clientX - scrub.getBoundingClientRect().left) / scrub.clientWidth * (b - a);
    if (sdrag === "cut") { cutDrag.b = Math.max(C.start, Math.min(C.end, t)); drawScrub(); return; }
    if (sdrag === "seek") ev.currentTime = Math.max(0, t);
    else { C[sdrag] = Math.max(0, Math.min(P.source.duration, t)); if (C.end - C.start < 1) C[sdrag] = sdrag === "start" ? C.end - 1 : C.start + 1; updTrim(); ev.currentTime = C[sdrag]; }
  };
  const sUp = () => {
    if (sdrag === "cut" && cutDrag) {
      const s = Math.min(cutDrag.a, cutDrag.b) - C.start, e2 = Math.max(cutDrag.a, cutDrag.b) - C.start;
      cutDrag = null;
      if (e2 - s >= 0.1) addCut(s, e2); else drawScrub();
    } else if (sdrag && sdrag !== "seek") saveTrim();
    sdrag = null;
  };
  window.addEventListener("mousemove", sMove); window.addEventListener("mouseup", sUp);
  S.cleanup.push(() => { window.removeEventListener("mousemove", sMove); window.removeEventListener("mouseup", sUp); });

  let lastWi = -2;
  pv.onTime = (t) => {
    const outT = Math.max(0, t) - (C.cuts || []).reduce((a, c) => a + Math.max(0, Math.min(t, c.e) - c.s), 0);
    $("#etc").textContent = `${fmt(Math.max(0, outT))} / ${fmt(finalLen())}${(C.cuts || []).length ? " (after cuts)" : ""}`;
    drawScrub();
    let wi = -1;
    for (let i = 0; i < words.length && words[i].s <= t; i++) if (t < words[i].e + 0.3) wi = i;
    if (wi !== lastWi) {
      $$("#words .word.cur").forEach(x => x.classList.remove("cur"));
      if (wi >= 0) { const el = $(`#words [data-i="${wi}"]`); el?.classList.add("cur"); }
      lastWi = wi;
    }
  };

  const cutTotal = () => (C.cuts || []).reduce((a, c) => a + (c.e - c.s), 0);
  const finalLen = () => Math.max(0, (C.end - C.start) - cutTotal());
  function updTrim() {
    $("#tStart").textContent = fmt(C.start, 2);
    $("#tEnd").textContent = `${fmt(C.end, 2)}  (${finalLen().toFixed(1)}s)`;
    renderCuts(); drawScrub();
  }
  async function saveTrim() {
    try {
      const r = await api("PATCH", `/api/projects/${pid}/clips/${cid}`, { start: C.start, end: C.end });
      Object.assign(C, { start: r.start, end: r.end, words: r.words, punch: r.punch, hype: r.hype, cuts: r.cuts || [] }); words = clone(r.words || []);
      st.effects.sfx_marks = r.settings?.effects?.sfx_marks ?? st.effects.sfx_marks;
      pv.set(C, st, words, P.source); renderWords(); updTrim(); renderMarks(); updSfxInfo();
    } catch (e) { fail(e); }
  }

  // ------------------------------------------------ cuts (remove parts inside the clip)
  C.cuts = C.cuts || [];
  const cutHistory = [];
  function mergeCuts(list) {
    const len = C.end - C.start;
    const r = list.map(c => ({ s: Math.max(0, Math.min(c.s, c.e)), e: Math.min(len, Math.max(c.s, c.e)) }))
      .filter(c => c.e - c.s >= 0.05).sort((a, b) => a.s - b.s);
    const out = [];
    for (const c of r) { if (out.length && c.s <= out.at(-1).e + 0.02) out.at(-1).e = Math.max(out.at(-1).e, c.e); else out.push({ ...c }); }
    return out.map(c => ({ s: +c.s.toFixed(3), e: +c.e.toFixed(3) }));
  }
  const saveCuts = debounce(async () => {
    try { const r = await api("PATCH", `/api/projects/${pid}/clips/${cid}`, { cuts: C.cuts }); C.cuts = r.cuts || []; updTrim(); }
    catch (e) { fail(e); }
  }, 350);
  function setCuts(list, msg) {
    const next = mergeCuts(list);
    if ((C.end - C.start) - next.reduce((a, c) => a + c.e - c.s, 0) < 1) return toast("Keep at least 1 second of the clip", true);
    cutHistory.push(clone(C.cuts)); if (cutHistory.length > 40) cutHistory.shift();
    C.cuts = next; pv.set(C, st, words, P.source); updTrim(); renderWords(); saveCuts();
    if (msg) toast(msg);
  }
  function addCut(s, e) { setCuts([...C.cuts, { s, e }], `✂ Cut ${(e - s).toFixed(1)}s — final length ${Math.max(0, finalLen() - (e - s)).toFixed(1)}s`); }
  function renderCuts() {
    const n = (C.cuts || []).length;
    $("#cutInfo").textContent = n ? `${n} cut${n > 1 ? "s" : ""} · −${cutTotal().toFixed(1)}s · final ${finalLen().toFixed(1)}s` : `final ${finalLen().toFixed(1)}s`;
    $("#cutList").innerHTML = (C.cuts || []).map((c, i) => `
      <div class="cutrow"><span class="dot"></span><span class="mono">${fmt(c.s, 1)} → ${fmt(c.e, 1)}</span><span class="muted">(${(c.e - c.s).toFixed(1)}s)</span>
      <span class="grow"></span><button class="btn xs" data-cutplay="${i}" title="Play around this cut">▶</button><button class="btn xs" data-cutdel="${i}" title="Put this part back">↺ Restore</button></div>`).join("");
    $("#cutUndo").disabled = !cutHistory.length;
    $("#cutOut").disabled = cutStart == null;
    $("#cutIn").classList.toggle("primary", cutStart != null);
  }
  $("#cutIn").onclick = () => {
    const t = ev.currentTime - C.start;
    if (t < 0 || t > C.end - C.start) return toast("Move the playhead inside the clip first", true);
    cutStart = t; renderCuts(); drawScrub(); toast("Cut start set — move to where the cut should end and press “Cut to here ]”");
  };
  $("#cutOut").onclick = () => {
    if (cutStart == null) return;
    const t = Math.max(0, Math.min(C.end - C.start, ev.currentTime - C.start));
    const s = Math.min(cutStart, t), e = Math.max(cutStart, t);
    cutStart = null;
    if (e - s < 0.1) { renderCuts(); drawScrub(); return toast("Cut is too short — move the playhead further", true); }
    addCut(s, e);
  };
  $("#cutUndo").onclick = () => {
    if (!cutHistory.length) return;
    C.cuts = cutHistory.pop(); pv.set(C, st, words, P.source); updTrim(); renderWords(); saveCuts(); toast("Undone");
  };
  $("#cutSil").onclick = () => {
    if (!words.length) return toast("Generate captions first — silences are found from the speech", true);
    const len = C.end - C.start, pad = 0.18, minGap = 0.7, gaps = [];
    const ws = [...words].filter(w => w.s >= 0 && w.s < len).sort((a, b) => a.s - b.s);
    if (ws.length && ws[0].s > minGap) gaps.push({ s: 0, e: ws[0].s - pad });
    for (let i = 0; i + 1 < ws.length; i++) { const g0 = ws[i].e, g1 = ws[i + 1].s; if (g1 - g0 > minGap) gaps.push({ s: g0 + pad, e: g1 - pad }); }
    if (ws.length && len - ws.at(-1).e > minGap) gaps.push({ s: ws.at(-1).e + pad, e: len });
    if (!gaps.length) return toast("No long silences found 👌");
    const removed = gaps.reduce((a, g) => a + g.e - g.s, 0);
    setCuts([...C.cuts, ...gaps], `🤫 Removed ${gaps.length} silence${gaps.length > 1 ? "s" : ""} (−${removed.toFixed(1)}s). Use ↶ Undo if you want them back.`);
  };
  $("#cutList").onclick = e => {
    const d = e.target.dataset;
    if (d.cutdel != null) { const list = clone(C.cuts); list.splice(+d.cutdel, 1); setCuts(list, "Part restored"); }
    if (d.cutplay != null) { const c = C.cuts[+d.cutplay]; ev.currentTime = C.start + Math.max(0, c.s - 2); unlockAudio(); ev.play(); }
  };
  const onCutKey = e => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.key === "[") $("#cutIn").click();
    if (e.key === "]") $("#cutOut").click();
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && cutHistory.length) { e.preventDefault(); $("#cutUndo").click(); }
  };
  window.addEventListener("keydown", onCutKey); S.cleanup.push(() => window.removeEventListener("keydown", onCutKey));
  $$("[data-nudge]").forEach(b => b.onclick = () => { const [k, d] = b.dataset.nudge.split(","); C[k] = Math.max(0, Math.min(P.source.duration, C[k] + +d)); updTrim(); ev.currentTime = C[k]; saveTrim(); });
  $$("[data-set]").forEach(b => b.onclick = () => { const k = b.dataset.set; C[k] = ev.currentTime; if (C.end - C.start < 1) return toast("Clip must be at least 1s", true); updTrim(); saveTrim(); });
  updTrim();

  // ------------------------------------------------ nav + title
  $("#prevC").onclick = () => location.hash = `#/p/${pid}/c/${P.clips[idx - 1].id}`;
  $("#nextC").onclick = () => location.hash = `#/p/${pid}/c/${P.clips[idx + 1].id}`;
  $("#ctitle").onchange = e => { C.title = e.target.value; api("PATCH", `/api/projects/${pid}/clips/${cid}`, { title: C.title }).catch(fail); };

  // ------------------------------------------------ tabs
  $("#tabs").onclick = e => {
    const t = e.target.dataset.tab; if (!t) return;
    $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
    $$(".pane").forEach(p => p.classList.toggle("on", p.dataset.pane === t));
    if (t === "exp") { renderResult(); renderYT(); }
  };

  // ------------------------------------------------ settings binding
  const saveSettings = debounce(() => api("PATCH", `/api/projects/${pid}/clips/${cid}`, { settings: st }).catch(fail), 450);
  function syncForm() {
    $$("[data-k]").forEach(el => {
      const val = getPath(st, el.dataset.k);
      if (el.type === "checkbox") el.checked = !!val;
      else if (val !== undefined && val !== null) el.value = val;
      else if (el.tagName === "SELECT") el.value = "";
    });
    $$("[data-v]").forEach(el => {
      const val = getPath(st, el.dataset.v), u = el.dataset.unit;
      el.textContent = val == null ? "" : u === "%" ? Math.round(val * 100) + "%" : u === "x" ? (+val).toFixed(2) + "x" : `${+(+val).toFixed(2)}${u}`;
    });
    $$("[data-seg]").forEach(el => { const val = String(getPath(st, el.dataset.seg)); $$("button[data-val]", el).forEach(b => b.classList.toggle("on", b.dataset.val === val)); });
    updSfxInfo();
    $("#camBox").classList.toggle("hidden", st.layout.mode !== "facecam");
    $("#fgY").classList.toggle("hidden", !["blur", "fit"].includes(st.layout.mode));
    $$(".preset").forEach(p => p.classList.toggle("on", p.dataset.p === st.style.preset));
  }
  function changed(k) {
    if (k.startsWith("style.") && k !== "style.preset") st.style.preset = "custom";
    syncForm(); pv.set(C, st, words, P.source); saveSettings(); if (k.startsWith("style.max")) renderWords();
  }
  onView("input", e => {
    const el = e.target, k = el.dataset.k; if (!k) return;
    let val = el.type === "checkbox" ? el.checked : (el.type === "range" || el.type === "number") ? +el.value : el.value;
    if (k === "effects.music" && val === "") val = null;
    setPath(st, k, val); changed(k);
    if (k === "effects.hype_sfx" && val !== "none") pv.playSfx(val);
  });
  onView("click", e => {
    const p = e.target.closest("[data-sfx-play]"); if (p) { pv.playSfx(p.dataset.sfxPlay); return; }
    const b = e.target.closest("[data-seg] button[data-val]"); if (!b) return;
    const k = b.closest("[data-seg]").dataset.seg; setPath(st, k, b.dataset.val); changed(k);
    if (k === "layout.aspect") pv.resize();
    if (k === "effects.bleep_sound" && b.dataset.val !== "mute") pv.playSfx(b.dataset.val);
  });
  if (!st.layout.facecam) st.layout.facecam = { x: 0.0, y: 0.0, w: 0.28, h: 0.3 };
  if (st.layout.cam_ratio == null) st.layout.cam_ratio = 0.38;

  // presets grid
  $("#presets").innerHTML = Object.entries(pres.captions).map(([k, p]) => {
    const s = p.style, ff = cssFont(s.font);
    const stroke = s.outline ? `-webkit-text-stroke:${Math.min(5, s.outline / 2.2)}px ${s.outline_color};paint-order:stroke fill;` : "";
    const shadow = s.blur ? `text-shadow:0 0 8px ${s.color};` : s.shadow ? `text-shadow:2px 2px 0 rgba(0,0,0,.5);` : "";
    const box = s.box ? `background:rgba(0,0,0,${s.box_opacity});padding:2px 6px;` : "";
    const wds = (k === "hindi" ? ["भाई", "ये", "क्या"] : k === "oneword" ? ["INSANE"] : ["this", "is", "insane"]).map(w => s.uppercase ? w.toUpperCase() : w);
    const hi = k === "oneword" ? 0 : 1;
    return `<div class="preset" data-p="${k}"><div class="sample" style="font-family:${ff};${stroke}${shadow}${box}">
      ${wds.map((w, i) => `<span style="color:${i === hi && s.highlight_mode !== "none" ? s.highlight : s.color};${s.highlight_mode === "dim" && i !== hi ? "opacity:.55" : ""}">${w}</span>`).join("")}</div>
      <div class="pl">${esc(p.label)}</div><div class="pd">${esc(p.desc)}</div></div>`;
  }).join("");
  $("#presets").onclick = e => {
    const p = e.target.closest(".preset"); if (!p) return;
    st.style = clone(pres.captions[p.dataset.p].style); changed("style.preset"); renderWords();
  };
  $("#saveDef").onclick = async () => {
    try { S.settings = await api("POST", "/api/settings", { defaults: { style: st.style, layout: st.layout, effects: { ...st.effects, hook_text: "", sfx_marks: [] }, export: st.export, captions: { ...st.captions } } }); toast("Saved — new clips will use this style ⭐"); }
    catch (e) { fail(e); }
  };
  $("#applyAll").onclick = async () => {
    if (!confirm(`Apply this caption style to all ${P.clips.length} clips in this project?`)) return;
    try { await Promise.all(P.clips.map(c => api("PATCH", `/api/projects/${pid}/clips/${c.id}`, { settings: { style: st.style } }))); toast("Style applied to all clips"); }
    catch (e) { fail(e); }
  };

  // music
  async function loadMusic() {
    const list = await api("GET", "/api/music").catch(() => []);
    $("#musicSel").innerHTML = `<option value="">— None —</option>` + list.map(m => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("");
    $("#musicSel").value = st.effects.music || "";
  }
  $("#musicUp").onclick = () => $("#musicFile").click();
  $("#musicFile").onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api("POST", "/api/music", fd); await loadMusic(); st.effects.music = r.name; $("#musicSel").value = r.name; changed("effects.music"); toast("Music added"); }
    catch (er) { fail(er); }
  };
  loadMusic();

  // ------------------------------------------------ sound effects
  function updSfxInfo() {
    const e = st.effects;
    $("#bleepBox").classList.toggle("hidden", !e.bleep);
    const n = words.filter(w => isSwear(w.w)).length;
    $("#bleepInfo").textContent = !words.length ? "· generate captions first" : n ? `· ${n} swear word${n === 1 ? "" : "s"} found` : "· no swear words found 😇";
    const hp = (C.hype || C.punch || []).length;
    $("#hypeInfo").textContent = e.hype_sfx === "none" ? "" : hp
      ? `Plays on ${hp} hype moment${hp === 1 ? "" : "s"} (▼ on the timeline)`
      : "No hype moments in this clip. Export will use the loudest moment.";
  }
  function renderMarks() {
    const box = $("#sfxMarks"), ms = st.effects.sfx_marks, len = C.end - C.start;
    if (!ms.length) { box.innerHTML = `<div class="muted small">No sounds placed yet. Play or scrub to a moment, then tap a sound above.</div>`; return; }
    box.innerHTML = `<div class="row small muted"><span class="grow">${ms.length} sound${ms.length === 1 ? "" : "s"} placed</span><button class="btn xs ghost" data-mclear>Clear all</button></div>` +
      ms.map((m, i) => `<div class="sfxmark ${m.t >= len ? "out" : ""}">
        <i style="background:${SFX_COLOR[m.sound] || "#ff2e88"}"></i><span class="mono">${fmt(m.t, 2)}</span>
        <span class="grow">${esc(sfxLabel(m.sound))}${m.t >= len ? ' <span class="muted small">(after clip end)</span>' : ""}</span>
        <button class="btn xs" data-mjump="${i}">Jump</button><button class="btn xs" data-sfx-play="${esc(m.sound)}" title="Listen">▶</button>
        <button class="btn xs danger" data-mdel="${i}" title="Remove">✕</button></div>`).join("");
  }
  $("#sboard").onclick = e => {
    const b = e.target.closest("[data-sfx-add]"); if (!b) return;
    const name = b.dataset.sfxAdd, len = C.end - C.start, rel = ev.currentTime - C.start;
    if (rel < -0.05 || rel > len + 0.05) return toast("Move the playhead inside the clip first", true);
    // floor (not round) so the mark is never just ahead of the playhead and fires a second time while playing
    const t = Math.floor(Math.max(0, Math.min(len - 0.01, rel)) * 100) / 100;
    st.effects.sfx_marks = [...st.effects.sfx_marks, { t, sound: name }].sort((a, b2) => a.t - b2.t);
    pv.playSfx(name); pv.syncAudioTime();
    changed("effects.sfx_marks"); renderMarks();
    toast(`${sfxLabel(name)} added at ${fmt(t, 2)}`);
  };
  $("#sfxMarks").onclick = e => {
    const d = e.target.closest("button")?.dataset; if (!d) return;
    if (d.mjump != null) { const m = st.effects.sfx_marks[+d.mjump]; if (m) { ev.currentTime = C.start + +m.t; pv.draw(); } return; }
    if (d.mdel != null) { st.effects.sfx_marks.splice(+d.mdel, 1); changed("effects.sfx_marks"); renderMarks(); return; }
    if (d.mclear != null && confirm("Remove all placed sounds from this clip?")) { st.effects.sfx_marks = []; changed("effects.sfx_marks"); renderMarks(); }
  };
  renderMarks();

  // ------------------------------------------------ text pop-ups
  if (!Array.isArray(st.effects.text_pops)) st.effects.text_pops = [];
  function renderPops() {
    const box = $("#popList"), ps = st.effects.text_pops, len = C.end - C.start;
    if (!ps.length) { box.innerHTML = `<div class="muted small" style="margin-top:6px">No pop-ups yet.</div>`; drawScrub(); return; }
    box.innerHTML = ps.map((p, i) => `<div class="cutrow">
      <span style="width:10px;height:10px;border-radius:3px;background:${esc(p.color || "#FFE500")}"></span>
      <span class="mono">${fmt(p.t, 2)}</span><b class="grow" style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.text)}</b>
      ${p.source === "game" ? '<span class="chip" style="padding:0 6px">🎮 game</span>' : ""}${p.t >= len ? '<span class="muted small">(after end)</span>' : ""}
      <button class="btn xs" data-pjump="${i}">Jump</button><button class="btn xs danger" data-pdel="${i}">✕</button></div>`).join("");
    drawScrub();
  }
  function addPop(text) {
    text = (text || "").trim(); if (!text) return toast("Type some text first", true);
    const len = C.end - C.start, rel = ev.currentTime - C.start;
    if (rel < -0.05 || rel > len + 0.05) return toast("Move the playhead inside the clip first", true);
    const t = Math.floor(Math.max(0, Math.min(len - 0.2, rel)) * 100) / 100;
    st.effects.text_pops = [...st.effects.text_pops, { t, text, dur: +$("#popDur").value, color: $("#popColor").value.toUpperCase() }]
      .sort((a, b) => a.t - b.t);
    changed("effects.text_pops"); renderPops(); pv.draw();
    toast(`💬 “${text}” added at ${fmt(t, 2)}`);
  }
  $("#popAdd").onclick = () => { addPop($("#popText").value); $("#popText").value = ""; };
  $("#popText").onkeydown = e => { if (e.key === "Enter") $("#popAdd").click(); };
  onView("click", e => { const q = e.target.closest("[data-popq]"); if (q) addPop(q.dataset.popq); });
  $("#popList").onclick = e => {
    const d = e.target.closest("button")?.dataset; if (!d) return;
    if (d.pjump != null) { const p = st.effects.text_pops[+d.pjump]; if (p) { ev.currentTime = C.start + +p.t; pv.draw(); } }
    if (d.pdel != null) { st.effects.text_pops.splice(+d.pdel, 1); changed("effects.text_pops"); renderPops(); pv.draw(); }
  };
  $("#popScan").onclick = async () => {
    const b = $("#popScan"); b.disabled = true; b.innerHTML = '<span class="spin"></span> Reading the game…';
    await api("PATCH", `/api/projects/${pid}/clips/${cid}`, { settings: st }).catch(() => {});
    try {
      const j = await api("POST", `/api/projects/${pid}/clips/${cid}/gametext`);
      Jobs.add(j, async jj => {
        if (!document.body.contains(b)) return;          // user moved to another clip/page meanwhile
        b.disabled = false; b.textContent = "🔍 Find game moments";
        if (jj.status !== "done") return;
        // keep the pop-ups the user added meanwhile; replace only the game-detected ones
        const found = (jj.result.found || []).map(f => ({ ...f }));
        st.effects.text_pops = [...st.effects.text_pops.filter(p => p.source !== "game"), ...found].sort((a, b2) => a.t - b2.t);
        changed("effects.text_pops");
        pv.set(C, st, words, P.source); renderPops();
        const n = jj.result.found.length;
        toast(n ? `🎮 Found ${n} game moment${n > 1 ? "s" : ""}: ${jj.result.found.map(f => f.text).join(", ")}` : "No game banners found in this clip (try adding your own pop-ups)");
      });
    } catch (e) { fail(e); b.disabled = false; b.textContent = "🔍 Find game moments"; }
  };
  renderPops();

  // ------------------------------------------------ transcript editor
  function renderWords() {
    const box = $("#words");
    if (!words.length) {
      box.innerHTML = `<div class="muted" style="padding:10px">No captions yet. Pick the language above and hit <b>Generate captions</b>. The AI listens to every word and syncs it to the exact moment it's spoken.</div>`;
      $("#capInfo").textContent = ""; return;
    }
    const chunks = chunkWords(words, st.style);
    let i = 0, html = "";
    chunks.forEach(ch => {
      ch.forEach(w => {
        const wi = words.indexOf(w);
        const inCut = (C.cuts || []).some(c => w.s >= c.s && w.s < c.e);
        html += `<span class="word ${w.p != null && w.p < 0.55 ? "low" : ""} ${wi === selWord ? "sel" : ""} ${inCut ? "cutw" : ""}" data-i="${wi}" title="${inCut ? "✂ Cut out — not in the video · " : ""}${w.s.toFixed(2)}s → ${w.e.toFixed(2)}s · confidence ${Math.round((w.p ?? 1) * 100)}%">${esc(w.w)}</span>`;
        i++;
      });
      html += `<span class="chunkbreak"></span>`;
    });
    box.innerHTML = html;
    const low = words.filter(w => w.p != null && w.p < 0.55).length;
    $("#capInfo").textContent = `${words.length} words · ${chunks.length} captions · language: ${C.language || "?"}${low ? ` · ${low} to double-check` : ""}`;
    $("#delWord").disabled = selWord < 0;
  }
  const saveWords = debounce(() => {
    api("PATCH", `/api/projects/${pid}/clips/${cid}`, { words }).then(() => { C.words = words; }).catch(fail);
  }, 400);
  function wordsChanged() { pv.set(C, st, words, P.source); renderWords(); saveWords(); drawScrub(); updSfxInfo(); }

  $("#words").addEventListener("click", e => {
    const el = e.target.closest(".word"); if (!el || el.querySelector("input")) return;
    selWord = +el.dataset.i; ev.currentTime = C.start + words[selWord].s + 0.01; renderWords();
  });
  $("#words").addEventListener("dblclick", e => {
    const el = e.target.closest(".word"); if (!el) return;
    const i = +el.dataset.i, w = words[i];
    el.innerHTML = `<input value="${esc(w.w)}">`;
    const inp = $("input", el); inp.focus(); inp.select();
    const commit = () => {
      const parts = inp.value.trim().split(/\s+/).filter(Boolean);
      if (!parts.length) words.splice(i, 1);
      else if (parts.length === 1) words[i] = { ...w, w: parts[0], p: 1 };
      else {
        const tot = parts.reduce((a, p) => a + p.length, 0), d = w.e - w.s; let t = w.s;
        const nw = parts.map(p => { const s = t; t += d * p.length / tot; return { w: p, s: +s.toFixed(3), e: +t.toFixed(3), p: 1 }; });
        words.splice(i, 1, ...nw);
      }
      selWord = -1; wordsChanged();
    };
    inp.onkeydown = k => { if (k.key === "Enter") commit(); if (k.key === "Escape") renderWords(); };
    inp.onblur = commit;
  });
  $("#delWord").onclick = () => { if (selWord >= 0) { words.splice(selWord, 1); selWord = -1; wordsChanged(); } };
  $$("[data-shift]").forEach(b => b.onclick = () => {
    const d = +b.dataset.shift; words = words.map(w => ({ ...w, s: +Math.max(0, w.s + d).toFixed(3), e: +Math.max(0.04, w.e + d).toFixed(3) })); wordsChanged();
    toast(`Captions shifted ${d > 0 ? "later" : "earlier"} by 50ms`);
  });
  $("#findRep").onclick = () => {
    const m = modal(`<h3>Find &amp; replace</h3><p class="muted small">Fix a word the AI keeps mishearing (names, slang). Case-insensitive, whole words.</p>
      <label class="f">Find</label><input class="input" id="frF"><label class="f">Replace with</label><input class="input" id="frR">
      <div class="row" style="margin-top:14px"><button class="btn primary" id="frGo">Replace all</button></div>`);
    $("#frF", m).focus();
    $("#frGo", m).onclick = () => {
      const f = $("#frF", m).value.trim().toLowerCase(), r = $("#frR", m).value.trim(); if (!f) return;
      let n = 0;
      const strip = s => s.toLowerCase().replace(/[.,!?;:"“”…।]/g, "");
      words = words.map(w => { if (strip(w.w) === f) { n++; const tail = (w.w.match(/[.,!?;:…।]+$/) || [""])[0]; return { ...w, w: r + tail, p: 1 }; } return w; });
      closeModal(); wordsChanged(); toast(`Replaced ${n} word${n === 1 ? "" : "s"}`);
    };
  };
  $("#genCap").onclick = async () => {
    const b = $("#genCap"); b.disabled = true; b.innerHTML = '<span class="spin"></span> Listening…';
    await api("PATCH", `/api/projects/${pid}/clips/${cid}`, { settings: st }).catch(() => {});
    try {
      const j = await api("POST", `/api/projects/${pid}/clips/${cid}/transcribe`, { ...st.captions, force: true });
      Jobs.add(j, async jj => {
        b.disabled = false; b.textContent = "✨ Re-generate captions";
        if (jj.status !== "done") return;
        const p2 = await api("GET", `/api/projects/${pid}`); const c2 = p2.clips.find(c => c.id === cid);
        Object.assign(C, c2); words = clone(c2.words || []); st.captions = c2.settings.captions;
        pv.set(C, st, words, P.source); renderWords(); syncForm(); drawScrub();
        toast(`Captions ready — ${words.length} words ✨`);
      });
    } catch (e) { fail(e); b.disabled = false; b.textContent = "✨ Generate captions"; }
  };

  // ------------------------------------------------ exact frame
  $("#exact").onclick = async () => {
    const b = $("#exact"); b.disabled = true; b.innerHTML = '<span class="spin"></span>';
    try {
      const r = await api("POST", `/api/projects/${pid}/clips/${cid}/frame`, { t: Math.max(0, ev.currentTime - C.start), settings: st, words, cuts: C.cuts || [], width: 720 }, true);
      const url = URL.createObjectURL(await r.blob());
      modal(`<h3>Exact export frame @ ${fmt(ev.currentTime - C.start, 2)}</h3><p class="muted small">Rendered by the real export engine.</p><img src="${url}">`);
    } catch (e) { fail(e); } finally { b.disabled = false; b.textContent = "🔍 Exact HD frame"; }
  };

  // ------------------------------------------------ export
  function renderResult() {
    const r = C.render, box = $("#result");
    const running = Jobs.forClip(cid).find(j => j.kind === "render");
    if (!r) { box.innerHTML = running ? "" : `<p class="muted small">Not rendered yet.</p>`; return; }
    box.innerHTML = `
      <video src="/api/projects/${pid}/clips/${cid}/output?v=${r.rendered_at}" controls playsinline></video>
      <div class="row wrap" style="margin-top:10px">
        <a class="btn primary" href="/api/projects/${pid}/clips/${cid}/output?download=1">⬇ Download MP4</a>
        ${r.srt ? `<a class="btn sm" href="/api/projects/${pid}/clips/${cid}/srt">⬇ Captions .srt</a>` : ""}
        <button class="btn sm" id="openF">📁 Show in folder</button>
        <button class="btn sm danger" id="delOut" title="Delete this rendered file from your PC to free space (you can re-render any time)">🗑 Delete file</button>
        <span class="muted small grow" style="text-align:right">${r.width}×${r.height} · ${r.fps}fps · ${(+r.duration).toFixed(1)}s${r.cuts ? ` (${r.cuts + 1} parts stitched)` : ""} · ${(r.size / 1048576).toFixed(1)} MB · ${r.seconds}s render</span>
      </div>`;
    $("#openF").onclick = () => api("POST", "/api/open-folder", { path: r.file }).catch(fail);
    $("#delOut").onclick = async () => {
      if (!confirm(`Delete ${r.name} (${(r.size / 1048576).toFixed(1)} MB) from your PC? You can re-render it any time.`)) return;
      try { const x = await api("POST", "/api/storage/clear", { what: "clip_export", project: pid, clip: cid }); C.render = null; renderResult(); renderYT(); toast(`Freed ${GB(x.freed)} 🧹`); }
      catch (e) { fail(e); }
    };
  }
  $("#renderBtn").onclick = async () => {
    const b = $("#renderBtn"); b.disabled = true;
    await api("PATCH", `/api/projects/${pid}/clips/${cid}`, { settings: st, words, title: $("#ctitle").value }).catch(() => {});
    if (st.captions.enabled !== false && !words.length && !confirm("This clip has no captions yet. Render without captions?")) { b.disabled = false; return; }
    try {
      const j = await api("POST", `/api/projects/${pid}/clips/${cid}/render`);
      const prog = $("#renderProg"); prog.classList.remove("hidden");
      const iv = setInterval(() => { const jj = Jobs.watch.get(j.id)?.job; if (jj) { $("i", prog).style.width = jj.progress + "%"; $("div.muted", prog).textContent = jj.message; } }, 300);
      S.cleanup.push(() => clearInterval(iv));
      Jobs.add(j, async jj => {
        clearInterval(iv); b.disabled = false; prog.classList.add("hidden");
        if (jj.status === "done") { C.render = jj.result; renderResult(); toast("Video ready 🚀"); }
      });
    } catch (e) { fail(e); b.disabled = false; }
  };

  let META = C.meta;
  function renderMeta() {
    if (!META) return;
    $("#metaBox").innerHTML = `
      <label class="f">Title ideas (click to use)</label>
      <div class="titlesug">${META.titles.map(t => `<button data-t="${esc(t)}">${esc(t)}</button>`).join("")}</div>
      <label class="f">Title <span class="muted" id="tLen"></span></label><input class="input" id="mTitle" maxlength="100" value="${esc(META.title)}">
      <label class="f">Description</label><textarea class="input" id="mDesc" rows="7">${esc(META.description)}</textarea>
      <label class="f">Tags (comma separated)</label><input class="input" id="mTags" value="${esc(META.tags.join(", "))}">
      <label class="f">📸 Instagram Reel caption <span class="muted" id="igLen"></span></label><textarea class="input" id="mIG" rows="6">${esc(META.ig_caption || "")}</textarea>
      <div class="row wrap" style="margin-top:10px"><button class="btn sm" data-copy="mTitle">📋 Copy title</button><button class="btn sm" data-copy="mDesc">📋 Copy description</button><button class="btn sm" data-copy="mTags">📋 Copy tags</button><button class="btn sm" data-copy="mIG">📋 Copy Reel caption</button></div>`;
    const upd = () => {
      META.title = $("#mTitle").value; META.description = $("#mDesc").value; META.ig_caption = $("#mIG").value;
      META.tags = $("#mTags").value.split(",").map(s => s.trim()).filter(Boolean);
      $("#tLen").textContent = `${META.title.length}/100`;
      $("#igLen").textContent = `${META.ig_caption.length}/2200 · ${(META.ig_caption.match(/#\w+/g) || []).length} hashtags`;
      saveMeta();
    };
    $("#metaBox").oninput = upd; upd();
    $("#metaBox").onclick = e => {
      if (e.target.dataset.t) { $("#mTitle").value = e.target.dataset.t; upd(); }
      if (e.target.dataset.copy) { navigator.clipboard.writeText($("#" + e.target.dataset.copy).value); toast("Copied 📋"); }
    };
  }
  const saveMeta = debounce(() => api("PATCH", `/api/projects/${pid}/clips/${cid}`, { meta: META }).catch(() => {}), 600);
  $("#genMeta").onclick = async () => {
    try { const m = await api("POST", `/api/projects/${pid}/clips/${cid}/metadata`); META = { ...m, title: META?.title && !confirm("Replace your current title?") ? META.title : m.title }; renderMeta(); }
    catch (e) { fail(e); }
  };
  renderMeta();

  $("#thumbBtn").onclick = async () => {
    const b = $("#thumbBtn"); b.disabled = true;
    try {
      const r = await api("POST", `/api/projects/${pid}/clips/${cid}/thumbnail`, { t: Math.max(0, ev.currentTime - C.start), text: st.effects.hook_text || $("#ctitle").value }, true);
      const url = URL.createObjectURL(await r.blob());
      $("#thumbBox").innerHTML = `<img src="${url}" style="max-width:100%;max-height:340px;border-radius:10px"><p class="muted small">Saved next to your video in the exports folder.</p>`;
      C.thumbnail = true;
    } catch (e) { fail(e); } finally { b.disabled = false; }
  };

  async function renderYT() {
    const card = $("#ytCard");
    const [y, ig] = await Promise.all([api("GET", "/api/youtube/status").catch(() => ({})), api("GET", "/api/instagram/status").catch(() => ({}))]);
    const posted = C.posts || {};
    if (!posted.youtube && C.upload) posted.youtube = C.upload;
    const done = Object.entries(posted).map(([k, r]) => `<div class="note" style="margin:6px 0">${PLAT[k]?.icon || ""} Posted to ${PLAT[k]?.name || k} ✓ <a target="_blank" href="${esc(r.url)}">${esc(r.url)}</a></div>`).join("");
    if (!y.connected && !ig.connected) {
      card.innerHTML = `<h3>📤 Post to YouTube &amp; Instagram</h3>${done}<p class="muted small">Connect your accounts once (Settings) to post straight from here or let Auto Pilot do it. You can always download the MP4 and post it yourself.</p>
        <button class="btn" id="ytSet">Connect accounts…</button>`;
      $("#ytSet").onclick = () => openSettings();
      return;
    }
    card.innerHTML = `<div class="row"><h3 class="grow">📤 Post this clip</h3><a class="btn sm" href="#/posts">Open 📤 Posts</a></div>${done}
      <label class="switch"><span>▶ YouTube Shorts ${y.connected ? `<span class="muted small">(${esc(y.channel?.title || "")})</span>` : '<span class="muted small">— not connected</span>'}</span><input type="checkbox" id="pYT" ${y.connected ? "checked" : "disabled"}></label>
      <label class="switch"><span>📸 Instagram Reel ${ig.connected ? `<span class="muted small">(@${esc(ig.username || "")})</span>` : '<span class="muted small">— not connected</span>'}</span><input type="checkbox" id="pIG" ${ig.connected ? "checked" : "disabled"}></label>
      <div class="grid2">
        <div><label class="f">YouTube visibility</label><select class="input" id="ytPriv"><option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option></select></div>
        <div><label class="f">When</label><input class="input" type="datetime-local" id="ytWhen" title="Leave empty to post right away"></div>
      </div>
      <p class="muted small">Uses the title, description, tags and Reel caption above (✨ Suggest fills them). Leave “When” empty to post right away.</p>
      <button class="btn pink" id="ytGo" ${C.render ? "" : "disabled"}>🚀 Post</button> ${C.render ? "" : '<span class="muted small">Render first</span>'}`;
    $("#ytGo").onclick = async () => {
      if (!META) { const m = await api("POST", `/api/projects/${pid}/clips/${cid}/metadata`); META = m; renderMeta(); }
      const platforms = [];
      if ($("#pYT").checked) platforms.push("youtube");
      if ($("#pIG").checked) platforms.push("instagram");
      if (!platforms.length) return toast("Tick YouTube and/or Instagram", true);
      const whenStr = $("#ytWhen").value, when = whenStr ? Math.floor(new Date(whenStr).getTime() / 1000) : null;
      if (when && when < Date.now() / 1000 + 60) return toast("Pick a time in the future (or leave it empty)", true);
      const names = platforms.map(p => PLAT[p].name).join(" + ");
      if (!confirm(`Post "${META.title}" to ${names} ${when ? "on " + new Date(when * 1000).toLocaleString() : "now"}?`)) return;
      try {
        await api("POST", "/api/posts", { project: pid, clip: cid, platforms, title: META.title, description: META.description,
          tags: META.tags, caption: META.ig_caption || META.title, privacy: $("#ytPriv").value, when, review: false });
        toast(when ? "Scheduled 🗓 — see 📤 Posts" : "Posting… 🚀 — progress in 📤 Posts");
      } catch (e) { fail(e); }
    };
  }

  syncForm(); renderWords(); renderMeta();
  setTimeout(() => { pv.resize(); drawScrub(); }, 50);
}

// ============================================================ SETTINGS
async function openSettings() {
  const [y, ig] = await Promise.all([api("GET", "/api/youtube/status").catch(() => ({})), api("GET", "/api/instagram/status").catch(() => ({}))]);
  const sys = S.sys || {};
  const m = modal(`
    <h2>Settings</h2>
    <div class="sep"></div>
    <h3>▶ YouTube channel</h3>
    ${y.connected ? `<div class="note">Connected to <b>${esc(y.channel?.title || "your channel")}</b>.</div>
      <div class="row" style="margin-top:10px"><button class="btn sm danger" id="ytDis">Disconnect</button></div>` : `
      <p class="muted small">One-time setup (≈5 min, free). Google requires your own API key so uploads come from <i>your</i> account:</p>
      <ol class="steps small">
        <li>Open <a href="https://console.cloud.google.com/" target="_blank">Google Cloud Console</a> → create a project.</li>
        <li>APIs &amp; Services → Library → enable <b>YouTube Data API v3</b>.</li>
        <li>OAuth consent screen → External → add your Gmail as a <b>Test user</b>.</li>
        <li>Credentials → Create credentials → OAuth client ID → <b>Desktop app</b> → Download JSON.</li>
        <li>Upload that JSON below, then click Connect and log in.</li>
      </ol>
      <div class="row wrap"><button class="btn sm" id="ytJson">${y.client_secret ? "✓ Client JSON added — replace" : "⬆ Upload client JSON"}</button>
      <input type="file" id="ytJsonF" accept=".json" hidden>
      <button class="btn sm primary" id="ytCon" ${y.client_secret ? "" : "disabled"}>Connect &amp; log in</button></div>`}
    <details style="margin-top:8px"><summary class="small" style="cursor:pointer">💡 YouTube tips: stay logged in &amp; post publicly</summary>
      <ul class="steps small">
        <li><b>Stay logged in:</b> in Google Cloud → OAuth consent screen, click <b>Publish app</b> (“In production”). In “Testing” mode Google logs you out every 7 days. You’ll see an “unverified app” warning for your own app — that's fine, click Advanced → Continue.</li>
        <li><b>Public posts:</b> Google keeps videos uploaded by new personal API projects <b>private</b> until the project passes YouTube's free <a href="https://support.google.com/youtube/contact/yt_api_form" target="_blank">API audit</a>. Until then, auto-posts arrive as private — make them public in YouTube Studio with one click.</li>
        <li><b>Daily limit:</b> about 6 uploads a day on the free quota. You can request more in Google Cloud.</li>
      </ul></details>
    <div class="sep"></div>
    <h3>📸 Instagram Reels</h3>
    ${ig.connected ? `<div class="note">Connected to <b>@${esc(ig.username || "")}</b> (${esc((ig.account_type || "").toLowerCase() || "professional")})${ig.quota_total ? ` · ${ig.quota_used ?? 0}/${ig.quota_total} posts used in the last 24h` : ""}.</div>
      ${ig.warning ? `<div class="note warn" style="margin-top:6px">${esc(ig.warning)}</div>` : ""}
      <div class="row" style="margin-top:10px"><button class="btn sm danger" id="igDis">Disconnect</button></div>` : `
      <p class="muted small">One-time setup (≈10 min, free). Meta requires a Professional account and your own app:</p>
      <ol class="steps small">
        <li>In the Instagram app: Settings → <b>Account type and tools</b> → switch to a <b>Creator</b> (or Business) account.</li>
        <li>Open <a href="https://developers.facebook.com/apps/" target="_blank">Meta for Developers</a> → <b>Create app</b> → use case <b>“Manage messaging &amp; content on Instagram”</b>.</li>
        <li>In the app: <b>Instagram → API setup with Instagram login</b> → <b>Add account</b> and log in with your Instagram (accept the tester invite if asked: Instagram → Settings → Website permissions → Apps and websites).</li>
        <li>Click <b>Generate token</b> next to your account and copy it.</li>
        <li>Paste it below and click Connect. ClipMasterMax refreshes it automatically (it lasts 60 days).</li>
      </ol>
      <div class="row"><input class="input grow" id="igTok" placeholder="Paste your Instagram access token (IGAA…)" type="password" autocomplete="off"><button class="btn sm primary" id="igCon">Connect</button></div>
      <p class="muted small">The token stays on this PC only (data folder). Limit: ~50 Reels a day. Scheduled Reels post from this app, so keep it running at the scheduled time.</p>`}
    <div class="sep"></div>
    <h3>💻 System</h3>
    <p class="small muted">FFmpeg ${esc(sys.ffmpeg)} · Video export: ${sys.nvenc ? "NVIDIA NVENC (fast)" : "CPU"} · Speech AI: ${sys.cuda ? "GPU (CUDA)" : "CPU"} · default model <b>${esc(sys.whisper_default)}</b> · yt-dlp ${sys.ytdlp ? "✓" : "missing"}</p>
    <p class="small muted">Data folder: <span class="mono">${esc(sys.data_dir)}</span></p>
    <button class="btn sm" id="openExp">📁 Open exports folder</button>`);
  $("#openExp", m).onclick = () => api("POST", "/api/open-folder", {}).catch(fail);
  if (ig.connected) {
    $("#igDis", m).onclick = async () => { if (!confirm("Disconnect Instagram?")) return; await api("POST", "/api/instagram/disconnect"); openSettings(); };
  } else {
    $("#igCon", m).onclick = async () => {
      const b = $("#igCon", m); b.disabled = true; b.innerHTML = '<span class="spin"></span>';
      try { const r = await api("POST", "/api/instagram/connect", { token: $("#igTok", m).value }); toast(`Instagram connected: @${r.username} ✅`); openSettings(); loadSys(); }
      catch (er) { fail(er); b.disabled = false; b.textContent = "Connect"; }
    };
  }
  if (y.connected) {
    $("#ytDis", m).onclick = async () => { await api("POST", "/api/youtube/disconnect"); closeModal(); loadSys(); };
  } else {
    $("#ytJson", m).onclick = () => $("#ytJsonF", m).click();
    $("#ytJsonF", m).onchange = async e => {
      const fd = new FormData(); fd.append("file", e.target.files[0]);
      try { await api("POST", "/api/youtube/client-secret", fd); toast("Client JSON saved"); openSettings(); } catch (er) { fail(er); }
    };
    $("#ytCon", m).onclick = async () => {
      try {
        const j = await api("POST", "/api/youtube/connect");
        toast("A Google login page opened in your browser…");
        Jobs.add(j, jj => { if (jj.status === "done") { toast("YouTube connected ✅"); loadSys(); closeModal(); } });
      } catch (er) { fail(er); }
    };
  }
}

boot();
