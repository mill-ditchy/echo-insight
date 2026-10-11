import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import webapp


class SchedulerConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.directory.name) / "scheduler.json"
        self.config_path.write_text('{"interval_hours":3,"enabled":true}', encoding="utf-8")
        self.config_patch = patch.object(webapp, "SCHEDULER_CONFIG_PATH", self.config_path)
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.directory.cleanup()

    def test_corrupt_config_does_not_get_overwritten_or_change_task(self):
        for data in ["broken json", "[]"]:
            with self.subTest(data=data):
                self.config_path.write_text(data, encoding="utf-8")
                with patch.object(webapp, "_set_scheduler_enabled") as set_enabled:
                    with self.assertRaisesRegex(RuntimeError, "保护原数据"):
                        webapp.update_automation(webapp.AutomationUpdate(enabled=False))
                    set_enabled.assert_not_called()
                self.assertEqual(self.config_path.read_text(encoding="utf-8"), data)

    def test_concurrent_interval_and_enable_changes_are_both_preserved(self):
        interval_entered = threading.Event()
        automation_started = threading.Event()

        def reconfigure(*args, **kwargs):
            interval_entered.set()
            if not automation_started.wait(3):
                raise RuntimeError("Concurrent update did not start")

        def update_automation():
            automation_started.set()
            return webapp.update_automation(webapp.AutomationUpdate(enabled=False))

        with (
            patch.object(webapp, "_reconfigure_scheduler", side_effect=reconfigure),
            patch.object(webapp, "_set_scheduler_enabled"),
            patch.object(webapp, "_scheduler_status", return_value={}),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            interval = executor.submit(webapp.update_interval, webapp.IntervalUpdate(interval_hours=6))
            self.assertTrue(interval_entered.wait(3))
            automation = executor.submit(update_automation)
            interval.result(timeout=5)
            automation.result(timeout=5)

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["interval_hours"], 6)
        self.assertFalse(saved["enabled"])
