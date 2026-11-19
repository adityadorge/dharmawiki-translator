"""SQLite state: translation memory + per-page tracking. Optionally synced to S3."""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tm (
  h TEXT PRIMARY KEY, lang TEXT, model TEXT, src TEXT, tgt TEXT, created REAL);
CREATE TABLE IF NOT EXISTS pages (
  title TEXT, lang TEXT, source_revid INTEGER, target_title TEXT, target_revid INTEGER,
  state TEXT, stats TEXT, updated REAL, PRIMARY KEY (title, lang));
"""


class Store:
    def __init__(self, path: str, s3_uri: Optional[str] = None):
        self.path, self.s3_uri = path, s3_uri
        if s3_uri:
            self._s3("download")
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    # translation memory -----------------------------------------------------
    @staticmethod
    def _h(src: str, lang: str, model: str) -> str:
        return hashlib.sha256(f"{lang}\x00{model}\x00{src}".encode()).hexdigest()

    def tm_get(self, src, lang, model):
        with self.lock:
            row = self.db.execute("SELECT tgt FROM tm WHERE h=?", (self._h(src, lang, model),)).fetchone()
        return row[0] if row else None

    def tm_put(self, src, lang, model, tgt):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO tm VALUES (?,?,?,?,?,?)",
                            (self._h(src, lang, model), lang, model, src, tgt, time.time()))
            self.db.commit()

    # pages --------------------------------------------------------------------
    def page_get(self, title, lang) -> Optional[dict]:
        with self.lock:
            cur = self.db.execute("SELECT * FROM pages WHERE title=? AND lang=?", (title, lang))
            row = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], row)) if row else None

    def page_put(self, title, lang, **kw):
        cur = self.page_get(title, lang) or {"title": title, "lang": lang}
        cur.update(kw, updated=time.time())
        cols = ["title", "lang", "source_revid", "target_title", "target_revid", "state", "stats", "updated"]
        with self.lock:
            self.db.execute(f"INSERT OR REPLACE INTO pages ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                            [cur.get(c) for c in cols])
            self.db.commit()

    def summary(self, lang=None):
        q = "SELECT lang, state, COUNT(*) FROM pages " + ("WHERE lang=? " if lang else "") + "GROUP BY lang, state"
        with self.lock:
            return self.db.execute(q, (lang,) if lang else ()).fetchall()

    # S3 sync ----------------------------------------------------------------
    def close(self):
        self.db.close()
        if self.s3_uri:
            self._s3("upload")

    def _s3(self, direction: str):
        import boto3
        bucket, _, key = self.s3_uri.removeprefix("s3://").partition("/")
        s3 = boto3.client("s3")
        if direction == "download":
            try:
                s3.download_file(bucket, key, self.path)
                log.info("state downloaded from %s", self.s3_uri)
            except Exception as e:  # first run: nothing there yet
                log.info("no state in S3 yet (%s)", e)
        elif os.path.exists(self.path):
            s3.upload_file(self.path, bucket, key)
            log.info("state uploaded to %s", self.s3_uri)
