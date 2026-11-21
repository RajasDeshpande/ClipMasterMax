"""Turns word timestamps + a style into an .ass subtitle file (burned in by libass).

The chunking/line-breaking logic is mirrored in static/js/captions.js so the
in-browser preview matches the final render.
"""
from __future__ import annotations

import re

from .presets import BAD_WORDS, DEVANAGARI_FALLBACK, FONTS

DEVA = re.compile(r"[ऀ-ॿ]")
PUNCT_STRIP = re.compile(r"[.,!?;:\"“”‘’…।॥()\[\]]")
SENT_END = re.compile(r"[.?!।…]$")


# ------------------------------------------------------------------ colours
def ass_color(hex_color: str, alpha: float = 1.0) -> str:
    """#RRGGBB + opacity -> &HAABBGGRR"""
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = int(round((1 - max(0.0, min(1.0, alpha))) * 255))
    return f"&H{a:02X}{b}{g}{r}".upper()


def ass_inline(hex_color: str) -> str:
    h = (hex_color or "#FFFFFF").lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


# ------------------------------------------------------------------ text transforms
def transform_word(w: str, style: dict) -> str:
    t = w
    if style.get("censor"):
        core = re.sub(r"[^\wऀ-ॿ]", "", t).lower()
        if core in BAD_WORDS:
            t = t[0] + "*" * max(2, len(core) - 1) if t else t
    if style.get("strip_punct"):
        t = PUNCT_STRIP.sub("", t)
    if style.get("uppercase"):
        t = t.upper()
    return t


def chunk_words(words: list[dict], style: dict) -> list[list[dict]]:
    max_words = max(1, int(style.get("max_words", 3)))
    max_chars = max(6, int(style.get("max_chars_line", 18))) * 2  # two lines max per chunk
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if not w.get("w", "").strip():
            continue
        if cur:
            chars = sum(len(x["w"]) + 1 for x in cur)
            gap = w["s"] - cur[-1]["e"]
            prev = cur[-1]["w"]
            if (len(cur) >= max_words or chars + len(w["w"]) > max_chars or gap > 0.6
                    or SENT_END.search(prev) or (prev.endswith(",") and len(cur) >= 2)):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def split_lines(tokens: list[str], max_line: int) -> list[list[int]]:
    """Return word-index groups per line (1 or 2 lines, balanced)."""
    total = sum(len(t) for t in tokens) + max(0, len(tokens) - 1)
    if total <= max_line or len(tokens) < 2:
        return [list(range(len(tokens)))]
    best, best_k = None, 1
    for k in range(1, len(tokens)):
        a = sum(len(t) for t in tokens[:k]) + k - 1
        b = sum(len(t) for t in tokens[k:]) + len(tokens) - k - 1
        cost = max(a, b)
        if best is None or cost < best:
            best, best_k = cost, k
    return [list(range(best_k)), list(range(best_k, len(tokens)))]


def chunk_timing(chunks: list[list[dict]], clip_len: float) -> list[tuple[float, float]]:
    out = []
    for i, ch in enumerate(chunks):
        st = ch[0]["s"]
        en = ch[-1]["e"]
        nxt = chunks[i + 1][0]["s"] if i + 1 < len(chunks) else clip_len
        if nxt - en < 0.5:
            en = nxt
        else:
            en = min(nxt, en + 0.25)
        if en - st < 0.25:
            en = min(nxt, st + 0.25) if nxt > st else st + 0.25
        out.append((max(0.0, st), min(clip_len, en)))
    return out


# ------------------------------------------------------------------ ASS build
def _font_for(text: str, font_key: str) -> dict:
    f = FONTS.get(font_key) or FONTS["poppins_black"]
    if DEVA.search(text) and not f["devanagari"]:
        return FONTS[DEVANAGARI_FALLBACK]
    return f


def _anim_tags(anim: str, x: int, y: int) -> str:
    if anim == "pop":
        return r"\fscx80\fscy80\t(0,90,\fscx100\fscy100)"
    if anim == "bounce":
        return r"\fscx70\fscy70\t(0,90,\fscx112\fscy112)\t(90,170,\fscx100\fscy100)"
    if anim == "fade":
        return r"\fad(120,0)"
    if anim == "slide":
        return rf"\move({x},{y + 40},{x},{y},0,120)\fad(80,0)"
    return ""


POP_ROWS = (0.36, 0.47, 0.25)  # vertical rows for pop-ups that are on screen at the same time


def pop_lanes(pops: list[dict]) -> list[int]:
    """Give each pop-up a row so pop-ups that overlap in time don't draw on top of each other.
    (Mirrored in static/js/preview.js -> popLanes.)"""
    ends = [-1.0] * len(POP_ROWS)
    out = []
    for p in pops:
        try:
            t0 = float(p.get("t", 0))
        except (TypeError, ValueError):
            t0 = 0.0
        d = max(0.5, float(p.get("dur", 1.8) or 1.8))
        lane = next((i for i, e in enumerate(ends) if e <= t0), min(range(len(ends)), key=lambda i: ends[i]))
        ends[lane] = t0 + d
        out.append(lane)
    return out


def build_ass(words: list[dict], settings: dict, W: int, H: int, clip_len: float) -> str:
    style = settings["style"]
    eff = settings.get("effects", {})
    scale = W / 1080.0
    size = int(round(style.get("size", 90) * scale))
    outline = round(style.get("outline", 6) * scale, 1)
    shadow = round(style.get("shadow", 0) * scale, 1)
    font = FONTS.get(style.get("font")) or FONTS["poppins_black"]
    box = bool(style.get("box"))
    color = style.get("color", "#FFFFFF")
    hl = style.get("highlight", "#FFE500")
    mode = style.get("highlight_mode", "color")
    anim = style.get("animation", "pop")
    blur = float(style.get("blur", 0) or 0)
    cx = W // 2
    cy = int(H * float(style.get("position", 0.7)))

    outline_col = ass_color(style.get("box_color", "#000000"), style.get("box_opacity", 0.6)) if box \
        else ass_color(style.get("outline_color", "#000000"))
    back_col = ass_color(style.get("shadow_color", "#000000"), 0.55)
    border_style = 3 if box else 1
    box_pad = max(6, int(size * 0.18)) if box else outline

    primary = ass_color(color)
    secondary = ass_color(color)
    if mode == "karaoke":
        primary, secondary = ass_color(hl), ass_color(color)

    hook_size = int(round(84 * scale))
    wm_size = int(round(40 * scale))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font['family']},{size},{primary},{secondary},{outline_col},{back_col},{-1 if font['bold'] else 0},0,0,0,"
        f"100,100,{1 if style.get('uppercase') else 0},0,{border_style},{box_pad if box else outline},{shadow},5,20,20,20,1",
        f"Style: Hook,Poppins Black,{hook_size},{ass_color(eff.get('hook_color', '#FFFFFF'))},{ass_color('#FFFFFF')},"
        f"{ass_color(eff.get('hook_bg', '#E11D48'))},{ass_color('#000000', 0.5)},0,0,0,0,100,100,0,0,3,{int(18 * scale)},0,5,40,40,20,1",
        f"Style: Mark,Poppins ExtraBold,{wm_size},{ass_color('#FFFFFF', 0.75)},{ass_color('#FFFFFF')},{ass_color('#000000', 0.5)},"
        f"{ass_color('#000000', 0.3)},0,0,0,0,100,100,1,0,1,{max(1.5, 2 * scale)},0,5,20,20,20,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # ---- hook title
    hook = (eff.get("hook_text") or "").strip()
    if hook:
        hy = int(H * float(eff.get("hook_position", 0.15)))
        hdur = float(eff.get("hook_duration", 3.0) or 0) or clip_len
        hf = "" if not DEVA.search(hook) else r"\fnPoppins Black"
        # wrap long hooks into 2 lines
        toks = hook.split()
        groups = split_lines(toks, 22)
        htext = r"\N".join(" ".join(toks[i] for i in g) for g in groups)
        lines.append(f"Dialogue: 2,{ts(0)},{ts(min(hdur, clip_len))},Hook,,0,0,0,,"
                     f"{{\\an5\\pos({cx},{hy}){hf}\\fscx60\\fscy60\\t(0,140,\\fscx100\\fscy100)}}{esc(htext)}")

    # ---- text pop-ups (manual + detected from the game screen)
    pops = sorted((p for p in (eff.get("text_pops") or []) if str(p.get("text", "")).strip()),
                  key=lambda p: float(p.get("t", 0) or 0))
    if pops:
        pop_size = int(round(112 * scale))
        lines.insert(lines.index("[Events]") - 1,
                     f"Style: Pop,Poppins Black,{pop_size},{ass_color('#FFE500')},{ass_color('#FFFFFF')},{ass_color('#000000')},"
                     f"{ass_color('#000000', 0.6)},0,0,0,0,100,100,1,0,1,{max(4, 9 * scale):.1f},{max(2, 5 * scale):.1f},5,40,40,20,1")
        lanes = pop_lanes(pops)
        for i, p in enumerate(pops):
            try:
                t0 = float(p["t"])
            except (TypeError, ValueError, KeyError):
                continue
            if not (0 <= t0 < clip_len):
                continue
            try:
                d = max(0.5, float(p.get("dur") or 1.8))
                py = int(H * float(p.get("y") or POP_ROWS[lanes[i]]))
            except (TypeError, ValueError):
                d, py = 1.8, int(H * POP_ROWS[lanes[i]])
            tilt = (-4, 3, -2, 4, -3)[i % 5]
            col = p.get("color") or "#FFE500"
            ptxt = esc(str(p["text"]).strip())
            pf = r"\fnPoppins Black" if DEVA.search(ptxt) else ""
            # shrink long pop-ups so they never run off the sides (Poppins Black ≈ 0.66 em per char)
            est_w = len(str(p["text"]).strip()) * pop_size * 0.66
            fs = int(pop_size * min(1.0, (W * 0.88) / max(1.0, est_w)))
            lines.append(
                f"Dialogue: 3,{ts(t0)},{ts(min(clip_len, t0 + d))},Pop,,0,0,0,,"
                f"{{\\an5\\pos({cx},{py}){pf}\\fs{fs}\\c{ass_inline(col)}\\frz{tilt}\\fscx30\\fscy30"
                f"\\t(0,130,\\fscx118\\fscy118)\\t(130,230,\\fscx100\\fscy100)\\fad(0,200)}}{ptxt}")

    # ---- watermark
    wm = (eff.get("watermark") or "").strip()
    if wm:
        wy = int(H * 0.05) if not hook else int(H * 0.955)
        lines.append(f"Dialogue: 1,{ts(0)},{ts(clip_len)},Mark,,0,0,0,,{{\\an5\\pos({cx},{wy})}}{esc(wm)}")

    # ---- captions
    if not settings.get("captions", {}).get("enabled", True) or not words:
        return "\n".join(lines) + "\n"

    words = [w for w in words if 0 <= w["s"] < clip_len]
    chunks = chunk_words(words, style)
    timing = chunk_timing(chunks, clip_len)
    max_line = int(style.get("max_chars_line", 18))
    hl_tag = ass_inline(hl)
    base_tag = ass_inline(color)
    blur_tag = rf"\blur{blur * scale:.1f}" if blur > 0 else ""

    for ch, (cst, cen) in zip(chunks, timing):
        tokens = [transform_word(w["w"], style) for w in ch]
        keep = [i for i, t in enumerate(tokens) if t.strip()]
        if not keep:
            continue
        ch = [ch[i] for i in keep]
        tokens = [tokens[i] for i in keep]
        groups = split_lines(tokens, max_line)
        f = _font_for(" ".join(tokens), style.get("font"))
        ftag = "" if f is font else rf"\fn{f['family']}\b{1 if f['bold'] else 0}"
        pos = rf"\an5\pos({cx},{cy})"

        if mode == "karaoke":
            parts = []
            for gi, g in enumerate(groups):
                seg = []
                for i in g:
                    nxt = ch[i + 1]["s"] if i + 1 < len(ch) else cen
                    start_ref = cst if i == 0 else ch[i]["s"]
                    if i == 0:
                        lead = max(0, int(round((ch[0]["s"] - cst) * 100)))
                        if lead:
                            seg.append(rf"{{\k{lead}}}")
                    dur = max(1, int(round((nxt - max(start_ref, ch[i]["s"])) * 100)))
                    seg.append(rf"{{\kf{dur}}}{esc(tokens[i])}" + (" " if i != g[-1] else ""))
                parts.append("".join(seg))
            text = r"\N".join(parts)
            lines.append(f"Dialogue: 0,{ts(cst)},{ts(cen)},Cap,,0,0,0,,{{{pos}{ftag}{blur_tag}{_anim_tags(anim, cx, cy)}}}{text}")
            continue

        if mode == "none":
            text = r"\N".join(" ".join(esc(tokens[i]) for i in g) for g in groups)
            lines.append(f"Dialogue: 0,{ts(cst)},{ts(cen)},Cap,,0,0,0,,{{{pos}{ftag}{blur_tag}{_anim_tags(anim, cx, cy)}}}{text}")
            continue

        # One event per active word; the chunk text stays identical so nothing jumps.
        for ai in range(len(ch)):
            ast = cst if ai == 0 else ch[ai]["s"]
            aen = ch[ai + 1]["s"] if ai + 1 < len(ch) else cen
            if aen <= ast:
                continue
            parts = []
            for g in groups:
                segs = []
                for i in g:
                    tok = esc(tokens[i])
                    if i == ai:
                        if mode == "grow":
                            segs.append(rf"{{\c{hl_tag}\fscx114\fscy114}}{tok}{{\c{base_tag}\fscx100\fscy100}}")
                        elif mode == "dim":
                            segs.append(rf"{{\alpha&H00&\c{hl_tag}}}{tok}{{\alpha&H70&\c{base_tag}}}")
                        else:
                            segs.append(rf"{{\c{hl_tag}}}{tok}{{\c{base_tag}}}")
                    else:
                        segs.append(tok)
                parts.append(" ".join(segs))
            text = r"\N".join(parts)
            lead = r"\alpha&H70&" if mode == "dim" else ""
            anim_tags = _anim_tags(anim, cx, cy) if ai == 0 else ""
            lines.append(f"Dialogue: 0,{ts(ast)},{ts(aen)},Cap,,0,0,0,,{{{pos}{ftag}{blur_tag}{lead}{anim_tags}}}{text}")

    return "\n".join(lines) + "\n"


def bleep_ranges(words: list[dict], clip_len: float) -> list[tuple[float, float]]:
    out = []
    for w in words or []:
        core = re.sub(r"[^\wऀ-ॿ]", "", w["w"]).lower()
        starred = "*" in w["w"] and len(core) >= 1  # Whisper sometimes writes "f***" itself
        if (core in BAD_WORDS or starred) and 0 <= w["s"] < clip_len:
            out.append((max(0.0, w["s"] - 0.03), min(clip_len, w["e"] + 0.03)))
    return out


def to_srt(words: list[dict], style: dict, clip_len: float) -> str:
    chunks = chunk_words(words, {**style, "max_words": max(6, int(style.get("max_words", 3)))})
    timing = chunk_timing(chunks, clip_len)

    def st(t):
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for i, (ch, (a, b)) in enumerate(zip(chunks, timing), 1):
        out.append(f"{i}\n{st(a)} --> {st(b)}\n{' '.join(w['w'] for w in ch)}\n")
    return "\n".join(out)
