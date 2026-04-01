# Repository Guidelines

## Project Structure & Module Organization
This repository mixes Hailo Raspberry Pi examples with Garuda application work. Use `basic_pipelines/` for Python inference pipelines and the web UI (`garuda_web/`). Use `ProjectGaruda/` for the Tkinter-based desktop app, `Garuda/` for the SwiftUI macOS app, `GarudaTests/` and `GarudaUITests/` for Apple-platform tests, `cpp/` for Meson-built post-process code, `resources/` for built artifacts, `doc/` and `docs/` for documentation, and `scripts/` for server helpers. Python tests live in `tests/`.

## Build, Test, and Development Commands
Set up the Hailo environment by sourcing `setup_env.sh`:

```bash
source setup_env.sh
```

Install Python dependencies with `pip install -r requirements.txt -r requirements-test.txt`. Run the Python test suite with `pytest tests`. Start the Garuda web server with `bash scripts/start_server.sh`, stop it with `bash scripts/stop_server.sh`, and restart it with `bash scripts/restart_server.sh`. Build the C++ post-process library with:

```bash
meson setup build.release
meson compile -C build.release
```

Open `Garuda.xcodeproj` in Xcode for SwiftUI development and run the `Garuda` scheme there.

## Coding Style & Naming Conventions
Follow existing file conventions instead of introducing a new style per module. Python uses 4-space indentation, snake_case functions, and `test_*.py` naming. Swift uses standard Apple naming: UpperCamelCase types and lowerCamelCase properties. Keep modules focused, avoid cross-directory imports unless already established, and prefer small, direct helper scripts in `scripts/`. No repo-wide formatter config is checked in, so keep changes consistent with surrounding code.

## Testing Guidelines
Use `pytest` for Python changes. Add tests under `tests/` with names like `test_api_state.py` or `test_project_garuda.py`, and keep test functions named `test_*`. Mock Hailo, GPIO, network, and UI dependencies where hardware is not available. For Swift changes, add or extend tests in `GarudaTests/` or `GarudaUITests/`.

## Commit & Pull Request Guidelines
Recent history favors short imperative subjects such as `Fix camera feed...` and `Add start/stop/restart server scripts`. Keep commits focused and use a concise summary line. Pull requests should describe the affected area, list validation steps (`pytest tests`, manual UI checks, Meson build), and include screenshots for UI work in `Garuda/` or `basic_pipelines/garuda_web/`.

## Security & Configuration Tips
Do not commit virtual environments, PID files, logs, or secrets. Treat `basic_pipelines/system_logs/` and `/tmp/garuda_server.log` as runtime data, not source. Hardware-dependent changes should document required devices, expected Hailo/TAPPAS versions, and any camera or servo assumptions.
