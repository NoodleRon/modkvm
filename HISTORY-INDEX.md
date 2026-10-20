# modkvm lineage and retained history index

Current product/source authority: modkvm at `~/Pontic/mk`. Digit -> PortaBody -> modkvm is one user-confirmed product lineage; Portabotty names and entry points remain compatible. The existing modkvm successor owner remains sole product owner.

## Current component location

The user moved the retained Digit folder into this project. Its verified physical location is [Digit](Digit/README.md), `~/Pontic/mk/Digit`. The former `~/Pontic/Digit` location is absent. `Digit` is the retained directory spelling; lowercase `digit` resolves to the same entry on this filesystem, not a second component copy. No folder was moved, renamed, copied or recreated during this reconciliation.

[Current location/provenance map](Digit/CURRENT-LOCATION.json) translates old absolute path prefixes without editing pinned historical receipts. Original producer/copyright provenance remains explicit; product ownership does not establish original authorship.

## Retained source and qualification history

| Evidence/component | Current link | State |
| --- | --- | --- |
| Original product documentation | [README](README.md), [architecture](ARCHITECTURE.md) | Existing source, unchanged by relocation reconciliation |
| Digit recovery and archive provenance | [Recovery record](Digit/recovery/RECOVERY.json) | Three archive hashes and81extracted source pins still match |
| Digit simulator0.2.0 | [Recovered source](Digit/recovery/source/simulator-0.2.0/digit/README.md) | Retained historical component; no actual observation/input adapter implied |
| Simulator offline qualification | [Handoff](Digit/candidates/simulator-offline-0.1.0/HANDOFF.json), [primary review](Digit/candidates/simulator-offline-0.1.0/PORTFOLIO-REVIEW.json) |119completed fake checks; synthetic qualification only, not rerun here |
| Windows BLE0.1.0 | [Source and limits](Digit/recovery/source/windows-ble-0.1.0/digit-windows-ble/README.md) | Native/package/profile/device qualification remains incomplete |
| Mac Classic probe | [Source and limits](Digit/recovery/source/mac-classic-NOT-working-probe/digit-classic-macos/README.md) | Explicitly NOT a working Bluetooth keyboard; relocation does not revive it |
| Active camera/controller candidate | [Qualification](docs/REMOTE-CAMERA-QUALIFICATION.md) | Source build0d144cf3151e; prior70synthetic checks; unchanged and not rerun |
| Reviewed geometry/caller source | [Source-clear receipt](../.modkvm-camera-review-20261019/geometry-source-proposal-0.1.0/revision-0.3.0/review/INDEPENDENT-COMPATIBILITY-REVIEW-0.3.0.json) |28completed focused checks; source-clear reviewed delta |
| Versioned source stage | [Staging receipt](../.modkvm-camera-review-20261019/geometry-source-proposal-0.1.0/staged-geometry-0.3.0/STAGING.json), [current path overlay](../.modkvm-camera-review-20261019/geometry-source-proposal-0.1.0/staged-geometry-0.3.0/CURRENT-PATHS.json) | Build4fafef729eb1; frozen snapshot, staged only and not selected for active source/runtime |

The move is a location change for retained component/history files. It does not integrate Digit, adopt the staged source, select a native profile, start a process, establish a Linux deployment/route, grant device permissions or activate camera/input. Future source staging must use an explicitly reviewed source closure; do not recursively distribute the newly nested recovery archives or helper material merely because they are under mk.
