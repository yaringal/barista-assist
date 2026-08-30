# Barista Assist v0.2.17

Dashboard fixes: entity labels no longer repeat the device name, and the Brew view is reorganized to be less cluttered.

## Fixed

- **"Bag details", "Current recipe", and "Connection and control" showed "Barista Assist" prefixed onto every entity label** (e.g. "Barista Assist New bag coffee" instead of "New bag coffee"). Those are `entities`-type cards, which render the full device-prefixed name unlike `tile` cards. Every row in all three cards now has an explicit short name.
- **Reworked the Brew view's layout:**
  - Last yield, Shot diagnosis, and Channeling suspicion moved into a new "Last brew" section at the bottom - they describe the previous shot, not the one in progress.
  - Stop compensation removed from the Brew view entirely (it's a global setting; it stays on the System view's "Connection and control" card).
  - The Active bag tile no longer duplicates the "Beans remaining" tile's value, which also fixed the roast-date text overflowing.
  - Brew/Tare/Abort are now one compact card instead of three separate tiles.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
