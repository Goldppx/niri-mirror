#!/usr/bin/env python3
"""测试用的假 niri：从 $FAKE_STATE 目录读状态，行为贴近 `niri msg`。"""
import json
import os
import pathlib
import sys
import time

STATE = pathlib.Path(os.environ["FAKE_STATE"])


def read(name, default):
    path = STATE / name
    if not path.exists():
        return default
    text = path.read_text().strip()
    return json.loads(text) if text else default


args = sys.argv[1:]
if not args or args[0] != "msg":
    print(f"fake-niri: unsupported args {args}", file=sys.stderr)
    sys.exit(2)

rest = [a for a in args[1:] if a != "--json"]
cmd = rest[0] if rest else ""
outputs = read("outputs.json", {})

if cmd == "outputs":
    print(json.dumps(outputs))
elif cmd == "focused-output":
    name_file = STATE / "focused.txt"
    name = name_file.read_text().strip() if name_file.exists() else ""
    print(json.dumps(outputs.get(name, {})))
elif cmd == "windows":
    print(json.dumps(read("windows.json", [])))
elif cmd == "workspaces":
    print(json.dumps(read("workspaces.json", [])))
elif cmd == "event-stream":
    last = None
    while True:
        bump = STATE / "bump"
        cur = bump.stat().st_mtime_ns if bump.exists() else 0
        if cur != last:
            last = cur
            print(json.dumps({"WorkspacesChanged": {}}), flush=True)
        time.sleep(0.05)
elif cmd == "action":
    with open(STATE / "actions.log", "a") as fh:
        fh.write(" ".join(rest[1:]) + "\n")
else:
    sys.exit(0)
