# Wyrd V1 dependencies

This document describes the dependencies of the current Linux desktop build. Wyrd does not install or manage Steam, Proton, Skyrim, or MO2 as system packages.

## Supported platform

- Linux x86_64 is the release target.
- A graphical desktop session is required to run the Qt application.
- The standalone executable targets glibc-based Linux distributions. It is not a fully static binary.

## Required host applications and data

| Dependency | Why Wyrd needs it | How it is supplied |
|---|---|---|
| Steam for Linux | Finds Steam libraries, starts/queries the client, and owns Skyrim AppID `489830` | Install and configure Steam separately |
| Skyrim Special Edition | The game Wyrd configures and launches | Install through Steam |
| Proton | Windows compatibility runtime for MO2 and Skyrim | Install through Steam; Wyrd discovers installed runtimes |
| MO2 | Mod manager and virtual filesystem used for modded launches | Wyrd can download and run the official MO2 2.5.2 installer |
| Network access | First-run Python package installation and official MO2 installer download | Host network connection |

Launch MO2/Skyrim only after Skyrim has been launched through Steam once and its AppID `489830` Proton prefix exists and is initialized. Wyrd intentionally refuses to create or reset that Steam prefix.

## Source-run dependencies

### Python

- Python 3.10 or newer is recommended.
- Python's `venv` module is required by `qt-ui/run.sh`.
- `pip` is required to install the project requirements.

### Python packages

The authoritative list is [`qt-ui/requirements.txt](../qt-ui/requirements.txt):

- `PySide6>=6.6,<7` — Qt 6 Python bindings and GUI runtime.

The launcher creates an isolated environment at `qt-ui/.venv` and installs this file through `qt-ui/install-requirements.sh`. It does not install packages globally.

### Linux system libraries

Qt wheels include much of the Qt runtime, but the host still needs compatible graphics and window-system libraries. On Ubuntu/Debian-family distributions, the following package set is a practical starting point:

```bash
sudo apt update
sudo apt install --yes \
  python3 python3-venv python3-pip \
  libegl1 libgl1 libdbus-1-3 libxkbcommon0 \
  libxkbcommon-x11-0 libxcb-cursor0 libpulse0
```

Package names and split-library packaging vary by distribution and desktop environment. If Qt reports a missing shared library, install the distribution package that provides that library. The GitHub CI job currently installs `libegl1`, `libgl1`, `libxkbcommon-x11-0`, and `libdbus-1-3` for its offscreen import/test environment.

## Standalone release dependencies

The `Wyrd-linux-x86_64.zip` artifact bundles:

- Python runtime
- PySide6 and the Qt application runtime
- Wyrd application code

It does not bundle:

- Steam or Steam WebHelper
- Proton or other compatibility tools
- Skyrim Special Edition
- Mod Organizer 2
- Host graphics drivers, display server, glibc, or all system libraries
- External archive tools

The standalone workflow builds on Ubuntu 22.04 and validates the executable and ZIP there. A compatible host system is still required.

## Build-only dependencies

These are used by GitHub Actions to build the standalone package, not required to run Wyrd from source:

- PyInstaller
- `patchelf`, `file`, and `zip`
- Ubuntu build runner's Qt/graphics support libraries

The exact build steps are maintained in [`.github/workflows/standalone.yml`](../.github/workflows/standalone.yml) and [`qt-ui/Wyrd.spec`](../qt-ui/Wyrd.spec).

## Verify the source environment

From the repository root:

```bash
python3 --version
python3 -m venv --help
python3 -m pip --version
bash qt-ui/run.sh
```

To inspect the installed Python dependency:

```bash
qt-ui/.venv/bin/python -c 'import PySide6; print(PySide6.__version__)'
```
