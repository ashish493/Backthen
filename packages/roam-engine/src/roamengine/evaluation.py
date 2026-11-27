"""Retrieval evaluation: a local store of labelled queries and Recall@K / Precision@K / nDCG@K.

Labels live in `<library>/labels.sqlite` (never inside the manifest). A judgment is (query, asset, grade) with
grade 0 = not relevant, 1 = relevant, 2 = highly relevant. Judgments are made on a pooled result list (the top results
shown in the labelling tool), so unjudged results count as not relevant: Precision@K is a lower bound, and Recall@K
is measured against the relevant items found so far (standard pooling caveat; widen the pool to tighten it).
"""
import datetime as dt
import math
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries(
  query_id   INTEGER PRIMARY KEY,
  text       TEXT NOT NULL UNIQUE,
  filters    TEXT,
  created_at TEXT NOT NULL,
  note       TEXT
);
CREATE TABLE IF NOT EXISTS judgments(
  query_id   INTEGER NOT NULL REFERENCES queries(query_id) ON DELETE CASCADE,
  asset_id   TEXT NOT NULL,
  grade      INTEGER NOT NULL CHECK (grade IN (0, 1, 2)),
  judged_at  TEXT NOT NULL,
  PRIMARY KEY(query_id, asset_id)
);
"""


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Labels:
    """Thread-safe: the local API serves each request on its own thread, and one sqlite3 connection shared between
    threads without a lock loses writes and returns wrong reads (BOTTLENECKS.md #27). Every call holds the lock."""

    def __init__(self, library):
        self.path = os.path.join(library, "labels.sqlite")
        self._lock = threading.Lock()
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(SCHEMA)

    def add_query(self, text, note=None):
        text = text.strip()
        with self._lock, self.con:                        # commits, or rolls back on error
            self.con.execute("INSERT OR IGNORE INTO queries(text, created_at, note) VALUES (?, ?, ?)",
                             (text, _now(), note))
            return self.con.execute("SELECT query_id FROM queries WHERE text=?", (text,)).fetchone()[0]

    def delete_query(self, query_id):
        with self._lock, self.con:
            self.con.execute("DELETE FROM queries WHERE query_id=?", (query_id,))

    def judge(self, query_id, asset_id, grade):
        with self._lock, self.con:
            if grade is None:
                self.con.execute("DELETE FROM judgments WHERE query_id=? AND asset_id=?", (query_id, asset_id))
            else:
                self.con.execute("INSERT OR REPLACE INTO judgments VALUES (?, ?, ?, ?)",
                                 (query_id, asset_id, int(grade), _now()))

    def queries(self):
        with self._lock:
            rows = self.con.execute(
                "SELECT q.query_id, q.text, q.note, COUNT(j.asset_id), SUM(j.grade > 0) FROM queries q "
                "LEFT JOIN judgments j ON j.query_id = q.query_id GROUP BY q.query_id ORDER BY q.query_id").fetchall()
        return [dict(query_id=a, text=b, note=c, judged=d, relevant=e or 0) for a, b, c, d, e in rows]

    def judgments(self, query_id):
        with self._lock:
            return dict(self.con.execute("SELECT asset_id, grade FROM judgments WHERE query_id=?", (query_id,)))

    def close(self):
        with self._lock:
            self.con.close()


def metrics_for(ranked, grades, ks=(1, 5, 10, 20)):
    """ranked: list of asset_ids in rank order; grades: {asset_id: grade}. -> {recall@k, precision@k, ndcg@k, rr}.
    recall@k is capped recall, hits / min(k, R): plain hits / R can't exceed k / R when a query has R > k relevant."""
    rel = {a for a, g in grades.items() if g > 0}
    out = {}
    for k in ks:
        top = ranked[:k]
        hits = sum(a in rel for a in top)
        out[f"recall@{k}"] = hits / min(k, len(rel)) if rel else None    # capped: a perfect ranking scores 1.0
        out[f"precision@{k}"] = hits / k
        dcg = sum((2 ** grades.get(a, 0) - 1) / math.log2(r + 2) for r, a in enumerate(top))
        ideal = sorted((g for g in grades.values() if g > 0), reverse=True)[:k]
        idcg = sum((2 ** g - 1) / math.log2(r + 2) for r, g in enumerate(ideal))
        out[f"ndcg@{k}"] = dcg / idcg if idcg else None
    first = next((r for r, a in enumerate(ranked) if a in rel), None)
    out["rr"] = 1 / (first + 1) if first is not None else 0.0
    return out


def evaluate(engine, labels, ks=(1, 5, 10, 20), depth=100):
    """Run every labelled query through the engine; macro-average the metrics over queries with >= 1 relevant item.
    Also reports the expected capped Recall@K of a random ranking as a floor: per query (K·R/N) / min(K, R)
    = max(K, R) / N, averaged over queries."""
    per = []
    n_assets = len(engine.cat.asset_ids)
    for q in labels.queries():
        g = labels.judgments(q["query_id"])
        if not any(v > 0 for v in g.values()):
            continue
        res = engine.search(q["text"], k=depth)
        ranked = [it["asset_id"] for it in res["items"]]
        per.append(dict(query_id=q["query_id"], relevant=sum(v > 0 for v in g.values()),
                        **metrics_for(ranked, g, ks)))
    keys = [k for k in (per[0] if per else {}) if "@" in k or k == "rr"]
    mean = {k: round(sum(p[k] for p in per if p[k] is not None) / max(1, sum(p[k] is not None for p in per)), 4)
            for k in keys}
    mean["mrr"] = mean.pop("rr", None)
    return dict(model_id=engine.cat.model_id, queries=len(per), assets=n_assets, mean=mean,
                random_recall={f"@{k}": round(sum(max(k, p["relevant"]) for p in per) / len(per) / n_assets, 6)
                               if n_assets and per else None for k in ks}, per_query=per)
