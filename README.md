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
| Time asleep, Time in bed | h | From the most recent main sleep (naps are skipped) |
| Deep sleep, Light sleep, REM sleep | h | Same session. Attribute: `minutes` |
| Bedtime, Wake time | timestamp | Start and end of that sleep |

Sleep durations are in hours so Home Assistant shows them as "7h 31m". Each one has
the exact whole minutes in its `minutes` attribute, which is the better value for
automations.

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

## Importing history

Charts only show what Home Assistant recorded, so they start on the day you set
things up. To fill in the past, run the **Import history** action
(`google_health_vitals.import_history`) from **Developer tools → Actions**:

```yaml
action: google_health_vitals.import_history
data:
  days: 7300
```

It fetches past days from Google Health and adds them to the long-term statistics
of these sensors and of the Google Health integration's own resting heart rate,
weight, body fat, sleep, steps, distance, active and total calories, floors, water and
calories eaten. `days` goes up to 7300 (20 years); asking for more than Google has is
fine, it imports what exists. Each day becomes
one statistics row at local midnight (the wake-up day for sleep), so daily charts
such as the statistics graph card show it.

- Hours Home Assistant already recorded are never touched, and running it again
  just rewrites the imported days with the same values.
- Daily totals like steps are chained onto the recorded running total, so today's
  count stays right.
- Daily totals are fetched from the first day with steps, so a long range doesn't
  mean years of empty requests. If Google asks it to slow down it waits and retries,
  and a metric that fails is skipped and reported rather than stopping the import.
- The response lists how many days were imported per sensor, and why any sensor
  was skipped (for example a metric your device doesn't record).
- History lives in statistics, not in the state history, so the history graph card
  won't show it; use the statistics graph card.

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
