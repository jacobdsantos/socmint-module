"""Load a local ``.env`` into the process environment.

The library reads configuration from ``os.environ`` and nothing else, which
keeps it testable and side-effect free. But the two entry points — the CLI and
the dashboard — are run by a human who expects the ``.env`` sitting in the
project root to be honoured.

Without this, the two entry points disagree: a key exported in one shell makes
the CLI work and leaves the dashboard reporting ``unsupported``, which looks
like a broken adapter rather than a missing variable.

Only the two entry points call this. Tests and imports never do.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load_env_file(path: Path | str = ".env") -> list[str]:
    """Set variables from ``path`` without overriding anything already set.

    Returns the names it set, so a caller can log what it picked up without
    ever logging a value. Missing files are not an error: the environment is a
    legitimate way to supply these.
    """
    file = Path(path)
    if not file.is_file():
        return []

    loaded: list[str] = []
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("could not read %s: %s", file, exc)
        return []

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export ") :].strip()
        value = value.strip().strip('"').strip("'")
        if not name or name in os.environ:
            # An explicit export always wins over the file.
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


__all__ = ["load_env_file"]
