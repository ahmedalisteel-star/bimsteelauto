# Claude <-> FreeCAD bridge (MCP)

Lets Claude (Claude Code or Claude Desktop) drive a **live FreeCAD
session**, so a building can be described in plain text and built,
designed and drawn without touching the GUI.

```
Claude  <-- MCP -->  freecad_mcp.py  <-- XML-RPC 127.0.0.1:9875 -->  FreeCAD (rpc_server.FCMacro)
```

## Tools Claude gets

| Tool | What it does |
|---|---|
| `freecad_ping` | Check FreeCAD is reachable |
| `freecad_get_version` | FreeCAD version |
| `freecad_execute_python` | Run Python inside FreeCAD - this is how BIMSteelAuto is driven |
| `freecad_new_document` / `freecad_list_documents` / `freecad_save_document` | Document handling |
| `freecad_create_box` / `freecad_create_portal_frame_grid` | Quick geometry |

## Setup

**1. Install the bridge's packages** (in your normal Python, not FreeCAD's):

```powershell
python -m pip install -r mcp\requirements.txt
```

**2. Start the listener in FreeCAD.** Copy `rpc_server.FCMacro` into your
FreeCAD macro folder (`%APPDATA%\FreeCAD\v1-1\Macro` for FreeCAD 1.1), then
**Macro > Macros > rpc_server > Execute**. The Report view shows
`FreeCAD RPC server listening on port 9875`. Leave FreeCAD open.

**3a. Claude Code** - nothing to do: the repo's `.mcp.json` registers the
server when you open Claude Code in the repo folder. Approve it once when
asked. Or add it by hand:

```powershell
claude mcp add freecad -- python mcp/freecad_mcp.py
```

**3b. Claude Desktop** - add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "freecad": {
      "command": "python",
      "args": ["C:\\path\\to\\bimsteelauto\\mcp\\freecad_mcp.py"]
    }
  }
}
```

Restart Claude Desktop.

## Security

The listener binds to **127.0.0.1 only** - nothing outside your PC can
reach it. But `freecad_execute_python` runs any Python it is given, with
your user's rights. Only start the macro when you are using it, and only
connect it to an assistant you trust.
