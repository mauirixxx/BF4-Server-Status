# BF4 HA v3.1.0

Released: 2026-09-29

v3.1.0 is the production-validated semi-automatic PostgreSQL HA release for BF4 ServerWatcher. It keeps destructive failover actions operator-confirmed while enforcing fencing, journal ownership, durable recovery checkpoints, replication verification, DNS convergence, and guarded workload resume.

## Validated failover sequence

The final Candidate12 runtime completed a live production transaction through:

1. ServerWatcher quiesce.
2. Former-primary fencing and interrupted-fence recovery.
3. Standby promotion.
4. Survivor reparenting.
5. BIND/TSIG service-DNS cutover.
6. Guarded eight-worker resume and postcheck.
7. Former-primary full reseed and rejoin.

The completed production journal reached revision 12 / `rejoin-completed`.

## Release source

The runtime payload is promoted from `BF4_HA_v3.1.0-alpha4-candidate12-20260929.tar.gz` (SHA256 `8f7bc2d407f7bcce1ffbe7de4a53d41e31b1aaeb7de613763bdfc0d65ab462df`) with no functional code changes.
