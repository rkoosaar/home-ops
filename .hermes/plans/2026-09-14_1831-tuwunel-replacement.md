# Replace Synapse + MAS + synapse-admin with Tuwunel

**Goal:** one Lean Matrix homeserver (Tuwunel) owning `matrix.${SECRET_DOMAIN}`,
with Element as the client and Kanidm as an optional SSO provider — no Synapse,
no MAS, no synapse-admin.

**Why:** the Synapse setup was configured but never used in anger (2 users, no
rooms worth keeping), so a fresh server is cheaper than maintaining delegation
(MAS as issuer + emulated Synapse admin API) for a two-person instance. Tuwunel
is a single Rust binary on RocksDB: no Postgres, no Redis, no signing key, and
its admin surface is an admin room instead of a separate web app.

**Verified facts** (from `matrix-construct/tuwunel` docs, 2026-09):

- No Synapse/Postgres import path exists — RocksDB databases from
  Conduit/conduwuit migrate in place; anything else is a fresh server.
- Tuwunel cannot be MAS-delegated. It runs its own OIDC issuer and takes MAS (or
  any OIDC provider, including Kanidm directly) as an *upstream* IdP.
- `login_with_password` defaults true and providers are additive, so password
  accounts and SSO accounts coexist per user.
- First user created via `tuwunel --execute "users create_user <name> [password]"`
  is automatically an admin.
- `/_synapse/admin` is implemented (~69/100 endpoints, all room ones) purely so
  third-party admin tools work; there is no native admin API or web UI.
- Defaults that must be overridden here: `address` binds loopback only;
  `allow_federation` defaults true; `db_pool_max_workers` defaults 2048 and can
  exceed the container task limit (startup dies with `EAGAIN`).
- K8s: `terminationGracePeriodSeconds` must exceed the longest DB migration, and
  a *startup* probe with a large budget is required — a liveness probe with
  default thresholds kills the pod mid-migration.

## Tasks

1. Add `kubernetes/apps/matrix/tuwunel/` — ks.yaml (volsync component), app/
   (kustomization, ocirepository, helmrelease, externalsecret) and
   `resources/tuwunel.toml` (Go-templated, secret resolved from the 1Password
   `matrix` item).
2. Delete `kubernetes/apps/matrix/{synapse,mas,synapse-admin}/`.
3. `kubernetes/apps/matrix/kustomization.yaml`: enable tuwunel + element, drop
   the three removed apps (bridges stay commented).
4. `kubernetes/apps/database/dragonfly/cluster/networkpolicy.yaml`: drop the
   `matrix-synapse` ingress rule (Tuwunel needs no Redis).
5. Validate: app-template schema on the HelmRelease, HTTPRoute render,
   `flux-local test --all-namespaces --enable-helm`.
6. PR; merge = the swap (nobody is relying on the old server).

## Post-merge (by hand)

1. Create the admin in the first boot logs / via `--execute`, or afterwards with
   the admin room: `!admin users create_user <name> [password]`.
2. Kanidm client: update its redirect URL to
   `https://matrix.${SECRET_DOMAIN}/_matrix/client/unstable/login/sso/callback/matrix`
   (client_id = the Kanidm client name, which is why the path says `matrix`).
3. Verify: `curl -sk https://matrix.${SECRET_DOMAIN}/_matrix/client/versions`,
   log in from Element, then `!admin server backup-database` once to confirm the
   backup path is writable.
4. Optional cleanup: drop the now-unused `synapse`/`mas` databases from CNPG and
   the leftover synapse/mas fields in 1Password.

## Risks / open items

- Tuwunel's image runs as root with no `USER` set; the manifest runs it as 1000
  with `fsGroup: 1000` on the PVC. If it fails to write its database, flip the
  pod securityContext to `runAsUser: 0` (one-line change).
- `readOnlyRootFilesystem: true` assumes Tuwunel writes only to
  `/var/lib/tuwunel` and `/tmp`.
- Media is *not* covered by Tuwunel's managed DB backups; volsync ships the whole
  PVC hourly, which covers media as files and gives off-box copies of the
  consistent DB backups.
- The old Synapse PVC/kopia snapshots are pruned with the app — intended, since
  the data is disposable.
