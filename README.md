# Wyrd

Wyrd is a focused Qt 6 desktop application for Linux that helps configure and launch **The Elder Scrolls V: Skyrim Special Edition** through **Mod Organizer 2 (MO2)** and Steam **Proton**.

V1 focuses on one job: discover the installed Skyrim/Proton environment, verify the MO2 instance and executable, and launch modded Skyrim through the game's existing Steam AppID `489830` compatibility context.

## Where Wyrd came from

Wyrd started as a collection of personal scripts written to solve one person's Skyrim-on-Linux setup and launch problems. Those scripts gradually grew into a desktop application: AI tools were later used to help refine the scripts, work through implementation details, and build a graphical interface around them.

It was originally made for personal use, not conceived as a commercial product or a universal modding solution. It is being shared in case the approach and tools are useful to other Linux players with a similar Skyrim and MO2 setup. The project is still intentionally focused, and your setup may differ.

## What Wyrd does

- Detects Skyrim Special Edition from Steam libraries, including libraries registered in Steam's `libraryfolders.vdf`.
- Discovers compatible Proton runtimes and remembers the user's selected runtime.
- Installs the official MO2 Windows installer into Skyrim's existing Proton prefix.
- Discovers MO2 instances, profiles, and configured Skyrim/SKSE executables.
- Verifies paths and the MO2-to-Skyrim drive mapping before launch.
- Launches MO2 and modded Skyrim through the existing Steam AppID `489830` context.
- Offers a direct Steam launch for an unmodded baseline.
- Monitors launch/session state and provides application and launch diagnostics.

MO2 remains responsible for profiles, mods, downloads, load order, and its virtual filesystem. Wyrd is an environment and launch companion, not a replacement mod manager.

## Requirements

Wyrd targets **Linux x86_64**. A working desktop session is required.

- Python 3.10+ with `venv` and `pip` for the source launcher, or use the standalone release.
- Steam for Linux, with Skyrim Special Edition installed and launched through Steam at least once so AppID `489830` has an initialized Proton prefix.
- A compatible Steam Proton runtime.
- Network access for first-run Python dependency installation and the official MO2 installer download.
- Compatible host graphics, display-server, and Qt system libraries.

See [Dependencies](docs/DEPENDENCIES.md) for distro package examples and the exact Python requirement.

## Install and run from source

Clone the repository, then run:

```bash
git clone https://github.com/JaspreetSingh199/Wyrd.git
cd Wyrd
bash qt-ui/run.sh
```

The launcher creates `qt-ui/.venv` and installs the Python requirements on first run. To add Wyrd to your desktop application menu:

```bash
bash qt-ui/install-desktop-launcher.sh
```

The desktop entry points to the checkout's `qt-ui/run.sh`; keep the repository in place after installing the launcher.

See [Installation and configuration](qt-ui/README.md) for first-run setup and operating instructions.

## Standalone release

The GitHub Actions workflow builds a standalone Linux x86_64 executable and packages it as `Wyrd-linux-x86_64.zip`.

- Download the ZIP from the repository's [Actions artifacts](https://github.com/JaspreetSingh199/Wyrd/actions) for a successful build, or from the Assets section of a versioned [GitHub Release](https://github.com/JaspreetSingh199/Wyrd/releases).
- Extract the ZIP.
- Run `./Wyrd` from the extracted `Wyrd-linux-x86_64` directory.

The standalone bundle includes Python, PySide6, and the Qt application runtime. It does **not** bundle Steam, Proton, Skyrim, MO2, or host graphics/display libraries.

## Safety boundaries

- Wyrd uses Skyrim's existing Steam AppID `489830` compatdata prefix for MO2 integration; it does not create or reset that Steam prefix.
- Wyrd does not migrate, copy, or symlink saves.
- Wyrd does not silently change the selected Proton runtime.
- MO2 discovery is read-only. Wyrd verifies the selected instance and launch target again at the launch boundary.
- Destructive MO2 removal is restricted to a verified Wyrd-managed MO2 program directory; MO2 instances and user data are preserved.
- Wyrd settings reset does not uninstall MO2 or touch Skyrim/Steam data.

## Future nice-to-haves (not V1 requirements)

Wyrd intentionally stays focused on launching modded Skyrim. The following conveniences may be considered later if real user demand justifies the extra complexity; they are not promises or prerequisites for V1.

- **Smoother adoption of existing MO2 installations:** make it easier to select and validate MO2 installations that were set up independently of Wyrd, while reusing the same discovery and launch workflow. MO2 should remain responsible for its own instances and data.
- **Moving an MO2 setup into another installation (user-managed workaround):** this may be possible without a Wyrd migration feature. A user can create a new instance of the same type in the destination MO2 installation, close MO2, back up the original instance and its associated data, then move/copy the relevant MO2 instance files and data into the newly created instance and update configuration entries that still point to the old locations. Reopen MO2 and check the instance, profiles, mods, downloads, and executable paths before using it. The exact files and paths depend on the source instance type and storage configuration, so this is a manual, case-by-case workaround—not a guaranteed or officially supported migration. Keep the original backup until the new instance has been tested successfully. Wyrd does not perform this transfer.
- **Broader multi-library and secondary-drive edge-case handling:** Wyrd already discovers Skyrim using Steam's registered library metadata. Future work could improve resilience for unusual library layouts, mount points, or Proton access to secondary drives. This does not mean turning Wyrd into a general Steam library or multi-game manager; Skyrim SE remains the supported game.
- **Long-term V2 possibility — Bethesda multi-game support:** once the Skyrim-focused V1 is stable and released, Wyrd could expand to other Bethesda games supported by MO2 (for example, Morrowind, Oblivion, other Skyrim editions, Fallout 3/New Vegas/4, and Starfield). This is deliberately a later idea, not current development scope. V1 remains focused on Skyrim Special Edition.

## Project direction

The immediate goal is to finish and polish the Skyrim experience, make the existing launch workflow dependable, and keep the project small and maintainable. After that, the plan is to publish a stable GitHub release and present Wyrd as a personal portfolio/resume project. A Nexus Mods release may follow as a low-pressure way to share it with the Skyrim community; broader adoption is welcome, but not a requirement for the project to be worthwhile.

## Known limitations and troubleshooting

See [Known limitations and troubleshooting](docs/TROUBLESHOOTING.md). In particular, Wyrd depends on Steam's local library metadata, a usable Proton runtime, and a correctly configured MO2 Skyrim instance.

## Development and verification

The Qt application is in `qt-ui/`. The source test/validation workflow is `.github/workflows/ci.yml`; the standalone build and release workflow is `.github/workflows/standalone.yml`.

Run the local validation suite from the repository root:

```bash
python3 -m pip install -r qt-ui/requirements.txt
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s qt-ui/tests -p 'test_*.py'
python3 -m compileall -q qt-ui
bash -n qt-ui/run.sh
bash -n qt-ui/install-desktop-launcher.sh
```

## License

See [LICENSE](LICENSE).
