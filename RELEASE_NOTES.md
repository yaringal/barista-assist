# Barista Assist v0.3.13

Steadier auto-stop, more trustworthy shot diagnoses, and fixes from live use - checked against 37 real recorded shots.

## Fixed

- **Shots stopping far too early.** A brief burst of flow right before the stop could make the auto-stop fire several grams early. Flow is now averaged over the last 2 seconds before deciding when to stop.
- **Early-stopped shots blamed on the grind.** A shot the auto-stop ended well short of target used to be called "too restrictive". It's now marked as an invalid measurement instead, with no grind change suggested. Shots that really choke (run to the time limit) are still "too restrictive".
- **The grey pre-infusion band drifting.** It used to move whenever the learned stop latency changed, and shot diagnoses moved with it. Both now use fixed timings measured on the real machine, and good shots now sit in the middle of the healthy range.
- **Taste feedback after a big overshoot.** If a shot lands more than 10% off its target yield, its taste answers are still saved but no longer change the recipe - a "thin" shot at 43g out of 18g is about the yield, not the dose. The recommendation card says when this happens.
- **Chart spike at the start.** Stray scale readings from before it settled are no longer drawn, so the weight axis isn't squashed.
- **Taste notifications not clearing on Android 16.** Notifications are now grouped, the known workaround.
- A scale wobble during pre-infusion could wrongly mark a good shot as an invalid measurement.

## Changed

- **One "Learned stop latency"** replaces the separate normal-flow and fast-flow values. Your current normal-flow value carries over automatically.
- **"Send test notification"** now sends the same two taste questions a real shot does (marked "[Test]"), so you can check both arrive and clear. Answering them doesn't record anything.

## Added

- **Installed version** shown on the Settings tab.
- Each shot now records how long the brew Bot took to confirm the stop press (in the shot export), to help track down overshoots.
- The chart legend explains the time axis: seconds since the brew press.

## Upgrade

Update via HACS, then **restart Home Assistant** (the update doesn't take effect until you do - the new "Installed version" row shows which release is running).

- The database is updated automatically on first start.
- The two old sensors **"Learned stop latency (normal flow)"** and **"Learned stop latency (fast flow)"** are replaced by one **"Learned stop latency"**. The old ones will show as unavailable - you can delete them under Settings → Devices & services → Entities. The packaged dashboard already uses the new one.
- Shot diagnoses for Bot-held shots will shift slightly (toward healthy for shots that used to read a little restrictive).

## Testing

- Full suite: 367 tests, all passing (up from 349).
