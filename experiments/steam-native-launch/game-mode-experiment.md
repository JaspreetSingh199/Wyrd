# Wyrd Experimental Game Mode preflight

This is a read-only inspection step. It does not create directories, initialize prefixes, create symlinks, or alter Steam configuration.

The preflight intentionally does not enable preparation until the MO2 data directory mapping is verified against STL's source and the installed Wyrd MO2 layout.

## Run locally

```bash
PYTHONPATH=qt-ui python3 -m mo2_game_mode_preflight
```

A nonzero exit code (2) means blockers remain. This is expected until the source/target mapping is verified.

## Findings and safety

- Skyrim detection is read-only.
- An initialized AppID 489830 prefix is checked; a missing prefix is never created.
- Proton discovery only inspects installed candidates.
- MO2 must be a verified Wyrd-managed installation; external installations are not adopted.
- Candidate paths are reported but not modified.
- A saved game path without a confirmed Steam library root is not used to guess compatdata.
- Existing directories and foreign symlinks are blockers.
- The report always states that filesystem changes were not made.

## Safety tests

```bash
PYTHONPATH=qt-ui python3 -m unittest discover -s qt-ui/tests -p 'test_mo2_game_mode_links.py' -v
PYTHONPATH=qt-ui python3 -m unittest discover -s qt-ui/tests -p 'test_mo2_game_mode_preflight.py' -v
```

## Current restriction

The candidate MO2 data source and destination remain unapproved. Do not run the old direct Proton launcher against the real Skyrim prefix until this mapping and the separate preparation/rollback path have been reviewed.
