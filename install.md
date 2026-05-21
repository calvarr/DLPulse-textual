# Installing DLPulse Textual

## Legal notice

**DLPulse** is open-source software for educational purposes, technical research, and personal media library management. It wraps yt-dlp/ffmpeg; the author does not host copyrighted media. You must comply with copyright laws and platform terms of service; personal offline use only; provided as-is without warranty. Not affiliated with streaming platforms. Full text: [LEGAL.md](../LEGAL.md).

---

**Project page:** https://github.com/calvarr/DLPulse-textual  
**Latest release:** https://github.com/calvarr/DLPulse-textual/releases/
**License:** MIT © 2026 calvarr

---

## Table of Contents

- [What you need before starting](#what-you-need-before-starting)
- [Linux](#linux)
  - [Arch / Manjaro](#arch--manjaro)
  - [Ubuntu / Debian](#ubuntu--debian)
  - [Fedora](#fedora)
- [macOS](#macos)
- [Windows](#windows)
- [Install DLPulse Textual](#install-dlpulse-textual)
  - [Option A — Download release ZIP](#option-a--download-release-zip)
  - [Option B — Clone from GitHub](#option-b--clone-from-github)
- [Configuration](#configuration)
- [Verify the installation](#verify-the-installation)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Troubleshooting](#troubleshooting)

---

## What you need before starting

| Requirement | Why |
|---|---|
| **Python 3.11+** | Runtime |
| **ffmpeg** | Merging video+audio streams and MP3 conversion (used by yt-dlp) |
| **mpv** or another player | Playback from the Library tab |
| **Git** | Only if installing via clone (Option B) |

---

## Linux

### Arch / Manjaro

```bash
# Check Python version first (must be 3.11+)
python3 --version

# Install everything needed
sudo pacman -S python ffmpeg mpv git
```

---

### Ubuntu / Debian

**Ubuntu 22.04** ships Python 3.10 — you need to add a PPA first:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-pip
```

**Ubuntu 23.04+ and Debian 12+** already have Python 3.11:

```bash
sudo apt install python3 python3-venv python3-pip
```

Then install ffmpeg and mpv:

```bash
sudo apt install ffmpeg mpv git
```

---

### Fedora

ffmpeg is not in the default Fedora repos. Enable RPM Fusion first:

```bash
sudo dnf install \
  https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
  https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm

sudo dnf install python3 python3-pip ffmpeg mpv git
```

---

## macOS

**1. Install Homebrew** (if not already installed):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**2. Install dependencies:**

```bash
brew install python ffmpeg mpv git
```

Alternatively, use **IINA** as your player (native macOS feel):

```bash
brew install --cask iina
```

Then set it in DLPulse Textual → Settings → Media Player:
```
/Applications/IINA.app/Contents/MacOS/iina-cli
```

---

## Windows

### Step 1 — Python 3.11+

Download from: https://www.python.org/downloads/windows/

> ⚠️ During setup, check **"Add Python to PATH"** before clicking Install.

Verify in PowerShell:

```powershell
python --version
```

### Step 2 — ffmpeg

```powershell
winget install ffmpeg
```

Manual install (if winget is unavailable):
1. Download from https://ffmpeg.org/download.html → Windows → **gyan.dev**
2. Extract to `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to your system PATH:
   - Search → *Edit the system environment variables* → Environment Variables → Path → New → `C:\ffmpeg\bin`
4. Open a new terminal and verify:

```powershell
ffmpeg -version
```

### Step 3 — mpv or VLC

```powershell
winget install mpv
# or
winget install VideoLAN.VLC
```

### Step 4 — Git

```powershell
winget install Git.Git
```

---

## Install DLPulse Textual

### Option A — Download release ZIP

This is the simplest way — no Git required.

1. Go to: https://github.com/calvarr/DLPulse-textual/releases/tag/v1.0.6
2. Under **Assets**, download `Source code (zip)` or `Source code (tar.gz)`
3. Extract the archive and open a terminal in the extracted folder

Create a virtual environment and install dependencies:

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the application:

```bash
python3 dlpulse_textual.py
```

---

### Option B — Clone from GitHub

```bash
git clone https://github.com/calvarr/DLPulse-textual.git
cd DLPulse-textual
```

Create and activate a virtual environment:

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run:

```bash
python3 dlpulse_textual.py
```

---

### Make it a global command (optional)

**Linux / macOS:**

```bash
chmod +x dlpulse_textual.py

# Symlink (activate the venv manually before running)
ln -sf "$(pwd)/dlpulse_textual.py" ~/.local/bin/dlpulse-textual

# Add to PATH if needed
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

For zsh (macOS default):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Windows — create a `.bat` launcher:**

Create `dlpulse-textual.bat` in a folder already in your PATH:

```bat
@echo off
call C:\path\to\DLPulse-textual\.venv\Scripts\activate.bat
python C:\path\to\DLPulse-textual\dlpulse_textual.py %*
```

---

## Configuration

DLPulse Textual stores its config at:

| Platform | Path |
|---|---|
| Linux | `~/.config/dlpulse/config.json` |
| macOS | `~/.config/dlpulse/config.json` |
| Windows | `%APPDATA%\dlpulse\config.json` |

The file is created automatically on first run. You can also edit all settings directly inside the app from the **Settings tab** (no need to touch the file manually).

**Settings available in the app:**

| Setting | Description |
|---|---|
| Downloads folder | Where files are saved. Default: `~/Downloads` |
| Media player | Command used to play files — e.g. `mpv`, `vlc` |
| Chromecast discovery wait | Seconds to scan for Cast devices on your network |

**Example `config.json`:**

```json
{
  "downloads_dir": "/home/user/Downloads",
  "player": "mpv",
  "cast_discovery_wait": 5
}
```

---

## Verify the installation

```bash
# Activate the virtual environment first
source .venv/bin/activate        # Linux / macOS
.venv\Scripts\Activate.ps1       # Windows

# Check Python version
python3 --version                # must be 3.11+

# Check ffmpeg
ffmpeg -version

# Check yt-dlp
yt-dlp --version

# Launch DLPulse Textual
python3 dlpulse_textual.py
```

The application should open a full-screen TUI in your terminal. If it opens correctly, the installation is complete.

---

## Updating

**Update DLPulse Textual (clone install):**

```bash
cd DLPulse-textual
git pull
pip install -r requirements.txt
```

If installed via ZIP, download the latest release from:
https://github.com/calvarr/DLPulse-textual/releases

**Update yt-dlp** *(do this regularly — site extractors change often)*:

```bash
pip install --upgrade yt-dlp
```

**Update ffmpeg:**

```bash
# Arch / Manjaro
sudo pacman -Syu ffmpeg

# Ubuntu / Debian
sudo apt upgrade ffmpeg

# macOS
brew upgrade ffmpeg

# Windows
winget upgrade ffmpeg
```

---

## Uninstalling

**Remove DLPulse Textual:**

```bash
# Delete the folder
rm -rf DLPulse-textual

# Remove global symlink if created (Linux / macOS)
rm ~/.local/bin/dlpulse-textual
```

**Remove config:**

```bash
# Linux / macOS
rm -rf ~/.config/dlpulse

# Windows (PowerShell)
Remove-Item -Recurse "$env:APPDATA\dlpulse"
```

---

## Troubleshooting

**Terminal shows garbled characters or boxes instead of the UI**

DLPulse Textual requires a terminal with UTF-8 and 256-color support.

- Linux: XFCE Terminal, GNOME Terminal, Kitty, Alacritty
- macOS: iTerm2 or built-in Terminal (macOS 12+)
- Windows: **Windows Terminal** (not the old `cmd.exe`)

Set your terminal to UTF-8 if needed:

```bash
export LANG=en_US.UTF-8
export TERM=xterm-256color
```

**`ModuleNotFoundError: No module named 'textual'`**

The virtual environment is not activated:

```bash
source .venv/bin/activate       # Linux / macOS
.venv\Scripts\Activate.ps1      # Windows
```

Or dependencies were not installed:

```bash
pip install -r requirements.txt
```

**`RuntimeError: DLPulse_textual expects the DLPulse repo at …`**

This happens when the folder layout does not match what `path_setup.py` expects. Make sure you run `dlpulse_textual.py` from inside the cloned `DLPulse-textual` folder and that the sibling `yt/flet_app` folder exists next to it.

**Downloads fail or MP3 conversion does not work**

ffmpeg is missing or not in PATH. Install it for your platform (see above) and verify:

```bash
ffmpeg -version
```

**yt-dlp errors / videos fail to download**

yt-dlp may be outdated:

```bash
pip install --upgrade yt-dlp
```

**Chromecast device not found**

- Make sure your computer and Chromecast are on the same Wi-Fi network
- Increase the discovery wait in **Settings → Chromecast discovery wait**
- Make sure no firewall is blocking UDP port 5353 (mDNS)

---

MIT License © 2026 calvarr  
☕ [buymeacoffee.com/medcodex](https://buymeacoffee.com/medcodex)