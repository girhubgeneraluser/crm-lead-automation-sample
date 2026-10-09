import copy
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from leadflow import pipeline, store  # noqa: E402

BASE_CONFIG = pipeline.load_config()


def config(**overrides):
    cfg = copy.deepcopy(BASE_CONFIG)
    for path, value in overrides.items():
        node = cfg
        parts = path.split("__")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return cfg


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def raw_lead(**kw):
    lead = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada.lovelace@analytical-engines.co.uk",
            "phone": "07700 900123", "company": "Analytical Engines Ltd", "job_title": "Head of Research",
            "country": "UK", "company_size": 120, "form": "contact", "submitted_at": "2026-09-01T10:00:00Z"}
    lead.update(kw)
    return lead


class DBTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="leadflow-test-")
        self.db_path = os.path.join(self.tmp, "test.sqlite3")
        self.conn = store.connect(self.db_path)
        self.cfg = config()
        self._seq = 0

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self, raw, source="website", key=None, cfg=None):
        self._seq += 1
        return pipeline.ingest(self.conn, raw, source, key or "test:%d" % self._seq, cfg or self.cfg)

    def count(self, sql, *params):
        return self.conn.execute(sql, params).fetchone()[0]
