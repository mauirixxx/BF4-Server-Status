# v3.1.0 Live Validation — 2026-09-29

Production operation: `20260929T093707Z-dcc3880a`
Controller: `mak-01`
Initial primary: `mak-db-01` / `192.168.10.77`
Promotion candidate: `mak-db-02` / `192.168.10.78`

## Result

The end-to-end production failover and former-primary rejoin completed successfully. The final journal reached revision 12 with phase `rejoin-completed`.

Final database topology:
- `mak-db-02` — PRIMARY
- `mak-db-01` — STANDBY, streaming
- `hnl-db-01` — STANDBY, streaming
- `kah-db-01` — STANDBY, streaming
- `rnt-db-01` — STANDBY, streaming

All four authoritative/resolver checks returned `db.bf4statusbot.com -> 192.168.10.78`. All eight ServerWatcher workers were running after guarded postcheck/resume. All four sites reported AVAILABLE.

The final status snapshot was `HEALTHY-DEGRADED` only because three asynchronous remote replicas showed a transient 400-byte lag; all remained streaming, while the Makawao standby showed zero lag.

## Production-discovered hardening

The live exercise exposed recovery boundaries not fully represented by earlier offline testing. Each was converted into a regression before continuing production recovery:

- Journal transition validation after successful fencing power-off.
- Guarded fence resume when the journaled old primary is intentionally unavailable.
- Remote-success/controller-acknowledgement handling around completed `pg_basebackup`.
- Interrupted PostgreSQL startup after completed reseed.
- Completion-marker identity binding and contradiction handling.
- H-018B distinction between legitimate historical `.pre-rejoin-*` backups and current-operation filesystem contradictions.

Candidate12's real-executable crash/recovery/adversarial matrix finished at 124/124 PASS.

## Former-primary rejoin

`mak-db-01` was maintenance-booted under `start.conf=disabled` plus dual systemd masks. Physical replication credentials were verified before PGDATA mutation. The operation durably recorded the original-PGDATA identity, preserved it at `/var/lib/postgresql/16/main.pre-rejoin-20260929T185859Z`, completed a full `pg_basebackup`, then started PostgreSQL and verified `mak-db-01` streaming from `mak-db-02`.
