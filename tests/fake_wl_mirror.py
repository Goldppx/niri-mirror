#!/usr/bin/env python3
"""测试用的假 wl-mirror：记录启动参数，收到 SIGTERM 时记一笔再退出。"""
import os
import pathlib
import signal
import sys
import time

STATE = pathlib.Path(os.environ["FAKE_STATE"])
LOG = STATE / "wlmirror.log"


def note(kind: str) -> None:
    with open(LOG, "a") as fh:
        fh.write(f"{kind} pid={os.getpid()} argv={' '.join(sys.argv[1:])}\n")


def on_term(_sig, _frame):
    note("TERM")
    sys.exit(0)


signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGINT, on_term)
note("START")
while True:
    time.sleep(0.05)
