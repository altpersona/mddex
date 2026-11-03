"""mddex CLI: index a tree, ask which file to read.

Exit codes: 0 ok, 1 error (unknown root/db, bad dir), 2 usage.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from . import __version__
from . import dex


def default_db() -> Path:
    import os

    base = os.environ.get("MDDEX_HOME") or os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return (Path(base).expanduser() / "mddex" / "docs.db")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mddex", description="Which file should you read?")
    p.add_argument("--version", action="version", version=f"mddex {__version__}")
    p.add_argument("--db", type=Path, default=None, help="index path (default ~/.cache/mddex/docs.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("index", help="index (or re-index) one markdown tree")
    i.add_argument("dir", type=Path)

    a = sub.add_parser("ask", help="which files answer this question?")
    a.add_argument("question")
    a.add_argument("-k", "--top", type=int, default=5)
    a.add_argument("--root", type=Path, default=None, help="search only this indexed root")

    sub.add_parser("roots", help="indexed roots with doc counts")

    l = sub.add_parser("list", help="indexed docs (path + derived description)")
    l.add_argument("--root", type=Path, default=None)

    f = sub.add_parser("forget", help="drop an indexed root")
    f.add_argument("dir", type=Path)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = args.db.expanduser() if args.db else default_db()

    if args.cmd == "index":
        try:
            n = dex.index_root(args.dir, db)
        except ValueError as exc:
            print(f"index: {exc}", file=sys.stderr)
            return 1
        print(f"indexed {n} docs from {args.dir.expanduser().resolve()} -> {db}")
        return 0

    if args.cmd in ("ask", "list", "forget"):
        known = ""
        try:
            if args.cmd == "ask":
                hits = dex.ask(args.question, db, k=args.top, root=args.root)
            elif args.cmd == "list":
                for path, desc in dex.list_docs(db, root=args.root):
                    print(f"{path}  # {desc}" if desc else path)
                return 0
            else:
                n = dex.forget(args.dir, db)
                print(f"forgot {args.dir.expanduser().resolve()} ({n} docs removed)")
                return 0
        except sqlite3.OperationalError:
            known = "".join(f"\n  {r} ({c} docs, {t})" for r, t, c in dex.roots(db)) if db.exists() else ""
            target = f" root {args.root.expanduser().resolve()!r}" if getattr(args, "root", None) else ""
            print(f"no mddex index at {db}{target}; run `mddex index <dir>` first"
                  f"{known}", file=sys.stderr)
            return 1
        if args.root is not None and not hits:
            # scoped ask with zero hits: distinguish "nothing matched" from
            # "root not indexed" — the honest separation
            if not any(r == str(args.root.expanduser().resolve()) for r, _t, _c in dex.roots(db)):
                print(f"root {args.root.expanduser().resolve()} is not indexed"
                      f" (see `mddex roots`)", file=sys.stderr)
                return 1
        for hit in hits:
            print(f"{hit.score:8.3f}  {hit.path}")
        return 0

    if args.cmd == "roots":
        try:
            rows = dex.roots(db)
        except sqlite3.OperationalError:
            print(f"no mddex index at {db}; run `mddex index <dir>` first", file=sys.stderr)
            return 1
        for root_id, indexed_at, count in rows:
            print(f"{count:6d}  {root_id}  (indexed {indexed_at})")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
