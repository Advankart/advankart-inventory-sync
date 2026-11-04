"""Self-update from GitHub Releases.

The app checks https://api.github.com/repos/<GITHUB_REPO>/releases/latest. If the release's version
is newer, "Update now" downloads the release zip, backs up the current code to data/backups/, copies
the new files over the app folder (never touching data/ or venv/), installs any new requirements and
asks the launcher to restart the app (exit code 3).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

from version import VERSION, GITHUB_REPO

APP_DIR = os.path.dirname(os.path.abspath(__file__))
KEEP = {"data", "venv", ".git", ".github", "__pycache__"}
RESTART_CODE = 3
_cache = {"at": 0, "info": None}


def vtuple(v):
    nums = re.findall(r"\d+", str(v or "0"))
    return tuple(int(n) for n in (nums + ["0", "0", "0"])[:3])


def check(force=False):
    if not force and _cache["info"] and time.time() - _cache["at"] < 3600:
        return _cache["info"]
    api = os.environ.get("INVSYNC_UPDATE_API", "https://api.github.com")
    r = requests.get(f"{api}/repos/{GITHUB_REPO}/releases/latest",
                     headers={"Accept": "application/vnd.github+json", "User-Agent": "AdvankartInventory"}, timeout=20)
    if r.status_code == 404:
        info = {"current": VERSION, "latest": VERSION, "available": False, "notes": "", "repo": GITHUB_REPO,
                "message": "No releases published yet."}
    else:
        r.raise_for_status()
        rel = r.json()
        latest = rel.get("tag_name", "").lstrip("vV")
        asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".zip")), None)
        info = {"current": VERSION, "latest": latest, "available": vtuple(latest) > vtuple(VERSION),
                "notes": rel.get("body") or "", "name": rel.get("name") or rel.get("tag_name"),
                "published": rel.get("published_at", ""), "url": rel.get("html_url"),
                "download": asset["browser_download_url"] if asset else rel.get("zipball_url"), "repo": GITHUB_REPO}
    _cache.update(at=time.time(), info=info)
    return info


def _find_root(folder):
    for root, dirs, files in os.walk(folder):
        if "app.py" in files and "version.py" in files:
            return root
    raise RuntimeError("Downloaded release does not look like Inventory Sync (app.py missing).")


def apply(progress=lambda m: None):
    info = check(force=True)
    if not info.get("available"):
        return {"updated": False, "message": f"You already have the latest version ({VERSION})."}
    tmp = tempfile.mkdtemp(prefix="invsync-upd-")
    try:
        progress(f"Downloading version {info['latest']}…")
        zpath = os.path.join(tmp, "release.zip")
        with requests.get(info["download"], stream=True, timeout=120,
                          headers={"User-Agent": "AdvankartInventory"}) as r:
            r.raise_for_status()
            with open(zpath, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
        progress("Unpacking…")
        ex = os.path.join(tmp, "x")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(ex)
        src = _find_root(ex)

        progress("Backing up current version…")
        backup = os.path.join(APP_DIR, "data", "backups", f"v{VERSION}-{time.strftime('%Y%m%d-%H%M%S')}")
        os.makedirs(backup, exist_ok=True)
        for name in os.listdir(APP_DIR):
            if name in KEEP:
                continue
            p = os.path.join(APP_DIR, name)
            (shutil.copytree if os.path.isdir(p) else shutil.copy2)(p, os.path.join(backup, name))

        old_req = _read(os.path.join(APP_DIR, "requirements.txt"))
        progress("Installing new files…")
        for name in os.listdir(src):
            if name in KEEP:
                continue
            s, d = os.path.join(src, name), os.path.join(APP_DIR, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

        if _read(os.path.join(APP_DIR, "requirements.txt")) != old_req:
            progress("Installing new components…")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                            os.path.join(APP_DIR, "requirements.txt")], check=False)
        return {"updated": True, "from": VERSION, "to": info["latest"], "backup": backup}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def restart_soon(delay=1.5):
    """Exit with RESTART_CODE; start.bat / start-mac-linux.sh relaunch the app on this code."""
    import threading

    def _go():
        time.sleep(delay)
        if not os.environ.get("INVSYNC_LAUNCHER"):
            # started directly (not via start.bat) — launch a fresh copy ourselves
            subprocess.Popen([sys.executable, os.path.join(APP_DIR, "app.py"), "--no-browser", "--wait-port"],
                             cwd=APP_DIR)
        os._exit(RESTART_CODE)

    threading.Thread(target=_go, daemon=True).start()
