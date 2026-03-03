# container-orchestrator

Container lifecycle management node for Tagentacle. Manages Docker/Podman containers via the bus — **not** part of the Daemon core.

> Like Docker is a userspace program on Linux (not a kernel module), this orchestrator is a Tagentacle ecosystem package (not a Daemon feature).

## Services

| Service | Description |
|---|---|
| `/containers/create` | Create and start a container from an image |
| `/containers/stop` | Stop a running container |
| `/containers/remove` | Remove a container |
| `/containers/list` | List all managed containers |
| `/containers/inspect` | Get container details |
| `/containers/exec` | Execute a command inside a container |

## Quick Start

```bash
# Install dependencies
cd container-orchestrator
uv sync

# Run (requires Docker daemon running)
tagentacle run .
# or directly:
python orchestrator.py
```

## Usage Examples

From any Tagentacle node or CLI:

```bash
# Create a container
tagentacle service call /containers/create '{"image": "ubuntu:22.04", "name": "agent_space_1"}'

# List managed containers
tagentacle service call /containers/list '{}'

# Run a command
tagentacle service call /containers/exec '{"name": "agent_space_1", "command": "ls -la /workspace"}'

# Stop
tagentacle service call /containers/stop '{"name": "agent_space_1"}'

# Remove
tagentacle service call /containers/remove '{"name": "agent_space_1"}'
```

## Design

- Connects to Daemon as a regular `LifecycleNode` — no special Daemon privileges
- All containers created are labeled with `tagentacle.managed=true`
- Auto-injects `TAGENTACLE_DAEMON_URL` env var so containerized nodes can connect back
- Default network mode: `host` (simplest for bus connectivity)
- No ACL logic — access control handled by TACL at the MCP layer

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TAGENTACLE_DAEMON_URL` | `tcp://127.0.0.1:19999` | Daemon address |
| `DOCKER_HOST` | _(system default)_ | Docker daemon socket URL |

## License

MIT
