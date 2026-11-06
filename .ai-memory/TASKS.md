# Tasks

- OPEN: register the server with Claude Code once the user agrees (`claude mcp add unreal-editor -s user -- "D:\Python312\python.exe" "E:\unreal engine mcp\server\run_server.py" --project "D:\ueproject\HT"`), then start a new session to load it.
- OPEN (user): close UE and run `D:\ueproject\HT\Saved\Install-20261106-1.6.4-0.1.26\Install.ps1`, reopen HT; then `editor_status` should show both script libraries and `material_parameters` should report the engine method.
- IDEAS (not requested): connect an end-to-end mod loop (save -> cook -> cooked_export -> packager -> game check); cancelable long calls; a `material_disconnect` tool.
- DONE 2026-11-06: phases 1-3 (see CURRENT_STATE).
