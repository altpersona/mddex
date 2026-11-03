"""The dex: Okapi BM25 over heading-derived doc descriptions, in sqlite.

Provenance: the docs-routing concept is inspired by mcptoon (Apache-2.0);
mddex is a clean-room implementation that shares no code with it. The
ranking algorithm is Okapi BM25, a long-public textbook formula.

Multi-root: many trees can be indexed at once; each root is replaced
independently on re-index, `forget` drops one root. Dot-directories (.git,
.venv, .pytest_cache, ...) are skipped during indexing — a hidden dir is
machinery, not documentation.

Documented limits (deliberate — a router, not a markdown engine):
  - Descriptions derive from the first 1-2 ATX headings (# .. ######), joined
    " - ", capped 120 chars. Setext (===/---) headings are not recognized.
  - Searchable text = description + filename stem + the body's vocabulary:
    every distinct body term (post-tokenizer, stopwords gone), stored as a
    sorted word list — findable, but no readable prose is kept in the db.
  - Tokenizer: lowercase [a-z0-9]+, small English stopword list, no stemming
    ("message" does not match "messages"). Each distinct query term counts
    once per document. Docs sharing no query term are omitted.
  - The corpus is rebuilt in memory per ask — comfortably fast at thousands
    of docs; not designed for a hundred thousand.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Hit", "BM25", "tokenize", "index_root", "ask", "list_docs", "roots", "forget"]


@dataclass
class Hit:
    """One search result. path is absolute."""

    id: str      # path relative to its root, forward slashes
    path: str    # resolved absolute path
    score: float
    root: str    # absolute root path the doc was indexed under


_STOPWORDS = frozenset(
    """the a an of to in is it for and or how what on at by as be are was were
    this that with from you your i we they do does did not no but if then
    than so""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")

_DESC_JOIN = " - "
_DESC_CAP = 120


def tokenize(text: str) -> list[str]:
    """Lowercase, keep [a-z0-9]+ runs, drop stopwords. Order preserved."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class BM25:
    """Okapi BM25 (k1=1.2, b=0.75) over an in-memory corpus.

    Corpus items are (id, path, text, root). search() returns Hits sorted by
    score descending, ties broken by id ascending — deterministic everywhere.
    """

    def __init__(
        self, corpus: list[tuple[str, str, str, str]], k1: float = 1.2, b: float = 0.75
    ) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[tuple[str, str, Counter, int, str]] = []
        self._df: dict[str, int] = {}
        for doc_id, path, text, root in corpus:
            tf = Counter(tokenize(text))
            self._docs.append((doc_id, path, tf, sum(tf.values()), root))
            for term in tf:
                self._df[term] = self._df.get(term, 0) + 1
        self._n = len(self._docs)
        self._avgdl = (sum(d[3] for d in self._docs) / self._n) if self._n else 0.0

    def _idf(self, term: str) -> float:
        df = self._df[term]
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int = 5) -> list[Hit]:
        terms = sorted({t for t in tokenize(query) if t in self._df})
        if not terms or not self._n:
            return []
        scored: list[tuple[float, str, str, str]] = []
        for doc_id, path, tf, dl, root in self._docs:
            score = 0.0
            for term in terms:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                norm = self.k1 * (1.0 - self.b + self.b * dl / self._avgdl)
                score += self._idf(term) * freq * (self.k1 + 1.0) / (freq + norm)
            if score > 0.0:
                scored.append((score, doc_id, path, root))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [Hit(id=i, path=p, score=s, root=r) for s, i, p, r in scored[:k]]


# ----------------------------------------------------------------- sqlite side


def _connect_ro(db: Path) -> sqlite3.Connection:
    """Read-only connection; sqlite3.OperationalError if db/table missing."""
    return sqlite3.connect(db.absolute().as_uri() + "?mode=ro", uri=True)


def _body_vocab(text: str) -> str:
    """The body's distinct terms as a sorted space-joined list.

    Headings are indexed via the derived description; this adds the body's
    vocabulary so a word living only in prose is still findable. Sorted and
    deduplicated, it is a word list, not stored prose.
    """
    return " ".join(sorted(set(tokenize(text))))


def _connect_rw(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS docs"
        " (root_id TEXT, id TEXT, path TEXT, description TEXT, vocab TEXT,"
        " PRIMARY KEY (root_id, id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS roots"
        " (root_id TEXT PRIMARY KEY, indexed_at TEXT)"
    )
    return conn


def _derive_description(text: str, max_headings: int = 2) -> str:
    """First 1-2 ATX headings joined, capped ~120 chars."""
    parts: list[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            parts.append(m.group(1).strip())
            if len(parts) >= max_headings:
                break
    return _DESC_JOIN.join(parts)[:_DESC_CAP]


def _md_files(root: Path):
    """*.md under root, recursive, skipping dot-directories."""
    for md in sorted(root.rglob("*.md")):
        if not md.is_file():
            continue
        rel = md.relative_to(root)
        if any(part.startswith(".") for part in rel.parts[:-1]):
            continue  # inside a hidden directory — machinery, not docs
        yield md, rel


def index_root(root: Path, db: Path) -> int:
    """Index one tree (replace-all for this root only). Returns its doc count."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_connect_rw(db)) as conn:
        conn.execute("DELETE FROM docs WHERE root_id = ?", (str(root),))
        conn.execute("INSERT OR REPLACE INTO roots VALUES (?, ?)", (str(root), now))
        for md, rel in _md_files(root):
            text = md.read_text(encoding="utf-8", errors="replace")
            conn.execute(
                "INSERT OR REPLACE INTO docs VALUES (?, ?, ?, ?, ?)",
                (str(root), rel.as_posix(), str(md),
                 _derive_description(text), _body_vocab(text)),
            )
        conn.commit()
        return conn.execute(
            "SELECT COUNT(*) FROM docs WHERE root_id = ?", (str(root),)
        ).fetchone()[0]


def forget(root: Path, db: Path) -> int:
    """Drop one root and its docs. Returns rows removed; 0 = unknown root."""
    root = root.expanduser().resolve()
    with closing(_connect_rw(db)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM docs WHERE root_id = ?", (str(root),)).fetchone()[0]
        conn.execute("DELETE FROM docs WHERE root_id = ?", (str(root),))
        conn.execute("DELETE FROM roots WHERE root_id = ?", (str(root),))
        conn.commit()
    return n


def roots(db: Path) -> list[tuple[str, str, int]]:
    """Indexed roots as (root, indexed_at, doc_count)."""
    with closing(_connect_ro(db)) as conn:
        return conn.execute(
            "SELECT r.root_id, r.indexed_at, COUNT(d.id)"
            " FROM roots r LEFT JOIN docs d ON d.root_id = r.root_id"
            " GROUP BY r.root_id ORDER BY r.root_id"
        ).fetchall()


def ask(query: str, db: Path, k: int = 5, root: Path | None = None) -> list[Hit]:
    """BM25 across every indexed root (or one), over description + stem + body vocab."""
    with closing(_connect_ro(db)) as conn:
        if root is None:
            rows = conn.execute(
                "SELECT root_id, id, path, description, vocab FROM docs"
            ).fetchall()
        else:
            rid = str(root.expanduser().resolve())
            rows = conn.execute(
                "SELECT root_id, id, path, description, vocab FROM docs WHERE root_id = ?",
                (rid,),
            ).fetchall()
    corpus = [
        (i, p, f"{desc} {Path(p).stem} {vocab}", rid)
        for rid, i, p, desc, vocab in rows
    ]
    return BM25(corpus).search(query, k)


def list_docs(db: Path, root: Path | None = None) -> list[tuple[str, str]]:
    """(absolute path, description) for all docs, or one root's."""
    with closing(_connect_ro(db)) as conn:
        if root is None:
            return conn.execute("SELECT path, description FROM docs ORDER BY path").fetchall()
        return conn.execute(
            "SELECT path, description FROM docs WHERE root_id = ? ORDER BY path",
            (str(root.expanduser().resolve()),),
        ).fetchall()
