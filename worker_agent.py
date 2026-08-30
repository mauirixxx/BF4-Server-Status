"""BF4 Server Watcher PR2 remote worker entry point.

PR4-D remote workers run the same control-plane/Discord leadership lifecycle as
serverwatcher.py. When keeper.distributed_enabled is true, every eligible worker
acquires only its HRW-owned Keeper snapshots while the fenced Discord leader
remains the sole processor of Discord/database side effects.
"""
from serverwatcher import main

if __name__ == "__main__":
    main()
