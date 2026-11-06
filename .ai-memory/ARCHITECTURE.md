# Architecture

Three layers. Only layer 3 needs C++, and every tool that does not need it works without it.

1. **MCP server** (`server/ue_mcp`, out of process, stdio JSON-RPC, standard library only).
   - `mcp_stdio.py`: protocol loop (initialize, tools/list, tools/call, ping).
   - `remote_exec.py`: client for the PythonScriptPlugin remote execution protocol (UDP multicast discovery, then a TCP command channel the editor connects back to).
   - `editor.py`: picks the editor whose `project_root` matches, serialises calls across processes, injects the editor-side helper, runs helper calls and returns JSON.
   - `logs.py`, `screenshot.py`, `backups.py`: work from outside the editor, so they still answer when the editor is busy.
   - `tools.py`: tool definitions.
2. **Editor-side helper** (`server/ue_mcp/editor_side/ue_mcp_editor.py`): sent into the editor's Python on connect, versioned by content hash and re-sent when it changes. All `unreal` API work lives here.
3. **Plugin script APIs** (phase 2/3): `UBlueprintFunctionLibrary` classes inside HTBlueprintToggleTool and NTEPanelSystem, JSON in / JSON out, wrapping the existing static generators. The helper uses them when `unreal` exposes them and reports when they are missing.

Data flow of one tool call: Claude -> stdio -> tools.py -> editor.py (lock, open TCP channel, `ExecuteFile` of a short call into the helper) -> editor game thread -> JSON result -> MCP content.
