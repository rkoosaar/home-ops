# kopiur

Volume backups for this cluster. Every app that owns a PersistentVolumeClaim includes one or more
of the components here, so its claim is snapshotted to object storage on a schedule, verified, and
restorable from a `Restore` CR.

- `backup/` — the `SnapshotPolicy` (the recipe) and `SnapshotSchedule` (the cron). Included by every
  backed-up app.
- `restore/` — the claim plus its `Restore`. Included only while an app is being rebuilt from a
  backup; it creates the PVC at its declared size and hydrates it from the newest snapshot.
- `secret/` — the per-namespace copy of the repository credential that movers read.

The operator is installed from `kubernetes/apps/kopiur-system/`, and the repository is the
cluster-scoped `ClusterRepository/garage`. An app must never include `backup` and `restore` at the
same time: they render the same objects.

Per-app sizing and mover identity come from the `KOPIUR_*` substitutions each app's `ks.yaml`
passes in: `KOPIUR_CAPACITY` (the claim size a restore creates), `KOPIUR_PUID` / `KOPIUR_PGID`
(which identity the mover reads and writes as), `KOPIUR_RUNASNONROOT` and `KOPIUR_SNAPSHOTCLASS`.
The mover identity must match the **data's** owner, not the workload's declared `runAsUser` — the
staged source is mounted read-only, so an `fsGroup` cannot rescue a mismatch. A root-owned volume
needs `runAsUser: "0"` plus the `kopiur.home-operations.com/privileged-movers: "true"` annotation
on its namespace.

Policies live in the namespace that owns the workload: `default` holds the bulk of them, with
`llm`, `matrix` and `security` alongside. The CLI is not all-namespaces aware here, so any command
below taking a namespace is run once per namespace.

## The CLI

Pinned in `.mise.toml` as `github:home-operations/kopiur`, kept matching the operator's chart
version. mise shims a tool under the *tool* name, so the binary is `kopiur` and `kubectl kopiur`
fails with "unknown command". For the plugin spelling, symlink
`~/.local/share/mise/installs/github-home-operations-kopiur/<version>/kopiur` into
`~/.local/bin/kubectl-kopiur`.

Commands here avoid `$` so they survive shells and paste paths that strip it.

## Is everything backing up?

```
kopiur status -n default
kopiur doctor -n default
```

`status` is one screen: repository phase, every policy's `LAST-SNAPSHOT` age, each schedule's
`LAST-FIRE` / `NEXT-FIRE` / `FAILURES`, in-flight runs, and a STALLED list. This is the view for
"did the tick fire?"

`doctor` checks the CRDs, controller, webhook (with a live admission probe), repositories,
credentials, blocked work and recent failures, and exits non-zero on any failure, so it is
scriptable. One warning is normal at this scale:

```
warn  recent warning events: N MissingDependency default/<app>-<ts>: waiting for VolumeSnapshot ...
```

That is the CSI staging handshake — roughly four to five events per policy per hourly run. Each
message carries its own deadline ("staging fails at … if still not ready"), which is what separates
a transient that then succeeded from a stuck one, and `no recent failed snapshots/restores` on the
same screen is what says none crossed it.

## Is one app backed up, and when?

```
kopiur snapshots list -n default
kubectl get snapshotpolicies -n default -o custom-columns=NAME:.metadata.name,LAST-VERIFIED:.status.lastVerified
kubectl -n default get snapshots -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,SIZE:.status.sizeBytes --sort-by=.metadata.creationTimestamp | tail -12
```

`origin: discovered` means the repository holds a snapshot whose CR is gone, which is not a missing
backup — deleting a Snapshot CR leaves the kopia snapshot in place, and the catalog rediscovers it.
Check the repository view before calling any gap a lost backup.

## Taking a backup now

```
kopiur snapshot now --policy plex -n default --wait
```

Before anything risky — an upgrade, a chart bump, a restore drill — anchor a known-good state that
retention cannot prune. A scheduled snapshot captures whatever the volume holds *now*, which after a
bad change is the bad state:

```
kopiur snapshot now --policy plex -n default --name plex-milestone-20261013 --tag reason=before-upgrade --pin --deletion-policy retain --wait --logs
```

`--pin` exempts it from the retention window and `--deletion-policy retain` keeps the kopia snapshot
if the CR is later deleted. Pinned copies are exempt forever, so release them once superseded:

```
kubectl -n default patch snapshot plex-milestone-20261013 --type merge -p '{"spec":{"pin":false}}'
```

## When something failed

```
kubectl -n default get snapshots -o jsonpath='{range .items[?(@.status.phase=="Failed")]}{.metadata.name}{": "}{.status.failure.message}{"\n"}{end}'
kubectl -n kopiur-system logs deploy/kopiur-controller --since=6h | grep -viE 'class="transient"' | grep -iE "failed terminally|PermissionDenied|SnapshotFailed|CredentialsUnavailable|MoverPermitted"
kubectl -n default delete snapshot <name>
```

The first prints each failed snapshot's own reason. The second is the operator log with the normal
staging noise filtered out — empty output is healthy, and the Deployment is `kopiur-controller`
(`deploy/kopiur` does not exist). Every run logs its staging progress at WARN with
`class="transient"`, so a grep that does not exclude it reads a healthy fleet as broken.

A `Failed` snapshot has no kopia snapshot behind it, so its CR is inert — deleting it loses nothing
and clears the 24h failure count in `doctor`. Expect one per app whose hourly tick lands inside a
restore window, because the claim it was reading no longer exists.

## Restoring

**A whole volume.** Two changes per app: remove the `restore` component to prune the claim, then
re-add it so the populator hydrates a fresh claim from the newest snapshot. A PVC's `dataSourceRef`
is immutable, which is why one change cannot do both. The app is down in between, and it stays down
until it is scaled back up — a manual `scale deploy <app> --replicas=0` is not something Flux
reverts. Verify the result on two axes: the file count against the snapshot the `Restore` actually
used (read `status.conditions[SecurityContextInherited].message` first — it names it), and
ownership via `ls -ln`, because a restore runs as the identity recorded on the backup and a
count-perfect tree stamped with the wrong uid leaves an app unable to write its own data.

**A single file.** `kopiur ls`, `cat`, `download` and `browse` do not work for the app namespaces
here: the browse session pod runs in the snapshot's namespace and loads the repository credential
itself, so it refuses when the secret lives in `kopiur-system`. Restore into a scratch claim
instead, then mount it read-only beside the app's own claim in one pod and copy from inside it — no
laptop round trip, and the app's volume is untouched until the copy:

```
kopiur restore --from-snapshot SNAPSHOT-NAME --create-pvc scratch-restore --size 5Gi --storage-class ceph-block -n default --wait
```

`--local` is the other route: a local kopia binary with the repository credentials and network to
the S3 endpoint.

## Repository and retention

```
kubectl get clusterrepository garage -o jsonpath='{.spec.deletionProtection}' ; echo
kopiur maintenance run --repository garage -n kopiur-system --full --wait
kubectl get clusterrepository garage -o jsonpath='{.status}' ; echo
```

`deletionProtection` is the only thing between a retention typo and a mass delete, so read it
before changing any retention window. The maintenance run is a manual GC and compaction; the
operator also schedules one.

## Verification

Every policy carries `spec.verification`, so restorability is checked rather than assumed. Both
tiers nest their cron under `schedule:`:

- `quick` — `kopia snapshot verify`, blob and index level, daily (`H 3 * * *`).
- `deep` — restores the newest snapshot into a throwaway volume, checks it and discards it,
  monthly (`H 5 1 * *`). It carries `capacity` and `storageClassName` for that scratch volume; a
  `deep` run with no `capacity` falls back to a node-local `emptyDir`, which is the wrong place to
  restore a large volume. `deep` runs on one repository are serialised, one at a time.

`verifyFilesPercent` decides how much of each file `quick` reads fully (kopia's own default of 0
verifies metadata and blobs but reads no file contents), and `successExpr` —
`stats.files > 0 && stats.errors == 0` — fails a verify that reported zero files, which is what a
backup of an empty or unmounted volume looks like. Results land in `status.lastVerified`, the
oldest timestamp across the policy's repositories, plus a `kopiur_snapshot_verified_timestamp`
metric.

Two things to know when reading it:

- **`<none>` for `LAST-VERIFIED` is the unset rendering, not a fault.** Verification is gated: no
  verify Job is created until a policy has one successful backup, and the operator catches up
  promptly once that is true.
- **Helm never upgrades a chart's `crds/` directory**, so an installed CRD can predate a field the
  operator ships — and the apiserver then prunes the unknown field silently: the apply succeeds,
  the object looks correct, and nothing verifies. Check before trusting a new field:

```
kubectl get crd snapshotpolicies.kopiur.home-operations.com -o yaml | grep -c verification
```

A non-zero count means the field is present. The fix is a CRD refresh (`kubectl apply
--server-side -f deploy/crds/` from the operator repository) or a chart bump, which the
cluster-apps patch applies with `install/upgrade crds: CreateReplace`.

## Alerts

The chart ships around a dozen rules, with `backupStaleAfterSeconds` overridden to 4h for this
hourly fleet (the default of 48h is sized for a nightly one). `KopiurBackupStale` is the one that
matters most: it is the only alert covering a policy that goes **quiet**, since the failure-path
rules only fire on runs that happen and fail.

## Reading the cluster without the CLI

```
kubectl get snapshotpolicies -n default
kubectl get snapshotschedules -n default
kubectl get snapshots -n default --sort-by=.metadata.creationTimestamp | tail -8
kubectl get restores -n default
kubectl get maintenance -n kopiur-system
kubectl get pods -A --field-selector status.phase=Failed
```

That last one returning nothing is a fleet-wide all-clear for pod-level work. Note that a
`Completed` pod is usually a finished one-shot rather than a stuck workload: `<app>-vfy-d-<epoch>`
for a verification run, `<app>-<cronjob-id>-…` for a CronJob, and `rook-ceph-osd-prepare-*` which
linger by design. `kubectl -n <namespace> get deploy <app>` is what settles whether an app is
actually down — a name match in `kubectl get pods | grep <app>` proves nothing.
