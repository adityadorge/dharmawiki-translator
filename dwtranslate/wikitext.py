"""Wikitext masking / segmentation / reassembly.

Strategy
--------
1. Parse the page with mwparserfromhell.
2. Replace everything that must NOT be translated (templates, <ref>, comments,
   categories, files, tag markup, magic words ...) with XLIFF-style
   placeholders  <x id="N"/>.
3. Replace links with paired tags  <g id="N">display text</g>  so the display
   text is translated *in context* while the link target is kept.
4. Split the masked text into lines (wikitext paragraphs are lines) and decide
   per line whether it is translatable (skips Devanagari / IAST verse lines,
   lines with no letters, etc.).
5. After translation, validate that every placeholder survived, then unmask.

`unmask(mask(x)) == x` is guaranteed and checked by tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import mwparserfromhell as mwp
from mwparserfromhell.nodes import (
    Argument, Comment, ExternalLink, Heading, HTMLEntity, Tag, Template, Text, Wikilink,
)

# Tags whose whole content is copied verbatim.
PROTECTED_TAGS = {
    "ref", "references", "math", "chem", "ce", "nowiki", "pre", "syntaxhighlight",
    "source", "code", "gallery", "score", "templatedata", "graph", "mapframe",
    "embedvideo", "youtube", "evu", "widget", "drawio", "timeline", "inputbox",
    "categorytree", "includeonly", "noinclude", "onlyinclude", "html", "script", "style",
    "imagemap", "hiero",
}
NON_TEXT_LINK_NS = ("category:", "file:", "image:", "media:")
INTERLANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z]+)?:", re.I)
MAGIC_WORD_RE = re.compile(r"(__[A-Z]+__)")
SENTINEL = "CONTENT"

X_RE = re.compile(r'<\s*x\s+id\s*=\s*["\']?(\d+)["\']?\s*/?\s*>', re.I)
G_OPEN_RE = re.compile(r'<\s*g\s+id\s*=\s*["\']?(\d+)["\']?\s*>', re.I)
G_CLOSE_RE = re.compile(r"<\s*/\s*g\s*>", re.I)
G_PAIR_RE = re.compile(r'<g id="(\d+)">(.*?)</g>', re.S)
ANY_TAG_RE = re.compile(r'<x id="\d+"/>|<g id="\d+">|</g>')

DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
IAST_CHARS = set("āīūṛṝḷḹṅñṭḍṇśṣḥṃṁĀĪŪṚṜḶṄÑṬḌṆŚṢḤṂ")
LETTER_RE = re.compile(r"[^\W\d_]", re.U)


@dataclass
class Slot:
    kind: str                    # "ph" | "link" | "xlink"
    raw: str = ""                # verbatim wikitext for "ph"
    target: str = ""             # link target / url
    had_text: bool = True        # [[Target]] vs [[Target|text]]


@dataclass
class MaskedPage:
    text: str
    slots: dict[int, Slot] = field(default_factory=dict)

    def link_targets(self) -> list[str]:
        return [s.target for s in self.slots.values() if s.kind == "link"]


class Masker:
    def __init__(self) -> None:
        self.slots: dict[int, Slot] = {}

    def _new(self, slot: Slot) -> int:
        i = len(self.slots) + 1
        self.slots[i] = slot
        return i

    def ph(self, raw: str) -> str:
        if raw == "":
            return ""
        return f'<x id="{self._new(Slot("ph", raw=raw))}"/>'

    def mask(self, wikitext: str) -> MaskedPage:
        code = mwp.parse(wikitext)
        return MaskedPage(self._nodes(code), self.slots)

    # -- recursive walk ------------------------------------------------------
    def _nodes(self, code) -> str:
        out: list[str] = []
        for node in code.nodes:
            out.append(self._node(node))
        return "".join(out)

    def _node(self, node) -> str:
        if isinstance(node, Text):
            parts = MAGIC_WORD_RE.split(str(node))
            return "".join(self.ph(p) if MAGIC_WORD_RE.fullmatch(p) else p for p in parts)

        if isinstance(node, (Template, Comment, Argument, HTMLEntity)):
            return self.ph(str(node))

        if isinstance(node, Heading):
            eq = "=" * node.level
            return eq + self._nodes(node.title) + eq

        if isinstance(node, Wikilink):
            title = str(node.title)
            t = title.strip()
            if t.lower().startswith(NON_TEXT_LINK_NS) or (
                not t.startswith(":") and INTERLANG_RE.match(t) and not t.lower().startswith(("help:", "user:", "talk:"))
                and len(t.split(":")[0]) <= 3
            ):
                return self.ph(str(node))
            if node.text is not None:
                inner = self._nodes(node.text)
                had = True
            else:
                inner, had = title, False
            i = self._new(Slot("link", target=title, had_text=had))
            return f'<g id="{i}">{inner}</g>'

        if isinstance(node, ExternalLink):
            if node.brackets and node.title is not None and str(node.title).strip():
                i = self._new(Slot("xlink", target=str(node.url)))
                return f'<g id="{i}">{self._nodes(node.title)}</g>'
            return self.ph(str(node))

        if isinstance(node, Tag):
            name = str(node.tag).strip().lower()
            if (name in PROTECTED_TAGS or node.self_closing or node.contents is None
                    or not str(node.contents).strip()):
                return self.ph(str(node))
            whole = str(node)
            orig = node.contents
            node.contents = SENTINEL
            shell = str(node)
            node.contents = orig
            if shell.count(SENTINEL) != 1:
                return self.ph(whole)
            open_, close = shell.split(SENTINEL)
            if open_ + str(orig) + close != whole:
                return self.ph(whole)
            return self.ph(open_) + self._nodes(orig) + self.ph(close)

        return self.ph(str(node))


# ---------------------------------------------------------------------------
# Unmasking
# ---------------------------------------------------------------------------
def normalize_tags(s: str) -> str:
    """Model output may write <x id=3 /> or < /g> etc. — normalise."""
    s = X_RE.sub(lambda m: f'<x id="{m.group(1)}"/>', s)
    s = G_OPEN_RE.sub(lambda m: f'<g id="{m.group(1)}">', s)
    s = G_CLOSE_RE.sub("</g>", s)
    return s


def tag_signature(s: str) -> tuple[list[str], list[str], int]:
    s = normalize_tags(s)
    xs = sorted(re.findall(r'<x id="(\d+)"/>', s))
    gs = sorted(re.findall(r'<g id="(\d+)">', s))
    closes = s.count("</g>")
    return xs, gs, closes


def tags_match(src: str, tgt: str) -> bool:
    a, b = tag_signature(src), tag_signature(tgt)
    if a != b:
        return False
    # every <g> must be closed in order (no crossing)
    return G_PAIR_RE.sub("", normalize_tags(tgt)).count("</g>") == 0 and \
        len(G_PAIR_RE.findall(normalize_tags(tgt))) == len(a[1])


def unmask(text: str, slots: dict[int, Slot], link_map: Optional[dict[str, str]] = None) -> str:
    link_map = link_map or {}
    text = normalize_tags(text)

    def _x(m):
        return slots[int(m.group(1))].raw

    def _g(m):
        slot = slots[int(m.group(1))]
        inner = X_RE.sub(_x, m.group(2))
        if slot.kind == "xlink":
            return f"[{slot.target} {inner}]"
        target = slot.target
        mapped = link_map.get(normalize_title(target.split("#")[0]))
        if mapped:
            frag = "#" + target.split("#", 1)[1] if "#" in target else ""
            target = mapped + frag
            return f"[[{target}|{inner}]]"
        if not slot.had_text and inner == slot.target:
            return f"[[{slot.target}]]"
        return f"[[{target}|{inner}]]"

    text = G_PAIR_RE.sub(_g, text)
    return X_RE.sub(_x, text)


def normalize_title(t: str) -> str:
    t = t.strip().lstrip(":").replace("_", " ")
    t = re.sub(r"\s+", " ", t)
    return t[:1].upper() + t[1:] if t else t


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------
HEADING_LINE = re.compile(r"^(=+)(\s*)(.*?)(\s*)(=+)(\s*)$")
LIST_LINE = re.compile(r"^([*#:;]+\s*)(.*)$", re.S)
LEAD_TAGS = re.compile(r'^(?:\s*<x id="\d+"/>)+\s*')
TRAIL_TAGS = re.compile(r'\s*(?:<x id="\d+"/>\s*)+$')


def visible_text(s: str) -> str:
    return ANY_TAG_RE.sub(" ", s)


def is_translatable(s: str) -> bool:
    vis = visible_text(s)
    letters = LETTER_RE.findall(vis)
    if len(letters) < 2:
        return False
    if not re.search(r"[A-Za-z]", vis):
        return False                      # already non-Latin (Devanagari verse etc.)
    deva = len(DEVANAGARI_RE.findall(vis))
    if deva / len(letters) > 0.5:
        return False
    iast = sum(1 for ch in vis if ch in IAST_CHARS)
    if iast >= 3 and iast / len(letters) > 0.06:
        return False                      # IAST transliteration of a verse
    return True


@dataclass
class Segment:
    prefix: str
    core: str
    suffix: str
    translate: bool


def segment_line(line: str) -> Segment:
    m = HEADING_LINE.match(line)
    if m and len(m.group(1)) == len(m.group(5)):
        prefix = m.group(1) + m.group(2)
        core = m.group(3)
        suffix = m.group(4) + m.group(5) + m.group(6)
    else:
        lm = LIST_LINE.match(line)
        prefix, core, suffix = (lm.group(1), lm.group(2), "") if lm else ("", line, "")
    lead = LEAD_TAGS.match(core)
    if lead:
        prefix += lead.group(0)
        core = core[lead.end():]
    trail = TRAIL_TAGS.search(core)
    if trail:
        suffix = trail.group(0) + suffix
        core = core[:trail.start()]
    # keep surrounding whitespace outside
    ws_l = len(core) - len(core.lstrip())
    ws_r = len(core) - len(core.rstrip())
    prefix += core[:ws_l]
    if ws_r:
        suffix = core[len(core) - ws_r:] + suffix
        core = core[:len(core) - ws_r]
    core = core[ws_l:] if ws_l else core
    return Segment(prefix, core, suffix, is_translatable(core))


# ---------------------------------------------------------------------------
# High-level: translate a page
# ---------------------------------------------------------------------------
TranslateFn = Callable[[str], str]


def split_fallback(core: str, translate: TranslateFn) -> str:
    """Translate only the text between tags; structure is guaranteed intact."""
    parts = re.split(r'(<x id="\d+"/>|<g id="\d+">|</g>)', core)
    out = []
    for p in parts:
        if not p or ANY_TAG_RE.fullmatch(p) or not is_translatable(p):
            out.append(p)
            continue
        l = len(p) - len(p.lstrip())
        r = len(p) - len(p.rstrip())
        body = p.strip()
        t = translate(body)
        out.append(p[:l] + t + (p[len(p) - r:] if r else ""))
    return "".join(out)


@dataclass
class PageStats:
    segments: int = 0
    translated: int = 0
    fallback: int = 0
    skipped: int = 0


def translate_wikitext(
    wikitext: str,
    translate: TranslateFn,
    link_map_fn: Optional[Callable[[list[str]], dict[str, str]]] = None,
    map_fn: Callable = map,
) -> tuple[str, PageStats]:
    """Translate a wikitext page. `translate` receives masked text with tags.

    map_fn can be a ThreadPoolExecutor.map for concurrency.
    """
    masked = Masker().mask(wikitext)
    lines = masked.text.split("\n")
    segs = [segment_line(l) for l in lines]
    stats = PageStats(segments=len(segs))

    def work(seg: Segment) -> tuple[str, str]:
        if not seg.translate:
            return seg.core, "skip"
        out = translate(seg.core)
        if tags_match(seg.core, out):
            return normalize_tags(out), "ok"
        return split_fallback(seg.core, translate), "fallback"

    results = list(map_fn(work, segs))
    new_lines = []
    field_for = {"ok": "translated", "fallback": "fallback", "skip": "skipped"}
    for seg, (core, how) in zip(segs, results):
        setattr(stats, field_for[how], getattr(stats, field_for[how]) + 1)
        new_lines.append(seg.prefix + core + seg.suffix)

    link_map = link_map_fn(masked.link_targets()) if link_map_fn else {}
    return unmask("\n".join(new_lines), masked.slots, link_map), stats
