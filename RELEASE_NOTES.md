# Barista Assist v0.3.3

A "too fast"/"too restrictive" cleanup: the duration-ratio bands that classify a shot's health are now a single shared source of truth for both classification and grind correction, instead of two copies that could quietly drift apart. Shot charts also get a clearer visual: a shaded healthy-completion window with a dashed target line, and region labels right under the chart.

## Changed

- The 6 duration-ratio band boundary settings (previously named `grind_band_*_max`) now drive both shot health classification and grind correction together, and have been renamed to `duration_ratio_band_*_max` to reflect that. The grind-correction delta settings (`grind_band_*_delta`) are unchanged.
- The System view's grind-correction card is now split into "Shot health categories" (the shared band boundaries) and "Grind correction" (grind deltas + puck-prep overrides).
- Shot charts now shade the healthy completion window in green with a dashed target-yield line, and label the pre-infusion/healthy/stop regions directly under the chart.

## Upgrade

Update via HACS and restart Home Assistant as usual. The 6 renamed entities (`duration_ratio_band_*_max`) will appear as new entities; the old `grind_band_*_max` entities become unavailable and can be removed from the entity registry. Any custom dashboard overrides you'd set on the old entities are not carried forward - reapply them on the new ones if needed. The 7 `grind_band_*_delta` entities are untouched.

## Testing

- Full suite: 318 tests, all passing.
