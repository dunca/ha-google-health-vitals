# Google Health Vitals

A Home Assistant custom integration that adds the metrics the built-in
[Google Health](https://www.home-assistant.io/integrations/google_health/) integration
doesn't cover yet:

| Sensor | Unit | Notes |
| --- | --- | --- |
| Blood oxygen | % | Nightly average SpO2. Attributes: `lower_bound`, `upper_bound`, `date` |
| Heart rate variability | ms | Nightly average. Attributes: `deep_sleep_rmssd`, `non_rem_heart_rate`, `date` |
| Breathing rate | br/min | Nightly average |
| Skin temperature variation | °C | Last night compared with your baseline |
| Cardio fitness | mL/kg/min | VO2 max. Attributes: `fitness_level`, `estimated`, `date` |
| Deep sleep, Light sleep, REM sleep | min | From the most recent main sleep (naps are skipped) |
| Bedtime, Wake time | timestamp | Start and end of that sleep |

Every daily value carries a `date` attribute: the day Google computed it for. Values
only appear once your device has synced and Google has processed the night, and a
sensor stays `unknown` when your device doesn't record that metric.

## No second sign-in

This integration borrows the Google sign-in of your existing Google Health
integration. There's no Google Cloud setup, no extra credentials, and no second
re-authentication. When the Google Health integration needs to re-authenticate,
these sensors go unavailable and recover on their own once you've done it.

Its sensors live on a `<account> vitals` device linked under the Google Health
account device.

## Requirements

- Home Assistant 2026.8 or newer
- The Google Health integration, set up and working

The sign-in already includes the permissions these metrics need
(`health_metrics_and_measurements` and `sleep`), so nothing changes on the Google side.

## Installation

### HACS

1. HACS → three-dot menu → **Custom repositories**
2. Add `https://github.com/dunca/ha-google-health-vitals` with type **Integration**
3. Download **Google Health Vitals** and restart Home Assistant

### Manual

Copy `custom_components/google_health_vitals` into your `config/custom_components`
folder and restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → Google Health Vitals**, then pick
the Google Health account.

## How it works

Every 30 minutes the integration asks the Google Health API for the last 7 days of
each daily metric and keeps the newest one, plus the sleep sessions from the last 48
hours, from which it takes the most recent finished main sleep. It uses the same
[`google-health-api`](https://github.com/allenporter/python-google-health-api) library
version as the built-in integration, so nothing extra gets installed.

## Development

```bash
uv venv && uv pip install "homeassistant==2026.9.4" "google-health-api==0.9.0" pytest-homeassistant-custom-component
.venv/bin/pytest
```

## License

MIT
