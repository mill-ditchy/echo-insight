"""Release tests cover forced private files, credential leaks and Git snapshots."""

import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "test@example.invalid")
        self.write("VERSION", "0.1.0\n")
        self.write("README.md", "Project\n")
        self.write(".gitignore", "dist/\nconfig/secrets/\n")
        self.git("add", ".")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True).stdout

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_force_added_credentials_are_rejected_by_path(self):
        self.write("config/secrets/session.json", '{"encrypted": "private"}')
        self.git("add", "--force", "config/secrets/session.json")
        with self.assertRaisesRegex(release.ReleaseError, "private"):
            release.check(self.root)

    def test_example_with_credential_is_rejected_without_echoing_value(self):
        self.write("config/ai_provider.example.json", '{"api_key": "never-echo-this"}')
        self.git("add", ".")
        with self.assertRaises(release.ReleaseError) as error:
            release.check(self.root)
        self.assertNotIn("never-echo-this", str(error.exception))
        self.assertIn("credential field", str(error.exception))

    def test_example_with_personal_sources_is_rejected(self):
        self.write("config/sources.example.json", '{"sources": [{"uid": "100"}]}')
        self.git("add", ".")
        with self.assertRaisesRegex(release.ReleaseError, "personal sources"):
            release.check(self.root)

    def test_token_in_public_source_is_detected_without_echoing_token(self):
        token = "ghp_" + "a" * 36
        self.write("src/demo.py", 'TOKEN = "' + token + '"\n')
        self.git("add", ".")
        with self.assertRaises(release.ReleaseError) as error:
            release.check(self.root)
        self.assertNotIn(token, str(error.exception))
        self.assertIn("credential-like", str(error.exception))

    def test_check_reads_staged_bytes_instead_of_cleaned_working_file(self):
        self.write("src/demo.py", 'TOKEN = "' + "ghp_" + "a" * 36 + '"\n')
        self.git("add", ".")
        self.write("src/demo.py", "# cleaned working copy\n")
        with self.assertRaisesRegex(release.ReleaseError, "credential-like"):
            release.check(self.root)

    def test_symlink_mode_is_rejected(self):
        blob = self.git("hash-object", "-w", "README.md").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},src/link.py")
        with self.assertRaisesRegex(release.ReleaseError, "symlinks"):
            release.check(self.root)

    def test_build_contains_only_commit_and_has_matching_checksum(self):
        self.write("config/sources.example.json", '{"sources": []}')
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Release")
        self.write("config/secrets/session.json", '{"encrypted": "private"}')
        archive = release.build(self.root)
        with zipfile.ZipFile(archive) as package:
            self.assertTrue(any(name.endswith("VERSION") for name in package.namelist()))
            self.assertFalse(any("secrets" in name for name in package.namelist()))
        manifest = json.loads(archive.with_suffix(".json").read_text())
        self.assertFalse(manifest["includes_companion"])
        self.assertEqual(manifest["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(manifest["commit"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertIn(manifest["sha256"], archive.with_suffix(".sha256").read_text())

    def test_build_rejects_uncommitted_source_changes(self):
        self.git("commit", "--quiet", "-m", "Release")
        self.write("README.md", "changed\n")
        with self.assertRaisesRegex(release.ReleaseError, "Commit all"):
            release.build(self.root)

    def test_large_asset_is_rejected_before_loading_content(self):
        self.write("src/huge.txt", "x" * (release.MAX_FILE_BYTES + 1))
        self.git("add", ".")
        with self.assertRaisesRegex(release.ReleaseError, "small source"):
            release.check(self.root)

    def test_paths_reject_downloads_history_and_environments(self):
        for name in ("data/history.json", "output/evidence.json", "models/model.onnx", "src/__pycache__/x.pyc", "../README.md", "config/provider.json", "scripts/.env.production", "src/Secrets/account.json"):
            with self.subTest(path=name):
                self.assertFalse(release.allowed_path(name))

    def test_current_companion_sources_are_publishable(self):
        project = Path(__file__).resolve().parents[1]
        names = ["mobile_server.py", "启动手机连接.ps1", "requirements-mobile.txt"]
        names += [path.relative_to(project).as_posix() for path in (project / "android").rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts]
        for name in names:
            with self.subTest(path=name):
                self.assertTrue(release.allowed_path(name, include_companion=True))
                self.write(name, "public source\n")
        self.git("add", ".")
        published = release.check(self.root, include_companion=True)
        self.assertTrue(set(names) <= published.keys())

    def test_companion_private_data_and_generated_files_are_rejected(self):
        names = (
            "config/mobile/access.json", "config/mobile/pairing.json",
            "config/android-signing.p12", "config/android-signing.json",
            "dist/companion.apk", "android/build/companion.apk",
            "android/assets/pairing.json", "android/assets/server-key.pem",
            "android/standalone/pairing.json", "android/standalone/history.sqlite", "android/tests/signing.p12",
            "android/src/com/example/Signing.keystore", "android/res/raw/signing.p12",
            "android/src/__pycache__/cache.java", "android/assets/.env.production",
            "src/signing.jks", "web/static/companion.apk",
        )
        for name in names:
            with self.subTest(path=name):
                self.assertFalse(release.allowed_path(name))
                self.assertFalse(release.allowed_path(name, include_companion=True))

    def test_windows_profile_excludes_companion_but_keeps_shared_web_resources(self):
        for name in (*release.COMPANION_FILES, "android/AndroidManifest.xml", "android/assets/native.js"):
            with self.subTest(path=name):
                self.assertFalse(release.allowed_path(name))
        for name in ("desktop.py", "webapp.py", "web/static/mobile.css", "tests/test_maintenance_boundaries.py", "tests/test_collection_host_frontend.js"):
            with self.subTest(path=name):
                self.assertTrue(release.allowed_path(name))

    def test_forced_companion_source_blocks_default_windows_release(self):
        paths = ("android/assets/native.js", "scripts/android_usb.py")
        for name in paths:
            self.write(name, "# companion source\n")
            self.git("add", "--force", name)
        with self.assertRaisesRegex(release.ReleaseError, "unexpected"):
            release.check(self.root)
        blobs = release.check(self.root, include_companion=True)
        for name in paths:
            self.assertIn(name, blobs)

    def test_forced_companion_signing_file_fails_release_scan(self):
        self.write("android/res/raw/signing.p12", "private signing material")
        self.git("add", "--force", "android/res/raw/signing.p12")
        with self.assertRaisesRegex(release.ReleaseError, "private"):
            release.check(self.root)


if __name__ == "__main__":
    unittest.main()
