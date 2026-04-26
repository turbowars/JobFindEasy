"""Resume and cover letter generation."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def mirror_to_public(src: Path) -> Path | None:
    """Copy a generated artifact to the user's public folder.

    Target dir is `$PUBLIC_EXPORT_DIR` if set, else `~/Public/JobFindEasy`.
    Returns the destination path on success, or None if copy failed.
    """
    target_dir = Path(
        os.environ.get("PUBLIC_EXPORT_DIR")
        or (Path.home() / "Public" / "JobFindEasy")
    ).expanduser()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / src.name
        shutil.copy2(src, dest)
        return dest
    except Exception:
        return None
