"""Spoken phrases from an editable file and recognition of insulting tickets.

%APPDATA%\\AresAssistant\\voice_phrases.json keeps greetings, reactions to insulting
tickets, the selection mode (random or one fixed phrase) and the voice. The user may
edit it by hand; the app re-reads it before every phrase, so edits apply immediately.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Any, Iterable

from config import APPDATA_ROOT
from voice import DEFAULT_VOICE, VOICES

LOGGER = logging.getLogger(__name__)
PHRASES_PATH = APPDATA_ROOT / "voice_phrases.json"
MODES = {"random": "Случайная фраза из списка", "fixed": "Всегда одна фраза"}

HELP = [
    "Файл фраз Ares Assistant. Редактируй в Блокноте и сохраняй в UTF-8.",
    "greeting — приветствие при запуске, insult — реакция на тикет с оскорблениями.",
    "mode: \"random\" — случайная фраза из phrases (подряд не повторяется), \"fixed\" — всегда фраза из fixed.",
    "В фразах реакции можно писать {ticket} — подставится номер тикета.",
    "insult_triggers_extra — свои слова-триггеры (начало слова), insult_ignore — слова, которые не считать оскорблением.",
    "voice — голос: " + ", ".join(VOICES),
]


def default_phrases(greeting: str = "Алексей, приветствую!") -> dict[str, Any]:
    greeting = greeting.strip() or "Алексей, приветствую!"
    greetings = [greeting] + [p for p in (
        "Алексей, приветствую!",
        "Привет, Алексей! Я на связи.",
        "Алексей, добрый день! Всё готово к работе.",
        "С возвращением, Алексей!",
    ) if p != greeting]
    return {
        "_справка": HELP,
        "voice": DEFAULT_VOICE,
        "greeting": {"mode": "fixed", "fixed": greeting, "phrases": greetings},
        "insult": {
            "enabled": True,
            "mode": "random",
            "fixed": "Внимание, в тикете оскорбления.",
            "phrases": [
                "Внимание, в тикете оскорбления.",
                "Алексей, пришёл токсичный тикет {ticket}.",
                "Осторожно, в обращении {ticket} ругаются.",
                "Тут мат в тикете, посмотри, пожалуйста.",
            ],
            "cooldown_seconds": 5,
        },
        "insult_triggers_extra": [],
        "insult_ignore": [],
    }


def _merge(defaults: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(defaults.get(key), dict):
            merged[key] = {**defaults[key], **value}
        else:
            merged[key] = value
    merged["_справка"] = HELP
    return merged


def load_phrases(greeting: str = "Алексей, приветствую!") -> tuple[dict[str, Any], str]:
    """Return (settings, error). A broken file is never overwritten: defaults are used instead."""
    defaults = default_phrases(greeting)
    if not PHRASES_PATH.exists():
        try:
            save_phrases(defaults)
        except OSError:
            LOGGER.exception("Unable to create %s", PHRASES_PATH)
        return defaults, ""
    try:
        data = json.loads(PHRASES_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("ожидался объект { … }")
        return _merge(defaults, data), ""
    except (OSError, ValueError) as exc:
        LOGGER.warning("Phrase file is invalid: %s", exc)
        return defaults, f"Файл фраз не прочитан ({exc}). Использую фразы по умолчанию."


def save_phrases(data: dict[str, Any]) -> None:
    PHRASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PHRASES_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({**data, "_справка": HELP}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, PHRASES_PATH)


def clean_phrases(values: Iterable[Any]) -> list[str]:
    seen, result = set(), []
    for value in values or []:
        text = " ".join(str(value).split())
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


_last_pick: dict[str, str] = {}


def pick_phrase(section: dict[str, Any], key: str = "", ticket: str = "", rng: random.Random | None = None) -> str:
    """Fixed mode returns the chosen phrase; random mode avoids saying the same phrase twice in a row."""
    phrases = clean_phrases(section.get("phrases", []))
    fixed = " ".join(str(section.get("fixed", "")).split())
    if section.get("mode") == "fixed":
        phrase = fixed or (phrases[0] if phrases else "")
    elif phrases:
        pool = [p for p in phrases if p != _last_pick.get(key)] or phrases
        phrase = (rng or random).choice(pool)
    else:
        phrase = fixed
    if key:
        _last_pick[key] = phrase
    return phrase.replace("{ticket}", str(ticket) if ticket else "").replace("  ", " ").strip()


# ----------------------------------------------------------------- insults
# Patterns run on normalised text: lower case, ё→е, letters typed in Latin turned into
# Cyrillic, "х у й"/"х.у.й" joined, repeated letters collapsed ("сукааа" → "сука").
# Each root is anchored to the start of a word and limited where ordinary words share
# it (мудрый, хлебать, похудеть, жидкий, баранина, вебинар, небанальный, херсон…).
_EB = r"(?:за|у|вы|до|на|об|отъ|от|разъ|раз|съ|при|по|пере|подъ|под|въ|про|недо)?[е]б(?:а|ан|ал|ат|ну|ло|ли|ут|ет|ись|у|уч|ыр|ок|ош|ищ|лан|ин)"
_RU_PATTERNS = [
    # мат
    r"(?:на|по|о|а|ни|до|за|от|рас|раз|с|при|вы)?ху[йяеию]\w*", r"ху(?:ли|ль|ло|ета|ет)\b",
    r"(?:на|по|ни)?хер(?:а|ов\w*|н\w*|ом|ня|ни|ь|ачить\w*)?\b",
    r"(?:с|о|у|за|рас|раз|на|вы|при|до|от|по|под|про)?пизд\w*",
    _EB + r"\w*", r"[е]б\b", r"[е]бл(?:о|а|у|ом|ан\w*|ище)\b", r"долбо\w+", r"долба[нн]\w*", r"долбаеб\w*",
    r"бл[яе]д\w*", r"бля\b", r"блят\w*",
    r"сук(?:а|и|у|е|ой|ам|ами)?\b", r"суч(?:ка|ки|ке|ку|кой|ара\w*|ий|ья|ье|ата|онок|онка|ности?)\b", r"сученыш\w*",
    r"муда[кч]\w*", r"мудил\w*", r"мудозвон\w*", r"муд(?:ень|ня|о)\b",
    r"пид[оа]?р\w*", r"пидрил\w*", r"педик\w*", r"педераст\w*", r"гомос[ея]к\w*",
    r"г[ао]ндон\w*", r"шлюх\w*", r"шалав\w*", r"проститутк\w*",
    r"залуп(?:а|ы|у|ой|е|ень|енец)\b", r"дроч\w*", r"задрот\w*", r"надроч\w*",
    r"манд(?:а|у|ой|ы|е|авош\w*|юк\w*|ец)\b",
    r"говн\w*", r"гавн\w*", r"дерьм\w*",
    r"сра(?:ть|ный|ная|ное|ные|ного|ному|ным|ка|ку|л|ла|ло|ли)\b", r"засран\w*", r"обосра\w*", r"обоса\w*",
    r"жоп(?:а|ы|е|у|ой|ам|ами|ах|ень|ник\w*|ошник\w*|ол\w*)?\b",
    r"отсос\w*", r"соси\b",
    # оскорбления
    r"дебил\w*", r"идиот\w*", r"кретин\w*", r"придур\w*", r"имбецил\w*", r"олигофрен\w*", r"даун(?:а|ы|ов|у|ом|ам|ище|ята)?\b",
    r"дур(?:а|ы|е|у|ой|ак|ака|аку|аком|аки|аков|ачье|ында|еха|ень|ачок|илка)\b",
    r"туп(?:ой|ая|ое|ые|ого|ому|ым|ыми|ица|ицы|ень|ня|орыл\w*|оголов\w*|арь)\b", r"тупорыл\w*",
    r"недоум\w*", r"недонос\w*", r"ублюд\w*", r"урод(?:а|у|ом|ы|ов|ам|ами|ина\w*|ец\w*|лив\w*|ство)?\b",
    r"вырод(?:ок|ка|ку|ки|ков|кам)\b", r"мраз\w*", r"твар(?:ь|и|ей|ям|ями)\b", r"скотин\w*", r"гнид\w*",
    r"падл\w*", r"сволоч\w*", r"чмо(?:шник\w*|шн\w*|ха)?\b", r"чмыр\w*",
    r"лох(?:а|и|у|ом|ов|ам|ушка|ушки)?\b", r"лошар\w*", r"лошп[еи]д\w*", r"олух\w*", r"болван\w*",
    r"баран(?:а|ы|у|ом|ов|ам|ами)?\b", r"коз[е]л\w*", r"козл(?:ы|ина|ов|у|ом|ам|ами|ище)\b",
    r"свинь[яи]\b", r"свин(?:ота|тус)\b", r"петух\w*", r"шавк\w*", r"псин\w*", r"гад(?:ина\w*|ены?ш\w*)?\b",
    r"ничтожеств\w*", r"нищеброд\w*", r"аутист\w*", r"шизик\w*", r"шиз\b", r"даунич\w*",
    r"сдох(?:ни|ните|нуть|ла|ли)?\b", r"убей(?:ся| себя)\b", r"выпились\b", r"заткн(?:ись|итесь|и\b)\w*",
    r"тво[юе] мат(?:ь|ушк\w*)\b", r"мат(?:ь|ери) тво[юя]\b", r"мамк\w* тво\w*", r"тво\w* мамк\w*", r"мамаш\w* тво\w*",
    # национальные оскорбления
    r"чурк\w*", r"хач(?:и|ик\w*|ей|ам|ами|ье)?\b", r"жид(?:ы|ов|ам|ами|ок|овск\w*|яр\w*)?\b",
    r"черномаз\w*", r"чернож[оу]п\w*", r"хохл(?:ы|ов|ам|ами|у|ом|ушк\w*|яцк\w*)?\b", r"хохол\b",
    r"москал\w*", r"кацап\w*", r"нерус\w*", r"узкоглаз\w*", r"нигг?ер\w*", r"пиндос\w*",
]
_EN_PATTERNS = [
    r"f+u+c*k+\w*", r"fck\w*", r"motherf\w*", r"sh[i1]t\w*", r"bitch\w*", r"b[i1]tch\w*", r"asshole\w*",
    r"cunt\w*", r"dick(?:head)?s?\b", r"retard\w*", r"fag+(?:ot)?s?\b", r"nigg\w*", r"whore\w*", r"slut\w*",
    r"stupid\w*", r"moron\w*", r"idiot\w*", r"bastard\w*", r"dumbass\w*",
]
# Masked spellings: х*й, нах*й, п**дец, бл*ть, с*ка, е*ать.
_MASKED_PATTERNS = [
    r"(?:на|по|ни|о|до)?х[*#$%]+[йяеиу]?\w*", r"(?:с|за|рас|о)?п[*#$%]+(?:зд|д)\w*", r"пи[*#$%]+\w*",
    r"бл[*#$%]+[дтяь]?\w*", r"бля[*#$%]+\w*", r"с[*#$%]+ка\b", r"су[*#$%]+а\b",
    r"(?:за|у|вы|на|от|по|до)?е[*#$%]+(?:а|ан|ал|у|ат)\w*", r"(?:за|у|вы|на|от|по|до)?еб[*#$%]+\w*",
]
_MASKED = re.compile(r"(?<![\w*#$%])(?:" + "|".join(_MASKED_PATTERNS) + ")", re.IGNORECASE)
_RU = re.compile(r"\b(?:" + "|".join(_RU_PATTERNS) + ")", re.IGNORECASE)
_EN = re.compile(r"\b(?:" + "|".join(_EN_PATTERNS) + ")", re.IGNORECASE)
TRIGGER_COUNT = len(_RU_PATTERNS) + len(_EN_PATTERNS) + len(_MASKED_PATTERNS)

_VISUAL = str.maketrans({"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у", "k": "к", "m": "м",
                         "t": "т", "b": "в", "h": "н", "u": "и", "3": "з", "0": "о", "@": "а", "6": "б", "4": "ч"})
_TRANSLIT = [("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ya", "я"), ("yo", "е"), ("yu", "ю"),
             ("ja", "я"), ("ju", "ю"), ("ts", "ц")]
_TRANSLIT_ONE = str.maketrans({"a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з", "i": "и", "j": "й",
                               "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т",
                               "u": "у", "f": "ф", "h": "х", "c": "ц", "x": "х", "w": "в", "q": "к", "'": "ь"})
_SPACED = re.compile(r"\b(?:[^\W\d_][^\w]{1,3}){2,}[^\W\d_]\b")
_LATIN = re.compile(r"[a-z]")
_CYRILLIC = re.compile(r"[а-я]")


def _translit(word: str) -> str:
    for src, dst in _TRANSLIT:
        word = word.replace(src, dst)
    word = re.sub(r"(?<=[aeiouяеюё])y", "й", word)
    return word.replace("y", "ы").translate(_TRANSLIT_ONE)


def _variants(text: str) -> list[str]:
    base = text.lower().replace("ё", "е")
    base = _SPACED.sub(lambda m: re.sub(r"[^\w]", "", m.group(0)), base)
    visual, translit = [], []
    for token in re.findall(r"[\w@']+|[^\w@']+", base):
        if _LATIN.search(token) and _CYRILLIC.search(token):
            visual.append(token.translate(_VISUAL)); translit.append(token.translate(_VISUAL))
        elif _LATIN.search(token):
            visual.append(token.translate(_VISUAL)); translit.append(_translit(token))
        else:
            visual.append(token); translit.append(token)
    collapse = lambda s: re.sub(r"(\w)\1+", r"\1", s)
    return list(dict.fromkeys(collapse(v) for v in (base, "".join(visual), "".join(translit))))


def detect_insults(text: str, extra: Iterable[str] = (), ignore: Iterable[str] = ()) -> list[str]:
    """Words of `text` recognised as swearing or insults (normalised, without duplicates)."""
    if not text or not text.strip():
        return []
    ignore = [w.lower().replace("ё", "е").strip() for w in ignore if str(w).strip()]
    extra = [re.escape(w.lower().replace("ё", "е").strip()) for w in extra if str(w).strip()]
    custom = re.compile(r"\b(?:" + "|".join(extra) + r")\w*") if extra else None
    found: list[str] = []
    for variant in _variants(text):
        for pattern in (_RU, custom):
            if pattern is None:
                continue
            for match in pattern.finditer(variant):
                word = match.group(0).strip()
                if word and not any(word.startswith(i) for i in ignore) and word not in found:
                    found.append(word)
    lowered = text.lower().replace("ё", "е")
    for match in list(_MASKED.finditer(lowered)) + list(_EN.finditer(lowered)):
        word = match.group(0)
        if word not in found and not any(word.startswith(i) for i in ignore):
            found.append(word)
    return found
