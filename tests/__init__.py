"""Make the working tree, not an installed distribution, the code under test.

`from adaptive_model_router import ...` is a query against sys.path, not a reference to
this repository, so a previously installed copy answers it and the suite silently verifies
a build nobody is editing.  Prepending `src/` here fixes that for every runner that imports
this package (`python run_tests.py`, `python -m unittest discover` from the repository root,
pytest); `tests/test_environment.py` fails loudly for the invocations that do not.
"""
from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

for path in (str(SOURCE_ROOT), str(REPOSITORY_ROOT)):
    if path in sys.path:
        sys.path.remove(path)
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(SOURCE_ROOT))

# The router now reads the surface it is running on (CLAUDE_CODE_ENTRYPOINT) and the active
# effort (CLAUDE_EFFORT) from the environment, and Claude Code sets both for everything it
# spawns - including whatever terminal this suite is run from.  Left in place they make the
# results a statement about the host surface: the same test blocks when the suite is run
# from a terminal and passes vacuously when it is run from the desktop app.  Clear them here
# so every test starts from "no surface, no effort" and states its own.
import os  # noqa: E402  - after the path bootstrap on purpose

for name in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_EFFORT"):
    os.environ.pop(name, None)
