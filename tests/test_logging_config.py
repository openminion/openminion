import io
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openminion.base.logging import (
    apply_logging_mode,
    configure_logging,
    format_structured_event,
    get_logger,
)


class LoggingConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._root = logging.getLogger()
        self._original_handlers = list(self._root.handlers)
        self._original_root_level = self._root.level
        self._openminion_logger = logging.getLogger("openminion")
        self._original_openminion_level = self._openminion_logger.level
        for handler in list(self._root.handlers):
            self._root.removeHandler(handler)

    def tearDown(self) -> None:
        for handler in list(self._root.handlers):
            self._root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in self._original_handlers:
            self._root.addHandler(handler)
        self._root.setLevel(self._original_root_level)
        self._openminion_logger.setLevel(self._original_openminion_level)

    def test_configure_logging_uses_runtime_level_when_no_env_override(self) -> None:
        logger = configure_logging("ERROR")
        self.assertEqual(logger.level, logging.ERROR)

    @patch.dict(os.environ, {"OPENMINION_LOG_LEVEL": "WARNING"}, clear=False)
    def test_configure_logging_honors_env_override(self) -> None:
        logger = configure_logging("ERROR")
        self.assertEqual(logger.level, logging.WARNING)

    @patch.dict(os.environ, {"OPENMINION_LOG_COLOR": "1"}, clear=False)
    def test_configure_logging_colorizes_records_when_forced(self) -> None:
        configure_logging("INFO")
        root = logging.getLogger()
        self.assertTrue(root.handlers)
        formatter = root.handlers[0].formatter
        self.assertIsNotNone(formatter)
        record = logging.LogRecord(
            name="openminion.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="hello",
            args=(),
            exc_info=None,
        )
        rendered = formatter.format(record)
        self.assertIn("\x1b[2;37m", rendered)
        self.assertIn("\x1b[0m", rendered)

    @patch.dict(os.environ, {"OPENMINION_LOG_COLOR": "0"}, clear=False)
    def test_configure_logging_disables_color_when_forced_off(self) -> None:
        configure_logging("INFO")
        root = logging.getLogger()
        self.assertTrue(root.handlers)
        formatter = root.handlers[0].formatter
        self.assertIsNotNone(formatter)
        record = logging.LogRecord(
            name="openminion.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="hello",
            args=(),
            exc_info=None,
        )
        rendered = formatter.format(record)
        self.assertNotIn("\x1b[", rendered)

    @patch.dict(os.environ, {"OPENMINION_LOG_COLOR": "0"}, clear=False)
    def test_configure_logging_suppresses_periodic_events_on_console_only(self) -> None:
        stream = io.StringIO()
        stream_handler = logging.StreamHandler(stream)
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        file_handler = logging.FileHandler(tmp_path, mode="w", encoding="utf-8")
        self._root.addHandler(stream_handler)
        self._root.addHandler(file_handler)

        configure_logging("INFO")

        runtime_logger = logging.getLogger("openminion.runtimectl")
        lifecycle_logger = logging.getLogger("openminion.lifecycle")
        runtime_logger.info("event=%s payload=%s", "cron.scheduler.heartbeat", "{}")
        lifecycle_logger.info(
            "event=%s source=%s component=%s",
            "component.heartbeat",
            "cron.scheduler.heartbeat",
            "{}",
        )
        lifecycle_logger.info(
            "event=%s source=%s component=%s",
            "component.started",
            "component.started",
            "{}",
        )

        for handler in self._root.handlers:
            if hasattr(handler, "flush"):
                handler.flush()

        console_output = stream.getvalue()
        file_output = tmp_path.read_text(encoding="utf-8")

        self.assertNotIn("component.heartbeat", console_output)
        self.assertNotIn("cron.scheduler.heartbeat", console_output)
        self.assertIn("component.started", console_output)

        self.assertIn("component.heartbeat", file_output)
        self.assertIn("cron.scheduler.heartbeat", file_output)
        self.assertIn("component.started", file_output)

        tmp_path.unlink(missing_ok=True)

    @patch.dict(os.environ, {"OPENMINION_LOG_COLOR": "0"}, clear=False)
    def test_apply_logging_mode_interactive_sets_centralized_quiet_levels(self) -> None:
        configure_logging("INFO")
        apply_logging_mode("interactive")

        self.assertEqual(logging.getLogger().level, logging.WARNING)
        self.assertEqual(logging.getLogger("openminion").level, logging.WARNING)
        self.assertEqual(logging.getLogger("openminion.gateway").level, logging.ERROR)
        self.assertEqual(logging.getLogger("openminion.provider").level, logging.ERROR)

    @patch.dict(os.environ, {"OPENMINION_LOG_COLOR": "0"}, clear=False)
    def test_configure_logging_adds_single_file_handler_when_file_path_provided(
        self,
    ) -> None:
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_path = Path(tmp.name).resolve()
        tmp.close()

        configure_logging("INFO", file_path=tmp_path)
        configure_logging("INFO", file_path=tmp_path)
        logging.getLogger("openminion.filetest").info("file-handler-check")

        for handler in self._root.handlers:
            if hasattr(handler, "flush"):
                handler.flush()

        file_handlers = [
            handler
            for handler in self._root.handlers
            if isinstance(handler, logging.FileHandler)
            and str(getattr(handler, "baseFilename", "")) == str(tmp_path)
        ]
        self.assertEqual(len(file_handlers), 1)
        self.assertIn("file-handler-check", tmp_path.read_text(encoding="utf-8"))

        tmp_path.unlink(missing_ok=True)

    def test_get_logger_normalizes_to_openminion_namespace(self) -> None:
        self.assertEqual(get_logger().name, "openminion")
        self.assertEqual(get_logger("daemon").name, "openminion.daemon")
        self.assertEqual(
            get_logger("openminion.lifecycle").name, "openminion.lifecycle"
        )
        self.assertEqual(get_logger("/modules//cron/").name, "openminion.modules.cron")

    def test_format_structured_event_outputs_stable_key_value_tokens(self) -> None:
        rendered = format_structured_event(
            "daemon.server.exited_error",
            reason="crash",
            error="boom\ntrace",
            empty="",
            none=None,
        )
        self.assertEqual(
            rendered,
            "event=daemon.server.exited_error reason=crash error=boom\\ntrace",
        )
