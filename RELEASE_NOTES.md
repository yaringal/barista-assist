# Barista Assist v0.2.19

A rework of the "Live shot" graph: a real dual-axis chart that tracks one shot at a time.

## Changed

- **"Live shot" now uses the [ApexCharts Card](https://github.com/RomRider/apexcharts-card) instead of the built-in history graph.** This is a new dependency - install it once via HACS (Frontend → search "ApexCharts Card" → Download) before restarting; see the README's "Add the dashboard once" section. Weight and Flow rate now plot on genuinely separate axes (0-60g / 0-6 g/s), so the earlier ×10 flow-rate scaling trick is gone.
- **The graph now shows exactly one shot instead of a rolling time window.** It grows live while a shot is running, anchored to when that shot actually started, and freezes on the completed shot's data afterward - instead of continuing to scroll with the clock and losing the shot off-screen a minute or so later.

## Upgrade

No database migration. Install the ApexCharts Card via HACS, then do a full Home Assistant restart to pick up this release.
