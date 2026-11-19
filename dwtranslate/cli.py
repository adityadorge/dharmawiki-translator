"""Command line: python -m dwtranslate <command> ..."""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys

import yaml

from .mediawiki import MediaWiki
from .pipeline import Pipeline
from .store import Store

ENV_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def load_config(path: str) -> dict:
    raw = open(path, encoding="utf-8").read()
    raw = ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), raw)
    return yaml.safe_load(raw)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dwtranslate", description="Translate Dharmawiki with BharatGen Param")
    ap.add_argument("-c", "--config", default=os.environ.get("DWT_CONFIG", "config.yaml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("translate", help="create/refresh machine-translated drafts")
    t.add_argument("--lang", required=True)
    t.add_argument("--page", action="append", help="page title (repeatable)")
    t.add_argument("--category", help="e.g. 'Category:Ayurveda'")
    t.add_argument("--all", action="store_true", help="all content pages")
    t.add_argument("--limit", type=int)
    t.add_argument("--dry-run", action="store_true", help="write to ./output instead of the wiki")
    t.add_argument("--force", action="store_true", help="re-translate even if up to date / published")

    p = sub.add_parser("publish", help="move drafts marked status=reviewed to their public title")
    p.add_argument("--lang", required=True)
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="show counts per language/state")

    x = sub.add_parser("try", help="translate one string (checks API key + prompt)")
    x.add_argument("--lang", required=True)
    x.add_argument("text")

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(a.config)
    st = cfg.get("state", {})
    store = Store(st.get("path", "state.sqlite"), st.get("s3_uri") or None)
    try:
        if a.cmd == "status":
            for row in store.summary():
                print(*row, sep="\t")
            return 0

        w = cfg["wiki"]
        needs_login = a.cmd == "publish" and not a.dry_run or a.cmd == "translate" and not a.dry_run
        wiki = MediaWiki(w["api_url"], w.get("username") if needs_login else None,
                         w.get("password") if needs_login else None,
                         user_agent=w.get("user_agent", "DharmawikiParamTranslator/1.0"),
                         edit_delay=w.get("edit_delay", 1.0))
        pipe = Pipeline(cfg, wiki, store, a.lang)

        if a.cmd == "try":
            print(pipe.tr.translate(a.text))
        elif a.cmd == "translate":
            titles = pipe.select(a.page, a.category, a.all, a.limit)
            if not titles:
                ap.error("choose --page, --category or --all")
            res = pipe.translate_pages(titles, dry_run=a.dry_run, force=a.force)
            for k, v in res.items():
                print(f"{v}\t{k}")
        elif a.cmd == "publish":
            for k, v in pipe.publish(dry_run=a.dry_run).items():
                print(f"{v}\t{k}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
