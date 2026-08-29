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
      icon: mdi:coffee-maker
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

## Barista Express shot-duration safety requirement

Before using automatic brew control, **program the relevant CUP button (1-CUP or 2-CUP) on the Barista Express with a maximum shot duration that you know, then confirm that duration in Barista Assist**.

Barista Assist treats that value as the machine's hard physical upper bound. It applies a configurable safety margin (3 s by default) and will automatically stop before the machine's own programmed maximum.

This is important because a Barista Express can finish a programmed shot on its own. If the application waited until after that point and then pressed the brew button, the same button press could start a **new** shot. The same risk exists for aborts, so Barista Assist never sends a stop/abort button press after the protected window has passed. Instead it enters `manual_stop_required` and waits for the machine's programmed maximum to expire while continuing to log the shot.

Do not use automatic brew control until this machine limit has been physically programmed and confirmed in the integration options.


## Shot-data export

The Brew view includes **Copy all shot data**. It copies every stored shot and its raw BOOKOO time series as plain text, including recipe/context metadata, stop timing, sample count and a `post_stop` flag. The export is intended to be pasted directly into a diagnostics tool; samples recorded after the automatic/manual stop are preserved so late scale movement or other recording artefacts can be identified.
