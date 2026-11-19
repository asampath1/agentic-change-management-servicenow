"""Small dependency-free BM25 index (good enough for ~300 incidents and 20 KB articles).

Swap for a vector store (Azure AI Search, pgvector, ...) behind the same ``search`` call later.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Generic, Sequence, TypeVar

T = TypeVar("T")

_STOP = set("""a an and are as at be by for from has have in is it its of on or that the this to was
were will with after since than then there these they not no our your my me we i you can cannot
auto generated azure monitor action group alert rule resource sev1 sev2 sev3 impacted service""".split())
_TOKEN = re.compile(r"[a-z][a-z0-9]+|[0-9]{3,}[a-z]*")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and not t.isdigit()]


class BM25(Generic[T]):
    def __init__(self, docs: Sequence[T], texts: Sequence[str], k1: float = 1.4, b: float = 0.75):
        self.docs = list(docs)
        self.toks = [tokenize(t) for t in texts]
        self.tf = [Counter(t) for t in self.toks]
        self.avgdl = sum(len(t) for t in self.toks) / max(1, len(self.toks))
        df = Counter(w for t in self.toks for w in set(t))
        n = len(self.toks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}
        self.k1, self.b = k1, b

    def score(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for tf, toks in zip(self.tf, self.toks):
            s, dl = 0.0, len(toks)
            for w in q:
                f = tf.get(w)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out

    def search(self, query: str, k: int = 5) -> list[tuple[T, float]]:
        scores = self.score(query)
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [(self.docs[i], scores[i]) for i in ranked if scores[i] > 0]


def overlap(a: str, b: str) -> float:
    """Token-set overlap coefficient in [0, 1]."""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))
