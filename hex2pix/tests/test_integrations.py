"""Tests for the optional GitHub updater."""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest

from hex2pix import integrations


def release_payload(version, prerelease=False, asset=True, digest=None,
                    size=4):
    payload = {
        "tag_name": "v" + version,
        "html_url": "https://github.com/example/hex2pix/releases/tag/v" + version,
        "body": "notes",
        "draft": False,
        "prerelease": prerelease,
        "assets": [],
    }
    if asset:
        payload["assets"].append({
            "name": "hex2pix.exe",
            "browser_download_url": "https://example.test/hex2pix.exe",
            "size": size,
            "digest": digest or "",
        })
    return payload


class FakeResponse(object):
    def __init__(self, data, status=200, headers=None):
        self._buffer = io.BytesIO(data)
        self.status = status
        self.headers = headers if headers is not None else {
            "Content-Length": str(len(data))}

    def read(self, size=-1):
        return self._buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._buffer.close()
        return False


class FakeOpener(object):
    def __init__(self, data, status=200, headers=None):
        self.data = data
        self.status = status
        self.headers = headers
        self.request = None
        self.timeout = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return FakeResponse(self.data, self.status, self.headers)


class VersionTests(unittest.TestCase):
    def test_parse_versions(self):
        self.assertEqual(str(integrations.parse_version("v1.2.3")), "1.2.3")
        self.assertEqual(str(integrations.parse_version("1.2.1b1")), "1.2.1b1")
        self.assertEqual(str(integrations.parse_version("2.0-beta.2")),
                         "2.0.0beta2")

    def test_prerelease_ordering(self):
        self.assertTrue(integrations.is_newer("1.2.1", "1.2.1b1"))
        self.assertTrue(integrations.is_newer("1.2.1b2", "1.2.1b1"))
        self.assertTrue(integrations.is_newer("1.2.2b1", "1.2.1"))
        self.assertFalse(integrations.is_newer("1.2.1b1", "1.2.1"))


class ReleaseTests(unittest.TestCase):
    def test_stable_install_skips_prereleases(self):
        release = integrations.select_update("1.2.0", [
            release_payload("1.2.1"),
            release_payload("1.3.0b1", prerelease=True),
        ])
        self.assertEqual(release.version, "1.2.1")

    def test_prerelease_install_can_advance_to_prerelease(self):
        release = integrations.select_update("1.2.0b1", [
            release_payload("1.2.0"),
            release_payload("1.3.0b1", prerelease=True),
        ])
        self.assertEqual(release.version, "1.3.0b1")

    def test_release_without_executable_is_ignored(self):
        release = integrations.select_update("1.2.0", [
            release_payload("1.3.0", asset=False),
        ])
        self.assertIsNone(release)

    def test_check_for_update_uses_repository_and_parses_asset(self):
        opener = FakeOpener(json.dumps([
            release_payload("1.3.0"),
        ]).encode("utf-8"))
        release = integrations.check_for_update(
            "1.2.0", "owner/repo", opener=opener)
        self.assertEqual(release.version, "1.3.0")
        self.assertEqual(release.asset_name, "hex2pix.exe")
        self.assertIn("/repos/owner/repo/releases", opener.request.full_url)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="hex2pix-update-test-")
        self.path = os.path.join(self.folder, "hex2pix.exe")

    def test_download_checks_size_and_digest(self):
        body = b"MZ" + b"\x00" * 30
        digest = hashlib.sha256(body).hexdigest()
        release = integrations.ReleaseInfo.from_payload(
            release_payload("1.3.0", digest="sha256:" + digest,
                            size=len(body)))
        opener = FakeOpener(body)
        path = integrations.download_update(release, self.path, opener=opener)
        self.assertEqual(path, os.path.abspath(self.path))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), body)

    def test_bad_digest_leaves_no_file(self):
        body = b"MZ" + b"\x00" * 30
        release = integrations.ReleaseInfo.from_payload(
            release_payload("1.3.0", digest="sha256:" + "0" * 64,
                            size=len(body)))
        with self.assertRaises(integrations.UpdateError):
            integrations.download_update(release, self.path,
                                         opener=FakeOpener(body))
        self.assertFalse(os.path.exists(self.path))


class FolderBuildTests(unittest.TestCase):
    def test_not_frozen_is_never_a_folder_build(self):
        had_frozen = hasattr(sys, "frozen")
        old_frozen = getattr(sys, "frozen", None)
        if had_frozen:
            del sys.frozen
        try:
            self.assertFalse(integrations.is_folder_build())
        finally:
            if had_frozen:
                sys.frozen = old_frozen

    def test_internal_folder_marks_the_portable_build(self):
        folder = tempfile.mkdtemp(prefix="hex2pix-folder-test-")
        exe = os.path.join(folder, "hex2pix.exe")
        with open(exe, "wb") as handle:
            handle.write(b"MZ")
        had_frozen = hasattr(sys, "frozen")
        old_frozen = getattr(sys, "frozen", None)
        old_executable = sys.executable
        sys.frozen = True
        sys.executable = exe
        try:
            self.assertFalse(integrations.is_folder_build())
            os.makedirs(os.path.join(folder, "_internal"))
            self.assertTrue(integrations.is_folder_build())
        finally:
            if had_frozen:
                sys.frozen = old_frozen
            else:
                del sys.frozen
            sys.executable = old_executable


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(
            tempfile.mkdtemp(prefix="hex2pix-settings-test-"),
            "settings.json")
        self.store = integrations.SettingsStore(self.path)

    def test_round_trip(self):
        self.store.set("check_updates", False)
        loaded = integrations.SettingsStore(self.path)
        self.assertFalse(loaded.get("check_updates"))

    def test_no_version_is_skipped_by_default(self):
        self.assertEqual(self.store.get("skipped_version"), "")

    def test_a_skipped_version_round_trips(self):
        # Skipping applies to that release only; newer releases ask again.
        self.store.set("skipped_version", "1.3.0b4")
        loaded = integrations.SettingsStore(self.path)
        self.assertEqual(loaded.get("skipped_version"), "1.3.0b4")
        self.assertNotEqual(loaded.get("skipped_version"), "1.3.0b5")
