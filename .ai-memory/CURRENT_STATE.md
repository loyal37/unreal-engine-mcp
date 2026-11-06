# Current State

## All three phases built (2026-11-06)

- Server v0.1.0: 34 tools (core: status, Python, console, log, screenshot, assets, material parameters with instance-editor visibility, graph read/edit, expose parameters, instance overrides/create, mesh slots, slots_override, texture check, save; plugin: toggle graph describe/check/edit/create/refresh/generate, classic toggles, material instance builder, panel scan/generate; workflow: slot name matching, cooked export). Repo commits up to this file's commit; see `git log`.
- Verified: 18 offline unit tests (fake editor, protocol, logs, cooked export incl. the selection-key hash against HT's real ini); 51/51 live checks against a scratch editor with HT 1.6.4 / panel 0.1.26 loaded (`tests/live_scratch.py --plugins`); read-only demo against the user's running HT editor (MI_player_090_elyms_body parameters with visibility: 15 parameters, 7 hidden; player_090_elyms_skin: 41 slots).
- Plugin side: HT `0d0a05b` (1.6.4) and panel `29a7839` (0.1.26) pushed; NOT installed in HT yet (user's editor open): `D:\ueproject\HT\Saved\Install-20261106-1.6.4-0.1.26\Install.ps1`. Until installed, the user's editor uses the Python fallbacks and the plugin tools report that the library is missing.
- NOT registered with Claude Code yet: registration is persistent configuration and needs the user's OK (command in README "Setup").
