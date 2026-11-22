from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
from reply_guard import ReplyGuard

TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
TICKET_RE = re.compile(r"\[#?(\d{3,12})\]|\bTICKET\s*#?(\d{3,12})\b", re.IGNORECASE)
COMMAND_RE = re.compile(r"^\s*t\s+a\s+(\d{3,12})\s+([\s\S]+?)\s*$", re.IGNORECASE)
DATE_AUTHOR_RE = re.compile(r"^\s*\[[^\]]+\]\s*([^:]{1,40}):\s*(.*)$", re.IGNORECASE)
TEMPLATE_PREFIX_RE = re.compile(r"^\s*t\s+a\s+(?:\{ticket_id\}|\d{3,12})\s*", re.IGNORECASE)


SYSTEM_TICKET_HEADER_RE = re.compile(
    r"^\s*\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\]?\s*"
    r"(?:\[#?\d{3,12}\])?.*\b(?:TG|VK)-?\d+.*\b\d+\s+сообщени",
    re.IGNORECASE,
)
SYSTEM_ONLY_RE = re.compile(
    r"^(?:переслано\s+от|поступил(?:о)?\s+(?:тикет|ответ)|тикет\s*#?\d+|"
    r"\[?\d{4}-\d{2}-\d{2}[^\]]*\]?\s*\[#?\d+\].*)$",
    re.IGNORECASE,
)
STOPWORDS = {
    "а", "без", "бы", "был", "была", "были", "быть", "в", "вам", "вас", "вы", "где", "да", "для",
    "до", "его", "ее", "если", "есть", "же", "за", "здесь", "и", "из", "или", "их", "к", "как", "какой",
    "когда", "ли", "мне", "мой", "моя", "мы", "на", "не", "нет", "но", "о", "он", "она", "они", "от",
    "по", "под", "при", "про", "с", "со", "так", "там", "то", "у", "уже", "что", "это", "я", "можно",
    "нужно", "хочу", "подскажите", "здравствуйте", "добрый", "день", "вечер", "утро",
}
GENERIC_TRIGGER_WORDS = {
    "аккаунт", "сервер", "игрок", "админ", "администратор", "модер", "модератор", "бан", "банят", "забанили",
    "донат", "привилегия", "правила", "помощь", "проблема", "ошибка", "вопрос", "сайт", "вк", "телеграм",
    "пароль", "ник", "майнкрафт", "зайти", "войти", "купил", "покупка", "ресурсы",
}


def contains_phrase(text: str, phrase: str) -> bool:
    """Token-boundary phrase matching. 'бан' no longer matches 'банковские'."""
    haystack = normalize(text)
    needle_value = normalize(phrase)
    if not haystack or not needle_value:
        return False
    return f" {needle_value} " in f" {haystack} "


def meaningful_tokens(text: str) -> list[str]:
    return [token for token in tokens(text) if token not in STOPWORDS and not token.isdigit() and len(token) > 1]


def is_noise_question(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return True
    compact = " ".join(value.split())
    if SYSTEM_TICKET_HEADER_RE.search(compact):
        return True
    lines = [line.strip() for line in value.replace("\r", "\n").split("\n") if line.strip()]
    if lines and all(SYSTEM_ONLY_RE.match(line) for line in lines):
        return True
    normalized = normalize(value)
    if not normalized:
        return True
    alpha_tokens = [token for token in tokens(value) if any(ch.isalpha() for ch in token)]
    useful = meaningful_tokens(value)
    # Service headers like "2026 ... TG 123 - 1 сообщение" have almost no human content.
    if len(alpha_tokens) <= 2 and any(word in normalized for word in ("сообщение", "tg", "vk")):
        return True
    if len(useful) == 0:
        return True
    return False


def matches_learning_exclusion(text: str, exclusions: Iterable[str]) -> bool:
    return any(contains_phrase(text, phrase) for phrase in exclusions if normalize(str(phrase)))


def phrase_specificity(phrase: str) -> float:
    parts = meaningful_tokens(phrase)
    if not parts:
        return 0.0
    if len(parts) >= 3:
        return 1.0
    if len(parts) == 2:
        return 0.82
    return 0.25 if parts[0] in GENERIC_TRIGGER_WORDS else 0.60


def token_bigrams(text: str) -> set[tuple[str, str]]:
    values = meaningful_tokens(text)
    return set(zip(values, values[1:])) if len(values) > 1 else set()


@dataclass(slots=True)
class Decision:
    ticket_id: str
    decision: str
    confidence: int
    draft: str = ""
    reason: str = ""
    matched_question: str = ""
    template_count: int = 0
    template_id: Optional[int] = None
    template_name: str = ""
    auto_allowed: bool = False
    close_requested: bool = False


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return " ".join(TOKEN_RE.findall(text))


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower().replace("ё", "е"))


def char_ngrams(text: str, n: int = 3) -> Counter[str]:
    value = f"  {normalize(text)}  "
    if len(value) < n:
        return Counter({value: 1}) if value.strip() else Counter()
    return Counter(value[index : index + n] for index in range(len(value) - n + 1))


def cosine_counter(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def cosine_dict(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0.0) for key, value in left.items())
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def canonical_answer(answer: str) -> str:
    answer = answer.strip()
    if "{ticket_id}" in answer:
        return answer
    return re.sub(r"^(\s*t\s+a\s+)\d{3,12}", r"\1{ticket_id}", answer, count=1, flags=re.IGNORECASE)


def template_key(answer: str) -> str:
    canonical = re.sub(r"\s+", " ", canonical_answer(answer)).strip().lower()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_answer(answer: str, ticket_id: str) -> str:
    template = canonical_answer(answer)
    if "{ticket_id}" in template:
        return template.replace("{ticket_id}", ticket_id)
    return re.sub(r"^(\s*t\s+a\s+)\d{3,12}", rf"\g<1>{ticket_id}", answer.strip(), count=1, flags=re.IGNORECASE)


def answer_body(answer: str) -> str:
    value = TEMPLATE_PREFIX_RE.sub("", canonical_answer(answer), count=1)
    value = re.sub(r"\s*@@@\s*$", "", value, flags=re.DOTALL)
    return value.strip()


def split_phrases(value: str) -> list[str]:
    """Split user-authored rules by lines without destroying punctuation.

    Older builds normalized rules before saving them, so ``1.21.4`` became
    ``1 21 4`` and commas/semicolons were silently treated as separators.
    Rules are now stored exactly as the operator typed them (apart from
    trimming surrounding/repeated whitespace). Matching can still use the
    tolerant token normalizer, so this is backward compatible with old data.
    """
    parts = (value or "").replace("\r", "\n").split("\n")
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = " ".join(part.strip().split())
        key = cleaned.casefold().replace("ё", "е")
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def is_generalizable_answer(answer: str) -> bool:
    """Return True only when an answer is safe to reuse for similar situations."""
    body = answer_body(answer)
    normalized = normalize(body)
    body_tokens = tokens(body)
    if not body or not normalized:
        return False
    # Links and established long instructions can be useful even with few natural-language words.
    if ("http://" in body.lower() or "https://" in body.lower()) and len(body) >= 16:
        return True
    stripped = re.sub(r"^здравствуйте\s*[,!.:-]*\s*", "", normalized).strip()
    generic = {
        "здравствуйте",
        "на сервер",
        "сюда",
        "да",
        "нет",
        "куда",
        "уточните",
        "попробуйте еще раз",
        "ожидайте",
        "не знаю",
        "в информации",
    }
    if normalized in generic or stripped in generic:
        return False
    if len(normalized) < 18 or len(body_tokens) < 3:
        return False
    if len(stripped) < 10 or len(tokens(stripped)) < 2:
        return False
    return True


def has_meaningful_manual_body(answer: str) -> bool:
    body = answer_body(answer)
    normalized = normalize(body)
    if not normalized:
        return False
    stripped = re.sub(r"^здравствуйте\s*", "", normalized).strip()
    if not stripped:
        return False
    return len(tokens(stripped)) >= 2 or "http" in body.lower() or any(character.isdigit() for character in body)


def detect_intents(text: str) -> set[str]:
    groups: dict[str, tuple[str, ...]] = {
        "punishment": (
            "забанили", "бан без причины", "бан ни за что", "мут", "наказание", "античит", "читер", "читы",
            "модератор забанил", "администратор забанил", "хелпер забанил", "жалоба", "примите меры", "без проверки", "варп pvp",
        ),
        "account_recovery": (
            "взломали аккаунт", "украли аккаунт", "угнали аккаунт", "потерял доступ", "не помню пароль", "пароль неправильный",
            "сменили пароль", "вернуть аккаунт", "чужой вк", "не могу войти в аккаунт", "восстановить аккаунт",
        ),
        "account_link": (
            "отвязать аккаунт", "перепривязать", "допривязать", "сменить привязку", "привязать другой", "сменить вк", "сменить телеграм",
        ),
        "resource_pack": ("ресурспак", "ресурс пак", "пакет ресурсов", "текстур пак", "текстуры не загружаются"),
        "connection": (
            "не заходит на сервер", "не могу зайти на сервер", "не подключается", "превышено время ожидания",
            "версия сервера", "айпи сервера", "ip сервера", "сервер недоступен",
        ),
        "wipe": ("вайп", "обнуление", "пропали ресурсы", "пропал дом", "пропали дома"),
        "two_factor": ("2фа", "2 fa", "двухэтапная", "двух этапная", "двухфакторная", "авторизация 2fa"),
        "rules_mods": ("разрешен ли", "разрешено ли", "можно ли мод", "визуал", "тотем свап", "преимущество в геймплее", "правила привилегии"),
        "idea": ("идея для сервера", "предложение для сервера", "предложить идею"),
        "bug": ("нашел баг", "нашёл баг", "дюп", "сообщить о баге"),
        "waiting": ("когда ответят", "не отвечают", "ожидаю ответа", "долго рассматривают"),
        "payment": ("оплата", "карта", "банковская карта", "платеж", "платёж", "купить донат", "покупка привилегии"),
        "privileges": ("привилегия", "донатер", "возможности привилегии", "правила привилегии"),
    }
    result: set[str] = set()
    for name, phrases in groups.items():
        if any(contains_phrase(text, phrase) for phrase in phrases):
            result.add(name)
    return result


# 2.9.6: deterministic semantic-context engine. It intentionally does not use a generative
# model. Generic nouns such as "аккаунт" and staff roles are only anchors; the
# surrounding action words decide the meaning of the ticket.
_CONTEXT_ROOTS: dict[str, tuple[str, ...]] = {
    "punishment": ("забан", "бан", "блокиров", "замут", "мут", "наказ", "разбан", "обжал", "жалоб"),
    "account_recovery": (
        "взлом", "взломан", "укра", "украден", "угнал", "увел", "увёл", "увели", "стащ",
        "потер", "доступ", "парол", "восстанов", "забыл", "забы", "войти", "вход", "вош", "зашел", "зашёл", "зашл", "сменил",
    ),
    # Primary binding ("как привязать аккаунт") is intentionally NOT an answer intent.
    # Only changing/removing an existing binding belongs to the relink template.
    "account_link": ("отвяз", "перепривяз", "допривяз", "перенест", "сменить", "замен"),
    "payment": ("оплат", "платеж", "платёж", "карт", "донат", "покуп", "купил", "купить"),
    "connection": ("зайт", "войти", "подключ", "таймаут", "ожидан", "недоступ", "соедин"),
    "rules": ("правил", "разреш", "запрещ", "можно", "нельзя"),
    "privileges": ("привилег", "донатер", "фобос", "цербер", "хелпер"),
    "bug": ("баг", "дюп", "ошибк", "сломал", "не работает"),
}

_ACCOUNT_ANCHORS = ("аккаунт", "акк", "ак", "профиль", "учетка", "учётка")
_STAFF_ANCHORS = ("хелпер", "модер", "модератор", "админ", "администратор")

# Binding is deliberately split into two different intents.
# "Привязать впервые" has no supported canned answer and must be ignored, while
# "отвязать / перепривязать / сменить существующую привязку" may use the relink template.
_RELINK_MARKERS = (
    "отвяз", "перепривяз", "допривяз", "сменить привяз", "смена привяз", "поменять привяз",
    "заменить привяз", "перенести привяз", "привязать другой", "привязать другую",
    "привязать к другому", "привязать к другой", "другой вк", "другой телеграм",
    "новый вк", "новый телеграм", "сменить вк", "сменить телеграм", "поменять вк",
    "поменять телеграм", "отвязать вк", "отвязать телеграм",
)
_PRIMARY_LINK_MARKERS = (
    "как привязать", "хочу привязать", "нужно привязать", "надо привязать", "можно привязать",
    "привязать аккаунт", "привязать акк", "привязать профиль", "привязка аккаунта",
    "привязка акка", "привязать вк к аккаунту", "привязать телеграм к аккаунту",
    "привязать telegram", "привязать вк", "привязать tg", "привязать тг", "первая привязка", "впервые привязать",
)


def account_binding_action(text: str) -> str:
    """Return 'relink', 'primary' or ''. Relink always wins over generic binding wording."""
    normalized = normalize(text)
    if not normalized:
        return ""
    if any(marker in normalized for marker in _RELINK_MARKERS):
        return "relink"
    # Morphological fallback for the destructive/change forms.
    values = tokens(text)
    if any(value.startswith(("отвяз", "перепривяз", "допривяз")) for value in values):
        return "relink"
    # "привязать аккаунт к другому VK" has the generic verb but clearly means replacement.
    if (
        any(value.startswith("привяз") for value in values)
        and any(value in _ACCOUNT_ANCHORS for value in values)
        and any(value.startswith(("друг", "нов", "другой")) for value in values)
        and any(value.startswith(("вк", "телеграм", "telegram", "tg", "тг")) for value in values)
    ):
        return "relink"
    if any(marker in normalized for marker in _PRIMARY_LINK_MARKERS):
        return "primary"
    # A plain verb "привязать/привязка" near an account anchor means first binding,
    # unless a relink marker above was present.
    has_account = any(value in _ACCOUNT_ANCHORS for value in values)
    has_plain_bind = any(value.startswith("привяз") for value in values)
    if has_account and has_plain_bind:
        return "primary"
    return ""

_ACCOUNT_RECOVERY_BASELINE_QUESTIONS = (
    "мой аккаунт взломали",
    "взломали мой аккаунт",
    "мой акк взломали",
    "украли аккаунт",
    "у меня украли аккаунт",
    "аккаунт угнали",
    "мой аккаунт угнали",
    "аккаунт взломан что делать",
    "что делать если взломали аккаунт",
    "вернуть доступ к аккаунту после взлома",
    "кто-то получил доступ к моему аккаунту",
    "кто-то зашел в мой аккаунт",
    "кто-то зашел в мой аккаунт и сменил пароль",
    "сменили пароль от моего аккаунта",
    "после взлома не могу войти в аккаунт",
    "не могу войти в аккаунт после взлома",
    "потерял доступ к аккаунту",
    "нет доступа к моему аккаунту",
    "забыл пароль от аккаунта",
    "не помню пароль от аккаунта",
    "мой аккаунт украли и я не могу войти",
    "чужой человек получил доступ к моему игровому аккаунту",
    "как восстановить взломанный аккаунт",
    "как вернуть украденный аккаунт",
    "мой акк взломали что делать",
    "акк взломан",
    "мой аккаунт украли",
    "мой акк украли",
    "мой игровой аккаунт взломали",
    "мой игровой аккаунт украли",
    "у меня увели аккаунт",
    "у меня увели акк",
    "кто то взломал мой акк",
    "кто то взломал мой аккаунт",
    "кто то украл мой аккаунт",
    "чужой человек зашел на аккаунт",
    "чужой человек вошел в мой аккаунт",
    "на мой аккаунт зашел другой человек",
    "кто то сменил пароль от аккаунта",
    "пароль от аккаунта изменили без меня",
    "не могу зайти в аккаунт после взлома",
    "верните мой аккаунт его украли",
    "помогите восстановить аккаунт после кражи",
    "доступ к аккаунту получил другой человек",
)

_ACCOUNT_RECOVERY_BASELINE_ANSWER = """t a {ticket_id} Ваш аккаунт взломали или утерян доступ. 

💼 Актуальная инструкция по восстановлению аккаунта: 

1. Проверьте привязку к нашему боту 
• ВКонтакте: vk.com/aresmine 

• Telegram: t.me/AresMineRobot 

В беседе нажмите «🛡 Привязанные аккаунты», затем — «🔑 Сменить пароль» и введите новый. 

Если аккаунт не привязан, то следуем следующим правилам:

2. Найдите чеки от покупок на почте, которую указывали при оплате. 
💡 Чеки принимаются только с почты, выписки и фотографии экрана не подходят! 

3. Обратитесь в службу технической поддержки: 
🔗 vk.com/aresmine_help 

4. Составьте заявку строго по форме: 

1) Ваш никнейм: 
2) Привилегия: 
3) Домен (IP), с которого вы заходили (пример: hot.aresmine.me): 
4) Город и примерная дата регистрации (пример: 10.10.2023, г. Москва): 
5) Скриншоты чеков всех покупок (только с устройства, где открыт чек): 


❗️ ВНИМАНИЕ 
• Заявка рассматривается только при наличии хотя бы одной покупки. 
• Срок восстановления — до 30 рабочих дней. 
• Не спамьте — заявки обрабатываются в порядке очереди. 
• Поддержка имеет право отказать без объяснения причины. 
• При оскорблениях, подделке чеков или спаме — блокировка в сообществе. @@@"""



def _starts_with_any(value: str, roots: Iterable[str]) -> bool:
    for root in roots:
        if root == "бан":
            # Do not treat "банковская" as a punishment.
            if value in {"бан", "бана", "бане", "баном", "баны", "банить", "банят", "банит"}:
                return True
            continue
        if value.startswith(root):
            return True
    return False


def context_concepts(text: str) -> dict[str, float]:
    """Return weighted semantic concepts based on words *around* anchors.

    The weights are deliberately explainable and cheap. A bare word like "аккаунт"
    contributes nothing by itself. "аккаунт забанили" becomes punishment,
    "аккаунт потерял доступ" becomes account_recovery, and "аккаунт привязать"
    becomes account_link.
    """
    raw_tokens = tokens(text)
    values = [value for value in raw_tokens if value and not value.isdigit()]
    scores: Counter[str] = Counter()

    # Direct action words. These are actions/intent markers rather than generic nouns.
    for value in values:
        if _starts_with_any(value, _CONTEXT_ROOTS["punishment"]):
            scores["punishment"] += 1.8
        if _starts_with_any(value, _CONTEXT_ROOTS["account_recovery"]):
            scores["account_recovery"] += 1.25
        if _starts_with_any(value, _CONTEXT_ROOTS["account_link"]):
            scores["account_link"] += 1.45
        if _starts_with_any(value, _CONTEXT_ROOTS["payment"]):
            scores["payment"] += 1.2
        if _starts_with_any(value, _CONTEXT_ROOTS["connection"]):
            scores["connection"] += 0.9
        if _starts_with_any(value, _CONTEXT_ROOTS["rules"]):
            scores["rules"] += 0.9
        if _starts_with_any(value, _CONTEXT_ROOTS["bug"]):
            scores["bug"] += 1.1

    normalized = normalize(text)
    binding_action = account_binding_action(text)
    if binding_action == "relink":
        scores["account_link"] += 4.2
    elif binding_action == "primary":
        # Do not let the generic stem «привяз» become a relink signal.
        scores["account_link"] = 0.0

    if any(contains_phrase(text, phrase) for phrase in ("просто так", "без причины", "ни за что", "не за что", "по ошибке")):
        if scores["punishment"] > 0:
            scores["punishment"] += 2.3
    if contains_phrase(text, "хочу подать жалобу") or contains_phrase(text, "куда жаловаться"):
        scores["punishment"] += 2.0
    if contains_phrase(text, "забыл пароль") or contains_phrase(text, "не помню пароль"):
        scores["account_recovery"] += 2.4
    if contains_phrase(text, "потерял доступ") or contains_phrase(text, "нет доступа"):
        scores["account_recovery"] += 2.2
    if contains_phrase(text, "взломали аккаунт") or contains_phrase(text, "украли аккаунт"):
        scores["account_recovery"] += 2.8
    recovery_phrases = (
        "акк взломали", "акк взломан", "аккаунт взломан", "мой акк взломали",
        "мой аккаунт взломали", "акк украли", "аккаунт украли", "увели аккаунт",
        "увели акк", "угнали акк", "угнали аккаунт", "сменили пароль", "изменили пароль",
        "чужой зашел", "чужой вошел", "другой человек зашел", "другой человек вошел",
        "кто то зашел", "кто то вошел", "кто-то зашел", "кто-то вошел",
    )
    if any(contains_phrase(text, phrase) for phrase in recovery_phrases):
        scores["account_recovery"] += 3.4

    # Anchor windows. This is the important part for generic words such as "аккаунт".
    for index, value in enumerate(values):
        if value not in _ACCOUNT_ANCHORS:
            continue
        left = max(0, index - 4)
        right = min(len(values), index + 5)
        window = values[left:right]
        if any(_starts_with_any(word, _CONTEXT_ROOTS["punishment"]) for word in window):
            scores["punishment"] += 3.4
        if any(_starts_with_any(word, _CONTEXT_ROOTS["account_recovery"]) for word in window):
            scores["account_recovery"] += 3.6
        if binding_action == "relink" and any(_starts_with_any(word, _CONTEXT_ROOTS["account_link"]) for word in window):
            scores["account_link"] += 3.8
        if any(_starts_with_any(word, _CONTEXT_ROOTS["payment"]) for word in window):
            scores["payment"] += 2.2

    # Staff names are not intents. They matter only when an action around them explains
    # what the staff member did.
    for index, value in enumerate(values):
        if value not in _STAFF_ANCHORS:
            continue
        left = max(0, index - 5)
        right = min(len(values), index + 6)
        window = values[left:right]
        if any(_starts_with_any(word, _CONTEXT_ROOTS["punishment"]) for word in window):
            scores["punishment"] += 2.7

    # Avoid treating a generic "карта" in unrelated prose as a strong payment intent.
    if scores["payment"] and not any(_starts_with_any(v, ("оплат", "платеж", "платёж", "покуп", "куп", "донат", "карт")) for v in values):
        scores["payment"] *= 0.5

    # Normalize tiny accidental hits away. Keeping absolute strength lets the classifier
    # distinguish a strong contextual pair from a weak one-word hint.
    return {name: round(float(score), 3) for name, score in scores.items() if score >= 1.0}


def concept_alignment(question: dict[str, float], profile: dict[str, float]) -> float:
    if not question or not profile:
        return 0.0
    strongest = max(question.values())
    if strongest <= 0:
        return 0.0
    weighted = 0.0
    total = 0.0
    for name, strength in question.items():
        weight = strength / strongest
        weighted += weight * float(profile.get(name, 0.0))
        total += weight
    return max(0.0, min(1.0, weighted / total if total else 0.0))


def dominant_concept(concepts: dict[str, float]) -> tuple[str, float]:
    if not concepts:
        return "", 0.0
    name = max(concepts, key=concepts.get)
    return name, float(concepts[name])


def _clean_question_lines(lines: list[str]) -> str:
    cleaned: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        lower = line.lower()
        if lower.startswith(("переслано от", "⚡", "поступил тикет", "поступил ответ")):
            continue
        if SYSTEM_TICKET_HEADER_RE.search(line) or SYSTEM_ONLY_RE.match(line):
            continue
        if re.match(r"^\[\d+\s+влож", line, re.IGNORECASE):
            continue
        cleaned.append(line)
    value = "\n".join(cleaned).strip()
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value


def parse_ticket(text: str) -> Optional[tuple[str, str]]:
    """Extract one ticket id and only the player's complete message, never service metadata."""
    matches = TICKET_RE.findall(text)
    identifiers = [left or right for left, right in matches]
    unique = list(dict.fromkeys(identifiers))
    if len(unique) != 1:
        return None
    ticket_id = unique[0]

    question_marker = re.search(r"\bQUESTION:\s*\n?(.*)$", text, re.IGNORECASE | re.DOTALL)
    if question_marker:
        question = _clean_question_lines(question_marker.group(1).splitlines())
        return (ticket_id, question) if question and not is_noise_question(question) else None

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    player_parts: list[str] = []
    active_player_block = False
    found_player = False

    for raw_line in lines:
        line = raw_line.strip()
        author_match = DATE_AUTHOR_RE.match(line)
        if author_match:
            author = normalize(author_match.group(1))
            content = author_match.group(2).strip()
            if "игрок" in author:
                found_player = True
                active_player_block = True
                if content:
                    player_parts.append(content)
                continue
            if active_player_block:
                active_player_block = False
            continue

        direct_player = re.search(r"(?:^|\])\s*Игрок:\s*(.*)$", line, re.IGNORECASE)
        if direct_player:
            found_player = True
            active_player_block = True
            content = direct_player.group(1).strip()
            if content:
                player_parts.append(content)
            continue

        if active_player_block:
            # Another dated/system line closes the player block instead of becoming part of the question.
            if TICKET_RE.search(line) and ("tg-" in line.lower() or "vk-" in line.lower()):
                active_player_block = False
                continue
            player_parts.append(raw_line)

    if found_player:
        question = _clean_question_lines(player_parts)
        if question and not is_noise_question(question):
            return ticket_id, question

    quoted: list[str] = []
    quote_started = False
    for raw_line in lines:
        stripped = raw_line.lstrip()
        if stripped.startswith(">"):
            quote_started = True
            quoted.append(stripped[1:].lstrip())
        elif quote_started and raw_line.strip() and not TICKET_RE.search(raw_line):
            quoted.append(raw_line.strip())
    question = _clean_question_lines(quoted)
    if question and not is_noise_question(question):
        return ticket_id, question

    # Conservative fallback: only use lines after the first blank separator and remove all service headers.
    body: list[str] = []
    header_finished = False
    for raw_line in lines:
        line = raw_line.strip()
        if not header_finished:
            if not line:
                header_finished = True
            continue
        body.append(raw_line)
    question = _clean_question_lines(body)
    return (ticket_id, question) if question and not is_noise_question(question) else None


def _suggest_template_name(answer: str) -> str:
    value = normalize(answer)
    if "кнопке информация" in value:
        return "Информация · правила и жалобы"
    if "восстанов" in value and "аккаунт" in value:
        return "Восстановление аккаунта"
    if "перепривяз" in value or "допривяз" in value or "аккаунты не отвязыва" in value:
        return "Привязка и перепривязка"
    if "ресурспак" in value or "пакет ресурс" in value:
        return "Серверный ресурспак"
    if "альтернатив" in value and ("адрес" in value or "айпи" in value):
        return "Подключение к серверу"
    if "вайп" in value:
        return "Вайп"
    if "пользовательского соглашения" in value or "возврат" in value:
        return "Возврат ресурсов и имущества"
    cleaned = answer_body(answer)
    cleaned = re.sub(r"^\s*Здравствуйте\s*[,!.:-]*\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split()).strip(" -—:;,.!")
    if not cleaned:
        return "Новый шаблон"
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    return (first_sentence[:58] + "…") if len(first_sentence) > 58 else first_sentence


class KnowledgeBase:
    def __init__(self, db_path: Path, seed_path: Path):
        self.db_path = db_path
        self.seed_path = seed_path
        self.lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path, check_same_thread=False, timeout=20)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        self._migrate_schema()
        self._migrate_obvious_version_punctuation_v2965()
        self.import_seed()
        self._sync_templates()
        self._install_context_baselines_v295()
        self._install_semantic_baselines_v296()
        self._cleanup_low_quality_templates_once()
        self._cleanup_auto_generated_oneoffs_once()
        self._cleanup_old_observed()
        self.reply_guard = ReplyGuard(self.db_path)
        self.reload()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS templates(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                answer_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                answer_template TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                match_mode TEXT NOT NULL DEFAULT 'smart',
                triggers TEXT NOT NULL DEFAULT '',
                negative_triggers TEXT NOT NULL DEFAULT '',
                close_triggers TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 0,
                reply_limit_enabled INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'seed',
                is_locked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS examples(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'seed',
                template_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(question, answer),
                FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS exact_rules(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                normalized_question TEXT NOT NULL UNIQUE,
                answer TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS pending(
                ticket_id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS observed(
                ticket_id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                predicted_template_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS processed(
                ticket_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT,
                question TEXT,
                action TEXT,
                confidence INTEGER,
                answer TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS metadata(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self.connection.commit()

    def _migrate_schema(self) -> None:
        with self.lock:
            pending_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(pending)")}
            if "updated_at" not in pending_columns:
                self.connection.execute("ALTER TABLE pending ADD COLUMN updated_at TEXT")
                self.connection.execute("UPDATE pending SET updated_at=COALESCE(created_at, CURRENT_TIMESTAMP)")

            example_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(examples)")}
            if "source" not in example_columns:
                self.connection.execute("ALTER TABLE examples ADD COLUMN source TEXT DEFAULT 'seed'")
            if "created_at" not in example_columns:
                self.connection.execute("ALTER TABLE examples ADD COLUMN created_at TEXT")
                self.connection.execute("UPDATE examples SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL")
            if "template_id" not in example_columns:
                self.connection.execute("ALTER TABLE examples ADD COLUMN template_id INTEGER")

            observed_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(observed)")}
            if observed_columns:
                if "predicted_template_id" not in observed_columns:
                    self.connection.execute("ALTER TABLE observed ADD COLUMN predicted_template_id INTEGER")
                if "created_at" not in observed_columns:
                    self.connection.execute("ALTER TABLE observed ADD COLUMN created_at TEXT")
                    self.connection.execute("UPDATE observed SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL")
                if "updated_at" not in observed_columns:
                    self.connection.execute("ALTER TABLE observed ADD COLUMN updated_at TEXT")
                    self.connection.execute("UPDATE observed SET updated_at=COALESCE(created_at, CURRENT_TIMESTAMP)")

            template_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(templates)")}
            additions = {
                "name": "TEXT NOT NULL DEFAULT 'Шаблон ответа'",
                "answer_template": "TEXT NOT NULL DEFAULT ''",
                "enabled": "INTEGER NOT NULL DEFAULT 1",
                "match_mode": "TEXT NOT NULL DEFAULT 'smart'",
                "triggers": "TEXT NOT NULL DEFAULT ''",
                "negative_triggers": "TEXT NOT NULL DEFAULT ''",
                "close_triggers": "TEXT NOT NULL DEFAULT ''",
                "priority": "INTEGER NOT NULL DEFAULT 0",
                "reply_limit_enabled": "INTEGER NOT NULL DEFAULT 1",
                "source": "TEXT NOT NULL DEFAULT 'seed'",
                # Защита от удаления (2.9.9.6+). Старые записи получают 0 = "не защищён",
                # поэтому миграция ничего не блокирует без ведома пользователя.
                "is_locked": "INTEGER NOT NULL DEFAULT 0",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            }
            for column, declaration in additions.items():
                if column not in template_columns:
                    self.connection.execute(f"ALTER TABLE templates ADD COLUMN {column} {declaration}")
            self.connection.execute("UPDATE templates SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL")
            self.connection.execute("UPDATE templates SET updated_at=COALESCE(created_at, CURRENT_TIMESTAMP) WHERE updated_at IS NULL")
            self.connection.execute("UPDATE templates SET match_mode='smart' WHERE match_mode IS NULL OR match_mode='' ")
            # Проверка загружаемых данных: в поле защиты допустимы только 0 и 1. Значения,
            # попавшие туда руками или из чужой базы (NULL, 'true', 2…), приводятся к булеву
            # виду; явное "1"/"true" считается защитой, всё прочее — её отсутствием.
            self.connection.execute(
                "UPDATE templates SET is_locked=CASE WHEN lower(CAST(is_locked AS TEXT)) IN ('1','true','yes') THEN 1 ELSE 0 END "
                "WHERE is_locked IS NULL OR is_locked NOT IN (0, 1)"
            )
            self.connection.commit()

    def _migrate_obvious_version_punctuation_v2965(self) -> None:
        """Restore dots in old rules where a Minecraft version was clearly normalized.

        Previous builds already discarded punctuation in persisted phrases. We cannot
        reconstruct arbitrary punctuation safely, but strings such as ``1 21 4`` and
        ``с версии 1 21 11`` are unambiguous enough to repair once.
        """
        with self.lock:
            if self._metadata("punctuation_migration_v2965") == "1":
                return
            rows = list(self.connection.execute("SELECT id, triggers, negative_triggers, close_triggers FROM templates"))
            changed = 0
            for row in rows:
                values = []
                row_changed = False
                for raw in row[1:4]:
                    lines = str(raw or "").replace("\r", "\n").split("\n")
                    fixed_lines = []
                    for line in lines:
                        original = line
                        stripped = line.strip()
                        if re.fullmatch(r"1\s+\d{1,2}\s+\d{1,2}", stripped) or re.search(r"\bверс(?:ия|ии|ию|ией|и|)\b", stripped, re.IGNORECASE):
                            line = re.sub(r"\b1\s+(\d{1,2})\s+(\d{1,2})\b", r"1.\1.\2", line)
                        if line != original:
                            row_changed = True
                        fixed_lines.append(line)
                    values.append("\n".join(fixed_lines))
                if row_changed:
                    self.connection.execute(
                        "UPDATE templates SET triggers=?, negative_triggers=?, close_triggers=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (*values, int(row[0])),
                    )
                    changed += 1
            self._set_metadata("punctuation_migration_v2965", "1")
            self._set_metadata("punctuation_migration_v2965_changed", str(changed))
            self.connection.commit()

    def close(self) -> None:
        self.reply_guard.close()
        with self.lock:
            self.connection.close()

    def _metadata(self, key: str, default: str = "") -> str:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def _set_metadata(self, key: str, value: str) -> None:
        self.connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES(?,?)", (key, value))

    def _upsert_exact_rule(self, question: str, answer: str, source: str = "manual") -> None:
        normalized_question = normalize(question)
        if not normalized_question:
            return
        self.connection.execute(
            """
            INSERT INTO exact_rules(question, normalized_question, answer, source, updated_at)
            VALUES(?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(normalized_question) DO UPDATE SET
                question=excluded.question,
                answer=excluded.answer,
                source=excluded.source,
                updated_at=CURRENT_TIMESTAMP
            """,
            (question.strip(), normalized_question, canonical_answer(answer), source),
        )

    def import_seed(self) -> None:
        if not self.seed_path.exists() or self._metadata("seed_deleted") == "1":
            return
        seed_hash = hashlib.sha256(self.seed_path.read_bytes()).hexdigest()
        with self.lock:
            if self._metadata("seed_hash") == seed_hash:
                return
            text = self.seed_path.read_text(encoding="utf-8", errors="replace")
            for block in text.split("=" * 72):
                match = re.search(r"QUESTION:\n(.*?)\n\nANSWER:\n(.*)\Z", block.strip(), re.DOTALL)
                if not match:
                    continue
                question, answer = (part.strip() for part in match.groups())
                if not question or not answer:
                    continue
                if is_generalizable_answer(answer):
                    self.connection.execute(
                        "INSERT OR IGNORE INTO examples(question, answer, source) VALUES(?,?, 'seed')",
                        (question, answer),
                    )
                else:
                    self._upsert_exact_rule(question, answer, "seed")
            self._set_metadata("seed_hash", seed_hash)
            self.connection.commit()

    def _ensure_template(self, answer: str, source: str = "manual", match_mode: Optional[str] = None) -> int:
        canonical = canonical_answer(answer)
        key = template_key(canonical)
        row = self.connection.execute("SELECT id FROM templates WHERE answer_key=?", (key,)).fetchone()
        if row:
            return int(row[0])
        mode = match_mode or ("smart" if is_generalizable_answer(canonical) else "exact")
        cursor = self.connection.execute(
            "INSERT INTO templates(answer_key, name, answer_template, match_mode, source) VALUES(?,?,?,?,?)",
            (key, _suggest_template_name(canonical), canonical, mode, source),
        )
        return int(cursor.lastrowid)

    def _sync_templates(self) -> None:
        with self.lock:
            rows = list(self.connection.execute("SELECT id, answer, source, template_id FROM examples ORDER BY id"))
            for row in rows:
                template_id = row[3]
                if template_id and self.connection.execute("SELECT 1 FROM templates WHERE id=?", (template_id,)).fetchone():
                    continue
                template_id = self._ensure_template(str(row[1]), str(row[2] or "seed"), "smart")
                self.connection.execute("UPDATE examples SET template_id=? WHERE id=?", (template_id, row[0]))
            self.connection.commit()

    def _install_context_baselines_v295(self) -> None:
        """Install a small one-time context pack for obvious account recovery phrases.

        Existing user edits are respected: if a recovery template already exists by name,
        the examples are attached to that template instead of creating a duplicate. A
        user-disabled template is not force-enabled.
        """
        if self._metadata("context_baselines_v295") == "1":
            return

        with self.lock:
            canonical_default = canonical_answer(_ACCOUNT_RECOVERY_BASELINE_ANSWER)
            default_key = template_key(canonical_default)

            row = self.connection.execute(
                "SELECT id, answer_template, enabled FROM templates WHERE answer_key=? LIMIT 1",
                (default_key,),
            ).fetchone()
            if not row:
                row = self.connection.execute(
                    """
                    SELECT id, answer_template, enabled
                    FROM templates
                    WHERE lower(name) LIKE '%восстановлен%аккаунт%'
                       OR lower(name) LIKE '%взлом%аккаунт%'
                    ORDER BY id
                    LIMIT 1
                    """
                ).fetchone()

            created = False
            if row:
                template_id = int(row[0])
                answer_template = str(row[1] or canonical_default)
            else:
                template_id = self._ensure_template(canonical_default, "seed", "smart")
                answer_template = canonical_default
                created = True
                self.connection.execute(
                    """
                    UPDATE templates
                    SET name='Восстановление аккаунта', source='seed', enabled=1,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (template_id,),
                )

            inserted = 0
            for question in _ACCOUNT_RECOVERY_BASELINE_QUESTIONS:
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO examples(question, answer, source, template_id)
                    VALUES(?,?, 'seed_context', ?)
                    """,
                    (question, answer_template, template_id),
                )
                inserted += max(0, int(cursor.rowcount or 0))

            self._set_metadata("context_baselines_v295", "1")
            self._set_metadata("context_baselines_v295_inserted", str(inserted))
            self._set_metadata("context_baselines_v295_template", str(template_id))
            self.connection.commit()

    def _install_semantic_baselines_v296(self) -> None:
        """Repair the account-recovery semantic pack on existing databases.

        Older builds could mark the previous one-time migration as completed even when
        the recovery template was later deleted. 2.9.6 uses a separate migration key and
        recreates the canonical template only when no matching template exists at all.
        It never re-enables a template the user explicitly disabled.
        """
        if self._metadata("semantic_baselines_v296") == "1":
            return

        with self.lock:
            canonical_default = canonical_answer(_ACCOUNT_RECOVERY_BASELINE_ANSWER)
            default_key = template_key(canonical_default)
            row = self.connection.execute(
                "SELECT id, answer_template, enabled FROM templates WHERE answer_key=? LIMIT 1",
                (default_key,),
            ).fetchone()
            if not row:
                row = self.connection.execute(
                    """
                    SELECT id, answer_template, enabled
                    FROM templates
                    WHERE lower(name) LIKE '%восстановлен%аккаунт%'
                       OR lower(name) LIKE '%взлом%аккаунт%'
                    ORDER BY enabled DESC, id
                    LIMIT 1
                    """
                ).fetchone()

            created = False
            if row:
                template_id = int(row[0])
                answer_template = str(row[1] or canonical_default)
            else:
                template_id = self._ensure_template(canonical_default, "seed_context", "smart")
                answer_template = canonical_default
                created = True
                self.connection.execute(
                    """
                    UPDATE templates
                    SET name='Восстановление аккаунта', source='seed_context', enabled=1,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (template_id,),
                )

            inserted = 0
            for question in _ACCOUNT_RECOVERY_BASELINE_QUESTIONS:
                exists = self.connection.execute(
                    "SELECT 1 FROM examples WHERE template_id=? AND lower(question)=lower(?) LIMIT 1",
                    (template_id, question),
                ).fetchone()
                if exists:
                    continue
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO examples(question, answer, source, template_id)
                    VALUES(?,?, 'seed_context_v296', ?)
                    """,
                    (question, answer_template, template_id),
                )
                inserted += max(0, int(cursor.rowcount or 0))

            self._set_metadata("semantic_baselines_v296", "1")
            self._set_metadata("semantic_baselines_v296_inserted", str(inserted))
            self._set_metadata("semantic_baselines_v296_template", str(template_id))
            self._set_metadata("semantic_baselines_v296_created", "1" if created else "0")
            self.connection.commit()

    def _cleanup_low_quality_templates_once(self) -> None:
        if self._metadata("template_cleanup_v26") == "1":
            return
        with self.lock:
            rows = list(self.connection.execute("SELECT id, answer_template, match_mode FROM templates WHERE is_locked=0"))
            moved = 0
            for template_id, answer_template, match_mode in rows:
                if str(match_mode or "smart") == "exact" or is_generalizable_answer(str(answer_template)):
                    continue
                examples = list(self.connection.execute("SELECT question, answer, source FROM examples WHERE template_id=?", (template_id,)))
                for question, answer, source in examples:
                    self._upsert_exact_rule(str(question), str(answer), str(source or "seed"))
                    moved += 1
                self.connection.execute("DELETE FROM examples WHERE template_id=?", (template_id,))
                self.connection.execute("DELETE FROM templates WHERE id=? AND is_locked=0", (template_id,))
            self._set_metadata("template_cleanup_v26", "1")
            self._set_metadata("template_cleanup_v26_moved", str(moved))
            self.connection.commit()

    def _cleanup_auto_generated_oneoffs_once(self) -> None:
        """Demote old learned one-off templates without triggers to exact rules.

        This removes the 2.6 behaviour where nearly every manual reply could create a reusable template.
        User-created templates have triggers, and seed templates are left untouched.
        """
        if self._metadata("template_cleanup_v27_oneoffs") == "1":
            return
        moved = 0
        with self.lock:
            rows = list(
                self.connection.execute(
                    """
                    SELECT t.id, t.source, t.triggers, COUNT(e.id) AS cnt
                    FROM templates t LEFT JOIN examples e ON e.template_id=t.id
                    WHERE t.is_locked=0
                    GROUP BY t.id
                    """
                )
            )
            for template_id, source, triggers_value, count in rows:
                if str(source or "") == "seed" or int(count or 0) != 1 or split_phrases(str(triggers_value or "")):
                    continue
                examples = list(self.connection.execute("SELECT question, answer, source FROM examples WHERE template_id=?", (template_id,)))
                if not examples:
                    continue
                for question, answer, example_source in examples:
                    self._upsert_exact_rule(str(question), str(answer), str(example_source or "manual"))
                self.connection.execute("DELETE FROM examples WHERE template_id=?", (template_id,))
                self.connection.execute("DELETE FROM templates WHERE id=? AND is_locked=0", (template_id,))
                moved += 1
            self._set_metadata("template_cleanup_v27_oneoffs", "1")
            self._set_metadata("template_cleanup_v27_oneoffs_moved", str(moved))
            self.connection.commit()

    def _cleanup_old_observed(self) -> None:
        with self.lock:
            self.connection.execute("DELETE FROM observed WHERE updated_at < datetime('now', '-90 days')")
            self.connection.commit()

    def reload(self) -> None:
        with self.lock:
            template_rows = list(
                self.connection.execute(
                    """
                    SELECT id, name, answer_template, enabled, triggers, negative_triggers, close_triggers, match_mode, priority
                    FROM templates
                    ORDER BY id
                    """
                )
            )
            example_rows = list(
                self.connection.execute(
                    """
                    SELECT id, question, answer, source, template_id
                    FROM examples
                    WHERE template_id IS NOT NULL
                    ORDER BY id
                    """
                )
            )
            exact_rows = list(
                self.connection.execute(
                    "SELECT normalized_question, question, answer, source FROM exact_rules"
                )
            )

        documents = [tokens(str(row[1])) for row in example_rows]
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(set(document))
        total = max(1, len(documents))
        self.idf = {
            word: math.log((total + 1) / (frequency + 1)) + 1.0
            for word, frequency in document_frequency.items()
        }

        templates: dict[int, dict[str, Any]] = {
            int(row[0]): {
                "id": int(row[0]),
                "name": str(row[1]),
                "answer_template": str(row[2]),
                "enabled": bool(row[3]),
                "triggers": split_phrases(str(row[4] or "")),
                "negative_triggers": split_phrases(str(row[5] or "")),
                "close_triggers": split_phrases(str(row[6] or "")),
                "match_mode": str(row[7] or "smart"),
                "priority": max(0, min(100, int(row[8] or 0))),
                "examples": [],
                "intents": set(),
                "concept_profile": {},
                "concept_counts": Counter(),
            }
            for row in template_rows
        }

        examples: list[dict[str, Any]] = []
        for row, document in zip(example_rows, documents):
            template_id = int(row[4])
            if template_id not in templates:
                continue
            counts = Counter(document)
            word_vector = {
                word: count * self.idf.get(word, 1.0)
                for word, count in counts.items()
            }
            example = {
                "id": int(row[0]),
                "question": str(row[1]),
                "answer": str(row[2]),
                "source": str(row[3] or "seed"),
                "template_id": template_id,
                "word_vector": word_vector,
                "char_vector": char_ngrams(str(row[1])),
                "token_set": set(document),
                "intents": detect_intents(str(row[1])),
                "concepts": context_concepts(str(row[1])),
                "bigrams": token_bigrams(str(row[1])),
            }
            examples.append(example)
            templates[template_id]["examples"].append(example)
            templates[template_id]["intents"].update(example["intents"])
            templates[template_id]["concept_counts"].update(example["concepts"].keys())

        for template in templates.values():
            template["intents"].update(detect_intents(" ".join(template["triggers"])))
            trigger_concepts = context_concepts(" ".join(template["triggers"]))
            # Name and answer describe what the template *means*. This matters when an old
            # database has few examples or the examples use very different wording.
            semantic_text = f"{template['name']} {answer_body(template['answer_template'])}"
            semantic_concepts = context_concepts(semantic_text)
            template["intents"].update(detect_intents(semantic_text))
            total_examples = max(1, len(template["examples"]))
            profile = {
                name: min(1.0, float(count) / total_examples)
                for name, count in template.pop("concept_counts", Counter()).items()
            }
            # Explicit multi-word triggers are authored by the user and therefore deserve
            # to influence the profile, but they still do not become an answer by themselves.
            for name in trigger_concepts:
                profile[name] = max(profile.get(name, 0.0), 0.55)
            for name, strength in semantic_concepts.items():
                if strength >= 1.8:
                    profile[name] = max(profile.get(name, 0.0), min(0.92, 0.55 + 0.055 * strength))
            template["concept_profile"] = profile

        self.examples = examples
        self.templates = templates
        self.exact_rules = {
            str(row[0]): {
                "question": str(row[1]),
                "answer": str(row[2]),
                "source": str(row[3] or "manual"),
            }
            for row in exact_rows
        }

    def _word_vector(self, text: str) -> dict[str, float]:
        counts = Counter(tokens(text))
        return {word: count * self.idf.get(word, 1.0) for word, count in counts.items()}

    def _example_score(
        self,
        question_word_vector: dict[str, float],
        question_char_vector: Counter[str],
        question_tokens: set[str],
        question_intents: set[str],
        question_bigrams: set[tuple[str, str]],
        example: dict[str, Any],
    ) -> float:
        word_score = cosine_dict(question_word_vector, example["word_vector"])
        char_score = cosine_counter(question_char_vector, example["char_vector"])
        example_tokens: set[str] = example["token_set"]
        union = question_tokens | example_tokens
        jaccard = len(question_tokens & example_tokens) / len(union) if union else 0.0
        example_bigrams: set[tuple[str, str]] = example.get("bigrams", set())
        bigram_union = question_bigrams | example_bigrams
        bigram_score = len(question_bigrams & example_bigrams) / len(bigram_union) if bigram_union else 0.0
        score = 0.46 * word_score + 0.28 * char_score + 0.12 * jaccard + 0.14 * bigram_score

        example_intents: set[str] = example["intents"]
        if question_intents and example_intents:
            if question_intents.isdisjoint(example_intents):
                score -= 0.34
            else:
                score += 0.06
        if example["source"] == "manual":
            score += 0.04
        return max(0.0, min(1.0, score))

    @staticmethod
    def _threshold_for_template(count: int, manual_count: int, strong_trigger_hits: int) -> float:
        # Context similarity, not a keyword, is the main signal.
        if count == 0:
            return 0.82 if strong_trigger_hits >= 2 else 0.99
        if manual_count >= 3:
            base = 0.56
        elif manual_count >= 1:
            base = 0.62 if count > 1 else 0.70
        elif count >= 8:
            base = 0.56
        elif count >= 4:
            base = 0.60
        elif count >= 2:
            base = 0.61
        else:
            base = 0.78
        if strong_trigger_hits >= 2:
            base -= min(0.08, 0.04 * strong_trigger_hits)
        return max(0.48, base)

    def observe_ticket(self, ticket_id: str, question: str, predicted_template_id: Optional[int] = None) -> None:
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO observed(ticket_id, question, predicted_template_id, updated_at)
                VALUES(?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    question=excluded.question,
                    predicted_template_id=excluded.predicted_template_id,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (ticket_id, question, predicted_template_id),
            )
            self.connection.commit()

    def classify(
        self,
        ticket_id: str,
        question: str,
        draft_threshold: int = 80,
        auto_threshold: int = 96,
        add_unknown_to_pending: bool = True,
        global_close_phrases: Iterable[str] = (),
        respect_processed: bool = True,
    ) -> Decision:
        if respect_processed and self.is_processed(ticket_id):
            return Decision(ticket_id, "IGNORE", 100, reason="Тикет уже был обработан")
        normalized = normalize(question)
        if not normalized or is_noise_question(question):
            return Decision(ticket_id, "IGNORE", 100, reason="Служебное сообщение без вопроса игрока")

        # Global close filter has priority over answers. It is intentionally exact-by-phrase
        # and user-authored: no fuzzy matching is used for destructive actions.
        matched_global_close = [
            str(phrase) for phrase in global_close_phrases
            if normalize(str(phrase)) and contains_phrase(question, str(phrase))
        ]
        if matched_global_close:
            matched_global_close.sort(key=lambda phrase: (len(tokens(phrase)), len(normalize(phrase))), reverse=True)
            phrase = matched_global_close[0]
            self.observe_ticket(ticket_id, question)
            return Decision(
                ticket_id,
                "CLOSE",
                100,
                reason=f"Глобальный фильтр автозакрытия: {phrase}",
                matched_question=question,
                template_name="Глобальное автозакрытие",
                auto_allowed=True,
                close_requested=True,
            )

        binding_action = account_binding_action(question)
        if binding_action == "primary":
            # The project has a canned answer only for changing/removing an existing binding.
            # A first-time binding question must never accidentally receive that answer, even
            # if old examples or trigger text happen to look similar.
            self.observe_ticket(ticket_id, question)
            return Decision(
                ticket_id,
                "IGNORE",
                100,
                reason="Первичная привязка аккаунта: шаблон перепривязки к этой ситуации не применяется",
                matched_question=question,
                template_name="Первичная привязка — игнор",
                auto_allowed=False,
            )

        exact_rule = self.exact_rules.get(normalized)
        if exact_rule:
            self.observe_ticket(ticket_id, question)
            return Decision(
                ticket_id,
                "DRAFT",
                100,
                draft=render_answer(str(exact_rule["answer"]), ticket_id),
                reason="Точная ситуация уже встречалась и была решена тобой",
                matched_question=str(exact_rule["question"]),
                template_count=1,
                template_name="Точное правило",
                auto_allowed=True,
            )

        # Explicit auto-close phrases are deterministic and separate from answer triggers.
        close_matches: list[tuple[int, int, dict[str, Any], str]] = []
        for template in self.templates.values():
            if not template["enabled"]:
                continue
            for phrase in template.get("close_triggers", []):
                if contains_phrase(question, phrase):
                    close_matches.append((len(normalize(phrase)), len(tokens(phrase)), template, phrase))
        if close_matches:
            close_matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
            template = close_matches[0][2]
            phrase = close_matches[0][3]
            self.observe_ticket(ticket_id, question, int(template["id"]))
            return Decision(
                ticket_id,
                "CLOSE",
                100,
                reason=f"Сработало правило автозакрытия: {phrase}",
                matched_question=question,
                template_id=int(template["id"]),
                template_name=str(template["name"]),
                auto_allowed=True,
                close_requested=True,
            )

        question_word_vector = self._word_vector(question)
        question_char_vector = char_ngrams(question)
        question_tokens = set(tokens(question))
        question_content_tokens = set(meaningful_tokens(question))
        question_bigrams = token_bigrams(question)
        question_intents = detect_intents(question)
        question_concepts = context_concepts(question)
        dominant_name, dominant_strength = dominant_concept(question_concepts)

        candidates: list[dict[str, Any]] = []
        for template_id, template in self.templates.items():
            if not template["enabled"]:
                continue
            if any(contains_phrase(question, phrase) for phrase in template["negative_triggers"]):
                continue

            hit_phrases = [phrase for phrase in template["triggers"] if contains_phrase(question, phrase)]
            strong_hits = [phrase for phrase in hit_phrases if phrase_specificity(phrase) >= 0.60]
            weak_hits = [phrase for phrase in hit_phrases if phrase not in strong_hits]
            exact_example = any(normalize(example["question"]) == normalized for example in template["examples"])
            exact_trigger = template.get("match_mode") == "exact" and any(normalized == normalize(phrase) for phrase in template["triggers"])
            exact = exact_example or exact_trigger
            if template.get("match_mode") == "exact" and not exact:
                continue

            template_intents: set[str] = set(template.get("intents", set()))
            concept_profile = dict(template.get("concept_profile", {}))
            context_match = concept_alignment(question_concepts, concept_profile)
            dominant_profile = float(concept_profile.get(dominant_name, 0.0)) if dominant_name else 0.0
            if question_intents and template_intents and question_intents.isdisjoint(template_intents) and not exact:
                # A strong neighboring-word concept may rescue paraphrases that the older
                # phrase list does not recognize; otherwise incompatible intents are rejected.
                if not (dominant_strength >= 2.5 and dominant_profile >= 0.45):
                    continue

            scored = [
                (
                    self._example_score(
                        question_word_vector,
                        question_char_vector,
                        question_tokens,
                        question_intents,
                        question_bigrams,
                        example,
                    ),
                    example,
                )
                for example in template["examples"]
            ]
            scored.sort(key=lambda item: item[0], reverse=True)

            if not scored:
                # Exact templates are allowed to work from one complete exact trigger.
                # Smart templates still need two meaningful hints until they have examples.
                if exact:
                    aggregate = 1.0
                    best_context = 1.0
                else:
                    if len(strong_hits) < 2:
                        continue
                    aggregate = min(0.92, 0.76 + 0.07 * len(strong_hits))
                    best_context = 0.0
            else:
                best_context = float(scored[0][0])
                if (
                    best_context < 0.30
                    and len(strong_hits) < 2
                    and not exact
                    and not (dominant_strength >= 2.5 and dominant_profile >= 0.45)
                ):
                    continue
                top = [item[0] for item in scored[:3]]
                aggregate = top[0]
                if len(top) > 1:
                    aggregate += 0.07 * top[1]
                if len(top) > 2:
                    aggregate += 0.025 * top[2]
                # Triggers are only a small boost. They cannot overpower unrelated context.
                aggregate += min(0.16, 0.055 * len(strong_hits) + 0.015 * len(weak_hits))
                # Matching semantic intent is a contextual signal, unlike a bare keyword.
                # Disjoint intents were rejected above, so this safely helps paraphrases
                # such as "взломали аккаунт / сменили пароль / вернуть доступ".
                if question_intents and template_intents and (question_intents & template_intents):
                    aggregate += 0.10

                best_example_tokens = set(meaningful_tokens(str(scored[0][1]["question"])))
                if question_content_tokens and best_example_tokens:
                    coverage = len(question_content_tokens & best_example_tokens) / max(1, min(len(question_content_tokens), len(best_example_tokens)))
                    if coverage < 0.16 and len(strong_hits) < 2 and not exact:
                        aggregate -= 0.12

            # Deterministic context layer: compare the *action around generic anchors*
            # against how often that concept occurs in this template's confirmed examples.
            # A bare "аккаунт" has no concept strength and therefore gives no boost.
            if dominant_strength >= 2.5:
                if dominant_profile >= 0.60:
                    aggregate += 0.40 + min(0.08, 0.06 * dominant_profile)
                elif dominant_profile >= 0.40:
                    aggregate += 0.28
                elif dominant_profile >= 0.20:
                    aggregate += 0.12
                elif concept_profile and max(concept_profile.values(), default=0.0) >= 0.35:
                    aggregate -= 0.24
                # When the sentence expresses one strong semantic action, a template from
                # another well-defined topic must not win merely because it shares "аккаунт".
                if dominant_strength >= 4.0 and dominant_profile < 0.12 and best_context < 0.62 and not exact:
                    continue
            elif context_match >= 0.45:
                aggregate += 0.10

            if exact:
                aggregate = 1.0
            manual_count = sum(1 for example in template["examples"] if example["source"] == "manual")
            if manual_count:
                aggregate += min(0.08, 0.025 * min(manual_count, 3))
            aggregate = max(0.0, min(1.0, aggregate))
            # Priority is a tie-break/routing preference, not extra confidence.
            # This lets (for example) a password/recovery template beat a ban
            # template when both are present in one sentence, without turning a
            # weak match into an automatic answer.
            priority = max(0, min(100, int(template.get("priority", 0) or 0)))
            rank_score = aggregate + (0.0025 * priority)
            candidates.append(
                {
                    "score": aggregate,
                    "rank_score": rank_score,
                    "priority": priority,
                    "template": template,
                    "scored": scored,
                    "manual_count": manual_count,
                    "strong_hits": len(strong_hits),
                    "weak_hits": len(weak_hits),
                    "exact": exact,
                    "best_context": best_context,
                    "context_match": context_match,
                    "dominant_profile": dominant_profile,
                }
            )

        if not candidates:
            self.observe_ticket(ticket_id, question)
            if add_unknown_to_pending:
                self.add_pending(ticket_id, question)
            return Decision(ticket_id, "UNKNOWN", 0, reason="Нет подходящего подтверждённого решения")

        candidates.sort(key=lambda item: (item["rank_score"], item["score"]), reverse=True)
        best = candidates[0]
        second_rank = float(candidates[1]["rank_score"]) if len(candidates) > 1 else 0.0
        template = best["template"]
        count = len(template["examples"])
        threshold = self._threshold_for_template(count, int(best["manual_count"]), int(best["strong_hits"]))
        score = float(best["score"])
        margin = float(best["rank_score"]) - second_rank
        best_context_match = float(best.get("context_match", 0.0))
        best_dominant_profile = float(best.get("dominant_profile", 0.0))
        if dominant_strength >= 2.5 and best_dominant_profile >= 0.60:
            threshold = max(0.40, threshold - 0.16)
        elif dominant_strength >= 2.5 and best_dominant_profile >= 0.45:
            threshold = max(0.44, threshold - 0.12)
        elif best_context_match >= 0.50:
            threshold = max(0.48, threshold - 0.05)

        if best["exact"]:
            confidence = 100
        else:
            score_component = max(0.0, min(1.0, (score - threshold) / max(0.01, 1.0 - threshold)))
            margin_component = max(0.0, min(1.0, margin / 0.22))
            trigger_component = min(1.0, int(best["strong_hits"]) / 2)
            context_component = min(1.0, max(0.0, float(best["best_context"]) / 0.72))
            semantic_component = min(1.0, max(best_context_match, best_dominant_profile))
            confidence = int(round(70 + 12 * score_component + 7 * margin_component + 3 * trigger_component + 5 * context_component + 8 * semantic_component))
            confidence = max(0, min(99, confidence))

        ambiguous = not best["exact"] and margin < 0.075
        if ambiguous and dominant_strength >= 2.5 and best_dominant_profile >= 0.55:
            second_profile = 0.0
            if len(candidates) > 1:
                second_profile = float(candidates[1].get("dominant_profile", 0.0))
            if best_dominant_profile - second_profile >= 0.20:
                ambiguous = False
        matched_question = str(best["scored"][0][1]["question"]) if best["scored"] else "Совпали несколько заданных триггеров"
        if score < threshold or confidence < draft_threshold or ambiguous:
            self.observe_ticket(ticket_id, question, int(template["id"]))
            if add_unknown_to_pending:
                self.add_pending(ticket_id, question)
            reason = "Контекст недостаточно похож на подтверждённые ситуации"
            if ambiguous:
                reason = "Есть несколько почти равных вариантов ответа — нужен твой выбор"
            return Decision(
                ticket_id,
                "UNKNOWN",
                confidence,
                reason=reason,
                matched_question=matched_question,
                template_count=count,
                template_id=int(template["id"]),
                template_name=str(template["name"]),
            )

        template_id = int(template["id"])
        self.observe_ticket(ticket_id, question, template_id)
        draft = render_answer(str(template["answer_template"]), ticket_id)
        has_manual = int(best["manual_count"]) > 0
        auto_allowed = confidence >= auto_threshold and not ambiguous and (count >= 2 or has_manual or int(best["strong_hits"]) >= 2 or bool(best["exact"]))
        return Decision(
            ticket_id,
            "DRAFT",
            confidence,
            draft=draft,
            reason=(
                f"Контекст совпал с базой; якорные слова проверены по соседним словам"
                + (f" · смысл: {dominant_name}" if dominant_name else "")
            ),
            matched_question=matched_question,
            template_count=count,
            template_id=template_id,
            template_name=str(template["name"]),
            auto_allowed=auto_allowed,
        )

    def ai_candidates(self, question: str, limit: int = 48) -> list[dict[str, Any]]:
        """Build a broad, compact catalog for the local semantic model.

        This stage is intentionally permissive: it ranks templates, but does not make the
        final decision. The local LLM receives enough alternatives to reject misleading
        one-word matches and compare the complete intent of the ticket.
        """
        normalized = normalize(question)
        if not normalized:
            return []
        binding_action = account_binding_action(question)
        limit = max(4, min(80, int(limit or 48)))
        question_word_vector = self._word_vector(question)
        question_char_vector = char_ngrams(question)
        question_tokens = set(tokens(question))
        question_bigrams = token_bigrams(question)
        question_intents = detect_intents(question)
        question_concepts = context_concepts(question)
        dominant_name, dominant_strength = dominant_concept(question_concepts)

        rows: list[tuple[float, dict[str, Any]]] = []
        for template in self.templates.values():
            is_enabled = bool(template.get("enabled"))
            concept_profile = dict(template.get("concept_profile", {}))
            context_match = concept_alignment(question_concepts, concept_profile)
            dominant_profile = float(concept_profile.get(dominant_name, 0.0)) if dominant_name else 0.0
            if binding_action == "primary" and float(concept_profile.get("account_link", 0.0)) >= 0.35:
                continue
            # Disabled templates never participate in real decisions, but a strongly matching
            # one is still useful in the sandbox so the operator can notice and re-enable it.
            if not is_enabled and not (dominant_strength >= 2.5 and dominant_profile >= 0.45):
                continue
            if any(contains_phrase(question, phrase) for phrase in template.get("negative_triggers", [])):
                continue
            exact = False
            if template.get("match_mode") == "exact":
                exact = any(normalize(example.get("question", "")) == normalized for example in template.get("examples", []))
                exact = exact or any(normalized == normalize(phrase) for phrase in template.get("triggers", []))
                if not exact:
                    continue

            scored_examples: list[tuple[float, dict[str, Any]]] = []
            for example in template.get("examples", []):
                score = self._example_score(
                    question_word_vector,
                    question_char_vector,
                    question_tokens,
                    question_intents,
                    question_bigrams,
                    example,
                )
                scored_examples.append((score, example))
            scored_examples.sort(key=lambda item: item[0], reverse=True)

            best_context = scored_examples[0][0] if scored_examples else 0.0
            trigger_hits = [phrase for phrase in template.get("triggers", []) if contains_phrase(question, phrase)]
            strong_hits = [phrase for phrase in trigger_hits if phrase_specificity(phrase) >= 0.60]
            template_intents = set(template.get("intents", set()))
            intent_overlap = bool(question_intents and template_intents and question_intents.intersection(template_intents))

            # Ranking only; no hard semantic decision here. A generic single trigger gives
            # almost no advantage, while full-context similarity and intent overlap do.
            rank_score = float(best_context)
            rank_score += min(0.08, 0.025 * len(strong_hits))
            if intent_overlap:
                rank_score += 0.08
            if question_intents and template_intents and question_intents.isdisjoint(template_intents):
                rank_score -= 0.05
            if dominant_strength >= 2.5:
                rank_score += 0.48 * dominant_profile
                if dominant_profile < 0.10 and concept_profile and max(concept_profile.values(), default=0.0) >= 0.35:
                    rank_score -= 0.18
            else:
                rank_score += 0.10 * context_match

            if exact:
                rank_score = max(rank_score, 1.0)
            if not is_enabled:
                rank_score -= 0.04
            priority = max(0, min(100, int(template.get("priority", 0) or 0)))
            rank_score += 0.0025 * priority
            rank_score = max(0.0, min(1.25, rank_score))
            examples = [str(example[1].get("question", "")) for example in scored_examples[:4]]
            if not examples:
                examples = [str(item.get("question", "")) for item in template.get("examples", [])[:4]]
            rows.append(
                (
                    rank_score,
                    {
                        "id": int(template["id"]),
                        "name": str(template.get("name", "")) + (" · выключен" if not is_enabled else ""),
                        "enabled": is_enabled,
                        "answer": answer_body(str(template.get("answer_template", ""))),
                        "triggers": list(template.get("triggers", [])),
                        "negative_triggers": list(template.get("negative_triggers", [])),
                        "examples": examples,
                        "example_count": len(template.get("examples", [])),
                        "priority": priority,
                        "rank_score": round(rank_score, 4),
                    },
                )
            )

        rows.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in rows[:limit]]

    def decision_from_template(
        self,
        ticket_id: str,
        question: str,
        template_id: int,
        confidence: int,
        reason: str,
        auto_threshold: int = 94,
    ) -> Decision:
        template = self.templates.get(int(template_id))
        if not template or not template.get("enabled"):
            return Decision(ticket_id, "UNKNOWN", 0, reason="Выбранный ИИ шаблон отключён или удалён")
        examples = list(template.get("examples", []))
        matched_question = str(examples[0].get("question", "")) if examples else ""
        self.observe_ticket(ticket_id, question, int(template_id))
        return Decision(
            ticket_id=ticket_id,
            decision="DRAFT",
            confidence=max(0, min(100, int(confidence))),
            draft=render_answer(str(template.get("answer_template", "")), ticket_id),
            reason=reason,
            matched_question=matched_question,
            template_count=len(examples),
            template_id=int(template_id),
            template_name=str(template.get("name", "")),
            auto_allowed=int(confidence) >= int(auto_threshold),
        )

    def _add_example_to_existing_template(
        self,
        question: str,
        answer: str,
        template_id: int,
        replace_exact_manual: bool = True,
        prefer_manual_text: bool = False,
    ) -> tuple[bool, int]:
        with self.lock:
            template_row = self.connection.execute(
                "SELECT answer_template, answer_key FROM templates WHERE id=?",
                (template_id,),
            ).fetchone()
            if not template_row:
                return self.add_example(question, answer, source="manual", replace_exact_manual=replace_exact_manual)

            normalized_question = normalize(question)
            if replace_exact_manual:
                rows = list(self.connection.execute("SELECT id, question FROM examples WHERE source='manual'"))
                for row in rows:
                    if normalize(str(row[1])) == normalized_question:
                        self.connection.execute("DELETE FROM examples WHERE id=?", (row[0],))
                self.connection.execute("DELETE FROM exact_rules WHERE normalized_question=?", (normalized_question,))

            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO examples(question, answer, source, template_id) VALUES(?,?, 'manual', ?)",
                (question.strip(), answer.strip(), template_id),
            )
            inserted = cursor.rowcount > 0

            if prefer_manual_text:
                canonical = canonical_answer(answer)
                key = template_key(canonical)
                duplicate = self.connection.execute(
                    "SELECT id FROM templates WHERE answer_key=? AND id<>?",
                    (key, template_id),
                ).fetchone()
                if not duplicate:
                    self.connection.execute(
                        """
                        UPDATE templates
                        SET answer_key=?, answer_template=?, source='manual', updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (key, canonical, template_id),
                    )
                else:
                    self.connection.execute(
                        "UPDATE templates SET source='manual', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (template_id,),
                    )
            else:
                self.connection.execute(
                    "UPDATE templates SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (template_id,),
                )
            self.connection.commit()
        self.reload()
        return inserted, template_id

    def _template_for_answer(self, answer: str, minimum_similarity: float = 0.90) -> tuple[Optional[int], float]:
        canonical = canonical_answer(answer)
        key = template_key(canonical)
        with self.lock:
            exact = self.connection.execute("SELECT id FROM templates WHERE answer_key=?", (key,)).fetchone()
            if exact:
                return int(exact[0]), 1.0
            rows = list(self.connection.execute("SELECT id, answer_template FROM templates WHERE enabled=1"))
        best_id: Optional[int] = None
        best_score = 0.0
        for template_id, answer_template in rows:
            score = _answer_similarity(canonical, str(answer_template))
            if score > best_score:
                best_id, best_score = int(template_id), score
        return (best_id, best_score) if best_id is not None and best_score >= minimum_similarity else (None, best_score)

    def _save_manual_conservatively(self, question: str, answer: str) -> tuple[int, str]:
        """One manual answer is an exact memory; repeated same answers become a reusable template."""
        existing_id, similarity = self._template_for_answer(answer, 0.92)
        if existing_id:
            self._add_example_to_existing_template(question, answer, existing_id, replace_exact_manual=True, prefer_manual_text=similarity >= 0.97)
            return existing_id, "smart_existing"

        canonical = canonical_answer(answer)
        with self.lock:
            siblings = list(self.connection.execute("SELECT question, answer, source FROM exact_rules"))
        same_answer = [row for row in siblings if _answer_similarity(canonical, str(row[1])) >= 0.97]
        if same_answer and is_generalizable_answer(answer):
            with self.lock:
                template_id = self._ensure_template(answer, "learned", "smart")
                for old_question, old_answer, old_source in same_answer:
                    self.connection.execute(
                        "INSERT OR IGNORE INTO examples(question, answer, source, template_id) VALUES(?,?,?,?)",
                        (str(old_question), str(old_answer), str(old_source or "manual"), template_id),
                    )
                    self.connection.execute("DELETE FROM exact_rules WHERE normalized_question=?", (normalize(str(old_question)),))
                self.connection.execute(
                    "INSERT OR IGNORE INTO examples(question, answer, source, template_id) VALUES(?,?, 'manual', ?)",
                    (question.strip(), answer.strip(), template_id),
                )
                self.connection.commit()
            self.reload()
            return template_id, "promoted"

        with self.lock:
            self._upsert_exact_rule(question, answer, "manual")
            self.connection.commit()
        self.reload()
        return 0, "exact_first"

    def add_example(self, question: str, answer: str, source: str = "manual", replace_exact_manual: bool = False) -> tuple[bool, int]:
        question = question.strip()
        answer = answer.strip()
        if not question or not answer:
            return False, 0
        if not is_generalizable_answer(answer):
            with self.lock:
                self._upsert_exact_rule(question, answer, source)
                self.connection.commit()
            self.reload()
            return True, 0
        with self.lock:
            template_id = self._ensure_template(answer, source, "smart")
            if replace_exact_manual:
                normalized_question = normalize(question)
                rows = list(self.connection.execute("SELECT id, question FROM examples WHERE source='manual'"))
                for row in rows:
                    if normalize(str(row[1])) == normalized_question:
                        self.connection.execute("DELETE FROM examples WHERE id=?", (row[0],))
                self.connection.execute("DELETE FROM exact_rules WHERE normalized_question=?", (normalized_question,))
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO examples(question, answer, source, template_id) VALUES(?,?,?,?)",
                (question, answer, source, template_id),
            )
            inserted = cursor.rowcount > 0
            self.connection.execute("UPDATE templates SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (template_id,))
            self.connection.commit()
        self.reload()
        return inserted, template_id

    def teach_question_to_template(self, question: str, template_id: int) -> tuple[bool, str]:
        """Attach a sandbox question to an existing template as a confirmed manual example."""
        value = question.strip()
        if not value:
            return False, "Пустой вопрос"
        if is_noise_question(value):
            return False, "Служебный/мусорный текст нельзя добавлять в обучение"

        template = self.templates.get(int(template_id))
        if not template:
            return False, "Шаблон удалён или не найден"
        if not template.get("enabled"):
            return False, "Шаблон выключен — сначала включи его в разделе «Шаблоны»"

        answer = str(template.get("answer_template", "")).strip()
        if not answer:
            return False, "У выбранного шаблона нет текста ответа"

        inserted, _ = self._add_example_to_existing_template(
            value,
            answer,
            int(template_id),
            replace_exact_manual=True,
            prefer_manual_text=False,
        )
        name = str(self.templates.get(int(template_id), {}).get("name", template.get("name", "")))
        if inserted:
            return True, f"Пример добавлен в шаблон «{name}»"
        return True, f"Эта ситуация уже была привязана к шаблону «{name}»"

    def create_template_from_manual_answer(
        self,
        question: str,
        answer: str,
        name: str = "",
    ) -> tuple[bool, int, str]:
        """Create/reuse a template directly from the sandbox and teach this question.

        The operator may paste either a full ``t a ID ... @@@`` command or just the body.
        A one-off short answer is kept as an exact template; a reusable answer becomes a
        smart template with the current question as its first confirmed example.
        """
        question = (question or "").strip()
        raw_answer = (answer or "").strip()
        if not question:
            return False, 0, "Пустой вопрос"
        if is_noise_question(question):
            return False, 0, "Служебный/мусорный текст нельзя добавлять в обучение"
        if not raw_answer:
            return False, 0, "Напиши правильный ответ"

        if re.match(r"^\s*t\s+a\s+(?:\{ticket_id\}|\d{3,12})\s+", raw_answer, re.IGNORECASE):
            canonical = canonical_answer(raw_answer)
            if not canonical.rstrip().endswith("@@@"):
                canonical = canonical.rstrip() + " @@@"
        else:
            body = raw_answer
            if body.rstrip().endswith("@@@"):
                body = re.sub(r"\s*@@@\s*$", "", body, flags=re.DOTALL).strip()
            canonical = f"t a {{ticket_id}} {body} @@@"

        if not has_meaningful_manual_body(canonical):
            return False, 0, "Ответ пустой или состоит только из приветствия"

        with self.lock:
            key = template_key(canonical)
            row = self.connection.execute("SELECT id FROM templates WHERE answer_key=?", (key,)).fetchone()
            if row:
                template_id = int(row[0])
                self.connection.execute(
                    "UPDATE templates SET enabled=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (template_id,),
                )
            else:
                mode = "smart" if is_generalizable_answer(canonical) else "exact"
                cursor = self.connection.execute(
                    """
                    INSERT INTO templates(answer_key, name, answer_template, enabled, match_mode, triggers, source)
                    VALUES(?,?,?,?,?,?, 'manual_sandbox')
                    """,
                    (
                        key,
                        (name or "").strip() or _suggest_template_name(canonical),
                        canonical,
                        1,
                        mode,
                        normalize(question) if mode == "exact" else "",
                    ),
                )
                template_id = int(cursor.lastrowid)

            # A sandbox correction is explicit human supervision: attach it immediately.
            self.connection.execute("DELETE FROM exact_rules WHERE normalized_question=?", (normalize(question),))
            self.connection.execute(
                "INSERT OR IGNORE INTO examples(question, answer, source, template_id) VALUES(?,?, 'manual_sandbox', ?)",
                (question, canonical, template_id),
            )
            self.connection.execute(
                "UPDATE templates SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (template_id,),
            )
            self.connection.commit()

        self.reload()
        template = self.templates.get(template_id, {})
        return True, template_id, f"Создан/обновлён шаблон «{template.get('name', name or template_id)}» и добавлен пример"

    def add_pending(self, ticket_id: str, question: str) -> None:
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO pending(ticket_id, question, updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(ticket_id) DO UPDATE SET question=excluded.question, updated_at=CURRENT_TIMESTAMP
                """,
                (ticket_id, question),
            )
            self.connection.commit()

    def learn_from_manual(
        self,
        message: str,
        learning_exclusions: Iterable[str] = (),
        only_corrections: bool = True,
        filter_noise: bool = True,
        learning_policy: str = "unknown_or_corrections",
    ) -> Optional[dict[str, Any]]:
        value = message.strip()
        match = COMMAND_RE.match(value)
        if not match or not value.endswith("@@@") or not has_meaningful_manual_body(value):
            return None
        ticket_id = match.group(1)
        predicted_template_id: Optional[int] = None
        was_pending = False
        with self.lock:
            row = self.connection.execute(
                "SELECT question, predicted_template_id FROM observed WHERE ticket_id=?",
                (ticket_id,),
            ).fetchone()
            pending_row = self.connection.execute("SELECT question FROM pending WHERE ticket_id=?", (ticket_id,)).fetchone()
            was_pending = bool(pending_row)
            if not row and pending_row:
                row = (pending_row[0], None)
            if not row:
                return None
            question = str(row[0])
            predicted_template_id = int(row[1]) if row[1] else None

        if (filter_noise and is_noise_question(question)) or matches_learning_exclusion(question, learning_exclusions):
            with self.lock:
                self.connection.execute("DELETE FROM pending WHERE ticket_id=?", (ticket_id,))
                self.connection.execute(
                    "INSERT OR REPLACE INTO processed(ticket_id, action, created_at) VALUES(?, 'manual_filtered', CURRENT_TIMESTAMP)",
                    (ticket_id,),
                )
                self.connection.execute(
                    "INSERT INTO history(ticket_id, question, action, confidence, answer, details) VALUES(?,?,?,?,?,?)",
                    (ticket_id, question, "learning_skipped", 100, value, "Исключено фильтром обучения"),
                )
                self.connection.commit()
            return {
                "ticket_id": ticket_id,
                "question": question,
                "answer": value,
                "template_id": None,
                "template_name": "Не добавлено",
                "generalizable": False,
                "skipped": True,
                "reason": "Фильтр обучения",
            }

        policy = str(learning_policy or "").strip().lower()
        if policy not in {"unknown_or_corrections", "unknown_only", "all"}:
            policy = "unknown_or_corrections" if only_corrections else "all"

        # Optional strict mode: only unknown/pending situations are allowed to train.
        if policy == "unknown_only" and predicted_template_id and not was_pending:
            with self.lock:
                predicted = self.connection.execute("SELECT name FROM templates WHERE id=?", (predicted_template_id,)).fetchone()
                self.connection.execute("DELETE FROM pending WHERE ticket_id=?", (ticket_id,))
                self.connection.execute(
                    "INSERT OR REPLACE INTO processed(ticket_id, action, created_at) VALUES(?, 'manual_known_skipped', CURRENT_TIMESTAMP)",
                    (ticket_id,),
                )
                self.connection.execute(
                    "INSERT INTO history(ticket_id, question, action, confidence, answer, details) VALUES(?,?,?,?,?,?)",
                    (ticket_id, question, "learning_not_needed", 100, value, "Политика обучения: сохранять только неизвестные ситуации"),
                )
                self.connection.commit()
            return {
                "ticket_id": ticket_id,
                "question": question,
                "answer": value,
                "template_id": predicted_template_id,
                "template_name": str(predicted[0]) if predicted else "Известный шаблон",
                "generalizable": True,
                "skipped": True,
                "reason": "Политика: только неизвестные",
            }

        # If the user merely sent the same answer the classifier had already chosen, don't create another example.
        if policy == "unknown_or_corrections" and predicted_template_id and not was_pending:
            with self.lock:
                predicted = self.connection.execute("SELECT answer_template, name FROM templates WHERE id=?", (predicted_template_id,)).fetchone()
            if predicted:
                similarity = _answer_similarity(value, str(predicted[0]))
                if similarity >= 0.94:
                    with self.lock:
                        self.connection.execute("DELETE FROM pending WHERE ticket_id=?", (ticket_id,))
                        self.connection.execute(
                            "INSERT OR REPLACE INTO processed(ticket_id, action, created_at) VALUES(?, 'manual_confirmed', CURRENT_TIMESTAMP)",
                            (ticket_id,),
                        )
                        self.connection.execute(
                            "INSERT INTO history(ticket_id, question, action, confidence, answer, details) VALUES(?,?,?,?,?,?)",
                            (ticket_id, question, "learning_not_needed", 100, value, "Ответ почти совпал с уже выбранным шаблоном"),
                        )
                        self.connection.commit()
                    return {
                        "ticket_id": ticket_id,
                        "question": question,
                        "answer": value,
                        "template_id": predicted_template_id,
                        "template_name": str(predicted[1]),
                        "generalizable": True,
                        "skipped": True,
                        "reason": "Такое решение уже известно",
                    }

        template_id = 0
        mode = "exact_first"
        generalizable = is_generalizable_answer(value)
        if generalizable and predicted_template_id:
            with self.lock:
                predicted = self.connection.execute("SELECT answer_template FROM templates WHERE id=?", (predicted_template_id,)).fetchone()
            similarity = _answer_similarity(value, str(predicted[0])) if predicted else 0.0
            # Much stricter than 2.6: a wrong prediction can no longer poison its own template.
            if similarity >= 0.88:
                _, template_id = self._add_example_to_existing_template(
                    question,
                    value,
                    predicted_template_id,
                    replace_exact_manual=True,
                    prefer_manual_text=similarity >= 0.97,
                )
                mode = "smart_existing"
            else:
                template_id, mode = self._save_manual_conservatively(question, value)
        else:
            template_id, mode = self._save_manual_conservatively(question, value)

        template_name = "Точное правило"
        with self.lock:
            self.connection.execute("DELETE FROM pending WHERE ticket_id=?", (ticket_id,))
            self.connection.execute(
                "INSERT OR REPLACE INTO processed(ticket_id, action, created_at) VALUES(?, 'manual', CURRENT_TIMESTAMP)",
                (ticket_id,),
            )
            if template_id:
                template_row = self.connection.execute("SELECT name FROM templates WHERE id=?", (template_id,)).fetchone()
                if template_row:
                    template_name = str(template_row[0])
            details_map = {
                "smart_existing": "Ситуация добавлена к уже подтверждённому шаблону",
                "promoted": "Повторившееся решение повышено из точных правил в умный шаблон",
                "exact_first": "Первое уникальное решение сохранено только как точное правило; общий шаблон пока не создаётся",
            }
            self.connection.execute(
                "INSERT INTO history(ticket_id, question, action, confidence, answer, details) VALUES(?,?,?,?,?,?)",
                (ticket_id, question, "learned_manual", 100, value, details_map.get(mode, mode)),
            )
            self.connection.commit()
        return {
            "ticket_id": ticket_id,
            "question": question,
            "answer": value,
            "template_id": template_id or None,
            "template_name": template_name,
            "generalizable": generalizable,
            "skipped": False,
            "reason": mode,
        }

    def pending_rows(self) -> list[tuple[str, str, str]]:
        with self.lock:
            return [tuple(row) for row in self.connection.execute("SELECT ticket_id, question, updated_at FROM pending ORDER BY updated_at DESC")]

    def delete_pending(self, ticket_id: str) -> None:
        with self.lock:
            self.connection.execute("DELETE FROM pending WHERE ticket_id=?", (ticket_id,))
            self.connection.commit()

    def clear_pending(self) -> int:
        with self.lock:
            count = int(self.connection.execute("SELECT COUNT(*) FROM pending").fetchone()[0])
            self.connection.execute("DELETE FROM pending")
            self.connection.commit()
        return count

    def cleanup_pending_noise(self, exclusions: Iterable[str] = ()) -> int:
        removed = 0
        with self.lock:
            rows = list(self.connection.execute("SELECT ticket_id, question FROM pending"))
            for ticket_id, question in rows:
                if is_noise_question(str(question)) or matches_learning_exclusion(str(question), exclusions):
                    self.connection.execute("DELETE FROM pending WHERE ticket_id=?", (ticket_id,))
                    removed += 1
            self.connection.commit()
        return removed

    def is_processed(self, ticket_id: str) -> bool:
        with self.lock:
            return bool(self.connection.execute("SELECT 1 FROM processed WHERE ticket_id=?", (ticket_id,)).fetchone())

    def mark_processed(self, ticket_id: str, action: str) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO processed(ticket_id, action, created_at) VALUES(?,?,CURRENT_TIMESTAMP)",
                (ticket_id, action),
            )
            self.connection.commit()

    def unmark_processed(self, ticket_id: str) -> None:
        with self.lock:
            self.connection.execute("DELETE FROM processed WHERE ticket_id=?", (ticket_id,))
            self.connection.commit()

    def add_history(
        self,
        ticket_id: str,
        question: str,
        action: str,
        confidence: int,
        answer: str = "",
        details: str = "",
    ) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO history(ticket_id, question, action, confidence, answer, details) VALUES(?,?,?,?,?,?)",
                (ticket_id, question, action, confidence, answer, details),
            )
            self.connection.commit()

    def history_rows(self, limit: int = 500) -> list[tuple[Any, ...]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT id, ticket_id, question, action, confidence, answer, created_at, details FROM history ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [tuple(row) for row in rows]

    def template_rows(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT t.id, t.name, t.answer_template, t.enabled, t.match_mode,
                       t.triggers, t.negative_triggers, t.close_triggers, t.priority, t.source,
                       COUNT(e.id) AS example_count,
                       SUM(CASE WHEN e.source='manual' THEN 1 ELSE 0 END) AS manual_count,
                       GROUP_CONCAT(e.question, ' ') AS example_text,
                       t.updated_at, t.reply_limit_enabled, t.is_locked
                FROM templates t LEFT JOIN examples e ON e.template_id=t.id
                GROUP BY t.id
                ORDER BY t.enabled DESC, manual_count DESC, example_count DESC, t.name
                """
            )
            return [dict(row) for row in rows]

    def get_template(self, template_id: int) -> Optional[dict[str, Any]]:
        with self.lock:
            row = self.connection.execute(
                "SELECT id, name, answer_template, enabled, match_mode, triggers, negative_triggers, close_triggers, priority, source, reply_limit_enabled, is_locked FROM templates WHERE id=?",
                (template_id,),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _validated_template(answer_template: str, match_mode: str) -> tuple[str, str, str]:
        answer_template = canonical_answer(answer_template.strip())
        if not re.match(r"^\s*t\s+a\s+\{ticket_id\}\s+", answer_template, re.IGNORECASE):
            raise ValueError("Ответ должен начинаться с: t a {ticket_id} ")
        if not answer_template.rstrip().endswith("@@@"):
            raise ValueError("Ответ должен заканчиваться на @@@")
        if not has_meaningful_manual_body(answer_template):
            raise ValueError("Ответ пустой или состоит только из приветствия")
        mode = "exact" if match_mode == "exact" else "smart"
        if mode == "smart" and not is_generalizable_answer(answer_template):
            raise ValueError("Ответ слишком короткий или зависит от контекста. Выбери режим «Только точное совпадение» либо допиши полноценный ответ.")
        return answer_template, template_key(answer_template), mode

    def create_template(
        self,
        name: str,
        answer_template: str,
        enabled: bool,
        triggers: str,
        negative_triggers: str,
        close_triggers: str = "",
        match_mode: str = "smart",
        priority: int = 0,
        reply_limit_enabled: bool = True,
    ) -> int:
        answer_template, key, mode = self._validated_template(answer_template, match_mode)
        name = name.strip() or _suggest_template_name(answer_template)
        triggers_clean = "\n".join(split_phrases(triggers))
        negative_clean = "\n".join(split_phrases(negative_triggers))
        close_clean = "\n".join(split_phrases(close_triggers))
        priority = max(0, min(100, int(priority or 0)))
        trigger_list = split_phrases(triggers_clean)
        if mode == "smart" and len(trigger_list) < 2:
            # A newly-created template has no examples yet, so two independent hints are required.
            raise ValueError("Для нового умного шаблона нужно минимум два триггера, чтобы не было случайных ответов.")
        if mode == "exact" and not trigger_list:
            raise ValueError("Для точного шаблона укажи полную фразу вопроса в поле триггеров.")
        with self.lock:
            if self.connection.execute("SELECT 1 FROM templates WHERE answer_key=?", (key,)).fetchone():
                raise ValueError("Шаблон с таким текстом уже существует")
            cursor = self.connection.execute(
                """
                INSERT INTO templates(answer_key, name, answer_template, enabled, match_mode, triggers, negative_triggers, close_triggers, priority, reply_limit_enabled, source)
                VALUES(?,?,?,?,?,?,?,?,?,?,'user')
                """,
                (key, name, answer_template, int(enabled), mode, triggers_clean, negative_clean, close_clean, priority, int(reply_limit_enabled)),
            )
            self.connection.commit()
            template_id = int(cursor.lastrowid)
        self.reload()
        return template_id

    def update_template(
        self,
        template_id: int,
        name: str,
        answer_template: str,
        enabled: bool,
        triggers: str,
        negative_triggers: str,
        close_triggers: str = "",
        match_mode: str = "smart",
        priority: int = 0,
        reply_limit_enabled: bool = True,
    ) -> None:
        answer_template, key, mode = self._validated_template(answer_template, match_mode)
        name = name.strip() or _suggest_template_name(answer_template)
        triggers_clean = "\n".join(split_phrases(triggers))
        negative_clean = "\n".join(split_phrases(negative_triggers))
        close_clean = "\n".join(split_phrases(close_triggers))
        priority = max(0, min(100, int(priority or 0)))
        with self.lock:
            duplicate = self.connection.execute("SELECT id FROM templates WHERE answer_key=? AND id<>?", (key, template_id)).fetchone()
            if duplicate:
                raise ValueError("Другой шаблон с таким текстом уже существует")
            self.connection.execute(
                """
                UPDATE templates
                SET answer_key=?, name=?, answer_template=?, enabled=?, match_mode=?, triggers=?, negative_triggers=?, close_triggers=?, priority=?, reply_limit_enabled=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (key, name, answer_template, int(enabled), mode, triggers_clean, negative_clean, close_clean, priority, int(reply_limit_enabled), template_id),
            )
            self.connection.commit()
        self.reload()

    def merge_from_database(self, source_path: Path) -> dict[str, int]:
        """Merge an exported update database into the active knowledge base.

        Templates are keyed by answer_key, examples keep their relation to the
        mapped template, and exact rules are upserted. Existing local history,
        pending tickets and Telegram state are intentionally left untouched.
        """
        source = sqlite3.connect(source_path)
        source.row_factory = sqlite3.Row
        stats = {"templates_added": 0, "templates_updated": 0, "templates_protected": 0, "examples_added": 0, "exact_rules": 0}
        try:
            source_columns = {row[1] for row in source.execute("PRAGMA table_info(templates)")}
            has_priority = "priority" in source_columns
            has_reply_limit = "reply_limit_enabled" in source_columns
            has_locked = "is_locked" in source_columns
            source_templates = list(source.execute(
                "SELECT id, answer_key, name, answer_template, enabled, match_mode, triggers, negative_triggers, close_triggers, "
                + ("priority" if has_priority else "0 AS priority")
                + ", source, " + ("reply_limit_enabled" if has_reply_limit else "1 AS reply_limit_enabled")
                + ", " + ("is_locked" if has_locked else "0 AS is_locked") + " FROM templates ORDER BY id"
            ))
            id_map: dict[int, int] = {}
            with self.lock:
                for row in source_templates:
                    existing = self.connection.execute("SELECT id, is_locked FROM templates WHERE answer_key=?", (row[1],)).fetchone()
                    values = (str(row[2]), str(row[3]), int(row[4]), str(row[5] or "smart"), str(row[6] or ""),
                              str(row[7] or ""), str(row[8] or ""), max(0, min(100, int(row[9] or 0))), str(row[10] or "user"), int(bool(row[11])))
                    # Защита переносится вместе с пакетом: пакет может включить её у
                    # шаблона, но никогда не снимает уже включённую локально.
                    imported_lock = 1 if str(row[12]).strip().lower() in ("1", "true", "yes") else 0
                    if existing and int(existing[1] or 0):
                        # Защищённый локальный шаблон импорт не перезаписывает: замена
                        # содержимого для пользователя равносильна удалению.
                        target_id = int(existing[0])
                        stats["templates_protected"] += 1
                    elif existing:
                        target_id = int(existing[0])
                        self.connection.execute(
                            "UPDATE templates SET name=?, answer_template=?, enabled=?, match_mode=?, triggers=?, negative_triggers=?, close_triggers=?, priority=?, source=?, reply_limit_enabled=?, is_locked=MAX(is_locked, ?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (*values, imported_lock, target_id),
                        )
                        stats["templates_updated"] += 1
                    else:
                        cursor = self.connection.execute(
                            "INSERT INTO templates(answer_key,name,answer_template,enabled,match_mode,triggers,negative_triggers,close_triggers,priority,source,reply_limit_enabled,is_locked) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (str(row[1]), *values, imported_lock),
                        )
                        target_id = int(cursor.lastrowid)
                        stats["templates_added"] += 1
                    id_map[int(row[0])] = target_id

                if self._table_exists_in(source, "examples"):
                    for row in source.execute("SELECT question, answer, source, template_id FROM examples ORDER BY id"):
                        mapped = id_map.get(int(row[3])) if row[3] is not None else None
                        before = self.connection.total_changes
                        self.connection.execute(
                            "INSERT OR IGNORE INTO examples(question, answer, source, template_id) VALUES(?,?,?,?)",
                            (str(row[0]), str(row[1]), str(row[2] or "manual"), mapped),
                        )
                        if self.connection.total_changes > before:
                            stats["examples_added"] += 1

                if self._table_exists_in(source, "exact_rules"):
                    for row in source.execute("SELECT question, answer, source FROM exact_rules ORDER BY id"):
                        self._upsert_exact_rule(str(row[0]), str(row[1]), str(row[2] or "manual"))
                        stats["exact_rules"] += 1
                self.connection.commit()
        finally:
            source.close()
        self.reload()
        return stats

    @staticmethod
    def _table_exists_in(connection: sqlite3.Connection, table: str) -> bool:
        return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def set_templates_enabled(self, template_ids: Iterable[int], enabled: bool) -> int:
        ids = sorted({int(value) for value in template_ids})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.lock:
            cursor = self.connection.execute(
                f"UPDATE templates SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                (int(enabled), *ids),
            )
            self.connection.commit()
            count = int(cursor.rowcount)
        self.reload()
        return count

    def set_all_templates_enabled(self, enabled: bool) -> int:
        with self.lock:
            cursor = self.connection.execute("UPDATE templates SET enabled=?, updated_at=CURRENT_TIMESTAMP", (int(enabled),))
            self.connection.commit()
            count = int(cursor.rowcount)
        self.reload()
        return count

    # ------------------------------------------------------------------
    # Защита шаблонов от удаления.
    #
    # ИНВАРИАНТ: строка templates с is_locked=1 не удаляется и не перезаписывается
    # ни одной операцией этого класса. Проверка выполняется здесь, внутри той же
    # транзакции (под self.lock), что и само удаление, — а не в интерфейсе. Поэтому
    # защиту нельзя обойти горячей клавишей, контекстным меню, «Удалить все»,
    # очисткой или импортом, и состояние защиты читается в момент выполнения
    # операции, а не в момент открытия диалога подтверждения.
    # ------------------------------------------------------------------
    def set_templates_locked(self, template_ids: Iterable[int], locked: bool) -> int:
        """Включает или снимает защиту; возвращает число реально изменённых шаблонов."""
        ids = sorted({int(value) for value in template_ids})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.lock:
            cursor = self.connection.execute(
                f"UPDATE templates SET is_locked=?, updated_at=CURRENT_TIMESTAMP WHERE id IN ({placeholders}) AND is_locked<>?",
                (int(bool(locked)), *ids, int(bool(locked))),
            )
            self.connection.commit()
            changed = int(cursor.rowcount)
        self.reload()
        return changed

    def locked_template_count(self) -> int:
        with self.lock:
            return int(self.connection.execute("SELECT COUNT(*) FROM templates WHERE is_locked=1").fetchone()[0])

    def deletion_preview(self, template_ids: Optional[Iterable[int]] = None) -> dict[str, int]:
        """Что произойдёт при удалении: сколько удалится и сколько защищённых останется.

        template_ids=None означает «Удалить все». Результат — только прогноз для
        диалога; сама операция удаления перепроверяет защиту заново.
        """
        with self.lock:
            if template_ids is None:
                row = self.connection.execute(
                    "SELECT SUM(is_locked=0), SUM(is_locked=1) FROM templates"
                ).fetchone()
                deletable, protected = int(row[0] or 0), int(row[1] or 0)
                return {"deletable": deletable, "protected": protected, "missing": 0}
            ids = sorted({int(value) for value in template_ids})
            if not ids:
                return {"deletable": 0, "protected": 0, "missing": 0}
            placeholders = ",".join("?" for _ in ids)
            row = self.connection.execute(
                f"SELECT SUM(is_locked=0), SUM(is_locked=1), COUNT(*) FROM templates WHERE id IN ({placeholders})", ids
            ).fetchone()
            deletable, protected, found = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
            return {"deletable": deletable, "protected": protected, "missing": len(ids) - found}

    def delete_templates_report(self, template_ids: Iterable[int]) -> dict[str, int]:
        """Удаляет только незащищённые шаблоны из списка; защищённые остаются полностью
        (вместе с примерами, ID и настройками). Возвращает фактический результат."""
        ids = sorted({int(value) for value in template_ids})
        if not ids:
            return {"deleted": 0, "protected": 0, "missing": 0}
        placeholders = ",".join("?" for _ in ids)
        with self.lock:
            total_before = int(self.connection.execute("SELECT COUNT(*) FROM templates").fetchone()[0])
            rows = self.connection.execute(f"SELECT id, is_locked FROM templates WHERE id IN ({placeholders})", ids).fetchall()
            deletable = [int(row[0]) for row in rows if not int(row[1] or 0)]
            protected = len(rows) - len(deletable)
            deleted = 0
            if deletable:
                marks = ",".join("?" for _ in deletable)
                self.connection.execute(f"DELETE FROM examples WHERE template_id IN ({marks})", deletable)
                # Условие is_locked=0 повторено в самом DELETE: даже если защита
                # появилась между выборкой и удалением, строка не будет удалена.
                cursor = self.connection.execute(f"DELETE FROM templates WHERE id IN ({marks}) AND is_locked=0", deletable)
                deleted = int(cursor.rowcount)
            if deleted and deleted >= total_before:
                self._set_metadata("seed_deleted", "1")
            self.connection.commit()
        self.reload()
        return {"deleted": deleted, "protected": protected, "missing": len(ids) - len(rows)}

    def delete_templates(self, template_ids: Iterable[int]) -> int:
        return self.delete_templates_report(template_ids)["deleted"]

    def delete_all_templates_report(self) -> dict[str, int]:
        """«Удалить все»: удаляет только шаблоны с is_locked=0."""
        with self.lock:
            protected = int(self.connection.execute("SELECT COUNT(*) FROM templates WHERE is_locked=1").fetchone()[0])
            self.connection.execute("DELETE FROM examples WHERE template_id IN (SELECT id FROM templates WHERE is_locked=0)")
            cursor = self.connection.execute("DELETE FROM templates WHERE is_locked=0")
            deleted = int(cursor.rowcount)
            # Базовые шаблоны не возвращаются автоматически, только если библиотека
            # действительно опустела; при сохранённых защищённых записях метка ставится
            # так же, чтобы удалённые базовые шаблоны не воскресали при перезапуске.
            if deleted:
                self._set_metadata("seed_deleted", "1")
            self.connection.commit()
        self.reload()
        return {"deleted": deleted, "protected": protected, "missing": 0}

    def delete_all_templates(self) -> int:
        return self.delete_all_templates_report()["deleted"]

    def restore_seed_templates(self) -> None:
        with self.lock:
            self._set_metadata("seed_deleted", "0")
            self.connection.execute("DELETE FROM metadata WHERE key='seed_hash'")
            self.connection.commit()
        self.import_seed()
        self._sync_templates()
        self.reload()

    def cleanup_suspicious_templates(self) -> int:
        moved = 0
        with self.lock:
            # Защищённые шаблоны пользователь сохранил осознанно: «очистка» их не трогает.
            rows = list(self.connection.execute("SELECT id, answer_template, match_mode FROM templates WHERE is_locked=0"))
            for template_id, answer_template, match_mode in rows:
                if str(match_mode or "smart") == "exact" or is_generalizable_answer(str(answer_template)):
                    continue
                examples = list(self.connection.execute("SELECT question, answer, source FROM examples WHERE template_id=?", (template_id,)))
                for question, answer, source in examples:
                    self._upsert_exact_rule(str(question), str(answer), str(source or "manual"))
                self.connection.execute("DELETE FROM examples WHERE template_id=?", (template_id,))
                self.connection.execute("DELETE FROM templates WHERE id=? AND is_locked=0", (template_id,))
                moved += 1
            self.connection.commit()
        self.reload()
        return moved

    def template_examples(self, template_id: int, limit: int = 100) -> list[tuple[str, str, str]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT question, source, created_at FROM examples WHERE template_id=? ORDER BY source='manual' DESC, id DESC LIMIT ?",
                (template_id, limit),
            )
            return [tuple(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.lock:
            examples = int(self.connection.execute("SELECT COUNT(*) FROM examples").fetchone()[0])
            manual_examples = int(self.connection.execute("SELECT COUNT(*) FROM examples WHERE source='manual'").fetchone()[0])
            manual_exact = int(self.connection.execute("SELECT COUNT(*) FROM exact_rules WHERE source='manual'").fetchone()[0])
            manual = manual_examples + manual_exact
            exact = int(self.connection.execute("SELECT COUNT(*) FROM exact_rules").fetchone()[0])
            pending = int(self.connection.execute("SELECT COUNT(*) FROM pending").fetchone()[0])
            processed = int(self.connection.execute("SELECT COUNT(*) FROM processed").fetchone()[0])
            templates = int(self.connection.execute("SELECT COUNT(*) FROM templates").fetchone()[0])
            disabled = int(self.connection.execute("SELECT COUNT(*) FROM templates WHERE enabled=0").fetchone()[0])
            locked = int(self.connection.execute("SELECT COUNT(*) FROM templates WHERE is_locked=1").fetchone()[0])
        return {
            "examples": examples,
            "manual": manual,
            "exact": exact,
            "pending": pending,
            "processed": processed,
            "templates": templates,
            "disabled": disabled,
            "locked": locked,
        }

    def export_examples(self, path: Path) -> None:
        with self.lock:
            rows = list(self.connection.execute("SELECT question, answer FROM examples ORDER BY id"))
            exact_rows = list(self.connection.execute("SELECT question, answer FROM exact_rules ORDER BY id"))
        blocks: list[str] = []
        for index, row in enumerate([*rows, *exact_rows], start=1):
            blocks.append(f"TICKET #{index}\nQUESTION:\n{row[0]}\n\nANSWER:\n{row[1]}\n" + "=" * 72)
        path.write_text("\n".join(blocks), encoding="utf-8")

def _answer_similarity(left: str, right: str) -> float:
    left_body = answer_body(left)
    right_body = answer_body(right)
    left_tokens = set(tokens(left_body))
    right_tokens = set(tokens(right_body))
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    char_score = cosine_counter(char_ngrams(left_body), char_ngrams(right_body))
    score = 0.45 * jaccard + 0.55 * char_score

    url_re = re.compile(r"https?://[^\\s]+", re.IGNORECASE)
    left_urls = {value.rstrip(".,;") for value in url_re.findall(left_body)}
    right_urls = {value.rstrip(".,;") for value in url_re.findall(right_body)}
    if left_urls and right_urls and left_urls.intersection(right_urls):
        score += 0.13
    if _suggest_template_name(left) == _suggest_template_name(right):
        score += 0.14
    return min(1.0, score)

