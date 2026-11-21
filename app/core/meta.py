"""Title / description / tag suggestions tuned for gaming Shorts."""
from __future__ import annotations

import random
import re

from .highlights import _hype_re
from .presets import GAMES

EN_TEMPLATES = [
    "{hook} 😱 #{tag}",
    "{game} but {hook_l} 💀",
    "This {game} moment is INSANE 🔥",
    "Wait for the end... {game} 😂",
    "{game}: {hook} 🤯",
    "Nobody expected this in {game} 😳",
    "{game} players will understand 😭",
    "POV: {hook_l} in {game}",
]
HI_TEMPLATES = [
    "{game} में ये क्या हो गया 😱",
    "Bhai ye kya tha 😂 | {game}",
    "{game} में सबसे पागल moment 🔥",
    "End तक देखना 💀 {game}",
    "Bhai {game} me ye mat karna 😭",
]
GENERIC_HOOKS = ["this happened", "everything went wrong", "the cops showed up", "I did the impossible",
                 "this was NOT the plan", "chaos broke loose"]


def _best_phrase(words: list[dict] | None) -> str | None:
    if not words:
        return None
    text = " ".join(w["w"] for w in words)
    sents = re.split(r"(?<=[.?!।])\s+", text)
    best = None
    for s in sents:
        s = s.strip()
        n = len(s.split())
        if 2 <= n <= 9:
            score = (3 if _hype_re.search(s) else 0) + (1 if s.endswith(("!", "?")) else 0) - abs(n - 5) * 0.2
            if best is None or score > best[0]:
                best = (score, s)
    return best[1].rstrip(".") if best else None


def suggest(proj: dict, clip: dict) -> dict:
    game = GAMES.get(proj.get("game") or "other", GAMES["other"])
    gname, tag = game["name"], game["tag"]
    phrase = clip.get("settings", {}).get("effects", {}).get("hook_text") or _best_phrase(clip.get("words"))
    hook = phrase or random.choice(GENERIC_HOOKS)
    hook_cap = hook[:1].upper() + hook[1:]
    vertical = clip.get("settings", {}).get("layout", {}).get("aspect", "9:16") in ("9:16", "4:5", "1:1")
    lang = clip.get("language") or "en"

    titles = []
    pool = (HI_TEMPLATES + EN_TEMPLATES[:4]) if lang == "hi" else (EN_TEMPLATES + HI_TEMPLATES[:2])
    for tpl in pool:
        t = tpl.format(hook=hook_cap, hook_l=hook.lower(), game=gname, tag=tag)
        if vertical and "#shorts" not in t.lower() and len(t) < 88:
            t += " #shorts"
        if t not in titles:
            titles.append(t[:100])
    titles = titles[:8]

    hashtags = list(dict.fromkeys(game["hashtags"] + (["#Shorts", "#ShortsFeed"] if vertical else []) +
                                  (["#HindiGaming", "#IndianGamer"] if lang == "hi" else []) + ["#FunnyMoments", "#Viral"]))[:12]
    desc_lines = [
        f"{hook_cap} 🎮" if phrase else f"Insane {gname} moment 🎮",
        "",
        f"🔔 Subscribe for daily {gname} clips, funny moments & highlights!",
        "👍 Like if this made you laugh, comment your favourite moment 👇",
        "",
        f"Game: {gname}",
        "",
        " ".join(hashtags),
    ]
    tags = list(dict.fromkeys(game["tags"] + ["gaming", "funny moments", "highlights", "viral", "shorts" if vertical else "gameplay"]))
    tag_out, total = [], 0
    for t in tags:
        if total + len(t) + 1 > 480:
            break
        tag_out.append(t)
        total += len(t) + 1
    # Instagram Reel caption: short hook, call to action, a handful of focused hashtags
    ig_tags = list(dict.fromkeys(game["hashtags"][:3] + (["#reels", "#gamingreels"] if vertical else ["#gaming"])))[:5]
    ig_hook = (titles[0].replace("#shorts", "").replace("#Shorts", "").strip()) if titles else hook_cap
    ig_caption = "\n".join([
        ig_hook,
        "",
        ("Follow for daily " + gname + " clips 🎮🔥") if lang != "hi" else (gname + " के daily clips के लिए Follow करो 🎮🔥"),
        "💬 Comment what you'd do next 👇" if lang != "hi" else "💬 Comment में बताओ 👇",
        "",
        " ".join(ig_tags),
    ])
    return {"titles": titles, "title": titles[0], "description": "\n".join(desc_lines), "tags": tag_out,
            "hashtags": hashtags, "hook": hook_cap, "ig_caption": ig_caption[:2150]}
