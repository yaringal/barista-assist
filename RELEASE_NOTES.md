# Barista Assist v0.2.18

Follow-ups to v0.2.17: a Tare safety fix, a live-shot graph, and a small display fix.

## Fixed

- **Tare could be pressed with no scale connected** - it's now disabled in that state, matching Brew and Abort.
- **Bean slot's dropdown now reads "Normal"/"Decaf"** instead of lowercase.

## Changed

- **"Live shot" now shows Weight and Flow rate as a single live graph** instead of two separate tiles, so you can watch the pour's shape over time. Flow rate is plotted ×10 scaled so it doesn't look flat next to weight.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
