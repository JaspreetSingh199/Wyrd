# MO2 discovery and storage boundaries

## V1 decision

Wyrd is a read-only discovery and launch orchestrator for existing Mod Organizer 2 setups. It does not maintain a registry of installations or instances, register/adopt external installations, rename instances, assign storage policies, or manage shared storage.

The live filesystem inventory is the source of truth. Wyrd inspects only known MO2 roots and configured instance INIs; it does not recursively search arbitrary disks. An instance is available to the picker when its files and configuration are found and validated. No Wyrd metadata record is required.

## What Wyrd reads

For each discovered instance, Wyrd may inspect:

- Instance name and instance path
- ModOrganizer.ini and configured game/profile information
- Base, Mods, Downloads, Cache, Profiles and Overwrite paths
- Whether configured paths are present, missing, aliased or unsafe
- Skyrim SE launch targets configured by that instance

The inventory is refreshed from disk. It is not persisted as a second source of truth.

## What Wyrd changes

V1 changes only the configuration required to make the detected Skyrim installation discoverable and launchable through the selected Proton runtime. Any INI synchronization must remain narrowly scoped, validated, and backed by the existing explicit launch/setup workflow.

Wyrd does not edit MO2 storage paths, move/copy/merge mods or downloads, create shared storage, migrate profiles, or claim ownership of existing MO2 instances.

## Installation and removal boundary

The optional MO2 installer may create the dedicated Wyrd installation directory and its ownership marker. That marker authorizes removal of the verified Wyrd-installed **program directory only**.

Removing that program directory must preserve:

- Global and portable MO2 instances
- Profiles, mods, downloads, overwrite data and custom storage
- Skyrim's Steam compatdata/489830 prefix
- Skyrim game files and saves
- External MO2 installations

No registry metadata is consulted to expand the deletion boundary. If the program directory or its ownership marker cannot be verified, removal fails closed.

## Deferred work

The following are explicitly out of V1:

- Multiple-installation registration and adoption
- Wyrd-owned instance records or instance renaming
- Game/runtime association databases
- Storage policy editing or shared storage
- Storage migration, relocation, cleanup or deduplication
- General-purpose MO2 library management

These features should be reconsidered only when Wyrd has a concrete ownership model and a user-facing requirement. Until then, live filesystem discovery remains the only inventory authority.
