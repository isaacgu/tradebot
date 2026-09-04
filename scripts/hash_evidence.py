"""Print portable SHA-256 evidence hashes for the Gate-0 candidate."""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Sequence
from pathlib import Path

# The raw .prom exposition is deliberately ABSENT: it carries a wall-clock _created
# stamp, so hashing it would write an unreproducible digest into a document that
# asserts reproducibility. The canonical sidecars are the reproducible artifact.
DEFAULT_PATHS = (
    Path("docs/SPEC.md"),
    Path("docs/SPEC-supplied-2026-09-03.md"),
    Path("uv.lock"),
    Path("build/gate0/demo-manifest.json"),
    Path("build/gate0/demo-manifest.metrics-backtest.canonical.sha256"),
    Path("build/gate0/demo-manifest.metrics-paper.canonical.sha256"),
)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of *path* without text newline conversion."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> None:
    """Hash explicit paths, or the standard Gate-0 evidence set."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    for path in args.paths or DEFAULT_PATHS:
        print(f"{sha256_file(path)}  {path}")


if __name__ == "__main__":
    main()
