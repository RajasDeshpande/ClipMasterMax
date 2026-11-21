"""Caption style presets, fonts, game profiles and default clip settings."""
from __future__ import annotations

import copy

# family = the font family name libass will match; bold = ASS Bold flag.
FONTS = {
    "poppins_black": {"label": "Poppins Black", "family": "Poppins Black", "bold": 0, "devanagari": True, "file": "Poppins-Black.ttf"},
    "poppins_xb":    {"label": "Poppins ExtraBold", "family": "Poppins ExtraBold", "bold": 0, "devanagari": True, "file": "Poppins-ExtraBold.ttf"},
    "poppins_bold":  {"label": "Poppins Bold", "family": "Poppins", "bold": 1, "devanagari": True, "file": "Poppins-Bold.ttf"},
    "anton":         {"label": "Anton (tall)", "family": "Anton", "bold": 0, "devanagari": False, "file": "Anton-Regular.ttf"},
    "bebas":         {"label": "Bebas Neue", "family": "Bebas Neue", "bold": 0, "devanagari": False, "file": "BebasNeue-Regular.ttf"},
    "bangers":       {"label": "Bangers (comic)", "family": "Bangers", "bold": 0, "devanagari": False, "file": "Bangers-Regular.ttf"},
    "mukta":         {"label": "Mukta ExtraBold (Hindi)", "family": "Mukta ExtraBold", "bold": 0, "devanagari": True, "file": "Mukta-ExtraBold.ttf"},
    "khand":         {"label": "Khand Bold (Hindi gaming)", "family": "Khand", "bold": 1, "devanagari": True, "file": "Khand-Bold.ttf"},
    "rajdhani":      {"label": "Rajdhani Bold (tech)", "family": "Rajdhani", "bold": 1, "devanagari": True, "file": "Rajdhani-Bold.ttf"},
    "impact":        {"label": "Impact (system)", "family": "Impact", "bold": 0, "devanagari": False, "file": None},
}
DEVANAGARI_FALLBACK = "poppins_black"

_BASE_STYLE = {
    "preset": "beast",
    "font": "poppins_black",
    "size": 92,               # px at 1080px output width
    "uppercase": True,
    "color": "#FFFFFF",
    "highlight": "#FFE500",
    "outline_color": "#000000",
    "outline": 7,
    "shadow": 3,
    "shadow_color": "#000000",
    "blur": 0,                # glow / soft edge
    "position": 0.70,         # vertical centre, 0 = top, 1 = bottom
    "max_words": 3,
    "max_chars_line": 18,
    "highlight_mode": "color",  # color | grow | dim | karaoke | none
    "animation": "pop",         # pop | bounce | fade | slide | none
    "box": False,
    "box_color": "#000000",
    "box_opacity": 0.6,
    "strip_punct": True,
    "censor": False,
}


def _style(**kw) -> dict:
    s = copy.deepcopy(_BASE_STYLE)
    s.update(kw)
    return s


CAPTION_PRESETS = {
    "beast":    {"label": "Beast Mode", "desc": "Bold white, yellow active word", "style": _style()},
    "hormozi":  {"label": "Viral Green", "desc": "Tall caps, green word grows", "style": _style(preset="hormozi", font="anton", size=118, highlight="#39FF14", outline=6, shadow=0, max_words=2, max_chars_line=14, highlight_mode="grow")},
    "clean":    {"label": "Clean Pro", "desc": "Minimal, dims inactive words", "style": _style(preset="clean", font="poppins_xb", size=70, uppercase=False, highlight="#FFFFFF", outline=3, shadow=4, max_words=4, max_chars_line=22, highlight_mode="dim", animation="fade")},
    "neon":     {"label": "Neon Gamer", "desc": "Cyan glow, pink active word", "style": _style(preset="neon", font="rajdhani", size=104, color="#00F0FF", highlight="#FF2BD6", outline_color="#06122B", outline=6, shadow=0, blur=2, animation="slide")},
    "comic":    {"label": "Comic Punch", "desc": "Cartoon yellow with red hits", "style": _style(preset="comic", font="bangers", size=108, color="#FFE14D", highlight="#FF3B3B", outline=8, shadow=5, animation="bounce")},
    "karaoke":  {"label": "Karaoke Sweep", "desc": "Colour fills as words are spoken", "style": _style(preset="karaoke", font="poppins_xb", size=80, uppercase=False, highlight="#FFC400", outline=5, shadow=2, max_words=5, max_chars_line=22, highlight_mode="karaoke", animation="none")},
    "oneword":  {"label": "One Word Blast", "desc": "One huge word at a time", "style": _style(preset="oneword", font="anton", size=160, highlight="#FFE500", outline=9, shadow=4, max_words=1, max_chars_line=12, highlight_mode="color", animation="bounce", position=0.62)},
    "fire":     {"label": "Fire Hype", "desc": "Orange active word that grows", "style": _style(preset="fire", font="poppins_black", size=94, highlight="#FF5A1F", highlight_mode="grow", animation="bounce")},
    "hindi":    {"label": "Desi Pro (Hindi)", "desc": "Khand font, saffron highlight", "style": _style(preset="hindi", font="khand", size=108, uppercase=False, highlight="#FF9933", outline=7, shadow=3)},
    "subtitle": {"label": "Classic Subtitle", "desc": "Boxed subtitles for long videos", "style": _style(preset="subtitle", font="poppins_bold", size=52, uppercase=False, outline=0, shadow=0, position=0.88, max_words=9, max_chars_line=34, highlight_mode="none", animation="none", box=True, strip_punct=False)},
}

GAMES = {
    "gta6": {"name": "GTA 6", "tag": "GTA6", "hotwords": "GTA 6, GTA VI, Lucia, Jason, Vice City, Leonida, Rockstar, wanted level, cops, heist, bro",
             "hashtags": ["#GTA6", "#GTAVI", "#GTA6Gameplay", "#ViceCity", "#RockstarGames", "#Gaming"],
             "tags": ["GTA 6", "GTA VI", "GTA 6 gameplay", "GTA 6 funny moments", "GTA 6 clips", "Grand Theft Auto VI", "Vice City", "Lucia", "Jason", "Rockstar Games", "GTA 6 shorts"]},
    "gta5": {"name": "GTA 5", "tag": "GTA5", "hotwords": "GTA 5, Los Santos, Michael, Franklin, Trevor, wanted level, cops, heist",
             "hashtags": ["#GTA5", "#GTAV", "#GTAOnline", "#Gaming"],
             "tags": ["GTA 5", "GTA V", "GTA Online", "GTA 5 funny moments", "GTA 5 clips", "Los Santos"]},
    "valorant": {"name": "Valorant", "tag": "Valorant", "hotwords": "Valorant, ace, clutch, Jett, Reyna, Phoenix, Sage, spike, defuse, headshot, one tap",
                 "hashtags": ["#Valorant", "#ValorantClips", "#Ace", "#Gaming"],
                 "tags": ["Valorant", "Valorant clips", "Valorant ace", "Valorant clutch", "Valorant highlights"]},
    "bgmi": {"name": "BGMI", "tag": "BGMI", "hotwords": "BGMI, PUBG, chicken dinner, squad, knock, AWM, M416, airdrop, zone, bhai",
             "hashtags": ["#BGMI", "#PUBGMobile", "#BGMIClips", "#Gaming"],
             "tags": ["BGMI", "BGMI clips", "PUBG Mobile", "BGMI clutch", "BGMI highlights", "chicken dinner"]},
    "freefire": {"name": "Free Fire", "tag": "FreeFire", "hotwords": "Free Fire, booyah, headshot, one tap, gloo wall, squad, bhai",
                 "hashtags": ["#FreeFire", "#FreeFireMax", "#Booyah", "#Gaming"],
                 "tags": ["Free Fire", "Free Fire Max", "Free Fire clips", "Booyah", "Free Fire headshot"]},
    "cod": {"name": "Call of Duty", "tag": "Warzone", "hotwords": "Warzone, Call of Duty, loadout, gulag, nuke, sniper, quickscope",
            "hashtags": ["#Warzone", "#CallOfDuty", "#COD", "#Gaming"],
            "tags": ["Warzone", "Call of Duty", "COD clips", "Warzone highlights", "COD funny moments"]},
    "fortnite": {"name": "Fortnite", "tag": "Fortnite", "hotwords": "Fortnite, victory royale, storm, build, edit, crank",
                 "hashtags": ["#Fortnite", "#FortniteClips", "#VictoryRoyale", "#Gaming"],
                 "tags": ["Fortnite", "Fortnite clips", "Fortnite highlights", "Victory Royale"]},
    "minecraft": {"name": "Minecraft", "tag": "Minecraft", "hotwords": "Minecraft, creeper, Enderman, Nether, diamond, villager, mob",
                  "hashtags": ["#Minecraft", "#MinecraftShorts", "#Gaming"],
                  "tags": ["Minecraft", "Minecraft funny", "Minecraft shorts", "Minecraft clips"]},
    "rdr2": {"name": "RDR 2", "tag": "RDR2", "hotwords": "Red Dead Redemption, Arthur, Dutch, horse, bounty, outlaw",
             "hashtags": ["#RDR2", "#RedDeadRedemption2", "#Gaming"],
             "tags": ["RDR2", "Red Dead Redemption 2", "RDR2 funny moments", "RDR2 clips"]},
    "other": {"name": "Gaming", "tag": "Gaming", "hotwords": "",
              "hashtags": ["#Gaming", "#GamingClips", "#Gamer"],
              "tags": ["gaming", "gaming clips", "funny moments", "highlights", "gameplay"]},
}

# Words to mask in captions (and optionally bleep in audio) for advertiser-friendly uploads.
BAD_WORDS = {
    "fuck", "fucking", "fucked", "fucker", "motherfucker", "shit", "bullshit", "bitch", "bastard",
    "asshole", "dick", "pussy", "cunt", "slut", "whore", "nigga", "nigger", "faggot", "wtf",
    "chutiya", "chutiye", "madarchod", "bhenchod", "behenchod", "bhosdike", "bhosdi", "gandu",
    "lund", "lauda", "lavda", "randi", "harami", "kutta", "kamina", "mc", "bc", "bsdk", "gaand",
    "चूतिया", "मादरचोद", "भेनचोद", "बहनचोद", "भोसडीके", "गांडू", "लौड़ा", "रंडी", "हरामी", "भोसड़ी",
}


def default_clip_settings() -> dict:
    return {
        "style": copy.deepcopy(CAPTION_PRESETS["beast"]["style"]),
        "layout": {"aspect": "9:16", "mode": "blur", "zoom": 1.0, "crop_x": 0.5, "fg_y": 0.45, "cam_fit": "sharp"},
        "effects": {
            "punch_zoom": True, "punch_strength": 0.10,
            "progress_bar": False, "bar_color": "#FFE500",
            "hook_text": "", "hook_duration": 3.0, "hook_position": 0.15,
            "hook_color": "#FFFFFF", "hook_bg": "#E11D48",
            "watermark": "", "bleep": False, "bleep_sound": "beep",
            "hype_sfx": "none", "sfx_volume": 0.8, "sfx_marks": [], "text_pops": [],
            "music": None, "music_volume": 0.12, "duck": True,
            "normalize": True, "fade": True,
        },
        "export": {"fps": "source", "quality": "high", "encoder": "auto"},
        "captions": {"enabled": True, "language": "en", "hindi_mode": "devanagari", "model": "auto", "hotwords": ""},
    }


def public_presets() -> dict:
    return {
        "captions": CAPTION_PRESETS,
        "fonts": {k: {kk: vv for kk, vv in v.items()} for k, v in FONTS.items()},
        "games": {k: {"name": v["name"], "hotwords": v["hotwords"]} for k, v in GAMES.items()},
        "defaults": default_clip_settings(),
        "bad_words": sorted(BAD_WORDS),
        "sounds": __import__("app.core.sfx", fromlist=["public"]).public(),
    }
