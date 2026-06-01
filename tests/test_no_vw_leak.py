"""Regression: no module outside `src.strategies.vertical_wing` may
mention `vertical_wing` in its code. The strategy must only be reached via
the registry. (Tests and docs are exempt.)
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"


def _iter_py_files(*roots: Path):
    for root in roots:
        if not root.exists():
            continue
        yield from root.rglob("*.py")


def test_no_vertical_wing_imports_outside_strategy_folder():
    offenders: list[tuple[str, int, str]] = []
    for path in _iter_py_files(SRC, SCRIPTS):
        rel = path.relative_to(REPO_ROOT).as_posix()
        # the strategy module is allowed to mention itself
        if rel.startswith("src/strategies/vertical_wing/"):
            continue
        # registry references it through config (string), not imports — code
        # never literally imports a vertical_wing module elsewhere.
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "vertical_wing" in line and ("import" in line or "from " in line):
                offenders.append((rel, i, line.strip()))
    assert not offenders, (
        "Strategy-name leakage outside vertical_wing/ folder:\n  "
        + "\n  ".join(f"{p}:{ln}: {s}" for p, ln, s in offenders)
    )
