# Barista Assist v0.2.24

A reliability fix for dashboard cards intermittently failing to load (at the cost of one new manual setup step), plus a fix for the shot-data export silently truncating on the Companion app.

## Changed

- **Adding Barista Assist's dashboard resource is now a one-time manual step instead of automatic.** The integration previously tried to auto-register its own cards' JavaScript, but that mechanism turned out to be unreliable in practice - cards (especially the Shots view) could show "Configuration error (timeout)" on a restart, on both desktop and mobile, roughly 90% of the time. There's no fully automatic and reliable way to do this from an integration (registering a real Lovelace resource programmatically carries a real risk of wiping out your *other* Lovelace resources, due to a still-open Home Assistant core bug), so the fix is a one-time manual step instead - see the updated "Add the dashboard once" section in the README.
- The integration's static file server no longer sends cache headers, so browsers reliably pick up new versions of the bundled JS after future updates.

## Fixed

- **"Copy all shot data" silently truncated the export on the Home Assistant Companion app**, reporting "Copied to clipboard" even though the copy was incomplete. The app's WebView clipboard can cut off a large copy without raising an error, so this is no longer trusted there - the Companion app now goes straight to the manual copy-from-textbox option instead.

## Upgrade

**Action required**: after upgrading, go to Settings → Dashboards → ⋮ → Resources → Add Resource, and add:
- URL: `/barista_assist_static/barista-assist-dashboard.js`
- Resource type: JavaScript Module

Without this, the Shots view, the Live shot graph, and the shot-data export button won't reliably load. A full Home Assistant restart is also needed to pick up this release.

## Testing

- Full suite: 178 tests, all passing. This change lives in `__init__.py`'s integration setup flow, which has no existing automated test harness in this repo.
