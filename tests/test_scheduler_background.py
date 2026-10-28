import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scheduler
from src import state_manager, task_worker, update_history


def content():
    return {"platform": "bilibili", "source_uid": "quiet-test", "source": "测试博主",
            "article_id": "BVquiet", "title": "新作品", "link": "https://www.bilibili.com/video/BVquiet",
            "summary": "公开配文", "category": "B站", "ai_status": "pending"}


class QuietSchedulerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for module, name, path in [(update_history, "DATA_PATH", "history.json"),
                                   (update_history, "SUMMARY_DIR", "summaries"),
                                   (state_manager, "STATE_PATH", "state.json")]:
            self.enterContext(patch.object(module, name, self.root / path))
        self.enterContext(patch.object(scheduler, "record_check"))
        self.enterContext(patch.object(scheduler, "write_update_report", return_value=Mock()))
        self.process = self.enterContext(patch.object(scheduler, "_process_and_persist", return_value=[]))
        self.notify = self.enterContext(patch.object(scheduler.WindowsNotifier, "notify"))

    def initialize(self, backend):
        update_history.initialize_storage(backend)
        state_manager.save_state({"version": 1, "sources": {"bilibili:quiet-test": {"seen_ids": []}}})

    def test_new_content_notifies_once_repeated_check_stays_quiet_on_both_backends(self):
        for backend in ("json", "sqlite"):
            with self.subTest(backend=backend):
                update_history.DATA_PATH = self.root / f"{backend}-history.json"
                state_manager.STATE_PATH = self.root / f"{backend}-state.json"
                self.initialize(backend)
                self.notify.reset_mock()
                with patch.object(scheduler, "fetch_metadata", side_effect=lambda: ([content()], [])):
                    self.assertEqual(scheduler.run_once(), 1)
                    self.assertEqual(scheduler.run_once(), 0)
                self.notify.assert_called_once()
                self.assertIn("回声洞察", self.notify.call_args.args[0])
                self.assertIn("1 条新内容", self.notify.call_args.args[1])
                self.assertEqual(len(update_history.list_updates(limit=None)), 1)

    def test_no_content_stays_quiet_unless_old_processing_finally_fails(self):
        self.initialize("json")
        for errors in ([], ["旧任务的模型暂时不可用"]):
            with self.subTest(errors=errors), patch.object(scheduler, "fetch_metadata", return_value=([], [])), \
                 patch.object(scheduler, "_pending_updates", return_value=[content()]):
                self.notify.reset_mock()
                self.process.return_value = errors
                self.assertEqual(scheduler.run_once(), 0)
                if errors:
                    self.notify.assert_called_once()
                    self.assertIn("旧任务的模型暂时不可用", self.notify.call_args.args[1])
                else:
                    self.notify.assert_not_called()

    def test_partial_fetch_failure_notifies_without_new_content_on_both_backends(self):
        for backend in ("json", "sqlite"):
            with self.subTest(backend=backend):
                update_history.DATA_PATH = self.root / f"{backend}-history.json"
                state_manager.STATE_PATH = self.root / f"{backend}-state.json"
                self.initialize(backend)
                state_manager.mark_seen([content()], state_manager.load_state())
                self.notify.reset_mock()
                with patch.object(scheduler, "fetch_metadata", return_value=([content()], ["另一来源读取失败"])):
                    self.assertEqual(scheduler.run_once(), 0)
                self.notify.assert_called_once()
                self.assertIn("另一来源读取失败", self.notify.call_args.args[1])

    def test_new_content_and_final_processing_failure_both_notify(self):
        self.initialize("json")
        self.process.return_value = ["新作品分析失败"]
        with patch.object(scheduler, "fetch_metadata", return_value=([content()], [])):
            self.assertEqual(scheduler.run_once(), 1)
        self.assertEqual(self.notify.call_count, 2)
        self.assertIn("有新内容", self.notify.call_args_list[0].args[0])
        self.assertIn("新作品分析失败", self.notify.call_args_list[1].args[1])

    def test_notification_failure_does_not_lose_saved_content_or_repeat_alert(self):
        self.initialize("sqlite")
        self.notify.side_effect = OSError("Toast unavailable")
        with patch.object(scheduler, "fetch_metadata", side_effect=lambda: ([content()], [])), self.assertLogs(level="ERROR"):
            self.assertEqual(scheduler.run_once(), 1)
            self.assertEqual(scheduler.run_once(), 0)
        self.notify.assert_called_once()
        self.assertIsNotNone(update_history.get_update("bilibili", "BVquiet"))

    def test_saved_new_content_notifies_even_if_analysis_submission_fails(self):
        self.initialize("sqlite")
        self.process.side_effect = RuntimeError("worker unavailable")
        with patch.object(scheduler, "fetch_metadata", return_value=([content()], [])):
            with self.assertRaisesRegex(RuntimeError, "worker unavailable"):
                scheduler.run_once()
        self.notify.assert_called_once()
        self.assertIsNotNone(update_history.get_update("bilibili", "BVquiet"))

    def test_worker_notifies_only_for_terminal_failures_it_completed(self):
        job = {"id": 1}
        for result, owned in (("succeeded", True), ("retry_wait", True), ("cancelled", True),
                              (None, True), ("failed", False), ("failed", True)):
            with self.subTest(result=result, owned=owned), \
                 patch.object(task_worker.task_queue, "claim", side_effect=[job, None]), \
                 patch.object(task_worker, "execute", return_value=owned), \
                 patch.object(task_worker.task_queue, "job_state", return_value=result), \
                 patch.object(task_worker.task_queue, "next_due", return_value=None), \
                 patch.object(task_worker.time, "monotonic", side_effect=[0, 0, 11]):
                self.notify.reset_mock()
                task_worker.run_worker()
                if result == "failed" and owned:
                    self.notify.assert_called_once()
                    self.assertIn("1 条AI简报最终分析失败", self.notify.call_args.args[1])
                else:
                    self.notify.assert_not_called()

    def test_worker_batches_failures_and_ignores_notification_errors(self):
        self.notify.side_effect = OSError("Toast unavailable")
        with patch.object(task_worker.task_queue, "claim", side_effect=[{"id": 1}, {"id": 2}, None]), \
             patch.object(task_worker, "execute", return_value=True), \
             patch.object(task_worker.task_queue, "job_state", return_value="failed"), \
             patch.object(task_worker.task_queue, "next_due", return_value=None), \
             patch.object(task_worker.time, "monotonic", side_effect=[0, 0, 0, 11]), \
             self.assertLogs(level="ERROR"):
            task_worker.run_worker()
        self.notify.assert_called_once()
        self.assertIn("2 条AI简报最终分析失败", self.notify.call_args.args[1])

    def test_worker_retry_wait_notifies_only_after_terminal_attempt(self):
        job = {"id": 1}
        def execute(_job):
            self.notify.assert_not_called()
            return True
        with patch.object(task_worker.task_queue, "claim", side_effect=[job, None, job, None]), \
             patch.object(task_worker, "execute", side_effect=execute), \
             patch.object(task_worker.task_queue, "job_state", side_effect=["retry_wait", "failed"]), \
             patch.object(task_worker.task_queue, "next_due", side_effect=[0, None]), \
             patch.object(task_worker.time, "monotonic", side_effect=[0, 0, 0, 11]), \
             patch.object(task_worker.time, "sleep"):
            task_worker.run_worker()
        self.notify.assert_called_once()

    def test_worker_unexpected_failure_notifies_and_preserves_error(self):
        for error in (RuntimeError("queue unavailable"), TimeoutError("queue unavailable")):
            with self.subTest(error=error), \
                 patch.object(task_worker.task_queue, "claim", side_effect=error), \
                 self.assertLogs(level="ERROR"), self.assertRaisesRegex(type(error), "queue unavailable"):
                self.notify.reset_mock()
                task_worker.run_worker()
            self.notify.assert_called_once()
            self.assertIn("意外停止", self.notify.call_args.args[1])

    def test_worker_lock_contention_stays_quiet(self):
        lock = Mock()
        lock.__enter__ = Mock(side_effect=TimeoutError("another worker owns the queue"))
        lock.__exit__ = Mock()
        with patch.object(task_worker, "interprocess_lock", return_value=lock):
            task_worker.run_worker()
        self.notify.assert_not_called()

    def test_pythonw_parent_starts_worker_without_console_and_with_log_handles(self):
        pythonw = self.root / "pythonw.exe"
        python = self.root / "python.exe"
        python.touch()
        with patch.object(task_worker.sys, "executable", str(pythonw)), \
             patch.object(task_worker.subprocess, "Popen") as launch:
            task_worker.ensure_worker()
        args, kwargs = launch.call_args
        self.assertEqual(args[0][0], str(python))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertIs(kwargs["stdout"], kwargs["stderr"])
        self.assertEqual(Path(kwargs["stdout"].name), self.root / "worker.log")

    def test_worker_redirected_logs_preserve_unicode_titles(self):
        source_dir = self.root / "src"
        source_dir.mkdir()
        (source_dir / "__init__.py").write_text("", encoding="utf-8")
        (source_dir / "task_worker.py").write_text(
            "import sys\n"
            "print('新作品 😀 开始分析')\n"
            "print('处理状态 🚀', file=sys.stderr)\n",
            encoding="utf-8",
        )
        processes = []
        launch = subprocess.Popen

        def start(*args, **kwargs):
            process = launch(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(task_worker, "ROOT", self.root), \
             patch.object(task_worker.subprocess, "Popen", side_effect=start):
            task_worker.ensure_worker()
        process = processes[0]
        try:
            self.assertEqual(process.wait(timeout=15), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        log = (self.root / "worker.log").read_text(encoding="utf-8")
        self.assertIn("新作品 😀 开始分析", log)
        self.assertIn("处理状态 🚀", log)

    @unittest.skipUnless(os.name == "nt", "Windows pythonw probe")
    def test_real_pythonw_has_no_console_and_scheduler_logs_work(self):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            self.skipTest("pythonw.exe is unavailable")
        result_path = self.root / "probe.json"
        code = (
            "import ctypes,json,logging,pathlib,sys;"
            f"sys.path.insert(0,{str(scheduler.PROJECT_ROOT)!r});"
            "import scheduler;"
            f"scheduler.LOG_DIR=pathlib.Path({str(self.root / 'logs')!r});"
            "scheduler.setup_logging();logging.info('windowless scheduler probe');"
            f"pathlib.Path({str(result_path)!r}).write_text(json.dumps({{'console':bool(ctypes.windll.kernel32.GetConsoleWindow()),'stdout':sys.stdout.writable(),'stderr':sys.stderr.writable()}}),encoding='utf-8')"
        )
        result = subprocess.run([str(pythonw), "-c", code], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result_path.read_text()), {"console": False, "stdout": True, "stderr": True})
        self.assertIn("windowless scheduler probe", (self.root / "logs" / "scheduler.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
