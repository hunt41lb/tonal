"""Self-update via GitHub Releases — check for, download, and install new versions.

The update channel is this project's GitHub Releases feed and nothing else (see
constants.UPDATE_API_URL). /releases/latest is used deliberately: GitHub
excludes drafts and pre-releases from that endpoint, so a beta can be published
for testers without every installed copy offering it as an update.

Version ordering is delegated to `dpkg --compare-versions` rather than a
hand-rolled parser: dpkg is guaranteed present on any system that installed the
.deb, and it already orders this project's scheme correctly
(1.0.2 < 1.0.4-u5 < 1.0.4-u6 < 1.0.4-u10 < 1.0.5). Historical release tags mix
"v" and "v." prefixes (v1.0.1, v.1.0.2), so tags are normalized before compare.

Everything here is synchronous and GTK-free; callers (pages/status.py) run it
on worker threads and marshal results back with GLib.idle_add — the same split
pipewire_ctl uses.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

from constants import APP_VERSION, UPDATE_API_URL

log = logging.getLogger("tonal.updater")

_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"tonal-updater/{APP_VERSION}",
}


def _normalize_tag(tag):
    """Release tag → bare version: 'v1.0.4-u6' → '1.0.4-u6', 'v.1.0.2' → '1.0.2'."""
    tag = tag.strip()
    if tag[:1] in ("v", "V"):
        tag = tag[1:]
    if tag[:1] == ".":
        tag = tag[1:]
    return tag


def _is_newer(candidate, current):
    """True when `candidate` sorts after `current` in Debian version order.

    Falls back to False when dpkg is unavailable — a non-Debian system cannot
    install the .deb anyway, so offering the update would be a dead end.
    """
    try:
        r = subprocess.run(["dpkg", "--compare-versions", candidate, "gt", current],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("dpkg unavailable — cannot order versions, reporting no update")
        return False


def check_for_update(timeout=10):
    """Query GitHub for the latest release. Returns (ok, result).

    ok=True  → result is a dict:
        current, version, tag, available (bool), notes, page_url,
        asset_name, asset_url, asset_size.
        asset_url is "" when the release has no .deb attached — the UI should
        surface that rather than treating it as up to date.
    ok=False → result is a short error string safe to show in a toast.
    """
    req = urllib.request.Request(UPDATE_API_URL, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "No releases published on GitHub yet"
        if e.code == 403:
            return False, "GitHub rate limit reached — try again in an hour"
        return False, f"GitHub returned HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"Network error: {getattr(e, 'reason', e)}"
    except (TimeoutError, json.JSONDecodeError, OSError) as e:
        return False, f"Update check failed: {e}"

    tag = (data.get("tag_name") or "").strip()
    version = _normalize_tag(tag)
    if not version:
        return False, "Latest release has no version tag"

    debs = [a for a in data.get("assets", []) if a.get("name", "").endswith(".deb")]
    # Prefer the canonically named package if several .debs are ever attached
    asset = next((a for a in debs if a.get("name") == f"tonal_{version}_all.deb"),
                 debs[0] if debs else None)

    info = {
        "current": APP_VERSION,
        "version": version,
        "tag": tag,
        "available": _is_newer(version, APP_VERSION),
        "notes": (data.get("body") or "").strip(),
        "page_url": data.get("html_url", ""),
        "asset_name": asset.get("name", "") if asset else "",
        "asset_url": asset.get("browser_download_url", "") if asset else "",
        "asset_size": int(asset.get("size") or 0) if asset else 0,
    }
    log.info("Update check: current %s, latest %s → %s", APP_VERSION, version,
             "update available" if info["available"] else "up to date")
    return True, info


def download_update(asset_url, expected_size=0, timeout=120):
    """Download the release .deb to a temp file. Returns (ok, path_or_error).

    The byte count is verified against the size the API reported for the
    asset, so a truncated download can never reach dpkg.
    """
    if not asset_url:
        return False, "This release has no .deb package attached"

    req = urllib.request.Request(asset_url, headers={
        "User-Agent": _HEADERS["User-Agent"],
        "Accept": "application/octet-stream",
    })
    fd, path = tempfile.mkstemp(prefix="tonal-update-", suffix=".deb")
    try:
        with os.fdopen(fd, "wb") as out, \
                urllib.request.urlopen(req, timeout=timeout) as resp:
            shutil.copyfileobj(resp, out)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _remove_quietly(path)
        return False, f"Download failed: {e}"

    size = os.path.getsize(path)
    if expected_size and size != expected_size:
        _remove_quietly(path)
        return False, f"Download incomplete: {size:,} of {expected_size:,} bytes"

    log.info("Downloaded update to %s (%s bytes)", path, f"{size:,}")
    return True, path


def install_update(deb_path):
    """Install a downloaded .deb via `pkexec dpkg -i`. Returns (ok, msg).

    pkexec raises the standard polkit authentication dialog, so no part of
    Tonal itself runs privileged. The temp package is removed on success and
    on an authorization cancel (a retry just re-downloads ~200 KB); it is kept
    and named in the message when dpkg itself fails, so a manual
    `sudo dpkg -i <path>` rescue stays possible.
    """
    if not os.path.exists(deb_path):
        return False, "Downloaded package not found"
    if shutil.which("dpkg") is None:
        return False, "dpkg not available — updates require a Debian-based system"
    if shutil.which("pkexec") is None:
        return False, f"pkexec not available — install manually: sudo dpkg -i {deb_path}"

    try:
        r = subprocess.run(["pkexec", "dpkg", "-i", deb_path],
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "Install timed out"

    if r.returncode == 0:
        _remove_quietly(deb_path)
        log.info("Update installed from %s", deb_path)
        return True, "Update installed"

    if r.returncode in (126, 127):  # polkit dialog dismissed / auth refused
        _remove_quietly(deb_path)
        return False, "Authorization cancelled"

    detail = (r.stderr or r.stdout or "").strip().splitlines()
    last = detail[-1] if detail else f"exit code {r.returncode}"
    log.error("dpkg install failed: %s", last)
    return False, f"Install failed: {last} — package kept at {deb_path}"


def is_installed_copy():
    """True when this code runs from the packaged tree (/usr/share/tonal).

    A source checkout (~/Projects/Tonal) must not dpkg over itself — the
    running copy wouldn't change and the two trees would silently diverge.
    The Status page swaps Install for a pointer to the release page instead.
    """
    return os.path.abspath(__file__).startswith("/usr/share/tonal" + os.sep)


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass
