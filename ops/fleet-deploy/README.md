# BF4 Server Watcher fleet deployment

This directory preserves the deployment mechanism first used for the production v3.1.1 rollout on 2026-09-30 and contains the generalized forced-command deployer for later releases.

## Design

The deployment key originates on `mak-01.bf4statusbot.com` under the unprivileged `bf4remote` account. On remote workers, the public key is installed in root's `authorized_keys` with a forced command pointing at `/usr/local/sbin/bf4-fleet-deploy` and with agent, port, X11, and PTY forwarding disabled.

The forced-command wrapper accepts only explicitly implemented operations. Arbitrary commands are rejected with exit code 64. The production boundary was verified by requesting `id` and confirming that the wrapper returned `DENIED` rather than executing a root shell command.

Use FQDNs for fleet infrastructure access; do not rely on short hostnames.

## Production fleet

Current membership is defined by `fleet-inventory.txt`. Fleet size is not fixed; add or remove FQDN entries as workers enter or leave production. Runtime roles and Discord leadership are intentionally not encoded in the inventory.

`mak-01.bf4statusbot.com` is currently the orchestration host. Remote workers use the forced-command SSH path. A local `mak-01` deployment can invoke the same wrapper by setting `SSH_ORIGINAL_COMMAND` directly.

## Generalized release deployer

`bf4-fleet-deploy` keeps the same forced-command boundary but removes the need to rewrite the deployment procedure for every release. It does **not** accept arbitrary URLs or arbitrary releases.

Each deployable release must first be added to the root-owned `release_metadata()` allowlist with three pinned values:

- semantic version;
- expected GitHub Release asset filename;
- expected SHA-256 of that asset.

The caller can then request only `deploy-v<approved-version>`. Unknown versions, malformed versions, arbitrary commands, and arbitrary URLs remain denied.

Before the production container is stopped, the deployer downloads the approved asset from the fixed `mauirixxx/BF4-Server-Status` GitHub repository, verifies its pinned SHA-256, rejects unsafe archive paths and mutable/cache content, extracts into a temporary staging directory, verifies required runtime files, and verifies the embedded `BOT_VERSION`.

The versioned Docker image is built from the validated staging tree while the existing worker is still running. Only after that build succeeds does the deployer cross the write boundary. Before doing so it snapshots the current application tree, `.env` fingerprint, container image reference, and immutable image ID. The application tree is then replaced and the worker recreated. The deployment is accepted only if the expected image is running and the `.env` fingerprint is unchanged.

If replacement, container recreation, or post-start validation fails after the write boundary, the deployer automatically restores the previous application tree, retags the captured previous image ID to its prior image reference, recreates the prior worker, and verifies that it is running with the original `.env` fingerprint. A successful recovery reports `ROLLBACK PASS`; rollback failure escalates separately for operator intervention.

`approved-releases` provides a read-only way to see what the installed wrapper will permit.

## Rollout procedure

1. Create and publish the GitHub release asset.
2. Compute and independently verify its SHA-256.
3. Add that version, exact asset filename, and SHA-256 to `release_metadata()` on a feature branch and review the change.
4. Install the reviewed root-owned wrapper on the fleet.
5. Drain one target worker and verify Keeper/Persona ownership reaches zero.
6. Run `ssh <worker-fqdn> deploy-v<version>` using the restricted fleet identity.
7. Require `DEPLOYMENT PASS`, the expected image, a running container, and an unchanged `.env` fingerprint.
8. Resume the canary and verify `/operator status` before continuing serially through the fleet.
9. Move Discord leadership deliberately before deploying the current leader.
10. Upgrade the former leader last, resume all workers, and verify the current inventory has rebalanced.

The first production v3.1.1 rollout used `mak-02` as the official-release canary, moved Discord leadership from `mak-01` to `mak-02`, upgraded `mak-01` last, and then resumed the fleet.

## Preserved v3.1.1 artifact

`bf4-fleet-deploy-v3.1.1` is the exact wrapper used for the successful production rollout. Its SHA-256 at preservation time is:

`47bb58ca1b2e727d1ef7d26aee6b1a9deb310cefcdb359ec103e2696d4a190d0`

It remains in the repository as the known-good historical implementation rather than being overwritten by the generalized deployer.
