"""Rule-based extraction of decisions and action items.

Design rules (non-negotiable):
  * Never invent an owner. If none is stated -> owner=None, needs_review=True.
  * Never invent a deadline. If none is stated or it can't be resolved -> None + needs_review.
  * Every item keeps the original utterance in `source_text` so a human can verify it.

`extract()` (bottom of file) tries the LLM extractor first and falls back to these rules
if the API is unavailable or returns something unusable.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date

from cleaner import clean_transcript, speaker_aliases
import llm_extractor
from dates import DatePhrase, find_date_phrases, resolve
from textutil import TENTATIVE, slug

_VERBS = (
    r"take|do|send|draft|write|prepare|share|set\s+up|follow\s+up|own|handle|check|review|"
    r"schedule|book|update|fix|look|get|pull|put|create|build|talk|reach|ping|email|call|"
    r"finish|finali[sz]e|submit|deliver|document|test|deploy|ship|circulate|coordinate|"
    r"confirm|raise|file|add|make|run|investigate|research|organi[sz]e|arrange|plan|sort|"
    r"push|merge|close|open|complete|collect|gather|compile|post|publish|loop|decide|"
    r"approve|sign|pay|order|buy|fill|clean|migrate|set|reply|respond|chase|nudge|prep|"
    r"summari[sz]e|record|upload|draw|design|walk|present|demo|onboard|hire|interview"
)

_LABEL = re.compile(r"^(action\s*items?|todo|to-do|ai|decision)\s*[:\-–]\s*", re.I)
_DECISION = re.compile(r"""\b(?:
    we(?:'ve|\s+have)?\s+(?:decided|agreed|settled|resolved|concluded)
  | decided\s+(?:to|on|that)
  | (?:it(?:'s|\s+is)|that(?:'s|\s+is))\s+(?:decided|settled|agreed)
  | (?:the\s+)?decision\s+is
  | (?:we(?:'re|\s+are)|we(?:'ll|\s+will)|let's|lets|let\s+us)\s+(?:going\s+|moving\s+)?
      (?:go\s+with|going\s+with|stick\s+with|sticking\s+with|use|using|pick|choose|
         move\s+forward\s+with|move\s+ahead\s+with|adopt|drop|dropping|skip|skipping|
         postpone|postponing|delay|cancel|cancelling)
  | (?:agreed|approved)\s+(?:on|that|to)
  | going\s+with
)\b""", re.I | re.X)
_ACCEPT = re.compile(
    r"^(?:(?:will\s+do|can\s+do|on\s+it|got\s+it|i\s+got\s+it|no\s+problem|done\s+deal)\b"
    r"|(?:sure|yes|yep|yeah|okay|ok|alright|all\s+right|absolutely|of\s+course|cool)(?:\s+thing)?[.!]*$)", re.I)
# a short reply that proposes its own date: "Wednesday works", "Friday is fine"
_DATE_REPLY = re.compile(r"\b(?:works?|good|fine|ok(?:ay)?|can\s+do|doable|perfect|sounds|instead|rather|better|by|before)\b", re.I)
_ADDR = re.compile(
    rf"^(?:(?P<name>[A-Za-z]+)\s*,\s*)?(?:(?:can|could|would|will)\s+you|you(?:'ll|\s+will|\s+need\s+to|\s+should)|please)"
    rf"\s+(?:please\s+)?(?:{_VERBS})\b", re.I)
_PLEASE = re.compile(r"^(?:(?P<name>[A-Za-z]+)\s*,\s*)?please\b", re.I)
_NAMED = re.compile(
    r"\b(?P<names>[A-Z][a-z]+(?:\s+and\s+[A-Z][a-z]+)?)"
    r"(?:'ll\s+|\s+(?:will|is\s+going\s+to|are\s+going\s+to|is\s+gonna|needs?\s+to|has\s+to|have\s+to|should|must|to|(?P<modal>can|could|would))\s+)"
    r"(?!be\b|not\b)")
_SELF = re.compile(
    rf"\b(?:i(?:'ll|\s+will|\s+shall|\s+am\s+going\s+to|'m\s+going\s+to|'m\s+gonna|\s+am\s+gonna)\s+(?!not\b|never\b)"
    rf"|let\s+me\s+"
    rf"|i\s+can\s+(?=(?:{_VERBS})\b)"
    rf"|i\s+(?:need\s+to|have\s+to|should|must|ought\s+to)\s+(?=(?:{_VERBS})\b)"
    rf"|i\s+(?:can\s+)?take\s+(?:care\s+of|this|that|it)\b)", re.I)
_UNASSIGNED = re.compile(
    rf"\b(?:(?:we|someone|somebody|everyone|anyone|anybody)\s+(?:need(?:s)?\s+to|should|have\s+to|has\s+to|must|ought\s+to)\s+(?!be\b|not\b|think\b|worry\b|forget\b|remember\b)"
    rf"|(?:we|someone|somebody|everyone)\s*(?:'ll|\s+will|\s+can|\s+could)\s+(?=(?:{_VERBS})\b))", re.I)
_FOLLOWUP_DL = re.compile(r"^(?:by|before|until|no\s+later\s+than|due|on|latest)\b", re.I)
_NOT_NAMES = {"I", "We", "You", "They", "He", "She", "It", "The", "This", "That", "These", "Those",
              "Then", "So", "And", "But", "Also", "Maybe", "Please", "Let", "Today", "Tomorrow",
              "Someone", "Somebody", "Everyone", "Everybody", "Nobody", "Who", "What", "When",
              "Where", "Why", "How", "Yes", "No", "Okay", "Ok", "Sure", "Thanks", "Thank",
              "Welcome", "According", "Due", "Next", "Back", "Going", "Anyone", "Anybody"}
_ACCEPT_STOP = {"that", "this", "with", "have", "will", "from", "about", "need", "should", "also", "just"}


@dataclass
class Sent:
    utt: int
    speaker: str | None
    text: str
    raw: str


@dataclass
class Cls:
    kind: str                       # decision | action | accept
    description: str
    owners: list = field(default_factory=list)
    self_commit: bool = False
    addressed: bool = False
    reasons: list = field(default_factory=list)


def _reason(code, message):
    return {"code": code, "message": message}


def _split(utts):
    sents = []
    for i, u in enumerate(utts):
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", u.text):
            part = part.strip()
            if part:
                sents.append(Sent(i, u.speaker, part, u.raw))
    return sents


def _owner(name, aliases):
    """-> (canonical label, is_a_meeting_speaker)"""
    label = aliases.get(name.lower())
    return (label, True) if label else (name, False)


def _tidy(desc):
    desc = re.sub(r"^(?:and|but|also|then)\s+", "", desc, flags=re.I).strip()
    return desc[:1].upper() + desc[1:] if desc else desc


def _classify(s: Sent, aliases) -> Cls | None:
    t = s.text.strip()
    forced = None
    m = _LABEL.match(t)
    if m:
        forced = "decision" if m.group(1).lower().startswith("decision") else "action"
        t = t[m.end():].strip()
    if not t:
        return None
    words = len(t.split())
    is_q = t.endswith("?")

    if forced == "decision" or (not forced and not is_q and words >= 4 and _DECISION.search(t)):
        return Cls("decision", _tidy(t))
    if not forced and _ACCEPT.match(t):
        return Cls("accept", t, owners=[s.speaker] if s.speaker else [], self_commit=True)

    tentative = [_reason("tentative", "Wording is tentative (maybe/might/try to) — confirm it's a real commitment")] \
        if TENTATIVE.search(t) else []

    # "Priya, can you ..." / "Can you ..." / "Please ..."
    m = _ADDR.match(t) or _PLEASE.match(t)
    if m:
        name = m.group("name")
        if name and (name[:1].isupper() or name.lower() in aliases) and name not in _NOT_NAMES \
                and name.lower() not in {"so", "ok", "okay", "sure", "yes", "yeah", "also", "and", "but", "then"}:
            label, is_spk = _owner(name, aliases)
            reasons = [] if is_spk else [_reason("owner_not_speaker", f"'{label}' didn't speak in this meeting — confirm the owner")]
            return Cls("action", _tidy(t), owners=[label], addressed=True, reasons=reasons + tentative)
        return Cls("action", _tidy(t), addressed=True,
                   reasons=[_reason("owner_unclear", "Request was addressed to 'you' but nobody was named")] + tentative)
    if is_q and not forced:
        return None

    for m in _NAMED.finditer(t):
        names = re.split(r"\s+and\s+", m.group("names"))
        if any(n in _NOT_NAMES for n in names):
            continue
        rest = t[m.end():]
        known = all(n.lower() in aliases for n in names)
        verb_ok = re.match(rf"(?:also\s+|then\s+|just\s+)?(?:{_VERBS})\b", rest, re.I)
        if m.group("modal") and not verb_ok:
            continue
        if not known and not verb_ok:
            continue            # "Stripe will charge 2%" is not an action item
        owners, reasons = [], []
        for n in names:
            label, is_spk = _owner(n, aliases)
            owners.append(label)
            if not is_spk:
                reasons.append(_reason("owner_not_speaker", f"'{label}' didn't speak in this meeting — confirm this is the right person"))
        return Cls("action", _tidy(t), owners=owners, reasons=reasons + tentative)

    m = _SELF.search(t)
    if m:
        if s.speaker:
            return Cls("action", _tidy(t), owners=[s.speaker], self_commit=True, reasons=tentative)
        return Cls("action", _tidy(t), reasons=[_reason("owner_missing", "Speaker unknown, so the owner can't be determined")] + tentative)

    if _UNASSIGNED.search(t):
        return Cls("action", _tidy(t),
                   reasons=[_reason("owner_missing", "Nobody was assigned (said 'we'/'someone')")] + tentative)
    if forced == "action":
        return Cls("action", _tidy(t), reasons=[_reason("owner_missing", "No owner stated")] + tentative)
    return None


def _deadline(s: Sent, nxt: Sent | None, ref: date | None, is_action: bool) -> dict:
    out = {"deadline": None, "raw": None, "note": None, "reasons": []}
    phrases: list[DatePhrase] = find_date_phrases(s.text)
    from_next = False
    if not phrases and nxt is not None and nxt.speaker == s.speaker \
            and (_FOLLOWUP_DL.match(nxt.text) or len(nxt.text.split()) <= 5):
        phrases = find_date_phrases(nxt.text)
        from_next = bool(phrases)

    if not phrases:
        if is_action:
            out["reasons"].append(_reason("deadline_missing", "No deadline was stated"))
        return out

    results = [(p, resolve(p.text, ref)) for p in phrases]
    out["raw"] = " | ".join(dict.fromkeys(p.text for p, _ in results))
    dates = {r.date for _, r in results if r.date}
    all_ok = all(r.date for _, r in results)

    if len(results) > 1 and not (all_ok and len(dates) == 1):
        if is_action:
            out["reasons"].append(_reason("multiple_dates", f"Several different dates were mentioned ({out['raw']}) — pick one"))
        return out

    p, r = results[0]
    if r.date:
        out["deadline"] = r.date
        out["note"] = r.note
        if from_next:
            out["note"] = "; ".join(x for x in (r.note, "deadline taken from the following sentence") if x)
        if r.ambiguous and is_action:
            out["reasons"].append(_reason("deadline_ambiguous", f"Please confirm the date: {r.note}"))
    elif is_action:
        out["reasons"].append(_reason("deadline_unresolved", f"'{p.text}' couldn't be turned into a date: {r.unresolved_reason}"))
    return out


_DL_CODES = {"deadline_missing", "deadline_unresolved", "deadline_ambiguous", "multiple_dates"}


_TOPIC_FOR = re.compile(r"\b(?:for|on|about|regarding)\s+(?:the\s+|our\s+|a\s+)?([a-z][a-z\-]*(?:\s+[a-z][a-z\-]*)?)", re.I)
_TOPIC_STOP = {"the", "a", "an", "our", "it", "this", "that", "now", "later", "today"}


def _rule_topic(desc: str) -> str:
    """Best-effort label for a decision when no LLM is available."""
    if find_date_phrases(desc) and re.search(r"\b(?:launch|release|ship|go\s*live|deadline)", desc, re.I):
        return "launch_date"
    m = _TOPIC_FOR.search(desc)
    if m:
        words = [w for w in m.group(1).lower().split() if w not in _TOPIC_STOP]
        if words:
            return slug("_".join(words))
    return "general"


def _make_item(s: Sent, c: Cls, dl: dict) -> dict:
    reasons = list(c.reasons)
    if c.kind == "action" and not c.owners and not any(r["code"].startswith("owner") for r in reasons):
        reasons.append(_reason("owner_missing", "No owner stated"))
    reasons += dl["reasons"]
    return {
        "kind": "decision" if c.kind == "decision" else "action_item",
        "description": c.description,
        "topic": _rule_topic(c.description) if c.kind == "decision" else None,
        "owner": ", ".join(c.owners) or None,
        "deadline": dl["deadline"], "deadline_raw": dl["raw"], "deadline_note": dl["note"],
        "speaker": s.speaker, "source_text": s.raw,
        "needs_review": bool(reasons), "review_reasons": reasons,
        "_utt": s.utt, "_addressed": c.addressed,
    }


def _is_acceptance(prev: dict, s: Sent, c: Cls) -> bool:
    """Is this self-commitment just the addressee saying yes to the previous request?"""
    if c.kind == "accept":
        return True
    content = lambda txt: {w for w in re.findall(r"[a-z]{4,}", txt.lower()) if w not in _ACCEPT_STOP}
    if content(c.description) & content(prev["description"]):
        return True
    return len(s.text.split()) <= 8 and bool(re.search(r"\b(?:it|that|this)\b", s.text, re.I))


def _is_date_reply(s: Sent) -> bool:
    return (not s.text.endswith("?") and len(s.text.split()) <= 8
            and bool(_DATE_REPLY.search(s.text)) and bool(find_date_phrases(s.text)))


def _try_merge(prev: dict, s: Sent, c: Cls, dl: dict) -> bool:
    if not (s.speaker and prev["_addressed"] and prev["_utt"] == s.utt - 1
            and prev["speaker"] != s.speaker and prev["owner"] in (None, s.speaker)
            and _is_acceptance(prev, s, c)):
        return False
    prev["owner"] = s.speaker
    reasons = [r for r in prev["review_reasons"] if not r["code"].startswith("owner_unclear")
               and r["code"] != "owner_missing"]
    if dl["raw"] is not None:        # the reply names its own date -> final statement wins
        old_raw = prev["deadline_raw"]
        note = dl["note"]
        if old_raw and old_raw.lower() != dl["raw"].lower():
            note = "; ".join(x for x in (note, f"replaced '{old_raw}' from the original request") if x)
        reasons = [r for r in reasons if r["code"] not in _DL_CODES] + dl["reasons"]
        prev["deadline"], prev["deadline_raw"], prev["deadline_note"] = dl["deadline"], dl["raw"], note
    # else: the reply is a bare "Got it"/"Sure" -> only the owner is added; the request's deadline stays
    prev["review_reasons"] = reasons
    prev["needs_review"] = bool(reasons)
    prev["source_text"] += "\n" + s.raw
    prev["_addressed"] = False
    return True


def extract_rules(transcript: str, meeting_date: date | None) -> dict:
    utts, cleaned, log = clean_transcript(transcript)
    aliases = speaker_aliases(utts)
    sents = _split(utts)
    cls = [_classify(s, aliases) for s in sents]
    items: list[dict] = []
    for i, (s, c) in enumerate(zip(sents, cls)):
        if c is None:
            if items and _is_date_reply(s) and _try_merge(items[-1], s, Cls("accept", s.text, self_commit=True),
                                                           _deadline(s, None, meeting_date, True)):
                continue
            continue
        nxt = sents[i + 1] if i + 1 < len(sents) and cls[i + 1] is None else None
        dl = _deadline(s, nxt, meeting_date, is_action=(c.kind != "decision"))
        if c.self_commit and items and _try_merge(items[-1], s, c, dl):
            continue
        if c.kind == "accept":
            continue
        items.append(_make_item(s, c, dl))
    for it in items:
        it.pop("_utt"), it.pop("_addressed")
    return {"cleaned_transcript": cleaned, "cleaning_log": log, "items": items,
            "speakers": sorted(set(aliases.values()))}


log = logging.getLogger("meetloop")


def extract(transcript: str, meeting_date: date | None, mode: str | None = None) -> dict:
    """Public entry point. Output format is identical whichever extractor ran.

    mode: "auto" (default; LLM, falling back to rules on any failure) or "rules" (never call the API).
    Result carries `extractor` ("llm" | "rules") and `fallback_reason` (why the LLM wasn't used).
    """
    mode = (mode or os.environ.get("MEETLOOP_EXTRACTOR") or "auto").strip().lower()
    reason = None
    if mode != "rules":
        try:
            return {**llm_extractor.extract_llm(transcript, meeting_date), "extractor": "llm", "fallback_reason": None}
        except llm_extractor.LLMExtractionError as e:
            reason = str(e)
        except Exception as e:                       # never let an LLM problem break the endpoint
            reason = f"unexpected error ({type(e).__name__})"
        log.warning("LLM extraction failed, using rule-based extractor: %s", reason)
    return {**extract_rules(transcript, meeting_date), "extractor": "rules", "fallback_reason": reason}
