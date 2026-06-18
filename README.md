# RF Test Equipment MCPs

MCP servers for controlling RF test equipment in B2B demo topologies driven by Spirent Velocity.

## Topologies

```
SigGen ──► SpecAn                    (basic link budget demo)

SigGen ──► Vertex ──► SpecAn         (channel emulation demo)
```

## Servers

| Server | Instrument | Protocol |
|--------|-----------|---------|
| [`siggen_mcp/`](siggen_mcp/) | Agilent E4438C ESG (and compatible) | SCPI over TCP |
| [`specan_mcp/`](specan_mcp/) | Agilent N9020A MXA (and compatible) | SCPI over TCP |
| [`vertex_mcp/`](vertex_mcp/) | Spirent Vertex Channel Emulator | RPI over Telnet |

## Quick Start

```bash
pip install mcp pydantic

# Set instrument addresses
export SIGGEN_HOST=192.168.1.10
export SPECAN_HOST=192.168.1.11
export VERTEX_HOST=192.168.1.12

# Run any server (stdio transport — used by Velocity / MCP clients)
python siggen_mcp/server.py
python specan_mcp/server.py
python vertex_mcp/server.py
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SIGGEN_HOST` | *(required)* | Signal Generator IP |
| `SIGGEN_PORT` | `5024` | SCPI TCP port |
| `SIGGEN_TIMEOUT` | `10` | Seconds |
| `SPECAN_HOST` | *(required)* | Spectrum Analyzer IP |
| `SPECAN_PORT` | `5025` | SCPI TCP port |
| `SPECAN_TIMEOUT` | `30` | Seconds (allow for sweep time) |
| `VERTEX_HOST` | *(required)* | Vertex IP |
| `VERTEX_PORT` | `23` | Telnet port |
| `VERTEX_TIMEOUT` | `30` | Seconds |

## Key Tools

### Signal Generator (`siggen_mcp`)
- `siggen_set_cw` — set frequency + power + RF enable in one call
- `siggen_get_status` — query current freq/power/RF state
- `siggen_setup_sweep` — configure step frequency sweep

### Spectrum Analyzer (`specan_mcp`)
- `specan_setup_measurement` — set center/span/RBW/reflevel
- `specan_measure_peak` — sweep and return peak frequency + amplitude
- `specan_measure_at_frequency` — measure power at a specific frequency

### Vertex Channel Emulator (`vertex_mcp`)
- `vertex_setup_b2b_demo` — one-shot B2B setup (topology + freq + power + loss + fading)
- `vertex_set_propagation` — apply fading profile (AWGN, EPA5, EVA70, ETU300)
- `vertex_set_awgn` — set C/N ratio for noise injection
- `vertex_start_emulation` / `vertex_stop_emulation` — control emulation state
- `vertex_measure_port_power` — live power readings at ports

## Credits

Instrument command sets derived from the Spirent iTest community driver library:

- [di_agilent_signal_analyzer](https://github.com/Spirent/iTest-assets/tree/main/Libraries/Test.Equipment/Community/di_agilent_signal_analyzer)
- [di_agilent_signal_generator](https://github.com/Spirent/iTest-assets/tree/main/Libraries/Test.Equipment/Community/di_agilent_signal_generator)
- [di_vertex](https://github.com/Spirent/iTest-assets/tree/main/Libraries/Test.Equipment/Community/di_vertex)

Source: [https://github.com/Spirent/iTest-assets](https://github.com/Spirent/iTest-assets)
