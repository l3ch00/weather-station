"""Responsive Textual dashboard for InPost's air quality readings."""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from urllib.error import URLError

from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Footer, Header, Input, Label, Static

from air_index import Terminal, fetch_air_quality, load_terminal


SENSORS = {
    "PM1": ("PM1 · fine particles", "µg/m³"),
    "PM25": ("PM2.5 · fine particles", "µg/m³"),
    "PM10": ("PM10 · particles", "µg/m³"),
    "TEMPERATURE": ("Temperature", "°C"),
    "HUMIDITY": ("Humidity", "%"),
    "PRESSURE": ("Pressure", "hPa"),
}
QUALITY_LEVELS = {
    "VERY_GOOD": ("VERY GOOD", "#6ee7b7"),
    "GOOD": ("GOOD", "#a3e635"),
    "MODERATE": ("MODERATE", "#facc15"),
    "SATISFACTORY": ("SATISFACTORY", "#fbbf24"),
    "SUFFICIENT": ("SUFFICIENT", "#fb923c"),
    "BAD": ("BAD", "#f87171"),
    "VERY_BAD": ("VERY BAD", "#fb7185"),
}


@dataclass(frozen=True)
class Reading:
    value: float
    reference_percent: float | None = None


def parse_sensors(payload: dict) -> dict[str, Reading]:
    """Parse the API's NAME:value:reference-percent strings, skipping bad data."""
    readings = {}
    sensors = payload.get("air_sensors", [])
    if not isinstance(sensors, list):
        return readings
    for sensor in sensors:
        if not isinstance(sensor, str):
            continue
        parts = sensor.split(":")
        if len(parts) < 2 or parts[0] not in SENSORS:
            continue
        try:
            value = float(parts[1])
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        percent = None
        if len(parts) > 2 and parts[2]:
            try:
                candidate = float(parts[2])
                if math.isfinite(candidate) and candidate >= 0:
                    percent = candidate
            except ValueError:
                pass
        readings[parts[0]] = Reading(value, percent)
    return readings


class SensorCard(Static):
    def __init__(self, sensor: str) -> None:
        super().__init__(id=f"sensor-{sensor.lower()}", classes="sensor-card")
        self.sensor = sensor
        self.set_reading(None)

    def set_reading(self, reading: Reading | None) -> None:
        label, unit = SENSORS[self.sensor]
        text = Text()
        text.append(f"{label.upper()}\n\n", style="#94a3b8")
        if reading is None:
            text.append("—", style="bold #64748b")
            text.append("\nNo sensor reading", style="#64748b")
        else:
            text.append(f"{reading.value:,.2f}", style="bold #f8fafc")
            text.append(f" {unit}\n", style="#94a3b8")
            percent = reading.reference_percent
            if percent is not None:
                color = "#6ee7b7" if percent <= 100 else "#fbbf24"
                filled = min(12, round(percent / 100 * 12))
                text.append("━" * filled, style=color)
                text.append("━" * (12 - filled), style="#334155")
                text.append(f" {percent:.1f}%", style=color)
            else:
                text.append("Latest sensor reading", style="#64748b")
        self.update(text)


class TerminalDialog(ModalScreen[Terminal | None]):
    """Edit the two identifiers required by the station endpoint."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    TerminalDialog { align: center middle; background: #0b1120 80%; }
    #terminal-dialog { width: 54; max-width: 100%; height: auto; padding: 1 2;
                       background: #111c2e; border: round #fbbf24; }
    #terminal-dialog Label { height: auto; margin-bottom: 1; }
    #terminal-dialog Input { margin-bottom: 1; }
    #terminal-error { height: auto; color: #f87171; margin-bottom: 1; }
    #terminal-actions { height: 3; align-horizontal: right; }
    #terminal-actions Button { min-width: 10; margin-left: 1; }
    """

    def __init__(self, terminal: Terminal) -> None:
        super().__init__()
        self.terminal = terminal

    def compose(self) -> ComposeResult:
        with Vertical(id="terminal-dialog"):
            yield Label("Change InPost terminal")
            yield Label("Terminal ID")
            yield Input(self.terminal.id, placeholder="Terminal ID", id="terminal-id")
            yield Label("Terminal code")
            yield Input(self.terminal.code, placeholder="Terminal code", id="terminal-code")
            yield Static("", id="terminal-error")
            with Horizontal(id="terminal-actions"):
                yield Button("Cancel", id="terminal-cancel")
                yield Button("Apply", id="terminal-apply", variant="primary")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def apply_terminal(self) -> None:
        try:
            terminal = Terminal(
                self.query_one("#terminal-id", Input).value,
                self.query_one("#terminal-code", Input).value,
            )
        except ValueError as error:
            self.query_one("#terminal-error", Static).update(Text(str(error)))
            return
        self.dismiss(terminal)

    def on_input_submitted(self) -> None:
        self.apply_terminal()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "terminal-apply":
            self.apply_terminal()
        elif event.button.id == "terminal-cancel":
            self.action_cancel()


class AirQualityApp(App):
    TITLE = "InPost · Air quality"
    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("j", "toggle_json", "Raw JSON"),
        ("t", "change_terminal", "Terminal"),
        ("q", "quit", "Quit"),
    ]
    CSS = """
    Screen { background: #0b1120; color: #e2e8f0; }
    Header { background: #152238; color: #fbbf24; }
    Footer { background: #152238; }
    #dashboard { padding: 1 3; }
    #heading { height: 4; align-vertical: middle; }
    #station { width: 1fr; height: 3; }
    #refresh { min-width: 16; background: #fbbf24; color: #0b1120; border: none; }
    #quality { height: 5; padding: 0 2; border: round #6ee7b7; background: #10252a; }
    #section { height: 2; padding-top: 1; color: #94a3b8; }
    #sensors { grid-size: 3 2; grid-gutter: 1 2; grid-rows: 7; height: 15; }
    .sensor-card { height: 7; padding: 0 1; border: round #334155; background: #111c2e; }
    #connection { height: auto; min-height: 2; margin-top: 1; color: #94a3b8; }
    #reference-note { height: auto; color: #64748b; margin-bottom: 1; }
    #raw-panel { height: auto; background: #111c2e; border: round #334155; }
    #raw-json { height: auto; }
    #dashboard.compact { padding: 1 2; }
    #dashboard.compact #sensors { grid-size: 2 3; height: 23; }
    #dashboard.narrow { padding: 1; }
    #dashboard.narrow #sensors { grid-size: 1 6; height: 47; }
    #dashboard.narrow #heading { height: 7; layout: vertical; }
    #dashboard.narrow #station { width: 100%; }
    #dashboard.narrow #refresh { width: 100%; }
    """

    def __init__(
        self, refresh_interval: int = 300,
        terminal_id: str | None = None,
        terminal_code: str | None = None,
    ) -> None:
        super().__init__()
        self.terminal = load_terminal(terminal_id, terminal_code)
        self.sub_title = self.terminal.code
        self.request_id = 0
        self.refresh_interval = refresh_interval
        self.refreshing = False
        self.last_updated: datetime | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="dashboard"):
            with Horizontal(id="heading"):
                yield Static(Text.assemble(
                    ("LOCAL AIR MONITOR\n", "bold #fbbf24"),
                    (f"{self.terminal.code}  ·  ID {self.terminal.id}", "#94a3b8"),
                ), id="station")
                yield Button("Refresh ↻", id="refresh")
            yield Static("\nConnecting to the sensor station…", id="quality")
            yield Static("PARTICLES & WEATHER", id="section")
            with Grid(id="sensors"):
                for sensor in SENSORS:
                    yield SensorCard(sensor)
            yield Static("Waiting for the first reading…", id="connection")
            yield Static(
                "Particle bars show % of the API's reference value, not a separate AQI.",
                id="reference-note",
            )
            with Collapsible(title="Raw API response  ·  J to toggle", id="raw-panel"):
                yield Static("No response yet.", id="raw-json")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(self.refresh_interval, self.action_refresh)
        self.action_refresh()

    def on_resize(self, event: Resize) -> None:
        dashboard = self.query_one("#dashboard")
        dashboard.set_class(event.size.width < 95, "compact")
        dashboard.set_class(event.size.width < 55, "narrow")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.action_refresh()

    def action_toggle_json(self) -> None:
        panel = self.query_one("#raw-panel", Collapsible)
        panel.collapsed = not panel.collapsed
        if not panel.collapsed:
            panel.scroll_visible()

    def action_change_terminal(self) -> None:
        self.push_screen(TerminalDialog(self.terminal), self.change_terminal)

    def change_terminal(self, terminal: Terminal | None) -> None:
        if terminal is None or terminal == self.terminal:
            return
        self.terminal = terminal
        self.sub_title = terminal.code
        self.query_one("#station", Static).update(Text.assemble(
            ("LOCAL AIR MONITOR\n", "bold #fbbf24"),
            (f"{terminal.code}  ·  ID {terminal.id}", "#94a3b8"),
        ))
        self.last_updated = None
        for card in self.query(SensorCard):
            card.set_reading(None)
        quality = self.query_one("#quality", Static)
        quality.styles.border = ("round", "#94a3b8")
        quality.update("\nConnecting to the sensor station…")
        self.query_one("#raw-json", Static).update("No response yet.")
        self.refreshing = False
        self.action_refresh()

    def action_refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        button = self.query_one("#refresh", Button)
        button.disabled = True
        button.label = "Refreshing…"
        self.query_one("#connection", Static).update("Fetching live readings…")
        self.request_id += 1
        self.load_readings(self.request_id, self.terminal)

    @work(thread=True, exclusive=True)
    def load_readings(self, request_id: int, terminal: Terminal) -> None:
        try:
            payload = fetch_air_quality(terminal.id, terminal.code)
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object from the station")
        except (URLError, TimeoutError, UnicodeError, ValueError) as error:
            self.call_from_thread(self.receive_result, request_id, None, str(error))
        else:
            self.call_from_thread(self.receive_result, request_id, payload, None)

    def receive_result(self, request_id: int, payload: dict | None, error: str | None) -> None:
        # Cancelled HTTP threads can finish after a terminal switch; ignore them.
        if request_id != self.request_id:
            return
        if error is not None:
            self.show_error(error)
        elif payload is not None:
            self.show_readings(payload)

    def finish_refresh(self) -> None:
        self.refreshing = False
        button = self.query_one("#refresh", Button)
        button.disabled = False
        button.label = "Refresh ↻"

    def show_readings(self, payload: dict) -> None:
        readings = parse_sensors(payload)
        for card in self.query(SensorCard):
            card.set_reading(readings.get(card.sensor))

        level = payload.get("air_index_level")
        label, color = "UNKNOWN", "#94a3b8"
        if isinstance(level, str):
            label, color = QUALITY_LEVELS.get(level, (label, color))
        quality = self.query_one("#quality", Static)
        quality.styles.border = ("round", color)
        quality.update(Text.assemble(
            ("AIR QUALITY\n", "#94a3b8"),
            (f"●  {label}\n", f"bold {color}"),
            ("Reported by the InPost station", "#94a3b8"),
        ))
        self.last_updated = datetime.now().astimezone()
        self.query_one("#connection", Static).update(Text.assemble(
            ("● LIVE", "bold #6ee7b7"),
            (
                f"  ·  Updated {self.last_updated:%H:%M:%S %Z}"
                f"  ·  Refresh every {self.refresh_interval}s",
                "#94a3b8",
            ),
        ))
        self.query_one("#raw-json", Static).update(Syntax(
            json.dumps(payload, indent=2, ensure_ascii=False),
            "json", theme="monokai", background_color="#111c2e", word_wrap=True,
        ))
        self.finish_refresh()

    def show_error(self, message: str) -> None:
        if self.last_updated is None:
            self.query_one("#quality", Static).update(Text(
                "CONNECTION UNAVAILABLE\nNo readings received yet.", style="#fbbf24",
            ))
        status = Text(f"Update failed: {message}\n", style="#fbbf24")
        if self.last_updated:
            status.append(f"Showing cached readings from {self.last_updated:%H:%M:%S %Z}. ")
        status.append("Press R to retry; automatic refresh is still enabled.")
        self.query_one("#connection", Static).update(status)
        self.finish_refresh()
