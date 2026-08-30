# Barista Assist v0.2.20

A safety fix for a real stuck-shot bug, plus Bluetooth setup guidance from live testing.

## Fixed

- **A failed stop/abort press could leave a shot permanently stuck**, with Brew never re-enabling and Abort clicks doing nothing - the only way out was restarting or reloading. Root cause: a flag meant to stop two concurrent presses racing was left `True` after a failed attempt, so every later stop/abort attempt (manual or automatic) silently no-op'd. A retry now actually works.

## Changed

- Flow rate is now drawn behind Weight in the "Live shot" graph, at 10% opacity, so it reads as a subtle backdrop instead of competing with the weight curve.

## Docs

- Documented that a Raspberry Pi's onboard Bluetooth adapter is a common way to run into the connection-slot limit described in the SwitchBot requirement section, with step-by-step instructions for setting up an ESPHome Bluetooth proxy instead.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
