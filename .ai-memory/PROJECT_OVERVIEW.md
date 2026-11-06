# Project Overview

## Name and goal

Unreal Engine MCP (`E:\unreal engine mcp`): a Model Context Protocol server that lets Claude Code work with the user's **running** Unreal Editor while making Neverness to Everness (NTE) character mods, without closing the editor, launching headless commandlets or reading `.uasset` bytes.

## Who it is for

The project author, who builds NTE mods in the UE 5.6 project `D:\ueproject\HT` with the in-house editor plugins HTBlueprintToggleTool and NTEPanelSystem. Claude sessions used to drive UE through `UnrealEditor-Cmd -run=pythonscript`: the user had to close UE for every run, each run took minutes, and live-editor behaviour was invisible (unconnected parameter nodes never show in the material instance editor, which led to a wrong "it's done" on 2026-11-06).

## Environment

- Engine: UE 5.6.1 launcher build at `D:\ue\UE_5.6`.
- Main project: `D:\ueproject\HT\HT.uproject` (PythonScriptPlugin on; remote execution already enabled in `HT\Config\DefaultEngine.ini`: group `239.0.0.1:6766`, bind `0.0.0.0`, TTL 0).
- Plugins served by phase 2/3: `D:\ueproject\HT\Plugins\HTBlueprintToggleTool` (repo loyal37/NTE-mod-tools) and `D:\ueproject\HT\Plugins\NTEPanelSystem` (repo loyal37/NTE-Panel-System). Their own memory lives in `D:\ueproject\HT\.ai-memory`.
- Python for the server: `D:\Python312\python.exe` (same as the user's efmi MCP), standard library only.

## Scope

- Phase 1: server, remote-execution transport, editor-side helper; live tools for status, Python, console, log, screenshots, asset info, material inspection (with instance-editor visibility) and editing, instance overrides, skeletal mesh slots, textures, saving.
- Phase 2: script APIs (Python-callable `UFUNCTION`s) in both plugins and MCP tools for toggle trees, legacy toggles, the four-texture material instance builder and the panel generator.
- Phase 3: slot mapping, cooked asset export, editing toggle graph nodes from JSON.

## Long-term constraints

- Never close or disturb the user's open editor. Mutating tests run only in a minimal project in the session scratchpad.
- Any editor launch rewrites `%LOCALAPPDATA%\UnrealEngine\5.6\Saved\AutoLoadProject.txt` and the recent list in `%LOCALAPPDATA%\UnrealEngine\5.6\Saved\Config\WindowsEditor\EditorSettings.ini`: back both up and restore them so UE keeps opening `D:/ueproject/HT/HT.uproject`.
- Nothing is saved implicitly; every edit is undoable; demos are read-only on the user's assets.
- Code, README, commits in English. Reports to the user in Chinese.
- Ask the user before changing project settings or registering the server with Claude Code.
