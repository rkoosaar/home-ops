# kopiur migration — remaining work

Status as of 2026-09-22. **Migration proper is done**: all 26 deployed VolSync apps
have kopiur policies, each one verified by a restore-and-count drill before its
`ReplicationSource` was paused; `kopiur doctor` reports 0 failed checks. What
follows is the work between here and VolSync being deleted, then optional hardening.

Tracking: the steps below mirror a live todo list. Phase 1 is manifest-only (safe);
Phase 2 onwards touches live PVCs.

---

## Phase 1 — sizing and DR prep (manifest work, no cluster risk) — **COMPLETE**
Merged as #2519 (`98561f20`), #2520 (`1af66472`), #2521 (`b27ba2e7`).

### 1. `KOPIUR_CAPACITY` where the data demands it
`components/kopiur/backup/pvc.yaml` defaults to `${KOPIUR_CAPACITY:=5Gi}` and no app
sets it, so today every DR PVC would be 5Gi. Measured data says only plex exceeds
that (13.35 GiB); minecraft is worth headroom (1.77 GB, growing worlds).
- `plex/ks.yaml` → `KOPIUR_CAPACITY: 30Gi`
- `minecraft/ks.yaml` → `KOPIUR_CAPACITY: 10Gi`
Both are below today's claims (50Gi / 16Gi), so a DR recreates those volumes
smaller — deliberate, since the data fits with headroom and ceph expansion is
grow-only. PR #2519 — **merged `98561f20`**.
Mirrors upstream (onedr0p: plex 50Gi, radarr 10Gi; ~17 of his apps keep the 5Gi
default). Alternative, if you'd rather DR volumes match today's claims exactly: set
`KOPIUR_CAPACITY` = `VOLSYNC_CAPACITY` for all 26. Nothing consumes the variable
until the DR half is enabled, so this is a no-op today.

### 2. `inheritSecurityContextFrom.snapshot` on the component's `restore.yaml`
A DR restore otherwise stamps `65532` and leaves kanidm (999) unable to write its
own database. `mover.inheritSecurityContextFrom.snapshot: {}` makes the restore run
as the identity recorded on the backup. Keep a comment: upstream has no mover block
at all because every one of his apps runs as 1000 — ours doesn't.
Written as PR #2520 — **merged `1af66472`**. `inheritSecurityContextFrom.snapshot: {}`
added alongside `podSecurityContext.fsGroup`, plus a note that the privileged-mover
gate still applies to a recorded root identity (tunarr). Verified by scratch render +
validation against the published Restore schema.

Why upstream didn't need it (checked, not assumed): his `restore.yaml` has no mover
block, no `privilegedMode` and no `inheritSecurityContextFrom` — because all 20 of
his app pods declare `runAsUser/runAsGroup/fsGroup: 1000` + `fsGroupChangePolicy:
OnRootMismatch`, so the kubelet repairs a `65532`-stamped restore tree on first
mount. Ours is heterogeneous (fsGroup 100 ×19, 1000 ×10, 0 ×2, 977, 5050, 10000).

Decision on the apps whose HELMRELEASE values declare no securityContext (kanidm,
open-webui, kavita, bedrockconnect, firefly): **do not add fsGroup to them as part
of this work.** It would mutate a live volume (OnRootMismatch chgrps the existing
tree at the next mount) and it is strictly worse than the recorded identity, which
reproduces the original ownership rather than forcing one group onto everything.
Caveat that sentence needs: HelmRelease values are not the pod's effective spec —
kanidm's workload comes from a Kanidm CR (kaniop) that isn't in Git, and a chart can
set a securityContext its values don't mention. Settle it by reading the live pods
before claiming anything about them.

### 3. Split the DR half into its own component
`components/kopiur/backup` currently holds `snapshotpolicy` + `snapshotschedule` +
`pvc` + `restore`. Because it is one component, uncommenting `pvc.yaml` applies a
`dataSourceRef` change to *every* app's live PVC in one reconcile and is rejected as
immutable — 26 Kustomizations NotReady. Split into e.g. `components/kopiur/backup`
(policy + schedule) and `components/kopiur/restore` (pvc + restore), included per
app as its turn comes. Upstream didn't need this because his rollout was big-bang.
Done as PR #2521 — **merged `b27ba2e7`**: `backup` now carries policy + schedule only,
`restore/` carries the pair plus the two-apply cutover recipe, and no app includes it
yet (verified: `git grep components/kopiur/restore` in apps = 0).

---

## When each piece becomes load-bearing

- **`inheritSecurityContextFrom.snapshot` (#2520)** — inert until the DR half is
  enabled; first exercised at step 4. That drill must therefore assert OWNERSHIP and
  not just a file count: hydrated files come back owned by the app's own uid
  (kanidm 999, hermes 10000, tunarr root, 1000/100 elsewhere), never 65532. That is
  the only proof the recorded-identity path ran instead of silently falling back.
- **The component's `kustomization.yaml` comment** — the note #2520 adds is permanent;
  the comment above it (why `pvc.yaml`/`restore.yaml` are off, and the two-commit
  drill recipe) describes the migration state and goes stale the moment the DR half is
  enabled for the first app. Rewrite it then.
- **The backup-side mover pins (kanidm 999, hermes 10000, tunarr root)** — already
  live in the policies, nothing to schedule. But their justifying comments reference
  `VOLSYNC_PUID`/the outgoing component; reword during step 8, or they read as advice
  about a system that no longer exists.
- **The no-fsGroup question (kanidm, open-webui, kavita, bedrockconnect, firefly)** —
  parked deliberately. Settle it with the live-pod read before Phase 2, and note that
  the answer does not change any of the above: the recorded identity covers those apps
  whether or not their pods carry an fsGroup.

### 4. Real DR drill on one low-stakes app (bookboss or convertx) — **DONE 2026-09-22 (bookboss)**
PRs #2523 (detach volsync, `8e19b844`) and #2524 (attach kopiur/restore, `3eb66ce6`).
Result: claim rebuilt 5Gi and `Bound`, `Restore` `Completed`, 3/3 files, ownership
`1000:100` matching `status.recorded`, `du -sb` 7,024,840 vs 7,004,360 (drift upward
from live writes, not a short read). Pod needed scaling back up — zeroscaler only
reacts to traffic, it does not undo a manual `scale --replicas=0`.

Findings worth carrying to the other 25 apps:
- **Assert with `Restore.status.conditions[SecurityContextInherited]`**, which names the
  inherited identity *before* a file is written (`RecordedApplied: the mover inherited
  the identity recorded on Snapshot … and runs as uid 1000 (gid 100, fsGroup 100)`).
  That is stronger and cheaper than inspecting the restored tree afterwards.
- **The populator stages through a `prime-*` PVC** and cleans it up once the claim is
  satisfied (verified: no leftover). Expect a `Pending` claim with `PopulatingPrimePvc`
  while it works — that is progress, not a stall.
- Rollback is a single commit that removes `kopiur/restore` and re-adds `components/volsync`,
  because both render a PVC of the same name; including both is a duplicate resource id and
  kustomize rejects it.
- Two one-line commits per app, and the outage is the gap between them. For media-heavy
  apps the hydration time dominates, not the paperwork.
Per app, two commits:
1. remove `components/volsync` from that app's `ks.yaml` → prunes its VolSync PVC,
   `ReplicationSource`, `ReplicationDestination` and secret; app goes down.
2. add `components/kopiur/restore` → creates a fresh PVC (same name) owned by the
   populator, which hydrates it from the latest kopiur snapshot.
Then verify the app returns with its data — this is the first real recovery rather
than a count on a scratch volume. Check `Restore` status and the app's own view of
its files, not just that pods went Running.

### 5. Roll the swap across the fleet, in batches — **COMPLETE 2026-10-06**
**Final tally: 23 apps on kopiur (backup + restore) and 3 deliberately dropped.** By namespace:
19 `default` + 2 `llm` + 1 `matrix` + 1 `security`. Zero apps carry both components. Five
non-deployed apps still reference `components/volsync` (Phase 3 strips them).
Same recipe, watching `kopiur status` / `doctor` between batches; biggest last.
Batches of ~5 apps share one pair of commits (each app's swap is independent — the
PVC names differ) plus ONE verification pod that mounts all of the batch's claims
read-only while the batch is still scaled down. The 0-file apps (bedrockconnect,
firefly) get a MOUNT CHECK before any swap: 0 files is the pgadmin shape, a claim the
workload never mounts, and swapping a claim nothing uses is theatre. Not deployed and
never to be swapped: calibre, calibre-web, calibre-web-automated, dispatcharr, jellyseerr.

**Roster** (sizes are the migration drills' verified stats; UNMEASURED means read the
newest snapshot's `stats` before sizing):
- **A — DONE:** bookboss 3 files / 7 MB — #2523 + #2524, `3eb66ce6`
- **B — DONE (2026-09-23):** seerr 20 / 5.24 MB · convertx 3 / 3.29 MB · bazarr 19 / 0.90 MB ·
  sabnzbd 8 / 1.42 MB · prowlarr 627 / 18.83 MB — #2526 + #2527, `2d8d429b`. All five claims
  Bound in 56 s, `RecordedApplied True` on each, all five back `1/1 Running`, 0 restarts.
  Ownership `1000:100` as recorded on convertx/bazarr/sabnzbd/prowlarr; **seerr came back
  `1000:1000`** because its workload declares `runAsGroup/fsGroup: 1000` against a recorded
  gid of 100, so the kubelet chgrp'd the fresh volume on first mount. Benign (seerr runs as
  1000:1000 and owns its files) and the live demonstration of the masking rule — read the
  app's HelmRelease `fsGroup` against `status.recorded.gid` to predict it.
- **C — DONE (2026-09-24):** tautulli 235 / 98 MB · ersatztv 234 / 107 MB · pinchflat 717 / 121 MB ·
  neko 166 / 115 MB · recyclarr 5809 / 155 MB — #2532 + #2533, `5e30d277`. Counts exact on all
  five from the single pre-scale-up check pod; all four Deployments `1/1 Running`, 0 restarts;
  recyclarr (a **CronJob**, not a Deployment) proved read AND write by completing a manual run
  against the restored claim.
  **The two-reading rule, now confirmed on five apps:** the check pod (before any workload
  mounts) reads the RECORDED identity — `1000:100` on every app — while the same volume read
  through the app's own pod shows `1000:1000` wherever the app declares `fsGroup: 1000`
  (neko, seerr) with `lost+found` at `0:1000`, and `1000:100` where the app's `fsGroup: 100`
  matches (tautulli, ersatztv, pinchflat). `fsGroup` changes the GROUP, never the uid, which is
  why a mismatch is cosmetic for an app whose uid owns the files.
- **D — DONE (2026-09-24):** kavita 485 / 177 MB · navidrome 1634 / 237 MB · tunarr 132 / 413 MB ·
  audiobookshelf 10904 / 558 MB (ns `default`) · kanidm 12 / 83 MB (ns `security`) — #2534 + #2535,
  `7cd94f3c`. Counts match the snapshot each restore actually used (kavita's 485 is its 08:29 tick,
  not the 07:29 one first referenced); ownership as predicted (`1000:100` ×3, `0:0` tunarr,
  `999:999` kanidm); all five back `1/1 Running`, 0 restarts.
  - **The privileged-mover gate never fired**: tunarr's Restore is `Completed` with NO
    `MoverPermitted` condition — the condition exists only to REFUSE — so #2508's annotation is
    now proven on a real deploy-or-restore, not just a one-shot Snapshot.
  - **kavita runs as root** (`id` = uid 0) so it needs no fsGroup; its write test passed and new
    files land as `0:100` because the restored directories keep their setgid bit. That is the
    parked fsGroup question answered by the app that prompted it.
  - Unresolved footnote: navidrome restored 1634 files exactly while `du -sb` (278,739,069) exceeds
    the snapshot's `sizeBytes` (236,681,341) by 18%. Every hourly snapshot reports the same total
    and the count is exact, so it reads as a stats-vs-tree accounting difference, not data loss.
- **E1 — DONE (2026-09-24):** sonarr 2672 / 834 MB · radarr 3321 / 1.12 GB (ns `default`) ·
  hermes 4622 / 630 MB (ns `llm`, mover **10000**) · open-webui 28 / 33 MB (ns `llm`) —
  #2536 + #2537, `7fac2d2a`. Counts exact on all four; ownership confirmed in BOTH readings:
  sonarr `1000:1000` in its own pod (third instance of the fsGroup shape), radarr `1000:100`
  in both, hermes `10000:10000` in both (first non-1000/root/999 pin through a swap),
  open-webui `1000:100`.
  - **open-webui runs as root** (`id` = uid 0), like kavita — both apps that declare no
    securityContext at all are root, so neither needs a fix. It restarted once shortly after
    coming up; Running since.
  - **The frozen-volume theory is refuted**: open-webui is as frozen as navidrome (byte-identical
    snapshots since the 21st) and its restored `du` matched `sizeBytes` within 0.2%, so navidrome's
    18% gap is navidrome-specific and stays open (P4.22).
- **E2 — DONE (2026-09-24):** tuwunel 218 / 97.5 MB (ns `matrix`, the Matrix homeserver) —
  #2538 + #2539, `286a0c2f`. Run as a pre-written blind runbook because the drill drops the
  agent's own comms; the outage was the paste time plus ~60 s of hydration. 5Gi target with no
  `KOPIUR_CAPACITY` override (93 MB of data), and `drill/tuwunel-rollback` pre-staged holding the
  pre-drill config — never needed. **Acceptance test was this conversation reconnecting** through
  the restored database. Outstanding: the `matrix` count/ownership check (run it from inside the
  app pod, since the claim is RWO-attached to its node now).
- **F — DONE (2026-10-05):** minecraft 1978 / 1.785 GB (1663 files / 1.786 GB at the hydrate
  point) — #2578 + #2579, `8aba523b`. Claim rebuilt at **10Gi**, `Restore Completed` in **65 s**
  for 1.77 GB (~27 MB/s — the calibration plex's estimate now rests on). Ran as the inverted
  ordering the app demands: scale down to force the SIGTERM world save, *then* snapshot. The
  16:15 hourly and the 16:21 manual snapshot differ by 516 bytes, which is the evidence that the
  save had nothing material left to flush. Ownership `1000:1000` in-app as predicted.
  **Acceptance was the app's own log line**: `Done (3.41s)!` with BlueMap bound and Geyser
  started — world, configs, mods and libraries all restored. Counts drift upward (1978 vs 1969)
  because a live server writes immediately; the same reason tuwunel's check was not
  byte-exact.
  - Lesson for plex: same ordering. Its SQLite libraries are written continuously, so
    scale-down-then-snapshot beats snapshotting a live media database.
- **G — DONE (2026-10-06):** plex 88,808 files / 14.59 GB (`du`) — #2582 + #2583, `edb1b948`.
  Claim rebuilt at **30Gi**; ownership `1000:100` in BOTH readings (the agreeing case: the
  workload's `fsGroup: 100` equals the recorded gid, so nothing chgrp'd). File count EXACT
  against the snapshot the restore used.
  - **The restore used `plex-20261006105718` (a scheduled tick), not the manual snapshot.** With
    `offset: 0` the newest wins, and the manual one had been taken while the pod was still
    `Terminating` — so it was the TORN one: it captured a **580 MB** SQLite WAL plus its
    `-wal`/`-shm` sidecars, while the 10:57 tick caught the checkpointed state (3 files fewer,
    580 MB smaller). The ordering was right; the manual snapshot was simply too early, and the
    scheduled tick rescued it.
  - **Byte reconciliation** (it looked 2% short at first): `du -sb` 14,588,593,794 = the
    snapshot's 14,310,241,813 of file content + 278 MB of DIRECTORY entries (67,415 dirs x
    ~4 KiB). `du` counts directories, kopia's `sizeBytes` counts file content only.
  - **Calibration:** apply → `Completed` was ~40 min, of which only ~6 (11:02:02 → 11:07:57)
    were the copy itself (~40 MB/s for 15 GB across 89k files). The rest was the operator's own
    staging and reconcile cycle, and is unexplained rather than assumed.
  - Non-issue seen in the logs: `Critical: libusb_init failed` (Plex has no USB tuner passed
    through). Pre-existing and unrelated to the restore.
- **DROPPED rather than swapped (2026-10-06)** — each kept `zeroscaler`, pruned its claim along
  with the RS/RD/secret, and left a `volsync-src-<app>-cache` PVC for the Phase 3 sweep:
  - **bedrockconnect** (`#2585`): never mounted anything — its only persistence entry is a
    ConfigMap. The PVC existed solely because the component rendered one.
  - **pgadmin** (`#2586`): claim shadowed by a chart-mounted emptyDir at `/var/lib/pgadmin`, so it
    held year-old rows no future pgadmin would mount.
  - **firefly** (`#2587`): its claim *is* mounted (`.../storage/upload`) but held 0 files in 170
    days. Accepted trade: a future attachment is not in any backup. Its real data lives in the
    CNPG cluster, protected independently by barman (2h base backups + continuous WAL + 30d).
- **Debris: swept** (2026-10-06) — `restore/drill`, `restore/tautulli-drill` and `matrix/drill-check`
  deleted; the `drill-*` Snapshot CRs had already gone in the earlier sessions.

---

## Phase 3 — retire VolSync

### 6. Remove the VolSync stack
Per app: drop the `components/volsync` include (already gone for swapped apps) and
the `${APP}-volsync-secret` ExternalSecret. Then the cluster pieces:
`apps/storage/volsync` (fork HelmRelease + `KopiaMaintenance` CronJob), the volsync
CRD entry in `bootstrap/helmfile.d/00-crds.yaml`, `apps/storage/volsync-old`, and
any Renovate entry for the fork chart.
Catches found while swapping: **kanidm's `ks.yaml` has `dependsOn: volsync` (ns
`storage`)** — that blocks its Kustomization once the volsync KS is gone, so drop the
dependency in the same commit. Also check every `dependsOn: volsync` across the
fleet (`grep -rn "name: volsync" kubernetes/apps`), not just kanidm's.

Two more catches from the fleet inventory (2026-10-06):

- **Five files still *include* `components/volsync`, all of them non-deployed** (calibre,
  calibre-web, calibre-web-automated, dispatcharr, jellyseerr). They sit commented out of the
  namespace kustomization and never build, and RK's call (2026-10-06) is to leave them alone:
  not deployed, not in scope. Expect this consequence and don't mistake it for breakage — after
  the component directory is deleted they point at a path that no longer exists, which is
  harmless while undeployed and a one-line fix if one is ever enabled. (`pgadmin` and `firefly`
  left this list when they were dropped in #2586/#2587.)
- **`templates/config/**` is a stale parallel tree that still lists `components/volsync` for
  every app**, including the 23 that no longer have it. It is invisible to the cluster, but a
  `task template:configure` regenerate would rebuild the rendered tree FROM it and undo the
  whole migration. Sync or update the templates before any regenerate — the same hazard the
  skill already records for editing the rendered tree.
- A plain `git grep components/volsync` over the repo is NOT an inventory of includes: after
  the detaches, 23 hits are the explanatory comment text. Count from the `components:` lists.

Also worth noting: a cache PVC per app persists independently of these manifests
(`volsync-src-<app>-cache`, controller-created) — see step 7.

### 7. Reclaim the orphaned cache PVCs
`volsync-src-<app>-cache` on `openebs-hostpath`, one per app that had a VolSync mover. Every
`ReplicationSource` in the fleet is paused and the kopiur movers do not use these volumes at all
(kopiur's mover cache is ephemeral by design), so all of them are unused and safe to delete —
kopia caches are disposable and re-fetch on demand.

The honest number: the sum of their REQUESTS is ~130Gi (plex's was 50Gi alone), but these are
local hostPath volumes whose size is largely a label, so the real reclaim is whatever kopia
actually wrote. Measure with `node_filesystem_avail_bytes` before and after, not by adding up
requests. Start with `kubectl get pvc -A | grep volsync-src`.

### 8. Rename `VOLSYNC_*` → `KOPIUR_*` per app
Mechanical, 26 files; fold into step 6 while each `ks.yaml` is being edited anyway.

### 9. Soak and confirm
**Retention PRUNES — verified 2026-10-05**: after ~11 days unattended, `kopiur snapshots list`
counts 31 snapshots for minecraft and 31 for convertx, inside the 24-hourly/7-daily band, so the
repo is not accumulating without bound. Remaining to confirm: the old NFS repository going quiet
(read-only history) and `deletionProtection` on the ClusterRepository, which is unset — read it
before any retention edit, since it is the only thing standing between a retention change and a
mass delete.

---

## Phase 4 — optional hardening (beyond the reference)

### 10. Enable backup verification
`SnapshotPolicy.spec.verification`: `quick` daily (cheap, blob-level) and optionally
`deep` (a real scratch restore, weekly, with `capacity` for the scratch PVC). All
policies currently show `LAST-VERIFIED: -`. Not used in onedr0p's repo.

### 11. Off-box replication
`RepositoryReplication` (blob mirror) to R2/B2. Also not upstream: his MinIO is on
the same NAS that Garage runs on, so neither of us has a second failure domain yet.

### 12. pgadmin's orphaned volume
Its chart mounts an emptyDir at `/var/lib/pgadmin`, so the PVC holds year-old data
and its backup preserves a dead volume. Either remove `components/volsync` +
`components/kopiur/backup` from it, or keep the paused config as documentation.

### 13. The skipped-tick investigation
convertx (17:57, 22:57, 00:57, 04:57) and open-webui's first four ticks produced no
snapshot at all — absent from the repository, not just the CR list. Settle it with
the scheduler's own `created scheduled Snapshot` log lines and, if it logged
creations that never materialised, report upstream. `KopiurBackupStale` now fires at
4h instead of the chart's 48h default, so the next occurrence announces itself.

---

## Constraints worth not re-deriving

- A PVC's spec is immutable except `volumeName` (once), `resources.requests.storage`
  (grow only) and `volumeAttributesClassName` — so a `dataSourceRef` swap requires
  deleting the claim, which is why Phase 2 is a deliberate drill and not a Flux edit.
- Pruning the VolSync PVC happens as part of step 1 of the drill; the populator PVC
  must be created in a separate apply afterwards, or the claim is gone with nothing
  to rehydrate it.
- Mover UID must match the **data's** owner, not the workload's declared
  `runAsUser`: kanidm 999 (also its `VOLSYNC_PUID`), hermes 10000, and tunarr root via
  the `privileged-movers` annotation on namespace `default`. pgadmin's own pins were
  removed for exactly this reason.
- Completeness gate for any future drill: `stats.filesFailed == 0` and
  `filesNew + filesModified + filesUnchanged` equal to the restored file count.
