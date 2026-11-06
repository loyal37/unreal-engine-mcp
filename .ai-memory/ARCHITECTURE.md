# Architecture

Three layers. Only layer 3 needs C++, and every tool that does not need it works without it.

1. **MCP server** (`server/ue_mcp`, out of process, stdio JSON-RPC, standard library only; run with `D:\Python312\python.exe server\run_server.py --project <project>`).
   - `mcp_stdio.py`: protocol loop (initialize, tools/list, tools/call, ping); unknown arguments are refused.
   - `remote_exec.py`: client for the PythonScriptPlugin remote execution protocol (UDP multicast discovery bound like the editor, TCP command channel on an ephemeral port the editor connects back to, reads until the JSON reply is complete, refuses commands over 6000 bytes).
   - `machine_lock.py`: named Win32 mutex per editor node; held around open -> command -> close.
   - `editor.py`: `EditorLink.call(func, args)` writes `<tmp>/ue-mcp/calls-<pid>/<id>.req.json`, sends the bootstrap (imports the helper from this repo, `ensure_current(<hash of helper sources>)`, `handle(req, resp)`), reads `.resp.json`.
   - `app.py`: shared state: log file per editor node (found through a marker line), editor PID, log offsets.
   - `logs.py`, `screenshot.py` (PrintWindow + own PNG encoder), `cooked.py` (Cooked Asset Exporter port): run outside the editor.
   - `tools.py` (core tools), `tools_plugins.py` (plugin tools, slot matching, cooked export).
2. **Editor-side helper** (`server/ue_mcp/editor_side/ue_mcp_editor/`): `core` (dispatch, JSON, transactions, open-editor detection), `status` (status, log marker, run_python with a namespace that survives reloads in module `ue_mcp_state`, console), `assets`, `materials` (graph access, Python port of GetVisibleMaterialParameters, parameter listing, graph edits, instance overrides), `meshes`, `textures`, `plugins` (script library calls, slot name matching).
3. **Plugin script APIs** (C++, in the HT plugin repos): `UHTModScriptLibrary` (HTBlueprintToggleTool 1.6.4: visibility, expression list, static switch override state/clear, open asset editors, toggle graph describe/check/refresh/generate/edit, classic toggle generator, material instance builder) and `UNTEPanelScriptLibrary` (NTEPanelSystem 0.1.26: scan, generate). JSON in / JSON out; `success` + `errors` in every result.

Tests: `tests/test_*.py` offline (fake editor on multicast port 16766); `tests/live_scratch.py` against a scratch editor started by `scripts/scratch_editor.py` (backs up and restores AutoLoadProject.txt / EditorSettings.ini).
