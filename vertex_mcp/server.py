#!/usr/bin/env python3
"""
MCP Server for Spirent Vertex Channel Emulator.

Connects over Telnet to the Vertex RPI (Remote Procedure Interface).
Configure via env vars:
  VERTEX_HOST    - instrument IP address (required)
  VERTEX_PORT    - telnet port (default: 23)
  VERTEX_TIMEOUT - command timeout in seconds (default: 30)

Topology support:
  SigGen --> Vertex --> SpecAn  (A-side = SigGen, B-side = SpecAn)
"""

import asyncio
import json
import os
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("vertex_mcp")

VERTEX_HOST = os.environ.get("VERTEX_HOST", "")
VERTEX_PORT = int(os.environ.get("VERTEX_PORT", "23"))
VERTEX_TIMEOUT = float(os.environ.get("VERTEX_TIMEOUT", "30"))

VERTEX_PROMPT = b"Vertex>"


# ---------------------------------------------------------------------------
# Telnet transport (RPI uses a prompt-based telnet interface)
# ---------------------------------------------------------------------------

async def _connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Open a telnet session and wait for the Vertex> prompt."""
    if not VERTEX_HOST:
        return None
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(VERTEX_HOST, VERTEX_PORT),
        timeout=VERTEX_TIMEOUT,
    )
    # Consume banner/prompt
    try:
        await asyncio.wait_for(reader.readuntil(VERTEX_PROMPT), timeout=VERTEX_TIMEOUT)
    except asyncio.TimeoutError:
        pass
    return reader, writer


async def _send_rpi(command: str) -> str:
    """Send one RPI command to the Vertex and return the response line."""
    if not VERTEX_HOST:
        return "Error: VERTEX_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(VERTEX_HOST, VERTEX_PORT),
            timeout=VERTEX_TIMEOUT,
        )
        # Consume prompt
        try:
            await asyncio.wait_for(reader.readuntil(VERTEX_PROMPT), timeout=5)
        except asyncio.TimeoutError:
            pass

        writer.write((command + "\r\n").encode())
        await writer.drain()

        # Read until next prompt
        data = await asyncio.wait_for(reader.readuntil(VERTEX_PROMPT), timeout=VERTEX_TIMEOUT)
        writer.close()
        await writer.wait_closed()

        # Strip the echoed command and trailing prompt, return clean response
        lines = data.decode(errors="replace").splitlines()
        response_lines = [l.strip() for l in lines if l.strip() and l.strip() != command.strip() and "Vertex>" not in l]
        return "\n".join(response_lines) if response_lines else "OK"

    except asyncio.TimeoutError:
        return "Error: Connection timed out. Check VERTEX_HOST and VERTEX_PORT."
    except OSError as e:
        return f"Error: Could not connect to {VERTEX_HOST}:{VERTEX_PORT} — {e}"


async def _send_rpi_batch(commands: list[str]) -> list[str]:
    """Send multiple RPI commands in one telnet session, collect responses."""
    if not VERTEX_HOST:
        return [f"Error: VERTEX_HOST environment variable not set."]
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(VERTEX_HOST, VERTEX_PORT),
            timeout=VERTEX_TIMEOUT,
        )
        try:
            await asyncio.wait_for(reader.readuntil(VERTEX_PROMPT), timeout=5)
        except asyncio.TimeoutError:
            pass

        responses = []
        for cmd in commands:
            writer.write((cmd + "\r\n").encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.readuntil(VERTEX_PROMPT), timeout=VERTEX_TIMEOUT)
            lines = data.decode(errors="replace").splitlines()
            resp = [l.strip() for l in lines if l.strip() and l.strip() != cmd.strip() and "Vertex>" not in l]
            responses.append("\n".join(resp) if resp else "OK")

        writer.close()
        await writer.wait_closed()
        return responses
    except asyncio.TimeoutError:
        return ["Error: Timed out."]
    except OSError as e:
        return [f"Error: {e}"]


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class ConnectionSetupInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    connection_name: str = Field(..., description="Connection setup name from the Vertex library (e.g. 'SISO_1X1', 'MIMO_2X2', 'DUAL_2X2_UNI'). Use vertex_list_connections to see available options.")


class PortFrequencyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    side: str = Field(..., description="Port side: 'A' (input from SigGen) or 'B' (output to SpecAn).")
    port: int = Field(default=1, description="Port number (1 to N, depending on connection setup).", ge=1)
    frequency_mhz: float = Field(..., description="Frequency in MHz (30 to 5925 MHz).", ge=30, le=5925)


class PortPowerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    side: str = Field(..., description="Port side: 'A' or 'B'.")
    port: int = Field(default=1, ge=1)
    power_dbm: float = Field(..., description="Expected input power in dBm (A side) or output power in dBm (B side).")
    is_input: bool = Field(default=True, description="True to set input power (A side), False to set output power (B side).")


class ChannelLossInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    side: str = Field(..., description="Port side: 'A' or 'B'.")
    port: int = Field(default=1, ge=1)
    loss_db: float = Field(..., description="Channel path loss in dB (0 to 130 dB).", ge=0, le=130)


class PropagationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    channel_model: int = Field(default=1, description="Channel model index (1-based). Use 1 for single SISO.", ge=1)
    condition_name: str = Field(..., description="Propagation condition from the Vertex library (e.g. 'AWGN', 'EPA5', 'EVA70', 'ETU300', 'CUSTOM'). Use vertex_list_propagation_conditions to see available options.")


class AWGNInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    side: str = Field(default="B", description="Port side: 'A' or 'B'. Typically 'B' (output port).")
    port: int = Field(default=1, ge=1)
    cn_db: float = Field(..., description="C/N ratio in dB (-30 to 32 dB).", ge=-30, le=32)


class DemoSetupInput(BaseModel):
    """High-level demo topology configurator."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    connection_name: str = Field(default="SISO_1X1", description="Connection setup (e.g. 'SISO_1X1'). Use vertex_list_connections to browse.")
    frequency_mhz: float = Field(..., description="Channel center frequency in MHz (30 to 5925).", ge=30, le=5925)
    input_power_dbm: float = Field(default=-20.0, description="Expected input power at A-side port in dBm.")
    channel_loss_db: float = Field(default=30.0, description="Path loss applied by the Vertex in dB.", ge=0, le=130)
    propagation_condition: str = Field(default="AWGN", description="Propagation condition (e.g. 'AWGN', 'EPA5', 'EVA70'). Use vertex_list_propagation_conditions.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vertex_init",
    annotations={"title": "Initialize Vertex", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_init() -> str:
    """Initialize the Spirent Vertex channel emulator.

    Queries system error queue to verify connectivity and returns instrument status.

    Returns:
        str: JSON with connection status and any system errors.
    """
    responses = await _send_rpi_batch(["ERR?", "EMUL:STAT?"])
    if responses and responses[0].startswith("Error:"):
        return responses[0]

    error_resp = responses[0] if len(responses) > 0 else "unknown"
    state_resp = responses[1] if len(responses) > 1 else "unknown"

    return json.dumps({
        "connected": True,
        "host": VERTEX_HOST,
        "system_error": error_resp,
        "emulation_state": state_resp,
    }, indent=2)


@mcp.tool(
    name="vertex_list_connections",
    annotations={"title": "List Available Connection Setups", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_list_connections() -> str:
    """Query all available connection setup configurations from the Vertex library.

    Returns:
        str: Comma-separated list of connection setup names available on this Vertex unit.
    """
    result = await _send_rpi("CON:LIBAV?")
    if result.startswith("Error:"):
        return result
    return json.dumps({"available_connections": result}, indent=2)


@mcp.tool(
    name="vertex_set_connection",
    annotations={"title": "Apply Connection Setup", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def vertex_set_connection(params: ConnectionSetupInput) -> str:
    """Apply a connection setup from the Vertex library.

    This defines the physical topology: how many ports, which directions, SISO vs MIMO.
    For SigGen → Vertex → SpecAn demos, use 'SISO_1X1' or similar.

    Args:
        params (ConnectionSetupInput):
            - connection_name (str): Name of the connection setup (e.g. 'SISO_1X1').

    Returns:
        str: Confirmation or error message.
    """
    responses = await _send_rpi_batch([
        f"CON:LIB {params.connection_name}",
        "ERR?",
    ])
    if responses[0].startswith("Error:"):
        return responses[0]
    err = responses[1] if len(responses) > 1 else "unknown"
    if err.startswith("0"):
        return f"OK — Connection setup '{params.connection_name}' applied successfully."
    return f"Warning: Connection '{params.connection_name}' applied. Error queue: {err}"


@mcp.tool(
    name="vertex_set_port_frequency",
    annotations={"title": "Set Port Frequency", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_set_port_frequency(params: PortFrequencyInput) -> str:
    """Set the RF frequency for a specific port on the Vertex.

    For B2B demos, set A1 and B1 to match the SigGen/SpecAn frequency.

    Args:
        params (PortFrequencyInput):
            - side (str): 'A' or 'B'.
            - port (int): Port number (default: 1).
            - frequency_mhz (float): Frequency in MHz (30 to 5925).

    Returns:
        str: Confirmation or error message.
    """
    cmd = f"PORT:{params.side.upper()}{params.port}:FREQ {params.frequency_mhz:.3f}MHZ"
    responses = await _send_rpi_batch([cmd, "ERR?"])
    if responses[0].startswith("Error:"):
        return responses[0]
    return json.dumps({
        "side": params.side.upper(),
        "port": params.port,
        "frequency_mhz": params.frequency_mhz,
        "frequency_ghz": params.frequency_mhz / 1e3,
        "status": "applied",
    }, indent=2)


@mcp.tool(
    name="vertex_set_port_power",
    annotations={"title": "Set Port Input/Output Power", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_set_port_power(params: PortPowerInput) -> str:
    """Set expected input power or output power level for a Vertex port.

    For B2B demos: set A-side input power to match SigGen output power.
    The Vertex uses this to calibrate its internal gain.

    Args:
        params (PortPowerInput):
            - side (str): 'A' or 'B'.
            - port (int): Port number (default: 1).
            - power_dbm (float): Power level in dBm.
            - is_input (bool): True = set expected input power, False = set output power.

    Returns:
        str: Confirmation or error message.
    """
    ptype = "INPWR" if params.is_input else "OUTPWR"
    cmd = f"PORT:{params.side.upper()}{params.port}:{ptype} {params.power_dbm:.2f}DBM"
    responses = await _send_rpi_batch([cmd, "ERR?"])
    if responses[0].startswith("Error:"):
        return responses[0]
    return json.dumps({
        "side": params.side.upper(),
        "port": params.port,
        "type": "input" if params.is_input else "output",
        "power_dbm": params.power_dbm,
        "status": "applied",
    }, indent=2)


@mcp.tool(
    name="vertex_set_channel_loss",
    annotations={"title": "Set Channel Path Loss", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_set_channel_loss(params: ChannelLossInput) -> str:
    """Set the channel path loss (attenuation) for a port.

    Controls how much signal attenuation the Vertex applies, simulating
    free-space path loss or cable loss between SigGen and SpecAn.

    Args:
        params (ChannelLossInput):
            - side (str): 'A' or 'B'.
            - port (int): Port number (default: 1).
            - loss_db (float): Path loss in dB (0 to 130 dB).

    Returns:
        str: Confirmation or error message.
    """
    cmd = f"PORT:{params.side.upper()}{params.port}:LOSS {params.loss_db:.2f}DB"
    responses = await _send_rpi_batch([cmd, "ERR?"])
    if responses[0].startswith("Error:"):
        return responses[0]
    return json.dumps({
        "side": params.side.upper(),
        "port": params.port,
        "loss_db": params.loss_db,
        "status": "applied",
    }, indent=2)


@mcp.tool(
    name="vertex_list_propagation_conditions",
    annotations={"title": "List Propagation Conditions", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_list_propagation_conditions(channel_model: int = 1) -> str:
    """List propagation conditions available in the Vertex library for a channel model.

    Args:
        channel_model (int): Channel model index (default: 1).

    Returns:
        str: Available propagation condition names.
    """
    result = await _send_rpi(f"CHM{channel_model}:PROP:LIBAV?")
    if result.startswith("Error:"):
        return result
    return json.dumps({"channel_model": channel_model, "available_conditions": result}, indent=2)


@mcp.tool(
    name="vertex_set_propagation",
    annotations={"title": "Apply Propagation Condition", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_set_propagation(params: PropagationInput) -> str:
    """Apply a propagation condition (fading profile) to a channel model.

    Propagation conditions define the multipath fading environment:
    - AWGN: Additive White Gaussian Noise only (no fading)
    - EPA5: Extended Pedestrian A, 5 Hz Doppler (low mobility)
    - EVA70: Extended Vehicular A, 70 Hz Doppler (medium mobility)
    - ETU300: Extended Typical Urban, 300 Hz Doppler (high mobility)

    Args:
        params (PropagationInput):
            - channel_model (int): Channel model index (default: 1).
            - condition_name (str): Propagation condition name.

    Returns:
        str: Confirmation or error message.
    """
    responses = await _send_rpi_batch([
        f"CHM{params.channel_model}:PROP:LIB {params.condition_name}",
        "ERR?",
    ])
    if responses[0].startswith("Error:"):
        return responses[0]
    return json.dumps({
        "channel_model": params.channel_model,
        "propagation_condition": params.condition_name,
        "status": "applied",
    }, indent=2)


@mcp.tool(
    name="vertex_set_awgn",
    annotations={"title": "Set AWGN C/N Ratio", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_set_awgn(params: AWGNInput) -> str:
    """Enable AWGN interference and set the C/N (carrier-to-noise) ratio on a port.

    Useful for SNR sensitivity demos without fading. Requires AWGN mode to be
    set in propagation conditions first.

    Args:
        params (AWGNInput):
            - side (str): Port side ('A' or 'B', default: 'B').
            - port (int): Port number (default: 1).
            - cn_db (float): C/N ratio in dB (-30 to 32 dB).

    Returns:
        str: Confirmation or error message.
    """
    responses = await _send_rpi_batch([
        f"PORT:{params.side.upper()}{params.port}:INT:MODE ON",
        f"PORT:{params.side.upper()}{params.port}:INT:UNIT CTON",
        f"PORT:{params.side.upper()}{params.port}:INT:CN {params.cn_db:.2f}DB",
        "ERR?",
    ])
    if responses[0].startswith("Error:"):
        return responses[0]
    return json.dumps({
        "side": params.side.upper(),
        "port": params.port,
        "awgn_enabled": True,
        "cn_db": params.cn_db,
        "status": "applied",
    }, indent=2)


@mcp.tool(
    name="vertex_start_emulation",
    annotations={"title": "Start Channel Emulation", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def vertex_start_emulation() -> str:
    """Start channel emulation on the Vertex.

    Begins applying the configured propagation conditions, path loss, and AWGN
    to the signals passing through the instrument.

    Returns:
        str: Emulation state confirmation or error.
    """
    responses = await _send_rpi_batch(["EMUL:PLAY", "EMUL:STAT?"])
    if responses[0].startswith("Error:"):
        return responses[0]
    state = responses[1] if len(responses) > 1 else "unknown"
    return json.dumps({"emulation_state": state, "status": "start_requested"}, indent=2)


@mcp.tool(
    name="vertex_stop_emulation",
    annotations={"title": "Stop Channel Emulation", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_stop_emulation() -> str:
    """Stop channel emulation on the Vertex.

    Returns:
        str: Confirmation or error.
    """
    responses = await _send_rpi_batch(["EMUL:STOP", "EMUL:STAT?"])
    if responses[0].startswith("Error:"):
        return responses[0]
    state = responses[1] if len(responses) > 1 else "unknown"
    return json.dumps({"emulation_state": state, "status": "stopped"}, indent=2)


@mcp.tool(
    name="vertex_get_status",
    annotations={"title": "Get Vertex Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def vertex_get_status() -> str:
    """Query Vertex emulation state, current connection setup, and system errors.

    Returns:
        str: JSON with emulation_state, connection_setup, system_error fields.
    """
    responses = await _send_rpi_batch([
        "EMUL:STAT?",
        "CON:LIB?",
        "ERR?",
    ])
    if responses and responses[0].startswith("Error:"):
        return responses[0]

    return json.dumps({
        "emulation_state": responses[0] if len(responses) > 0 else "unknown",
        "connection_setup": responses[1] if len(responses) > 1 else "unknown",
        "system_error": responses[2] if len(responses) > 2 else "unknown",
        "host": VERTEX_HOST,
    }, indent=2)


@mcp.tool(
    name="vertex_measure_port_power",
    annotations={"title": "Measure Port Input/Output Power", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def vertex_measure_port_power(side: str = "A", port: int = 1) -> str:
    """Query the measured input and output power levels for a Vertex port.

    Useful for verifying that the SigGen signal is arriving at the expected level.

    Args:
        side (str): Port side 'A' or 'B' (default: 'A').
        port (int): Port number (default: 1).

    Returns:
        str: JSON with measured_input_dbm and measured_output_dbm.
    """
    s = side.upper()
    responses = await _send_rpi_batch([
        f"PORT:{s}{port}:MEAS:INLVL?",
        f"PORT:{s}{port}:MEAS:OUTLVL?",
    ])
    if responses and responses[0].startswith("Error:"):
        return responses[0]
    return json.dumps({
        "side": s,
        "port": port,
        "measured_input_dbm": responses[0] if len(responses) > 0 else "n/a",
        "measured_output_dbm": responses[1] if len(responses) > 1 else "n/a",
    }, indent=2)


@mcp.tool(
    name="vertex_setup_b2b_demo",
    annotations={"title": "Setup Full B2B Demo Topology", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def vertex_setup_b2b_demo(params: DemoSetupInput) -> str:
    """Configure the Vertex for a complete SigGen → Vertex → SpecAn B2B demo.

    This high-level tool sets up everything in one call:
    1. Applies connection setup
    2. Sets port frequencies (A1 and B1)
    3. Sets expected input power on A1
    4. Sets channel path loss on A1
    5. Applies propagation condition

    After calling this, start emulation with vertex_start_emulation.

    Args:
        params (DemoSetupInput):
            - connection_name (str): e.g. 'SISO_1X1' (default).
            - frequency_mhz (float): Channel frequency in MHz.
            - input_power_dbm (float): Expected SigGen output power at A1 (default: -20 dBm).
            - channel_loss_db (float): Path loss to apply in dB (default: 30 dB).
            - propagation_condition (str): e.g. 'AWGN', 'EPA5', 'EVA70' (default: 'AWGN').

    Returns:
        str: JSON summary of all applied settings.
    """
    commands = [
        # 1. Connection setup
        f"CON:LIB {params.connection_name}",
        # 2. Port frequencies
        f"PORT:A1:FREQ {params.frequency_mhz:.3f}MHZ",
        f"PORT:B1:FREQ {params.frequency_mhz:.3f}MHZ",
        # 3. Expected input power on A1
        f"PORT:A1:INPWR {params.input_power_dbm:.2f}DBM",
        # 4. Channel loss
        f"PORT:A1:LOSS {params.channel_loss_db:.2f}DB",
        # 5. Propagation condition on channel model 1
        f"CHM1:PROP:LIB {params.propagation_condition}",
        # 6. Check errors
        "ERR?",
    ]
    responses = await _send_rpi_batch(commands)
    if responses and responses[0].startswith("Error:"):
        return responses[0]

    err = responses[-1] if responses else "unknown"
    ok = err.startswith("0")

    return json.dumps({
        "connection_setup": params.connection_name,
        "frequency_mhz": params.frequency_mhz,
        "frequency_ghz": params.frequency_mhz / 1e3,
        "input_power_dbm": params.input_power_dbm,
        "channel_loss_db": params.channel_loss_db,
        "propagation_condition": params.propagation_condition,
        "system_error": err,
        "status": "configured — call vertex_start_emulation to begin",
        "next_step": "vertex_start_emulation",
    }, indent=2)


if __name__ == "__main__":
    mcp.run()
