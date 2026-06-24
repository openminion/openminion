from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch


class TestStylesModule(unittest.TestCase):
    def setUp(self):
        import openminion.cli.presentation.styles as styles_module

        styles_module._COLOR_MODE = None
        self._orig_no_color = os.environ.pop("NO_COLOR", None)

    def tearDown(self):
        import openminion.cli.presentation.styles as styles_module

        styles_module._COLOR_MODE = None
        if self._orig_no_color is not None:
            os.environ["NO_COLOR"] = self._orig_no_color

    def test_style_token_enum_values(self):
        from openminion.cli.presentation.styles import StyleToken

        self.assertEqual(StyleToken.USER.value, "user")
        self.assertEqual(StyleToken.ASSISTANT.value, "assistant")
        self.assertEqual(StyleToken.SYSTEM.value, "system")
        self.assertEqual(StyleToken.WARNING.value, "warning")
        self.assertEqual(StyleToken.ERROR.value, "error")
        self.assertEqual(StyleToken.MUTED.value, "muted")
        self.assertEqual(StyleToken.PROMPT.value, "prompt")

    def test_get_color_mode_auto_when_tty(self):
        with patch.object(sys.stdout, "isatty", return_value=True):
            import openminion.cli.presentation.styles as styles_module

            styles_module._COLOR_MODE = None
            mode = styles_module.get_color_mode()
            self.assertEqual(mode, "auto")

    def test_get_color_mode_off_when_not_tty(self):
        with patch.object(sys.stdout, "isatty", return_value=False):
            import openminion.cli.presentation.styles as styles_module

            styles_module._COLOR_MODE = None
            mode = styles_module.get_color_mode()
            self.assertEqual(mode, "off")

    def test_no_color_env_disables_colors(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            with patch.object(sys.stdout, "isatty", return_value=True):
                import openminion.cli.presentation.styles as styles_module

                styles_module._COLOR_MODE = None
                mode = styles_module.get_color_mode()
                self.assertEqual(mode, "off")
                self.assertFalse(styles_module.is_color_enabled())

    def test_openminion_color_on_enables_colors(self):
        with patch.dict(os.environ, {"OPENMINION_COLOR": "1"}):
            with patch.object(sys.stdout, "isatty", return_value=False):
                import openminion.cli.presentation.styles as styles_module

                styles_module._COLOR_MODE = None
                mode = styles_module.get_color_mode()
                self.assertEqual(mode, "on")
                self.assertTrue(styles_module.is_color_enabled())

    def test_openminion_color_off_disables_colors(self):
        with patch.dict(os.environ, {"OPENMINION_COLOR": "0"}):
            with patch.object(sys.stdout, "isatty", return_value=True):
                import openminion.cli.presentation.styles as styles_module

                styles_module._COLOR_MODE = None
                mode = styles_module.get_color_mode()
                self.assertEqual(mode, "off")
                self.assertFalse(styles_module.is_color_enabled())

    def test_style_returns_plain_text_when_disabled(self):
        with patch.object(sys.stdout, "isatty", return_value=False):
            import openminion.cli.presentation.styles as styles_module

            styles_module._COLOR_MODE = None
            result = styles_module.style(styles_module.StyleToken.USER, "hello")
            self.assertEqual(result, "hello")

    def test_style_returns_ansi_when_enabled(self):
        with patch.dict(os.environ, {"OPENMINION_COLOR": "1"}):
            import openminion.cli.presentation.styles as styles_module

            styles_module._COLOR_MODE = None
            result = styles_module.style(styles_module.StyleToken.USER, "hello")
            self.assertIn("\033", result)
            self.assertIn("hello", result)

    def test_get_theme_info_returns_dict(self):
        import openminion.cli.presentation.styles as styles_module

        styles_module._COLOR_MODE = None
        info = styles_module.get_theme_info()
        self.assertIn("color_mode", info)
        self.assertIn("color_enabled", info)
        self.assertIn("is_tty", info)
        self.assertIn("no_color_env", info)
        self.assertIn("openminion_color_env", info)

    def test_clear_line_returns_escape_sequence_when_enabled(self):
        with patch.dict(os.environ, {"OPENMINION_COLOR": "1"}):
            import openminion.cli.presentation.styles as styles_module

            styles_module._COLOR_MODE = None
            result = styles_module.clear_line()
            self.assertIn("\033", result)

    def test_clear_line_returns_plain_when_disabled(self):
        with patch.object(sys.stdout, "isatty", return_value=False):
            import openminion.cli.presentation.styles as styles_module

            styles_module._COLOR_MODE = None
            result = styles_module.clear_line()
            self.assertEqual(result, "\r")

    def test_format_prefix_adds_colon(self):
        import openminion.cli.presentation.styles as styles_module

        styles_module._COLOR_MODE = "off"
        result = styles_module.format_prefix(styles_module.StyleToken.USER, "user")
        self.assertEqual(result, "user: ")

    def test_get_spinner_frame_returns_string(self):
        import openminion.cli.presentation.styles as styles_module

        styles_module._COLOR_MODE = "on"
        frame = styles_module.get_spinner_frame()
        self.assertIsInstance(frame, str)
        self.assertTrue(len(frame) > 0)

    def test_reset_spinner_resets_index(self):
        import openminion.cli.presentation.styles as styles_module

        styles_module._COLOR_MODE = "on"
        styles_module._spinner_index = 5
        styles_module.reset_spinner()
        self.assertEqual(styles_module._spinner_index, 0)


class TestChatColorIntegration(unittest.TestCase):
    def test_chat_module_imports_styles(self):
        from openminion.cli.commands import chat

        self.assertTrue(hasattr(chat, "styles"))

    def test_chat_help_includes_theme_command(self):
        from openminion.cli.chat.ui import chat_help_lines

        lines = chat_help_lines()
        self.assertIn("  /theme             show color/theme display settings", lines)
