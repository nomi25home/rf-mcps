#!/usr/bin/env python3
"""
MCP Server for Agilent Signal Generator (E4438C ESG and compatible).

Connects over Telnet/SCPI. Configure via env vars:
  SIGGEN_HOST  - instrument IP address (required)
  SIGGEN_PORT  - telnet port (default: 5024)
  SIGGEN_TIMEOUT - command timeout in seconds (default: 10)
"""

import asyncio
import json
import os
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("siggen_mcp")

SIGGEN_HOST = os.environ.get("SIGGEN_HOST", "")
SIGGEN_PORT = int(os.environ.get("SIGGEN_PORT", "5024"))
SIGGEN_TIMEOUT = float(os.environ.get("SIGGEN_TIMEOUT", "10"))


# ---------------------------------------------------------------------------
# Telnet transport
# ---------------------------------------------------------------------------

async def _send_scpi(command: str) -> str:
    """Open a connection, send a SCPI command, return stripped response."""
    if not SIGGEN_HOST:
        return "Error: SIGGEN_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SIGGEN_HOST, SIGGEN_PORT),
            timeout=SIGGEN_TIMEOUT,
        )
        writer.write((command + "\n").encode())
        await writer.drain()
        if "?" in command:
            data = await asyncio.wait_for(reader.readline(), timeout=SIGGEN_TIMEOUT)
            response = data.decode().strip()
        else:
            response = ""
        writer.close()
        await writer.wait_closed()
        return response
    except asyncio.TimeoutError:
        return "Error: Connection timed out. Check SIGGEN_HOST and SIGGEN_PORT."
    except OSError as e:
        return f"Error: Could not connect to {SIGGEN_HOST}:{SIGGEN_PORT} — {e}"


async def _send_multi(commands: list[str]) -> str:
    """Send multiple SCPI commands in a single TCP session."""
    if not SIGGEN_HOST:
        return "Error: SIGGEN_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SIGGEN_HOST, SIGGEN_PORT),
            timeout=SIGGEN_TIMEOUT,
        )
        responses = []
        for cmd in commands:
            writer.write((cmd + "\n").encode())
            await writer.drain()
            if "?" in cmd:
                data = await asyncio.wait_for(reader.readline(), timeout=SIGGEN_TIMEOUT)
                responses.append(data.decode().strip())
        writer.close()
        await writer.wait_closed()
        return "\n".join(responses) if responses else "OK"
    except asyncio.TimeoutError:
        return "Error: Connection timed out."
    except OSError as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class SetCWInput(BaseModel):
    """Input model for configuring CW output in one shot."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    frequency_hz: float = Field(..., description="Output frequency in Hz (e.g. 2.4e9 for 2.4 GHz). Range: 100e3 to 6e9 depending on option.")
    power_dbm: float = Field(..., description="Output power in dBm (e.g. -10.0). Typical range: -136 to +25 dBm.")
    rf_on: bool = Field(default=True, description="True to enable RF output, False to disable.")


class SetFrequencyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    frequency_hz: float = Field(..., description="Output frequency in Hz (e.g. 1e9 = 1 GHz). Range depends on installed options.")


class SetPowerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    power_dbm: float = Field(..., description="RF output power in dBm.")


class SetRFOutputInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    enabled: bool = Field(..., description="True to turn RF output ON, False to turn it OFF.")


class SweepInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    start_hz: float = Field(..., description="Sweep start frequency in Hz.")
    stop_hz: float = Field(..., description="Sweep stop frequency in Hz.")
    power_dbm: float = Field(..., description="Output power in dBm during sweep.")
    dwell_s: float = Field(default=0.01, description="Dwell time per step in seconds (0.001 to 60).", ge=0.001, le=60)
    num_points: int = Field(default=101, description="Number of sweep points (2 to 65535).", ge=2, le=65535)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="siggen_init",
    annotations={"title": "Initialize Signal Generator", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def siggen_init() -> str:
    """Initialize the Agilent Signal Generator and apply a mode preset.

    Sends *RST to reset the instrument and :SYST:PRES to preset all settings
    to factory defaults. This should be called at the start of a test session.

    Returns:
        str: "OK" on success or an error message.
    """
    result = await _send_multi(["*RST", ":SYST:PRES"])
    if result.startswith("Error"):
        return result
    return "OK — Signal Generator initialized and preset applied."


@mcp.tool(
    name="siggen_set_cw",
    annotations={"title": "Set CW Output (Frequency + Power + RF)", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def siggen_set_cw(params: SetCWInput) -> str:
    """Configure CW output: set frequency, power, and RF enable state in one operation.

    This is the primary setup tool for B2B demos. It sets all three parameters
    in a single instrument session for efficiency.

    Args:
        params (SetCWInput):
            - frequency_hz (float): Output frequency in Hz (e.g. 2.4e9).
            - power_dbm (float): Output power in dBm (e.g. -10.0).
            - rf_on (bool): Enable or disable RF output (default: True).

    Returns:
        str: JSON summary of applied settings or an error message.

    Examples:
        - Demo setup at 2.4 GHz, -20 dBm: frequency_hz=2.4e9, power_dbm=-20.0
        - Turn off RF without changing freq/power: frequency_hz=<any>, power_dbm=<any>, rf_on=False
    """
    rf_state = "1" if params.rf_on else "0"
    commands = [
        ":FREQ:MODE CW",
        f":FREQ:CW {params.frequency_hz:.6f}HZ",
        f":POW:LEV:IMM:AMPL {params.power_dbm:.2f}DBM",
        f":OUTP:STAT {rf_state}",
    ]
    result = await _send_multi(commands)
    if result.startswith("Error"):
        return result
    summary = {
        "frequency_hz": params.frequency_hz,
        "frequency_ghz": params.frequency_hz / 1e9,
        "power_dbm": params.power_dbm,
        "rf_output": "ON" if params.rf_on else "OFF",
        "status": "applied",
    }
    return json.dumps(summary, indent=2)


@mcp.tool(
    name="siggen_set_frequency",
    annotations={"title": "Set Output Frequency", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def siggen_set_frequency(params: SetFrequencyInput) -> str:
    """Set the signal generator CW output frequency.

    Args:
        params (SetFrequencyInput):
            - frequency_hz (float): Output frequency in Hz.

    Returns:
        str: Confirmation or error message.
    """
    result = await _send_multi([
        ":FREQ:MODE CW",
        f":FREQ:CW {params.frequency_hz:.6f}HZ",
    ])
    if result.startswith("Error"):
        return result
    return f"OK — Frequency set to {params.frequency_hz / 1e9:.6f} GHz"


@mcp.tool(
    name="siggen_set_power",
    annotations={"title": "Set Output Power", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def siggen_set_power(params: SetPowerInput) -> str:
    """Set the signal generator RF output power level.

    Args:
        params (SetPowerInput):
            - power_dbm (float): Output power in dBm.

    Returns:
        str: Confirmation or error message.
    """
    result = await _send_scpi(f":POW:LEV:IMM:AMPL {params.power_dbm:.2f}DBM")
    if result.startswith("Error"):
        return result
    return f"OK — Power set to {params.power_dbm:.2f} dBm"


@mcp.tool(
    name="siggen_set_rf_output",
    annotations={"title": "Enable/Disable RF Output", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def siggen_set_rf_output(params: SetRFOutputInput) -> str:
    """Enable or disable the RF output of the signal generator.

    Args:
        params (SetRFOutputInput):
            - enabled (bool): True to enable RF output, False to disable.

    Returns:
        str: Confirmation or error message.
    """
    state = "1" if params.enabled else "0"
    result = await _send_scpi(f":OUTP:STAT {state}")
    if result.startswith("Error"):
        return result
    return f"OK — RF output {'ON' if params.enabled else 'OFF'}"


@mcp.tool(
    name="siggen_get_status",
    annotations={"title": "Get Signal Generator Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def siggen_get_status() -> str:
    """Query current frequency, power, and RF output state from the signal generator.

    Returns:
        str: JSON object with frequency_hz, power_dbm, rf_output, and frequency_ghz fields.

    Error response:
        "Error: <message>" if communication fails.
    """
    if not SIGGEN_HOST:
        return "Error: SIGGEN_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SIGGEN_HOST, SIGGEN_PORT),
            timeout=SIGGEN_TIMEOUT,
        )
        results = {}
        for key, cmd in [("frequency_hz", ":FREQ:CW?"), ("power_dbm", ":POW:LEV:IMM:AMPL?"), ("rf_output", ":OUTP:STAT?")]:
            writer.write((cmd + "\n").encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.readline(), timeout=SIGGEN_TIMEOUT)
            results[key] = data.decode().strip()
        writer.close()
        await writer.wait_closed()

        freq_hz = float(results["frequency_hz"]) if results["frequency_hz"] else 0.0
        pwr = float(results["power_dbm"]) if results["power_dbm"] else 0.0
        rf = "ON" if results["rf_output"] in ("1", "ON") else "OFF"
        return json.dumps({
            "frequency_hz": freq_hz,
            "frequency_ghz": freq_hz / 1e9,
            "power_dbm": pwr,
            "rf_output": rf,
        }, indent=2)
    except asyncio.TimeoutError:
        return "Error: Timed out querying signal generator."
    except OSError as e:
        return f"Error: {e}"


@mcp.tool(
    name="siggen_setup_sweep",
    annotations={"title": "Setup Frequency Step Sweep", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def siggen_setup_sweep(params: SweepInput) -> str:
    """Configure a step frequency sweep with equal spacing between start and stop.

    The sweep does NOT start automatically — call siggen_sweep_start after this.

    Args:
        params (SweepInput):
            - start_hz (float): Start frequency in Hz.
            - stop_hz (float): Stop frequency in Hz.
            - power_dbm (float): Output power during sweep.
            - dwell_s (float): Dwell time per step in seconds (default: 0.01).
            - num_points (int): Number of sweep points (default: 101).

    Returns:
        str: JSON summary of sweep configuration or error message.
    """
    commands = [
        ":FREQ:MODE LIST",
        ":LIST:TYPE STEP",
        f":FREQ:STAR {params.start_hz:.6f}HZ",
        f":FREQ:STOP {params.stop_hz:.6f}HZ",
        f":SWE:POIN {params.num_points}",
        f":SWE:DWEL {params.dwell_s:.3f}S",
        f":POW:LEV:IMM:AMPL {params.power_dbm:.2f}DBM",
        ":LIST:TRIG:SOUR IMM",
    ]
    result = await _send_multi(commands)
    if result.startswith("Error"):
        return result
    return json.dumps({
        "start_hz": params.start_hz,
        "stop_hz": params.stop_hz,
        "start_ghz": params.start_hz / 1e9,
        "stop_ghz": params.stop_hz / 1e9,
        "power_dbm": params.power_dbm,
        "num_points": params.num_points,
        "dwell_s": params.dwell_s,
        "status": "sweep configured — call siggen_set_rf_output to enable RF then sweep will start",
    }, indent=2)


if __name__ == "__main__":
    mcp.run()
