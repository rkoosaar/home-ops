# Minecraft — KVSHS Forever Survival

Fabric survival server on [itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server) (image `ghcr.io/itzg/minecraft-server`, `TYPE: FABRIC`), deployed via the bjw-s app-template HelmRelease in this directory.

## Key facts

- **Namespace / names**: `default` — HelmRelease + Deployment both named `minecraft`, service `minecraft` (LB `10.1.1.47`, `mc.${SECRET_DOMAIN}`), bluemap web UI at `mcmap.${SECRET_DOMAIN}`
- **World**: PVC `minecraft`, mounted at `/data`; world folder is `/data/world` (`level.dat` + `region/`; nether = `DIM-1/region`, end = `DIM1/region`)
- **Seed is pinned** (`SEED: 844424960210770`) — chunk regeneration is fully deterministic
- **Mods**: pulled from Modrinth via `MODRINTH_PROJECTS` (fabric-api, spark, geyser, floodgate, bluemap, lithium, ferrite-core, anti-enderman-grief). `FORCE_REDOWLOAD: "true"` re-fetches mods on every start; `REMOVE_OLD_MODS: "true"` cleans stale builds
- **BlueMap**: data on PVC `minecraft-bluemap`; config comes from the `minecraft-bluemap` ConfigMap (read-only mount) + emptyDir for `/data/config/bluemap` — config changes are git changes + `/bluemap reload`
- **Access**: playit tunnel sidecar (`playit-secret`), Geyser/Floodgate for Bedrock clients

## Runbook: regenerate chunks after a worldgen update

New Minecraft versions/drops only generate new content (biomes, cave biomes, structures) in chunks created *after* the upgrade — existing chunks never change. To get new content in explored areas, delete those chunks so they regenerate with the current generator. Because the seed is pinned, regenerated chunks are deterministic and consistent with the rest of the world.

Example: Chaos Cubed (26.2) sulfur caves — a cave biome that only generates in 26.2+ chunks.

### Phase 0 — Pre-flight

1. Tell players the server is going down for maintenance.
2. Ensure a fresh backup exists (kopia/VolSync) — the world tar pulled out in Phase 2 also doubles as rollback.
3. Confirm the new server version is running cleanly *before* touching the world:
   `kubectl -n default logs deployment/minecraft --tail=40`
   (version line + `Done` / `Ready for connections`; check all mods loaded — bluemap is usually the last to get a build for a new MC version)

### Phase 1 — Suspend Flux + stop the server

```sh
flux suspend helmrelease minecraft -n default
kubectl -n default scale deployment minecraft --replicas=0
# no flux CLI? use:
# kubectl -n default annotate helmrelease minecraft helm.toolkit.fluxcd.io/reconcile=disabled --overwrite
```

Wait for the pod to terminate (`kubectl -n default get pods -w`).

### Phase 2 — Copy the world out

```yaml
# mc-helper.yaml — root helper pod mounting the same PVC
apiVersion: v1
kind: Pod
metadata:
  name: mc-helper
  namespace: default
spec:
  restartPolicy: Never
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: minecraft
  containers:
    - name: helper
      image: alpine
      command: ["/bin/sh", "-c", "sleep 86400"]
      volumeMounts:
        - name: data
          mountPath: /data
```

```sh
kubectl apply -f mc-helper.yaml
kubectl -n default wait --for=condition=Ready pod/mc-helper
kubectl -n default exec mc-helper -- sh -c "tar czf /tmp/world.tar.gz -C /data world"
kubectl -n default cp default/mc-helper:/tmp/world.tar.gz ./world.tar.gz
tar xzf world.tar.gz
# keep world.tar.gz as the rollback until everything is verified
# leave the helper pod running — it's reused for the upload
```

### Phase 3 — Select and delete chunks (MCA Selector)

Run on a desktop machine (Java 17+): <https://github.com/Querz/mcaselector/releases>

1. `File -> Open World` -> the extracted `world/` folder; load **Overworld only** (worldgen content like sulfur caves is overworld-only; nether/end stay untouched).
2. The view shows only chunks that exist in the region files — i.e. the explored area, chunk-accurate. Buildings are visible in the chunk render.
3. `Ctrl+A` selects all chunks. Switch the toolbar tool to **deselect** (minus icon) and drag over every chunk containing a build/farm/tunnel/anything you care about.
4. Check the selected-chunk count, then right-click -> `Delete selected chunks`, `File -> Save`.

Useful math: chunk coords = `floor(block / 16)`; `/locate biome <id>` (e.g. `sulfur_caves`) returns the nearest spot — it scans the biome source, so it works for already-generated and ungenerated chunks alike, and regeneration at that spot is deterministic. `y` in the locate output is elevation — for map purposes use x and z only.

### Phase 4 — Upload and restart

```sh
tar czf world-edited.tar.gz world
kubectl -n default cp ./world-edited.tar.gz default/mc-helper:/tmp/world-edited.tar.gz
kubectl -n default exec mc-helper -- sh -c "rm -rf /data/world && tar xzf /tmp/world-edited.tar.gz -C /data"
kubectl delete -f mc-helper.yaml
kubectl -n default scale deployment minecraft --replicas=1
flux resume helmrelease minecraft -n default
kubectl -n default logs -f deployment/minecraft
```

Only `/data/world` is replaced — mods/config in `/data/config` are untouched. Wait for `Done` / `Ready for connections`.

### Phase 5 — Verify + BlueMap

1. `/tp <x> <z>` to a reset area and confirm the new terrain; `/locate biome sulfur_caves` to re-check a spot.
2. BlueMap cannot render chunks that don't exist yet — reset areas stay blank/ghosted until the chunks are actually generated. **Visit the areas** (or pre-generate with Chunky, or `/forceload add <chunkX> <chunkZ>` on corner chunks), then:
   - `/bluemap update overworld` — targeted re-render of changed chunks (usually enough)
   - hard-refresh the browser (`Ctrl+Shift+R`) — the webapp caches tiles aggressively
   - if ghost tiles persist for chunks that *do* exist: `/bluemap purge overworld` — wipes ALL overworld data and re-renders everything (heavy; do it with nobody online; `/bluemap stop` / `/bluemap start` to pause/resume)
3. Delete `world.tar.gz` / `world-edited.tar.gz` locally only after everything checks out (or keep the original as a permanent pre-reset backup).

## Gotchas

- A chunk reset wipes the **entire 16x16x384 column** — chests, builds, everything. Deselect carefully.
- The surface renderer shows almost no difference after a reset for cave-only updates (e.g. Chaos Cubed barely touches surface generation). Underground verification requires digging in game or a BlueMap cave renderer (see BlueMap docs: "Explaining cave rendering"; it renders a horizontal slice at a chosen y — the `/locate` result's y is a good starting depth).
- After a server version bump, verify every mod in `MODRINTH_PROJECTS` has a build for the new version before merging the Renovate bump — a missing build can fail boot or silently skip the mod.
- BlueMap config is a ConfigMap: changes go through git + `/bluemap reload`.
