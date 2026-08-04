# Production refactoring requirements

This document is the concise implementation contract for the staged production
refactor. The detailed rationale and validation program remain in
`BACKUP_HARD_PRODUCTION_REFACTORING_PLAN.md` and `AGENTS.md`.

## Safety

- Sources are read-only. Android transport may list, inspect, and stream only.
- Existing backups, reports, and unrelated destination files are immutable.
- A destination is writable only when its managed run marker proves ownership.
- Planning is locked before copying; resume never recalculates mappings.
- SQLite is authoritative and commits successful state after every file.
- Copy and hashing use bounded memory and application-owned temporary files.
- Verification independently checks the requested source and destination state.

## Android media layout

Media destinations use `media/<origin>/<media-type>/<YYYY_MM>/<file>`, where
media type is `images`, `videos`, or `audio`. A group of at most 100 files has no
part directory. A larger group places every file in deterministic `part_NNN`
directories of at most 100 entries. Original paths and classification evidence
remain in the locked plan and manifest.

Documents use `documents/<normalized-extension>/<file>` without part folders.
All destination names are deterministically encoded for Windows and collisions
are resolved case-insensitively after Unicode normalization.

## Snapshot and verification

Every Android snapshot contains `.backup-hard/run.json`, `manifest.sqlite`,
locked policy and plan JSON, and their SHA-256 digests. Existing unmarked trees
are refused. Verification levels are `metadata`, `hash`, and `full`; full mode
freshly checks source inventory, source hashes, destination hashes, mapping
completeness, scan errors, and policy/plan digests.
