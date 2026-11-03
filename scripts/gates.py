#!/usr/bin/env python3
"""CI gates: stdlib-only imports, module size caps (400/module, 1000 total)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "mddex"
MODULE_CAP = 400
TOTAL_CAP = 1000
ALLOWED = set(sys.stdlib_module_names) | {"__future__", "mddex"}


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def main() -> int:
    failures: list[str] = []
    files = sorted(PKG.rglob("*.py"))
    if not files:
        print("FAIL: no package files found")
        return 1
    total = 0
    for f in files:
        n = len(f.read_text().splitlines())
        total += n
        if n > MODULE_CAP:
            failures.append(f"{f.name}: {n} lines > {MODULE_CAP}")
        for imp in sorted(imports_of(f) - ALLOWED):
            failures.append(f"{f.name}: non-stdlib import {imp!r}")
    if total > TOTAL_CAP:
        failures.append(f"package total {total} lines > {TOTAL_CAP}")
    print(f"gates: {total} lines across {len(files)} package files (caps: {MODULE_CAP}/module, {TOTAL_CAP}/total)")
    for m in failures:
        print("FAIL:", m)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
