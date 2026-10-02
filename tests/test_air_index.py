import contextlib
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import air_index
from air_index_tui import AirQualityApp, Reading, SensorCard, TerminalDialog, parse_sensors
from textual.widgets import Button, Collapsible, Input, Static


PAYLOAD = {
    "air_index_level": "VERY_GOOD",
    "air_sensors": [
        "PM1:6.81:",
        "PM25:10.27:68.49",
        "PM10:13.06:29.03",
        "PRESSURE:1031.05:",
        "HUMIDITY:69.49:",
        "TEMPERATURE:16.81:",
    ],
}


TEST_SETTINGS = {"INPOST_TERMINAL_ID": "90001", "INPOST_TERMINAL_CODE": "TEST01APP"}


class SyntheticSettings:
    def setUp(self):
        settings = patch("air_index.read_terminal_settings", return_value=TEST_SETTINGS)
        settings.start()
        self.addCleanup(settings.stop)


class RequestTests(SyntheticSettings, unittest.TestCase):
    def test_browser_like_post(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = Message()
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.read.return_value = json.dumps(PAYLOAD).encode()
        with patch.object(air_index, "urlopen", return_value=response) as fetch:
            self.assertEqual(air_index.fetch_air_quality(), PAYLOAD)
        request = fetch.call_args.args[0]
        self.assertEqual(request.full_url, air_index.Terminal("90001", "TEST01APP").url)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, b"")
        self.assertEqual(request.get_header("X-requested-with"), "XMLHttpRequest")
        self.assertIn("Mozilla/5.0", request.get_header("User-agent"))
        self.assertEqual(fetch.call_args.kwargs["timeout"], 30)

    def test_json_mode(self):
        output = io.StringIO()
        with patch("sys.argv", ["air_index.py", "--json"]):
            with patch.object(air_index, "fetch_air_quality", return_value=PAYLOAD):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(air_index.main(), 0)
        self.assertEqual(json.loads(output.getvalue()), PAYLOAD)
        self.assertIn('\n    "air_index_level"', output.getvalue())

    def test_custom_terminal_request_url(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = Message()
        response.read.return_value = b'{}'
        with patch.object(air_index, "urlopen", return_value=response) as fetch:
            air_index.fetch_air_quality("12345", "test01app")
        self.assertEqual(fetch.call_args.args[0].full_url,
                         "https://inpost.pl/shipx-point-data/12345/TEST01APP/air_index_level")

    def test_custom_terminal_cli_json(self):
        with patch("sys.argv", ["air_index.py", "--json", "--terminal-id", "12345",
                                "--terminal-code", "test01app"]):
            with patch.object(air_index, "fetch_air_quality", return_value=PAYLOAD) as fetch:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(air_index.main(), 0)
        fetch.assert_called_once_with("12345", "TEST01APP")

    def test_custom_terminal_cli_dashboard(self):
        with patch("sys.argv", ["air_index.py", "--terminal-id", "12345",
                                "--terminal-code", "TEST01APP"]):
            with patch("air_index_tui.AirQualityApp") as app:
                self.assertEqual(air_index.main(), 0)
        app.assert_called_once_with(refresh_interval=60, terminal_id="12345",
                                    terminal_code="TEST01APP")
        app.return_value.run.assert_called_once()

    def test_terminal_validation(self):
        self.assertEqual(air_index.Terminal(" 12345 ", " test01app "),
                         air_index.Terminal("12345", "TEST01APP"))
        for terminal_id, code in (("0", "ABC"), ("-1", "ABC"), ("abc", "ABC"),
                                  ("1", ""), ("1", "../ABC"), ("1", "ABC?x=1")):
            with self.subTest(terminal_id=terminal_id, code=code):
                with self.assertRaises(ValueError):
                    air_index.Terminal(terminal_id, code)
        with patch("sys.argv", ["air_index.py", "--terminal-id", "bad"]):
            with patch.object(air_index, "fetch_air_quality") as fetch:
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        air_index.main()
        self.assertEqual(error.exception.code, 2)
        fetch.assert_not_called()

    def test_json_mode_network_error(self):
        output = io.StringIO()
        with patch("sys.argv", ["air_index.py", "--json"]):
            with patch.object(air_index, "fetch_air_quality", side_effect=URLError("offline")):
                with contextlib.redirect_stderr(output):
                    self.assertEqual(air_index.main(), 1)
        self.assertIn("offline", output.getvalue())

    def test_refresh_interval_validation(self):
        self.assertEqual(air_index.positive_interval("30"), 30)
        for invalid in ("0", "-1", "no", "1.5"):
            with self.subTest(value=invalid):
                with self.assertRaises(air_index.argparse.ArgumentTypeError):
                    air_index.positive_interval(invalid)


class SensorTests(unittest.TestCase):
    def test_api_sensor_format(self):
        readings = parse_sensors(PAYLOAD)
        self.assertEqual(len(readings), 6)
        self.assertEqual(readings["PM25"], Reading(10.27, 68.49))
        self.assertEqual(readings["TEMPERATURE"], Reading(16.81))

    def test_invalid_and_missing_readings(self):
        payload = {"air_sensors": [
            None, {}, "broken", "OTHER:123:", "PM1:bad:", "PM10:inf:",
            "HUMIDITY:nan:", "TEMPERATURE:-3.5:", "PM25:0:bad",
            "PRESSURE:1000:-5",
        ]}
        self.assertEqual(parse_sensors(payload), {
            "TEMPERATURE": Reading(-3.5),
            "PM25": Reading(0),
            "PRESSURE": Reading(1000),
        })
        self.assertEqual(parse_sensors({}), {})
        self.assertEqual(parse_sensors({"air_sensors": None}), {})


class DashboardTests(SyntheticSettings, unittest.IsolatedAsyncioTestCase):
    async def settle(self, app, pilot):
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def test_dashboard_refresh_and_json_toggle(self):
        with patch("air_index_tui.fetch_air_quality", return_value=PAYLOAD) as fetch:
            app = AirQualityApp()
            async with app.run_test(size=(120, 42)) as pilot:
                await self.settle(app, pilot)
                self.assertEqual(fetch.call_count, 1)
                self.assertIsNotNone(app.last_updated)
                self.assertFalse(app.refreshing)
                self.assertIn("VERY GOOD", str(app.query_one("#quality", Static).content))
                self.assertIn("10.27", str(app.query_one("#sensor-pm25", SensorCard).content))
                self.assertIn("68.5%", str(app.query_one("#sensor-pm25", SensorCard).content))
                self.assertFalse(app.query_one("#refresh", Button).disabled)
                panel = app.query_one("#raw-panel", Collapsible)
                self.assertTrue(panel.collapsed)
                await pilot.press("j")
                self.assertFalse(panel.collapsed)
                await pilot.press("j", "r")
                await self.settle(app, pilot)
                self.assertTrue(panel.collapsed)
                self.assertEqual(fetch.call_count, 2)
                await pilot.click("#refresh")
                await self.settle(app, pilot)
                self.assertEqual(fetch.call_count, 3)

    async def test_refresh_failure_keeps_last_readings_and_recovers(self):
        with patch("air_index_tui.fetch_air_quality", side_effect=[
            PAYLOAD, URLError("offline"), PAYLOAD,
        ]):
            app = AirQualityApp()
            async with app.run_test() as pilot:
                await self.settle(app, pilot)
                previous_update = app.last_updated
                previous_card = str(app.query_one("#sensor-pm25", SensorCard).content)
                await pilot.press("r")
                await self.settle(app, pilot)
                self.assertEqual(app.last_updated, previous_update)
                self.assertEqual(str(app.query_one("#sensor-pm25", SensorCard).content), previous_card)
                status = str(app.query_one("#connection", Static).content)
                self.assertIn("offline", status)
                self.assertIn("cached", status)
                self.assertFalse(app.refreshing)
                await pilot.press("r")
                await self.settle(app, pilot)
                self.assertIn("LIVE", str(app.query_one("#connection", Static).content))

    async def test_initial_failure_and_invalid_response(self):
        for response in (URLError("offline"), ValueError("invalid JSON"), []):
            with self.subTest(response=response):
                kwargs = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
                with patch("air_index_tui.fetch_air_quality", **kwargs):
                    app = AirQualityApp()
                    async with app.run_test() as pilot:
                        await self.settle(app, pilot)
                        self.assertIsNone(app.last_updated)
                        self.assertFalse(app.refreshing)
                        self.assertFalse(app.query_one("#refresh", Button).disabled)
                        self.assertIn("UNAVAILABLE", str(app.query_one("#quality", Static).content))

    async def test_responsive_layouts(self):
        for size, expected_class in (
            ((80, 30), "compact"), ((55, 24), "compact"), ((40, 24), "narrow"),
        ):
            with self.subTest(size=size):
                with patch("air_index_tui.fetch_air_quality", return_value=PAYLOAD):
                    app = AirQualityApp()
                    async with app.run_test(size=size) as pilot:
                        await self.settle(app, pilot)
                        self.assertTrue(app.query_one("#dashboard").has_class(expected_class))
                        self.assertEqual(len(app.query(SensorCard)), 6)
                        self.assertIsNotNone(app.last_updated)
                        await pilot.resize_terminal(120, 42)
                        self.assertFalse(app.query_one("#dashboard").has_class("compact"))
                        self.assertFalse(app.query_one("#dashboard").has_class("narrow"))

    async def test_timer_triggers_refresh(self):
        with patch("air_index_tui.fetch_air_quality", return_value=PAYLOAD) as fetch:
            app = AirQualityApp(refresh_interval=1)
            async with app.run_test() as pilot:
                await self.settle(app, pilot)
                await pilot.pause(1.1)
                await self.settle(app, pilot)
                self.assertGreaterEqual(fetch.call_count, 2)

    async def test_change_terminal_dialog(self):
        with patch("air_index_tui.fetch_air_quality", side_effect=[PAYLOAD, URLError("offline")]) as fetch:
            app = AirQualityApp()
            async with app.run_test(size=(120, 42)) as pilot:
                await self.settle(app, pilot)
                old_request_id = app.request_id
                await pilot.press("t")
                self.assertIsInstance(app.screen, TerminalDialog)
                app.screen.query_one("#terminal-id", Input).value = "12345"
                app.screen.query_one("#terminal-code", Input).value = "test01app"
                await pilot.click("#terminal-apply")
                await self.settle(app, pilot)
                fetch.assert_called_with("12345", "TEST01APP")
                self.assertEqual(app.sub_title, "TEST01APP")
                self.assertIn("12345", str(app.query_one("#station", Static).content))
                self.assertIsNone(app.last_updated)
                self.assertNotIn("10.27", str(app.query_one("#sensor-pm25", SensorCard).content))
                self.assertEqual(app.query_one("#raw-json", Static).content, "No response yet.")
                # A late success or error from the previous station cannot overwrite the UI.
                app.receive_result(old_request_id, PAYLOAD, None)
                app.receive_result(old_request_id, None, "old station failed")
                self.assertIsNone(app.last_updated)
                self.assertNotIn("old station", str(app.query_one("#connection", Static).content))

    async def test_terminal_dialog_validation_cancel_and_background_refresh(self):
        with patch("air_index_tui.fetch_air_quality", return_value=PAYLOAD) as fetch:
            app = AirQualityApp()
            async with app.run_test() as pilot:
                await self.settle(app, pilot)
                await pilot.press("t")
                app.screen.query_one("#terminal-id", Input).value = "bad"
                await pilot.click("#terminal-apply")
                self.assertIsInstance(app.screen, TerminalDialog)
                self.assertIn("positive integer", str(app.screen.query_one("#terminal-error", Static).content))
                app.action_refresh()
                await self.settle(app, pilot)
                await pilot.resize_terminal(65, 30)
                await pilot.press("escape")
                self.assertEqual(app.terminal, air_index.Terminal("90001", "TEST01APP"))
                self.assertEqual(fetch.call_count, 2)


class SettingsTests(unittest.TestCase):
    def test_dotenv_quotes_comments_and_blank_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                '# Private defaults\nexport INPOST_TERMINAL_ID="90001" # comment\n'
                "INPOST_TERMINAL_CODE='test01app'\nOTHER=ignored\n",
                encoding="utf-8",
            )
            self.assertEqual(air_index.read_terminal_settings(path), {
                "INPOST_TERMINAL_ID": "90001", "INPOST_TERMINAL_CODE": "test01app",
            })
            path.write_text("INPOST_TERMINAL_ID=\nINPOST_TERMINAL_CODE=\n", encoding="utf-8")
            with patch.object(air_index, "ENV_FILE", path):
                with self.assertRaisesRegex(ValueError, "Set INPOST_TERMINAL_ID"):
                    air_index.load_terminal()

    def test_missing_dotenv_and_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(air_index, "ENV_FILE", Path(directory) / ".env"):
                self.assertEqual(air_index.read_terminal_settings(), {})
                with self.assertRaises(ValueError):
                    air_index.load_terminal()
                self.assertEqual(air_index.load_terminal("90001", "TEST01APP"),
                                 air_index.Terminal("90001", "TEST01APP"))

    def test_partial_override(self):
        with patch("air_index.read_terminal_settings", return_value=TEST_SETTINGS):
            self.assertEqual(air_index.load_terminal(terminal_id="90002"),
                             air_index.Terminal("90002", "TEST01APP"))
            self.assertEqual(air_index.load_terminal(terminal_code="TEST02APP"),
                             air_index.Terminal("90001", "TEST02APP"))

    def test_malformed_setting_error_does_not_echo_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('INPOST_TERMINAL_CODE="sensitive-unclosed', encoding="utf-8")
            with self.assertRaises(ValueError) as error:
                air_index.read_terminal_settings(path)
            self.assertNotIn("sensitive", str(error.exception))

    def test_cli_missing_settings_is_clear_and_does_not_fetch(self):
        with patch("air_index.read_terminal_settings", return_value={}):
            with patch("sys.argv", ["air_index.py", "--json"]):
                with patch.object(air_index, "fetch_air_quality") as fetch:
                    output = io.StringIO()
                    with contextlib.redirect_stderr(output):
                        with self.assertRaises(SystemExit) as error:
                            air_index.main()
        self.assertEqual(error.exception.code, 2)
        self.assertIn(".env", output.getvalue())
        fetch.assert_not_called()

    def test_help_does_not_load_private_settings(self):
        with patch("air_index.read_terminal_settings") as settings:
            with patch("sys.argv", ["air_index.py", "--help"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        air_index.main()
        self.assertEqual(error.exception.code, 0)
        settings.assert_not_called()


if __name__ == "__main__":
    unittest.main()
