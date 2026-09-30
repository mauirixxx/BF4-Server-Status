# BF4 Server Watcher fleet deployment

This directory preserves the deployment mechanism first used for the production v3.1.1 rollout on 2026-09-30.

## Design

The deployment key originates on `mak-01.bf4statusbot.com` under the unprivileged `bf4remote` account. On remote workers, the public key is installed in root's `authorized_keys` with a forced command pointing at `/usr/local/sbin/bf4-fleet-deploy` and with agent, port, X11, and PTY forwarding disabled.

The forced-command wrapper accepts only explicitly implemented operations. Arbitrary commands are rejected with exit code 64. The production boundary was verified by requesting `id` and confirming that the wrapper returned `DENIED` rather than executing a root shell command.

Use FQDNs for fleet infrastructure access; do not rely on short hostnames.

## v3.1.1 production fleet

The eight workers are:

- `mak-01.bf4statusbot.com`
- `mak-02.bf4statusbot.com`
- `mak-03.bf4statusbot.com`
- `mak-04.bf4statusbot.com`
- `hnl-01.bf4statusbot.com`
- `hnl-02.bf4statusbot.com`
- `kah-01.bf4statusbot.com`
- `rnt-01.bf4statusbot.com`

`mak-01` is the orchestration host. The seven other workers use the forced-command SSH path. The local `mak-01` deployment can invoke the same wrapper by setting `SSH_ORIGINAL_COMMAND` directly.

The v3.1.1 wrapper is intentionally release-specific: it pins the GitHub release asset URL and expected SHA-256 instead of accepting an arbitrary URL or version from the caller.

## v3.1.1 rollout procedure

1. Drain the target worker through the bot operator controls and verify Keeper/Persona ownership reaches zero.
2. Run `ssh <worker-fqdn> deploy-v3.1.1` using the restricted fleet identity.
3. The wrapper downloads the official release ZIP, verifies its pinned SHA-256, validates archive contents, and only then crosses the write boundary.
4. The node-local `.env` is preserved and fingerprinted before and after replacement.
5. The image is rebuilt locally and the worker-agent container is recreated from the existing Compose definitions.
6. Require `DEPLOYMENT PASS`, the expected image, a running container, and an unchanged `.env` fingerprint before proceeding.
7. Resume workers deliberately and verify `/operator status` after the fleet has rebalanced.

The first production v3.1.1 rollout used `mak-02` as the official-release canary, moved Discord leadership from `mak-01` to `mak-02`, upgraded `mak-01` last, and then resumed the fleet.

## Preserved artifact

`bf4-fleet-deploy-v3.1.1` is the exact wrapper used for the successful production rollout. Its SHA-256 at preservation time is:

`47bb58ca1b2e727d1ef7d26aee6b1a9deb310cefcdb359ec103e2696d4a190d0`
