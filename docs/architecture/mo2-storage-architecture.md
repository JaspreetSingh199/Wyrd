# Wyrd MO2 storage and installation architecture

Status: architecture decision record (ADR), design only. No migration or path rewriting is implemented by this document.

## Goals

Wyrd will manage multiple independent MO2 installations and instances while keeping game runtimes separate. A user should be able to register an existing installation, associate an MO2 instance with a game explicitly, and inspect current storage paths without changing them. Future storage pooling must be explicit, reversible, and MO2-compatible.

## MO2 behavior this design relies on

MO2 distinguishes global instances from portable instances. Global instances are discovered under the global instances root (commonly `%LOCALAPPDATA%/ModOrganizer/<instance-name>`) and one MO2 installation can manage several of them. A portable instance uses the installation directory for its instance INI and related critical files. Both layouts retain an instance configuration file.

`ModOrganizer.ini` path settings are instance-scoped. The documented Paths settings include Base Directory, Downloads, Mods, Caches, Profiles, and Overwrite. `%BASE_DIR%` is a substitution variable for those settings, not a storage root that MO2 itself owns. These configured directories may be on separate volumes. Changing an entry changes the configured path; it does not relocate the data.

Consequences:
- Never infer sharing from equal display names or from `%BASE_DIR%`.
- Never point two live instances at a shared Overwrite directory.
- Profiles and each profile's enabled-mod list/load order remain instance/profile state. Do not share a Profiles directory by default.
- A shared Mods pool is a deliberate shared repository. Verify compatibility with MO2's mod metadata, profile references, and deployment/USVFS behavior before enabling it.
- Downloads may be shared as a cache/repository when deliberately configured. Their contents and metadata must be preserved.
- Categories are not simply a single universal text file in every version/layout. Treat category definitions and Nexus/category mappings as versioned metadata. Inspect the actual installation/version before deciding whether a given categories.dat (or replacement format) is global or instance-scoped.
- MO2's global-instance discovery root and its installation-specific binaries/plugins are distinct. A registered installation must point to an executable and runtime context; an instance must separately point to its INI/data path.

## Proposed Wyrd-owned layout

```text
$XDG_DATA_HOME/Wyrd/mo2/
  installations/
    <installation-id>/
      installation.json       # Wyrd metadata only; MO2 binaries remain in their own root
  shared/
    mods/                     # optional shared mod repository; only by explicit choice
    downloads/                # optional shared download cache/repository
    metadata/
      categories/              # versioned metadata snapshots/imports, not blindly injected
  instances/
    <instance-id>/
      instance.json           # Wyrd association and storage policy
  backups/
    <operation-id>/            # future migration snapshots/manifests
$XDG_CONFIG_HOME/Wyrd/mo2/
  registry.json                # installations, instances, game/runtime associations
```

The user's proposed `global/profiles` is intentionally not a shared global profiles directory. Each MO2 global instance should retain its own Profiles path unless a future MO2-source audit proves a supported and safe sharing mechanism. Each instance also has its own Overwrite path. A storage-pool concept may share Mods and Downloads independently.

Existing MO2 paths, including external installs, are authoritative until the user explicitly changes them. Wyrd's new registry starts empty and never auto-adopts or rewrites existing INIs.

## Registry model (illustrative)

Installation record:
- stable Wyrd installation ID
- canonical and lexical executable/install paths
- owner: `wyrd` or `external` (external installations remain read-only to Wyrd unless a later, explicit feature authorizes a particular action)
- MO2 version/build and architecture
- Proton/runtime association and prefix path, if applicable
- discovered instance IDs

Instance record:
- stable Wyrd instance ID
- installation ID
- instance type: `global` or `portable`
- instance directory and INI path
- game ID and resolved game path (explicit association)
- runtime ID / Proton prefix association (per game/runtime)
- observed storage paths and status for base, mods, downloads, caches, profiles, overwrite
- intended storage policy: local/shared per supported directory
- last inspection timestamp and source

Game/runtime record:
- game ID, Steam AppID where applicable, canonical game path
- runtime ID and Proton build path
- Proton prefix path
- never shared across different game AppIDs merely because they use one MO2 installation

No path in this registry grants ownership. Ownership comes from validated Wyrd markers and a known Wyrd-created resource record; external remains external.

## Registration and discovery

1. Discover only bounded, known MO2 installation and instance locations. For global instances, enumerate direct child directories of known Wine `AppData/Local/ModOrganizer` and `AppData/Roaming/ModOrganizer` roots. For portable instances, inspect a supplied installation directory for its INI.
2. Parse INI files read-only, with interpolation disabled and robust Qt/QSettings value normalization.
3. Resolve `%BASE_DIR%`, relative paths, and Windows drive mappings without traversing arbitrary disks. Record unresolved, missing, symlinked, outside-prefix, and ambiguous paths distinctly.
4. Inspect all five path settings: `mod_directory`, `download_directory`, `cache_directory`, `profiles_directory`, and `overwrite_directory`, plus `base_directory`. Existing Wyrd inspection currently records only base, profiles, mods and downloads; cache and overwrite are a known gap.
5. Associate a game only when MO2's game name/path and validated executable evidence agree with a user-selected/detected game. A game name alone is insufficient.
6. Register each installation separately. One installation may own/manage multiple global instances; different installations may point to the same game only when the user deliberately configures it.
7. Registration is metadata-only and does not create or update the MO2 INI.

## Storage policy

- Profiles: instance-local by default. Contains profile lists, enabled mods/order, plugin order, profile INIs, saves configuration and other profile-level state.
- Mods: instance-local by default; optional shared pool only after testing MO2's exact shared-mod behavior and data ownership. Avoid simultaneous destructive writes from multiple MO2 processes.
- Downloads: instance-local by default; optional shared cache/pool. Do not deduplicate, rename or clean files during registration.
- Categories/metadata: keep per-install/per-version snapshots and compatibility metadata. Any synchronization is a separate reviewed action with backups and merge semantics.
- Overwrite: always instance-local. It contains generated output that must not bleed between profiles/instances.
- Caches/logs/crash dumps: installation or instance local as MO2 requires; not treated as user mod storage.
- MO2 binaries/plugins: installation-specific. Do not share or symlink executable/plugin trees between installations.
- Proton prefix: per game/runtime (Steam AppID 489830 remains Skyrim's prefix). Never move an MO2 instance across prefixes without an explicit plan and test; a prefix may contain unrelated Steam/Wine state.

## Read-only inspection and migration preview

The first implementation phase may add a read-only storage inventory and preview UI only. It must:
- report the exact INI and each configured storage path;
- report effective resolved paths and resolution method;
- show missing/unreadable/symlink/external paths as warnings, not silently normalize them;
- calculate file counts and byte totals only after explicit scan scope approval, without following symlinks;
- identify overlap/shared paths between instances;
- identify installation ownership and game/runtime associations separately;
- show a proposed target layout and estimated copy size without writing anything;
- leave INI bytes, directories, permissions, timestamps and files unchanged.

## Future migration contract (not part of this ADR implementation)

Migration is opt-in per instance and per storage category. Require a preview and a second explicit confirmation. Before execution:
- close the corresponding MO2 processes and verify no game/tool process uses the instance;
- create a durable, verifiable backup/manifest;
- copy (never move as first operation), verify hashes/counts/sizes, then stage the intended INI change;
- preserve the original INI and original files until user confirms the migrated instance works;
- provide rollback that restores the original INI and storage paths;
- never clean up source data automatically;
- refuse ambiguous paths, symlink traversal, cross-instance collisions, insufficient space, or external targets without an explicit ownership decision.

## Current Wyrd implementation gaps

- Registry for multiple installations/instances does not exist.
- Storage inspection currently exposes base, profiles, mods and downloads; cache and overwrite are omitted.
- INI inspection is read-only, but containment verification currently treats profiles under the install/prefix as verified; global external storage roots need a distinct safe classification.
- Instance roots are bounded to known Wine user directories and a supplied install root, which is a good discovery baseline.
- The Wyrd single-install managed path and isolated legacy prefix must remain unchanged while the new registry is introduced.
- External MO2 discovery/launch must remain explicitly external and must not be relabeled Wyrd-owned.

## Acceptance criteria for the first implementation

1. New ADR committed before any path/registry implementation.
2. Unit tests for global and portable instances, multiple installations, multiple games, QSettings byte-array values, relative/absolute/Windows paths, missing paths, symlinks, shared-path collisions and external ownership.
3. A read-only inventory command/API returning every relevant storage category and explicit verification status.
4. Registry load/save validation tests, atomic writes, schema versioning, duplicate-ID/path detection and no implicit adoption.
5. Qt GUI inventory/preview tests under PySide6; GUI tests must execute (not skip) in the CI environment.
6. No data migration, directory creation, INI rewrite, or prefix change in this phase.


## Read-only inventory API refinement

The first implementation exposes \`inspect_mo2_storage()\`, a read-only view over known instance INIs. It reports Base, Mods, Downloads, Cache, Profiles, and Overwrite independently; distinguishes Wine C: paths outside the supplied prefix, symlinks, missing directories, and external paths; and identifies directory aliases referenced by multiple instances. It does not walk storage trees, calculate sizes, create directories, change INI values, or change ownership. Migration preview UI remains future work.
