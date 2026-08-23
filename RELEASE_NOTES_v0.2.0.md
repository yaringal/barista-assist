# Barista Assist v0.2.0

v0.2.0 is a readability/maintainability refactor with a non-destructive v0.1 upgrade path.

Highlights:

- `definitions.yaml` is now the declarative source of truth for recipe defaults, entities, ranges, DF54 steps, slots and dashboard tokens.
- First-bag default is now **18.0 g in -> 36.0 g out**.
- Pre-infusion is part of each bag recipe.
- Existing `barista_assist.brew`, `.abort`, `.tare` and `.select_slot` actions remain available.
- SQLite now focuses on durable bag/shot/sample history; lightweight UI/controller state is separated.
- Database schema uses versioned SQL migrations and preserves v0.1 history/PI during upgrade.
- Entity Python modules are generic and much easier to extend.
- Dashboard mappings come from the same YAML definitions instead of a second hard-coded map.
- 14 dependency-light tests pass, including a v0.1 database migration test.

Hardware behaviour still requires a water-only bench test with the actual BOOKOO scale and mounted SwitchBot before relying on automatic stopping.
