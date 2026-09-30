# Optional PostgreSQL HA Toolkit

This directory contains the optional semi-automatic PostgreSQL high-availability toolkit released with BF4 Server Watcher v3.1.0.

**You do not need this toolkit to run BF4 Server Watcher.** A normal installation may use one PostgreSQL server through `DATABASE_URL`, exactly as before. The HA toolkit is for operators who deliberately run multiple PostgreSQL nodes and want guarded failover/recovery tooling.

## What it provides

The toolkit implements operator-confirmed workflows for workload quiesce, former-primary fencing, standby promotion, surviving-replica reparenting, BIND/TSIG service-DNS cutover, guarded worker resume/postcheck, takeover/recovery, and former-primary reseed/rejoin.

It is intentionally semi-automatic. Destructive boundaries remain operator-confirmed, while the tools validate fencing, journal ownership, PostgreSQL roles, replication, DNS convergence, and recovery checkpoints.

## Production validation

The runtime here is the production-validated BF4 HA v3.1.0 Candidate12 payload. On 2026-09-29 it completed a full live failover/rejoin transaction from `mak-db-01` to `mak-db-02`, including an interrupted-fence recovery discovered during the exercise. The final journal reached revision 12 / `rejoin-completed` and the real-executable crash/recovery/adversarial matrix reached 124/124 PASS.

See `RELEASE-NOTES.md`, `LIVE-VALIDATION-20260929.md`, and `CHANGELOG.md` for the detailed validation record and hardening history.

## Deployment warning

Do not copy the example `config/cluster.json` over an existing production configuration. Treat it as a topology/configuration template. HA state, credentials, TSIG keys, PostgreSQL credentials, vCenter/ESXi credentials, and site-specific addressing are operator-managed and are not included in this repository.

The HA tools assume a deliberately designed PostgreSQL replication/fencing environment. Review the configuration and dry-run/readiness commands before any live operation.
