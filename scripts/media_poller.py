"""Backward-compatible shim for the media poller.

The poller now lives in the package so it ships as a console script
(``tradingagents-poller``) and is importable for the backtest loader. This shim
keeps ``python scripts/media_poller.py …`` working; prefer the console script or
``python -m tradingagents.poller`` going forward. See tradingagents/poller.py.
"""

from tradingagents.poller import main

if __name__ == "__main__":
    main()
