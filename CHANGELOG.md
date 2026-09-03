## v3.0.1

- Added fair PostgreSQL-backed Keeper request scheduling so healthy
  distributed workers cannot starve under sustained load.

- Added adaptive distributed presence health based on observed Keeper
  sweep cadence instead of a fixed 15-minute snapshot assumption.

- Added durable presence aggregate fallback across Discord leadership
  handoffs.

- Preserved meaningful Keeper cadence telemetry across zero-work sweeps
  during drains, restarts, and rolling deployments.
