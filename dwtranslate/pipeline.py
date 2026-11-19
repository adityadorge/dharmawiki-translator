"""Orchestration: source page -> machine-translated draft -> (editor review) -> published page."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Optional

import mwparserfromhell as mwp

from .llm import ParamTranslator
from .mediawiki import MediaWiki
from .store import Store
from .wikitext import DEVANAGARI_RE, LETTER_RE, normalize_title, translate_wikitext

log = logging.getLogger(__name__)

DRAFT_TPL = "Machine translation"
PUBLISHED_TPL = "Translated page"
REVIEWED = {"reviewed", "approved", "publish", "ready"}


class Pipeline:
    def __init__(self, cfg: dict, wiki: MediaWiki, store: Store, lang: str):
        self.cfg, self.wiki, self.store, self.lang = cfg, wiki, store, lang
        self.lang_name = cfg["languages"][lang]
        self.tr = ParamTranslator(cfg["param"], store, lang, self.lang_name)
        self.pool = ThreadPoolExecutor(max_workers=cfg["param"].get("concurrency", 4))
        t = cfg["titles"]
        self.draft_fmt, self.pub_fmt = t["draft"], t["published"]
        self.draft_ns = t.get("draft_namespace", 3000)
        self.lang_suffixes = tuple(f"/{c}" for c in cfg["languages"])

    def draft_title(self, src: str) -> str:
        return self.draft_fmt.format(title=src, lang=self.lang)

    def pub_title(self, src: str) -> str:
        return self.pub_fmt.format(title=src, lang=self.lang)

    # ------------------------------------------------------------------ select
    def select(self, pages=None, category=None, all_pages=False, limit=None) -> list[str]:
        titles: list[str] = list(pages or [])
        if category:
            titles += list(self.wiki.category_members(category))
        if all_pages:
            for ns in self.cfg["pages"].get("namespaces", [0]):
                titles += list(self.wiki.all_pages(ns))
        excl = set(self.cfg["pages"].get("exclude_titles", []))
        out, seen = [], set()
        for t in titles:
            if t in seen or t in excl or t.endswith(self.lang_suffixes):
                continue
            seen.add(t)
            out.append(t)
        return out[:limit] if limit else out

    # --------------------------------------------------------------- translate
    def translate_pages(self, titles: Iterable[str], dry_run=False, force=False, out_dir="output"):
        results = {}
        for i, t in enumerate(titles, 1):
            try:
                results[t] = self.translate_page(t, dry_run=dry_run, force=force, out_dir=out_dir)
            except Exception as e:  # keep going; record the failure
                log.exception("failed: %s", t)
                self.store.page_put(t, self.lang, state="error", stats=str(e)[:500])
                results[t] = "error"
            log.info("[%d] %s -> %s (API calls so far: %d)", i, t, results[t], self.tr.calls)
        return results

    def translate_page(self, title: str, dry_run=False, force=False, out_dir="output") -> str:
        src = self.wiki.page(title)
        if not src or src["redirect"]:
            return "missing-or-redirect"
        title = src["title"]
        if set(src["categories"]) & set(self.cfg["pages"].get("exclude_categories", [])):
            return "excluded-category"
        if not self._is_source_language(src["content"]):
            return "excluded-not-english"

        rec = self.store.page_get(title, self.lang) or {}
        draft_t, pub_t = self.draft_title(title), self.pub_title(title)
        existing = self.wiki.exists([draft_t, pub_t])

        if not force and pub_t in existing:
            if rec.get("source_revid") == src["revid"] or self._tpl_revid(pub_t, PUBLISHED_TPL) == src["revid"]:
                return "up-to-date"
            if not dry_run:
                self._mark_outdated(pub_t, PUBLISHED_TPL, src["revid"])
            self.store.page_put(title, self.lang, state="published-outdated")
            return "published-outdated"

        if not force and draft_t in existing:
            if rec.get("source_revid") == src["revid"]:
                return "up-to-date"
            humans = self.wiki.editors(draft_t) - {self.wiki.username}
            if humans:  # never overwrite editors' work
                if not dry_run:
                    self._mark_outdated(draft_t, DRAFT_TPL, src["revid"])
                self.store.page_put(title, self.lang, state="draft-outdated")
                return "draft-outdated"

        body, stats = translate_wikitext(src["content"], self.tr.translate,
                                         link_map_fn=self._published_link_map, map_fn=self.pool.map)
        local_title = self.tr.translate(title)
        header = ("{{%s|source=%s|lang=%s|revid=%d|title=%s|status=needs-review}}\n"
                  % (DRAFT_TPL, title, self.lang, src["revid"], local_title.replace("|", "{{!}}")))
        content = header + body
        stats_s = json.dumps(stats.__dict__)

        if dry_run:
            p = Path(out_dir) / self.lang / (re.sub(r'[\\/:*?"<>|]', "_", title) + ".wikitext")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self.store.page_put(title, self.lang, state="dry-run", stats=stats_s)
            return f"dry-run -> {p}"

        r = self.wiki.edit(draft_t, content,
                           summary=f"Machine translation ({self.lang_name}) of [[{title}]] rev {src['revid']} via BharatGen Param — needs review")
        if self.cfg.get("set_page_language"):
            self.wiki.set_page_language(draft_t, self.lang, "Machine translation")
        self.store.page_put(title, self.lang, source_revid=src["revid"], target_title=draft_t,
                            target_revid=r.get("newrevid"), state="draft" if r.get("result") == "Success" else "queued",
                            stats=stats_s)
        return "draft-saved" if r.get("result") == "Success" else "queued-by-moderation"

    # ----------------------------------------------------------------- publish
    def publish(self, dry_run=False) -> dict:
        """Move drafts whose template has status=reviewed to the public title."""
        results = {}
        suffix = f"/{self.lang}"
        for t in self.wiki.all_pages(self.draft_ns):
            if not t.endswith(suffix):
                continue
            page = self.wiki.page(t)
            code = mwp.parse(page["content"])
            tpl = next((x for x in code.filter_templates() if normalize_title(str(x.name)) == DRAFT_TPL), None)
            if not tpl or self._param(tpl, "status").lower() not in REVIEWED:
                continue
            source, revid = self._param(tpl, "source"), self._param(tpl, "revid")
            pub_t = self.pub_title(source)
            new_tpl = "{{%s|source=%s|lang=%s|revid=%s|title=%s}}" % (
                PUBLISHED_TPL, source, self.lang, revid, self._param(tpl, "title"))
            code.replace(tpl, new_tpl)
            text = self._relink(str(code))
            if dry_run:
                results[t] = f"would publish -> {pub_t}"
                continue
            if self.wiki.exists([pub_t]):
                results[t] = f"skipped: {pub_t} already exists (merge manually)"
                continue
            self.wiki.move(t, pub_t, reason=f"Publishing reviewed {self.lang_name} translation of [[{source}]]")
            self.wiki.edit(pub_t, text, summary="Published reviewed translation", nocreate=True)
            if self.cfg.get("set_page_language"):
                self.wiki.set_page_language(pub_t, self.lang, "Translated page")
            self.store.page_put(source, self.lang, target_title=pub_t, state="published",
                                source_revid=int(revid) if revid.isdigit() else None)
            results[t] = f"published -> {pub_t}"
        return results

    # ----------------------------------------------------------------- helpers
    def _is_source_language(self, text: str) -> bool:
        letters = LETTER_RE.findall(text)
        return bool(letters) and len(DEVANAGARI_RE.findall(text)) / len(letters) < 0.6

    def _published_link_map(self, targets: list[str]) -> dict[str, str]:
        norm = {normalize_title(t.split("#")[0]) for t in targets if t.strip()}
        cand = {self.pub_title(t): t for t in norm}
        found = self.wiki.exists(list(cand))
        return {cand[p]: p for p in found}

    def _relink(self, text: str) -> str:
        code = mwp.parse(text)
        links = [l for l in code.filter_wikilinks() if ":" not in str(l.title).lstrip(":")]
        m = self._published_link_map([str(l.title) for l in links])
        for l in links:
            base = normalize_title(str(l.title).split("#")[0])
            if base in m:
                if l.text is None:
                    l.text = str(l.title)
                l.title = m[base] + ("#" + str(l.title).split("#", 1)[1] if "#" in str(l.title) else "")
        return str(code)

    @staticmethod
    def _param(tpl, name: str) -> str:
        return str(tpl.get(name).value).strip() if tpl.has(name) else ""

    def _tpl_revid(self, title: str, tpl_name: str) -> Optional[int]:
        p = self.wiki.page(title)
        if not p:
            return None
        for x in mwp.parse(p["content"]).filter_templates():
            if normalize_title(str(x.name)) == tpl_name and x.has("revid"):
                v = str(x.get("revid").value).strip()
                return int(v) if v.isdigit() else None
        return None

    def _mark_outdated(self, title: str, tpl_name: str, new_revid: int):
        p = self.wiki.page(title)
        code = mwp.parse(p["content"])
        for x in code.filter_templates():
            if normalize_title(str(x.name)) == tpl_name:
                if x.has("outdated") and str(x.get("outdated").value).strip() == str(new_revid):
                    return
                x.add("outdated", str(new_revid))
                self.wiki.edit(title, str(code), basetimestamp=p["timestamp"],
                               summary=f"Source page changed (rev {new_revid}); flagging translation for update")
                return
        log.warning("%s has no {{%s}} template; not flagged", title, tpl_name)
