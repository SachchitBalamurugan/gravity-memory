"""Optional dense retrieval for historical passages. Raw writes never wait for a model."""
import heapq
import json
import math
import struct
from urllib.error import URLError
from urllib.request import Request, urlopen

from .store import limit_rows, string, utc


class OllamaEmbedder:
    def __init__(self, model):
        self.name = string(model, "model", 200)

    def embed(self, texts):
        request = Request("http://127.0.0.1:11434/api/embed", method="POST",
                          headers={"Content-Type": "application/json"},
                          data=json.dumps({"model": self.name, "input": texts, "truncate": False}).encode())
        try:
            with urlopen(request, timeout=90) as response:
                return json.load(response)["embeddings"]
        except (URLError, TimeoutError, KeyError) as exc:
            raise ValueError(f"local embedding service unavailable for {self.name}") from exc


def unit(vector):
    if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 16384:
        raise ValueError("embedding must have 1 to 16384 dimensions")
    if any(type(x) not in (float, int) or not math.isfinite(x) for x in vector):
        raise ValueError("invalid embedding coordinates")
    norm = math.hypot(*vector)
    if not norm or not math.isfinite(norm):
        raise ValueError("invalid embedding magnitude")
    return tuple(x / norm for x in vector)


def index_passages(ledger, embedder, batch_size=16):
    limit_rows(batch_size)
    model = string(embedder.name, "model", 200)
    completed = 0
    while True:
        with ledger._tx() as db:
            batch = db.execute("""
                SELECT c.id,p.text FROM chunks c JOIN passage_search p ON p.rowid=c.id
                WHERE NOT EXISTS(SELECT 1 FROM vectors v WHERE v.chunk=c.id AND v.model=?)
                ORDER BY c.id LIMIT ?
            """, (model, batch_size)).fetchall()
        if not batch:
            return {"model": model, "new_vectors": completed}
        vectors = embedder.embed([r["text"] for r in batch])
        if not isinstance(vectors, list) or len(vectors) != len(batch):
            raise ValueError("embedding response count differs from batch size")
        with ledger._tx(write=True) as db:
            sample = db.execute("SELECT length(data)/4 FROM vectors WHERE model=? LIMIT 1", (model,)).fetchone()
            dimension = sample[0] if sample else None
            for row, vector in zip(batch, vectors):
                coords = unit(vector)
                if dimension is None:
                    dimension = len(coords)
                if len(coords) != dimension:
                    raise ValueError("embedding dimensions changed for the same model")
                # Recheck the passage in case the operator erased or restored the workspace during inference.
                packed = struct.pack(f"<{dimension}f", *coords)
                completed += db.execute("""
                    INSERT OR IGNORE INTO vectors(chunk,model,data)
                    SELECT rowid,?,? FROM passage_search WHERE rowid=? AND text=?
                """, (model, packed, row["id"], row["text"])).rowcount


def hybrid(ledger, query, limit=8, as_of=None, known_at=None):
    if ledger.embedder is None:
        raise ValueError("configure an embedding model and run index-vectors first")
    string(query, "query", 2048)
    limit_rows(limit)
    at, known = utc(as_of), utc(known_at)
    embedder = ledger.embedder
    vectors = embedder.embed([query])
    if not isinstance(vectors, list) or len(vectors) != 1:
        raise ValueError("embedding service must return one query vector")
    q = unit(vectors[0])
    count = limit * 4
    with ledger._tx() as db:
        from .language import search_terms
        terms = search_terms(query)
        sparse = []
        if terms:
            match = " OR ".join('"' + term + '"' for term in terms)
            sparse = db.execute("""
                SELECT c.id FROM passage_search JOIN chunks c ON c.id=passage_search.rowid
                JOIN documents d ON d.id=c.doc WHERE passage_search MATCH ?
                AND d.effective_at<=? AND d.recorded_at<=? ORDER BY bm25(passage_search),c.id DESC LIMIT ?
            """, (match, at, known, count)).fetchall()
        query_rows = db.execute("""
            SELECT c.id,v.data FROM chunks c JOIN documents d ON d.id=c.doc
            LEFT JOIN vectors v ON v.chunk=c.id AND v.model=?
            WHERE d.effective_at<=? AND d.recorded_at<=?
        """, (embedder.name, at, known))
        total = indexed = 0
        top = []
        for row in query_rows:
            total += 1
            data = row["data"]
            if data is None:
                continue
            indexed += 1
            if len(data) != len(q) * 4:
                raise ValueError("query embedding dimensions differ from index")
            sim = sum(x * y for x, y in zip(q, struct.unpack(f"<{len(q)}f", data)))
            if sim >= 0.3:
                heapq.heappush(top, (sim, row["id"]))
                if len(top) > count:
                    heapq.heappop(top)
        ranks = {}
        for channel, ids in (("lexical", [r[0] for r in sparse]), ("semantic", [i for _, i in sorted(top, reverse=True)])):
            for rank, idx in enumerate(ids, 1):
                ranks.setdefault(idx, {})[channel] = rank
        scores = {idx: sum(1 / (60 + rank) for rank in channels.values()) for idx, channels in ranks.items()}
        hits = []
        for idx in sorted(ranks, key=lambda i: (scores[i], i), reverse=True)[:limit]:
            row = db.execute("""
                SELECT d.source,d.request_id,c.start,c.stop,substr(d.body,c.start+1,c.stop-c.start) AS quote,
                       d.recorded_at,d.effective_at FROM chunks c JOIN documents d ON d.id=c.doc WHERE c.id=?
            """, (idx,)).fetchone()
            hits.append({**dict(row), "ranks": ranks[idx], "fusion_score": scores[idx]})
        return {"view": "hybrid_archive", "model": embedder.name, "indexed_passages": indexed,
                "eligible_passages": total, "results": hits}
