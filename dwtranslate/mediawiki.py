"""Minimal MediaWiki Action API client (works with MediaWiki 1.35)."""
from __future__ import annotations

import logging
import time
from typing import Iterator, Optional

import requests

log = logging.getLogger(__name__)


class WikiError(RuntimeError):
    def __init__(self, code, info):
        super().__init__(f"{code}: {info}")
        self.code = code


class MediaWiki:
    def __init__(self, api_url: str, user: Optional[str] = None, password: Optional[str] = None,
                 user_agent: str = "DharmawikiParamTranslator/1.0", edit_delay: float = 1.0):
        self.api = api_url
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent
        self.edit_delay = edit_delay
        self._csrf = None
        self.username = None
        if user and password:
            self.login(user, password)

    # low level ------------------------------------------------------------------
    def _req(self, method: str, **params) -> dict:
        params.update(format="json", formatversion="2", maxlag="5")
        for attempt in range(6):
            if method == "GET":
                r = self.s.get(self.api, params=params, timeout=60)
            else:
                r = self.s.post(self.api, data=params, timeout=120)
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            err = data.get("error")
            if err and err.get("code") == "maxlag":
                time.sleep(int(r.headers.get("Retry-After", 5)))
                continue
            if err:
                raise WikiError(err.get("code"), err.get("info"))
            return data
        raise WikiError("http", "too many retries")

    def get(self, **p):
        return self._req("GET", **p)

    def post(self, **p):
        return self._req("POST", **p)

    # auth -------------------------------------------------------------------------
    def login(self, user: str, password: str):
        """Use a Bot Password from Special:BotPasswords (user is 'Name@BotName')."""
        tok = self.get(action="query", meta="tokens", type="login")["query"]["tokens"]["logintoken"]
        r = self.post(action="login", lgname=user, lgpassword=password, lgtoken=tok)["login"]
        if r.get("result") != "Success":
            raise WikiError("login", r)
        self.username = r["lgusername"]
        log.info("logged in as %s", self.username)

    def csrf(self) -> str:
        if not self._csrf:
            self._csrf = self.get(action="query", meta="tokens")["query"]["tokens"]["csrftoken"]
        return self._csrf

    # read -----------------------------------------------------------------------
    def page(self, title: str) -> Optional[dict]:
        """Return {title, revid, timestamp, content, redirect, categories} or None if missing."""
        d = self.get(action="query", prop="revisions|info|categories", titles=title, rvprop="ids|timestamp|content",
                     rvslots="main", cllimit="max", redirects="0")
        p = d["query"]["pages"][0]
        if p.get("missing") or p.get("invalid"):
            return None
        rev = p["revisions"][0]
        return {"title": p["title"], "revid": rev["revid"], "timestamp": rev["timestamp"],
                "content": rev["slots"]["main"]["content"], "redirect": p.get("redirect", False),
                "categories": [c["title"] for c in p.get("categories", [])]}

    def exists(self, titles: list[str]) -> set[str]:
        found = set()
        for i in range(0, len(titles), 50):
            d = self.get(action="query", titles="|".join(titles[i:i + 50]))
            for p in d["query"]["pages"]:
                if not p.get("missing") and not p.get("invalid"):
                    found.add(p["title"])
        return found

    def editors(self, title: str) -> set[str]:
        d = self.get(action="query", prop="revisions", titles=title, rvprop="user", rvlimit="max")
        p = d["query"]["pages"][0]
        return {r.get("user", "") for r in p.get("revisions", [])}

    def all_pages(self, namespace: int = 0, prefix: Optional[str] = None) -> Iterator[str]:
        params = dict(action="query", list="allpages", apnamespace=namespace, apfilterredir="nonredirects",
                      aplimit="max")
        if prefix:
            params["apprefix"] = prefix
        while True:
            d = self.get(**params)
            for p in d["query"]["allpages"]:
                yield p["title"]
            if "continue" not in d:
                return
            params.update(d["continue"])

    def category_members(self, category: str, namespace: int = 0) -> Iterator[str]:
        params = dict(action="query", list="categorymembers", cmtitle=category, cmnamespace=namespace,
                      cmlimit="max")
        while True:
            d = self.get(**params)
            for p in d["query"]["categorymembers"]:
                yield p["title"]
            if "continue" not in d:
                return
            params.update(d["continue"])

    # write ------------------------------------------------------------------------
    def edit(self, title: str, text: str, summary: str, createonly=False, nocreate=False,
             basetimestamp: Optional[str] = None) -> dict:
        p = dict(action="edit", title=title, text=text, summary=summary, bot="1", token=self.csrf())
        if createonly:
            p["createonly"] = "1"
        if nocreate:
            p["nocreate"] = "1"
        if basetimestamp:
            p["basetimestamp"] = basetimestamp
        try:
            r = self.post(**p)
        except WikiError as e:
            if e.code and "moderation" in e.code:
                # Extension:Moderation intercepted the edit (bot lacks 'automoderated')
                log.warning("edit to %s was queued by Moderation (%s)", title, e)
                return {"result": "Queued"}
            raise
        time.sleep(self.edit_delay)
        return r["edit"]

    def move(self, src: str, dst: str, reason: str, noredirect=True) -> dict:
        p = dict(action="move", **{"from": src}, to=dst, reason=reason, movetalk="1", token=self.csrf())
        if noredirect:
            p["noredirect"] = "1"
        r = self.post(**p)
        time.sleep(self.edit_delay)
        return r["move"]

    def set_page_language(self, title: str, lang: str, reason: str):
        """Requires $wgPageLanguageUseDB = true and the 'pagelang' right."""
        try:
            return self.post(action="setpagelanguage", title=title, lang=lang, reason=reason, token=self.csrf())
        except WikiError as e:
            log.warning("setpagelanguage failed for %s: %s", title, e)
