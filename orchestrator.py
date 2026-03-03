"""
Tagentacle Container Orchestrator: Docker/Podman Lifecycle Management.

A LifecycleNode that manages container lifecycles via the Docker API and exposes
them as Tagentacle bus Services. This is an ecosystem package — the Daemon knows
nothing about containers; orchestration lives entirely in userspace.

Bootstrap flow:
  1. Daemon starts (TCP 19999)
  2. This orchestrator starts as a bare process, connects to Daemon
  3. Orchestrator creates/manages containers via Docker API
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

import docker
from docker.errors import DockerException, NotFound, APIError

from tagentacle_py_core import LifecycleNode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default label for containers managed by this orchestrator
MANAGED_LABEL = "tagentacle.managed"


class ContainerOrchestrator(LifecycleNode):
    """Container lifecycle manager node.

    Connects to the local Docker daemon and exposes container CRUD + exec
    as Tagentacle bus Services. All containers created by this orchestrator
    are labeled with ``tagentacle.managed=true`` for easy filtering.
    """

    def __init__(self, node_id: str = "container_orchestrator"):
        super().__init__(node_id)
        self.docker: Optional[docker.DockerClient] = None

    # ── Lifecycle hooks ──────────────────────────────────────────────

    def on_configure(self, config: Dict[str, Any]):
        """Connect to Docker daemon."""
        docker_url = config.get("docker_url") or os.environ.get("DOCKER_HOST")
        try:
            if docker_url:
                self.docker = docker.DockerClient(base_url=docker_url)
            else:
                self.docker = docker.from_env()
            info = self.docker.info()
            logger.info(
                f"Docker connected: {info.get('Name', '?')} "
                f"(containers: {info.get('Containers', '?')}, "
                f"images: {info.get('Images', '?')})"
            )
        except DockerException as e:
            logger.error(f"Failed to connect to Docker: {e}")
            raise

    def on_activate(self):
        """Register bus services."""
        pass  # Services are registered via decorators below

    def on_shutdown(self):
        """Close Docker client."""
        if self.docker:
            try:
                self.docker.close()
            except Exception:
                pass
            self.docker = None
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

    # ── Docker operations (sync, run in executor) ────────────────────

    def _create_container(self, payload: dict) -> dict:
        """Create and start a container.

        Request payload:
            image: str          — Docker image (required)
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
            container = self.docker.containers.run(
                image,
                command=command,
                name=name,
                environment=env_vars,
                volumes=volumes,
                network_mode=network_mode,
                labels=labels,
                detach=True,
                stdin_open=True,
            )
            logger.info(f"Created container '{container.name}' ({container.short_id}) from {image}")
            return {
                "status": "created",
                "id": container.id,
                "short_id": container.short_id,
                "name": container.name,
                "image": image,
            }
        except APIError as e:
            logger.error(f"Docker API error creating container: {e}")
            return {"error": str(e)}
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
            container = self.docker.containers.get(cid)
            container.stop(timeout=timeout)
            logger.info(f"Stopped container '{cid}'")
            return {"status": "stopped", "name": container.name, "id": container.id}
        except NotFound:
            return {"error": f"Container '{cid}' not found"}
        except APIError as e:
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
            container = self.docker.containers.get(cid)
            container.remove(force=force)
            logger.info(f"Removed container '{cid}'")
            return {"status": "removed", "name": container.name, "id": container.id}
        except NotFound:
            return {"error": f"Container '{cid}' not found"}
        except APIError as e:
            return {"error": str(e)}

    def _list_containers(self, payload: dict) -> dict:
        """List containers managed by this orchestrator.

        Request: { "all": true } to include stopped containers.
        """
        show_all = payload.get("all", False)
        try:
            filters = {"label": MANAGED_LABEL}
            containers = self.docker.containers.list(all=show_all, filters=filters)
            result = []
            for c in containers:
                result.append({
                    "id": c.id,
                    "short_id": c.short_id,
                    "name": c.name,
                    "image": str(c.image.tags[0]) if c.image.tags else str(c.image.id[:12]),
                    "status": c.status,
                    "labels": dict(c.labels),
                })
            return {"containers": result, "count": len(result)}
        except APIError as e:
            return {"error": str(e)}

    def _inspect_container(self, payload: dict) -> dict:
        """Get detailed info for a container.

        Request: { "name": "..." } or { "id": "..." }
        """
        cid = payload.get("name") or payload.get("id")
        if not cid:
            return {"error": "Missing 'name' or 'id'"}

        try:
            container = self.docker.containers.get(cid)
            attrs = container.attrs
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
        except NotFound:
            return {"error": f"Container '{cid}' not found"}
        except APIError as e:
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

        # Convert command string to shell invocation
        if isinstance(command, str):
            cmd = ["sh", "-c", command]
        else:
            cmd = command

        # Build env list
        environment = {k: str(v) for k, v in env_vars.items()} if env_vars else None

        try:
            container = self.docker.containers.get(cid)
            exit_code, output = container.exec_run(
                cmd,
                workdir=workdir,
                environment=environment,
                demux=True,
            )
            stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
            stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""

            # Truncate very large outputs
            max_len = 64 * 1024  # 64 KB
            if len(stdout) > max_len:
                stdout = stdout[:max_len] + "\n... (truncated)"
            if len(stderr) > max_len:
                stderr = stderr[:max_len] + "\n... (truncated)"

            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
            }
        except NotFound:
            return {"error": f"Container '{cid}' not found"}
        except APIError as e:
            return {"error": str(e)}


async def main():
    node = ContainerOrchestrator()

    # Register services before connect so they get batch-registered
    await node._register_services()

    config = {}
    docker_url = os.environ.get("DOCKER_HOST")
    if docker_url:
        config["docker_url"] = docker_url

    await node.bringup(config)
    logger.info(
        "Container Orchestrator ready. "
        "Services: /containers/create, /containers/stop, /containers/remove, "
        "/containers/list, /containers/inspect, /containers/exec"
    )
    await node.spin()


if __name__ == "__main__":
    asyncio.run(main())
