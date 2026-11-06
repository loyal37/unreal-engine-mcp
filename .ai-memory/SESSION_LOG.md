# Session Log

## 2026-11-06 - Plan and repo setup

- Requested from the HT nested-toggle session after the user found headless commandlet runs too slow. Plan given first; the user asked for a mod-focused MCP (call the HT plugins, edit material instances), pointed at GitHub projects for reference (runreal/unreal-mcp, aadeshrao123/Unreal-MCP, ChiR24/Unreal_mcp, uuuuzz/UEBridgeMCP), then said to build it in `E:\unreal engine mcp` with `.ai-memory` first and finish all three phases.
- Verified: remote execution discovery works with bind `0.0.0.0`; one command connection per editor; engine client truncates large replies; plugin generators are not script-callable.

## 2026-11-06 - Phases 1-3 built

- Phase 1: server, transport, helper and core tools; read-only demo on the user's HT editor; mutating tools checked on a scratch editor. Found on the way: the editor reads a command in one 8 KB Recv (requests go through files), `GetMaterialPropertyInputNode` has no null check (outputs whitelisted), MaterialEditingLibrary does not Modify() (helper does), Python cannot list open asset editors or set the instance factory's parent.
- Phase 2: HT 1.6.4 / panel 0.1.26 script libraries (pushed, staged for install), plugin tools; 51/51 live checks with the libraries loaded, engine visibility agreed with the Python port on the test material.
- Phase 3: slot name matching and cooked export ported to Python; toggle graph editing through the library. Build host and scratch project recycled; AutoLoadProject.txt / EditorSettings.ini restored after every scratch run.
