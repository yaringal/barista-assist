# Barista Assist v0.3.6

Another load-failure fix, two chart accuracy bugs, and a new way to test your notification setup on demand.

## Added

- A "Send test notification" button (System view → Settings) sends a real test push through your configured notification service right away, so you can check it works without waiting for a real healthy shot.

## Fixed

- Fixed another "integration failed to load" error some of you hit after updating - a leftover blocking file read on startup. Now uses Home Assistant's own already-loaded integration data instead of reading the integration's own files again.
- A shot chart's "Weight (max ...g)" legend could show a higher number than the shot actually reached. The legend now always shows the real recorded max.
- A shot chart's plotted curve could quietly leave off the shot's own final (and highest) weight reading, for longer shots. It's now always included.
- The Pre-infusion/Healthy chart labels moved back under the chart (only Stop Prediction stays at the top), since Healthy was regularly overlapping Stop Prediction.

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Full suite: 326 tests, all passing (up from 321).
