"""Transcript parsing and clean-up: speakers, filler words, stutters, self-corrections.

Rule of thumb: when someone corrects themselves, the LAST thing they said wins.
Every correction we apply is logged so the user can audit it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from dates import find_date_phrases


@dataclass
class Utterance:
    speaker: str | None
    raw: str
    text: str = ""


# ---------------------------------------------------------------- parsing
_TS = re.compile(r"^\s*[\[(]?\d{1,2}:\d{2}(?::\d{2})?[\])]?\s*[-–]?\s*")
_SPEAKER = re.compile(r"^([A-Z][\w.'\-]*(?:\s+[\w.'\-]+){0,3})\s*:\s*(.*)$")
# "Action item: ..." looks like a speaker label but isn't one.
_NOT_SPEAKERS = {"action item", "action items", "action", "decision", "decisions", "todo",
                 "to-do", "note", "notes", "agenda", "ai", "next steps", "summary",
                 "recap", "reminder"}


def parse_transcript(text: str) -> list[Utterance]:
    utts: list[Utterance] = []
    labels: dict[str, str] = {}
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        match = None
        for cand in (_TS.sub("", line, count=1), line):
            m = _SPEAKER.match(cand)
            if m:
                match = m
                break
        if match and match.group(1).strip().lower() in _NOT_SPEAKERS:
            utts.append(Utterance(None, line))          # label line, no speaker
        elif match:
            label = " ".join(match.group(1).split())
            label = labels.setdefault(label.lower(), label)
            utts.append(Utterance(label, match.group(2).strip()))
        elif utts:
            utts[-1].raw = (utts[-1].raw + " " + line).strip()   # wrapped line
        else:
            utts.append(Utterance(None, line))
    return utts


def speaker_aliases(utts: list[Utterance]) -> dict[str, str]:
    """lowercased name or first name -> canonical speaker label."""
    aliases: dict[str, str] = {}
    for label in {u.speaker for u in utts if u.speaker}:
        aliases[label.lower()] = label
    for label in sorted({u.speaker for u in utts if u.speaker}):
        first = label.split()[0]
        if len(first) > 1:
            aliases.setdefault(first.lower(), label)
    return aliases


# ---------------------------------------------------------- self-correction
_PUNCT = r"[\s,;:\-–—.…]*"
_MARKER = (r"(?:no(?:\s+no)?|sorry|wait|i\s+mean|i\s+meant|actually|rather|or\s+rather|"
           r"scratch\s+that|make\s+that|correction|my\s+bad)")
_CONNECT = (r"(?:the|date|deadline|day|is|it's|its|it|on|by|that's|that|should|be|was|i|meant|"
            r"make|let's|say|sorry|no|wait|rather|actually|um|uh|er|erm)")
_BETWEEN = re.compile(rf"{_PUNCT}{_MARKER}\b{_PUNCT}(?:{_CONNECT}\b{_PUNCT})*", re.I)
_DOUBLE_PREP = re.compile(r"\b(on|by|before|until|for)\s+(on|by|before|until|for)\b", re.I)


def _drop_corrected(text, find_spans, kind, speaker, log):
    """If two consecutive spans are separated only by a correction marker
    ('Friday, no, the date is on Thursday'), delete the first one and the marker."""
    for _ in range(20):
        spans = find_spans(text)
        for a, b in zip(spans, spans[1:]):
            if _BETWEEN.fullmatch(text[a[1]:b[0]]):
                log.append({"type": "self_correction", "field": kind, "speaker": speaker,
                            "original": text[a[0]:b[1]], "kept": text[b[0]:b[1]]})
                text = _DOUBLE_PREP.sub(r"\2", text[:a[0]] + text[b[0]:])
                break
        else:
            return text
    return text


# ------------------------------------------------------------------ fillers
_START = r"(?:^|(?<=[,.!?]\s))"
_FILLERS = [
    re.compile(r"\b(?:um+|uh+|uhm+|erm+|er|ah+|hmm+|mm+|mhm)\b[,.…]*\s*", re.I),
    re.compile(rf"{_START}(?:you know|i mean),?\s*", re.I),
    re.compile(r",\s*(?:you know|i mean)(?=\s*[,.!?]|$)", re.I),
    re.compile(r",\s*like,\s*", re.I),
    re.compile(r"\b(?:basically|literally),?\s*", re.I),
    re.compile(r"\bso yeah\b[,.]?\s*", re.I),
]
_OPENERS = re.compile(r"^(?:(?:okay|ok|alright|all right|right|so|well|yeah|yep|yes|sure|cool|great|good)\b[,.!]?\s*)+", re.I)
_BARE_ACK = re.compile(r"(?:sure|yes|yep|yeah|okay|ok|alright|all right|cool)", re.I)
_KEEP_REPEATS = {"that", "had"}
_BIGRAM = re.compile(r"\b([\w']+\s+[\w']+)(?:,?\s+\1\b)+", re.I)
_UNIGRAM = re.compile(r"\b([\w']+)(?:,?\s+\1\b)+", re.I)


def _destutter(text: str) -> str:
    def keep_first(m):
        return m.group(0) if m.group(1).lower() in _KEEP_REPEATS else m.group(1)
    return _UNIGRAM.sub(keep_first, _BIGRAM.sub(lambda m: m.group(1), text))


def _normalise(text: str) -> str:
    text = re.sub(r"\b(I'll|I will|I can|we'll|will|can|to|and|the)\s*,\s+", r"\1 ", text, flags=re.I)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r",(\s*,)+", ",", text)
    text = re.sub(r"([.!?])\s*[,.]+", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;-–—")
    if text and text[0].islower() and not re.match(r"[a-z]+[A-Z]", text):
        text = text[0].upper() + text[1:]
    return text


def clean_utterance(text: str, speaker, name_re, log) -> str:
    text = _drop_corrected(text, lambda t: [(p.start, p.end) for p in find_date_phrases(t)],
                           "date", speaker, log)
    if name_re:
        text = _drop_corrected(text, lambda t: [m.span() for m in name_re.finditer(t)],
                               "owner", speaker, log)
    for rx in _FILLERS:
        text = rx.sub("", text)
    text = _destutter(text)
    text = _normalise(text)
    stripped = _normalise(_OPENERS.sub("", text))
    if not stripped and _BARE_ACK.fullmatch(text.strip(" .!")):
        return text        # a lone "Sure." may be someone accepting a task; the extractor needs it
    return stripped


def clean_transcript(raw: str):
    """-> (utterances with .text filled in, cleaned transcript string, correction log)"""
    utts = parse_transcript(raw)
    aliases = speaker_aliases(utts)
    names = {lab for lab in aliases.values()} | {lab.split()[0] for lab in aliases.values()}
    name_re = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b") if names else None
    log: list[dict] = []
    kept = []
    for u in utts:
        u.text = clean_utterance(u.raw, u.speaker, name_re, log)
        if u.text:
            kept.append(u)
    cleaned = "\n".join(f"{u.speaker}: {u.text}" if u.speaker else u.text for u in kept)
    return kept, cleaned, log
