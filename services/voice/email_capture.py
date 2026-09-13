"""Email capture (vr_plan.md §9): normalise hard, score confidence, spell the local part only."""
from __future__ import annotations

import re

from config import load_yaml, threshold
from contracts.voice import EmailCandidate

_DOMAINS: list[str] = list(load_yaml("au_email_domains.yaml").get("domains", []))
_DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
           "seven": "7", "eight": "8", "nine": "9"}
_CONFUSABLE = {"b": "bravo", "p": "papa", "d": "delta", "t": "tango", "e": "echo", "m": "mike", "n": "november",
               "s": "sierra", "f": "foxtrot"}
_NATO = {"alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e", "foxtrot": "f",
         "golf": "g", "hotel": "h", "india": "i", "juliet": "j", "kilo": "k", "lima": "l", "mike": "m",
         "november": "n", "oscar": "o", "papa": "p", "quebec": "q", "romeo": "r", "sierra": "s", "tango": "t",
         "uniform": "u", "victor": "v", "whiskey": "w", "xray": "x", "x-ray": "x", "yankee": "y", "zulu": "z"}
_LOCAL_OK = re.compile(r"^[a-z0-9._+-]+$")
_SHAPE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,6}$")


def normalise(raw: str) -> str:
    text = " " + (raw or "").lower().strip() + " "
    # Lookarounds keep the surrounding spaces so back-to-back tokens ("at at", "dot dot") all match.
    text = re.sub(r"(?<=\s)at\s+the\s+rate\s+of(?=\s)", "@", text)
    text = re.sub(r"(?<=\s)at\s+sign(?=\s)", "@", text)
    text = re.sub(r"(?<=\s)at(?=\s)", "@", text)
    text = re.sub(r"(?<=\s)(dot|point|full\s+stop)(?=\s)", ".", text)
    text = re.sub(r"(?<=\s)(underscore|under\s+score)(?=\s)", "_", text)
    text = re.sub(r"(?<=\s)(dash|hyphen|minus)(?=\s)", "-", text)
    text = re.sub(r"(?<=\s)plus(?=\s)", "+", text)
    for word, digit in _DIGITS.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"@+", "@", text)
    text = re.sub(r"\.{2,}", ".", text)
    return text


def split(normalised: str) -> tuple[str, str]:
    if "@" not in normalised:
        return normalised, ""
    local, _, domain = normalised.rpartition("@")
    return local, domain


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score_domain(domain: str) -> tuple[float, str]:
    """(domain_score, corrected_domain) per §9.2."""
    if not domain:
        return 0.0, domain
    if domain in _DOMAINS:
        return 1.0, domain
    best = min(_DOMAINS, key=lambda d: levenshtein(domain, d)) if _DOMAINS else None
    if best is not None and levenshtein(domain, best) <= 2:
        return 0.85, best
    if _SHAPE.match(domain):
        return 0.55, domain
    return 0.0, domain


def score_local(local: str, *, min_word_confidence: float | None = None) -> float:
    score = 1.0
    if len(local) < 3:
        score -= 0.4
    if len(local) > 30:
        score -= 0.2
    if not _LOCAL_OK.match(local or "x"):
        score -= 0.5
    if re.search(r"[bcdfghjklmnpqrstvwxz]{4,}", local):
        score -= 0.25
    if ".." in local:
        score -= 0.3
    if min_word_confidence is not None and min_word_confidence < 0.7:
        score -= 0.3
    score = max(0.0, min(1.0, score))
    if min_word_confidence is not None:
        score = max(0.0, min(1.0, score * min_word_confidence))
    return score


def capture(raw: str, *, word_confidences: list[float] | None = None) -> EmailCandidate:
    local, domain = split(normalise(raw))
    domain_score, corrected = score_domain(domain)
    min_conf = min(word_confidences) if word_confidences else None
    local_score = score_local(local, min_word_confidence=min_conf)
    confidence = round(min(domain_score, local_score), 3)
    return EmailCandidate(raw=raw, local=local, domain=corrected, domain_score=domain_score, local_score=local_score,
                          confidence=confidence, needs_spelling=confidence < threshold("EMAIL_CONFIDENCE_THRESHOLD"))


def spoken(local: str, domain: str) -> str:
    def say(part: str) -> str:
        return part.replace(".", " dot ").replace("_", " underscore ").replace("-", " dash ").replace("+", " plus ")

    return re.sub(r"\s+", " ", f"{say(local)} at {say(domain)}").strip()


def letters_from_spelling(text: str) -> str:
    """'s a r a h', 's-a-r-a-h', 'b for bravo, e for echo' or 'sierra alpha' → 'sarah'."""
    out = []
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    skip_next = False
    for i, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token == "for" and i + 1 < len(tokens):
            skip_next = True  # "b for bravo": bravo confirms b, don't add both
            continue
        if token in ("as", "in", "like"):
            skip_next = True
            continue
        if token in _NATO:
            if out and i >= 1 and tokens[i - 1] == _NATO[token]:
                continue
            out.append(_NATO[token])
        elif token in _DIGITS:
            out.append(_DIGITS[token])
        elif token in ("dot", "point"):
            out.append(".")
        elif token in ("underscore",):
            out.append("_")
        elif token in ("dash", "hyphen"):
            out.append("-")
        elif len(token) == 1:
            out.append(token)
        elif token.isdigit():
            out.append(token)
    return "".join(out)


def phonetic_checks(local: str) -> list[str]:
    """Only the confusable letters get a phonetic confirmation (§9.3)."""
    seen: list[str] = []
    for ch in local:
        if ch in _CONFUSABLE and ch not in seen:
            seen.append(ch)
    return [f"{ch.upper()} for {_CONFUSABLE[ch]}" for ch in seen]


def rebuild(local: str, domain: str) -> EmailCandidate:
    domain_score, corrected = score_domain(domain)
    local_score = score_local(local)
    confidence = round(min(domain_score, local_score), 3)
    return EmailCandidate(raw=f"{local}@{domain}", local=local, domain=corrected, domain_score=domain_score,
                          local_score=local_score, confidence=confidence,
                          needs_spelling=confidence < threshold("EMAIL_CONFIDENCE_THRESHOLD"))
