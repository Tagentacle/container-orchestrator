"""
Tagentacle Container Orchestrator: Container Lifecycle Management.

A LifecycleNode that manages container lifecycles via a pluggable container
runtime (Podman or Docker) and exposes them as Tagentacle bus Services.
This is an ecosystem package — the Daemon knows nothing about containers;
orchestration lives entirely in userspace.

Bootstrap flow:
  1. Daemon starts (TCP 19999)
  2. This orchestrator starts as a bare process, connects to Daemon
  3. Orchestrator creates/manages containers via Podman/Docker API
  4. Containerized nodes connect back to Daemon via TCP

Services provided:
  /containers/create   — Create and start a container
  /containers/stop     — Stop a running container
  /containers/remove   — Remove a container
  /containers/list     — List all managed containers
  /containers/inspect  — Get container details
  /containers/exec     — Execute a command inside a container
"""

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from container_runtime import ContainerRuntime

from tagentacle_py_core import LifecycleNode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default label for containers managed by this orchestrator
MANAGED_LABEL = "tagentacle.managed"


class ContainerOrchestrator(LifecycleNode):
    """Container lifecycle manager node.

    Connects to a container runtime (Podman or Docker) and exposes container
    CRUD + exec as Tagentacle bus Services. All containers created by this
    orchestrator are labeled with ``tagentacle.managed=true`` for easy filtering.
    """

    def __init__(self, node_id: str = "container_orchestrator"):
        super().__init__(node_id)
        self.runtime: Optional[ContainerRuntime] = None

    # ── Lifecycle hooks ──────────────────────────────────────────────

    def on_configure(self, config: Dict[str, Any]):
        """Connect to container runtime (auto-detects Podman or Docker)."""
        runtime_url = (
            config.get("runtime_url")
            or os.environ.get("CONTAINER_HOST")
            or os.environ.get("DOCKER_HOST")
        )
        backend = config.get("runtime_backend") or os.environ.get("CONTAINER_RUNTIME")

        try:
            self.runtime = ContainerRuntime.connect(url=runtime_url, backend=backend)
            info = self.runtime.info()
            logger.info(
                f"{self.runtime.backend.capitalize()} connected: {info.get('Name', '?')} "
                f"(containers: {info.get('Containers', '?')}, "
                f"images: {info.get('Images', '?')})"
            )
        except RuntimeError as e:
            logger.error(f"Failed to connect to container runtime: {e}")
            raise

    def on_activate(self):
        """Register bus services."""
        pass  # Services are registered via decorators below

    def on_shutdown(self):
        """Close container runtime client."""
        if self.runtime:
            self.runtime.close()
            self.runtime = None
        logger.info("Container orchestrator shut down.")

    # ── Bus Services ─────────────────────────────────────────────────

    async def _register_services(self):
        """Register all /containers/* services after connect."""

        @self.service("/containers/create")
        async def handle_create(payload: dict) -> dict:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._create_container, payload
            )

        @self.service("/containers/stop")
        async def handle_stop(payload: dict) -> dict:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._stop_container, payload
            )

        @self.service("/containers/remove")
        async def handle_remove(payload: dict) -> dict:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._remove_container, payload
            )

        @self.service("/containers/list")
        async def handle_list(payload: dict) -> dict:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._list_containers, payload
            )

        @self.service("/containers/inspect")
        async def handle_inspect(payload: dict) -> dict:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._inspect_container, payload
            )

        @self.service("/containers/exec")
        async def handle_exec(payload: dict) -> dict:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._exec_in_container, payload
            )

    # ── Container operations (sync, run in executor) ────────────────────

    def _create_container(self, payload: dict) -> dict:
        """Create and start a container.

        Request payload:
            image: str          — Container image (required)
            name: str           — Container name (optional)
            env: dict           — Environment variables (optional)
            volumes: dict       — Volume binds {host_path: {"bind": path, "mode": "rw"}} (optional)
            network_mode: str   — Network mode, default "host" (optional)
            command: str        — Override entrypoint command (optional)
            labels: dict        — Extra labels (optional)
        """
        image = payload.get("image")
        if not image:
            return {"error": "Missing required field: image"}

        name = payload.get("name")
        env_vars = payload.get("env", {})
        volumes = payload.get("volumes", {})
        network_mode = payload.get("network_mode", "host")
        command = payload.get("command")
        extra_labels = payload.get("labels", {})

        # Inject Daemon URL so containerized nodes can connect back
        daemon_url = os.environ.get("TAGENTACLE_DAEMON_URL", "tcp://127.0.0.1:19999")
        env_vars.setdefault("TAGENTACLE_DAEMON_URL", daemon_url)

        labels = {MANAGED_LABEL: "true"}
        labels.update(extra_labels)

        try:
            info = self.runtime.create(
                image,
                command=command,
                name=name,
                environment=env_vars,
                volumes=volumes,
                network_mode=network_mode,
                labels=labels,
            )
            logger.info(
                f"Created container '{info.name}' ({info.short_id}) from {image}"
            )
            return {
                "status": "created",
                "id": info.id,
                "short_id": info.short_id,
                "name": info.name,
                "image": image,
            }
        except Exception as e:
            logger.error(f"Failed to create container: {e}")
            return {"error": str(e)}

    def _stop_container(self, payload: dict) -> dict:
        """Stop a container.

        Request: { "name": "..." } or { "id": "..." }, optional "timeout": int
        """
        cid = payload.get("name") or payload.get("id")
        if not cid:
            return {"error": "Missing 'name' or 'id'"}

        timeout = payload.get("timeout", 10)
        try:
            self.runtime.stop(cid, timeout=timeout)
            logger.info(f"Stopped container '{cid}'")
            return {"status": "stopped", "id": cid}
        except Exception as e:
            return {"error": str(e)}

    def _remove_container(self, payload: dict) -> dict:
        """Remove a container.

        Request: { "name": "..." } or { "id": "..." }, optional "force": bool
        """
        cid = payload.get("name") or payload.get("id")
        if not cid:
            return {"error": "Missing 'name' or 'id'"}

        force = payload.get("force", False)
        try:
            self.runtime.remove(cid, force=force)
            logger.info(f"Removed container '{cid}'")
            return {"status": "removed", "id": cid}
        except Exception as e:
            return {"error": str(e)}

    def _list_containers(self, payload: dict) -> dict:
        """List containers managed by this orchestrator.

        Request: { "all": true } to include stopped containers.
        """
        show_all = payload.get("all", False)
        try:
            filters = {"label": MANAGED_LABEL}
            containers = self.runtime.list(all=show_all, filters=filters)
            result = []
            for c in containers:
                result.append(
                    {
                        "id": c.id,
                        "short_id": c.short_id,
                        "name": c.name,
                        "image": c.image,
                        "status": c.status,
                        "labels": c.labels,
                    }
                )
            return {"containers": result, "count": len(result)}
        except Exception as e:
            return {"error": str(e)}

    def _inspect_container(self, payload: dict) -> dict:
        """Get detailed info for a container.

        Request: { "name": "..." } or { "id": "..." }
        """
        cid = payload.get("name") or payload.get("id")
        if not cid:
            return {"error": "Missing 'name' or 'id'"}

        try:
            attrs = self.runtime.inspect(cid)
            return {
                "id": attrs.get("Id", ""),
                "name": attrs.get("Name", "").lstrip("/"),
                "image": attrs.get("Config", {}).get("Image", ""),
                "status": attrs.get("State", {}).get("Status", ""),
                "started_at": attrs.get("State", {}).get("StartedAt", ""),
                "env": attrs.get("Config", {}).get("Env", []),
                "labels": attrs.get("Config", {}).get("Labels", {}),
                "network_mode": attrs.get("HostConfig", {}).get("NetworkMode", ""),
                "ports": attrs.get("NetworkSettings", {}).get("Ports", {}),
            }
        except Exception as e:
            return {"error": str(e)}

    def _exec_in_container(self, payload: dict) -> dict:
        """Execute a command inside a container.

        Request:
            name/id: str        — Target container (required)
            command: str|list   — Command to run (required)
            workdir: str        — Working directory inside container (optional)
            env: dict           — Extra env vars for this exec (optional)
        """
        cid = payload.get("name") or payload.get("id")
        command = payload.get("command")
        if not cid:
            return {"error": "Missing 'name' or 'id'"}
        if not command:
            return {"error": "Missing 'command'"}

        workdir = payload.get("workdir")
        env_vars = payload.get("env", {})
        environment = {k: str(v) for k, v in env_vars.items()} if env_vars else None

        try:
            result = self.runtime.exec(
                cid,
                command,
                workdir=workdir,
                environment=environment,
            )

            stdout = result.stdout
            stderr = result.stderr

            # Truncate very large outputs
            max_len = 64 * 1024  # 64 KB
            if len(stdout) > max_len:
                stdout = stdout[:max_len] + "\n... (truncated)"
            if len(stderr) > max_len:
                stderr = stderr[:max_len] + "\n... (truncated)"

            return {
                "exit_code": result.exit_code,
                "stdout": stdout,
                "stderr": stderr,
            }
        except Exception as e:
            return {"error": str(e)}


async def main():
    node = ContainerOrchestrator()

    # Register services before connect so they get batch-registered
    await node._register_services()

    config = {}
    # Support explicit runtime configuration
    runtime_url = os.environ.get("CONTAINER_HOST") or os.environ.get("DOCKER_HOST")
    if runtime_url:
        config["runtime_url"] = runtime_url
    runtime_backend = os.environ.get("CONTAINER_RUNTIME")
    if runtime_backend:
        config["runtime_backend"] = runtime_backend

    await node.bringup(config)
    logger.info(
        f"Container Orchestrator ready (backend: {node.runtime.backend}). "
        "Services: /containers/create, /containers/stop, /containers/remove, "
        "/containers/list, /containers/inspect, /containers/exec"
    )
    await node.spin()


if __name__ == "__main__":
    asyncio.run(main())
