"""Engine + sqlite behavior: ranking sanity, derivation, multi-root, replace
semantics, forget, dot-dir skipping, read-only tree, perf."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from docdex import dex


# ------------------------------------------------------------------- BM25 unit


def _corpus():
    return [
        ("a", "/x/a.md", "git commit message conventions", "/x"),
        ("b", "/x/b.md", "docker compose service healthchecks", "/x"),
        ("c", "/y/c.md", "write release notes from commits", "/y"),
    ]


def test_ranking_finds_the_right_doc():
    hits = dex.BM25(_corpus()).search("git commit message", k=3)
    assert hits[0].id == "a"
    assert all(h.score > 0 for h in hits)


def test_cross_root_corpus_and_root_field():
    hits = dex.BM25(_corpus()).search("release notes", k=3)
    assert hits[0].id == "c"
    assert hits[0].root == "/y"


def test_stopword_only_query_returns_empty():
    assert dex.BM25(_corpus()).search("the of and how", k=3) == []


def test_out_of_vocab_query_returns_empty():
    assert dex.BM25(_corpus()).search("kubernetes helm chart", k=3) == []


def test_determinism_and_tie_break():
    eng = dex.BM25([("b", "/b", "same words here", "/r"), ("a", "/a", "same words here", "/r")])
    hits1 = eng.search("same words", k=2)
    hits2 = dex.BM25([("b", "/b", "same words here", "/r"), ("a", "/a", "same words here", "/r")]).search("same words", k=2)
    assert [h.id for h in hits1] == ["a", "b"]  # tie broken by id
    assert hits1 == hits2  # stable across instances


def test_empty_corpus():
    assert dex.BM25([]).search("anything", k=3) == []


# ------------------------------------------------------------------ derivation


def test_derive_description_two_headings_capped():
    text = "# Install Guide\n\nbody\n## Configuration Options Galore\nmore\n## Third"
    assert dex._derive_description(text) == "Install Guide - Configuration Options Galore"


def test_derive_description_no_headings():
    assert dex._derive_description("just prose, no headings") == ""


def test_dot_directories_skipped(tmp_path):
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "noise.md").write_text("# Noise\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x.md").write_text("# Git\n")
    (tmp_path / "real.md").write_text("# Real Doc\n")
    files = list(dex._md_files(tmp_path))
    assert [rel.as_posix() for _md, rel in files] == ["real.md"]


# ----------------------------------------------------------- index/ask/forget


@pytest.fixture
def tree_a(tmp_path):
    a = tmp_path / "projA"
    (a / "docs").mkdir(parents=True)
    (a / "docs" / "bench.md").write_text("# Token Benchmarks\n\n## Method\n")
    (a / "docs" / "conf.md").write_text("# Configuration Reference\n")
    return a


@pytest.fixture
def tree_b(tmp_path):
    b = tmp_path / "projB"
    b.mkdir()
    (b / "release.md").write_text("# Release Process\n\n## Signing\n")
    return b


def test_index_ask_roundtrip_multi_root(tree_a, tree_b, tmp_path):
    db = tmp_path / "state" / "docs.db"
    assert dex.index_root(tree_a, db) == 2
    assert dex.index_root(tree_b, db) == 1

    hits = dex.ask("token benchmarks method", db, k=3)
    assert hits and hits[0].path.endswith("bench.md")

    # scoped: only projB answers, even though projA is indexed too
    scoped = dex.ask("release signing", db, k=3, root=tree_b)
    assert scoped and scoped[0].path.endswith("release.md")
    wrong_scope = dex.ask("release signing", db, k=3, root=tree_a)
    assert wrong_scope == []


def test_reindex_replaces_only_that_root(tree_a, tree_b, tmp_path):
    db = tmp_path / "docs.db"
    dex.index_root(tree_a, db)
    dex.index_root(tree_b, db)
    (tree_a / "docs" / "new.md").write_text("# Brand New Page\n")
    (tree_a / "docs" / "conf.md").unlink()  # gone from disk -> gone from index
    assert dex.index_root(tree_a, db) == 2  # bench + new; no dupes, conf dropped
    assert len(dex.list_docs(db, root=tree_b)) == 1  # projB untouched
    all_docs = dex.list_docs(db)
    assert len(all_docs) == 3


def test_forget_removes_root_only(tree_a, tree_b, tmp_path):
    db = tmp_path / "docs.db"
    dex.index_root(tree_a, db)
    dex.index_root(tree_b, db)
    assert dex.forget(tree_a, db) == 2
    assert dex.forget(tree_a, db) == 0  # already gone
    assert len(dex.list_docs(db)) == 1


def test_roots_listing(tree_a, tree_b, tmp_path):
    db = tmp_path / "docs.db"
    dex.index_root(tree_a, db)
    dex.index_root(tree_b, db)
    rows = dex.roots(db)
    assert {(r, c) for r, _t, c in rows} == {
        (str(tree_a.resolve()), 2),
        (str(tree_b.resolve()), 1),
    }


def test_index_missing_dir_raises(tmp_path):
    with pytest.raises(ValueError):
        dex.index_root(tmp_path / "nope", tmp_path / "docs.db")


def test_ask_missing_db_raises(tmp_path):
    with pytest.raises(Exception):
        dex.ask("x", tmp_path / "nothing.db")


def test_index_never_touches_the_tree(tree_a, tmp_path):
    def tree_hash(root: Path) -> dict[str, str]:
        return {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()
        }

    before = tree_hash(tree_a)
    dex.index_root(tree_a, tmp_path / "elsewhere" / "docs.db")
    assert tree_hash(tree_a) == before


def test_body_terms_findable_when_headings_lack_them(tmp_path):
    """The founding failure: a term living only in a table body must still
    be findable — heading-only indexing ranks the wrong file first there."""
    tree = tmp_path / "t"
    tree.mkdir()
    (tree / "readme.md").write_text(
        "# Some Project\n\n| Setting | Default | Use |\n|---|---|---|\n"
        "| `HOSTS_LIST` | -- | Comma-separated list for model discovery |\n"
    )
    (tree / "threat.md").write_text("# Threat Model\n\nattackers and hardening\n")
    db = tmp_path / "docs.db"
    dex.index_root(tree, db)
    hits = dex.ask("model discovery", db, k=2)
    assert hits and hits[0].path.endswith("readme.md")


def test_db_stores_no_prose(tmp_path):
    """vocab is a sorted word list — body sentences must not be recoverable."""
    tree = tmp_path / "t"
    tree.mkdir()
    sentence = "the quick brown fox jumps over the lazy dog repeatedly"
    (tree / "s.md").write_text(f"# Story\n\n{sentence} and then some unique tail words\n")
    db = tmp_path / "docs.db"
    dex.index_root(tree, db)
    raw = db.read_bytes()
    assert sentence.encode() not in raw
    assert b"quick brown" not in raw  # not even adjacent words survive


def test_perf_500_docs(tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(500):
        (big / f"doc_{i:03d}.md").write_text(f"# Document {i}\n\n## Topic {i % 20}\n")
    db = tmp_path / "docs.db"
    dex.index_root(big, db)
    t0 = time.perf_counter()
    hits = dex.ask("topic 7 document", db, k=5)
    ms = (time.perf_counter() - t0) * 1000
    assert hits and ms < 50, f"{ms:.1f}ms"
    print(f"\nask over 500 docs: {ms:.2f} ms")
