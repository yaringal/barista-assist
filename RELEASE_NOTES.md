# Barista Assist v0.2.17

Dashboard fixes: entity labels no longer repeat the device name, and the Brew view is reorganized to be less cluttered.

## Fixed

- **"Bag details", "Current recipe", and "Connection and control" showed "Barista Assist" prefixed onto every entity label** (e.g. "Barista Assist New bag coffee" instead of "New bag coffee"). Those are `entities`-type cards, which render the full device-prefixed name unlike `tile` cards. Every row in all three cards now has an explicit short name.
- **Reworked the Brew view's layout:**
  - Last yield, Shot diagnosis, and Channeling suspicion moved into a new "Last brew" section at the bottom - they describe the previous shot, not the one in progress.
  - Stop compensation removed from the Brew view entirely (it's a global setting; it stays on the System view's "Connection and control" card).
  - The Active bag tile no longer duplicates the "Beans remaining" tile's value.
  - Brew/Tare/Abort are now one compact card instead of three separate tiles.
- **The Active bag tile still cut off its text (e.g. missing roast date) with a long coffee/roaster name, and looked narrower than its neighbors.** It was missing the same `vertical: true` layout its neighbors (Status, Beans remaining) already use, which gives the state text much more width to work with.
- **Tare could be pressed with no scale connected** - it's now disabled in that state, matching Brew and Abort.

## Changed

- **"Live shot" now shows Weight and Flow rate as a single live graph** instead of two separate tiles, so you can watch the pour's shape over time. Flow rate is plotted ×10 scaled so it doesn't look flat next to weight.
- **Bean slot's dropdown now reads "Normal"/"Decaf"** instead of lowercase.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
