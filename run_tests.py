"""Run the test suite against this working tree.

Use this instead of a bare `python -m unittest discover -s tests`: that form leaves the
import of `adaptive_model_router` to sys.path, so an installed copy of the package answers
it and the suite reports on a build that is not the one in this repository.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    verbosity = 2 if "-v" in (argv if argv is not None else sys.argv[1:]) else 1
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(REPOSITORY_ROOT / "tests"), top_level_dir=str(REPOSITORY_ROOT),
    )
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
