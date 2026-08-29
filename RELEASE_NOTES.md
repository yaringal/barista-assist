# Barista Assist v0.2.7

Replaces the Community Dashboard strategy with a plain YAML-mode dashboard file: no new features, but the dashboard setup step and its underlying mechanism have changed.

## Changed

- **The dashboard no longer relies on Home Assistant's Community Dashboard strategy mechanism.** Live testing after v0.2.6 shipped showed the "timeout waiting for strategy element ... to be registered" error persisting even after that release's `Content-Type` fix, on both desktop and mobile browsers. Further investigation traced it to a bug in Home Assistant 2026.5's own brand-new browser-side strategy-registration feature (`window.customStrategies`) — the same failure affects several unrelated Home Assistant dashboard-strategy projects, and the community's own suggested workaround doesn't reliably fix it on mobile clients. Since this is a Home Assistant core issue outside this project's control, Barista Assist now writes a fully token-substituted YAML-mode dashboard file (`barista_assist_dashboard.yaml`) into your Home Assistant config directory on every setup/reload, instead of registering a strategy at all.
- **One-time setup has changed.** Instead of adding the dashboard from **Settings -> Dashboards -> Add dashboard -> Community dashboards**, you now add a short block to `configuration.yaml` once and restart Home Assistant. See the README's "Add the dashboard once" section for the exact snippet. This trades one menu click for one config edit, but removes the browser-side registration step entirely — the dashboard now loads reliably on both desktop and the Companion app.
- The dashboard file keeps auto-updating with every future release, exactly as before: it's fully regenerated from the packaged `dashboard.yaml` template on every integration load, so a HACS update still needs no repeat setup.
- Removed the v0.2.6 `.js` `Content-Type` fix and the dashboard-strategy JavaScript class — both were specific to the now-abandoned strategy mechanism. The shot-export card (**Copy all shot data**) and its WebSocket endpoint are unaffected.

## Upgrade

1. Update via HACS and restart Home Assistant as usual.
2. Add the `lovelace.dashboards` block from the README to `configuration.yaml`.
3. Restart Home Assistant once more (YAML-mode dashboards are only picked up on restart).
4. If you'd already added the old Community Dashboard, you can remove it from **Settings -> Dashboards** — it no longer registers or updates.

No database migration.
