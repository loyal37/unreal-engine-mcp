# Decisions

## 2026-11-06 - Transport: Python remote execution, not Web Remote Control or an in-editor server

- Remote execution is already enabled in HT, needs no plugin build, and gives full `unreal` Python. Verified against the running editor: discovery answered in 0.4 s with `project_root D:/ueproject/HT/`.
- Web Remote Control would need another plugin enabled in HT (a project change) and exposes only UFUNCTION/UPROPERTY calls. An in-editor HTTP server (UEBridgeMCP, Epic's 5.8 MCP style) needs a C++ rebuild and editor restart for every tool change and cannot answer while the editor is down or busy.
- C++ is used only where Python cannot reach: the HT plugin generators (non-exported plain C++), the engine's exact instance-editor visibility (`FMaterialEditorUtilities::GetVisibleMaterialParameters`) and static switch override clearing.

## 2026-11-06 - Own remote-execution client instead of the engine's remote_execution.py

- Bind the multicast socket to the project's `RemoteExecutionMulticastBindAddress` (`0.0.0.0` in HT). The engine client's default `127.0.0.1` finds no node on this machine.
- Select the node by `project_root`, never the first node (a scratch editor can run at the same time).
- The editor keeps exactly one command connection (`FPythonScriptRemoteExecution::CommandConnection`); a new `open_connection` replaces it. Calls hold a machine-wide named mutex and open/close the channel per call, so several Claude sessions queue instead of breaking each other. Each process listens on an ephemeral port (the editor connects to the `command_port` we send), so 6776 is never contended.
- The engine client stops reading as soon as one `recv` returns less than 8 KB, which truncates large replies. Ours reads until the JSON message is complete.
- Commands run with `unattended=true` so script-triggered dialogs cannot block the editor.

## 2026-11-06 - Instance-editor visibility

- The instance editor lists only parameters reachable from the material outputs, following the instance's static switch values, function calls, layers and reroutes (`MaterialEditorUtilities.cpp` `GetVisibleMaterialParameters`).
- Exact answer from the HT script library when it is loaded; otherwise a Python port of the same walk (reroutes, static switches, function calls and inputs; material layers reported as unsupported). The result says which method was used.

## 2026-11-06 - Editing rules

- MaterialEditingLibrary does not call `Modify()`; the helper calls `modify()` on every touched object inside `unreal.ScopedEditorTransaction`, so Ctrl+Z undoes MCP edits.
- No implicit saves. Packages are written only by `save_assets`. Plugin generators get `bSaveAssets=false` unless the caller asks.
- A material open in the Material Editor is edited there on a copy; graph edits refuse such materials unless the caller passes `allow_open_editor`.
- Before a package is saved, its files are copied to `Saved/Backups/ue-mcp-<date>/` (the first copy of the day is kept). Plugin generators with `save: true` back up the Blueprints they write first.

## 2026-11-06 - Where each mod feature lives

- Plugin features that are C++ (toggle trees, classic generators, material instance builder, panel) are reached through script libraries added to the plugins themselves (1.6.4 / 0.1.26), JSON in and out, with no logic of their own. The generator classes are not exported, so a separate plugin could not link to them.
- The Slot Materials "Match Names" rule and the Cooked Asset Exporter's copy rules were ported to Python (helper / server) instead: they are small, live in Slate code, and the export must work without the editor. If the plugin's rules change, port the change (`SHTMaterialSlotMapper.cpp` OnAutoMatchClicked, `SHTCookedAssetExporter.cpp` ScanSourceDirectory/OnExportClicked).
- The exporter's saved selection key is `SelectedAssets_%08X` of UE's `GetTypeHash(FString)` (`FCrc::Strihash_DEPRECATED` with the MSB-first 0x04C11DB7 table) of the lower-case source folder; verified against HT's ini.
- `toggle_graph_edit` runs a batch in one transaction and undoes it with `GEditor->UndoTransaction()` when an operation fails (not when the caller already has a transaction open).
- `cooked_export` defaults to dry run; `package` launches the packager the plugin launches (`D:\NTE Mod Packager\NTE Mod Packager.exe`) unless another exe is given.
