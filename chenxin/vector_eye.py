# -*- coding: utf-8 -*-
"""语义眼：关键词 + 向量混合召回。

给核心记忆和原话归档建立向量索引，让"字对不上、意思相同"的记忆也能被找到。
召回方式：
    lexical   关键词匹配（子串 / 标签，沿核心 v3.4.2 思路）
    semantic  向量语义（cosine）
    hybrid    两路用 Reciprocal Rank Fusion 融合，分数尺度无关、更稳健
    rerank    有 reranker 时对 top-N 候选精排

没有 embedding 服务时自动用离线确定性向量（同 model_adapter），流程照常。
向量以 JSON 存于 SQLite，不依赖 numpy。
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .model_adapter import ModelClient, cosine, offline_vector

# 真实 bge-m3 维度；离线向量维度
LIVE_DIMS = 1024
OFFLINE_DIMS = 256
# 纯语义扫描的候选上限（避免 25 万全量余弦过慢），先粗排再精排
SCAN_CAP = 2000
# RRF 融合常数
RRF_K = 60


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class VectorStore:
    """向量索引存储。"""

    def __init__(self, db_path=None):
        self.db_path = str(db_path or config.VECTOR_DB)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self):
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vectors (
                id TEXT PRIMARY KEY,
                source TEXT,
                ref_id TEXT,
                text TEXT,
                dims INTEGER,
                vector TEXT,
                updated_at TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vectors_source ON vectors(source)")
        self.conn.commit()

    def upsert(self, items, vectors):
        """批量写入 (id, source, ref_id, text) + 向量。"""
        rows = []
        for item, vector in zip(items, vectors):
            rows.append((
                item["id"], item.get("source", ""), item.get("ref_id", ""),
                item["text"], len(vector), json.dumps(vector), utc_now(),
            ))
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO vectors
                (id, source, ref_id, text, dims, vector, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    def all(self):
        """返回全部索引记录。"""
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM vectors ORDER BY id")]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def delete_source(self, source):
        """删除某来源的索引（便于重建）。"""
        self.conn.execute("DELETE FROM vectors WHERE source=?", (source,))
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()


class VectorEye:
    """混合召回引擎。"""

    def __init__(self, client=None, store=None):
        self.client = client or ModelClient(config.load_models())
        self.store = store or VectorStore()
        self.live = self.client.is_live("embedding")
        self.dims = LIVE_DIMS if self.live else OFFLINE_DIMS

    # ----------------------------- 建索引 -----------------------------
    def embed_texts(self, texts):
        """向量化：真实服务优先，否则离线向量。"""
        if self.live:
            vectors = self.client.embed(texts)
            if vectors is not None:
                return vectors
        return self.client.offline_embed(texts, self.dims)

    def index(self, items):
        """批量建索引，items=[{id, text, source?, ref_id?}]。"""
        vectors = self.embed_texts([item["text"] for item in items])
        self.store.upsert(items, vectors)
        return len(items)

    # ----------------------------- 召回 -----------------------------
    def _query_vector(self, query):
        if self.live:
            vectors = self.client.embed([query])
            if vectors:
                return vectors[0]
        return offline_vector(query, self.dims)

    def semantic(self, query, limit=10, min_cosine=0.0, scan_cap=SCAN_CAP):
        """向量语义召回，返回 [(score, record)]。"""
        qvec = self._query_vector(query)
        records = self.store.all()
        scored = []
        for record in records:
            vector = json.loads(record["vector"])
            score = cosine(qvec, vector)
            if score >= min_cosine:
                scored.append((score, record))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]

    def lexical(self, query, limit=10):
        """关键词召回（子串命中，按命中位置粗排）。"""
        records = self.store.all()
        terms = query.split()
        scored = []
        for record in records:
            text = record["text"]
            score = 0.0
            for term in terms + [query]:
                if term and term in text:
                    score += 1.0 / (1 + text.find(term) * 0.001)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]

    def search(self, query, limit=5, method="hybrid", min_cosine=0.0):
        """统一召回入口，返回记录列表，含 score/method。"""
        if method == "lexical":
            hits = self.lexical(query, limit)
            return [_result(record, score, "lexical")
                    for score, record in hits]
        if method == "semantic":
            hits = self.semantic(query, limit, min_cosine)
            return [_result(record, score, "semantic")
                    for score, record in hits]
        return self._hybrid(query, limit)

    def _hybrid(self, query, limit):
        """lexical + semantic 用 RRF 融合，再可选 rerank。"""
        lexical_hits = self.lexical(query, SCAN_CAP)
        semantic_hits = self.semantic(query, SCAN_CAP)
        rrf = {}
        records = {}
        for rank, (_, record) in enumerate(lexical_hits):
            rrf[record["id"]] = rrf.get(record["id"], 0.0) \
                + 1.0 / (RRF_K + rank + 1)
            records[record["id"]] = record
        for rank, (_, record) in enumerate(semantic_hits):
            rrf[record["id"]] = rrf.get(record["id"], 0.0) \
                + 1.0 / (RRF_K + rank + 1)
            records[record["id"]] = record
        ordered = sorted(rrf.items(), key=lambda x: x[1], reverse=True)
        candidates = [records[item_id] for item_id, _ in ordered]
        return self._maybe_rerank(query, candidates, limit)

    def _maybe_rerank(self, query, candidates, limit):
        """有 reranker 时精排，否则按融合顺序截断。"""
        if self.client.is_live("reranker") and candidates:
            documents = [record["text"] for record in candidates]
            ranked = self.client.rerank(query, documents, top_n=limit)
            if ranked:
                results = []
                for index, score in ranked:
                    results.append(_result(candidates[index], score, "rerank"))
                return results
        return [_result(record, None, "hybrid")
                for record in candidates[:limit]]


def _result(record, score, method):
    """组装召回结果。"""
    return {
        "id": record["id"],
        "ref_id": record.get("ref_id", ""),
        "source": record.get("source", ""),
        "text": record["text"],
        "score": score,
        "method": method,
    }
