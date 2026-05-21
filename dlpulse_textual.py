#!/usr/bin/env python3
"""DLPulse — Textual TUI (run: python dlpulse_textual.py)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Sequence

from rich.markup import escape
from pathlib import Path

from path_setup import ensure_yt_flet_on_path

ensure_yt_flet_on_path()

from cast_http import guess_mime_for_cast, media_url, start_cast_server
from chromecast_helper import (
    discover_chromecasts,
    get_lan_ip,
    play_url,
    play_url_to_casts,
    stop_last_cast,
    stop_projection,
)
from paths import DOWNLOADS_DIR as _DEFAULT_DOWNLOADS_DIR
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from yt_core import (
    FORMAT_ARTWORK_ONLY,
    FORMAT_PRESETS,
    detect_content_type,
    extract_url_info,
    fetch_playlist_entries,
    get_format_preset,
    run_download,
    search_soundcloud,
    search_youtube,
)

try:
    from ytdlp_update import get_installed_ytdlp_version, fetch_pypi_latest_ytdlp_version
except ImportError:
    get_installed_ytdlp_version = None  # type: ignore
    fetch_pypi_latest_ytdlp_version = None  # type: ignore

# ── Config persistence ────────────────────────────────────────────────────────
_CONFIG_PATH = Path.home() / ".config" / "dlpulse" / "config.json"

# Library: only these extensions (lowercase suffix incl. dot)
def _artwork_preset_index() -> int:
    for i, (_, spec, _) in enumerate(FORMAT_PRESETS):
        if spec == FORMAT_ARTWORK_ONLY:
            return i
    return max(0, len(FORMAT_PRESETS) - 1)


_LIB_MEDIA_SUFFIXES = frozenset(
    {
        ".mp4",
        ".webm",
        ".mkv",
        ".avi",
        ".mov",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".wmv",
        ".flv",
        ".ogv",
        ".ts",
        ".m2ts",
        ".3gp",
        ".f4v",
        ".mp3",
        ".m4a",
        ".aac",
        ".opus",
        ".ogg",
        ".oga",
        ".flac",
        ".wav",
        ".wma",
        ".alac",
        ".ac3",
        ".eac3",
        ".mka",
        ".aiff",
        ".aif",
    }
)


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _get_cast_discover_wait(cfg: dict) -> float:
    """Chromecast mDNS listen duration (seconds), clamped 0.5–120."""
    try:
        v = float(cfg.get("cast_discover_wait", 3))
    except (TypeError, ValueError):
        v = 3.0
    return max(0.5, min(120.0, v))


def _get_downloads_dir(cfg: dict) -> Path:
    p = cfg.get("downloads_dir")
    if p:
        return Path(p)
    # Default: ~/Downloads
    d = Path.home() / "Downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_player(cfg: dict) -> str:
    """Stored value for the player field; empty = use OS default for URLs/files."""
    raw = cfg.get("player")
    if raw is None:
        return ""
    return str(raw).strip()


def _find_ffmpeg_binary() -> str | None:
    """Return path to ffmpeg if found on PATH or common Windows install dirs."""
    for name in ("ffmpeg", "ffmpeg.exe"):
        w = shutil.which(name)
        if w:
            return w
    if sys.platform == "win32":
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ):
            for parts in (
                ("ffmpeg", "bin", "ffmpeg.exe"),
                ("FFmpeg", "bin", "ffmpeg.exe"),
            ):
                cand = Path(base).joinpath(*parts)
                if cand.is_file():
                    return str(cand)
    return None


def _resolve_ffmpeg_for_ytdlp(cfg: dict) -> str | None:
    """Path for yt-dlp ``ffmpeg_location`` (exe or directory containing ffmpeg)."""
    raw = (cfg.get("ffmpeg_location") or "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            low = p.name.lower()
            if low in ("ffmpeg", "ffmpeg.exe"):
                return str(p.resolve())
            return None
        if p.is_dir():
            exe = p / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if exe.is_file():
                return str(p.resolve())
        return None
    found = _find_ffmpeg_binary()
    return str(Path(found).resolve()) if found else None


def _ffmpeg_status_not_found_markup() -> str:
    if sys.platform == "win32":
        tip = "Windows: [bold]winget install Gyan.FFmpeg[/] then restart, or set path below."
    elif sys.platform == "darwin":
        tip = "macOS: [bold]brew install ffmpeg[/], or set path below."
    else:
        tip = "Linux/BSD: install [bold]ffmpeg[/] from your package manager, or set path below."
    return (
        "FFmpeg: [bold #f85149]not found[/] — needed for video+audio merge and MP3/M4A. "
        f"[#484f58]{tip}[/]"
    )


def _ffmpeg_status_markup(cfg: dict) -> str:
    override = (cfg.get("ffmpeg_location") or "").strip()
    resolved = _resolve_ffmpeg_for_ytdlp(cfg)
    if resolved:
        src = "custom" if override else "PATH"
        return f"FFmpeg: [bold #3fb950]OK[/] ({src}) [dim]{escape(resolved)}[/]"
    return _ffmpeg_status_not_found_markup()


def _ffmpeg_settings_hint_markup() -> str:
    if sys.platform == "win32":
        return (
            "[#484f58]Leave empty for PATH. Otherwise: folder with [bold]ffmpeg.exe[/] "
            "or full path to that file.[/]"
        )
    if sys.platform == "darwin":
        return (
            "[#484f58]Leave empty for PATH. Otherwise: directory with the [bold]ffmpeg[/] binary "
            "(e.g. [bold]/opt/homebrew/bin[/]) or full path to [bold]ffmpeg[/].[/]"
        )
    return (
        "[#484f58]Leave empty for PATH. Otherwise: directory with [bold]ffmpeg[/] "
        "(e.g. [bold]/usr/bin[/]) or full path to the binary.[/]"
    )


def _ffmpeg_input_placeholder() -> str:
    if sys.platform == "win32":
        return r"C:\ffmpeg\bin · full path to ffmpeg.exe …"
    if sys.platform == "darwin":
        return "/opt/homebrew/bin · /usr/local/bin/ffmpeg …"
    return "/usr/bin · ~/.local/bin · full path to ffmpeg …"


def _ffmpeg_missing_download_tip_log() -> str:
    if sys.platform == "win32":
        return (
            "[yellow]Tip:[/] winget install Gyan.FFmpeg (restart app) or set FFmpeg path in Settings."
        )
    if sys.platform == "darwin":
        return "[yellow]Tip:[/] brew install ffmpeg, or set FFmpeg path in Settings."
    return "[yellow]Tip:[/] Install ffmpeg from your distro, or set path in Settings (F6)."


def _ffmpeg_missing_err_suffix() -> str:
    if sys.platform == "win32":
        return " — Settings → FFmpeg, or: winget install Gyan.FFmpeg"
    if sys.platform == "darwin":
        return " — Settings → FFmpeg, or: brew install ffmpeg"
    return " — Settings → FFmpeg, or install ffmpeg via your package manager"


def _locate_ytdlp_binary() -> str | None:
    """Find system yt-dlp / youtube-dl (mpv ytdl_hook needs it for YouTube/SoundCloud URLs)."""
    w = shutil.which("yt-dlp") or shutil.which("youtube-dl")
    if w:
        return w
    for d in ("/usr/local/bin", "/usr/bin", "/bin", str(Path.home() / ".local" / "bin")):
        for name in ("yt-dlp", "youtube-dl"):
            p = Path(d) / name
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


def _subprocess_env_for_external_gui() -> dict[str, str] | None:
    """When frozen (PyInstaller), sanitize env for external GUI/CLI children.

    - Strip ``_MEIPASS`` from ``LD_LIBRARY_PATH`` / ``PYTHONPATH`` (wrong libc for browser/mpv).
    - Drop ``LD_PRELOAD`` / ``PYTHONHOME`` (bootloader hooks can break ``mpv``).
    - Prepend standard ``PATH`` dirs so ``mpv`` finds ``yt-dlp`` for stream URLs.
    """
    if not getattr(sys, "frozen", False):
        return None
    env = os.environ.copy()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        me = os.path.normpath(str(meipass))
        for key in ("LD_LIBRARY_PATH", "PYTHONPATH"):
            val = env.get(key)
            if not val:
                continue
            parts = [
                p
                for p in val.split(os.pathsep)
                if p and os.path.normpath(p) != me and not os.path.normpath(p).startswith(me + os.sep)
            ]
            if parts:
                env[key] = os.pathsep.join(parts)
            else:
                env.pop(key, None)
    env.pop("PYTHONHOME", None)
    env.pop("LD_PRELOAD", None)
    if sys.platform != "win32":
        cur = [p for p in (env.get("PATH") or "").split(os.pathsep) if p]
        prepend: list[str] = []
        for p in ("/usr/local/bin", "/usr/bin", "/bin", str(Path.home() / ".local" / "bin")):
            if os.path.isdir(p) and p not in prepend:
                prepend.append(p)
        for p in reversed(prepend):
            if p not in cur:
                cur.insert(0, p)
        if cur:
            env["PATH"] = os.pathsep.join(cur)
    return env


class RenameScreen(Screen[tuple[str, str, str] | None]):
    """Rename a file in the library."""

    def __init__(self, job: str, name: str) -> None:
        super().__init__()
        self._job = job
        self._name = name

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New name (was: {self._name}):"),
            Input(value=self._name, id="ren-in"),
            Horizontal(
                Button("OK", id="ren-ok", variant="success"),
                Button("Cancel", id="ren-cancel"),
            ),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ren-ok":
            nv = self.query_one("#ren-in", Input).value.strip()
            if nv and nv != self._name:
                self.dismiss((self._job, self._name, nv))
            else:
                self.dismiss(None)
        elif event.button.id == "ren-cancel":
            self.dismiss(None)


class FileRenameScreen(Screen[tuple[Path, str] | None]):
    """Rename a single file by basename (browse folder mode)."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New name (was: {self._path.name}):"),
            Input(value=self._path.name, id="fil-ren-in"),
            Horizontal(
                Button("OK", id="fil-ren-ok", variant="success"),
                Button("Cancel", id="fil-ren-x"),
            ),
        )

    @on(Button.Pressed, "#fil-ren-ok")
    def fil_ren_ok(self) -> None:
        nv = self.query_one("#fil-ren-in", Input).value.strip()
        if nv and nv != self._path.name:
            self.dismiss((self._path, nv))
        else:
            self.dismiss(None)

    @on(Button.Pressed, "#fil-ren-x")
    def fil_ren_x(self) -> None:
        self.dismiss(None)


class FolderPickerScreen(Screen[Path | None]):
    """Modal folder picker with Up / Home / tree navigation."""

    BINDINGS = [
        ("escape", "dismiss_none", "Cancel"),
        ("backspace", "go_up", "Up"),
    ]

    def __init__(self, start: Path | None = None) -> None:
        super().__init__()
        self._current = (start or Path.home()).resolve()

    def compose(self) -> ComposeResult:
        # No id on DirectoryTree: remounting on "Up" would duplicate id if compose re-ran.
        yield Vertical(
            Label("  Browse folders — ↑↓ navigate · Enter expand · Backspace go up · Esc cancel", id="fp-title"),
            Static(str(self._current), id="fp-current-path"),
            Horizontal(
                Button("⬆ Up", id="fp-up"),
                Button("⌂ Home", id="fp-home"),
                Button("✓ Select", id="fp-ok", variant="success"),
                Button("Cancel", id="fp-cancel"),
                id="fp-btns",
            ),
            DirectoryTree(str(self._current), classes="fp-picker-tree"),
            id="fp-root",
        )

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def action_go_up(self) -> None:
        self._navigate_to(self._current.parent)

    def _navigate_to(self, path: Path) -> None:
        path = path.resolve()
        if not path.is_dir():
            return
        self._current = path
        self.query_one("#fp-current-path", Static).update(str(path))
        root = self.query_one("#fp-root", Vertical)
        old_tree = root.query_one(DirectoryTree)
        old_tree.remove()
        new_tree = DirectoryTree(str(path), classes="fp-picker-tree")
        root.mount(new_tree)
        new_tree.focus()

    @on(Button.Pressed, "#fp-up")
    def fp_up(self) -> None:
        self._navigate_to(self._current.parent)

    @on(Button.Pressed, "#fp-home")
    def fp_home(self) -> None:
        self._navigate_to(Path.home())

    @on(Button.Pressed, "#fp-cancel")
    def fp_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#fp-ok")
    def fp_ok(self) -> None:
        tree = self.query_one("#fp-root", Vertical).query_one(DirectoryTree)
        node = tree.cursor_node
        if node and node.data and node.data.path:
            p = Path(node.data.path)
            selected = p if p.is_dir() else p.parent
        else:
            selected = self._current
        self.dismiss(selected)

    @on(DirectoryTree.DirectorySelected)
    def fp_dir_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self._current = Path(event.path).resolve()
        self.query_one("#fp-current-path", Static).update(str(self._current))

    @on(DirectoryTree.FileSelected)
    def fp_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(Path(event.path).parent)


class DLPulseTextualApp(App[None]):
    TITLE = "DLPulse"
    DARK = True

    CSS = """
    /* ── Global ─────────────────────────────────────────────────────────── */
    Screen {
        background: #0d1117;
        color: #e6edf3;
        layers: base overlay;
    }

    Header {
        background: #010409;
        color: #58a6ff;
        text-style: bold;
        height: 1;
    }

    Footer {
        background: #010409;
        color: #484f58;
        height: 1;
    }

    /* ── Tabs ────────────────────────────────────────────────────────────── */
    TabbedContent { height: 1fr; min-height: 0; }

    TabbedContent > Tabs {
        background: #010409;
        border-bottom: solid #21262d;
        height: 2;
    }

    Tab {
        color: #484f58;
        padding: 0 2;
    }

    Tab.-active {
        color: #e6edf3;
        background: #0d1117;
        text-style: bold;
    }

    Tab:hover { color: #c9d1d9; background: #161b22; }

    TabPane { height: 1fr; min-height: 0; padding: 0; }

    /* ── Layout helpers ───────────────────────────────────────────────────── */
    .tab-pane-body {
        height: 1fr;
        min-height: 0;
        layout: vertical;
        padding: 1 2;
    }

    .section-label {
        color: #484f58;
        text-style: bold;
        height: 1;
        margin-top: 1;
        padding: 0 1;
        background: #010409;
        border-left: heavy #58a6ff;
    }

    .row-gap {
        height: auto;
        layout: horizontal;
        margin-bottom: 0;
        margin-top: 1;
    }

    /* Settings card groups */
    .settings-card {
        layout: vertical;
        height: auto;
        background: #0d1117;
        border: solid #21262d;
        padding: 0 1 1 1;
        margin-bottom: 1;
    }

    .settings-card .section-label {
        margin-top: 0;
    }

    /* ── Download: unified input ─────────────────────────────────────────── */
    #main-input-row {
        height: auto;
        layout: horizontal;
        margin-top: 1;
        margin-bottom: 0;
    }

    #main-input { width: 1fr; }

    #input-status {
        height: 1;
        color: #484f58;
        padding: 0 1;
        margin-bottom: 0;
    }

    #results-table {
        height: 8;
        border: solid #21262d;
        background: #161b22;
    }

    #results-table.hidden { display: none; }

    #results-table-placeholder {
        height: auto;
        min-height: 0;
        color: #484f58;
        padding: 0 1;
    }

    #results-table-placeholder.hidden { display: none; }



    #fmt-block {
        height: auto;
        layout: vertical;
        width: 1fr;
        margin-top: 1;
        margin-bottom: 0;
    }

    #fmt-actions-row {
        height: auto;
        layout: horizontal;
        width: 1fr;
    }

    #fmt-actions-row Button {
        width: 1fr;
    }

    /* Same width as the button row above (full #fmt-block width). */
    #dl-fmt {
        width: 1fr;
        margin-top: 1;
    }

    /* ── Download: split ─────────────────────────────────────────────────── */
    #download-split {
        height: 1fr;
        min-height: 0;
        layout: horizontal;
    }

    /* ── Queue sidebar ────────────────────────────────────────────────────── */
    #queue-pane {
        background: #010409;
        border-right: solid #21262d;
        width: 26;
        min-width: 18;
        height: 1fr;
        layout: vertical;
        padding: 0;
    }

    #queue-title {
        background: #0d2119;
        color: #3fb950;
        text-style: bold;
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }

    #queue-log {
        height: 1fr;
        min-height: 4;
        background: #010409;
        border: none;
        padding: 0 1;
        scrollbar-color: #21262d;
        scrollbar-background: #010409;
    }

    /* ── Download right panel ────────────────────────────────────────────── */
    #dl-right {
        width: 1fr;
        height: 1fr;
        min-width: 0;
        layout: vertical;
        padding: 0 1;
    }

    #dl-active-name {
        height: auto;
        min-height: 0;
        color: #484f58;
        padding: 0 1;
    }

    #dl-active-name.hidden { display: none; }

    #dl-progress-wrap {
        layout: vertical;
        height: auto;
        margin-top: 0;
    }

    #dl-progress-wrap.hidden { display: none; }

    #dl-progress { height: 1; }

    ProgressBar > .bar--bar     { color: #388bfd; }
    ProgressBar > .bar--complete { color: #3fb950; }
    ProgressBar > .bar--indeterminate { color: #d29922; }

    #dl-log {
        height: 1fr;
        min-height: 5;
        border: solid #21262d;
        background: #161b22;
        padding: 0 1;
        scrollbar-color: #21262d;
        scrollbar-background: #161b22;
    }

    /* ── Library ─────────────────────────────────────────────────────────── */
    #lib-table {
        height: 1fr;
        min-height: 5;
        border: solid #21262d;
        background: #161b22;
    }

    DataTable > .datatable--header {
        background: #161b22;
        color: #58a6ff;
        text-style: bold;
    }

    DataTable > .datatable--cursor  { background: #1f2d3d; color: #e6edf3; }
    DataTable > .datatable--hover   { background: #161b22; }

    #lib-status {
        height: 1;
        color: #d29922;
        padding: 0 1;
        background: #161b22;
        border-left: heavy #d29922;
        margin-top: 0;
        display: none;
    }

    #lib-status.visible { display: block; }

    #lib-cast-hint { height: 1; color: #3fb950; padding: 0 1; }

    #lib-browse-path {
        height: 1;
        min-height: 1;
        color: #58a6ff;
        padding: 0 1;
        background: #010409;
    }

    #lib-browse-path.hidden { display: none; }

    #log-lib {
        height: 4;
        border: solid #21262d;
        background: #161b22;
        padding: 0 1;
    }

    .lib-action-row {
        height: auto;
        layout: horizontal;
        margin-top: 1;
        margin-bottom: 0;
    }

    /* ── Cast ────────────────────────────────────────────────────────────── */
    #cast-help {
        height: auto;
        min-height: 2;
        color: #484f58;
        background: #010409;
        border: solid #21262d;
        border-left: heavy #388bfd;
        padding: 0 1;
        margin-bottom: 1;
    }

    #cast-srv-status {
        height: 1;
        color: #3fb950;
        padding: 0 1;
    }

    #cast-srv-status.offline { color: #f85149; }

    #cast-table {
        height: 1fr;
        min-height: 5;
        border: solid #21262d;
        background: #161b22;
    }

    #log-cast {
        height: 5;
        border: solid #21262d;
        background: #161b22;
        padding: 0 1;
    }

    #cast-name-filter { width: 1fr; }

    /* ── Settings ────────────────────────────────────────────────────────── */
    .settings-tab-root {
        height: 1fr;
        min-height: 0;
        layout: vertical;
        padding: 0 2 1 2;
    }

    .settings-toolbar {
        layout: horizontal;
        height: auto;
        margin-bottom: 1;
        content-align: left middle;
    }

    /* Save first so narrow terminals never clip the primary action */
    #btn-settings-save-all,
    #btn-settings-save-all-bottom {
        min-width: 22;
        margin-right: 1;
    }

    .settings-toolbar-hint {
        color: #484f58;
        height: auto;
        max-height: 2;
        width: 1fr;
        content-align: left middle;
    }

    /* Per-row: Save left, field grows — avoids hidden Save on small widths */
    .settings-field-row {
        height: auto;
        layout: horizontal;
        margin-bottom: 0;
        margin-top: 1;
    }

    .settings-field-row Button {
        margin-right: 1;
    }

    .settings-field-row Input {
        width: 1fr;
        min-width: 8;
    }

    #settings-scroll {
        height: 1fr;
        min-height: 0;
    }

    #settings-inner-scroll {
        height: auto;
        layout: vertical;
    }

    #settings-dl-path  { color: #58a6ff; padding: 0 1; height: 1; }
    #settings-player   { color: #3fb950; padding: 0 1; height: auto; min-height: 1; }
    #settings-ytdlp    { color: #58a6ff; padding: 0 1; height: 1; }

    #settings-ffmpeg-status {
        height: auto;
        min-height: 1;
        color: #e6edf3;
        padding: 0 1;
    }

    #settings-player-hint {
        height: auto;
        min-height: 1;
        color: #484f58;
        padding: 0 1;
    }

    #settings-cast-wait-help {
        height: auto;
        min-height: 1;
        color: #484f58;
        padding: 0 1;
    }

    #settings-cast-wait {
        width: 1fr;
        max-width: 18;
        min-width: 8;
    }

    #settings-ffmpeg-input { min-width: 16; width: 1fr; }

    #log-settings {
        height: 7;
        min-height: 5;
        max-height: 12;
        border: solid #21262d;
        background: #161b22;
        padding: 0 1;
        margin-top: 1;
    }

    /* ── Inputs ───────────────────────────────────────────────────────────── */
    Input {
        background: #161b22;
        border: tall #30363d;
        color: #e6edf3;
        height: 3;
    }

    Input:focus     { border: tall #388bfd; }
    Input.-valid    { border: tall #3fb950; }
    Input.-invalid  { border: tall #f85149; }

    /* ── Buttons ──────────────────────────────────────────────────────────── */
    Button {
        background: #21262d;
        border: tall #30363d;
        color: #c9d1d9;
        height: 3;
        min-width: 10;
        margin: 0 1 0 0;
    }

    Button:hover  { background: #30363d; color: #e6edf3; }
    Button:focus  { border: tall #388bfd; background: #1f2d3d; }
    Button.-active { background: #1f2d3d; }

    Button.-primary {
        background: #1f2d3d;
        border: tall #388bfd;
        color: #79c0ff;
        text-style: bold;
    }

    Button.-primary:hover  { background: #2d4a6e; }

    Button.-success {
        background: #0d2119;
        border: tall #3fb950;
        color: #3fb950;
        text-style: bold;
    }

    Button.-success:hover  { background: #0f2d1e; }

    Button.-warning {
        background: #2d1f00;
        border: tall #d29922;
        color: #d29922;
    }

    Button.-warning:hover  { background: #3a2900; }

    Button.-error {
        background: #2d0d0d;
        border: tall #f85149;
        color: #f85149;
    }

    Button.-error:hover  { background: #3d1111; }

    /* ── Select ───────────────────────────────────────────────────────────── */
    Select            { background: #161b22; border: tall #30363d; color: #e6edf3; }
    Select:focus      { border: tall #388bfd; }
    SelectOverlay     { background: #161b22; border: solid #30363d; }
    SelectOverlay > .option-list--option-highlighted {
        background: #1f2d3d;
        color: #e6edf3;
    }

    /* ── Checkbox ─────────────────────────────────────────────────────────── */
    Checkbox        { color: #484f58; height: 1; margin: 0; padding: 0; }
    Checkbox.-on    { color: #3fb950; }
    Checkbox:focus  { color: #58a6ff; }

    /* ── RichLog ──────────────────────────────────────────────────────────── */
    RichLog {
        scrollbar-gutter: stable;
        scrollbar-color: #21262d;
        scrollbar-background: #161b22;
    }

    /* ── Home tab ─────────────────────────────────────────────────────────── */
    #home-wrap {
        height: 1fr;
        layout: vertical;
        align: center middle;
        padding: 1 4;
    }

    #home-text {
        height: auto;
        color: #8b949e;
        background: #0d1117;
    }

    /* ── FolderPickerScreen ───────────────────────────────────────────────── */
    FolderPickerScreen { align: center middle; }

    FolderPickerScreen > Vertical {
        background: #161b22;
        border: solid #30363d;
        padding: 1 2;
        width: 75%;
        height: 85%;
        min-width: 54;
        min-height: 22;
    }

    #fp-title {
        color: #484f58;
        height: 1;
        padding: 0;
        border-bottom: solid #21262d;
        margin-bottom: 1;
    }

    #fp-current-path {
        color: #58a6ff;
        background: #0d1117;
        height: 1;
        padding: 0 1;
        border: solid #21262d;
        border-left: heavy #388bfd;
    }

    #fp-btns {
        height: auto;
        layout: horizontal;
        margin-top: 1;
        margin-bottom: 1;
    }

    .fp-picker-tree {
        height: 1fr;
        border: solid #21262d;
        background: #0d1117;
    }

    DirectoryTree > .directory-tree--folder { color: #58a6ff; }
    DirectoryTree > .directory-tree--file   { color: #8b949e; }
    DirectoryTree > .tree--cursor           { background: #1f2d3d; color: #e6edf3; }
    DirectoryTree > .tree--highlight        { background: #161b22; }

    /* ── Modal screens (Rename / FileRename) ──────────────────────────────── */
    RenameScreen, FileRenameScreen { align: center middle; }

    RenameScreen > Vertical, FileRenameScreen > Vertical {
        background: #161b22;
        border: solid #30363d;
        border-top: heavy #388bfd;
        padding: 2 3;
        width: 62;
        height: auto;
        min-width: 40;
    }

    RenameScreen Label, FileRenameScreen Label {
        color: #484f58;
        margin-bottom: 1;
    }

    RenameScreen Input, FileRenameScreen Input { margin-bottom: 1; }

    RenameScreen Horizontal, FileRenameScreen Horizontal {
        height: auto;
        layout: horizontal;
        align: right middle;
        margin-top: 1;
    }
    """


    # ── Keyboard shortcuts ────────────────────────────────────────────────────
    # priority=True → funcționează indiferent de widget-ul cu focus
    # (fără priority, DataTable/Input înghit F-keys și Ctrl+letter)
    BINDINGS = [
        Binding("q",      "quit",               "q  Quit",        show=True,  priority=True),
        # ── Tabs: F2-F6 ───────────────────────────────────────────────────
        Binding("f2",     "tab_home",           "F2 Home",        show=True,  priority=True),
        Binding("f3",     "tab_download",       "F3 Search",      show=True,  priority=True),
        Binding("f4",     "tab_library",        "F4 Library",     show=True,  priority=True),
        Binding("f5",     "tab_cast",           "F5 Cast",        show=True,  priority=True),
        Binding("f6",     "tab_settings",       "F6 Settings",    show=True,  priority=True),
        # ── Search&Download ───────────────────────────────────────────────
        Binding("ctrl+g", "go",                 "^G Go",          show=False, priority=True),
        Binding("ctrl+n", "select_all",         "^N Sel All",     show=False, priority=True),
        Binding("ctrl+y", "play_sel",           "^Y Play",        show=False, priority=True),
        Binding("f7",     "download_sel",       "F7 Download",    show=False, priority=True),
        Binding("f10",    "download_artwork_sel", "F10 Cover",    show=False, priority=True),
        # ── Library ───────────────────────────────────────────────────────
        Binding("ctrl+r", "lib_refresh_kb",     "^R Refresh",     show=False, priority=True),
        Binding("f8",     "lib_browse_kb",      "F8 Browse",      show=False, priority=True),
        Binding("f9",     "lib_downloads_kb",   "F9 Downloads",   show=False, priority=True),
        Binding("ctrl+t", "lib_cast_kb",        "^T Cast",        show=False, priority=True),
        Binding("f11",    "lib_rename_kb",      "F11 Rename",     show=False, priority=True),
        Binding("f12",    "lib_delete_kb",      "F12 Delete",     show=False, priority=True),
        # ── Cast ─────────────────────────────────────────────────────────
        Binding("ctrl+b", "cast_http_kb",       "^B HTTP",        show=False, priority=True),
        Binding("ctrl+o", "cast_discover_kb",   "^O Discover",    show=False, priority=True),
        Binding("ctrl+j", "cast_start_kb",      "^J Cast",        show=False, priority=True),
        Binding("ctrl+m", "cast_all_kb",        "^M All dev",     show=False, priority=True),
        Binding("ctrl+l", "cast_stop_sel_kb",   "^L Stop sel",    show=False, priority=True),
        Binding("ctrl+h", "cast_stop_last_kb",  "^H Stop last",   show=False, priority=True),
        # ── Settings ─────────────────────────────────────────────────────
        Binding("ctrl+s", "settings_browse_kb", "^S Browse",      show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cfg = _load_config()
        self._downloads_dir: Path = _get_downloads_dir(self._cfg)
        self._player: str = _get_player(self._cfg)
        self._results: list[dict] = []
        self._result_mode: str = ""
        self._selected_rows: set[int] = set()
        self._lib_selected_rows: set[int] = set()
        self._lib_mode: str = "downloads"  # "downloads" | "browse"
        self._lib_browse_dir: Path = self._downloads_dir
        self._lib_file_paths: list[Path] = []
        self._cast_devices: list = []
        self._cast_port: int = 0
        self._cast_selected_rows: set[int] = set()
        self._files_to_cast: list[str] = []

    @staticmethod
    def _is_url(text: str) -> bool:
        u = (text or "").strip().lower()
        if u.startswith(("http://", "https://")):
            return True
        if u.startswith("www.") and (
            "youtube.com" in u or "youtu.be" in u or "soundcloud.com" in u
        ):
            return True
        if u.startswith("youtu.be/"):
            return True
        if "soundcloud.com/" in u:
            return True
        return False

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs", initial="tab-download"):
            with TabPane("⬡ Home  F2", id="tab-home"):
                yield Vertical(
                    Static(
                        "\n"
                        "  [bold #388bfd]██████╗ ██╗     ██████╗ ██╗   ██╗██╗     ███████╗███████╗[/]\n"
                        "  [bold #388bfd]██╔══██╗██║     ██╔══██╗██║   ██║██║     ██╔════╝██╔════╝[/]\n"
                        "  [bold #58a6ff]██║  ██║██║     ██████╔╝██║   ██║██║     ███████╗█████╗  [/]\n"
                        "  [bold #58a6ff]██║  ██║██║     ██╔═══╝ ██║   ██║██║     ╚════██║██╔══╝  [/]\n"
                        "  [bold #79c0ff]██████╔╝███████╗██║     ╚██████╔╝███████╗███████║███████╗[/]\n"
                        "  [bold #79c0ff]╚═════╝ ╚══════╝╚═╝      ╚═════╝ ╚══════╝╚══════╝╚══════╝[/]\n"
                        "  [#484f58]YouTube, SoundCloud & more — yt-dlp + Chromecast[/]\n\n"
                        "  [#21262d]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]\n\n"
                        "  [#388bfd]⬇[/]  [bold #c9d1d9]Search&Download[/]   [#484f58]Study / library · yt-dlp URL or keyword · playlist[/]\n"
                        "  [#388bfd]▤[/]  [bold #c9d1d9]Library[/]            [#484f58]Browse, play, cast, rename, delete[/]\n"
                        "  [#388bfd]⊹[/]  [bold #c9d1d9]Cast[/]               [#484f58]HTTP server + Chromecast discovery[/]\n"
                        "  [#388bfd]⚙[/]  [bold #c9d1d9]Settings[/]           [#484f58]Downloads folder · media player[/]\n\n"
                        "  [#21262d]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]\n\n"
                        "  [#484f58]Ctrl+D[/] [#c9d1d9]Download[/]  [#484f58]Ctrl+L[/] [#c9d1d9]Library[/]  [#484f58]Ctrl+C[/] [#c9d1d9]Cast[/]  [#484f58]Q[/] [#c9d1d9]Quit[/]\n"
                        "  [#484f58]Tab / Shift+Tab[/] [#c9d1d9]navigate panels[/]  [#484f58]Space[/] [#c9d1d9]select rows[/]\n\n"
                        "  [#21262d]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]\n\n"
                        "  [#58a6ff]github.com/calvarr/DLPulse[/]   "
                        "[#f9826c]☕ buymeacoffee.com/medcodex[/]\n\n"
                        "  [#484f58]Built on:[/] [#3fb950]yt-dlp[/] · [#3fb950]Textual[/] · [#3fb950]Rich[/] · [#3fb950]pychromecast[/] · [#3fb950]Flask[/]\n\n",
                        id="home-text",
                    ),
                    id="home-wrap",
                )
            with TabPane("⬇ Search  F3", id="tab-download"):
                with Horizontal(id="download-split"):
                    with Vertical(id="queue-pane"):
                        yield Label(" ⬡  QUEUE ", id="queue-title")
                        yield RichLog(id="queue-log", markup=True, highlight=False)
                    with Vertical(id="dl-right"):
                        yield Label("  SEARCH OR URL", classes="section-label")
                        yield Horizontal(
                            Input(
                                placeholder="Keywords… or paste YouTube / SoundCloud URL",
                                id="main-input",
                            ),
                            Button("Go  ^G", id="btn-go", variant="primary"),
                            id="main-input-row",
                        )
                        yield Horizontal(
                            Static("  Source ", classes="section-label"),
                            Select(
                                [("YouTube", "yt"), ("SoundCloud", "sc")],
                                value="yt",
                                id="search-src",
                            ),
                            classes="row-gap",
                        )
                        yield Static("", id="input-status")
                        yield Static("", id="results-table-placeholder")
                        yield DataTable(cursor_type="row", id="results-table", classes="hidden")
                        with Vertical(id="fmt-block"):
                            yield Horizontal(
                                Button("✓ Sel All  ^N", id="btn-select-all", variant="warning"),
                                Button("▶ Play  ^Y", id="btn-play-selected", variant="primary"),
                                Button("⬇ Download  F7", id="btn-dl-selected", variant="success"),
                                Button("🖼 Cover  F10", id="btn-dl-artwork", variant="warning"),
                                id="fmt-actions-row",
                            )
                            yield Select(
                                [(p[0], str(i)) for i, p in enumerate(FORMAT_PRESETS)],
                                value=str(0),
                                id="dl-fmt",
                            )
                        yield Static("", id="dl-active-name", classes="hidden")
                        with Vertical(id="dl-progress-wrap", classes="hidden"):
                            yield ProgressBar(total=100, show_eta=False, id="dl-progress")
                        yield RichLog(id="dl-log", highlight=True, markup=True)
            with TabPane("▤ Library  F4", id="tab-lib"):
                yield Vertical(
                    Horizontal(
                        Button("↺ Refresh  ^R", id="btn-lib-refresh", variant="primary"),
                        Button("📁 Browse  F8", id="btn-lib-browse", variant="primary"),
                        Button("⬇ Downloads  F9", id="btn-lib-downloads"),
                        classes="row-gap",
                    ),
                    Static("", id="lib-browse-path", classes="hidden"),
                    DataTable(cursor_type="row", id="lib-table"),
                    Static("", id="lib-status"),
                    Horizontal(
                        Button("▶ Play  ^Y", id="btn-lib-play", variant="success"),
                        Button("⬡ Cast  ^T", id="btn-lib-cast", variant="primary"),
                        Button("✎ Rename  F11", id="btn-lib-ren"),
                        Button("✕ Delete  F12", id="btn-lib-del", variant="error"),
                        classes="lib-action-row",
                    ),
                    Static("", id="lib-cast-hint"),
                    RichLog(id="log-lib", highlight=True, markup=True),
                    classes="tab-pane-body",
                )
            with TabPane("⊹ Cast  F5", id="tab-cast"):
                yield Vertical(
                    Static(
                        " [bold #388bfd]HOW TO CAST:[/]  "
                        "[#484f58]Library → select file(s) → ⬡ Cast[/]  "
                        "[#484f58]·[/]  [#484f58]Then: Discover → select device → ▶ Start casting[/]\n"
                        " [#484f58]Space = multi-select devices · name filter = cast to matching device(s) · scan time in Settings[/]",
                        id="cast-help",
                    ),
                    Horizontal(
                        Button("⬡ HTTP  ^B", id="btn-cast-http", variant="primary"),
                        Button("⊹ Discover  ^O", id="btn-cast-disc", variant="primary"),
                        Input(placeholder="Filter by device name…", id="cast-name-filter"),
                        classes="row-gap",
                    ),
                    Static("[#484f58]Server offline[/]", id="cast-srv-status"),
                    DataTable(cursor_type="row", id="cast-table"),
                    Horizontal(
                        Button("▶ Cast  ^J", id="btn-cast-play", variant="success"),
                        Button("▶ All dev  ^M", id="btn-cast-play-all", variant="primary"),
                        Button("⏹ Stop sel  ^L", id="btn-cast-stop", variant="warning"),
                        Button("⏹ Stop last  ^H", id="btn-cast-stop-last", variant="warning"),
                        classes="row-gap",
                    ),
                    RichLog(id="log-cast", highlight=True, markup=True),
                    classes="tab-pane-body",
                )
            with TabPane("⚙ Settings  F6", id="tab-settings"):
                yield Vertical(
                    Horizontal(
                        Button("Save all settings", id="btn-settings-save-all", variant="primary"),
                        Static(
                            "Saves player, FFmpeg path & Cast wait (or use Save in each section).",
                            id="settings-toolbar-hint",
                            classes="settings-toolbar-hint",
                        ),
                        classes="settings-toolbar",
                    ),
                    ScrollableContainer(
                        Vertical(
                            Vertical(
                                Label("  📁  DOWNLOADS", classes="section-label"),
                                Static("", id="settings-dl-path"),
                                Horizontal(
                                    Button("Browse  ^S", id="btn-settings-browse-dl", variant="primary"),
                                    Button("Reset ~/Downloads", id="btn-settings-reset-dl"),
                                    classes="row-gap",
                                ),
                                classes="settings-card",
                            ),
                            Vertical(
                                Label("  ⬡  YT-DLP & FFMPEG", classes="section-label"),
                                Static("", id="settings-ytdlp"),
                                Static("", id="settings-ffmpeg-status"),
                                Static(_ffmpeg_settings_hint_markup(), id="settings-ffmpeg-hint"),
                                Horizontal(
                                    Button("Save", id="btn-settings-save-ffmpeg", variant="success"),
                                    Button("Clear", id="btn-settings-clear-ffmpeg"),
                                    Input(
                                        placeholder=_ffmpeg_input_placeholder(),
                                        id="settings-ffmpeg-input",
                                    ),
                                    classes="settings-field-row",
                                ),
                                Button("Check PyPI version", id="btn-settings-pypi"),
                                classes="settings-card",
                            ),
                            Vertical(
                                Label("  ▶  MEDIA PLAYER", classes="section-label"),
                                Static("", id="settings-player"),
                                Static(
                                    "[#484f58]Empty = OS default · vlc / mpv / full path — Save or Enter[/]",
                                    id="settings-player-hint",
                                ),
                                Horizontal(
                                    Button("Save", id="btn-settings-save-player", variant="success"),
                                    Input(
                                        placeholder="empty · vlc · mpv · full path…",
                                        id="settings-player-input",
                                    ),
                                    classes="settings-field-row",
                                ),
                                classes="settings-card",
                            ),
                            Vertical(
                                Label("  ⊹  CHROMECAST", classes="section-label"),
                                Static(
                                    "[#484f58]Seconds to listen for devices (mDNS). ↑ scroll if needed[/]",
                                    id="settings-cast-wait-help",
                                ),
                                Horizontal(
                                    Button("Save", id="btn-settings-save-cast-wait", variant="success"),
                                    Input(placeholder="3", id="settings-cast-wait"),
                                    classes="settings-field-row",
                                ),
                                classes="settings-card",
                            ),
                            Horizontal(
                                Button("Save all settings", id="btn-settings-save-all-bottom", variant="primary"),
                                classes="settings-toolbar",
                            ),
                            Vertical(
                                Label("  ⚖  LEGAL NOTICE", classes="section-label"),
                                Static(
                                    "[bold]Legal notice[/]\n\n"
                                    "DLPulse is open-source software for educational purposes, technical research, "
                                    "and personal media library management.\n\n"
                                    "[bold]1. Nature[/] — Wrapper around yt-dlp/ffmpeg; no hosted copyrighted media; "
                                    "public sources only; you are responsible for use.\n\n"
                                    "[bold]2. Your responsibility[/] — Copyright & ToS compliance; personal offline "
                                    "use only; no warranties; author not liable for third-party penalties.\n\n"
                                    "[bold]3. No affiliation[/] — Not affiliated with YouTube, SoundCloud, Google LLC.\n\n"
                                    "[#888]If you disagree, do not use or distribute this software.[/]",
                                    id="settings-legal",
                                ),
                                classes="settings-card",
                            ),
                            RichLog(id="log-settings", markup=True),
                            id="settings-inner-scroll",
                        ),
                        id="settings-scroll",
                    ),
                    classes="settings-tab-root",
                )
        yield Footer()

    def _show_dl_progress_wrap(self) -> None:
        self.query_one("#dl-progress-wrap", Vertical).remove_class("hidden")

    def _reset_dl_progress_ui(self) -> None:
        self.query_one("#dl-progress-wrap", Vertical).add_class("hidden")
        self.query_one("#dl-progress", ProgressBar).update(progress=0.0)
        el = self.query_one("#dl-active-name", Static)
        el.update("")
        el.add_class("hidden")

    def _set_dl_active_line(self, text: str) -> None:
        el = self.query_one("#dl-active-name", Static)
        s = (text or "").strip()
        el.update(s[:220] if s else "")
        if s:
            el.remove_class("hidden")
        else:
            el.add_class("hidden")

    def _resolve_player_executable(self) -> str | None:
        """Absolute path to the configured player, or None = use OS default for URLs/files."""
        p = (self._player or "").strip()
        if not p or p.lower() in ("auto", "default", "os", "system"):
            return None
        path = Path(p)
        if path.is_file():
            return str(path.resolve())
        if sys.platform == "win32" and path.suffix.lower() != ".exe":
            pexe = path.with_suffix(".exe")
            if pexe.is_file():
                return str(pexe.resolve())
        w = shutil.which(p)
        if w:
            return w
        if sys.platform == "win32":
            w = shutil.which(f"{p}.exe")
            if w:
                return w
            low = Path(p).name.lower().replace(".exe", "")
            if low == "vlc":
                for base in (
                    os.environ.get("ProgramFiles", r"C:\Program Files"),
                    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                ):
                    cand = Path(base) / "VideoLAN" / "VLC" / "vlc.exe"
                    if cand.is_file():
                        return str(cand)
        return None

    def _build_player_argv(self, player: str, targets: Sequence[str | Path]) -> list[str]:
        """Build argv; mpv gets flags so audio-only still opens a GUI window (OSC), not the TUI tty."""
        parts = [str(x) for x in targets]
        name = Path(player).name.lower()
        if name in ("mpv", "mpv.exe", "mpv.com"):
            opts: list[str] = [
                "--no-terminal",
                "--input-terminal=no",
                "--force-window=yes",
            ]
            # Frozen one-file: minimal PATH / hook discovery — point ytdl_hook at system yt-dlp.
            if getattr(sys, "frozen", False) and sys.platform != "win32":
                ytd = _locate_ytdlp_binary()
                if ytd:
                    opts.insert(
                        0,
                        "--script-opts=ytdl_hook-ytdl_path=" + ytd,
                    )
            return [player] + opts + parts
        return [player] + parts

    def _launch_player_detached(self, player: str, targets: Sequence[str | Path]) -> None:
        """Start the player detached from the TUI; targets are local paths or URLs (playlist order)."""
        argv = self._build_player_argv(player, targets)
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "start_new_session": True,
        }
        env = _subprocess_env_for_external_gui()
        if env is not None:
            kwargs["env"] = env
        if sys.platform == "win32":
            cr = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # Do not hide GUI for players that need their own window (mpv/vlc).
            gui = Path(player).name.lower()
            if cr and gui not in ("mpv.exe", "mpv.com", "vlc.exe", "vlc"):
                kwargs["creationflags"] = cr
        subprocess.Popen(argv, **kwargs)

    def _launch_urls_os_default(self, urls: Sequence[str]) -> None:
        """Open URLs with the OS-registered handler (browser, VLC URL scheme, …)."""
        env = _subprocess_env_for_external_gui()
        for u in urls:
            s = (u or "").strip()
            if not s:
                continue
            if sys.platform == "win32":
                cr = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(
                    ["cmd", "/c", "start", "", s],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=cr,
                )
            elif sys.platform == "darwin":
                kwargs = dict(
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                if env is not None:
                    kwargs["env"] = env
                subprocess.Popen(["open", s], **kwargs)
            else:
                xdg = shutil.which("xdg-open")
                gio = shutil.which("gio")
                if xdg:
                    argv = [xdg, s]
                elif gio:
                    argv = [gio, "open", s]
                else:
                    raise RuntimeError("xdg-open not found — install xdg-utils (or set a player in Settings).")
                kwargs = dict(
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                if env is not None:
                    kwargs["env"] = env
                subprocess.Popen(argv, **kwargs)

    def _launch_paths_os_default(self, paths: Sequence[Path | str]) -> None:
        """Open local files with the OS default application."""
        env = _subprocess_env_for_external_gui()
        for raw in paths:
            p = Path(str(raw))
            if not p.is_file():
                continue
            ab = str(p.resolve())
            if sys.platform == "win32":
                os.startfile(ab)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                kwargs = dict(
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                if env is not None:
                    kwargs["env"] = env
                subprocess.Popen(["open", ab], **kwargs)
            else:
                xdg = shutil.which("xdg-open")
                gio = shutil.which("gio")
                if xdg:
                    argv = [xdg, ab]
                elif gio:
                    argv = [gio, "open", ab]
                else:
                    raise RuntimeError("xdg-open not found — install xdg-utils (or set a player in Settings).")
                kwargs = dict(
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                if env is not None:
                    kwargs["env"] = env
                subprocess.Popen(argv, **kwargs)

    def on_mount(self) -> None:
        rt = self.query_one("#results-table", DataTable)
        rt.add_columns("✓", "Title", "Info")
        lt = self.query_one("#lib-table", DataTable)
        lt.add_columns("✓", "File", "Size")
        self.query_one("#cast-table", DataTable).add_columns("✓", "Device", "Model", "Host:port")
        self._refresh_library_table()
        self.query_one("#lib-cast-hint", Static).update("")
        self._refresh_settings_static()
        self._log_queue("[#30363d]Queue is empty — start a download.[/]")
        self._sync_results_table_visibility()

    @on(Input.Submitted, "#main-input")
    def main_input_submitted(self) -> None:
        self.query_one("#btn-go", Button).press()

    def on_key(self, event) -> None:
        """Space toggles row selection (results, library, cast tables)."""
        if event.key != "space":
            return
        event.stop()
        # Results table
        try:
            rt = self.query_one("#results-table", DataTable)
            if rt.has_focus:
                row = rt.cursor_row
                if 0 <= row < len(self._results):
                    if row in self._selected_rows:
                        self._selected_rows.discard(row)
                    else:
                        self._selected_rows.add(row)
                    self._refresh_results_column()
                return
        except Exception:
            pass
        # Library table
        try:
            lt = self.query_one("#lib-table", DataTable)
            if lt.has_focus:
                row = lt.cursor_row
                if 0 <= row < len(self._lib_file_paths):
                    if row in self._lib_selected_rows:
                        self._lib_selected_rows.discard(row)
                    else:
                        self._lib_selected_rows.add(row)
                    self._refresh_lib_column()
                    self._update_lib_status()
                return
        except Exception:
            pass
        # Cast devices table
        try:
            ct = self.query_one("#cast-table", DataTable)
            if ct.has_focus:
                row = ct.cursor_row
                if 0 <= row < len(self._cast_devices):
                    if row in self._cast_selected_rows:
                        self._cast_selected_rows.discard(row)
                    else:
                        self._cast_selected_rows.add(row)
                    self._refresh_cast_column()
        except Exception:
            pass

    def _refresh_results_column(self) -> None:
        table = self.query_one("#results-table", DataTable)
        for i in range(len(self._results)):
            mark = "[#3fb950]✓[/]" if i in self._selected_rows else " "
            try:
                table.update_cell_at((i, 0), mark, update_width=False)
            except Exception:
                pass

    def _refresh_lib_column(self) -> None:
        table = self.query_one("#lib-table", DataTable)
        for i in range(len(self._lib_file_paths)):
            mark = "[#3fb950]✓[/]" if i in self._lib_selected_rows else " "
            try:
                table.update_cell_at((i, 0), mark, update_width=False)
            except Exception:
                pass

    def _refresh_cast_column(self) -> None:
        table = self.query_one("#cast-table", DataTable)
        for i in range(len(self._cast_devices)):
            mark = "[#3fb950]✓[/]" if i in self._cast_selected_rows else " "
            try:
                table.update_cell_at((i, 0), mark, update_width=False)
            except Exception:
                pass

    def _update_lib_status(self) -> None:
        n = len(self._lib_selected_rows)
        st = self.query_one("#lib-status", Static)
        if n == 0:
            st.update("")
            st.remove_class("visible")
        elif n == 1:
            st.update(" ✓  1 file selected   ·   ↑↓ move  ·  Space toggle  ·  ▶ Play  ·  ⬡ Cast")
            st.add_class("visible")
        else:
            st.update(f" ✓  {n} files selected   ·   playlist ready   ·   ▶ Play  ·  ⬡ Cast")
            st.add_class("visible")

    def _populate_results_table(self, items: list[dict], mode: str) -> None:
        self._results = items
        self._result_mode = mode
        self._selected_rows = set()
        table = self.query_one("#results-table", DataTable)
        table.clear()
        for item in items:
            title = item.get("title") or item.get("url") or "?"
            if mode == "search":
                info = item.get("channel") or item.get("uploader") or ""
                dur = item.get("duration")
                if dur:
                    m, s = divmod(int(dur), 60)
                    info = f"{m}:{s:02d}  {info}"
            else:
                info = item.get("url", "")[-40:]
            table.add_row(" ", title[:72], info[:36])
        self._sync_results_table_visibility()

    def _sync_results_table_visibility(self) -> None:
        """When there are no rows, hide the DataTable (no empty grey block) and show the placeholder."""
        n = len(self._results)
        t = self.query_one("#results-table", DataTable)
        ph = self.query_one("#results-table-placeholder", Static)
        if n == 0:
            t.add_class("hidden")
            ph.remove_class("hidden")
            ph.update("[#6e7681]No rows yet — search YouTube or paste a URL.[/]")
        else:
            t.remove_class("hidden")
            ph.add_class("hidden")

    def _focus_results_table_if_any(self) -> None:
        if not self._results:
            return
        try:
            self.set_focus(self.query_one("#results-table", DataTable))
        except Exception:
            pass

    @on(Button.Pressed, "#btn-select-all")
    def results_select_all_btn(self) -> None:
        """Toggle: if all selected → deselect all; otherwise select all."""
        if not self._results:
            return
        if len(self._selected_rows) == len(self._results):
            self._selected_rows = set()
        else:
            self._selected_rows = set(range(len(self._results)))
        self._refresh_results_column()



    def _refresh_settings_static(self) -> None:
        self.query_one("#settings-dl-path", Static).update(
            f"[bold #58a6ff]{self._downloads_dir}[/]"
        )
        if get_installed_ytdlp_version:
            self.query_one("#settings-ytdlp", Static).update(
                f"yt-dlp: [cyan]{get_installed_ytdlp_version()}[/]"
            )
        else:
            self.query_one("#settings-ytdlp", Static).update("yt-dlp: (unavailable)")
        self.query_one("#settings-ffmpeg-status", Static).update(_ffmpeg_status_markup(self._cfg))
        try:
            self.query_one("#settings-ffmpeg-hint", Static).update(_ffmpeg_settings_hint_markup())
            self.query_one("#settings-ffmpeg-input", Input).value = str(
                self._cfg.get("ffmpeg_location") or ""
            )
        except Exception:
            pass
        raw = (self._player or "").strip()
        disp = raw if raw else "system default"
        resolved = self._resolve_player_executable()
        if resolved:
            pline = f"Player: [bold #3fb950]{escape(disp)}[/] → [dim]{escape(resolved)}[/]"
        else:
            pline = (
                f"Player: [bold #3fb950]{escape(disp)}[/] → [dim]"
                "OS default (start / xdg-open / open)[/]"
            )
        self.query_one("#settings-player", Static).update(pline)
        try:
            self.query_one("#settings-player-input", Input).value = self._player
        except Exception:
            pass
        cw = _get_cast_discover_wait(self._cfg)
        try:
            inp = self.query_one("#settings-cast-wait", Input)
            inp.value = str(int(cw)) if cw == int(cw) else f"{cw:g}"
        except Exception:
            pass

    def _log(self, wid: str, msg: str) -> None:
        self.query_one(wid, RichLog).write(msg)

    def _log_queue(self, msg: str) -> None:
        self.query_one("#queue-log", RichLog).write(msg)

    def _refresh_library_table(self) -> None:
        """Refresh library list (respects current mode: downloads jobs vs browse folder)."""
        if self._lib_mode == "browse":
            self._refresh_library_browse()
        else:
            self._refresh_library_downloads()

    def _refresh_library_downloads(self) -> None:
        self._lib_mode = "downloads"
        t = self.query_one("#lib-table", DataTable)
        t.clear()
        self._lib_file_paths = []
        self._lib_selected_rows = set()
        if not self._downloads_dir.is_dir():
            self._update_lib_path_label()
            return
        for job in sorted(self._downloads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not job.is_dir():
                continue
            jid = job.name
            if ".." in jid or "/" in jid:
                continue
            try:
                files = sorted([f for f in job.iterdir() if f.is_file()], key=lambda x: x.name.lower())
                media = [f for f in files if f.suffix.lower() in _LIB_MEDIA_SUFFIXES]
                if not media:
                    continue
                for f in media:
                    full = (self._downloads_dir / jid / f.name).resolve()
                    self._lib_file_paths.append(full)
                    size = f.stat().st_size
                    sz = f"{size/1_048_576:.1f} MB" if size >= 1_048_576 else f"{size//1024} KB"
                    t.add_row(" ", f.name, sz)
            except OSError:
                continue
        self._update_lib_path_label()

    def _refresh_library_browse(self) -> None:
        self._lib_mode = "browse"
        t = self.query_one("#lib-table", DataTable)
        t.clear()
        self._lib_file_paths = []
        self._lib_selected_rows = set()
        bd = self._lib_browse_dir
        if not bd.is_dir():
            self._update_lib_path_label()
            return
        try:
            files = sorted([f for f in bd.iterdir() if f.is_file()], key=lambda x: x.name.lower())
        except OSError:
            self._update_lib_path_label()
            return
        for f in files:
            if f.suffix.lower() not in _LIB_MEDIA_SUFFIXES:
                continue
            full = f.resolve()
            self._lib_file_paths.append(full)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            sz = f"{size/1_048_576:.1f} MB" if size >= 1_048_576 else f"{size//1024} KB"
            t.add_row(" ", f.name, sz)
        self._update_lib_path_label()

    def _update_lib_path_label(self) -> None:
        try:
            st = self.query_one("#lib-browse-path", Static)
        except Exception:
            return
        if self._lib_mode == "downloads":
            st.update(
                f"[#6e7681]Source:[/] download jobs under [bold #58a6ff]{self._downloads_dir}[/]"
            )
        else:
            st.update(f"[#6e7681]Browsing folder:[/] [bold #58a6ff]{self._lib_browse_dir}[/]")

    def _path_under_downloads_rel(self, p: Path) -> str | None:
        try:
            return str(p.resolve().relative_to(self._downloads_dir.resolve()))
        except ValueError:
            return None

    def _clear_container(self, cid: str) -> None:
        self.query_one(f"#{cid}").remove_children()

    def _selected_fmt(self) -> int:
        v = self.query_one("#dl-fmt", Select).value
        try:
            return int(str(v))
        except (TypeError, ValueError):
            return 0

    def _apply_dl_progress(self, d: dict) -> None:
        msg = str(d.get("message") or "")
        frac = d.get("fraction")
        fn = str(d.get("filename") or "")
        title = str(d.get("title") or "").strip()
        parts: list[str] = []
        if title:
            parts.append(f"[bold]{escape(title[:72])}[/]")
        line = msg
        if fn:
            line = f"{line}  |  {escape(fn)}" if line else escape(fn)
        if line:
            parts.append(line)
        if parts:
            self._set_dl_active_line("\n".join(parts)[:220])
        bar = self.query_one("#dl-progress", ProgressBar)
        if isinstance(frac, (int, float)):
            bar.update(progress=min(100.0, max(0.0, float(frac) * 100.0)))

    @work(thread=True, exclusive=True)
    def _download_urls(
        self,
        urls: list[str],
        fmt_idx: int,
        no_pl: bool,
        log_id: str,
        queue_label: str,
    ) -> None:
        preset = get_format_preset(fmt_idx)
        if not preset:
            self.call_from_thread(self._log, log_id, "[red]Invalid format[/]")
            return

        def on_prog(d: dict) -> None:
            self.call_from_thread(self._apply_dl_progress, d)

        spec, extra = preset
        extra_dl = dict(extra)
        ff_loc = _resolve_ffmpeg_for_ytdlp(self._cfg)
        if ff_loc:
            extra_dl["ffmpeg_location"] = ff_loc
        needs_ffmpeg = "+" in spec or any(
            isinstance(x, dict) and str(x.get("key", "")).startswith("FFmpeg")
            for x in (extra_dl.get("postprocessors") or [])
        )
        if needs_ffmpeg and not ff_loc:
            self.call_from_thread(
                self.notify,
                "FFmpeg not found — needed for this format. Install FFmpeg or set its path in Settings (F6).",
            )
            self.call_from_thread(self._log, log_id, _ffmpeg_missing_download_tip_log())
        self.call_from_thread(self._show_dl_progress_wrap)
        self.call_from_thread(
            self._log_queue,
            f"[#d29922]↓[/] [bold #e6edf3]{queue_label}[/] [#6e7681]starting…[/]",
        )
        for u in urls:
            job = str(uuid.uuid4())
            out = self._downloads_dir / job
            out.mkdir(parents=True, exist_ok=True)
            self.call_from_thread(self._log, log_id, f"[cyan]↓[/] {u[:80]}…")
            self.call_from_thread(
                self._apply_dl_progress,
                {"message": f"Starting…  |  {escape(u[:100])}", "fraction": None, "title": "", "filename": ""},
            )
            ok, files, err = run_download(
                u, spec, extra_dl, str(out), no_playlist=no_pl, progress_callback=on_prog
            )
            if ok:
                self.call_from_thread(self._log, log_id, f"[green]OK:[/] {', '.join(files)}")
                self.call_from_thread(
                    self._log_queue,
                    f"[#3fb950]✓[/] [#8b949e]{queue_label[:40]}[/] — [#6e7681]{', '.join(files)[:50]}[/]",
                )
            else:
                err_s = err or "?"
                if "ffmpeg" in err_s.lower():
                    err_s += _ffmpeg_missing_err_suffix()
                self.call_from_thread(self._log, log_id, f"[red]{err_s}[/]")
                self.call_from_thread(
                    self._log_queue,
                    f"[#f85149]✗[/] [#8b949e]{queue_label[:40]}[/] [#6e7681]— {err_s[:120]}[/]",
                )
        self.call_from_thread(self._refresh_library_table)
        self.call_from_thread(self._reset_dl_progress_ui)

    @on(Button.Pressed, "#btn-go")
    def do_go(self) -> None:
        text = self.query_one("#main-input", Input).value.strip()
        if not text:
            self.notify("Type a search query or paste a URL.")
            return
        urlish = text
        if not urlish.lower().startswith("http") and "soundcloud.com/" in urlish.lower():
            urlish = "https://" + urlish.lstrip("/")
        if self._is_url(urlish):
            self._do_url_inspect(urlish)
        else:
            self._do_search(text)

    def _do_search(self, q: str) -> None:
        src = str(self.query_one("#search-src", Select).value)
        site = "SoundCloud" if src == "sc" else "YouTube"
        self.query_one("#input-status", Static).update(
            f"[#6e7681]Searching {site}:[/] [#e6edf3]{q}[/]"
        )
        self._log("#dl-log", f"[#d29922]Search {site}:[/] {q}")
        hits = (
            search_soundcloud(q, max_results=15)
            if src == "sc"
            else search_youtube(q, max_results=15)
        )
        self._populate_results_table(hits, "search")
        self.query_one("#input-status", Static).update(
            f"[#3fb950]{len(hits)} results[/]  [#6e7681]↑↓ · Space / Select all · Play or Download selected[/]"
        )
        self._log("#dl-log", f"[#3fb950]{len(hits)} results[/]")
        self._log_queue(f"[#6e7681]🔍[/] {site[:2]} {q[:34]} ({len(hits)} hits)")
        if hits:
            self._focus_results_table_if_any()
        else:
            try:
                self.set_focus(self.query_one("#main-input", Input))
            except Exception:
                pass

    def _do_url_inspect(self, url: str) -> None:
        self.query_one("#input-status", Static).update("[#6e7681]Inspecting URL…[/]")
        info = extract_url_info(url)
        if not info:
            self.query_one("#input-status", Static).update("[#f85149]URL unreachable.[/]")
            return
        ctype, desc = detect_content_type(info)
        if ctype in ("playlist", "channel"):
            self.query_one("#input-status", Static).update(
                f"[#d29922]{desc}[/] — loading entries…"
            )
            entries, err = fetch_playlist_entries(url, max_entries=200)
            if err:
                self.query_one("#input-status", Static).update(f"[#f85149]{err}[/]")
                self._log("#dl-log", f"[red]{err}[/]")
                return
            self._populate_results_table(entries, "playlist")
            self.query_one("#input-status", Static).update(
                f"[#3fb950]{len(entries)} entries[/]  [#6e7681]↑↓ · Space / Select all · Play or Download selected[/]"
            )
            self._log("#dl-log", f"[#3fb950]{len(entries)}[/] playlist entries loaded.")
            if entries:
                self._focus_results_table_if_any()
            else:
                try:
                    self.set_focus(self.query_one("#main-input", Input))
                except Exception:
                    pass
        else:
            title = info.get("title") or url
            self._populate_results_table([{"title": title, "url": url}], "video")
            self._selected_rows = {0}
            self._refresh_results_column()
            self.query_one("#input-status", Static).update(
                f"[#3fb950]{desc}[/]  [#6e7681]Play or Download selected[/]"
            )
            self._log("#dl-log", f"[#3fb950]Video:[/] {title[:80]}")
            self._focus_results_table_if_any()

    def _download_selected(self, fmt_idx: int) -> None:
        if not self._results:
            self.notify("Run a search or paste a URL first.")
            return
        no_pl = False
        if self._selected_rows:
            indices = sorted(self._selected_rows)
        else:
            row = self.query_one("#results-table", DataTable).cursor_row
            if row < 0 or row >= len(self._results):
                self.notify("Move cursor to a row or select with Space.")
                return
            indices = [row]
        urls = [self._results[i].get("url", "") for i in indices if self._results[i].get("url")]
        if not urls:
            self.notify("No valid URLs in selection.")
            return
        label = self._results[indices[0]].get("title", "")[:36] or f"{len(urls)} items"
        self._download_urls(urls, fmt_idx, no_pl, "#dl-log", label)

    @on(Button.Pressed, "#btn-dl-selected")
    def dl_selected(self) -> None:
        self._download_selected(self._selected_fmt())

    @on(Button.Pressed, "#btn-dl-artwork")
    def dl_artwork_selected(self) -> None:
        self._download_selected(_artwork_preset_index())

    def _results_collect_urls(self, indices: list[int]) -> list[str]:
        urls: list[str] = []
        for i in indices:
            if 0 <= i < len(self._results):
                u = self._results[i].get("url")
                if u:
                    urls.append(str(u))
        return urls

    def _results_play_urls(self, urls: list[str]) -> None:
        if not urls:
            self.notify("No URLs to play.")
            return
        player = self._resolve_player_executable()
        if player is None:
            try:
                self._launch_urls_os_default(urls)
                self.notify(f"Opened {len(urls)} URL(s) with the system default app.")
                self._log("#dl-log", f"[#3fb950]▶[/] (system default) — {len(urls)} URL(s)")
            except Exception as e:
                self.notify(str(e))
            return
        if (
            getattr(sys, "frozen", False)
            and Path(player).name.lower().startswith("mpv")
            and not _locate_ytdlp_binary()
            and any("youtu" in u.lower() or "soundcloud" in u.lower() for u in urls)
        ):
            self.notify(
                "Install system yt-dlp (e.g. pacman -S yt-dlp / apt install yt-dlp) — "
                "mpv needs it to play YouTube/SoundCloud URLs from this bundle."
            )
        try:
            self._launch_player_detached(player, urls)
            self.notify(f"Playlist: {len(urls)} item(s) in {Path(player).name}.")
            self._log("#dl-log", f"[#3fb950]▶[/] {player} — {len(urls)} URL(s) in order")
            if Path(player).name.lower().startswith("mpv"):
                self.notify(
                    "mpv runs in its own window: use the on-screen controls or close the window to stop."
                )
        except FileNotFoundError:
            self.notify(f"Player not found: {player}. Set it in Settings.")
        except Exception as e:
            self.notify(str(e))

    @on(Button.Pressed, "#btn-play-selected")
    def results_play_selected(self) -> None:
        if not self._results:
            self.notify("Run a search or paste a URL first.")
            return
        if self._selected_rows:
            indices = sorted(self._selected_rows)
        else:
            row = self.query_one("#results-table", DataTable).cursor_row
            if row < 0 or row >= len(self._results):
                self.notify("Move cursor to a row or select with Space.")
                return
            indices = [row]
        urls = self._results_collect_urls(indices)
        if not urls:
            self.notify("No valid URLs in selection.")
            return
        self._results_play_urls(urls)

    @on(Button.Pressed, "#btn-lib-refresh")
    def lib_refresh(self) -> None:
        self._refresh_library_table()
        self.notify("Library list refreshed.")

    @on(Button.Pressed, "#btn-lib-browse")
    def lib_browse(self) -> None:
        start = self._lib_browse_dir if self._lib_browse_dir.is_dir() else self._downloads_dir
        self.push_screen(FolderPickerScreen(start), self._after_lib_browse_folder)

    def _after_lib_browse_folder(self, picked: Path | None) -> None:
        if not picked or not picked.is_dir():
            return
        self._lib_browse_dir = picked.resolve()
        self._lib_mode = "browse"
        self._refresh_library_browse()
        self.notify(f"Showing folder: {self._lib_browse_dir}")

    @on(Button.Pressed, "#btn-lib-downloads")
    def lib_downloads_view(self) -> None:
        self._lib_mode = "downloads"
        self._lib_browse_dir = self._downloads_dir
        self._refresh_library_downloads()
        self.notify("Library: downloads folder (per-job subfolders).")

    @on(Button.Pressed, "#btn-lib-ren")
    def lib_rename(self) -> None:
        paths = self._lib_get_selected_paths()
        if len(paths) != 1:
            self.notify("Select exactly one file to rename.")
            return
        p = paths[0]
        if self._lib_mode == "browse":
            self.push_screen(FileRenameScreen(p), self._after_file_rename_browse)
            return
        rel = self._path_under_downloads_rel(p)
        if not rel:
            self.notify("Cannot rename this path.")
            return
        parts = rel.split("/")
        if len(parts) < 2:
            self.notify("Cannot rename this path.")
            return
        job, name = parts[0], parts[-1]
        self.push_screen(RenameScreen(job, name), self._after_rename)

    def _after_rename(self, result: tuple[str, str, str] | None) -> None:
        if not result:
            return
        job, old_n, new_n = result
        p = self._downloads_dir / job / old_n
        dest = self._downloads_dir / job / new_n
        if dest.exists():
            self.notify("That name already exists.")
            return
        try:
            p.rename(dest)
            self._refresh_library_table()
            self.notify("Renamed.")
        except OSError as e:
            self.notify(str(e))

    def _after_file_rename_browse(self, result: tuple[Path, str] | None) -> None:
        if not result:
            return
        p, new_name = result
        dest = p.parent / new_name
        if dest.exists():
            self.notify("That name already exists.")
            return
        try:
            p.rename(dest)
            self._refresh_library_browse()
            self.notify("Renamed.")
        except OSError as e:
            self.notify(str(e))

    @on(Button.Pressed, "#btn-lib-del")
    def lib_del(self) -> None:
        paths = self._lib_get_selected_paths()
        if not paths:
            self.notify("Select one or more files.")
            return
        for p in paths:
            if not p.is_file():
                continue
            try:
                p.unlink()
                jd = p.parent
                if self._lib_mode == "downloads" and jd.is_dir() and jd != self._downloads_dir:
                    if not any(jd.iterdir()):
                        jd.rmdir()
            except OSError as e:
                self.notify(str(e))
                return
        self._refresh_library_table()
        self.notify("Deleted.")

    def _lib_get_selected_paths(self) -> list[Path]:
        """Selected file paths (multi-select, or cursor row if none checked)."""
        if self._lib_selected_rows:
            indices = sorted(self._lib_selected_rows)
        else:
            table = self.query_one("#lib-table", DataTable)
            row = table.cursor_row
            if row < 0 or row >= len(self._lib_file_paths):
                return []
            indices = [row]
        return [self._lib_file_paths[i] for i in indices if i < len(self._lib_file_paths)]

    @on(Button.Pressed, "#btn-lib-play")
    def lib_play(self) -> None:
        paths = self._lib_get_selected_paths()
        if not paths:
            self.notify("Select one or more files.")
            return
        player = self._resolve_player_executable()
        if player is None:
            try:
                self._launch_paths_os_default(paths)
                names = ", ".join(p.name for p in paths[:3])
                if len(paths) > 3:
                    names += f" (+{len(paths)-3} more)"
                self.notify(f"Opened with system default: {names}")
                self._log(
                    "#log-lib",
                    f"[#3fb950]▶[/] (system default) {' '.join(str(p.name) for p in paths[:2])}",
                )
            except Exception as e:
                self.notify(str(e))
            return
        try:
            self._launch_player_detached(player, paths)
            names = ", ".join(p.name for p in paths[:3])
            if len(paths) > 3:
                names += f" (+{len(paths)-3} more)"
            self.notify(f"Playing: {names}")
            self._log("#log-lib", f"[#3fb950]▶[/] {player} {' '.join(str(p.name) for p in paths[:2])}")
            if Path(player).name.lower().startswith("mpv"):
                self.notify(
                    "mpv runs in its own window: use the on-screen controls or close the window to stop."
                )
        except FileNotFoundError:
            self.notify(f"Player not found: {player}. Set it in Settings.")
        except Exception as e:
            self.notify(str(e))

    @on(Button.Pressed, "#btn-lib-cast")
    def lib_prepare_cast(self) -> None:
        paths = self._lib_get_selected_paths()
        if not paths:
            self.notify("Select one or more files.")
            return
        self._files_to_cast = []
        for p in paths:
            rel = self._path_under_downloads_rel(p)
            if rel is None:
                self.notify(
                    "Cast only streams files inside your downloads folder. "
                    'Use the "Downloads" view or choose a folder under that path.',
                    severity="warning",
                )
                return
            self._files_to_cast.append(rel)
        if any(r.lower().endswith(".mkv") for r in self._files_to_cast):
            self.notify("MKV may fail on Chromecast; MP4 safer.", severity="warning")
        n = len(self._files_to_cast)
        hint = (
            f"[#3fb950]Ready:[/] {n} file(s) — opening Cast tab, HTTP server and device scan."
        )
        self.query_one("#lib-cast-hint", Static).update(hint)
        self.notify(f"{n} file(s) ready — switching to Cast…")
        # Defer: Chromecast discovery blocks the event loop; switching tab in the same
        # handler often never paints. Next tick: activate Cast, HTTP, then discover in a worker.
        self.set_timer(0, self._lib_cast_open_cast_tab_and_setup)

    def _lib_cast_open_cast_tab_and_setup(self) -> None:
        try:
            tabs = self.query_one("#tabs", TabbedContent)
            tabs.active = "tab-cast"
        except Exception as e:
            self.notify(f"Could not switch to Cast tab: {e}", severity="error")
            return
        self._log(
            "#log-cast",
            "[#58a6ff]Library → Cast:[/] Cast tab · starting HTTP server · discovering devices…",
        )
        try:
            self._cast_start_http_impl()
        except Exception as e:
            self.notify(f"Cast HTTP server: {e}", severity="error")
            return
        wait_s = self._cast_discover_wait_seconds()
        self.run_worker(
            lambda w=wait_s: self._cast_discover_in_background(w),
            name="cast-discover",
            thread=True,
            exclusive=False,
            exit_on_error=False,
        )
        # Focus the device table after the Cast pane is visible (timer 0 is not always enough).
        self.set_timer(0.08, self._defer_focus_cast_table)

    def _defer_focus_cast_table(self) -> None:
        try:
            self.set_focus(self.query_one("#cast-table", DataTable))
        except Exception:
            pass

    def _cast_discover_wait_seconds(self) -> float:
        return _get_cast_discover_wait(self._cfg)

    def _cast_discover_in_background(self, wait_s: float) -> None:
        """Runs in a worker thread; never touch widgets here directly."""
        self.call_from_thread(
            self._log,
            "#log-cast",
            f"Scanning Chromecasts ({wait_s}s)…",
        )
        try:
            devices = discover_chromecasts(wait_s=wait_s)
        except Exception as e:
            self.call_from_thread(
                self._log,
                "#log-cast",
                f"[red]Discovery failed: {e}[/]",
            )
            return
        self.call_from_thread(self._apply_cast_devices_table, devices)

    def _apply_cast_devices_table(self, devices: list) -> None:
        self._cast_devices = list(devices)
        self._cast_selected_rows = set()
        ct = self.query_one("#cast-table", DataTable)
        ct.clear()
        for c in self._cast_devices:
            info = c.cast_info
            host = getattr(info, "host", "?")
            port = getattr(info, "port", None) or 8009
            ct.add_row(
                " ",
                info.friendly_name or "—",
                info.model_name or "—",
                f"{host}:{port}",
            )
        self._log("#log-cast", f"[green]{len(self._cast_devices)} device(s).[/]")

    def _cast_start_http_impl(self) -> None:
        """Start the Flask HTTP server for /media/… (used by Cast tab and Library → Cast)."""
        self._cast_port = start_cast_server(port=0)
        ip = get_lan_ip()
        self.query_one("#cast-srv-status", Static).update(
            f"[#3fb950]● HTTP[/]  http://{ip}:{self._cast_port}/media/…"
        )
        self._log("#log-cast", f"Port {self._cast_port}, LAN {ip}")

    def _cast_discover_impl(self) -> None:
        """Fill #cast-table from mDNS on the main thread (wait from Settings → cast_discover_wait)."""
        wait_s = self._cast_discover_wait_seconds()
        self._log("#log-cast", f"Scanning Chromecasts ({wait_s}s)…")
        self._cast_devices = discover_chromecasts(wait_s=wait_s)
        self._apply_cast_devices_table(self._cast_devices)

    @on(Button.Pressed, "#btn-cast-http")
    def cast_start_http(self) -> None:
        self._cast_start_http_impl()

    @on(Button.Pressed, "#btn-cast-disc")
    def cast_discover(self) -> None:
        wait_s = self._cast_discover_wait_seconds()
        self.run_worker(
            lambda w=wait_s: self._cast_discover_in_background(w),
            name="cast-discover-btn",
            thread=True,
            exclusive=False,
            exit_on_error=False,
        )

    def _cast_selected_index(self) -> int | None:
        table = self.query_one("#cast-table", DataTable)
        coord = table.cursor_coordinate
        if coord is None or coord.row < 0:
            return None
        if coord.row >= len(self._cast_devices):
            return None
        return coord.row

    def _cast_resolve_targets(self) -> list:
        """Resolve Cast targets: checked rows first, else name filter, else cursor row."""
        if not self._cast_devices:
            return []
        if self._cast_selected_rows:
            idxs = sorted(self._cast_selected_rows)
            return [self._cast_devices[i] for i in idxs if 0 <= i < len(self._cast_devices)]
        name_f = self.query_one("#cast-name-filter", Input).value.strip().lower()
        if name_f:
            return [
                c
                for c in self._cast_devices
                if name_f in (c.cast_info.friendly_name or "").lower()
            ]
        idx = self._cast_selected_index()
        if idx is not None:
            return [self._cast_devices[idx]]
        return []

    def _cast_play_targets(self, targets: list) -> None:
        if not self._files_to_cast:
            self.notify("Library → select files → Cast.")
            return
        if self._cast_port <= 0:
            self.notify("Start the HTTP server first.")
            return
        if not targets:
            return
        ip = get_lan_ip()
        rel = self._files_to_cast[0]
        url = media_url(rel, ip, self._cast_port)
        name = Path(rel).name
        mime = guess_mime_for_cast(name)
        if len(self._files_to_cast) > 1:
            self.notify(
                f"Casting {len(self._files_to_cast)} files — only first is sent to Chromecast.",
                severity="warning",
            )
        self._log("#log-cast", f"{url}\n{mime}")
        try:
            if len(targets) == 1:
                play_url(targets[0], url, mime)
            else:
                play_url_to_casts(targets, url, mime)
            self.notify(f"Casting started on {len(targets)} device(s).")
        except Exception as e:
            self._log("#log-cast", f"[red]{e}[/]")
            self.notify(f"Error: {e}", severity="error")

    @on(Button.Pressed, "#btn-cast-play")
    def cast_play(self) -> None:
        if not self._cast_devices:
            self.notify("No Chromecasts in the list — wait for scan or press Discover.")
            return
        targets = self._cast_resolve_targets()
        if not targets:
            nf = self.query_one("#cast-name-filter", Input).value.strip().lower()
            if nf and not self._cast_selected_rows:
                self.notify("No device matches the name filter.")
            else:
                self.notify(
                    "Select row(s) with Space, or move cursor to one device, or use name filter."
                )
            return
        self._cast_play_targets(targets)

    @on(Button.Pressed, "#btn-cast-play-all")
    def cast_play_all(self) -> None:
        if not self._cast_devices:
            self.notify("No devices — run Discover or use Library → Cast (auto scan).")
            return
        self._cast_play_targets(list(self._cast_devices))

    @work(thread=True, exclusive=True)
    def _stop_last_cast_work(self) -> None:
        ok, msg = stop_last_cast()

        def show() -> None:
            self.notify(msg)
            self._log("#log-cast", f"[green]{msg}[/]" if ok else f"[yellow]{msg}[/]")

        self.call_from_thread(show)

    @on(Button.Pressed, "#btn-cast-stop-last")
    def cast_stop_last_btn(self) -> None:
        self._stop_last_cast_work()

    @on(Button.Pressed, "#btn-cast-stop")
    def cast_stop_btn(self) -> None:
        if not self._cast_devices:
            self.notify("Discover Chromecasts first.")
            return
        if self._cast_selected_rows:
            for i in sorted(self._cast_selected_rows):
                if 0 <= i < len(self._cast_devices):
                    try:
                        stop_projection(self._cast_devices[i])
                    except Exception as e:
                        self._log("#log-cast", f"[yellow]{e}[/]")
            self.notify("Stop sent to selected device(s).")
            return
        idx = self._cast_selected_index()
        if idx is None:
            self.notify("Select a device row (or Space to select several).")
            return
        try:
            stop_projection(self._cast_devices[idx])
            self.notify("Casting stopped.")
        except Exception as e:
            self.notify(str(e))

    @on(Button.Pressed, "#btn-settings-browse-dl")
    def settings_browse_dl(self) -> None:
        self.push_screen(FolderPickerScreen(self._downloads_dir), self._after_pick_dl)

    def _after_pick_dl(self, result: Path | None) -> None:
        if not result:
            return
        self._downloads_dir = result
        self._cfg["downloads_dir"] = str(result)
        _save_config(self._cfg)
        self._refresh_settings_static()
        self._refresh_library_table()
        self.notify(f"Downloads folder: {result}")

    @on(Button.Pressed, "#btn-settings-reset-dl")
    def settings_reset_dl(self) -> None:
        dl = Path.home() / "Downloads"
        dl.mkdir(parents=True, exist_ok=True)
        self._downloads_dir = dl
        self._cfg.pop("downloads_dir", None)
        _save_config(self._cfg)
        self._refresh_settings_static()
        self._refresh_library_table()
        self.notify("Reset to ~/Downloads")

    def _save_ffmpeg_setting_from_input(self, *, notify: bool = True, do_refresh: bool = True) -> bool:
        val = self.query_one("#settings-ffmpeg-input", Input).value.strip()
        if not val:
            self._cfg.pop("ffmpeg_location", None)
            _save_config(self._cfg)
            if do_refresh:
                self._refresh_settings_static()
            if notify:
                self.notify("FFmpeg path cleared (use PATH auto-detect).")
            return True
        p = Path(val)
        ok = False
        if p.is_file() and p.name.lower() in ("ffmpeg", "ffmpeg.exe"):
            ok = True
        elif p.is_dir():
            exe = p / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if exe.is_file():
                ok = True
        if not ok:
            if notify:
                self.notify("Invalid: choose the ffmpeg binary or a folder that contains it.")
            return False
        self._cfg["ffmpeg_location"] = val
        _save_config(self._cfg)
        if do_refresh:
            self._refresh_settings_static()
        if notify:
            self.notify("FFmpeg location saved.")
        return True

    @on(Button.Pressed, "#btn-settings-save-ffmpeg")
    def settings_save_ffmpeg(self) -> None:
        self._save_ffmpeg_setting_from_input()

    @on(Input.Submitted, "#settings-ffmpeg-input")
    def settings_ffmpeg_input_submitted(self) -> None:
        self._save_ffmpeg_setting_from_input()

    @on(Button.Pressed, "#btn-settings-clear-ffmpeg")
    def settings_clear_ffmpeg(self) -> None:
        self._cfg.pop("ffmpeg_location", None)
        _save_config(self._cfg)
        try:
            self.query_one("#settings-ffmpeg-input", Input).value = ""
        except Exception:
            pass
        self._refresh_settings_static()
        self.notify("FFmpeg override cleared.")

    def _save_player_setting_from_input(self, *, notify: bool = True, do_refresh: bool = True) -> None:
        val = self.query_one("#settings-player-input", Input).value.strip()
        if val.lower() in ("auto", "default", "os", "system"):
            val = ""
        self._player = val
        if val:
            self._cfg["player"] = val
        else:
            self._cfg.pop("player", None)
        _save_config(self._cfg)
        if do_refresh:
            self._refresh_settings_static()
        if notify:
            msg = "system default (URLs/files open with the OS handler)" if not val else val
            self.notify(f"Player saved: {msg}")

    @on(Button.Pressed, "#btn-settings-save-player")
    def settings_save_player(self) -> None:
        self._save_player_setting_from_input()

    @on(Input.Submitted, "#settings-player-input")
    def settings_player_input_submitted(self) -> None:
        self._save_player_setting_from_input()

    def _save_cast_wait_from_input(self, *, notify: bool = True, do_refresh: bool = True) -> bool:
        raw = self.query_one("#settings-cast-wait", Input).value.strip()
        try:
            v = float(raw) if raw else _get_cast_discover_wait(self._cfg)
        except ValueError:
            if notify:
                self.notify("Enter a number (seconds).")
            return False
        v = max(0.5, min(120.0, v))
        self._cfg["cast_discover_wait"] = v
        _save_config(self._cfg)
        if do_refresh:
            self._refresh_settings_static()
        if notify:
            self.notify(f"Chromecast discovery wait: {v}s")
        return True

    @on(Button.Pressed, "#btn-settings-save-cast-wait")
    def settings_save_cast_wait(self) -> None:
        self._save_cast_wait_from_input()

    @on(Input.Submitted, "#settings-cast-wait")
    def settings_cast_wait_submitted(self) -> None:
        self._save_cast_wait_from_input()

    @on(Button.Pressed, "#btn-settings-save-all-bottom")
    @on(Button.Pressed, "#btn-settings-save-all")
    def settings_save_all(self) -> None:
        ok_ff = self._save_ffmpeg_setting_from_input(notify=False, do_refresh=False)
        self._save_player_setting_from_input(notify=False, do_refresh=False)
        ok_cast = self._save_cast_wait_from_input(notify=False, do_refresh=False)
        self._refresh_settings_static()
        if ok_ff and ok_cast:
            self.notify("All settings saved (FFmpeg, player, Cast wait).")
        elif not ok_ff:
            self.notify("Fix FFmpeg path (folder with ffmpeg or path to binary), or clear the field.")
        else:
            self.notify("Saved. Fix Chromecast wait (enter seconds).")

    @on(Button.Pressed, "#btn-settings-pypi")
    def settings_pypi(self) -> None:
        log = self.query_one("#log-settings", RichLog)
        if not get_installed_ytdlp_version or not fetch_pypi_latest_ytdlp_version:
            log.write("[red]ytdlp_update not available.[/]")
            return
        inst = get_installed_ytdlp_version()
        latest = fetch_pypi_latest_ytdlp_version()
        log.write(f"Installed: {inst}")
        log.write(f"PyPI latest: {latest or '?'}")

    def _press(self, btn_id: str) -> None:
        try:
            self.query_one(f"#{btn_id}", Button).press()
        except Exception:
            pass

    def _go_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id

    # ── Tab navigation (Ctrl+1…5) ─────────────────────────────────────────
    def action_tab_home(self) -> None:     self._go_tab("tab-home")
    def action_tab_download(self) -> None: self._go_tab("tab-download")
    def action_tab_library(self) -> None:  self._go_tab("tab-lib")
    def action_tab_cast(self) -> None:     self._go_tab("tab-cast")
    def action_tab_settings(self) -> None: self._go_tab("tab-settings")

    # Compatibilitate internă
    def action_focus_cast(self) -> None:     self._go_tab("tab-cast")
    def action_focus_download(self) -> None: self._go_tab("tab-download")
    def action_focus_library(self) -> None:  self._go_tab("tab-lib")

    # ── Search&Download ───────────────────────────────────────────────────
    def action_go(self) -> None:          self._press("btn-go")
    def action_select_all(self) -> None:  self._press("btn-select-all")
    def action_play_sel(self) -> None:
        tab = self.query_one("#tabs", TabbedContent).active
        self._press("btn-lib-play" if tab == "tab-lib" else "btn-play-selected")
    def action_download_sel(self) -> None: self._press("btn-dl-selected")
    def action_download_artwork_sel(self) -> None: self._press("btn-dl-artwork")

    # ── Library ───────────────────────────────────────────────────────────
    def action_lib_refresh_kb(self) -> None: self._press("btn-lib-refresh")
    def action_lib_browse_kb(self) -> None:    self._press("btn-lib-browse")
    def action_lib_downloads_kb(self) -> None: self._press("btn-lib-downloads")
    def action_lib_cast_kb(self) -> None:    self._press("btn-lib-cast")
    def action_lib_rename_kb(self) -> None:  self._press("btn-lib-ren")
    def action_lib_delete_kb(self) -> None:  self._press("btn-lib-del")

    # ── Cast ─────────────────────────────────────────────────────────────
    def action_cast_http_kb(self) -> None:      self._press("btn-cast-http")
    def action_cast_discover_kb(self) -> None:  self._press("btn-cast-disc")
    def action_cast_start_kb(self) -> None:     self._press("btn-cast-play")
    def action_cast_all_kb(self) -> None:       self._press("btn-cast-play-all")
    def action_cast_stop_sel_kb(self) -> None:  self._press("btn-cast-stop")
    def action_cast_stop_last_kb(self) -> None:  self._press("btn-cast-stop-last")

    # ── Settings ─────────────────────────────────────────────────────────
    def action_settings_browse_kb(self) -> None: self._press("btn-settings-browse-dl")

    def action_quit(self) -> None:
        self.exit()


def main() -> None:
    DLPulseTextualApp().run()


if __name__ == "__main__":
    main()