# BF4 HA v3.1.0-alpha4-candidate1

This candidate hardens the Alpha3 R2 HA transaction model without enabling fully automatic failover.

- H-001: commands use the highest valid effective local journal for the active operation; same-revision conflicts fail closed; journal status identifies superseded active state.
- H-002: operator confirmations and secret prompts are bounded (120 seconds) and abort cleanly on timeout/EOF/interrupt.
- H-003: schema-v3 journals are checked for operation/controller/revision validity, legal phases, monotonic completion flags, and phase/flag consistency before trusted use/transport.
- H-004: recovery-quorum loss is distinguished from local commit failure; the local checkpoint remains authoritative and the operator is directed to journal-quorum-check.
- H-005: atomic JSON replacement fsyncs the containing directory.
- H-006: fence no longer unlinks the active journal before atomic replacement.
- H-007: a new fencing transaction is blocked by an unfinished effective active transaction.
- H-008: fence-prepared is committed and replicated before the first old-primary mutation; boot-guard proof is checkpointed as fence-guard-established before power-off; fence --resume and takeover reconcile interrupted fencing forward from independently verified reality.
- Recovery hardening: promotion can reconcile an already-promoted writable candidate; postcheck can preserve a partially resumed worker fleet; rejoin can reconcile an already-streaming former primary without reseeding it twice.

Release rule: package/artifact must pass static and offline regression checks before any controlled deployment validation.


## Candidate2 packaging fix
- Replaced hard-coded `/opt/bf4-ha/lib` Python bootstrap in every executable with a path derived from the executable location, allowing isolated staging while retaining production config/state paths.
- Removed duplicate `confirm_exact` import in `bf4-ha-fence`.

## Candidate3
- UX-002: `bf4-ha-fence` now validates `--expect-primary` immediately after discovering/validating the current primary, before DNS/quiesce/candidate readiness or any vCenter/ESXi credential prompt.
- The existing later execute-path expected-primary assertion remains in place as defense in depth before destructive continuation.


## Candidate4
- H-009: `bf4-ha-takeover` now preserves PostgreSQL primary cardinality from `discover_roles()` instead of collapsing both zero and multiple primaries through `current_primary() == None`.
- Two or more positively observed reachable PostgreSQL primaries fail closed with rc=24 before fencing credential prompts or takeover mutation.
- Role-discovery warnings are surfaced to the operator. Zero reachable primaries remains distinct from a positively observed split-brain condition and continues through the existing phase-specific recovery checks.

## Candidate5
- H-009B: `bf4-ha-takeover` now treats a sole positively observed PostgreSQL primary outside the journaled `{old_primary, candidate}` pair as a global topology violation and fails closed with rc=23 before fencing credential prompts or phase-specific continuation.
- The journaled old primary remains globally allowable so legitimate `fence-prepared` / prepared-unmutated takeover recovery is preserved; phase-specific fence checks still decide whether that old-primary observation is valid for the current phase.
- Candidate4's 2+ primary split-brain rc=24 gate remains unchanged.

## Candidate6
- H-011: rejoin now durably records the canonical PGDATA path and the one-and-only original former-primary backup pathname in a new `rejoin-reseed-prepared` journal checkpoint before the first PGDATA rename.
- Interrupted reseed recovery reuses that recorded original backup identity. If the original backup already exists, a partial reseed destination is preserved under `.failed-reseed-*` and a fresh destination is created; it is never mislabeled as the original pre-rejoin database.
- Journal semantic validation requires `rejoin-reseed-prepared` to carry a canonical PostgreSQL PGDATA path and matching `.pre-rejoin-*` backup identity.

## Candidate7
- H-012: rejoin now durably checkpoints a successfully completed `pg_basebackup` as `rejoin-basebackup-completed` before restoring PostgreSQL automatic-start behavior or starting the reseeded standby.
- The checkpoint records explicit basebackup completion evidence together with the canonical PGDATA path, source-primary identity, and replication-slot identity.
- Recovery recognizes the durable completion evidence independently of the current workflow phase, because a subsequent recovery invocation may legitimately pass through `rejoin-guard-established` before evaluating reseed recovery.
- Before reusing a completed reseed, rejoin validates the recorded PGDATA/source/slot identities and requires both canonical PGDATA and the original `.pre-rejoin-*` backup to exist.
- A proven completed basebackup is reused without another destructive reseed; genuinely partial/interrupted basebackups retain Candidate6 H-011 behavior and are preserved under `.failed-reseed-*` before retry.
- Offline validation: H-012 journal semantic regression suite 13/13 PASS; real-executable rejoin crash/recovery matrix 48/48 PASS.
## Candidate8
- H-012B: closes the remote-success/controller-acknowledgement gap around `pg_basebackup`. The former-primary host now creates an operation-specific `.basebackup-complete` marker only after `pg_basebackup` exits successfully, before the SSH command can return success to the controller. The marker is atomically published via temporary-file + rename and binds operation ID, canonical PGDATA, original-backup path, source-primary identity, and replication-slot identity.
- Recovery may adopt that remote completion evidence only when the durable rejoin backup identity is already recorded and both canonical PGDATA and the recorded original `.pre-rejoin-*` backup still exist; it then journals `rejoin-basebackup-completed` and reuses the completed reseed instead of copying the database again.
- Partial/interrupted basebackups never create the marker and retain H-011 `.failed-reseed-*` preservation/retry behavior.
- H-013 startup recovery remains fail-closed for unsafe powered-on former-primary reality and reuses completed reseeds across crashes after `start.conf=auto` or unit unmasking.
- Offline validation: real-executable H-013 crash/recovery matrix 70/70 PASS, including the previously failing remote-success acknowledgement window and H-012C rejection of a mismatched/forged completion-marker identity; target executable and harness `py_compile` PASS.
## Candidate9
- H-015D: closes a journal/remote-evidence contradiction discovered after Candidate8 packaging. If a durable controller `rejoin-basebackup-completed` checkpoint exists and a remote `.basebackup-complete` marker is also present, their bound identities must agree; a contradictory marker now fails closed before startup with zero additional basebackup operations.
- A missing remote marker remains recoverable after the controller completion checkpoint is durable; H-015E proves completed PGDATA is reused and the rejoin completes without another basebackup.
- H-015A/B/C prove completed-checkpoint recovery fails closed when canonical PGDATA, the recorded original backup, or both disappear.
- H-016A/B/C prove corrupt durable completed-basebackup PGDATA/source-primary/slot identities fail closed with zero additional basebackup operations.
- H-017 proves orphan temporary completion markers are ignored and invalid final markers fail closed.
- H-018 binds the durable original-PGDATA backup identity to filesystem history: if the recorded path is absent while a sibling `.pre-rejoin-*` backup exists, recovery fails closed before any further PGDATA mutation. Stale evidence from a previous operation or backup identity cannot authorize reuse or a fresh destructive rename.
- Expanded real-executable crash/recovery/adversarial matrix: 121/121 PASS. Candidate8 packaged archives remain frozen and unchanged; this worktree is Candidate9.


## Candidate10
- Fixes production-discovered journal transition validation ordering: completion evidence belongs to the target phase and is now validated after the in-memory phase advance, while controller ownership remains enforced before the transition.
- Exact live failure preserved as regression rationale: fence power-off succeeded, then Candidate9 rejected fence_completed=True while the in-memory phase was still fence-guard-established.
- 121/121 rejoin adversarial matrix and explicit end-to-end journal transition sequence PASS.

## Candidate11
- Production validation exposed a guarded fence-resume topology gate that still rejected the intentionally unavailable journaled old primary after a verified power-off.
- During the exact `fence-guard-established` resume phase, only that journaled old primary is exempted from the generic unavailable-DB blocker; all unrelated unavailable DB nodes remain fail-closed.
- Existing TCP/5432-down and fencing-backend `poweredOff` proofs remain mandatory before recovery can advance to `fence-completed-recovered`.
- Focused regression proves recovery performs zero duplicate power-off operations; negative control proves unrelated DB loss remains blocked.
- Full H-013/H-018 real-executable matrix remains 121/121 PASS.
