# Barista Assist

A self-contained Home Assistant custom integration for a non-invasive smart espresso workflow using:

- Sage/Breville Barista Express
- DF54 grinder
- BOOKOO Themis Ultra scale
- SwitchBot Bot on the brew button
- Home Assistant

Barista Assist is installed and updated as one HACS package. 

## What this integration does

- Connects directly to a BOOKOO Themis Ultra over Home Assistant Bluetooth.
- Records timestamped raw weight/flow/battery samples.
- Uses a SwitchBot `switch` entity as the brew actuator, with direct BLE configuration of its stored long-press duration.
- Programs per-bag SwitchBot long-press pre-infusion and controls automatic stop once weight reaches `target yield - stop margin`, where the stop margin is your Minimum early stop margin setting or a live flow-rate projection (capped at your Maximum early stop margin setting), whichever is larger (see "Adaptive stop margin" below), reprogramming the Bot back to an instant tap once extraction begins, so the stop/abort press itself is quick.

- Stores physical bags, recipes, shots, and raw samples in SQLite.
- Lists every stored shot in a Shots view, each expandable into full details and a weight/flow graph, with a delete option per shot.
- Classifies every finished shot's flow curve (`healthy`, `too_fast`, `too_restrictive`, `puck_prep_issue`, `invalid_measurement`) with a channeling-suspicion score, comparing against each bag's own recent healthy-shot history.
- Maintains separate **Normal** and **Decaf** active physical-bag slots.
- Tracks estimated remaining beans from logged doses.
- Enforces DF54 settings in `0.5`-unit increments.
- Exposes all controls as ordinary Home Assistant entities.
- Keeps a Home Assistant action API:
  - `barista_assist.brew`
  - `barista_assist.abort`
  - `barista_assist.tare`
  - `barista_assist.select_slot`
- Writes a YAML-mode Lovelace dashboard file, regenerated on every startup/reload, so the UI stays in sync with the installed release without any browser-side registration step.
- Migrates v0.1.x databases without deleting bags or shot history.

## Default starting recipe

The first bag in a slot starts at:

```yaml
dose_g: 18.0
grind: 15.0
target_yield_g: 36.0
temperature_offset_c: 0
preinfusion_s: 7
```

`18 g -> 36 g` is the neutral 1:2 starting recipe. It keeps sufficient espresso concentration for a flat white while leaving yield, temperature, dose, grind and PI available for later expert-rule adjustments.

Replacement bags inherit the current recipe from that slot rather than resetting to the defaults.

## Architecture

The integration is built around a declarative core (introduced in the v0.2.0 refactor):

```text
definitions.yaml
├── recipe defaults
├── entity ranges / steps
├── entity source mappings
├── Normal / Decaf slots
├── dashboard tokens
└── button actions

Python
├── BOOKOO BLE protocol/client
├── shot state machine
├── bag/recipe operations
├── SQLite repository
└── Home Assistant entity adapters

frontend/dashboard.yaml
└── package-owned stock Home Assistant UI
```

The principle is simple: **configuration and metadata live in YAML; control flow and safety-critical behaviour stay in Python.**

## Editable YAML

### `definitions.yaml`

The main source of truth for user-facing controls is:

```text
custom_components/barista_assist/definitions.yaml
```

For example, the DF54 is declared as:

```yaml
grind:
  source: bag
  field: grind
  translation_key: grind
  token: __GRIND__
  min: 0.0
  max: 100.0
  step: 0.5
  mode: box
  requires_bag: true
```

The definitions file is validated on integration setup and by the unit tests.

### `frontend/dashboard.yaml`

The visible dashboard is:

```text
custom_components/barista_assist/frontend/dashboard.yaml
```

It uses stock Home Assistant cards/features. On every setup/reload, the integration substitutes this template's entity-ID tokens and writes the result to `barista_assist_dashboard.yaml` in your Home Assistant config directory, which a YAML-mode dashboard entry reads (see "Add the dashboard once" below). The adjacent JavaScript file only registers the shot-export card used inside that dashboard.

Entity names and icons are provided by:

```text
translations/en.json
icons.json
```

## Home Assistant entities

Typical entities include:

```text
sensor.barista_assist_status
sensor.barista_assist_scale_weight
sensor.barista_assist_flow_rate
sensor.barista_assist_active_bag
sensor.barista_assist_last_yield
sensor.barista_assist_shot_classification
sensor.barista_assist_shot_channeling_suspicion
sensor.barista_assist_beans_remaining
sensor.barista_assist_stop_latency_normal
sensor.barista_assist_stop_latency_elevated

number.barista_assist_dose
number.barista_assist_grind
number.barista_assist_target_yield
number.barista_assist_preinfusion
number.barista_assist_early_stop_margin_min
number.barista_assist_early_stop_margin_max
number.barista_assist_machine_max_shot_seconds
number.barista_assist_safety_margin_seconds
number.barista_assist_machine_pi_seconds

select.barista_assist_bean_slot
select.barista_assist_temperature_offset

button.barista_assist_brew
button.barista_assist_abort
button.barista_assist_tare
button.barista_assist_create_bag
```

Entity IDs may differ if Home Assistant has retained registry names from an earlier install, but unique IDs remain stable across the v0.1 -> v0.2 upgrade.

## Stable action API

The integration deliberately retains its package-level Home Assistant actions for external automations:

```yaml
action: barista_assist.brew
```

```yaml
action: barista_assist.abort
```

```yaml
action: barista_assist.tare
```

```yaml
action: barista_assist.select_slot
data:
  slot: decaf
```

These are useful for watches, ESPHome buttons, NFC workflows, voice control, and ordinary HA automations without coupling those automations to a particular entity ID.

## Storage and migration

Research/history data lives in:

```text
.storage/barista_assist_<config_entry_id>.sqlite3
```

SQLite contains only durable coffee data:

- physical bags;
- bag recipes;
- shots;
- raw timestamped samples.

UI/application state such as selected slot and the early stop margin settings uses Home Assistant's lightweight storage mechanism. Unfinished new-bag form values are intentionally ephemeral.

Database schema is versioned with SQL migration files:

```text
custom_components/barista_assist/storage_migrations/
├── 001_initial.sql
└── 002_bag_preinfusion.sql
```

When upgrading from v0.1.x:

- existing bags and shots are retained;
- pre-infusion is added to each bag recipe;
- the previous global pre-infusion value is copied to existing active bags;
- the previous selected slot and stop compensation are adopted into the new lightweight state store on first load;
- the old `settings` SQL table is left intact but is no longer written by v0.2.

## Installation

### Preferred: HACS custom repository

On your Home Assistant instance:

1. Open **HACS**.
2. Open the **three-dot menu** in the top-right.
3. Choose **Custom repositories**.
4. Paste the repository URL, for example:
   `https://github.com/yaringal/barista-assist`
5. For **Type**, choose **Integration**.
6. Select **Add**.
7. Search HACS for **Barista Assist** and open it.
8. Select **Download** and choose the version you just released if HACS asks for one.

After HACS installs Barista Assist:

1. Restart Home Assistant.
2. Go to **Settings -> Devices & services -> Add integration -> Barista Assist**.
3. Turn on the Themis Ultra.
4. Select the scale.
5. Select the SwitchBot attached to the brew button.
6. Set the maximum shot safety timeout.

Recipe, PI and the early stop margin settings are deliberately **not** set in the setup flow; edit them from the Barista Assist entities/dashboard.

### Add the dashboard once

Every card in the packaged dashboard, including the Brew view's "Live shot" weight/flow chart, is either a built-in Home Assistant card type or one of Barista Assist's own bundled custom elements - no third-party card needs to be installed separately.

After the integration is set up, it writes its dashboard as a YAML file into your Home Assistant config directory:

```text
<HA config>/barista_assist_dashboard.yaml
```

This file is fully regenerated every time the integration (re)loads, so a future HACS update keeps the dashboard in sync automatically. Point a YAML-mode dashboard entry at it once, in `configuration.yaml`:

```yaml
lovelace:
  dashboards:
    barista-assist:
      mode: yaml
      title: Barista Assist
      icon: mdi:coffee-to-go
      show_in_sidebar: true
      filename: barista_assist_dashboard.yaml
```

Then restart Home Assistant (YAML-mode dashboards are only picked up on restart, not on a reload). The **Barista Assist** dashboard then appears in the sidebar and works identically in a browser and in the Companion app.

This replaces the Community Dashboard strategy used before v0.2.7 — Home Assistant's newer browser-side dashboard-strategy registration mechanism proved unreliable on some installs (a "timeout waiting for strategy element to be registered" error that a manual Lovelace-resource workaround didn't fix on mobile clients either). The YAML-mode file above uses no custom JavaScript or registration step, so it doesn't depend on that mechanism at all.

### Manual test installation

Extract the manual component ZIP and copy the contained `barista_assist` directory to:

```text
<HA config>/custom_components/barista_assist
```

Restart Home Assistant and add the integration normally.

## Safety / bench test

**Do not leave the espresso machine unattended.**

Before using coffee, test with water and confirm:

1. Brew starts correctly.
2. The configured Bot hold duration produces the intended PI.
3. A later Bot activation reliably stops extraction.
4. The physical brew button remains accessible for manual intervention.
5. The BOOKOO remains connected while the machine runs.
6. Abort and the maximum-shot timeout behave as expected.

Automatic stopping is convenience logic, not a substitute for supervision or the machine's own safety systems.

## Not implemented yet

- **acceleration-aware stop projection**: the adaptive stop margin (see below) projects from the *current* smoothed flow rate, not where flow is heading. Checked against the real shot that motivated the feature (36g target → 47.9g actual, flow accelerating ~4 → 8.67 g/s), the current-flow-only projection barely moves the stop point earlier at all for that specific shot - its acceleration happened *during* the stop-latency window itself, after the projection's decision point, so no amount of extrapolating the *current* rate can catch it. A first attempt at extrapolating the flow trend forward instead (projecting where flow is heading, not just where it is) was tried and rejected: checked against real shots, it badly over-fired during completely normal early-shot flow ramp-up and stopped two genuinely healthy shots several grams early - every shot's flow rises from zero at the start, and that ordinary rise isn't a danger signal the way a mid-shot acceleration is. The current-flow-only projection is still worth having on its own, for pours that are already fast/gushing before the margin is crossed (verified via `tests/fixtures/real_shots/violent_gush_machine_pi.txt`), but a trend-aware version needs either a fundamentally different shape (e.g. gated to only fire once a shot is well past its own initial ramp-up) or meaningfully more real shot history to tell normal onset apart from real danger - not just a better-tuned version of the same naive extrapolation;
- data-driven thresholds for flow-curve diagnostics, once enough shot history exists to derive them instead of using placeholder constants;
- automatic DF54 recommendations;
- sensory expert rules;
- Bayesian optimisation;
- automatic temperature-button actuation;
- actual pre-grind dose measurement;
- RH/temperature modelling.

## Testing

The dependency-light test suite covers:

- BOOKOO packet parsing/checksums;
- `definitions.yaml` validation;
- default 18 g -> 36 g recipe;
- DF54 0.5-step declaration;
- dashboard placeholder/definition consistency;
- bag replacement;
- partial recipe updates;
- shot storage and estimated bean use;
- v0.1 database migration and PI preservation.

Run:

```bash
python -m unittest discover -s tests -v
```

## Repository layout

```text
custom_components/barista_assist/
├── __init__.py
├── manifest.json
├── definitions.yaml
├── definitions.py
├── translations/
│   └── en.json
├── icons.json
├── entity.py
├── sensor.py
├── number.py
├── select.py
├── text.py
├── date.py
├── button.py
├── config_flow.py
├── services.py
├── services.yaml
├── runtime.py
├── storage.py
├── storage_migrations/
│   ├── 001_initial.sql
│   └── 002_bag_preinfusion.sql
├── bookoo.py
├── protocol.py
├── websocket.py
└── frontend/
    ├── dashboard.yaml
    └── barista-assist-dashboard.js
```

See `docs/DESIGN.md` for the longer-term project design.

## SwitchBot requirement

> **Strongly recommended: give this integration a dedicated ESPHome Bluetooth proxy** (see [setup instructions](#setting-up-an-esphome-bluetooth-proxy) below) rather than relying on your Home Assistant host's own Bluetooth adapter. This is the single highest-priority fix for the connection problems described below - live testing repeatedly hit `BleakOutOfConnectionSlotsError`/wedged connections on a shared adapter, and a dedicated proxy resolved it completely.

Barista Assist uses the existing Home Assistant SwitchBot entity for the actual button action, and opens a short direct BLE connection before each shot to program the Bot's stored long-press duration to the selected bag's pre-infusion time. SwitchBot documents this BLE command in its public Bot protocol. Once extraction begins, Barista Assist reprograms that same stored duration back down to an instant tap (0 s), so the later stop/abort press is quick instead of holding for the pre-infusion duration; if a stop is needed before that reprogram lands, it happens inline first rather than pressing with a stale hold time.

The brew Bot must be configured in **press / momentary mode**, not toggle/retract mode. Barista Assist will refuse to brew if the selected Bot reports switch mode.

**Needs at least 2 concurrent Bluetooth connection slots.** The BOOKOO scale holds one BLE connection continuously; brewing briefly opens a second, separate BLE connection to the SwitchBot Bot to (re)program its long-press duration. A single Bluetooth adapter or a single ESPHome Bluetooth proxy in range often only supports **one** connection at a time, in which case the Bot connection fails with something like `BleakOutOfConnectionSlotsError: ... No backend with an available connection slot ...` every time you brew while the scale is connected — this is a Bluetooth capacity limit, not a bug, and Barista Assist already degrades gracefully when it happens (it logs a warning and the Bot press falls back to holding for the full pre-infusion duration instead of an instant tap). Add a second [ESPHome Bluetooth proxy](https://esphome.io/projects/?type=bluetooth&diy) (or a proxy/adapter that supports multiple simultaneous connections) near the machine so the scale and the Bot can each hold their own connection.

**Running Home Assistant on a Raspberry Pi's own onboard Bluetooth adapter is a common way to hit this.** The RPi's built-in adapter has historically been unreliable at holding more than one active BLE connection open at once and toggling between them - live testing on one showed the scale connection and a brew Bot press interfering with each other even one at a time, with the Bot connection getting silently wedged (`BleakOutOfConnectionSlotsError` even on a fresh Home Assistant restart) until the Bluetooth integration itself was reloaded. If your HA host is a Raspberry Pi, don't rely on its onboard adapter for this integration - use a dedicated ESPHome Bluetooth proxy instead (see below), and reserve the Pi's own adapter for other, lighter Bluetooth use if you use it at all.

### Setting up an ESPHome Bluetooth proxy

An ESPHome Bluetooth proxy is a small, cheap ESP32-based device that gives Home Assistant an extra set of Bluetooth "ears" - each one adds its own independent connection slots that Home Assistant automatically load-balances across, on top of whatever your main adapter already provides.

1. Get any ESP32 board - a bare devkit, or a pre-made proxy like an M5Stack Atom Lite/S3, works fine.
2. Pick a ready-made DIY Bluetooth proxy project from [esphome.io/projects (Bluetooth, DIY)](https://esphome.io/projects/?type=bluetooth&diy) matching your board, and flash it via the web installer.
3. After flashing, follow the **"The device is adoptable in the ESPHome dashboard"** link the installer shows you - this adds it to your ESPHome dashboard (the `esphome` HA add-on, or the standalone dashboard) so you can manage/reconfigure it later. If the DIY project's default config doesn't come up correctly, edit it in the dashboard to make sure it has both components set to **active** mode (not just passive scanning) - see ESPHome's own [complete recommended Bluetooth proxy configuration](https://esphome.io/components/bluetooth_proxy/#complete-sample-recommended-configuration-for-a-wifi-connected-bluetooth-proxy) for the full file:

   ```yaml
   esp32_ble_tracker:
     scan_parameters:
       # We currently use the defaults to ensure Bluetooth
       # can co-exist with WiFi In the future we may be able to
       # enable the built-in coexistence logic in ESP-IDF
       active: true

   bluetooth_proxy:
     active: true
   ```
4. Home Assistant then auto-discovers it as a new **ESPHome** integration entry - accept the discovery notification (or add it manually under **Settings → Devices & Services → Add Integration → ESPHome**).
5. Place it physically near the espresso machine/scale - a proxy only sees devices within its own local BLE range, so it needs to actually be close enough to both the BOOKOO scale and the brew SwitchBot, not just anywhere in the house.
6. No further Barista Assist configuration is needed - once the proxy is online, Home Assistant automatically uses whichever available connection slot (local adapter or any proxy) can reach each device, so the scale and the Bot can each hold their own connection without you having to point either one at a specific adapter.

## Barista Express shot-duration safety requirement

**Barista Assist drives the 1-CUP/2-CUP button by holding it down** (that's how it programs your configured pre-infusion time), which Breville's own instruction books ([BES875](https://assets.breville.com/BES875/BES875_ANZ_IB_F22_FA_LR.pdf), [BES878](https://www.manualslib.com/manual/1580178/Breville-The-Barista-Pro-Bes878.html?page=15)) describe as a distinct "Manual Pre-Infusion & Extraction" mode - not the same as a plain single tap, which extracts to a pre-set volume and stops itself automatically.

The machine still has its own cutoff in this held mode - live testing confirms this, and it lines up with reports elsewhere that these machines track shot **volume** via an internal flow meter rather than pure elapsed time (e.g. a shot pulled with no portafilter/no grounds - i.e. almost no flow resistance - is reported to cut off after roughly 30 seconds too). A water-only bench test (no coffee) reproduced that cutoff at about 30 seconds total, including a 7 s pre-infusion hold. However do not assume the exact number from a water test carries over to a real coffee shot.

Given that, **you must still determine your own machine's real cutoff by testing it yourself, with a real coffee puck in the portafilter, not just water** - hold the button through a full bench-test shot (see "Safety / bench test" below) and time how long it takes before the machine stops itself. Program the relevant CUP button (1-CUP or 2-CUP) on the Barista Express with that duration, then confirm it in Barista Assist. Barista Assist treats that value as a hard ceiling for its own logic: past a safety margin (3 s by default) before it, Barista Assist stops sending stop/abort button presses entirely and enters `manual_stop_required` instead, because pressing too close to when a shot would naturally end risks starting a **new** shot instead of stopping the current one. In `manual_stop_required`, Barista Assist keeps logging the shot but takes no further automatic action - treat the machine's own cutoff as a last-resort backstop, not a substitute for watching the shot, since its exact timing (and whether it behaves identically for every recipe/dose) isn't something documented publicly with full confidence.

Do not use automatic brew control unless you're prepared to supervise every shot closely enough to intervene manually, and until this machine limit has been physically programmed and confirmed in the integration options.

## Adaptive stop margin

**Minimum early stop margin** and **Maximum early stop margin** live under the "Yield Prediction" subsection of System view → Connection and control, alongside two read-only sensors (**Learned latency (normal flow)**/**Learned latency (fast flow)**) showing the two latency estimates described below.

**Minimum early stop margin** is a floor rather than a flat margin applied unconditionally. There's real latency between deciding to stop and the pour actually stopping (BLE connect, the button press itself, the pump physically stopping, residual drips settling) - a fixed margin only lands close to target when the pour happens to be running at roughly the same rate it was calibrated against. A real shot overshot from a 36g target to 47.9g because flow was still accelerating (~4 → 8.67 g/s) right when the fixed margin was crossed - the same latency landed far more beverage than the margin assumed.

Barista Assist now also projects a margin from the shot's own live, smoothed flow rate (an estimate of that physical stop latency × the current flow rate) and uses **whichever is larger**: your calibrated Minimum early stop margin, or the live projection - clipped so it never exceeds your **Maximum early stop margin** setting, an independent, separately-tunable cap rather than a multiple of the floor, so raising one doesn't silently change how the other behaves. At a normal pour the projection stays below your calibrated minimum, so nothing changes - this is a one-sided adjustment, it can only add extra margin on top of what you've already tuned, never shrink below it. A pour running unusually fast gets the larger, projected margin instead (stopping earlier, in grams-so-far, since more will land before the mechanical stop completes), up to the maximum you've configured.

An earlier version of this instead tried to derive the physical stop latency *from* the minimum margin itself (dividing it by a generic, cross-installation reference flow rate) and let the live projection replace the flat margin entirely rather than only raise it. For an installation calibrated well above that generic reference, this implied a physically impossible ~6 second stop latency and triggered a real good shot's stop 6g/17% early with no actual problem to react to - a regression, not an improvement. The floor-based design above was adopted specifically because it's provably safe regardless of how the latency estimate is tuned: it can only help, never regress a shot that was already stopping correctly.

The latency estimate itself isn't a fixed constant - it's learned continuously from your own machine's shots, and isn't a single number either. Checked against real shots, a shot's own flow rate at the stop decision predicts almost perfectly how much extra latency it needs (correlation 0.97 across 5 real shots): a fast-flowing shot's residual drip doesn't behave like a normal-paced one's, and averaging both into one shared estimate risked dragging it past the point where the floor still protects an ordinary shot. So there are two independently-learned latency estimates instead of one - a "normal" bucket for shots flowing under 3.0 g/s at the moment of the stop decision, and an "elevated" bucket for shots at or above it - each seeded from real shots on its own side of that cutoff (3.4s and 4.3s respectively) and nudged a small step toward what each newly-completed shot on its side actually needed. Predictive, flow-rate-based stop-by-weight is an established technique (La Marzocco/Acaia's Connected Scale and the open-source Gaggiuino project both do a version of it), and per-installation online calibration - a small step at a time from real outcomes, rather than a one-time batch fit from a handful of historical shots - is the standard way these systems are actually tuned in practice: a value hand-derived from just two or three shots didn't reliably generalize, since real per-shot latency (BLE timing, how a specific pour's residual drip behaves) varies more than a single number can capture. No single unusual shot can swing either estimate by much; each converges over real usage instead, bounded to [0.5s, 8s] regardless of what any one shot implies. Recording also now waits a bit longer after a stop/abort press before finalizing the shot and recording its final yield - long enough to comfortably exceed the larger of the two learned latencies, since a fixed, too-short window was previously found to cut a real shot's tail off before it had actually finished, both under-recording that shot's yield and denying the learning step above an accurate observation to work from. A shot may take a couple of seconds longer to return to "ready to brew" after stopping as a result.

## Adapt PI

Adapt PI is a toggle switch (System view → Connection and control) that controls whether Barista Assist **adapts the pre-infusion duration itself**, per bag, or leaves pre-infusion entirely to the machine's own built-in default.

**Adapt PI enabled (default, "App-controlled"):** Barista Assist holds the button for the active bag's own Pre-infusion recipe value (the "Manual Pre-Infusion & Extraction" mode described above), so the pre-infusion duration follows whatever's set per bag/recipe. Barista Assist still taps the button a second time to stop the pour once the target weight is reached.

**Adapt PI disabled ("Machine-controlled"):** Barista Assist sends a single short tap instead, letting the Barista Express run its own built-in pre-infusion - whatever duration is physically programmed into that CUP button - before ramping to full pressure. Barista Assist can't observe or control that duration, so the **Machine pre-infusion** setting (System view → Connection and control) is where you tell it what your machine's own pre-infusion actually is, so shot records log the true value used instead of a guess; update it if you reprogram the machine's own pre-infusion timing. It's always visible there (not just while Adapt PI is off), so you can set it up in advance.

With Adapt PI disabled:

- **No direct BLE connection is used at all.** Both the start and stop presses go through Home Assistant's own SwitchBot integration (`switch.turn_on`) - Barista Assist never opens its own BLE session to reprogram the Bot's long-press duration, since there's no hold duration to configure.
- **The Pre-infusion tile is hidden** from the Recipe section of the dashboard as soon as you flip the toggle off, since the duration is fixed by the machine rather than something Barista Assist controls per bag.
- The single tap starts what Breville's manuals describe as "Pre-Programmed Shot Volume" mode, which has its own auto-stop at whatever volume is currently programmed into the CUP button - Barista Assist's own stop press is meant to land well before that, but if it's ever late, the same safety-margin/`manual_stop_required` logic as the normal mode still applies (see above), and the machine's programmed volume becomes the backstop instead of its water-only volume-based cutoff.

## Shot history

The **Shots** view lists every stored shot, most recent first (`dd/mm hh:mm:ss`). Click a row to expand it into full recipe/result details and a weight/flow graph of that shot's raw samples, including its roaster, **Total duration** (from the first brew press to the stop/abort press - not the extra settle time recorded afterward), and **Effective stop margin**: the actual live-projected margin used at that shot's own automatic stop decision (blank for a manually aborted/timed-out shot, since none was ever computed), which can run higher than your configured Minimum early stop margin for a fast-flowing shot - see "Adaptive stop margin" above. Each row also has a delete button (with a confirmation prompt) for removing shots you don't want kept - a bad bench test, a duplicate, or anything else cluttering your history. Deleting a shot also removes its raw samples and updates that bag's estimated remaining beans accordingly. The shot currently brewing can't be deleted.

## Shot-data export

The Brew view includes **Copy all shot data**. It copies every stored shot and its raw BOOKOO time series as plain text, including recipe/context metadata, stop timing, sample count and a `post_stop` flag. The export is intended to be pasted directly into a diagnostics tool; samples recorded after the automatic/manual stop are preserved so late scale movement or other recording artefacts can be identified.

## Debug logging

For BLE/timing issues (the most common source of real-world problems), enable debug logging for the integration in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.barista_assist: debug
```

This logs every shot-phase transition, brew/stop/abort call (with elapsed time and reason), scale connect/disconnect events, and every brew-Bot connect/program/press attempt - including `_bot_lock` waits and reconnect-retries - which is usually enough to tell whether a stuck or failed shot was a Bluetooth capacity/timing issue versus something else. Restart Home Assistant (or reload the integration) after changing this.

## Troubleshooting

**A card shows "Configuration error" on the Home Assistant Companion app, but the same dashboard works fine in a desktop browser.** This is a stale cache in the Companion app, not an actual problem with the dashboard config (a working desktop browser proves the YAML itself is valid) - the mobile app can keep serving an older cached copy of Barista Assist's own bundled `barista-assist-dashboard.js` after an integration update adds a new card type or config option it doesn't recognize yet. Fix: force-quit the Companion app, then **Settings → Companion App → Debugging → Reset frontend cache** (or clear the app's storage/cache from your phone's OS settings if that option isn't available), then reopen it.
