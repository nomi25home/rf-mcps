#!/usr/bin/env python3
"""
MCP Server for Agilent Signal Analyzer (N9020A MXA and compatible).

Connects over Telnet/SCPI. Configure via env vars:
  SPECAN_HOST    - instrument IP address (required)
  SPECAN_PORT    - telnet port (default: 5025)
  SPECAN_TIMEOUT - command timeout in seconds (default: 30)
"""

import asyncio
import json
import os
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("specan_mcp")

SPECAN_HOST = os.environ.get("SPECAN_HOST", "")
SPECAN_PORT = int(os.environ.get("SPECAN_PORT", "5025"))
SPECAN_TIMEOUT = float(os.environ.get("SPECAN_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# Telnet transport
# ---------------------------------------------------------------------------

async def _send_scpi(command: str) -> str:
    """Send a SCPI command, return response if query."""
    if not SPECAN_HOST:
        return "Error: SPECAN_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SPECAN_HOST, SPECAN_PORT),
            timeout=SPECAN_TIMEOUT,
        )
        writer.write((command + "\n").encode())
        await writer.drain()
        if "?" in command:
            data = await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)
            response = data.decode().strip()
        else:
            response = ""
        writer.close()
        await writer.wait_closed()
        return response
    except asyncio.TimeoutError:
        return "Error: Connection timed out. Check SPECAN_HOST and SPECAN_PORT."
    except OSError as e:
        return f"Error: Could not connect to {SPECAN_HOST}:{SPECAN_PORT} — {e}"


async def _send_multi(commands: list[str]) -> dict[str, str]:
    """Send multiple SCPI commands, collect responses for queries."""
    if not SPECAN_HOST:
        return {"_error": "SPECAN_HOST environment variable not set."}
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SPECAN_HOST, SPECAN_PORT),
            timeout=SPECAN_TIMEOUT,
        )
        responses: dict[str, str] = {}
        for cmd in commands:
            writer.write((cmd + "\n").encode())
            await writer.drain()
            if "?" in cmd:
                data = await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)
                responses[cmd] = data.decode().strip()
        writer.close()
        await writer.wait_closed()
        return responses
    except asyncio.TimeoutError:
        return {"_error": "Connection timed out."}
    except OSError as e:
        return {"_error": str(e)}


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class MeasurementSetupInput(BaseModel):
    """Input for configuring spectrum analyzer measurement."""
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    center_hz: float = Field(..., description="Center frequency in Hz (e.g. 2.4e9 for 2.4 GHz).")
    span_hz: float = Field(..., description="Frequency span in Hz (e.g. 10e6 for 10 MHz span).")
    rbw_hz: Optional[float] = Field(default=None, description="Resolution bandwidth in Hz (1 to 8e6). If None, auto-coupled.")
    ref_level_dbm: float = Field(default=0.0, description="Reference level in dBm (top of display).")


class PeakSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    marker_number: int = Field(default=1, description="Marker number to use (1 to 12).", ge=1, le=12)
    num_averages: int = Field(default=1, description="Number of sweeps to average before searching peak (1 = single sweep).", ge=1, le=100)


class MarkerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    marker_number: int = Field(default=1, description="Marker number (1 to 12).", ge=1, le=12)
    frequency_hz: Optional[float] = Field(default=None, description="Place marker at this frequency in Hz. If None, uses current marker position.")


class ChannelPowerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    center_hz: float = Field(..., description="Center frequency in Hz.")
    channel_bw_hz: float = Field(..., description="Channel integration bandwidth in Hz.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="specan_init",
    annotations={"title": "Initialize Spectrum Analyzer", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def specan_init() -> str:
    """Initialize the Agilent Signal Analyzer with a mode preset.

    Resets the analyzer to SA (Spectrum Analyzer) mode with default settings.
    Call this at the start of each test session.

    Returns:
        str: "OK" on success or an error message.
    """
    responses = await _send_multi(["*RST", ":INST:SEL SA", ":SENS:SWE:MODE SING"])
    if "_error" in responses:
        return f"Error: {responses['_error']}"
    return "OK — Spectrum Analyzer initialized in SA mode."


@mcp.tool(
    name="specan_setup_measurement",
    annotations={"title": "Setup Spectrum Measurement", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def specan_setup_measurement(params: MeasurementSetupInput) -> str:
    """Configure center frequency, span, RBW, and reference level for a spectrum measurement.

    This is the primary configuration tool for B2B demos. Call this before
    any measurement or peak search.

    Args:
        params (MeasurementSetupInput):
            - center_hz (float): Center frequency in Hz.
            - span_hz (float): Frequency span in Hz.
            - rbw_hz (Optional[float]): Resolution bandwidth in Hz (auto if None).
            - ref_level_dbm (float): Reference level in dBm (default: 0.0).

    Returns:
        str: JSON confirmation of applied settings or error message.
    """
    commands = [
        f":FREQ:CENT {params.center_hz:.6f}HZ",
        f":FREQ:SPAN {params.span_hz:.6f}HZ",
        f":DISP:WIND:TRAC:Y:RLEV {params.ref_level_dbm:.2f}DBM",
    ]
    if params.rbw_hz is not None:
        commands.append(f":BAND {params.rbw_hz:.1f}HZ")
    else:
        commands.append(":BAND:AUTO ON")

    responses = await _send_multi(commands)
    if "_error" in responses:
        return f"Error: {responses['_error']}"

    return json.dumps({
        "center_hz": params.center_hz,
        "center_ghz": params.center_hz / 1e9,
        "span_hz": params.span_hz,
        "span_mhz": params.span_hz / 1e6,
        "rbw_hz": params.rbw_hz if params.rbw_hz else "auto",
        "ref_level_dbm": params.ref_level_dbm,
        "status": "applied",
    }, indent=2)


@mcp.tool(
    name="specan_measure_peak",
    annotations={"title": "Measure Peak Signal", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def specan_measure_peak(params: PeakSearchInput) -> str:
    """Trigger a sweep, find the peak signal, and return its frequency and amplitude.

    The primary measurement tool for B2B demos. Returns peak frequency and power
    from marker 1 (or specified marker).

    Args:
        params (PeakSearchInput):
            - marker_number (int): Which marker to use (default: 1).
            - num_averages (int): Number of sweeps to average first (default: 1).

    Returns:
        str: JSON with peak_frequency_hz, peak_frequency_ghz, peak_amplitude_dbm, marker_number.

    Error response:
        "Error: <message>" if communication fails.
    """
    if not SPECAN_HOST:
        return "Error: SPECAN_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SPECAN_HOST, SPECAN_PORT),
            timeout=SPECAN_TIMEOUT,
        )
        mn = params.marker_number
        setup_cmds = []
        if params.num_averages > 1:
            setup_cmds += [
                f":AVER:COUN {params.num_averages}",
                ":AVER ON",
                ":INIT:CONT OFF",
                ":INIT:IMM;*WAI",
            ]
        else:
            setup_cmds += [
                ":AVER OFF",
                ":INIT:CONT OFF",
                ":INIT:IMM;*WAI",
            ]

        for cmd in setup_cmds:
            writer.write((cmd + "\n").encode())
            await writer.drain()

        # Wait for sweep completion
        writer.write((":OPC?\n").encode())
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)

        # Peak search and read marker
        peak_cmds = [
            f":CALC:MARK{mn}:MODE POS",
            f":CALC:MARK{mn}:MAX",
        ]
        for cmd in peak_cmds:
            writer.write((cmd + "\n").encode())
            await writer.drain()

        # Query freq
        writer.write((f":CALC:MARK{mn}:X?\n").encode())
        await writer.drain()
        freq_data = await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)

        # Query amplitude
        writer.write((f":CALC:MARK{mn}:Y?\n").encode())
        await writer.drain()
        amp_data = await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)

        writer.close()
        await writer.wait_closed()

        freq_hz = float(freq_data.decode().strip())
        amp_dbm = float(amp_data.decode().strip())

        return json.dumps({
            "marker_number": mn,
            "peak_frequency_hz": freq_hz,
            "peak_frequency_ghz": freq_hz / 1e9,
            "peak_amplitude_dbm": amp_dbm,
            "averages": params.num_averages,
        }, indent=2)

    except asyncio.TimeoutError:
        return "Error: Timed out waiting for sweep to complete. Try increasing SPECAN_TIMEOUT."
    except (OSError, ValueError) as e:
        return f"Error: {e}"


@mcp.tool(
    name="specan_place_marker",
    annotations={"title": "Place Marker at Frequency", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def specan_place_marker(params: MarkerInput) -> str:
    """Place a marker at a specific frequency and read its amplitude.

    Useful for measuring power at a known frequency (e.g. the SigGen output).

    Args:
        params (MarkerInput):
            - marker_number (int): Marker to use (1 to 12, default: 1).
            - frequency_hz (Optional[float]): Place marker here. If None, reads current position.

    Returns:
        str: JSON with marker frequency and amplitude, or error message.
    """
    if not SPECAN_HOST:
        return "Error: SPECAN_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SPECAN_HOST, SPECAN_PORT),
            timeout=SPECAN_TIMEOUT,
        )
        mn = params.marker_number
        cmds = [
            ":INIT:CONT OFF",
            ":INIT:IMM;*WAI",
        ]
        for cmd in cmds:
            writer.write((cmd + "\n").encode())
            await writer.drain()

        writer.write((":OPC?\n").encode())
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)

        if params.frequency_hz is not None:
            writer.write((f":CALC:MARK{mn}:MODE POS\n").encode())
            await writer.drain()
            writer.write((f":CALC:MARK{mn}:X {params.frequency_hz:.6f}HZ\n").encode())
            await writer.drain()

        writer.write((f":CALC:MARK{mn}:X?\n").encode())
        await writer.drain()
        freq_data = await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)

        writer.write((f":CALC:MARK{mn}:Y?\n").encode())
        await writer.drain()
        amp_data = await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)

        writer.close()
        await writer.wait_closed()

        freq_hz = float(freq_data.decode().strip())
        amp_dbm = float(amp_data.decode().strip())

        return json.dumps({
            "marker_number": mn,
            "frequency_hz": freq_hz,
            "frequency_ghz": freq_hz / 1e9,
            "amplitude_dbm": amp_dbm,
        }, indent=2)
    except asyncio.TimeoutError:
        return "Error: Timed out."
    except (OSError, ValueError) as e:
        return f"Error: {e}"


@mcp.tool(
    name="specan_get_status",
    annotations={"title": "Get Spectrum Analyzer Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def specan_get_status() -> str:
    """Query current center frequency, span, RBW, and reference level settings.

    Returns:
        str: JSON object with center_hz, span_hz, rbw_hz, ref_level_dbm fields.
    """
    queries = {
        "center_hz": ":FREQ:CENT?",
        "span_hz": ":FREQ:SPAN?",
        "rbw_hz": ":BAND?",
        "ref_level_dbm": ":DISP:WIND:TRAC:Y:RLEV?",
    }
    if not SPECAN_HOST:
        return "Error: SPECAN_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SPECAN_HOST, SPECAN_PORT),
            timeout=SPECAN_TIMEOUT,
        )
        results = {}
        for key, cmd in queries.items():
            writer.write((cmd + "\n").encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)
            results[key] = float(data.decode().strip())
        writer.close()
        await writer.wait_closed()

        results["center_ghz"] = results["center_hz"] / 1e9
        results["span_mhz"] = results["span_hz"] / 1e6
        results["rbw_khz"] = results["rbw_hz"] / 1e3
        return json.dumps(results, indent=2)
    except asyncio.TimeoutError:
        return "Error: Timed out."
    except (OSError, ValueError) as e:
        return f"Error: {e}"


@mcp.tool(
    name="specan_measure_at_frequency",
    annotations={"title": "Measure Power at Specific Frequency", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def specan_measure_at_frequency(params: MarkerInput) -> str:
    """Configure, sweep, and measure power at a specific frequency in one call.

    This is a high-level workflow tool for demos: sets center to the target
    frequency with a 10 MHz span, sweeps once, and returns the amplitude.

    Args:
        params (MarkerInput):
            - frequency_hz (float): Frequency to measure at in Hz.
            - marker_number (int): Marker to use (default: 1).

    Returns:
        str: JSON with frequency and measured amplitude in dBm.
    """
    if params.frequency_hz is None:
        return "Error: frequency_hz is required for this tool."
    if not SPECAN_HOST:
        return "Error: SPECAN_HOST environment variable not set."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SPECAN_HOST, SPECAN_PORT),
            timeout=SPECAN_TIMEOUT,
        )
        mn = params.marker_number
        span_hz = 10e6  # 10 MHz span for single-carrier measurements
        cmds = [
            f":FREQ:CENT {params.frequency_hz:.6f}HZ",
            f":FREQ:SPAN {span_hz:.0f}HZ",
            ":BAND:AUTO ON",
            ":AVER OFF",
            ":INIT:CONT OFF",
            ":INIT:IMM;*WAI",
        ]
        for cmd in cmds:
            writer.write((cmd + "\n").encode())
            await writer.drain()

        writer.write((":OPC?\n").encode())
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)

        marker_cmds = [
            f":CALC:MARK{mn}:MODE POS",
            f":CALC:MARK{mn}:X {params.frequency_hz:.6f}HZ",
        ]
        for cmd in marker_cmds:
            writer.write((cmd + "\n").encode())
            await writer.drain()

        writer.write((f":CALC:MARK{mn}:Y?\n").encode())
        await writer.drain()
        amp_data = await asyncio.wait_for(reader.readline(), timeout=SPECAN_TIMEOUT)

        writer.close()
        await writer.wait_closed()

        amp_dbm = float(amp_data.decode().strip())
        return json.dumps({
            "frequency_hz": params.frequency_hz,
            "frequency_ghz": params.frequency_hz / 1e9,
            "amplitude_dbm": amp_dbm,
            "marker_number": mn,
        }, indent=2)
    except asyncio.TimeoutError:
        return "Error: Timed out waiting for measurement."
    except (OSError, ValueError) as e:
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run()
