# InPost air quality dashboard

A terminal dashboard featuring color-coded air quality,
particle and weather readings, automatic refresh, and an expandable JSON view.

## Run

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# On a new installation, copy .env.example to .env and fill in both settings.
python air_index.py
```

### Controls

- **R** — refresh now (also available as a button).
- **J** — expand or collapse the raw JSON response.
- **T** — change the InPost terminal by ID and code; **Enter** applies, **Esc** cancels.
- **Q** — quit.
- Scroll with the mouse or use **Tab** to focus the dashboard, then the arrow keys.

The layout adapts to smaller terminals. Requests run in the background so the UI
stays responsive. Failed updates keep the last successful readings and display
an error; automatic refresh continues.

```bash
python air_index.py --refresh 30  # Refresh every 30 seconds (default: 60).
python air_index.py --json        # Original pretty JSON output; no dependencies needed.
python air_index.py --terminal-id YOUR_TERMINAL_ID --terminal-code YOUR_TERMINAL_CODE
python air_index.py --terminal-id YOUR_TERMINAL_ID --terminal-code YOUR_TERMINAL_CODE --json
```

Default station settings are loaded from the **private `.env` beside `air_index.py`**,
regardless of the current working directory. Set `INPOST_TERMINAL_ID` and
`INPOST_TERMINAL_CODE`; `.env.example` contains empty placeholders, not real data.
The application requires both settings unless supplied through CLI options.
Quoted values and comments are supported; values are not shell-expanded.

CLI options override `.env`. Codes are normalized to uppercase. Changes made in
the dashboard apply to this session only and do not modify `.env`.
Switching terminals clears old readings and ignores any late response from the
previous terminal. Not all InPost terminals necessarily provide air quality data.

**Privacy:** `.env` is ignored by Git. Do not commit or share it. The dashboard
still displays the selected station locally, so screenshots can reveal it. CLI
arguments can also appear in shell history and process listings; prefer `.env`.
Ignoring a file does not remove sensitive data from earlier Git commits or shared
copies. Tests use synthetic station settings and do not read your private `.env`.

Particle bars use the reference percentages supplied by the API. They are not a
separately calculated air quality index. Missing or invalid sensor values display
as unavailable, not as zero.

## Tests

```bash
python -m unittest discover -s tests -v
```
