"""Optional GitHub release updates for the packaged Windows build.

The updater talks only to GitHub over HTTPS and replaces a frozen executable
after the editor exits. It sends no document data or usage telemetry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import __version__

GITHUB_REPOSITORY = "Shagg097/hex2pixbeta"
GITHUB_RELEASES_API = ("https://api.github.com/repos/%s/releases?per_page=20"
                       % GITHUB_REPOSITORY)
GITHUB_PAGE = "https://github.com/%s" % GITHUB_REPOSITORY
UPDATE_ASSET_NAME = "hex2pix.exe"
MAX_API_BYTES = 2 * 1024 * 1024
MAX_UPDATE_BYTES = 256 * 1024 * 1024
USER_AGENT = "hex2pix/%s" % __version__

DEFAULT_SETTINGS = {
    "check_updates": True,
    "skipped_version": "",
    "custom_brush": "",
    "text_font": "Pixel 5x7",
    "text_font_file": "",
    "text_size": "16",
    "text_bold": False,
    "text_italic": False,
}


class UpdateError(Exception):
    """A release check or update download could not be completed."""


def default_settings_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"),
                                                     ".config")
    return os.path.join(base, "hex2pix", "settings.json")


def is_folder_build():
    """True for the portable build, whose runtime sits in `_internal`.

    The single-file build unpacks to a `_MEIxxxxxx` folder under %TEMP% instead,
    so the executable's own folder has no `_internal` beside it.
    """
    if not getattr(sys, "frozen", False):
        return False
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.isdir(os.path.join(exe_dir, "_internal"))


class SettingsStore(object):
    """Small JSON settings file for optional update checks."""

    def __init__(self, path=None):
        self.path = str(path or default_settings_path())
        self.data = dict(DEFAULT_SETTINGS)
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key, default in DEFAULT_SETTINGS.items():
            value = raw.get(key, default)
            if isinstance(default, bool):
                if isinstance(value, bool):
                    self.data[key] = value
            elif isinstance(value, str):
                self.data[key] = value.strip()

    def get(self, key, default=None):
        return self.data.get(key, DEFAULT_SETTINGS.get(key, default))

    def set(self, key, value):
        if key in DEFAULT_SETTINGS:
            default = DEFAULT_SETTINGS[key]
            if isinstance(default, bool):
                value = bool(value)
            else:
                value = str(value or "").strip()
        self.data[key] = value
        self.save()

    def save(self):
        folder = os.path.dirname(self.path)
        temporary = self.path + ".tmp"
        try:
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, self.path)
        except OSError:
            try:
                os.remove(temporary)
            except OSError:
                pass


# --------------------------------------------------------------- versions
_VERSION_RE = re.compile(
    r"^[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:[-._]?([A-Za-z]+)[._-]?(\d*))?$")
_PRERELEASE_RANKS = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2,
                     "pre": 2, "preview": 2}


@dataclass(frozen=True)
class Version(object):
    major: int
    minor: int
    patch: int
    label: str = ""
    number: int = 0

    @property
    def prerelease(self):
        return bool(self.label)

    def sort_key(self):
        if not self.label:
            return (self.major, self.minor, self.patch, 1, 0, 0)
        rank = _PRERELEASE_RANKS.get(self.label.lower(), 3)
        return (self.major, self.minor, self.patch, 0, rank, self.number)

    def __str__(self):
        text = "%d.%d.%d" % (self.major, self.minor, self.patch)
        if self.label:
            text += "%s%d" % (self.label, self.number)
        return text


def parse_version(text):
    """Parse ``1.2.3``, ``v1.2.3``, ``1.2.3b1`` and similar tags."""
    match = _VERSION_RE.match(str(text or "").strip())
    if not match:
        raise UpdateError("not a version: %s" % text)
    major, minor, patch, label, number = match.groups()
    return Version(int(major), int(minor or 0), int(patch or 0),
                   (label or "").lower(), int(number or 0))


def is_newer(candidate, current):
    return parse_version(candidate).sort_key() > parse_version(current).sort_key()


# ---------------------------------------------------------------- releases
@dataclass(frozen=True)
class ReleaseInfo(object):
    version: str
    tag: str
    page_url: str
    notes: str
    prerelease: bool
    asset_name: str
    asset_url: str
    asset_size: int
    digest: str

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or payload.get("draft"):
            raise UpdateError("draft release")
        tag = str(payload.get("tag_name") or "").strip()
        version = parse_version(tag)
        assets = payload.get("assets") or []
        if not isinstance(assets, list):
            assets = []
        candidates = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            if not url.startswith("https://"):
                continue
            if name.lower() == UPDATE_ASSET_NAME:
                candidates.insert(0, asset)
            elif name.lower().endswith(".exe"):
                candidates.append(asset)
        if not candidates:
            raise UpdateError("release has no Windows executable")
        asset = candidates[0]
        return cls(
            version=str(version),
            tag=tag,
            page_url=str(payload.get("html_url") or GITHUB_PAGE),
            notes=str(payload.get("body") or ""),
            prerelease=(bool(payload.get("prerelease")) or version.prerelease),
            asset_name=str(asset.get("name") or UPDATE_ASSET_NAME),
            asset_url=str(asset.get("browser_download_url") or ""),
            asset_size=int(asset.get("size") or 0),
            digest=str(asset.get("digest") or ""),
        )


def select_update(current, payloads):
    """Pick the newest installable release compatible with ``current``.

    Stable installs only receive stable releases. A prerelease install may also
    receive prereleases. Draft releases and releases without an ``.exe`` asset
    are ignored.
    """
    current_version = parse_version(current)
    include_prerelease = current_version.prerelease
    best = None
    for payload in payloads if isinstance(payloads, list) else []:
        try:
            release = ReleaseInfo.from_payload(payload)
        except (UpdateError, TypeError, ValueError):
            continue
        if release.prerelease and not include_prerelease:
            continue
        try:
            newer = parse_version(release.version).sort_key() > \
                current_version.sort_key()
        except UpdateError:
            continue
        if not newer:
            continue
        if (best is None
                or parse_version(release.version).sort_key()
                > parse_version(best.version).sort_key()):
            best = release
    return best


def _request_json(url, opener=None, timeout=8):
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": USER_AGENT,
                      "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise UpdateError("GitHub returned HTTP %d" % status)
            data = response.read(MAX_API_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("This repository has no published releases.")
        raise UpdateError("GitHub returned HTTP %s." % exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError("Could not reach GitHub: %s" % exc)
    if len(data) > MAX_API_BYTES:
        raise UpdateError("GitHub returned an oversized response.")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("GitHub returned invalid JSON: %s" % exc)


def fetch_releases(repository=GITHUB_REPOSITORY, opener=None, timeout=8):
    url = GITHUB_RELEASES_API
    if repository != GITHUB_REPOSITORY:
        url = ("https://api.github.com/repos/%s/releases?per_page=20"
               % repository)
    payload = _request_json(url, opener=opener,
                            timeout=timeout)
    if not isinstance(payload, list):
        raise UpdateError("GitHub returned an unexpected releases response.")
    return payload


def check_for_update(current=__version__, repository=GITHUB_REPOSITORY,
                     opener=None, timeout=8):
    """Return the best newer :class:`ReleaseInfo`, or ``None``."""
    return select_update(current, fetch_releases(repository=repository,
                                                 opener=opener,
                                                 timeout=timeout))


def _sha256_digest(value):
    value = str(value or "").strip().lower()
    if value.startswith("sha256:"):
        value = value[7:]
    elif value.startswith("sha256="):
        value = value[7:]
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        return ""
    return value


def download_update(release, destination=None, opener=None, timeout=30):
    """Download a release executable and verify its size and SHA-256.

    The final file is only created after the download passes verification.
    """
    if not isinstance(release, ReleaseInfo) or not release.asset_url:
        raise UpdateError("This release has no downloadable executable.")
    opener = opener or urllib.request.urlopen
    if destination:
        target = os.path.abspath(str(destination))
        folder = os.path.dirname(target)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
    else:
        folder = tempfile.mkdtemp(prefix="hex2pix-update-")
        name = os.path.basename(release.asset_name or UPDATE_ASSET_NAME)
        target = os.path.join(folder, name if name.lower().endswith(".exe")
                              else UPDATE_ASSET_NAME)
    part = target + ".part"
    request = urllib.request.Request(
        release.asset_url,
        headers={"Accept": "application/octet-stream",
                 "User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    total = 0
    first = b""
    try:
        with opener(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise UpdateError("Download returned HTTP %d" % status)
            declared = int(getattr(response, "headers", {}).get("Content-Length")
                           or 0)
            if declared > MAX_UPDATE_BYTES:
                raise UpdateError("The update is larger than 256 MB.")
            with open(part, "wb") as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPDATE_BYTES:
                        raise UpdateError("The update is larger than 256 MB.")
                    if len(first) < 2:
                        first += chunk[:2 - len(first)]
                    digest.update(chunk)
                    handle.write(chunk)
    except UpdateError:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        try:
            os.remove(part)
        except OSError:
            pass
        raise UpdateError("Download failed: %s" % exc)
    if total < 2 or first != b"MZ":
        try:
            os.remove(part)
        except OSError:
            pass
        raise UpdateError("The downloaded file is not a Windows executable.")
    if release.asset_size and total != release.asset_size:
        try:
            os.remove(part)
        except OSError:
            pass
        raise UpdateError("The download size does not match the release.")
    expected = _sha256_digest(release.digest)
    if expected and digest.hexdigest() != expected:
        try:
            os.remove(part)
        except OSError:
            pass
        raise UpdateError("The update checksum does not match GitHub's digest.")
    try:
        os.replace(part, target)
    except OSError as exc:
        try:
            os.remove(part)
        except OSError:
            pass
        raise UpdateError("Could not save the update: %s" % exc)
    return target


def _powershell_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def apply_update(downloaded, target=None, pid=None, restart=True):
    """Schedule an executable replacement after this process exits.

    Windows locks the running executable, so the actual replacement is done by
    a detached PowerShell helper. The helper waits for ``pid``, copies the new
    build over ``target`` and restarts it. The command is passed encoded so
    paths with spaces or non-ASCII characters survive the hand-off.
    """
    if os.name != "nt":
        raise UpdateError("Automatic installation is only available on Windows.")
    downloaded = os.path.abspath(str(downloaded))
    target = os.path.abspath(str(target or sys.executable))
    if not os.path.isfile(downloaded):
        raise UpdateError("The downloaded update is missing.")
    if not os.path.isfile(target):
        raise UpdateError("The installed executable was not found.")
    if not (getattr(sys, "frozen", False) and target == os.path.abspath(
            sys.executable)):
        raise UpdateError("Automatic installation requires the packaged build.")
    if is_folder_build():
        raise UpdateError("This is the portable folder build. Replace the "
                          "folder with hex2pix-portable.zip instead.")
    pid = int(pid or os.getpid())
    folder = tempfile.mkdtemp(prefix="hex2pix-install-")
    log = os.path.join(folder, "apply-update.log")
    start = ("Start-Process -FilePath %s"
             % _powershell_quote(target) if restart else "")
    body = """$ErrorActionPreference = 'Stop'
try {
    Wait-Process -Id %d -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 700
    Copy-Item -LiteralPath %s -Destination %s -Force
    Remove-Item -LiteralPath %s -Force -ErrorAction SilentlyContinue
    %s
} catch {
    $_ | Out-File -FilePath %s -Encoding utf8 -Append
    %s
}
""" % (pid, _powershell_quote(downloaded), _powershell_quote(target),
       _powershell_quote(downloaded), start, _powershell_quote(log), start)
    encoded = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0",
                              "powershell.exe")
    if not os.path.isfile(powershell):
        powershell = "powershell.exe"
    flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [powershell, "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
             "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
            creationflags=flags)
    except OSError as exc:
        raise UpdateError("Could not start the update helper: %s" % exc)
    return log
