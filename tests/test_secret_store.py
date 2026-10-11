import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.security import secret_store


class SecretStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.secret_dir = Path(self.temp_dir.name) / "secrets"
        self.protect = patch.object(secret_store, "SECRET_DIR", self.secret_dir)
        self.encrypt = patch.object(secret_store, "_protect", side_effect=lambda value: b"locked:" + value)
        self.decrypt = patch.object(secret_store, "_unprotect", side_effect=lambda value: value.removeprefix(b"locked:"))
        self.protect.start()
        self.encrypt.start()
        self.decrypt.start()

    def tearDown(self) -> None:
        self.decrypt.stop()
        self.encrypt.stop()
        self.protect.stop()
        self.temp_dir.cleanup()

    def test_round_trip_does_not_store_plaintext(self) -> None:
        secret_store.save_secret("sample", "very-sensitive-value")

        stored = (self.secret_dir / "sample.dpapi.json").read_text(encoding="utf-8")
        self.assertNotIn("very-sensitive-value", stored)
        self.assertEqual(secret_store.load_secret("sample"), "very-sensitive-value")

    def test_migration_removes_plaintext_only_after_encrypted_save(self) -> None:
        legacy = Path(self.temp_dir.name) / "legacy.txt"
        legacy.write_text("old-secret", encoding="utf-8")

        value = secret_store.load_or_migrate_secret("migrated", [legacy])

        self.assertEqual(value, "old-secret")
        self.assertFalse(legacy.exists())
        self.assertEqual(secret_store.load_secret("migrated"), "old-secret")

    def test_migration_removes_all_legacy_variants(self) -> None:
        primary = Path(self.temp_dir.name) / "primary.txt"
        fallback = Path(self.temp_dir.name) / "fallback.txt"
        primary.write_text("current-secret", encoding="utf-8")
        fallback.write_text("stale-secret", encoding="utf-8")

        value = secret_store.load_or_migrate_secret(
            "variants",
            [primary, fallback],
        )

        self.assertEqual(value, "current-secret")
        self.assertFalse(primary.exists())
        self.assertFalse(fallback.exists())

    def test_existing_encrypted_secret_cleans_up_leftover_plaintext(self) -> None:
        secret_store.save_secret("sample", "current-secret")
        legacy = Path(self.temp_dir.name) / "leftover.txt"
        legacy.write_text("stale-plaintext", encoding="utf-8")

        self.assertEqual(secret_store.load_or_migrate_secret("sample", [legacy]), "current-secret")
        self.assertFalse(legacy.exists())

    def test_storage_status_reports_unreadable_encrypted_cookie(self) -> None:
        secret_store.save_secret("bilibili_cookie", "cookie-value")
        with patch.object(
            secret_store,
            "_unprotect",
            side_effect=OSError("different Windows user"),
        ):
            status = secret_store.credential_storage_status()

        self.assertTrue(status["encrypted"])
        self.assertFalse(status["readable"])
        self.assertTrue(status["needs_relogin"])

    def test_api_key_status_reports_unreadable_encrypted_key(self) -> None:
        secret_store.save_secret("opencode_api_key", "secret-key")
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}),
            patch.object(
                secret_store,
                "_unprotect",
                side_effect=OSError("different Windows user"),
            ),
        ):
            status = secret_store.opencode_api_key_status()

        self.assertFalse(status["configured"])
        self.assertTrue(status["needs_reconfigure"])

    def test_saving_api_key_overwrites_unreadable_key(self) -> None:
        secret_store.save_secret("opencode_api_key", "old-key")
        secret_store.save_opencode_api_key("new-key")

        self.assertEqual(secret_store.load_secret("opencode_api_key"), "new-key")

    def test_normalizes_copied_cookie_header(self) -> None:
        self.assertEqual(
            secret_store.normalize_zhihu_cookie("Cookie: z_c0=test; d_c0=demo"),
            "z_c0=test; d_c0=demo",
        )

    def test_rejects_non_ascii_cookie_explanation(self) -> None:
        with self.assertRaisesRegex(ValueError, "包含中文"):
            secret_store.normalize_zhihu_cookie("Cookie: 这是错误内容")

    def test_clear_bilibili_credentials_removes_cookie_and_refresh_token(self) -> None:
        cookie_legacy = Path(self.temp_dir.name) / "bilibili_cookie.txt"
        refresh_legacy = Path(self.temp_dir.name) / "bilibili_refresh_token.txt"
        secret_store.save_secret("bilibili_cookie", "cookie-value")
        secret_store.save_secret("bilibili_refresh_token", "refresh-value")
        cookie_legacy.write_text("legacy-cookie", encoding="utf-8")
        refresh_legacy.write_text("legacy-refresh", encoding="utf-8")

        with (
            patch.dict("os.environ", {"BILIBILI_COOKIE": ""}),
            patch.object(secret_store, "COOKIE_LEGACY_PATH", cookie_legacy),
            patch.object(secret_store, "REFRESH_TOKEN_LEGACY_PATH", refresh_legacy),
        ):
            secret_store.clear_bilibili_credentials()

        self.assertFalse((self.secret_dir / "bilibili_cookie.dpapi.json").exists())
        self.assertFalse((self.secret_dir / "bilibili_refresh_token.dpapi.json").exists())
        self.assertFalse(cookie_legacy.exists())
        self.assertFalse(refresh_legacy.exists())


if __name__ == "__main__":
    unittest.main()
