# Unreal Engine MCP

An MCP server that lets Claude Code work with a **running** Unreal Editor (UE 5.6) while you make
character mods: inspect and edit materials and material instances, mesh slots and textures, read the
log, take screenshots, and drive the HT mod plugins (toggle trees, the NTE panel, the material
instance builder) — without closing the editor, launching headless commandlets or reading
`.uasset` bytes.

It talks to the editor through the Python plugin's built-in **remote execution**, so the core needs
no editor plugin of its own. Standard-library Python only; nothing to `pip install`.

## Requirements

- Unreal Editor 5.6 with the **Python Editor Script Plugin** enabled in the project.
- Remote execution enabled for the project (Project Settings → Plugins → Python → *Enable Remote
  Execution*). In `Config/DefaultEngine.ini`:

  ```ini
  [/Script/PythonScriptPlugin.PythonScriptPluginSettings]
  bRemoteExecution=True
  RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766
  RemoteExecutionMulticastBindAddress=0.0.0.0
  ```

  The server reads these settings from the project and binds the same way the editor does.
- Python 3.10 or later on the machine (for example `D:\Python312\python.exe`).
- For the plugin tools: HT Blueprint Toggle Tool **1.6.4** and NTE Panel System **0.1.26** or later
  (they add `unreal.HTModScriptLibrary` and `unreal.NTEPanelScriptLibrary`). Everything else works
  without them; with them, instance-editor visibility comes from the engine itself.

## Setup

1. Open the project in the editor as usual.
2. Register the server with Claude Code (user scope, so every session can use it):

   ```bash
   claude mcp add unreal-editor -s user -- "D:\Python312\python.exe" "E:\unreal engine mcp\server\run_server.py" --project "D:\ueproject\HT"
   ```

   Or add it to a project's `.mcp.json`:

   ```json
   {
     "mcpServers": {
       "unreal-editor": {
         "command": "D:\\Python312\\python.exe",
         "args": ["E:\\unreal engine mcp\\server\\run_server.py", "--project", "D:\\ueproject\\HT"]
       }
     }
   }
   ```
3. Start a new Claude Code session (running sessions do not pick up new servers) and ask it to call
   `editor_status`.

Without an MCP host, `python scripts/call.py --project D:\ueproject\HT <tool> '<json args>'` calls
one tool from the command line.

## Tools

| Area | Tools |
|---|---|
| Editor | `editor_status`, `run_python`, `console_command`, `output_log`, `screenshot` |
| Assets | `find_assets`, `asset_info` (class, parent chain, dependencies, referencers, files), `open_asset`, `unsaved_assets`, `save_assets` |
| Materials | `material_parameters` (group, default, override, **visible in the instance editor**), `material_graph`, `material_add_parameter`, `material_connect`, `material_expose_parameters` |
| Instances | `instance_set_parameters` (set / clear overrides, change parent, dry run), `instance_create` |
| Meshes, textures | `mesh_slots`, `mesh_set_slot_materials`, `slots_override`, `slot_match_names`, `texture_check` |
| Toggle trees | `toggle_graph_describe`, `toggle_graph_check`, `toggle_graph_edit`, `toggle_graph_create`, `toggle_graph_refresh_slots`, `toggle_graph_generate` |
| Generators | `toggle_generate` (classic modes), `material_instance_build`, `panel_scan`, `panel_generate`, `script_libraries` |
| Packaging | `cooked_export` (the Cooked Asset Exporter without the editor; dry run by default) |

### Instance-editor visibility

The material instance editor lists only parameters that reach a material output under the
instance's static switch values (`FMaterialEditorUtilities::GetVisibleMaterialParameters`). An
unconnected parameter node exists in the material but cannot be overridden in the instance editor.
`material_parameters` reports `visible_in_instance_editor` for every parameter; with the HT script
library loaded the engine function answers, otherwise a Python port of it does (material layers
are not followed by the port).

## Safety

- **Nothing is saved implicitly.** Only `save_assets` writes packages (and the plugin tools when
  you pass `save: true`). Before writing, the current files are copied to
  `<Project>/Saved/Backups/ue-mcp-<date>/`.
- **Edits are undoable.** Each editing tool call is one Ctrl+Z step (the helper calls `Modify()` on
  everything it touches inside an editor transaction). A failed `toggle_graph_edit` batch is undone
  as a whole.
- A material open in the Material Editor is refused for graph edits: that editor works on a copy
  and would overwrite the change on Apply.
- Commands run unattended, so script-triggered dialogs cannot block the editor. Console commands
  that would quit or crash the editor are refused. A request that waited too long in a busy editor
  is dropped instead of running later.

## How it works

```
Claude Code ──stdio──> server/ue_mcp (outside the editor)
                         │ UDP multicast: find the editor whose project_root matches
                         │ TCP: the editor connects back; one short bootstrap command
                         ▼
                       editor Python: imports server/ue_mcp/editor_side/ue_mcp_editor,
                       reads the request file, writes the reply file
```

- The request and reply travel as JSON files; only a short bootstrap goes over the socket, because
  the editor reads each command with a single 8 KB receive.
- The editor keeps one command connection at a time, so every server process takes a machine-wide
  mutex around each call: several Claude sessions queue instead of cutting each other off.
- The editor-side package is reloaded automatically when its source changes, so tool updates do not
  need an editor restart.
- The log is read straight from the editor's log file, and screenshots are taken with `PrintWindow`,
  so both work while the editor is busy.

## Development

```bash
python -m unittest discover -s tests        # offline: protocol, transport (fake editor), logs
```

Tools that change assets are checked against a throwaway scratch editor, never a real project:

```bash
python scripts/scratch_editor.py create --dir <scratch> [--plugins <folder with built HT plugins>]
python scripts/scratch_editor.py start  --dir <scratch>
python tests/live_scratch.py --project <scratch> [--plugins]
python scripts/scratch_editor.py stop   --dir <scratch>
```

`start` backs up `AutoLoadProject.txt` and `EditorSettings.ini` (every editor launch rewrites them)
and `stop` restores them, so the editor keeps opening your real project.
