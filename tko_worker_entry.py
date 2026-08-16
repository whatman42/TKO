"""
TKO — Tokocrypto autonomous worker entrypoint (packaged Windows/Linux).
Default: PAPER (safe). LIVE requires HardLiveGate. Never embeds credentials.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def configure_logging(base: Path) -> Path:
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "tko_worker.log"
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    return log_path


def main(argv: list[str] | None = None) -> int:
    base = app_base_dir()
    try:
        os.chdir(base)
    except OSError:
        pass
    log_path = configure_logging(base)
    log = logging.getLogger("TKO.Entrypoint")

    parser = argparse.ArgumentParser(
        prog="TKO",
        description="TKO Tokocrypto autonomous trading worker (single-exchange)",
    )
    parser.add_argument(
        "--mode",
        choices=["PAPER", "SHADOW", "LIVE"],
        default="PAPER",
        help="Default PAPER (safe). LIVE is fail-closed via HardLiveGate.",
    )
    parser.add_argument(
        "--db-path",
        default=str(base / "data" / "tko.db"),
        help="Writable SQLite path (default: <deploy>/data/tko.db)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=10.0,
        help="Worker loop interval seconds",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.critical("Cannot create data directory (fail-closed): %s", e)
        return 1

    log.info(
        "TKO start mode=%s db=%s log=%s base=%s frozen=%s",
        args.mode, db_path, log_path, base, getattr(sys, "frozen", False),
    )

    from tokocrypto_bot.application import (
        AutonomousTradingWorker,
        ExecutionMode,
        LiveTradingBlockedError,
    )

    mode = ExecutionMode[args.mode]
    if mode == ExecutionMode.LIVE:
        log.warning(
            "LIVE requested — HardLiveGate mandatory; credentials from "
            "secure store/environment only (never embedded in the executable)."
        )

    try:
        worker = AutonomousTradingWorker(
            mode=mode,
            db_path=str(db_path),
            api_key="",
            api_secret="",
        )
    except LiveTradingBlockedError as e:
        log.critical("LIVE blocked (fail-closed): %s", e)
        return 2
    except Exception as e:
        log.critical("Worker init failed (fail-closed, LIVE not enabled): %s", e)
        return 1

    unlocked = bool(getattr(worker, "_live_unlocked", False))
    log.info("Worker ready. live_unlocked=%s", unlocked)
    if mode == ExecutionMode.LIVE and not unlocked:
        log.critical("LIVE requested but not unlocked — refusing to run.")
        return 2

    try:
        worker.run_worker_loop(poll_interval_sec=args.poll_interval)
    except KeyboardInterrupt:
        log.info("Shutdown requested by user.")
    except Exception as e:
        log.critical("Worker loop crashed (LIVE not auto-enabled): %s", e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
