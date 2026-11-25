"""End-to-end flow against an in-memory fake wiki (no network)."""
import re
from pathlib import Path

import yaml

from dwtranslate.pipeline import Pipeline
from dwtranslate.store import Store

SAMPLE = (Path(__file__).parent / "sample_page.wikitext").read_text(encoding="utf-8")
CFG = yaml.safe_load((Path(__file__).parent.parent / "config.example.yaml").read_text())
CFG["param"].update(api_key="x")


class FakeWiki:
    username = "ParamTranslator"

    def __init__(self):
        self.pages = {}   # title -> list of (revid, user, text)
        self.rev = 100

    def _put(self, t, text, user):
        self.rev += 1
        self.pages.setdefault(t, []).append((self.rev, user, text))
        return self.rev

    def page(self, t):
        if t not in self.pages:
            return None
        rev, _, text = self.pages[t][-1]
        cats = re.findall(r"\[\[(Category:[^\]|]+)", text)
        return dict(title=t, revid=rev, timestamp="2026-01-01T00:00:00Z", content=text,
                    redirect=False, categories=cats)

    def exists(self, ts):
        return {t for t in ts if t in self.pages}

    def editors(self, t):
        return {u for _, u, _ in self.pages[t]}

    def all_pages(self, ns=0, prefix=None):
        pre = {0: "", 3000: "Translation:"}[ns]
        return [t for t in self.pages if (t.startswith("Translation:") == (ns == 3000))]

    def edit(self, t, text, summary, **kw):
        return {"result": "Success", "newrevid": self._put(t, text, self.username)}

    def move(self, a, b, reason, **kw):
        self.pages[b] = self.pages.pop(a)
        return {}

    def set_page_language(self, *a):
        pass


def upper(s):
    return re.sub(r"(<[^>]+>)|([^<]+)", lambda m: m.group(1) or m.group(2).upper(), s)


def make(tmp_path):
    wiki = FakeWiki()
    wiki._put("Ayu", SAMPLE, "Editor1")
    wiki._put("Ayurveda", "Ayurveda is the science of life.", "Editor1")
    pipe = Pipeline(CFG, wiki, Store(str(tmp_path / "s.sqlite")), "hi")
    pipe.tr.translate = upper
    return wiki, pipe


def test_full_flow(tmp_path):
    wiki, pipe = make(tmp_path)
    assert pipe.translate_page("Ayu") == "draft-saved"
    draft = wiki.page("Translation:Ayu/hi")["content"]
    assert draft.startswith("{{Machine translation|source=Ayu|lang=hi|revid=101|title=AYU|status=needs-review}}")
    assert "[[Category:Ayurveda]]" in draft
    assert pipe.translate_page("Ayu") == "up-to-date"

    # nothing is public until an editor reviews
    assert pipe.publish() == {}

    # editor fixes text with the normal editor and flips status
    wiki._put("Translation:Ayu/hi", draft.replace("needs-review", "reviewed"), "Editor2")
    wiki._put("Ayurveda/hi", "{{Translated page|source=Ayurveda|lang=hi|revid=102}}", "Editor2")
    res = pipe.publish()
    assert res == {"Translation:Ayu/hi": "published -> Ayu/hi"}
    pub = wiki.page("Ayu/hi")["content"]
    assert pub.startswith("{{Translated page|source=Ayu|lang=hi|revid=101|title=AYU}}")
    assert "[[Ayurveda/hi|AYURVEDA]]" in pub            # links now point to Hindi pages
    assert "Translation:Ayu/hi" not in wiki.pages

    # source changes -> published page flagged, never overwritten
    wiki._put("Ayu", SAMPLE + "\nNew paragraph.", "Editor1")
    assert pipe.translate_page("Ayu") == "published-outdated"
    assert "outdated=" in wiki.page("Ayu/hi")["content"]


def test_human_edited_draft_not_overwritten(tmp_path):
    wiki, pipe = make(tmp_path)
    pipe.translate_page("Ayu")
    wiki._put("Translation:Ayu/hi", wiki.page("Translation:Ayu/hi")["content"] + "\nfix", "Editor2")
    wiki._put("Ayu", SAMPLE + "\nMore.", "Editor1")
    assert pipe.translate_page("Ayu") == "draft-outdated"
    assert wiki.page("Translation:Ayu/hi")["content"].endswith("fix")


def test_bot_only_draft_refreshed(tmp_path):
    wiki, pipe = make(tmp_path)
    pipe.translate_page("Ayu")
    wiki._put("Ayu", SAMPLE + "\nMore text here.", "Editor1")
    assert pipe.translate_page("Ayu") == "draft-saved"
    assert "MORE TEXT HERE." in wiki.page("Translation:Ayu/hi")["content"]


def test_dry_run_writes_file(tmp_path):
    wiki, pipe = make(tmp_path)
    r = pipe.translate_page("Ayu", dry_run=True, out_dir=str(tmp_path / "out"))
    assert r.startswith("dry-run") and "Translation:Ayu/hi" not in wiki.pages
    assert (tmp_path / "out/hi/Ayu.wikitext").exists()


def test_clean_strips_think_tags():
    from dwtranslate.llm import ParamTranslator
    assert ParamTranslator._clean("<think> </think> आयु संस्कृत शब्द है।", "Ayu") == "आयु संस्कृत शब्द है।"
    assert ParamTranslator._clean("<think>\nreasoning\n</think>\nTranslation: नमस्ते", "Hello") == "नमस्ते"
