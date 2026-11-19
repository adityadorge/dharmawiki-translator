"""Client for the BharatGen Param translation model (OpenAI-compatible API)."""
from __future__ import annotations

import logging
import re
import time

import requests

from .store import Store

log = logging.getLogger(__name__)

TAG_HINT = (
    "Keep every tag like <x id=\"1\"/> and <g id=\"2\">...</g> exactly as-is, in the "
    "right position; translate only the text (including text inside <g> tags). "
    "Keep Sanskrit terms in Devanagari. Output only the translation."
)


class ParamTranslator:
    def __init__(self, cfg: dict, store: Store, lang_code: str, lang_name: str):
        self.url = cfg["base_url"].rstrip("/") + "/chat/completions"
        self.key = cfg["api_key"]
        self.model = cfg.get("model", "param-translate")
        self.prompt = cfg.get("prompt", "Translate to {lang}: {text}")
        self.tag_prompt = cfg.get("tag_prompt", "Translate to {lang}. " + TAG_HINT + "\n\n{text}")
        self.timeout = cfg.get("timeout", 120)
        self.retries = cfg.get("retries", 4)
        self.max_chars = cfg.get("max_chars", 1800)
        self.temperature = cfg.get("temperature", 0.1)
        self.store = store
        self.lang_code, self.lang_name = lang_code, lang_name
        self.session = requests.Session()
        self.calls = 0

    # public -----------------------------------------------------------------
    def translate(self, text: str) -> str:
        if len(text) > self.max_chars:
            return " ".join(self.translate(c) for c in self._chunks(text))
        cached = self.store.tm_get(text, self.lang_code, self.model)
        if cached is not None:
            return cached
        has_tags = "<x id=" in text or "<g id=" in text
        prompt = (self.tag_prompt if has_tags else self.prompt).format(lang=self.lang_name, text=text)
        out = self._clean(self._call(prompt), text)
        self.store.tm_put(text, self.lang_code, self.model, out)
        return out

    # internals --------------------------------------------------------------
    def _call(self, prompt: str) -> str:
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        delay = 2.0
        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.post(self.url, json=body, headers=headers, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                self.calls += 1
                return r.json()["choices"][0]["message"]["content"]
            except (requests.RequestException, KeyError, ValueError) as e:
                if attempt == self.retries:
                    raise
                log.warning("Param API error (attempt %d): %s; retrying in %.0fs", attempt, e, delay)
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

    @staticmethod
    def _clean(out: str, src: str) -> str:
        out = out.strip()
        out = re.sub(r"^(translation|अनुवाद)\s*[:：]\s*", "", out, flags=re.I)
        if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'“”" and src[:1] not in "\"'“”":
            out = out[1:-1].strip()
        # model should not add line breaks inside a wikitext paragraph
        return re.sub(r"\s*\n+\s*", " ", out)

    def _chunks(self, text: str) -> list[str]:
        """Split long paragraphs at sentence ends, never inside a <g> pair."""
        sentences = re.split(r"(?<=[.!?।])\s+", text)
        chunks, cur = [], ""
        for s in sentences:
            cand = (cur + " " + s).strip()
            if cur and len(cand) > self.max_chars and cur.count("<g id=") == cur.count("</g>"):
                chunks.append(cur)
                cur = s
            else:
                cur = cand
        if cur:
            chunks.append(cur)
        return chunks
