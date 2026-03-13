# Changelog — container-orchestrator

All notable changes to **container-orchestrator** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-03-13

### Added
- **`.gitignore`**: New file; ignores `__pycache__/`, `.ruff_cache/`, `.venv/`, etc.
- **GitHub Actions CI** (Layer 1): Lint (ruff) + build (uv). No test job — use `tagentacle test` locally.
- **`[build-system]`** in `pyproject.toml`: Added hatchling backend for `uv build` support.

### Fixed
- **`container_runtime.py`**: Defensive `exec()` output parsing — normalizes across Docker-py and podman-py 4.x return formats.
- **`container_runtime.py`**: Defensive `_to_info()` — `container.status` can crash on podman-py 4.x when `State` is a string; now wrapped with try/except fallback.
- **`container_runtime.py`**: Removed unused `shutil` import.
- **`pyproject.toml`**: Podman dependency range fixed to `>=4.0.0,<5.0.0` (podman-py 5.x incompatible with Podman 4.x engine).

### Changed
- **`orchestrator.py`**: Applied ruff formatting (line length, dict literals).

## [0.2.0] - 2026-03-03

### Added
- Initial release as standalone package (extracted from Tagentacle monorepo).
- `ContainerOrchestrator(LifecycleNode)` with 6 bus services: `/containers/{create,stop,remove,list,inspect,exec}`.
- `container_runtime.py` — unified Podman/Docker abstraction with auto-detection.

## [0.1.0] - 2026-03-03

### Added
- Prototype (part of tagentacle v0.4.0 release).
