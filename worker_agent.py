"""BF4 Server Watcher PR2 remote worker entry point.

PR2 remote workers run the same control-plane/Discord leadership lifecycle as
serverwatcher.py. Keeper remains explicitly guarded to rnt-01 inside the
leader session, so remote agents cannot start Keeper polling in PR2.
"""
from serverwatcher import main

if __name__ == "__main__":
    main()
