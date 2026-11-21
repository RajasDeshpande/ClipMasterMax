// Live canvas preview. Mirrors app/core/captions.py + render.py closely so what
// you see is what gets rendered. ("Exact HD frame" renders through FFmpeg for 100% truth.)

export const ASPECTS = { "9:16": [1080, 1920], "1:1": [1080, 1080], "4:5": [1080, 1350], "16:9": [1920, 1080] };
const DEVA = /[ऀ-ॿ]/;
const PUNCT = /[.,!?;:"“”‘’…।॥()\[\]]/g;
const SENT_END = /[.?!।…]$/;

export let FONTS = {};
export let BAD = new Set();

export function setFonts(fonts) {
  FONTS = fonts;
  const css = Object.entries(fonts).filter(([, f]) => f.file)
    .map(([k, f]) => `@font-face{font-family:"cm-${k}";src:url("/fonts/${f.file}");}`).join("\n");
  const el = document.createElement("style");
  el.textContent = css;
  document.head.appendChild(el);
  Object.entries(fonts).forEach(([k, f]) => { if (f.file) document.fonts.load(`40px "cm-${k}"`).catch(() => {}); });
}
export function setBadWords(list) { BAD = new Set(list || []); }

// ---------------- sound effects (mirrors core/sfx.py + render.py sfx_events) ----------------
export const TONE_FREQ = { beep: 1000, high: 2000 };
let ACTX = null;
const SFX_BUF = {};

// Create/resume the AudioContext. Call from a user gesture (play click) because of autoplay rules.
export function unlockAudio() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  if (!ACTX) ACTX = new AC();
  if (ACTX.state === "suspended") ACTX.resume().catch(() => {});
  return ACTX;
}
export function loadSfx(name) {
  if (!ACTX || !name || name === "none" || name === "mute") return Promise.resolve(null);
  if (!SFX_BUF[name]) {
    SFX_BUF[name] = fetch(`/api/sfx/${encodeURIComponent(name)}`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.arrayBuffer(); })
      .then(ab => new Promise((res, rej) => ACTX.decodeAudioData(ab, res, rej)))
      .catch(e => { delete SFX_BUF[name]; console.warn("sfx load failed", name, e); return null; });
  }
  return SFX_BUF[name];
}

// Same rule as captions.bleep_ranges() in the backend.
export function isSwear(word) {
  const w = String(word || "");
  const core = w.replace(/[^\p{L}\p{N}_ऀ-ॿ]/gu, "").toLowerCase();
  return BAD.has(core) || (w.includes("*") && core.length >= 1);
}
// -> [[start, end], ...] clip-relative seconds
export function bleepRanges(words, len) {
  const out = [];
  for (const w of words || []) {
    if (isSwear(w.w) && w.s >= 0 && w.s < len) out.push([Math.max(0, w.s - 0.03), Math.min(len, w.e + 0.03)]);
  }
  return out;
}

export function cssFont(key) {
  const f = FONTS[key];
  if (!f) return "'cm-poppins_black'";
  return f.file ? `'cm-${key}'` : `'${f.family}'`;
}

// ---------------- shared caption logic (keep in sync with captions.py) ----------------
export function transformWord(w, st) {
  let t = w;
  if (st.censor) {
    const core = t.replace(/[^\p{L}\p{N}_ऀ-ॿ]/gu, "").toLowerCase();
    if (BAD.has(core)) t = t ? t[0] + "*".repeat(Math.max(2, core.length - 1)) : t;
  }
  if (st.strip_punct) t = t.replace(PUNCT, "");
  if (st.uppercase) t = t.toUpperCase();
  return t;
}

export function chunkWords(words, st) {
  const maxW = Math.max(1, +st.max_words || 3);
  const maxC = Math.max(6, +st.max_chars_line || 18) * 2;
  const chunks = []; let cur = [];
  for (const w of words) {
    if (!(w.w || "").trim()) continue;
    if (cur.length) {
      const chars = cur.reduce((a, x) => a + x.w.length + 1, 0);
      const gap = w.s - cur[cur.length - 1].e;
      const prev = cur[cur.length - 1].w;
      if (cur.length >= maxW || chars + w.w.length > maxC || gap > 0.6 || SENT_END.test(prev) || (prev.endsWith(",") && cur.length >= 2)) {
        chunks.push(cur); cur = [];
      }
    }
    cur.push(w);
  }
  if (cur.length) chunks.push(cur);
  return chunks;
}

export function splitLines(tokens, maxLine) {
  const total = tokens.reduce((a, t) => a + t.length, 0) + Math.max(0, tokens.length - 1);
  if (total <= maxLine || tokens.length < 2) return [tokens.map((_, i) => i)];
  let best = null, bk = 1;
  for (let k = 1; k < tokens.length; k++) {
    const a = tokens.slice(0, k).reduce((s, t) => s + t.length, 0) + k - 1;
    const b = tokens.slice(k).reduce((s, t) => s + t.length, 0) + tokens.length - k - 1;
    const c = Math.max(a, b);
    if (best === null || c < best) { best = c; bk = k; }
  }
  return [[...Array(bk).keys()], [...Array(tokens.length - bk).keys()].map(i => i + bk)];
}

export function chunkTiming(chunks, len) {
  return chunks.map((ch, i) => {
    const st = ch[0].s; let en = ch[ch.length - 1].e;
    const nxt = i + 1 < chunks.length ? chunks[i + 1][0].s : len;
    en = (nxt - en < 0.5) ? nxt : Math.min(nxt, en + 0.25);
    if (en - st < 0.25) en = nxt > st ? Math.min(nxt, st + 0.25) : st + 0.25;
    return [Math.max(0, st), Math.min(len, en)];
  });
}

function zoomAt(t, peaks, strength) {
  if (!peaks || !peaks.length || !strength) return 1;
  let s = 0;
  for (const p of peaks) {
    const a = Math.max(0, p - 0.12);
    if (t > a && t < p) s += (t - a) / Math.max(0.01, p - a);
    if (t >= p && t <= p + 0.9) s += Math.pow(1 - (t - p) / 0.9, 2);
  }
  return 1 + strength * Math.min(1, s);
}

function hexA(hex, a) {
  const h = (hex || "#000000").replace("#", "");
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

// ---------------- renderer ----------------
export class Preview {
  constructor(canvas, video) {
    this.c = canvas; this.ctx = canvas.getContext("2d"); this.v = video;
    this.clip = null; this.settings = null; this.words = []; this.src = { width: 1920, height: 1080 };
    this.running = false; this.loop = true; this.onTime = null;
    this._raf = this._raf.bind(this);
    // live sfx state
    this._bleeps = []; this._lastT = null; this._muted = false; this._wasMuted = false; this._osc = null;
    video.addEventListener("pause", () => { this._audioOff(); this._lastT = null; });
  }
  set(clip, settings, words, src) {
    this.clip = clip; this.settings = settings; this.words = words || []; this.src = src || this.src;
    this._bleeps = clip ? bleepRanges(this.words, clip.end - clip.start) : [];
    this._preload();
    this.resize();
  }

  // ---------------- live sound effects ----------------
  _vol() { const v = +this.settings?.effects?.sfx_volume; return isNaN(v) ? 0.8 : Math.max(0, Math.min(1.5, v)); }
  _preload() {
    if (!ACTX) return;
    const e = this.settings?.effects || {};
    const names = new Set((e.sfx_marks || []).map(m => m.sound));
    if (e.hype_sfx && e.hype_sfx !== "none") names.add(e.hype_sfx);
    if (e.bleep && !TONE_FREQ[e.bleep_sound] && e.bleep_sound !== "mute") names.add(e.bleep_sound || "beep");
    names.forEach(n => loadSfx(n));
  }
  // Play one sound now (soundboard, previews, markers). Safe to call from a click handler.
  async playSfx(name, vol) {
    if (!unlockAudio()) return;
    this._preload();
    const buf = await loadSfx(name);
    if (!buf || !ACTX) return;
    const src = ACTX.createBufferSource(), g = ACTX.createGain();
    src.buffer = buf; g.gain.value = vol ?? this._vol();
    src.connect(g).connect(ACTX.destination);
    src.start();
  }
  // Forget the previous playhead so nothing between it and "now" fires (after adding a mark at the playhead).
  syncAudioTime() { this._lastT = this.clip ? this.v.currentTime - this.clip.start : null; }
  _toneOn(freq) {
    if (!ACTX) return;
    if (this._osc && this._osc.freq === freq) return;
    this._toneOff();
    const o = ACTX.createOscillator(), g = ACTX.createGain();
    o.type = "sine"; o.frequency.value = freq; g.gain.value = 0.15;
    o.connect(g).connect(ACTX.destination); o.start();
    this._osc = { o, g, freq };
  }
  _toneOff() {
    if (!this._osc) return;
    try { this._osc.o.stop(); this._osc.o.disconnect(); this._osc.g.disconnect(); } catch {}
    this._osc = null;
  }
  _mute(on) {
    if (on && !this._muted) { this._wasMuted = this.v.muted; this.v.muted = true; this._muted = true; }
    else if (!on && this._muted) { this.v.muted = this._wasMuted; this._muted = false; }
  }
  _audioOff() { this._toneOff(); this._mute(false); }
  _audioTick() {
    const v = this.v, clip = this.clip, eff = this.settings?.effects;
    if (!clip || !eff || v.paused || v.seeking) { this._audioOff(); this._lastT = null; return; }
    const t = v.currentTime - clip.start, prev = this._lastT;
    this._lastT = t;
    // a "crossing" only counts during normal playback (no seeks / loop jumps)
    const crossed = (x) => prev != null && t >= prev && t - prev <= 0.5 && x > prev && x <= t;
    // bleep: mute the game audio inside swear ranges, tone or one-shot on top
    const snd = eff.bleep_sound || "beep";
    const inside = eff.bleep && this._bleeps.some(([a, b]) => t >= a && t < b);
    this._mute(!!inside);
    if (inside && TONE_FREQ[snd]) this._toneOn(TONE_FREQ[snd]); else this._toneOff();
    if (eff.bleep && !TONE_FREQ[snd] && snd !== "mute") {
      for (const [a] of this._bleeps) if (crossed(a)) this.playSfx(snd);
    }
    // auto sound on hype moments (same lead-in as render.py)
    const hype = eff.hype_sfx || "none";
    const hypeTimes = clip.hype?.length ? clip.hype : clip.punch;
    if (hype !== "none" && hypeTimes?.length) {
      const lead = hype === "boom" || hype === "horn" ? 0.05 : 0.25;
      for (const p of hypeTimes) if (crossed(Math.max(0, p - lead))) this.playSfx(hype);
    }
    // manual soundboard markers
    for (const m of eff.sfx_marks || []) if (m && m.sound && crossed(+m.t)) this.playSfx(m.sound);
  }
  resize() {
    const [W, H] = ASPECTS[this.settings?.layout?.aspect || "9:16"] || ASPECTS["9:16"];
    this.W = W; this.H = H;
    const box = this.c.parentElement;
    const maxW = box.parentElement.clientWidth || 380;
    const maxH = window.innerHeight - 290;
    let cw = Math.min(maxW, 420), ch = cw * H / W;
    if (ch > maxH && maxH > 300) { ch = maxH; cw = ch * W / H; }
    box.style.width = cw + "px"; box.style.height = ch + "px";
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.c.width = Math.round(cw * dpr); this.c.height = Math.round(ch * dpr);
    this.k = this.c.width / W;
    this.draw();
  }
  start() { if (!this.running) { this.running = true; requestAnimationFrame(this._raf); } }
  stop() { this.running = false; this._audioOff(); this._lastT = null; }
  // clip time -> final video time (cut parts removed)
  outTime(t) {
    let o = t;
    for (const c of this.clip?.cuts || []) o -= Math.max(0, Math.min(t, c.e) - c.s);
    return Math.max(0, o);
  }
  outLen() { return this.outTime(this.clip.end - this.clip.start); }
  _raf() {
    if (!this.running) return;
    if (this.clip && !this.v.paused && this.v.currentTime >= this.clip.end - 0.02) {
      if (this.loop) this.v.currentTime = this.clip.start; else this.v.pause();
    }
    // jump over cut-out parts while playing, exactly like the final video
    if (this.clip && !this.v.paused && !this.v.seeking) {
      const t = this.v.currentTime - this.clip.start;
      const cut = (this.clip.cuts || []).find(c => t >= c.s && t < c.e - 0.01);
      if (cut) {
        if (cut.e >= this.clip.end - this.clip.start - 0.02) { if (this.loop) this.v.currentTime = this.clip.start; else this.v.pause(); }
        else this.v.currentTime = this.clip.start + cut.e;
      }
    }
    this._audioTick();
    this.draw();
    if (this.onTime) this.onTime(this.v.currentTime - (this.clip?.start || 0));
    requestAnimationFrame(this._raf);
  }

  draw() {
    const { ctx, W, H, k } = this;
    if (!this.settings || !k) return;
    const s = this.settings, lay = s.layout, eff = s.effects;
    const t = Math.max(0, this.v.currentTime - (this.clip?.start || 0));
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";   // sharper zoomed facecam/gameplay
    ctx.save();
    ctx.setTransform(k, 0, 0, k, 0, 0);
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
    const z = eff.punch_zoom ? zoomAt(t, this.clip?.punch, +eff.punch_strength || 0) : 1;
    if (z !== 1) { ctx.translate(W / 2, H / 2); ctx.scale(z, z); ctx.translate(-W / 2, -H / 2); }
    if (this.v.readyState >= 2) this.drawLayout(lay);
    ctx.setTransform(k, 0, 0, k, 0, 0);
    const to = this.outTime(t), olen = this.outLen();
    if (eff.progress_bar) {
      const bh = Math.max(6, Math.round(H * 0.006 / 2) * 2);
      ctx.fillStyle = eff.bar_color || "#FFE500";
      ctx.fillRect(0, H - bh, W * Math.min(1, to / olen), bh);
    }
    if (s.captions?.enabled !== false) this.drawCaptions(t);
    this.drawPops(t);
    this.drawHook(to); this.drawMark();
    if (eff.fade) {
      const len = olen, t2 = to;
      let a = 0;
      if (t2 < 0.15) a = 1 - t2 / 0.15; else if (t2 > len - 0.3) a = (t2 - (len - 0.3)) / 0.3;
      if (a > 0) { ctx.fillStyle = `rgba(0,0,0,${Math.min(1, a)})`; ctx.fillRect(0, 0, W, H); }
    }
    ctx.restore();
  }

  drawLayout(lay) {
    const { ctx, W, H, v } = this;
    const sw = v.videoWidth || this.src.width, sh = v.videoHeight || this.src.height;
    const mode = lay.mode || "blur";
    const zoom = Math.max(mode === "crop" || mode === "facecam" ? 1 : 0.5, +lay.zoom || 1);
    const num = (x, d) => (x === undefined || x === null || isNaN(+x)) ? d : +x;
    const cx = num(lay.crop_x, 0.5), fy = num(lay.fg_y, 0.5);
    const cover = (dw, dh, z, ox, dx, dy) => {
      const sc = Math.max(dw / sw, dh / sh) * z;
      const w2 = sw * sc, h2 = sh * sc;
      const offx = (w2 - dw) * ox, offy = (h2 - dh) * 0.5;
      ctx.save(); ctx.beginPath(); ctx.rect(dx, dy, dw, dh); ctx.clip();
      ctx.drawImage(v, dx - offx, dy - offy, w2, h2); ctx.restore();
    };
    if (mode === "crop") { cover(W, H, zoom, cx, 0, 0); return; }
    if (mode === "fit") {
      const sc = Math.min(W / sw, H / sh), w2 = sw * sc, h2 = sh * sc;
      ctx.drawImage(v, (W - w2) / 2, (H - h2) * fy, w2, h2); return;
    }
    if (mode === "facecam") {
      const fc = lay.facecam || { x: 0, y: 0, w: .3, h: .3 };
      const Hc = Math.round(H * Math.min(.6, Math.max(.2, +lay.cam_ratio || .38)) / 2) * 2, Hg = H - Hc;
      const csw = sw * fc.w, csh = sh * fc.h, sc = Math.max(W / csw, Hc / csh);
      // same rule as render.py: zoom is measured on the ORIGINAL recording; small webcams aren't
      // stretched past the "Facecam size" limit, they sit centred on a blurred copy instead
      const CAP = { sharp: 2.4, big: 3.0, fill: 99 }[lay.cam_fit || "sharp"] ?? 2.4;
      const srcW = this.src?.width || sw, srcH = this.src?.height || sh;
      const fillUp = Math.max(W / (srcW * fc.w), Hc / (srcH * fc.h));
      ctx.save(); ctx.beginPath(); ctx.rect(0, 0, W, Hc); ctx.clip();
      if (fillUp <= CAP) {
        const w2 = csw * sc, h2 = csh * sc;
        ctx.drawImage(v, sw * fc.x, sh * fc.y, csw, csh, (W - w2) / 2, (Hc - h2) / 2, w2, h2);
      } else {
        const w2 = csw * sc, h2 = csh * sc;
        ctx.filter = `blur(${Math.round(W / 40)}px) brightness(0.86) saturate(1.1)`;
        ctx.drawImage(v, sw * fc.x, sh * fc.y, csw, csh, (W - w2) / 2, (Hc - h2) / 2, w2, h2);
        ctx.filter = "none";
        const k = CAP / fillUp * sc;           // CAP-times the original size, in preview pixels
        const fw2 = Math.min(W, csw * k), fh2 = Math.min(Hc, csh * k);
        const sx = sw * fc.x + (csw * k - fw2) / 2 / k, sy = sh * fc.y + (csh * k - fh2) / 2 / k;
        ctx.drawImage(v, sx, sy, fw2 / k, fh2 / k, (W - fw2) / 2, (Hc - fh2) / 2, fw2, fh2);
      }
      ctx.restore();
      cover(W, Hg, zoom, cx, 0, Hc); return;
    }
    // blur
    ctx.save(); ctx.filter = `blur(${Math.round(W / 40)}px) brightness(0.86) saturate(1.2)`;
    const sc = Math.max(W / sw, H / sh) * 1.1;
    ctx.drawImage(v, (W - sw * sc) / 2, (H - sh * sc) / 2, sw * sc, sh * sc); ctx.restore();
    const fw = W * zoom, fh = sh * fw / sw, cw = Math.min(fw, W), chh = Math.min(fh, H);
    const sx = (fw - cw) * cx / fw * sw, sy = (fh - chh) * 0.5 / fh * sh;
    ctx.drawImage(v, sx, sy, cw / fw * sw, chh / fh * sh, (W - cw) / 2, (H - chh) * fy, cw, chh);
  }

  _font(text, st) {
    let key = st.font;
    const f = FONTS[key];
    if (DEVA.test(text) && f && !f.devanagari) key = "poppins_black";
    return cssFont(key);
  }

  drawText(lines, opts) {
    // lines: [[{text, color, scale, alpha, fill (0..1 karaoke)}]] centred at (cx, cy)
    const { ctx } = this;
    const { size, family, outline, outlineColor, shadow, shadowColor, box, boxColor, boxOpacity, blur, cx, cy, spacing, scaleAll = 1, alphaAll = 1, karaokeBase } = opts;
    const lh = size * 1.18;
    const totalH = lh * lines.length;
    ctx.save();
    ctx.translate(cx, cy); ctx.scale(scaleAll, scaleAll); ctx.translate(-cx, -cy);
    ctx.globalAlpha = alphaAll;
    ctx.textBaseline = "middle"; ctx.lineJoin = "round"; ctx.miterLimit = 2;
    if ("letterSpacing" in ctx) ctx.letterSpacing = (spacing || 0) + "px";
    lines.forEach((ln, li) => {
      const y = cy - totalH / 2 + lh * (li + 0.5);
      const parts = ln.map(p => { ctx.font = `${size * (p.scale || 1)}px ${family}`; return { ...p, w: ctx.measureText(p.text).width }; });
      ctx.font = `${size}px ${family}`;
      const spW = ctx.measureText(" ").width;
      const total = parts.reduce((a, p) => a + p.w, 0) + spW * (parts.length - 1);
      let x = cx - total / 2;
      if (box) {
        const pad = Math.max(6, size * 0.18);
        ctx.fillStyle = hexA(boxColor, boxOpacity);
        ctx.fillRect(x - pad, y - size * 0.6 - pad * .4, total + pad * 2, size * 1.2 + pad * .8);
      }
      for (const p of parts) {
        ctx.font = `${size * (p.scale || 1)}px ${family}`;
        const px = x;
        ctx.globalAlpha = alphaAll * (p.alpha ?? 1);
        if (shadow > 0) {
          ctx.fillStyle = hexA(shadowColor, 0.45);
          if (outline > 0 && !box) { ctx.lineWidth = outline * 2; ctx.strokeStyle = hexA(shadowColor, 0.45); ctx.strokeText(p.text, px + shadow, y + shadow); }
          ctx.fillText(p.text, px + shadow, y + shadow);
        }
        if (outline > 0 && !box) {
          if (blur > 0) { ctx.shadowColor = outlineColor; ctx.shadowBlur = blur * 6; }
          ctx.lineWidth = outline * 2; ctx.strokeStyle = outlineColor; ctx.strokeText(p.text, px, y);
          ctx.shadowBlur = 0;
        }
        ctx.fillStyle = p.color; ctx.fillText(p.text, px, y);
        if (p.fill !== undefined && p.fill < 1) {
          // karaoke: re-paint unfilled part in base colour
          ctx.save(); ctx.beginPath(); ctx.rect(px + p.w * p.fill, y - size, p.w * (1 - p.fill) + 2, size * 2); ctx.clip();
          ctx.fillStyle = karaokeBase; ctx.fillText(p.text, px, y); ctx.restore();
        }
        x += p.w + spW;
      }
    });
    ctx.restore();
  }

  drawCaptions(t) {
    const st = this.settings.style, len = this.clip.end - this.clip.start;
    const words = this.words.filter(w => w.s >= 0 && w.s < len);
    if (!words.length) return;
    const chunks = chunkWords(words, st), timing = chunkTiming(chunks, len);
    const idx = timing.findIndex(([a, b]) => t >= a && t < b);
    if (idx < 0) return;
    let ch = chunks[idx]; const [cst, cen] = timing[idx];
    let tokens = ch.map(w => transformWord(w.w, st));
    const keep = tokens.map((x, i) => x.trim() ? i : -1).filter(i => i >= 0);
    ch = keep.map(i => ch[i]); tokens = keep.map(i => tokens[i]);
    if (!tokens.length) return;
    const groups = splitLines(tokens, +st.max_chars_line || 18);
    const mode = st.highlight_mode || "color";
    let ai = 0;
    for (let i = 0; i < ch.length; i++) if (t >= (i === 0 ? cst : ch[i].s)) ai = i;
    const lines = groups.map(g => g.map(i => {
      const p = { text: tokens[i], color: st.color };
      if (mode === "karaoke") {
        const ws = ch[i].s, we = i + 1 < ch.length ? ch[i + 1].s : cen;
        p.color = st.highlight; p.fill = Math.max(0, Math.min(1, (t - ws) / Math.max(0.01, we - ws)));
      } else if (mode !== "none" && i === ai) {
        p.color = st.highlight;
        if (mode === "grow") p.scale = 1.14;
      } else if (mode === "dim") p.alpha = 0.56;
      return p;
    }));
    const el = t - cst, anim = st.animation;
    let sc = 1, al = 1, dy = 0;
    if (anim === "pop") sc = el < 0.09 ? 0.8 + 0.2 * el / 0.09 : 1;
    else if (anim === "bounce") sc = el < 0.09 ? 0.7 + 0.42 * el / 0.09 : el < 0.17 ? 1.12 - 0.12 * (el - 0.09) / 0.08 : 1;
    else if (anim === "fade") al = Math.min(1, el / 0.12);
    else if (anim === "slide") { dy = 40 * Math.max(0, 1 - el / 0.12); al = Math.min(1, el / 0.08); }
    this.drawText(lines, {
      size: +st.size || 90, family: this._font(tokens.join(" "), st), outline: +st.outline || 0,
      outlineColor: st.outline_color, shadow: +st.shadow || 0, shadowColor: st.shadow_color || "#000",
      box: !!st.box, boxColor: st.box_color, boxOpacity: +st.box_opacity, blur: +st.blur || 0,
      cx: this.W / 2, cy: this.H * (+st.position) + dy, spacing: st.uppercase ? 1 : 0,
      scaleAll: sc, alphaAll: al, karaokeBase: st.color,
    });
  }

  drawHook(t) {
    const eff = this.settings.effects, text = (eff.hook_text || "").trim();
    if (!text) return;
    const len = this.clip.end - this.clip.start, d = +eff.hook_duration || len;
    if (t > Math.min(d, len)) return;
    const W = this.W / 1080, toks = text.split(/\s+/), groups = splitLines(toks, 22);
    const sc = t < 0.14 ? 0.6 + 0.4 * t / 0.14 : 1;
    const { ctx } = this, size = 84 * W, pad = 18 * W, cy = this.H * (+eff.hook_position || 0.15);
    const family = DEVA.test(text) ? cssFont("poppins_black") : cssFont("poppins_black");
    ctx.save(); ctx.translate(this.W / 2, cy); ctx.scale(sc, sc); ctx.translate(-this.W / 2, -cy);
    ctx.font = `${size}px ${family}`; ctx.textBaseline = "middle";
    const lh = size * 1.2, lines = groups.map(g => g.map(i => toks[i]).join(" "));
    const total = lh * lines.length;
    lines.forEach((ln, i) => {
      const w = ctx.measureText(ln).width, y = cy - total / 2 + lh * (i + .5);
      ctx.fillStyle = eff.hook_bg || "#E11D48";
      ctx.fillRect(this.W / 2 - w / 2 - pad, y - size * .6 - pad * .5, w + pad * 2, size * 1.2 + pad);
      ctx.fillStyle = eff.hook_color || "#fff"; ctx.textAlign = "center"; ctx.fillText(ln, this.W / 2, y);
    });
    ctx.restore();
  }

  // Text pop-ups (mirrors the "Pop" style in captions.py)
  drawPops(t) {
    const pops = [...(this.settings.effects?.text_pops || [])].filter(p => String(p.text || "").trim()).sort((a, b) => (+a.t || 0) - (+b.t || 0));
    if (!pops.length) return;
    const { ctx } = this, k1 = this.W / 1080, tilts = [-4, 3, -2, 4, -3];
    // same row assignment as captions.py pop_lanes(): overlapping pop-ups get their own row
    const ROWS = [0.36, 0.47, 0.25], ends = [-1, -1, -1];
    const lanes = pops.map(p => {
      const t0 = +p.t || 0, d = Math.max(0.5, +p.dur || 1.8);
      let lane = ends.findIndex(e => e <= t0);
      if (lane < 0) lane = ends.indexOf(Math.min(...ends));
      ends[lane] = t0 + d; return lane;
    });
    pops.forEach((p, i) => {
      const t0 = +p.t, d = Math.max(0.5, +p.dur || 1.8), el = t - t0;
      if (!(el >= 0 && el < d) || !String(p.text || "").trim()) return;
      const sc = el < 0.13 ? 0.3 + 0.88 * el / 0.13 : el < 0.23 ? 1.18 - 0.18 * (el - 0.13) / 0.1 : 1;
      const al = el > d - 0.2 ? Math.max(0, (d - el) / 0.2) : 1;
      let size = 112 * k1; const cx = this.W / 2, cy = this.H * (+p.y || ROWS[lanes[i]]);
      // same auto-shrink rule as the export (captions.py): long pop-ups must fit the frame width
      const estW = String(p.text).trim().length * size * 0.66;
      size *= Math.min(1, (this.W * 0.88) / Math.max(1, estW));
      ctx.save();
      ctx.globalAlpha = al;
      ctx.translate(cx, cy); ctx.rotate(-tilts[i % 5] * Math.PI / 180); ctx.scale(sc, sc);
      ctx.font = `${size}px ${cssFont("poppins_black")}`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.lineJoin = "round";
      if ("letterSpacing" in ctx) ctx.letterSpacing = "1px";
      ctx.fillStyle = "rgba(0,0,0,.4)"; ctx.fillText(p.text, 5 * k1, 5 * k1);
      ctx.lineWidth = 18 * k1; ctx.strokeStyle = "#000"; ctx.strokeText(p.text, 0, 0);
      ctx.fillStyle = p.color || "#FFE500"; ctx.fillText(p.text, 0, 0);
      ctx.restore();
    });
  }

  drawMark() {
    const eff = this.settings.effects, text = (eff.watermark || "").trim();
    if (!text) return;
    const { ctx } = this, size = 40 * this.W / 1080;
    const y = eff.hook_text?.trim() ? this.H * 0.955 : this.H * 0.05;
    ctx.save(); ctx.font = `${size}px ${cssFont("poppins_xb")}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.lineWidth = 4; ctx.strokeStyle = "rgba(0,0,0,.5)"; ctx.strokeText(text, this.W / 2, y);
    ctx.fillStyle = "rgba(255,255,255,.75)"; ctx.fillText(text, this.W / 2, y); ctx.restore();
  }
}
