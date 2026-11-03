#!/usr/bin/env python3
"""测试用的假 notify-send：把调用参数记到 $FAKE_STATE/notify.log。"""
import os
import pathlib
import sys

STATE = pathlib.Path(os.environ["FAKE_STATE"])
with open(STATE / "notify.log", "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
