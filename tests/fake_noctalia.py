#!/usr/bin/env python3
"""测试用的假 noctalia CLI：记录 `noctalia msg ...` 的调用。"""
import os
import pathlib
import sys

STATE = pathlib.Path(os.environ["FAKE_STATE"])
with open(STATE / "noctalia.log", "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
