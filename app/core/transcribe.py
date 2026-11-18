"""Word-accurate speech recognition with faster-whisper (English + Hindi)."""
from __future__ import annotations

import inspect
import re
import threading
from typing import Callable

import numpy as np

from . import config  # noqa: F401  (registers CUDA DLL dirs)
from . import ffmpeg
from .config import MODELS_DIR

_model = None
_model_key = None
_model_lock = threading.Lock()
_device_info = {"device": None, "error": None}

HALLUCINATIONS = re.compile(
    r"(thanks? (you )?for watching|please subscribe|subscribe to my channel|subtitles by|amara\.org|"
    r"like and subscribe|transcribed by|www\.|\.com)", re.I)

# Gentle clean-up: band-limit to the voice range and level the volume. (Heavier denoise /
# dynamic normalisation pumps game noise up into "speech" and causes hallucinated loops.)
SPEECH_FILTER = "highpass=f=80,lowpass=f=8000,loudnorm=I=-18:TP=-2"


def collapse_repeats(words: list[dict]) -> list[dict]:
    """Remove Whisper's looping hallucinations (e.g. the same word/phrase 30x in a row).
    A single word may repeat up to 3x ("run run run"), a phrase up to 2x."""
    def key(w):
        return re.sub(r"[^\wऀ-ॿ]", "", w["w"].lower())
    out = list(words)
    for n in (4, 3, 2, 1):
        limit = 3 if n == 1 else 2
        i, res = 0, []
        while i < len(out):
            gram = [key(w) for w in out[i:i + n]]
            reps = 1
            while i + (reps + 1) * n <= len(out) and [key(w) for w in out[i + reps * n:i + (reps + 1) * n]] == gram:
                reps += 1
            if reps > limit and len(gram) == n and any(gram):
                res.extend(out[i:i + n * (1 if reps > 6 else limit)])
                i += reps * n
            else:
                res.append(out[i])
                i += 1
        out = res
    return out


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def model_cached(size: str) -> bool:
    repo = MODELS_DIR / f"models--Systran--faster-whisper-{size}" / "snapshots"
    return any(p.exists() for p in repo.glob("*/model.bin")) if repo.exists() else False


def resolve_model(name: str | None) -> str:
    if not name or name == "auto":
        if not cuda_available():
            return "small"
        # Don't make the user wait on a 3 GB download: use the best model already on disk.
        for size in ("large-v3", "medium", "small"):
            if model_cached(size):
                return size
        return "large-v3"
    return name


def get_model(name: str | None):
    global _model, _model_key
    from faster_whisper import WhisperModel

    size = resolve_model(name)
    with _model_lock:
        if _model is not None and _model_key == size:
            return _model
        _model = None
        attempts = []
        if cuda_available():
            attempts += [("cuda", "int8_float16"), ("cuda", "float16")]
        attempts += [("cpu", "int8")]
        last = None
        for device, ctype in attempts:
            try:
                # Cached models load offline: no network round-trip (which can hang on a busy connection).
                _model = WhisperModel(size, device=device, compute_type=ctype, download_root=str(MODELS_DIR),
                                      local_files_only=model_cached(size))
                _model_key = size
                _device_info.update(device=f"{device}/{ctype}", error=None)
                return _model
            except Exception as e:  # noqa: BLE001
                last = e
                _device_info["error"] = str(e)
        raise RuntimeError(f"Could not load Whisper model '{size}': {last}")


def device_info() -> dict:
    return dict(_device_info)


# ------------------------------------------------------------------ Hinglish
_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "फ़": "f", "ड़": "r", "ढ़": "rh",
    "य़": "y", "ऱ": "r", "ळ": "l",
}
_NUKTA_MAP = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "फ": "f", "ड": "r", "ढ": "rh"}
_VOW = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
        "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
_MATRA = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
          "ौ": "au", "ॉ": "o", "ॅ": "e"}
_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


# How people actually type these in Hinglish (beats letter-by-letter rules), plus English
# words that Whisper writes in Devanagari when Hindi speakers use them.
HINGLISH_WORDS = {
    "है": "hai", "हैं": "hain", "मैं": "main", "में": "mein", "नहीं": "nahi", "नही": "nahi", "क्या": "kya",
    "यार": "yaar", "भाई": "bhai", "भाईया": "bhaiya", "भैया": "bhaiya", "अरे": "arre", "चल": "chal", "चलो": "chalo",
    "ये": "ye", "यह": "yeh", "वो": "wo", "वह": "woh", "तू": "tu", "तुम": "tum", "तुम्हें": "tumhe", "हम": "hum",
    "क्यों": "kyun", "कैसे": "kaise", "कहाँ": "kahan", "कहां": "kahan", "बहुत": "bahut", "अच्छा": "accha",
    "ठीक": "theek", "हाँ": "haan", "हां": "haan", "ना": "na", "मत": "mat", "देख": "dekh", "देखो": "dekho",
    "गया": "gaya", "गई": "gayi", "गए": "gaye", "करो": "karo", "कर": "kar", "रहा": "raha", "रही": "rahi",
    "रहे": "rahe", "था": "tha", "थी": "thi", "थे": "the", "और": "aur", "लेकिन": "lekin", "तो": "toh",
    "भी": "bhi", "ही": "hi", "का": "ka", "की": "ki", "के": "ke", "को": "ko", "से": "se", "पर": "par",
    "अब": "ab", "फिर": "phir", "मार": "maar", "मारा": "maara", "भाग": "bhaag", "भागो": "bhaago",
    "बचाओ": "bachao", "वाह": "wah", "आ": "aa", "जा": "ja", "जाओ": "jao", "आओ": "aao", "सब": "sab",
    "कुछ": "kuch", "कोई": "koi", "एक": "ek", "दो": "do", "बस": "bas", "पागल": "paagal", "गाड़ी": "gaadi",
    "पैसा": "paisa", "पैसे": "paise", "बंदा": "banda", "लड़की": "ladki", "लड़का": "ladka", "मज़ा": "maza",
    "मजा": "maza", "ज़्यादा": "zyada", "ज्यादा": "zyada", "अभी": "abhi", "कब": "kab", "कौन": "kaun",
    "मेरा": "mera", "मेरी": "meri", "मेरे": "mere", "तेरा": "tera", "तेरी": "teri", "तेरे": "tere",
    "हमारा": "hamara", "उसको": "usko", "इसको": "isko", "मुझे": "mujhe", "तुझे": "tujhe", "चुप": "chup",
    "चूप": "chup", "रुक": "ruk", "रुको": "ruko", "जल्दी": "jaldi", "लो": "lo", "दे": "de", "दो।": "do",
    "हो": "ho", "होगा": "hoga", "गयी": "gayi", "वाला": "wala", "वाली": "wali", "सच": "sach", "साथ": "saath",
    # English words spoken by Hindi speakers
    "गेम": "game", "कार": "car", "पुलिस": "police", "गन": "gun", "किल": "kill", "बॉस": "boss", "मिशन": "mission",
    "ब्रो": "bro", "ओके": "ok", "सॉरी": "sorry", "प्लेयर": "player", "रिकॉर्डिंग": "recording",
    "रेकॉर्डिंग": "recording", "रिकॉर्ड": "record", "लास्ट": "last", "सेकंड": "second", "सेकेंड": "second",
    "सब्सक्राइब": "subscribe", "चैनल": "channel", "वीडियो": "video", "लाइक": "like", "कमेंट": "comment",
    "हेलो": "hello", "हाय": "hi", "बाय": "bye", "वेट": "wait", "स्टॉप": "stop", "रन": "run", "जंप": "jump",
    "बाइक": "bike", "प्लेन": "plane", "हेलीकॉप्टर": "helicopter", "बैंक": "bank", "हाइस्ट": "heist",
    "वांटेड": "wanted", "स्टार": "star", "लेवल": "level", "मैप": "map", "शॉट": "shot", "हेडशॉट": "headshot",
    "स्नाइपर": "sniper", "बम": "bomb", "ब्लास्ट": "blast", "क्रैश": "crash", "स्पीड": "speed", "मोड": "mode",
    "ऑनलाइन": "online", "सर्वर": "server", "टीम": "team", "स्क्वाड": "squad", "क्लच": "clutch", "नूब": "noob",
    "प्रो": "pro", "ओह": "oh", "माय": "my", "गॉड": "god", "वॉव": "wow", "नो": "no", "वे": "way", "यस": "yes",
    "लेट्स": "let's", "गो": "go", "इंसेन": "insane", "क्रेजी": "crazy", "क्रेज़ी": "crazy", "फनी": "funny",
    "इंटरेस्टिंग": "interesting", "बेस्ट": "best", "फर्स्ट": "first", "टाइम": "time", "फोन": "phone",
}


def to_hinglish(word: str) -> str:
    if not DEVANAGARI_RE.search(word):
        return word
    m = re.match(r"^(.*?)([.,!?।…]*)$", word)
    core, tail = (m.group(1), m.group(2)) if m else (word, "")
    if core in HINGLISH_WORDS:
        return HINGLISH_WORDS[core] + tail.replace("।", ".")
    # syllables: [cons_str or "", vowel_str, is_schwa]
    syl: list[list] = []
    i, chars = 0, list(word)
    out_prefix, out_suffix = "", ""
    while i < len(chars):
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if c in _CONS:
            cons = _CONS[c]
            if nxt == "़":  # nukta
                cons = _NUKTA_MAP.get(c, cons)
                i += 1
                nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt == "्":  # virama -> conjunct, no vowel
                syl.append([cons, "", False])
                i += 2
                continue
            if nxt in _MATRA:
                syl.append([cons, _MATRA[nxt], False])
                i += 2
                continue
            syl.append([cons, "a", True])
            i += 1
            continue
        if c in _VOW:
            syl.append(["", _VOW[c], False])
        elif c in _MATRA:
            syl.append(["", _MATRA[c], False])
        elif c in ("ं", "ँ"):  # anusvara / chandrabindu
            if syl:
                syl[-1][1] += "n"
                syl[-1][2] = False
        elif c == "ः":
            if syl:
                syl[-1][1] += "h"
        elif c in _DIGITS:
            syl.append([_DIGITS[c], "", False])
        elif c in ("।", "॥"):
            syl.append([".", "", False])
        else:
            if not syl:
                out_prefix += c
            else:
                syl.append([c, "", False])
        i += 1

    letters = [s for s in syl if s[0] not in (".",) and s[0].isalpha() or s[1]]
    # schwa deletion: final, and V C(a) C V
    if len(letters) > 1 and syl and syl[-1][2]:
        syl[-1][1] = ""
    for k in range(1, len(syl) - 1):
        if syl[k][2] and syl[k][1] == "a" and syl[k + 1][1] and syl[k - 1][1] and not syl[k + 1][2]:
            syl[k][1] = ""
    s = out_prefix + "".join(a + b for a, b, _ in syl) + out_suffix
    s = re.sub(r"aa$", "a", s)
    s = re.sub(r"ee(n?)$", r"i\1", s)
    s = re.sub(r"aai$", "ai", s)
    return s


# ------------------------------------------------------------------ transcription
def _clean_words(raw: list[dict]) -> list[dict]:
    words = []
    for w in raw:
        t = (w["w"] or "").strip()
        if not t:
            continue
        words.append({"w": t, "s": float(w["s"]), "e": float(w["e"]), "p": round(float(w.get("p", 1.0)), 3)})
    words.sort(key=lambda x: x["s"])
    for i in range(len(words)):
        if i + 1 < len(words) and words[i]["e"] > words[i + 1]["s"]:
            words[i]["e"] = max(words[i]["s"] + 0.04, words[i + 1]["s"])
        if words[i]["e"] - words[i]["s"] < 0.04:
            words[i]["e"] = words[i]["s"] + 0.04
        words[i]["s"] = round(words[i]["s"], 3)
        words[i]["e"] = round(words[i]["e"], 3)
    return words


def transcribe(audio, *, language: str = "auto", hindi_mode: str = "devanagari", model: str | None = None,
               hotwords: str = "", duration: float | None = None,
               on_progress: Callable[[float, str], None] | None = None) -> dict:
    """audio: np.float32 16k mono array or a path. Returns {language, words, segments}."""
    m = get_model(model)
    lang = None if language in (None, "", "auto") else language
    if on_progress:
        on_progress(0.02, "Detecting language...")
    if lang is None:
        try:
            sample = audio if isinstance(audio, np.ndarray) else None
            if sample is not None and len(sample) > 0:
                det_lang, prob, all_probs = m.detect_language(sample[: 16000 * 30])
                # Whisper confuses Hindi with Urdu (Arabic script). Hindi speakers want Devanagari.
                if det_lang == "ur":
                    det_lang = "hi"
                if det_lang not in ("en", "hi"):
                    probs = dict(all_probs) if all_probs else {}
                    det_lang = "hi" if probs.get("hi", 0) + probs.get("ur", 0) > probs.get("en", 0) else "en"
                lang = det_lang
        except Exception:
            lang = None
    if lang == "ur":
        lang = "hi"

    task = "transcribe"
    if lang == "hi" and hindi_mode == "english":
        task = "translate"

    # Speed: one fallback retry max (each retry re-decodes the whole 30 s window), small sampling.
    kwargs = dict(language=lang, task=task, beam_size=5, best_of=2, word_timestamps=True,
                  condition_on_previous_text=False, vad_filter=True,
                  vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=300, threshold=0.5),
                  temperature=[0.0, 0.4], compression_ratio_threshold=2.0,
                  log_prob_threshold=-1.0, no_speech_threshold=0.6)
    if "repetition_penalty" in inspect.signature(m.transcribe).parameters:
        kwargs["repetition_penalty"] = 1.1
    prompt_bits = []
    if hotwords.strip():
        if "hotwords" in inspect.signature(m.transcribe).parameters:
            kwargs["hotwords"] = hotwords.strip()
        else:
            prompt_bits.append(hotwords.strip())
    if prompt_bits:
        kwargs["initial_prompt"] = " ".join(prompt_bits)

    segments, info = m.transcribe(audio, **kwargs)
    total = duration or getattr(info, "duration", None) or 1.0
    raw, segs = [], []
    for seg in segments:
        text = seg.text.strip()
        if seg.no_speech_prob > 0.5 and seg.avg_logprob < -0.9:
            continue
        if HALLUCINATIONS.search(text) and (seg.no_speech_prob > 0.2 or seg.avg_logprob < -0.6):
            continue
        segs.append({"start": round(seg.start, 3), "end": round(seg.end, 3), "text": text})
        for w in (seg.words or []):
            raw.append({"w": w.word, "s": w.start, "e": w.end, "p": w.probability})
        if on_progress:
            on_progress(min(0.98, seg.end / total), f"Transcribing... {min(100, seg.end / total * 100):.0f}%")

    words = collapse_repeats(_clean_words(raw))
    out_lang = info.language if lang is None else lang
    if out_lang == "ur":
        out_lang = "hi"
    if out_lang == "hi" and hindi_mode == "hinglish":
        for w in words:
            w["w"] = to_hinglish(w["w"])
        for s in segs:
            s["text"] = " ".join(to_hinglish(x) for x in s["text"].split())
    if task == "translate":
        out_lang = "en"
    return {"language": out_lang, "words": words, "segments": segs, "mode": hindi_mode}


def transcribe_range(src: str, start: float, end: float, **kw) -> dict:
    audio = ffmpeg.read_audio(src, start=start, duration=end - start, filters=SPEECH_FILTER)
    if len(audio) < 1600:
        return {"language": "en", "words": [], "segments": []}
    return transcribe(audio, duration=end - start, **kw)
