"""Constants for the Google Health Vitals integration."""

from datetime import timedelta

DOMAIN = "google_health_vitals"

# The built-in integration whose sign-in this integration borrows.
GOOGLE_HEALTH_DOMAIN = "google_health"

CONF_SOURCE_ENTRY_ID = "source_entry_id"

UPDATE_INTERVAL = timedelta(minutes=30)
# After a failed request (the API returns the odd 503), try again soon rather
# than leaving the sensor stale or unknown for half an hour.
RETRY_INTERVAL = timedelta(minutes=2)

# Daily metrics are computed once per night, so a week of history is plenty to
# find the newest value even after a few days without a sync.
DAILY_LOOKBACK = timedelta(days=7)
SLEEP_LOOKBACK = timedelta(hours=48)
