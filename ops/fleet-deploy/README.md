# BF4 Server Watcher fleet deployment

This directory contains a reusable, restricted deployment mechanism for rolling BF4 Server Watcher releases across one or more Linux worker nodes.

The tooling is intentionally generic. It does not assume any specific hostname, site, fleet size, or Discord leadership placement. Current production membership can be tracked separately in `fleet-inventory.txt`, but the deployer itself operates on one node at a time.

## What this provides

- a root-owned forced-command wrapper installed as `/usr/local/sbin/bf4-fleet-deploy`;
- a dedicated SSH deployment key whose remote authorization is restricted to that wrapper;
- release allowlisting by exact version, GitHub Release asset filename, and SHA-256;
- pre-write validation of archive safety, required files, version metadata, Compose image tags, and Docker buildability;
- preservation and fingerprint verification of the node-local `.env`;
- automatic rollback to the previous application tree/image/container if a post-write deployment step fails;
- explicit rollback-failure escalation for operator intervention.

The SSH caller cannot provide an arbitrary URL or arbitrary root command.

## Files

- `bf4-fleet-deploy` — current generalized forced-command deployer.
- `bf4-fleet-deploy-v3.1.1` — preserved historical wrapper from the first production rollout.
- `fleet-inventory.txt` — optional operator-maintained list of current worker FQDNs.
- `test-bf4-fleet-deploy.sh` — trust-boundary tests.
- `test-bf4-fleet-deploy-rollback.sh` — isolated rollback/fault-injection tests.

## Prerequisites

On every worker node:

- Docker Engine and Docker Compose v2;
- `curl`, `unzip`, `sha256sum`, `awk`, `find`;
- an existing BF4 Server Watcher installation at `/opt/bf4-serverwatcher`;
- a node-specific `/opt/bf4-serverwatcher/.env`;
- the worker container name `BF4_ServerWatcher_Agent`;
- network access to GitHub Releases.

On the orchestration host:

- OpenSSH client;
- a dedicated Ed25519 fleet deployment key;
- SSH host keys recorded for every managed worker.

Use FQDNs for fleet infrastructure access.

## 1. Create a dedicated fleet deployment key

Run on the orchestration host as the unprivileged operator account:

```bash
ssh-keygen -t ed25519 \
  -f ~/.ssh/id_ed25519_bf4_fleet \
  -C "BF4 Server Watcher fleet deployment"
```

Protect the private key:

```bash
chmod 0600 ~/.ssh/id_ed25519_bf4_fleet
```

The public key is:

```bash
cat ~/.ssh/id_ed25519_bf4_fleet.pub
```

Do not reuse a normal interactive administrator key for this purpose.

## 2. Install the deployer on each worker

Copy the reviewed `bf4-fleet-deploy` file to the worker as:

```text
/usr/local/sbin/bf4-fleet-deploy
```

Then, as root on the worker:

```bash
chown root:root /usr/local/sbin/bf4-fleet-deploy
chmod 0755 /usr/local/sbin/bf4-fleet-deploy
bash -n /usr/local/sbin/bf4-fleet-deploy
```

The syntax check must pass before continuing.

## 3. Install the restricted SSH authorization

Append the fleet public key to root's `~/.ssh/authorized_keys` on the worker with a forced command:

```text
command="/usr/local/sbin/bf4-fleet-deploy",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty ssh-ed25519 <PUBLIC-KEY-DATA> BF4 Server Watcher fleet deployment
```

The restriction is important: the key is allowed to invoke only operations implemented by the wrapper.

Recommended root SSH directory permissions:

```bash
chmod 0700 /root/.ssh
chmod 0600 /root/.ssh/authorized_keys
chown -R root:root /root/.ssh
```

## 4. Configure SSH on the orchestration host

Example `~/.ssh/config` entry:

```text
Host worker-a.example.com worker-b.example.com worker-c.example.com
    User root
    IdentityFile ~/.ssh/id_ed25519_bf4_fleet
    IdentitiesOnly yes
    BatchMode yes
```

Record worker host keys before unattended use:

```bash
ssh-keyscan -H worker-a.example.com >> ~/.ssh/known_hosts
```

Repeat for each worker.

## 5. Verify the restricted trust boundary

A supported read-only operation should work:

```bash
ssh worker-a.example.com probe
```

An arbitrary command must fail:

```bash
ssh worker-a.example.com id
```

Expected result:

```text
DENIED: unsupported BF4 fleet deployment operation
```

The denied command should return exit code 64.

## 6. Approve a release for deployment

Before a release can be deployed, add it to `release_metadata()` inside `bf4-fleet-deploy`.

Each entry pins:

1. version;
2. GitHub Release ZIP filename;
3. SHA-256 of that exact ZIP.

Example:

```bash
3.1.2)
    printf '%s\n' '3.1.2|BF4_Server_Watcher_v3.1.2.zip|<SHA256>'
    ;;
```

Do not add caller-controlled URLs.

After editing, run:

```bash
bash ops/fleet-deploy/test-bf4-fleet-deploy.sh
bash ops/fleet-deploy/test-bf4-fleet-deploy-rollback.sh
TEST_FAIL_ROLLBACK=1 bash ops/fleet-deploy/test-bf4-fleet-deploy-rollback.sh
```

Then install the reviewed updated wrapper on the worker fleet using the same root ownership and mode described above.

## 7. Check what a worker will allow

```bash
ssh worker-a.example.com approved-releases
```

Example:

```text
v3.1.1
v3.1.2
```

## 8. Prepare a worker for upgrade

Use the application's operator controls to drain the target worker before deploying.

Confirm that:

- the target worker is in the draining state;
- Keeper ownership has reached 0;
- Persona ownership has reached 0;
- the worker is not the active Discord leader, or leadership has been deliberately moved elsewhere;
- at least one healthy eligible worker remains online.

For the current Discord leader, move leadership away first and upgrade that worker last.

## 9. Deploy one worker

From the orchestration host:

```bash
ssh worker-a.example.com deploy-v3.1.2
```

The wrapper will:

1. inspect the existing container and fingerprint `.env`;
2. download the approved GitHub Release ZIP;
3. verify the pinned SHA-256;
4. reject unsafe archive paths and mutable/cache content;
5. verify required runtime files and embedded version;
6. verify both Compose files reference the expected image;
7. build the new Docker image while the old worker is still running;
8. snapshot the previous application tree and immutable image identity;
9. stop the old worker;
10. replace the application tree while preserving `.env`;
11. recreate the worker;
12. verify running state, expected image, and unchanged `.env`.

A successful deployment ends with:

```text
DEPLOYMENT PASS
host=<worker-hostname>
image=bf4-server-watcher:<version>
env=UNCHANGED
```

Do not continue the rollout if this result is not present.

## 10. What happens on failure

Failures before the write boundary leave the running worker untouched.

After the write boundary, the deployer automatically attempts to restore:

- the previous application tree;
- the previous Docker image reference using the captured immutable image ID;
- the previous worker container;
- the original `.env` fingerprint.

Successful recovery reports:

```text
ROLLBACK PASS: previous worker restored and .env unchanged
```

If rollback itself cannot be verified, the wrapper exits with code 90 and reports:

```text
ROLLBACK FAILED: operator intervention required
```

Stop the rollout and inspect that node manually.

## 11. Resume and validate the canary

After the first upgraded worker reports `DEPLOYMENT PASS`:

1. resume that worker through the application's operator controls;
2. wait for it to become healthy;
3. confirm it takes an appropriate share of distributed work;
4. verify Discord leadership remains stable;
5. verify the reported runtime version is correct.

Only then continue to the next worker.

## 12. Roll out across multiple nodes

Maintain the fleet membership in `fleet-inventory.txt`, one FQDN per line. Blank lines and lines beginning with `#` should be treated as comments by any orchestration script built around this inventory.

A simple serial operator loop can inspect every node:

```bash
while read -r HOST; do
    [[ -z "$HOST" || "$HOST" == \#* ]] && continue
    echo "===== $HOST ====="
    ssh "$HOST" probe
done < ops/fleet-deploy/fleet-inventory.txt
```

For actual upgrades, keep the drain/resume decision explicit rather than blindly upgrading all nodes at once.

Recommended sequence:

1. choose one non-leader worker as canary;
2. drain it;
3. deploy the release;
4. resume it and validate;
5. repeat serially for remaining non-leader workers;
6. deliberately move Discord leadership away from the current leader;
7. drain and upgrade the former leader last;
8. resume all workers;
9. verify the entire current inventory reports the expected version and that distributed ownership has rebalanced.

Fleet size is not fixed. Add or remove FQDN entries as workers enter or leave production.

## 13. Local deployment on the orchestration host

If the orchestration host is also a worker and the wrapper is installed locally, invoke it directly as root with the same command OpenSSH would supply:

```bash
SSH_ORIGINAL_COMMAND='deploy-v3.1.2' \
    /usr/local/sbin/bf4-fleet-deploy
```

Use the same drain, leadership, and validation rules as for a remote worker.

## Security notes

- Keep the fleet private key readable only by the orchestration account.
- Keep the wrapper root-owned and non-writable by the orchestration account.
- Never remove the forced-command restrictions from the fleet key.
- Never accept a release URL from the SSH caller.
- Pin the exact release ZIP SHA-256.
- Do not package `.env`, `.git`, Python bytecode, or cache directories into release ZIPs.
- Review changes to `release_metadata()` and the deployment wrapper as privileged infrastructure changes.
