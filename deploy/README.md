# CTA ETA Deployment Guide

Production deployment for the CTA train position and weather data collection system on Debian/Ubuntu.

---

## Prerequisites

- Debian/Ubuntu server (tested on Debian 12+)
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) installed system-wide
- A dedicated service user `cta-eta`:
  ```bash
  sudo useradd --system --home /opt/cta-eta --shell /bin/bash cta-eta
  sudo mkdir -p /opt/cta-eta
  sudo chown cta-eta:cta-eta /opt/cta-eta
  ```

---

## Installation

1. **Clone the repository** into the service home directory:
   ```bash
   sudo -u cta-eta git clone https://github.com/jdwh08/cta-eta /opt/cta-eta
   cd /opt/cta-eta
   ```

2. **Install dependencies** with uv:
   ```bash
   sudo -u cta-eta uv sync --no-dev
   ```
   This creates `/opt/cta-eta/.venv/` with all runtime dependencies and installs the CLI entry points (`cta-monitor`, `cta-alerts`, `cta-health`).

3. **Create the environment file** from the template:
   ```bash
   sudo -u cta-eta cp .env.template .env
   sudo -u cta-eta chmod 600 .env
   ```
   Fill in all required credentials in `/opt/cta-eta/.env`:
   - `CTA_API_KEY` — CTA Train Tracker API key
   - `NWS_APP_NAME` and `NWS_EMAIL` — National Weather Service contact info
   - `OPENWEATHERMAP_API_KEY` — fallback weather provider
   - `CHIDATA_APP_TOK` and `CHIDATA_APP_SECRET` — Chicago Data Portal credentials
   - Credentials for email alerting (see `config.toml` alerting section)

4. **Create runtime directories**:
   ```bash
   sudo -u cta-eta mkdir -p /opt/cta-eta/.daemon_state /opt/cta-eta/logs /opt/cta-eta/data
   ```

---

## Configuration

Edit `/opt/cta-eta/config.toml` for production settings (this file is version-controlled; override locally as needed):

- **`[storage]`** — Set `backend` to `"local"`, `"s3"`, or `"gcs"` and configure the corresponding bucket or path.
- **`[alerting]`** — Configure SMTP host, port, sender address, and recipient list for email alerts.
- **`[collection]`** — Adjust polling intervals if needed (`train_interval_seconds`, `weather_interval_minutes`).
- **`[logging]`** — Set `log_level` to `"INFO"` for production; `"DEBUG"` only for troubleshooting.
- **`[features]`** — Enable/disable individual collection features by toggling boolean flags.

---

## systemd Setup

Copy unit files and enable the services:

```bash
sudo cp /opt/cta-eta/deploy/*.service /opt/cta-eta/deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cta-train-daemon cta-weather-daemon cta-alerts.timer
sudo systemctl start cta-train-daemon cta-weather-daemon cta-alerts.timer
```

**Services installed:**

| Unit | Type | Purpose |
|------|------|---------|
| `cta-train-daemon.service` | Long-running | Polls CTA train positions every ~15 seconds |
| `cta-weather-daemon.service` | Long-running | Polls weather APIs every 30 minutes |
| `cta-alerts.service` | Oneshot | Runs alert checks (triggered by timer) |
| `cta-alerts.timer` | Timer | Triggers `cta-alerts.service` every 15 minutes |

Both daemons handle `SIGTERM` gracefully (60-second timeout before `SIGKILL`) to ensure clean shutdown without data loss.

---

## Log Rotation

Copy the logrotate configuration:

```bash
sudo cp /opt/cta-eta/deploy/logrotate.conf /etc/logrotate.d/cta-eta
```

Daemon stdout/stderr go to the **systemd journal** (managed automatically by journald). The logrotate config covers file-based logs written directly by the daemons (JSONL diagnostic events and general log files). See comments in `logrotate.conf` for the rationale for `copytruncate`.

To adjust journald retention, edit `/etc/systemd/journald.conf` and set `SystemMaxUse` (e.g., `SystemMaxUse=1G`).

---

## Monitoring

Three CLI tools are available in `/opt/cta-eta/.venv/bin/`:

- **`cta-monitor`** — Daemon status, recent errors, collection gaps, and metrics:
  ```bash
  sudo -u cta-eta /opt/cta-eta/.venv/bin/cta-monitor status
  sudo -u cta-eta /opt/cta-eta/.venv/bin/cta-monitor errors
  sudo -u cta-eta /opt/cta-eta/.venv/bin/cta-monitor gaps
  sudo -u cta-eta /opt/cta-eta/.venv/bin/cta-monitor metrics
  ```

- **`cta-health`** — Quick health check returning exit code 0 (healthy) or non-zero (degraded):
  ```bash
  sudo -u cta-eta /opt/cta-eta/.venv/bin/cta-health
  ```

- **`cta-alerts`** — Run an alert check immediately (normally run by timer):
  ```bash
  sudo -u cta-eta /opt/cta-eta/.venv/bin/cta-alerts
  ```

---

## Troubleshooting

**Check daemon logs:**
```bash
journalctl -u cta-train-daemon -f
journalctl -u cta-weather-daemon -f
journalctl -u cta-alerts.service
```

**Check service status:**
```bash
systemctl status cta-train-daemon cta-weather-daemon cta-alerts.timer
```

**Inspect daemon state files:**
```bash
ls -la /opt/cta-eta/.daemon_state/
# Heartbeat files updated every cycle; stale heartbeat = daemon stuck
cat /opt/cta-eta/.daemon_state/TrainPositionDaemon.heartbeat.json
cat /opt/cta-eta/.daemon_state/WeatherDaemon.heartbeat.json
```

**Run health check manually:**
```bash
sudo -u cta-eta /opt/cta-eta/.venv/bin/cta-health
```

**Check collection gaps:**
```bash
sudo -u cta-eta /opt/cta-eta/.venv/bin/cta-monitor gaps
```

---

## Updating

```bash
cd /opt/cta-eta
sudo -u cta-eta git pull
sudo -u cta-eta uv sync --no-dev
sudo systemctl restart cta-train-daemon cta-weather-daemon
sudo systemctl daemon-reload  # only needed if unit files changed
```

After updating, verify the daemons are running:
```bash
systemctl status cta-train-daemon cta-weather-daemon
```
