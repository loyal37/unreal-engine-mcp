# Known Issues

- The running HT editor was started without a project argument (AutoLoadProject), so its log is `%LOCALAPPDATA%\UnrealEngine\5.6\Saved\Logs\HT.log`, not `D:\ueproject\HT\Saved\Logs\HT.log`. The server finds the file through a marker line.
- A long tool call blocks the editor's game thread, and a call cannot be cancelled once the editor runs it. A call that waited past its deadline in the editor's queue is dropped.
- Without the HT script library (1.6.4+): visibility comes from the Python port (material layers not followed; a node feeding two inputs of one node with different outputs is reported with the first output name); static switch override state is inferred by comparing with the parent and cannot be cleared; material expressions are found by iterating loaded objects (deleted nodes kept by the undo history can show up; a note says so); "open in its editor" is detected only for Materials through the Material Editor's preview copy (misses a copy the user already changed).
- `PreviewSkeletalMesh` is read from asset registry tags (Python cannot read the property).
- `material_instance_build` clears the parent material's graph (plugin behaviour); the tool says so.
- `cooked_export` does not cook; it copies what the last cook produced and flags cooked files older than the saved asset.
