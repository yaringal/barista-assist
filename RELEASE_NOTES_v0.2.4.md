# Barista Assist v0.2.4

A single-fix patch release: **fresh installs on Home Assistant 2026.8 were completely broken.** Also cleanup and a new logo.

## Fixed

`manifest.json` pinned `bleak-retry-connector==4.6.0`. Home Assistant 2026.8 pins that same shared dependency to `bleak-retry-connector==4.6.3` in its own package constraints, so installing Barista Assist on HA 2026.8 always failed with `RequirementsNotFound` — surfaced by the frontend as "Config flow could not be loaded: 500 internal server error." The pin now matches what HA 2026.8 already requires.

## Upgrade

No database migration is required. If you hit the config-flow 500 error on a previous version, update to this release and try adding the integration again.