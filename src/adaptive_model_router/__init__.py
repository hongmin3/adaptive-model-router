"""Adaptive model and reasoning router."""

from importlib.metadata import PackageNotFoundError, version as _installed_version

# Read the version from the package metadata rather than restating it here: a literal in
# this file and the one in pyproject.toml are two statements about the same fact, and they
# had already drifted (0.1.0 against 0.2.0), so `--version` reported a build that does not
# exist.  The fallback covers running straight from a source tree that was never installed.
try:
    __version__ = _installed_version("adaptive-model-router")
except PackageNotFoundError:  # pragma: no cover - source checkout without an install
    __version__ = "0.0.0+source"
