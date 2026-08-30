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
- Programs per-bag SwitchBot long-press pre-infusion and controls automatic stop at:

  `target yield - stop compensation`

  reprogramming the Bot back to an instant tap once extraction begins, so the stop/abort press itself is quick.

- Stores physical bags, recipes, shots, and raw samples in SQLite.
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

number.barista_assist_dose
number.barista_assist_grind
number.barista_assist_target_yield
number.barista_assist_preinfusion
number.barista_assist_stop_compensation

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

UI/application state such as selected slot and stop compensation uses Home Assistant's lightweight storage mechanism. Unfinished new-bag form values are intentionally ephemeral.

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

Recipe, PI and stop compensation are deliberately **not** set in the setup flow; edit them from the Barista Assist entities/dashboard.

### Add the dashboard once

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

- adaptive/predictive tail compensation;
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

Barista Assist uses the existing Home Assistant SwitchBot entity for the actual button action, and opens a short direct BLE connection before each shot to program the Bot's stored long-press duration to the selected bag's pre-infusion time. SwitchBot documents this BLE command in its public Bot protocol. Once extraction begins, Barista Assist reprograms that same stored duration back down to an instant tap (0 s), so the later stop/abort press is quick instead of holding for the pre-infusion duration; if a stop is needed before that reprogram lands, it happens inline first rather than pressing with a stale hold time.

The brew Bot must be configured in **press / momentary mode**, not toggle/retract mode. Barista Assist will refuse to brew if the selected Bot reports switch mode.

**Needs at least 2 concurrent Bluetooth connection slots.** The BOOKOO scale holds one BLE connection continuously; brewing briefly opens a second, separate BLE connection to the SwitchBot Bot to (re)program its long-press duration. A single Bluetooth adapter or a single ESPHome Bluetooth proxy in range often only supports **one** connection at a time, in which case the Bot connection fails with something like `BleakOutOfConnectionSlotsError: ... No backend with an available connection slot ...` every time you brew while the scale is connected — this is a Bluetooth capacity limit, not a bug, and Barista Assist already degrades gracefully when it happens (it logs a warning and the Bot press falls back to holding for the full pre-infusion duration instead of an instant tap). If you hit this, add a second [ESPHome Bluetooth proxy](https://esphome.github.io/bluetooth-proxies/) (or a proxy/adapter that supports multiple simultaneous connections) near the machine so the scale and the Bot can each hold their own connection.

## Barista Express shot-duration safety requirement

**Barista Assist always drives the 1-CUP/2-CUP button by holding it down** (that's how it programs your configured pre-infusion time), which Breville's own instruction books ([BES875](https://assets.breville.com/BES875/BES875_ANZ_IB_F22_FA_LR.pdf), [BES878](https://www.manualslib.com/manual/1580178/Breville-The-Barista-Pro-Bes878.html?page=15)) describe as a distinct "Manual Pre-Infusion & Extraction" mode - not the same as a plain single tap, which extracts to a pre-set volume and stops itself automatically.

The machine still has its own cutoff in this manual/held mode - live testing confirms this, and it lines up with reports elsewhere that these machines track shot **volume** via an internal flow meter rather than pure elapsed time (e.g. a shot pulled with no portafilter/no grounds - i.e. almost no flow resistance - is reported to cut off after roughly 30 seconds too). A water-only bench test (no coffee) hit that cutoff at about 30 seconds total, including a 7 s pre-infusion hold. Whether that's a fixed, generic safety timer, or tied to whatever volume is *currently programmed* into that CUP button - the BES875 manual lists 30ml/60ml as the 1-CUP/2-CUP **defaults** for single-tap mode, suspiciously close to that 30-second water result - isn't confirmed; either way, **how long it takes depends on how fast liquid is actually flowing**, so a real shot through a resistive coffee puck should take meaningfully longer to reach the same cutoff than a fast, unrestricted water test did. Do not assume the exact number from a water test carries over to a real coffee shot.

Given that, **you must still determine your own machine's real cutoff by testing it yourself, with a real coffee puck in the portafilter, not just water** - hold the button through a full bench-test shot (see "Safety / bench test" below) and time how long it takes before the machine stops itself. Program the relevant CUP button (1-CUP or 2-CUP) on the Barista Express with that duration, then confirm it in Barista Assist. Barista Assist treats that value as a hard ceiling for its own logic: past a safety margin (3 s by default) before it, Barista Assist stops sending stop/abort button presses entirely and enters `manual_stop_required` instead, because pressing too close to when a shot would naturally end risks starting a **new** shot instead of stopping the current one. In `manual_stop_required`, Barista Assist keeps logging the shot but takes no further automatic action - treat the machine's own cutoff as a last-resort backstop, not a substitute for watching the shot, since its exact timing (and whether it behaves identically for every recipe/dose) isn't something documented publicly with full confidence.

Do not use automatic brew control unless you're prepared to supervise every shot closely enough to intervene manually, and until this machine limit has been physically programmed and confirmed in the integration options.

## Auto PI

Auto PI is a toggle switch (System view → Connection and control) that switches brewing from holding the button (the "Manual Pre-Infusion & Extraction" mode described above) to a single short tap, letting the Barista Express run its own built-in pre-infusion (assumed to be 8 s) before ramping to full pressure. Barista Assist still taps the button a second time to stop the pour once the target weight is reached, exactly as it does in the normal mode.

With Auto PI enabled:

- **No direct BLE connection is used at all.** Both the start and stop presses go through Home Assistant's own SwitchBot integration (`switch.turn_on`) - Barista Assist never opens its own BLE session to reprogram the Bot's long-press duration, since there's no hold duration to configure.
- **The Pre-infusion tile is hidden** from the Recipe section of the dashboard as soon as you flip the toggle, since the duration is fixed by the machine rather than something Barista Assist controls per bag.
- The single tap starts what Breville's manuals describe as "Pre-Programmed Shot Volume" mode, which has its own auto-stop at whatever volume is currently programmed into the CUP button - Barista Assist's own stop press is meant to land well before that, but if it's ever late, the same safety-margin/`manual_stop_required` logic as the normal mode still applies (see above), and the machine's programmed volume becomes the backstop instead of its water-only volume-based cutoff.

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
