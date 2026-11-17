"""The word list the checks use: the shipped list, without what the creator switched off, plus their own phrases.

The shipped list is ``lexicon.json``. Two of its groups can be changed by the creator, entry by entry, on the
Settings page: ``compliance`` (wording that advertising rules may object to) and ``ai_tells`` (wording that
makes a spoken line sound written by a machine). Every entry of those two groups has an ``id``.

- ``effective(store)`` is the list every check in the app reads: the shipped list without the entries the
  creator switched off, plus the phrases they added themselves (``own_phrases``).
- ``notice(lexicon)`` is what goes out once beside findings made with such a list: the day the list was
  reviewed, its disclaimer, and how many shipped entries are switched off.
- ``payload(store)`` is what the Settings page shows; ``save(store, body)`` stores a change.

What the creator changed is kept under one key of ``config.json`` (``KEY``), written through
``Store.update_config``, so the other settings stay as they are::

    "wordlist": {"off": ["superlative_zui"], "phrases": ["包邮"]}

What this module holds to:

- Nothing the creator typed is ever compiled as a pattern. Their phrases are looked for as they stand
  (``guardrails.fold_literal`` and ``str.find`` in ``guardrails.lint_script``).
- Any entry may be switched off, an ``error`` one too: it is the creator's list on the creator's computer.
  An id that names no entry of the shipped list (an entry a later version removed) is ignored.
- A stored value never stops a check. One that cannot be used as it stands (edited by hand, cut short, over
  a limit) is left out as a whole: the shipped list is read, and ``unreadable`` says so.
- It states nothing about the law. The lists are this project's own. ``lexicon.json`` says for each entry
  what was read and when, and for some that nothing was.

There is no MCP tool for any of this: only the creator changes the list, in the app.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import guardrails
from .store import loads_stored

KEY = "wordlist"                        # the one key of config.json this module writes
GROUPS = ("compliance", "ai_tells")     # the groups whose entries can be switched off, in the order they are shown
MAX_OWN_PHRASES = 50                    # must match what studio/web/wordlist.js is told in ``limits``; the page reads it from there
MAX_OWN_PHRASE_CHARS = 40
STATE = "_wordlist"                     # in an effective list: what the creator changed, for ``notice``

# What the Settings page is given of an entry. The pattern is left out: the page shows ``shows``, the examples.
_SHOWN = {
    "compliance": ("id", "severity", "shows", "why_zh", "why_en", "note_zh", "note_en", "basis"),
    "ai_tells": ("id", "shows", "source", "pattern_no"),
}


class Refused(ValueError):
    """A change to the word list that was not stored.

    ``code`` is what a page can map; ``message`` (English) and ``message_zh`` are written to be shown;
    ``extra`` holds the limit that was passed, when there is one.
    """

    def __init__(self, code: str, message: str, message_zh: str, extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code, self.message, self.message_zh, self.extra = code, message, message_zh, dict(extra or {})


def _bad() -> Refused:
    return Refused("bad_wordlist",
                   "The change to the word list could not be read: `off` has to be a list of entry ids and `phrases` a "
                   "list of texts, and at least one of the two has to be there.",
                   "词表的改动读不出来：off 必须是一组条目编号，phrases 必须是一组文字，两者至少要有一个。")


def shipped() -> Dict[str, Any]:
    """The list as it ships, read from ``lexicon.json``. A new object each time, so a caller may change it."""
    return guardrails.load_lexicon()


def _group(lexicon: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    value = lexicon.get(name)
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def entry_ids(lexicon: Dict[str, Any]) -> List[str]:
    """The ids of the entries that can be switched off, in the order of the list."""
    return [entry["id"] for name in GROUPS for entry in _group(lexicon, name) if isinstance(entry.get("id"), str)]


def _distinct(phrases: List[str]) -> List[str]:
    """``phrases`` with each one once, in the order given.

    Two phrases the checks read as the same one (``guardrails.fold_literal``: they differ only in letter case,
    width or a character that draws nothing) are one, as first given. A phrase with nothing to look for is dropped.
    """
    seen = set()
    out: List[str] = []
    for phrase in phrases:
        key = guardrails.fold_literal(phrase)
        if key and key not in seen:
            seen.add(key)
            out.append(phrase)
    return out


def _stored(config: Any) -> Tuple[List[str], List[str], bool]:
    """``(ids switched off, own phrases, unreadable)`` as the settings hold them.

    ``unreadable`` is true when something is stored under ``KEY`` that cannot be used as it stands: it is not an
    object, ``off`` or ``phrases`` is not a list of texts, or there are more phrases or a longer phrase than may
    be stored. Nothing of such a value is used, so a check is never slowed or stopped by what a file holds.
    Ids are returned as stored; one that names no entry is ignored where the list is built.
    """
    value = config.get(KEY) if isinstance(config, dict) else None
    if value is None:
        return [], [], False
    if not isinstance(value, dict):
        return [], [], True
    off, phrases = value.get("off", []), value.get("phrases", [])
    usable = (isinstance(off, list) and all(isinstance(entry_id, str) for entry_id in off)
              and isinstance(phrases, list) and len(phrases) <= MAX_OWN_PHRASES
              and all(isinstance(phrase, str) and 0 < len(phrase) <= MAX_OWN_PHRASE_CHARS for phrase in phrases))
    if not usable:
        return [], [], True
    return list(off), list(phrases), False


def from_config(config: Any) -> Dict[str, Any]:
    """The effective list for these settings (what ``Store.load_config`` returns); see ``effective``."""
    lexicon = shipped()
    off, phrases, unreadable = _stored(config)
    wanted = set(off)
    switched_off = [entry_id for entry_id in entry_ids(lexicon) if entry_id in wanted]     # an id that names no entry is ignored
    for name in GROUPS:
        if isinstance(lexicon.get(name), list):
            lexicon[name] = [entry for entry in lexicon[name] if not (isinstance(entry, dict) and entry.get("id") in wanted)]
    lexicon["own_phrases"] = _distinct(phrases)
    lexicon[STATE] = {"switched_off": switched_off, "unreadable": unreadable}
    return lexicon


def effective(store: Any) -> Dict[str, Any]:
    """The list the checks use: the shipped list without the entries the creator switched off, plus their own phrases.

    It has the shape of ``lexicon.json`` and is handed to ``guardrails.lint_script`` as ``lexicon``. Beside the
    shipped groups it holds ``own_phrases`` (the creator's phrases, looked for as they stand and flagged for
    review) and ``_wordlist`` (``{"switched_off": [ids], "unreadable": bool}``), which ``notice`` reads.

    ``store`` is anything with ``load_config()``. If what is stored cannot be used, the shipped list comes back
    whole and ``unreadable`` is true; this never raises for what the settings hold.
    """
    return from_config(store.load_config())


def effective_in(data_dir: Any) -> Dict[str, Any]:
    """The effective list of the installation whose data folder is ``data_dir``, without creating or changing anything there.

    For the command line (``python3 -m studio.guardrails --data-dir``), which has no ``Store``: opening one
    creates the folder and a database and closes the folder to other accounts. A folder without settings, or
    with settings that cannot be read, gives the shipped list.
    """
    try:
        config = loads_stored((Path(data_dir) / "config.json").read_bytes().decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        config = None
    return from_config(config)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def notice(lexicon: Dict[str, Any]) -> Dict[str, Any]:
    """What is said once wherever findings made with ``lexicon`` are listed.

    ``reviewed_on`` is the day the shipped list was last reviewed; the disclaimer says that the lists are this
    project's own and no statement of any rule. ``switched_off`` counts the shipped entries the creator switched
    off and ``own_phrases`` the phrases they added. ``unreadable`` is true when what was stored could not be
    used, so the shipped list was read as it stands.
    """
    state = lexicon.get(STATE) if isinstance(lexicon.get(STATE), dict) else {}
    own = lexicon.get("own_phrases")
    return {
        "reviewed_on": _text(lexicon.get("_reviewed_on")),
        "disclaimer_zh": _text(lexicon.get("_disclaimer_zh")), "disclaimer_en": _text(lexicon.get("_disclaimer_en")),
        "switched_off": len(state.get("switched_off") or []),
        "own_phrases": len(own) if isinstance(own, list) else 0,
        "unreadable": bool(state.get("unreadable")),
    }


def payload(store: Any) -> Dict[str, Any]:
    """The list as the Settings page shows it (``GET /api/wordlist``).

    ``sources`` is what the shipped list says about each text it rests on; ``entries`` is every entry that can
    be switched off, in the order of the list, each with ``group`` (``compliance`` or ``ai_tells``), ``on`` and
    what the list says about it (never its pattern); ``phrases`` is the creator's own list; ``limits`` is how
    many phrases may be kept and how long one may be. ``switched_off`` counts the entries that are off.
    ``unreadable`` is true when what is stored cannot be used: everything is then shown as on and no phrase is
    listed, which is also what the checks do.
    """
    lexicon = shipped()
    off, phrases, unreadable = _stored(store.load_config())
    wanted = set(off)
    entries: List[Dict[str, Any]] = []
    for name in GROUPS:
        for entry in _group(lexicon, name):
            shown = {key: entry.get(key) for key in _SHOWN[name]}
            shown.update(group=name, severity=entry.get("severity") or guardrails.REVIEW, on=entry.get("id") not in wanted)
            entries.append(shown)
    sources = lexicon.get("_sources")
    return {
        "reviewed_on": _text(lexicon.get("_reviewed_on")),
        "disclaimer_zh": _text(lexicon.get("_disclaimer_zh")), "disclaimer_en": _text(lexicon.get("_disclaimer_en")),
        "sources": sources if isinstance(sources, dict) else {},
        "entries": entries,
        "phrases": _distinct(phrases),
        "limits": {"phrases": MAX_OWN_PHRASES, "phrase_chars": MAX_OWN_PHRASE_CHARS},
        "switched_off": sum(1 for entry in entries if not entry["on"]),
        "unreadable": unreadable,
    }


def _wanted_off(value: Any, lexicon: Dict[str, Any]) -> List[str]:
    """The ids to switch off, from a request: those of ``value`` that name an entry, in the order of the list."""
    if not isinstance(value, list) or not all(isinstance(entry_id, str) for entry_id in value):
        raise _bad()
    wanted = set(value)
    return [entry_id for entry_id in entry_ids(lexicon) if entry_id in wanted]


def _findable(phrase: str) -> bool:
    """Whether a line that says exactly ``phrase`` is flagged for it.

    Asked of the linter itself rather than worked out here, so the answer stays true if what counts as spoken
    changes. Today a phrase cannot be found when nothing in it draws anything, or when part of it stands in
    cue brackets (``[赠品]``), which are not said aloud.
    """
    probe = {"script_lines": [{"line_no": 1, "spoken_script_zh": phrase}]}
    return any(finding.detail == guardrails.OWN_PHRASE_DETAIL
               for finding in guardrails.lint_script(probe, {}, lexicon={"own_phrases": [phrase]}))


def _uncheckable() -> Refused:
    return Refused("wordlist_phrase_uncheckable",
                   "One of the phrases could never be found in a spoken line: nothing in it is said aloud. Text in "
                   "square brackets is read as a cue, not as speech.",
                   "有一条说法在口播台词里永远找不到：里面没有会念出来的字。方括号里的内容算提示，不算台词。")


def _wanted_phrases(value: Any) -> List[str]:
    """The creator's own phrases, from a request: each on one line, each once, within the limits.

    Runs of whitespace become one space and an empty entry is dropped. Nothing is cut to fit: a phrase over
    the length limit, more phrases than may be kept, and a phrase that could never be found in a spoken line
    (``_findable``) are refused. The linter is asked about no more phrases than may be kept.
    """
    if not isinstance(value, list) or not all(isinstance(phrase, str) for phrase in value):
        raise _bad()
    tidy = [phrase for phrase in (" ".join(given.split()) for given in value) if phrase]
    for phrase in tidy:
        if len(phrase) > MAX_OWN_PHRASE_CHARS:
            raise Refused("wordlist_phrase_too_long",
                          "A phrase of the word list can have at most %d characters. Keep only the words to look for."
                          % MAX_OWN_PHRASE_CHARS,
                          "词表里的一条说法最多 %d 个字符。请只留下要找的那几个字。" % MAX_OWN_PHRASE_CHARS,
                          {"limit": MAX_OWN_PHRASE_CHARS})
        if not guardrails.fold_literal(phrase):
            raise _uncheckable()
    phrases = _distinct(tidy)
    if len(phrases) > MAX_OWN_PHRASES:
        raise Refused("wordlist_too_many_phrases",
                      "The word list can hold at most %d phrases of your own. Remove one before adding another." % MAX_OWN_PHRASES,
                      "词表里你自己加的说法最多 %d 条。请先删掉一条再加。" % MAX_OWN_PHRASES,
                      {"limit": MAX_OWN_PHRASES})
    if not all(_findable(phrase) for phrase in phrases):
        raise _uncheckable()
    return phrases


def save(store: Any, body: Any) -> Dict[str, Any]:
    """Store a change to the list (``PUT /api/wordlist``) and return ``payload(store)`` as it then is.

    ``body`` holds ``off`` (the ids of every shipped entry that is to be off), ``phrases`` (every phrase of the
    creator's own list), or both. What is absent stays as stored. An id that names no entry is dropped. If what
    was stored could not be used, the part that is absent starts empty, and the stored value is usable again.

    Raises ``Refused`` and stores nothing: ``bad_wordlist`` (not an object, neither key, or a key that is not a
    list of texts), ``wordlist_phrase_too_long``, ``wordlist_phrase_uncheckable``, ``wordlist_too_many_phrases``.
    Only ``KEY`` is written; every other setting is left as it is (``Store.update_config``).
    """
    if not isinstance(body, dict) or not ("off" in body or "phrases" in body):
        raise _bad()
    lexicon = shipped()
    off = _wanted_off(body["off"], lexicon) if "off" in body else None
    phrases = _wanted_phrases(body["phrases"]) if "phrases" in body else None

    def change(config: Dict[str, Any]) -> None:
        kept_off, kept_phrases, _unreadable = _stored(config)
        kept = set(kept_off)
        config[KEY] = {
            "off": off if off is not None else [entry_id for entry_id in entry_ids(lexicon) if entry_id in kept],
            "phrases": phrases if phrases is not None else _distinct(kept_phrases),
        }

    store.update_config(change)
    return payload(store)
