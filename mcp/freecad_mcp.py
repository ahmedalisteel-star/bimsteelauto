"""
FreeCAD MCP Server
-------------------
This is the server that Claude Desktop launches. It does NOT talk to FreeCAD
directly — instead it forwards requests over the network to the RPC listener
we already tested (freecad_rpc_server.py, running inside FreeCAD on port 9875).

Chain:  Claude Desktop  <-->  freecad_mcp.py  <-->  FreeCAD (rpc_server macro)

BEFORE USING:
1. Open FreeCAD
2. Run the rpc_server macro (Macro > Macros > rpc_server > Execute)
3. Confirm Report view shows "FreeCAD RPC server listening on port 9875"
4. Then Claude Desktop can use this server
"""

import xmlrpc.client
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

HOST = "127.0.0.1"
PORT = 9875

mcp = FastMCP("freecad_mcp")


def _get_proxy() -> xmlrpc.client.ServerProxy:
    """Create a connection to the FreeCAD RPC listener."""
    return xmlrpc.client.ServerProxy(f"http://{HOST}:{PORT}", allow_none=True)


def _call(method_name: str, *args):
    """
    Call a method on the FreeCAD RPC server with a clear error message
    if FreeCAD isn't reachable (most common failure: macro not running).
    """
    try:
        proxy = _get_proxy()
        method = getattr(proxy, method_name)
        return method(*args)
    except ConnectionRefusedError:
        raise RuntimeError(
            "Could not reach FreeCAD. Make sure FreeCAD is open and the "
            "'rpc_server' macro has been executed (Macro > Macros > rpc_server > Execute). "
            "Check the Report view for 'FreeCAD RPC server listening on port 9875'."
        )
    except Exception as e:
        raise RuntimeError(f"FreeCAD RPC error calling '{method_name}': {e}")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="freecad_ping",
    annotations={
        "title": "Check FreeCAD Connection",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def freecad_ping() -> str:
    """Check whether FreeCAD is reachable and responding.

    Use this first if any other FreeCAD tool fails, to confirm the
    connection is alive before trying more complex operations.

    Returns:
        str: "pong" if FreeCAD is reachable, or an error message explaining
        why the connection failed (e.g. FreeCAD not open, macro not running).
    """
    result = _call("ping")
    return f"FreeCAD responded: {result}"


@mcp.tool(
    name="freecad_get_version",
    annotations={
        "title": "Get FreeCAD Version",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def freecad_get_version() -> str:
    """Get the version of the currently running FreeCAD instance.

    Returns:
        str: The FreeCAD version string, e.g. "1.1.1".
    """
    version = _call("get_version")
    return f"FreeCAD version: {'.'.join(version[:3])} (build {version[4]})"


class ExecutePythonInput(BaseModel):
    """Input for executing Python code inside the live FreeCAD session."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(
        ...,
        description="Python source to execute inside FreeCAD. print() output "
        "is returned. State persists between calls (imports/variables carry "
        "over, like a console session).",
        min_length=1,
    )


@mcp.tool(
    name="freecad_execute_python",
    annotations={
        "title": "Execute Python in FreeCAD",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def freecad_execute_python(params: ExecutePythonInput) -> str:
    """Execute arbitrary Python code inside the live FreeCAD session.

    This is the bridge's power tool: run BIMSteelAuto (or any) code directly
    in FreeCAD — create members/connections, inspect shapes, run clash
    checks — instead of being limited to the canned tools. Requires the
    updated rpc_server macro (with execute_python registered).

    Args:
        params (ExecutePythonInput): The Python source to execute.

    Returns:
        str: Captured print() output, or the error traceback on failure.
    """
    result = _call("execute_python", params.code)
    if result["ok"]:
        output = result["output"].strip()
        return output if output else "(executed, no output)"
    return f"ERROR:\n{result['error']}\nOutput before error:\n{result['output']}"


class NewDocumentInput(BaseModel):
    """Input for creating a new FreeCAD document."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(
        ...,
        description="Name for the new document, e.g. 'Portal_Frame_Oman_B24'. "
        "Avoid spaces; use underscores instead.",
        min_length=1,
        max_length=100,
    )


@mcp.tool(
    name="freecad_new_document",
    annotations={
        "title": "Create New FreeCAD Document",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def freecad_new_document(params: NewDocumentInput) -> str:
    """Create a new, empty FreeCAD document. This is usually the first step
    before adding any geometry (columns, beams, etc.).

    Args:
        params (NewDocumentInput): Contains the document name to create.

    Returns:
        str: Confirmation with the created document's actual name.
    """
    doc_name = _call("new_document", params.name)
    return f"Created new FreeCAD document: '{doc_name}'"


@mcp.tool(
    name="freecad_list_documents",
    annotations={
        "title": "List Open FreeCAD Documents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def freecad_list_documents() -> str:
    """List all documents currently open in FreeCAD.

    Returns:
        str: Comma-separated list of open document names, or a message
        if none are open.
    """
    docs = _call("list_documents")
    if not docs:
        return "No documents are currently open in FreeCAD."
    return f"Open documents: {', '.join(docs)}"


class CreateBoxInput(BaseModel):
    """Input for creating a rectangular box (used as a placeholder for a
    column, beam, or rafter with a rectangular cross-section)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_name: str = Field(
        ..., description="Name of the FreeCAD document to add the box to. "
        "Must already exist (use freecad_new_document first).",
        min_length=1,
    )
    object_name: str = Field(
        ..., description="Name for this object, e.g. 'Column_C1', 'Rafter_R1'.",
        min_length=1,
    )
    length_mm: float = Field(
        ..., description="Length (X dimension) in millimeters.", gt=0
    )
    width_mm: float = Field(
        ..., description="Width (Y dimension) in millimeters.", gt=0
    )
    height_mm: float = Field(
        ..., description="Height (Z dimension) in millimeters.", gt=0
    )
    x_mm: float = Field(
        default=0, description="X position of the box's origin corner, in millimeters."
    )
    y_mm: float = Field(
        default=0, description="Y position of the box's origin corner, in millimeters."
    )
    z_mm: float = Field(
        default=0, description="Z position of the box's origin corner, in millimeters."
    )


@mcp.tool(
    name="freecad_create_box",
    annotations={
        "title": "Create Box Geometry in FreeCAD",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def freecad_create_box(params: CreateBoxInput) -> str:
    """Create a rectangular box object in an open FreeCAD document.

    This is a simple placeholder shape — useful for laying out column and
    beam positions quickly (rough massing) before applying real steel
    section profiles. All dimensions are in millimeters, matching your
    standard structural steel drawing units.

    Args:
        params (CreateBoxInput): Document name, object name, dimensions
            (length/width/height), and placement position (x/y/z).

    Returns:
        str: Confirmation with the created object's name and dimensions.
    """
    obj_name = _call(
        "create_box",
        params.document_name,
        params.object_name,
        params.length_mm,
        params.width_mm,
        params.height_mm,
        params.x_mm,
        params.y_mm,
        params.z_mm,
    )
    return (
        f"Created box '{obj_name}' in document '{params.document_name}': "
        f"{params.length_mm}x{params.width_mm}x{params.height_mm}mm "
        f"at position ({params.x_mm}, {params.y_mm}, {params.z_mm})"
    )


class SaveDocumentInput(BaseModel):
    """Input for saving a FreeCAD document to disk."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_name: str = Field(..., description="Name of the open document to save.", min_length=1)
    filepath: str = Field(
        ...,
        description="Full Windows file path to save to, e.g. "
        "'C:\\\\work\\\\Portal_Frame.FCStd'. "
        "Must end in .FCStd",
        min_length=1,
    )


@mcp.tool(
    name="freecad_save_document",
    annotations={
        "title": "Save FreeCAD Document",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def freecad_save_document(params: SaveDocumentInput) -> str:
    """Save an open FreeCAD document to a .FCStd file on disk.

    Args:
        params (SaveDocumentInput): Document name and full destination filepath.

    Returns:
        str: Confirmation with the saved file path.
    """
    path = _call("save_document", params.document_name, params.filepath)
    return f"Saved document '{params.document_name}' to: {path}"


class CreatePortalFrameGridInput(BaseModel):
    """Input for generating a full duo-pitch portal frame building grid."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_name: str = Field(
        ..., description="Name of the open document to add the frame to.", min_length=1
    )
    span_mm: float = Field(..., description="Clear span (eave to eave) in millimeters, e.g. 18000.", gt=0)
    eave_height_mm: float = Field(..., description="Eave (column top) height in millimeters, e.g. 6000.", gt=0)
    num_frames: int = Field(..., description="Number of transverse frames, e.g. 7.", ge=2)
    bay_spacing_mm: float = Field(..., description="Spacing between frames in millimeters, e.g. 6000.", gt=0)
    roof_slope_deg: float = Field(
        default=10.0, description="Roof slope in degrees from horizontal (typical PEB: 5-10 deg).", ge=0, le=45
    )
    column_width_mm: float = Field(
        default=300, description="Placeholder column section width (X direction) in mm. ASSUMED - update once EC3 confirms.", gt=0
    )
    column_depth_mm: float = Field(
        default=150, description="Placeholder column section depth (Y direction) in mm. ASSUMED - update once EC3 confirms.", gt=0
    )
    rafter_width_mm: float = Field(
        default=150, description="Placeholder rafter section width in mm. ASSUMED - update once EC3 confirms.", gt=0
    )
    rafter_depth_mm: float = Field(
        default=350, description="Placeholder rafter section depth in mm. ASSUMED - update once EC3 confirms.", gt=0
    )


@mcp.tool(
    name="freecad_create_portal_frame_grid",
    annotations={
        "title": "Create Full Portal Frame Grid",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def freecad_create_portal_frame_grid(params: CreatePortalFrameGridInput) -> str:
    """Build a complete duo-pitch (symmetric gable) portal frame building —
    all transverse frames (columns + sloped rafters) in one call.

    Uses simple rectangular box placeholder sections for columns and rafters
    (real EC3-checked sections can replace these later). Frames are spaced
    evenly along the building length. This is the fastest way to lay out
    the full steel skeleton before adding purlins, bracing, and shop drawings.

    Args:
        params (CreatePortalFrameGridInput): Span, eave height, frame count,
            bay spacing, roof slope, and placeholder section sizes.

    Returns:
        str: Summary of created objects and computed geometry (apex height,
        rafter length, overall building length).
    """
    result = _call(
        "create_portal_frame_grid",
        params.document_name,
        params.span_mm,
        params.eave_height_mm,
        params.num_frames,
        params.bay_spacing_mm,
        params.roof_slope_deg,
        params.column_width_mm,
        params.column_depth_mm,
        params.rafter_width_mm,
        params.rafter_depth_mm,
    )
    building_length_m = result["building_length_mm"] / 1000
    apex_height_m = result["apex_height_mm"] / 1000
    return (
        f"Created {len(result['created_objects'])} objects across {params.num_frames} frames.\n"
        f"Building length: {building_length_m:.2f}m | Apex height: {apex_height_m:.3f}m | "
        f"Rafter length (each slope): {result['rafter_length_mm']:.0f}mm\n"
        f"Sections used are ASSUMED placeholders — update once EC3 checks are confirmed."
    )


if __name__ == "__main__":
    mcp.run()
