"""Add ``../yt/flet_app`` (and repo root) to ``sys.path`` so ``yt_core`` (FORMAT_PRESETS, run_download, …) resolves from the sibling ``yt`` repo."""
from __future__ import annotations

import sys
from pathlib import Path


def ensure_yt_flet_on_path() -> Path:
    """
    Workspace layout::

        yt/                 ← parent of this package (Desktop/yt)
          yt/               ← git repo root (flet_app lives here)
            flet_app/
          DLPulse_textual/  ← this package

    When frozen (e.g. PyInstaller one-file), dependencies are bundled; no sibling
    ``yt`` folder is required — only ``sys._MEIPASS`` is prepended if present.

    Returns the ``flet_app`` directory path (or a placeholder under frozen).
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            mp = str(Path(meipass).resolve())
            if mp not in sys.path:
                sys.path.insert(0, mp)
        return Path(__file__).resolve().parent

    here = Path(__file__).resolve().parent
    workspace = here.parent
    yt_repo = workspace / "yt" / "flet_app"
    if not yt_repo.is_dir():
        raise RuntimeError(
            f"DLPulse_textual expects the DLPulse repo at {workspace / 'yt' / 'flet_app'!s} "
            "(clone or copy the ``yt`` project next to this folder)."
        )
    yt_root = workspace / "yt"
    for p in (yt_repo, yt_root, here):
        s = str(p.resolve())
        if s not in sys.path:
            sys.path.insert(0, s)
    return yt_repo
