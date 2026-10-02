#!/usr/bin/env python3
"""Fetch InPost air quality data, with a terminal dashboard or JSON output."""

import argparse
import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ENV_FILE = Path(__file__).resolve().with_name(".env")


@dataclass(frozen=True)
class Terminal:
    id: str
    code: str

    def __post_init__(self) -> None:
        terminal_id = str(self.id).strip()
        code = self.code.strip().upper()
        if not re.fullmatch(r"[0-9]+", terminal_id) or int(terminal_id) < 1:
            raise ValueError("Terminal ID must be a positive integer.")
        if not re.fullmatch(r"[A-Z0-9_-]+", code):
            raise ValueError("Terminal code must contain letters, digits, underscores or hyphens.")
        object.__setattr__(self, "id", terminal_id)
        object.__setattr__(self, "code", code)

    @property
    def url(self) -> str:
        return f"https://inpost.pl/shipx-point-data/{self.id}/{self.code}/air_index_level"


def read_terminal_settings(path: Path | None = None) -> dict[str, str]:
    """Read private defaults without importing dependencies or changing the environment."""
    path = path if path is not None else ENV_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as error:
        raise ValueError("Could not read .env station settings.") from error
    settings = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if key not in {"INPOST_TERMINAL_ID", "INPOST_TERMINAL_CODE"}:
            continue
        try:
            parts = shlex.split(value, comments=True)
        except ValueError as error:
            raise ValueError(f"Invalid .env setting on line {number}.") from error
        if not separator or len(parts) > 1:
            raise ValueError(f"Invalid .env setting on line {number}.")
        settings[key] = parts[0] if parts else ""
    return settings


def load_terminal(terminal_id: str | None = None, terminal_code: str | None = None) -> Terminal:
    if terminal_id is not None and terminal_code is not None:
        return Terminal(terminal_id, terminal_code)
    settings = read_terminal_settings()
    if terminal_id is None:
        terminal_id = settings.get("INPOST_TERMINAL_ID", "")
    if terminal_code is None:
        terminal_code = settings.get("INPOST_TERMINAL_CODE", "")
    if not terminal_id or not terminal_code:
        raise ValueError(
            "Set INPOST_TERMINAL_ID and INPOST_TERMINAL_CODE in .env, "
            "or provide --terminal-id and --terminal-code."
        )
    return Terminal(terminal_id, terminal_code)


def fetch_air_quality(
    terminal_id: str | None = None,
    terminal_code: str | None = None,
) -> object:
    """Make the browser-like POST request without requiring TUI dependencies."""
    request = Request(
        load_terminal(terminal_id, terminal_code).url,
        data=b"",
        method="POST",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://inpost.pl",
            "Referer": "https://inpost.pl/",
        },
    )

    with urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def positive_interval(value: str) -> int:
    try:
        interval = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("refresh interval must be an integer") from error
    if interval < 1:
        raise argparse.ArgumentTypeError("refresh interval must be at least 1 second")
    return interval


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true",
        help="print JSON instead of opening the dashboard",
    )
    parser.add_argument(
        "--refresh", type=positive_interval, default=300, metavar="SECONDS",
        help="dashboard refresh interval (default: 300 seconds / 5 minutes)",
    )
    parser.add_argument(
        "--terminal-id",
        help="InPost terminal ID (default: private .env setting)",
    )
    parser.add_argument(
        "--terminal-code",
        help="InPost terminal code (default: private .env setting)",
    )
    args = parser.parse_args()
    try:
        terminal = load_terminal(args.terminal_id, args.terminal_code)
    except ValueError as error:
        parser.error(str(error))

    if not args.json:
        try:
            from air_index_tui import AirQualityApp
        except ModuleNotFoundError as error:
            print(
                f"Missing dashboard dependency: {error.name}. "
                "Run: python3 -m pip install -r requirements.txt",
                file=sys.stderr,
            )
            return 1
        AirQualityApp(
            refresh_interval=args.refresh,
            terminal_id=terminal.id, terminal_code=terminal.code,
        ).run()
        return 0

    try:
        payload = fetch_air_quality(terminal.id, terminal.code)
    except (URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
        print(f"Could not fetch air quality data: {error}", file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=4, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
