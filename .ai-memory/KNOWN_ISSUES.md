# Known Issues

- The running HT editor was started without a project argument (AutoLoadProject), so its log is `%LOCALAPPDATA%\UnrealEngine\5.6\Saved\Logs\HT.log`, not `D:\ueproject\HT\Saved\Logs\HT.log`. The server asks the editor for its log path.
- A long tool call blocks the editor's game thread, and a call cannot be cancelled once the editor runs it.
- Python cannot read `UMaterialInstance::StaticParametersRuntime`: without the HT script library, static switch override state is inferred by comparing with the parent.
