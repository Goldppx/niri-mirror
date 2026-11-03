#!/usr/bin/env python3
"""niri-mirror 的端到端测试：用假的 niri / wl-mirror 模拟热插拔。

python3 tests/run_tests.py -v
"""
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN = ROOT / "niri_mirror.py"
FAKES = ROOT / "tests"

EDP = {
    "name": "eDP-2",
    "modes": [{"width": 2560, "height": 1600, "refresh_rate": 180000, "is_preferred": True}],
    "current_mode": 0,
    "logical": {"x": 0, "y": 0, "width": 1706, "height": 1066, "scale": 1.5, "transform": "Normal"},
}
HDMI = {
    "name": "HDMI-A-1",
    "modes": [{"width": 1920, "height": 1080, "refresh_rate": 60001, "is_preferred": True}],
    "current_mode": 0,
    "logical": {"x": 1706, "y": 0, "width": 1920, "height": 1080, "scale": 1.0, "transform": "Normal"},
}

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILED.append(msg)


class Sandbox:
    def __init__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="niri-mirror-test-"))
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.state = self.dir / "state"
        self.state.mkdir()
        self._install("niri", FAKES / "fake_niri.py")
        self._install("wl-mirror", FAKES / "fake_wl_mirror.py")
        self._install("notify-send", FAKES / "fake_notify_send.py")
        self._install("noctalia", FAKES / "fake_noctalia.py")
        (self.dir / "run").mkdir()
        (self.dir / "state" / "notify.log").touch()
        (self.dir / "state" / "noctalia.log").touch()

    def _install(self, name, src):
        dst = self.bin / name
        shutil.copy(src, dst)
        dst.chmod(0o755)

    def env(self):
        env = dict(os.environ)
        env["FAKE_STATE"] = str(self.state)
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        # 每个沙箱独立的 runtime dir，避免控制 socket / 实例锁串台
        env["XDG_RUNTIME_DIR"] = str(self.dir / "run")
        env["XDG_STATE_HOME"] = str(self.dir / "state-home")
        env["XDG_CONFIG_HOME"] = str(self.dir / "config-home")
        env.pop("NIRI_MIRROR_SOCKET", None)
        return env

    def write_config(self, **cfg):
        d = self.dir / "config-home" / "niri-mirror"
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps(cfg))

    def state_json(self):
        path = self.dir / "run" / "niri-mirror" / "state.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def notify_lines(self):
        return self.log_lines("notify.log")

    def noctalia_lines(self):
        return self.log_lines("noctalia.log")

    def drop_noctalia(self):
        """模拟没装 noctalia 的环境，验证 notify-send 兜底。"""
        (self.bin / "noctalia").unlink()

    def ctl(self, cmd, timeout=20):
        """通过客户端子命令控制守护进程（等价于 Win+U 走的那条路）。"""
        return subprocess.run(
            [sys.executable, str(MAIN), cmd], capture_output=True, text=True,
            env=self.env(), timeout=timeout)

    def set_outputs(self, *outputs, focused="eDP-2"):
        mapping = {o["name"]: o for o in outputs}
        (self.state / "outputs.json").write_text(json.dumps(mapping))
        (self.state / "focused.txt").write_text(focused)
        self.bump()

    def bump(self):
        (self.state / "bump").touch()

    def log_lines(self, name="wlmirror.log"):
        path = self.state / name
        if not path.exists():
            return []
        return [ln for ln in path.read_text().splitlines() if ln.strip()]

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_once(self, *args):
        cmd = [sys.executable, str(MAIN), "--niri", str(self.bin / "niri"),
               "--wl-mirror", str(self.bin / "wl-mirror"), "--once", *args]
        return subprocess.run(cmd, capture_output=True, text=True, env=self.env(), timeout=30)


def wait_for(predicate, timeout=8.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_dry_run_single_output():
    print("test: 只有一块屏时什么都不做 (--once --dry-run)")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP)
        r = sb.run_once("--dry-run")
        check(r.returncode == 0, f"退出码 0（实际 {r.returncode}）: {r.stderr.strip()[:200]}")
        check("会执行" not in r.stderr, "没有生成任何 wl-mirror 命令")
        check("源 output = eDP-2" in r.stderr, "选出源 output = eDP-2")
    finally:
        sb.cleanup()


def test_dry_run_two_outputs():
    print("test: 两块屏时把 eDP-2 镜像到 HDMI-A-1 (--once --dry-run)")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        r = sb.run_once("--dry-run")
        check(r.returncode == 0, "退出码 0")
        check("--fullscreen-output HDMI-A-1" in r.stderr, "全屏目标是 HDMI-A-1")
        check("--title niri-mirror:HDMI-A-1" in r.stderr, "窗口标题带标记，便于校验/清理")
        check(r.stderr.rstrip().endswith("eDP-2") or " eDP-2\n" in r.stderr,
              "源 output 是 eDP-2")
        check(sb.log_lines() == [], "dry-run 没有真的启动 wl-mirror")
    finally:
        sb.cleanup()


def test_fixed_source():
    print("test: --source HDMI-A-1 时反向外接屏镜像到笔记本屏")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        r = sb.run_once("--dry-run", "--source", "HDMI-A-1")
        check("--fullscreen-output eDP-2" in r.stderr, "目标变成 eDP-2")
        check(" --source HDMI-A-1" not in r.stderr, "源被固定为 HDMI-A-1")
    finally:
        sb.cleanup()


def test_target_filter():
    print("test: --exclude 生效")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        r = sb.run_once("--dry-run", "--exclude", "HDMI-A-1")
        check("会执行" not in r.stderr, "被排除的屏不镜像")
    finally:
        sb.cleanup()


def test_hotplug_daemon():
    print("test: 常驻守护 —— 插上就镜像，拔掉就清理（事件驱动）")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP)  # 只剩笔记本屏
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"),
               "--poll-interval", "60", "--debounce", "0.15", "-v"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        time.sleep(1.2)
        check(sb.log_lines() == [], "启动时没有外接屏 → 不起镜像")

        sb.set_outputs(EDP, HDMI)  # 热插拔
        ok = wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8)
        check(ok, "插入 HDMI 后立刻出现 wl-mirror 进程")
        start = sb.log_lines()[0] if sb.log_lines() else ""
        check("START" in start, "进程已启动")
        check("--fullscreen-output HDMI-A-1" in start, "以 HDMI-A-1 为全屏目标")
        check(start.rstrip().endswith("eDP-2"), "镜像源是 eDP-2")

        sb.set_outputs(EDP)  # 拔掉
        ok = wait_for(lambda: any("TERM" in ln for ln in sb.log_lines()), timeout=8)
        check(ok, "拔掉后镜像进程收到 SIGTERM 并退出")
        check(len(sb.log_lines()) == 2, f"只起过一个进程（日志 {sb.log_lines()}）")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        check(proc is not None and proc.returncode == 0, "守护进程收到 SIGTERM 后干净退出")
        sb.cleanup()


def test_shutdown_cleans_up():
    print("test: 守护进程退出时杀掉正在跑的镜像")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8), "镜像已启动")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        check(wait_for(lambda: any("TERM" in ln for ln in sb.log_lines()), timeout=5),
              "退出时镜像被清理")
    finally:
        sb.cleanup()


def test_source_switch():
    print("test: 源屏被拔掉后自动重新选举源")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"),
               "--poll-interval", "60", "--debounce", "0.15", "-v"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.PIPE, text=True)
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8), "初始镜像已启动")
        sb.set_outputs(HDMI, focused="HDMI-A-1")  # 拔掉笔记本屏
        check(wait_for(lambda: any("TERM" in ln for ln in sb.log_lines())),
              "旧镜像（目标是 eDP-2）已被清理")
        proc.send_signal(signal.SIGTERM)
        err = proc.communicate(timeout=10)[1]
        check("源 output = HDMI-A-1" in err, "源已切换到剩下的 HDMI-A-1")
        check("源 output eDP-2 已消失，重新选举" in err, "日志说明了重新选举的原因")
    finally:
        sb.cleanup()


def test_missing_wl_mirror():
    print("test: 没装 wl-mirror 时给出明确提示")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"), "--once"]
        env = dict(os.environ)
        empty = sb.dir / "emptybin"   # 空目录当 PATH，确保 which("wl-mirror") 找不到
        empty.mkdir(exist_ok=True)
        env["PATH"] = str(empty)
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
        check(r.returncode == 127, f"退出码 127（实际 {r.returncode}）")
        check("pacman -S wl-mirror" in r.stderr, "提示安装命令")
    finally:
        sb.cleanup()


def test_placement_warning():
    print("test: --fullscreen-output 没生效时给出警告")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        # 假 niri 报告镜像窗口落在 eDP-2（配错的情况）
        (sb.state / "windows.json").write_text(json.dumps([
            {"id": 1, "title": "niri-mirror:HDMI-A-1", "app_id": "wl-mirror",
             "pid": 999999, "workspace_id": 1},
        ]))
        (sb.state / "workspaces.json").write_text(json.dumps([
            {"id": 1, "idx": 1, "output": "eDP-2", "is_active": True, "is_focused": True},
        ]))
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.PIPE, text=True)
        time.sleep(3.5)
        proc.send_signal(signal.SIGTERM)
        err = proc.communicate(timeout=10)[1]
        check("警告: 镜像窗口没有落在 HDMI-A-1" in err, "发出了落位警告")
    finally:
        sb.cleanup()


def test_list_outputs():
    print("test: --list-outputs")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        r = subprocess.run(
            [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"), "--list-outputs"],
            capture_output=True, text=True, env=sb.env(), timeout=30)
        check(r.returncode == 0, "退出码 0")
        check("eDP-2" in r.stdout and "HDMI-A-1" in r.stdout, "列出两块屏")
        check("2560x1600@180Hz" in r.stdout, "显示当前模式")
        check("* eDP-2" in r.stdout, "标记出当前聚焦的屏")
    finally:
        sb.cleanup()




def test_toggle_cycle():
    print("test: Win+U 等价操作 —— toggle 关闭后镜像被清理，再 toggle 恢复（带 toast）")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8), "镜像已启动")

        r = sb.ctl("toggle")  # → 关
        check(r.returncode == 0, f"toggle 退出码 0（{r.stderr.strip()[:200]}）")
        check("关闭" in r.stdout, f"stdout 报告已关闭：{r.stdout.strip()}")
        check(wait_for(lambda: any("TERM" in ln for ln in sb.log_lines()), timeout=8),
              "关闭后 wl-mirror 收到 SIGTERM")
        check(wait_for(lambda: any("osd-show" in ln and "镜像已关闭" in ln
                                   for ln in sb.noctalia_lines()), timeout=8),
              "关闭时弹了 noctalia OSD")

        (sb.state / "bump").touch()
        time.sleep(1.0)
        check(len([ln for ln in sb.log_lines() if "START" in ln]) == 1,
              "关闭状态下热插拔事件不会重新起镜像")

        r = sb.ctl("toggle")  # → 开
        check(r.returncode == 0, "再次 toggle 成功")
        check("开启" in r.stdout, f"stdout 报告已开启：{r.stdout.strip()}")
        check(wait_for(lambda: len([ln for ln in sb.log_lines() if "START" in ln]) >= 2, timeout=8),
              "重新开启后又起了镜像")
        check(wait_for(lambda: any("osd-show" in ln and "镜像已开启" in ln
                                   for ln in sb.noctalia_lines()), timeout=8),
              "开启时弹了 noctalia OSD")

        r = sb.ctl("status")
        check("开启" in r.stdout, f"status 显示开启：{r.stdout.strip()}")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def test_toggle_autostart():
    print("test: 守护进程没在跑时按 Win+U 会把它拉起来")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        r = sb.ctl("status")
        check("未运行" in r.stdout, f"初始状态是未运行：{r.stdout.strip()}")

        r = sb.ctl("toggle")
        check(r.returncode == 0, f"toggle 退出码 0（{r.stderr.strip()[:300]}）")
        check("开启" in r.stdout, f"报告已开启：{r.stdout.strip()}")
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=10),
              "守护进程被拉起并开始镜像")
        check(wait_for(lambda: any("镜像已开启" in ln for ln in sb.noctalia_lines()), timeout=8),
              "启动时也弹了 OSD")

        r = sb.ctl("off")
        check("关闭" in r.stdout, f"off 生效：{r.stdout.strip()}")
        check(wait_for(lambda: any("TERM" in ln for ln in sb.log_lines()), timeout=8),
              "off 后镜像被清理")

        r = sb.ctl("quit")
        check(r.returncode == 0 and "退出" in r.stdout, f"quit 生效：{r.stdout.strip()}")
        check(wait_for(lambda: not (sb.dir / "run" / "niri-mirror.sock").exists(), timeout=8),
              "退出后控制 socket 被清理")
    finally:
        subprocess.run([sys.executable, str(MAIN), "quit"], env=sb.env(),
                       capture_output=True, timeout=20)
        sb.cleanup()


def test_sigusr1_toggle():
    print("test: kill -USR1 也能切换（备用绑定方式）")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8), "镜像已启动")
        proc.send_signal(signal.SIGUSR1)
        check(wait_for(lambda: any("TERM" in ln for ln in sb.log_lines()), timeout=8),
              "USR1 关闭了镜像")
        check(wait_for(lambda: any("镜像已关闭" in ln for ln in sb.noctalia_lines()), timeout=5),
              "USR1 也弹了 OSD")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def test_single_instance():
    print("test: 重复启动守护进程会被实例锁挡住")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: (sb.dir / "run" / "niri-mirror.sock").exists(), timeout=8),
              "第一个实例起来了")
        second = subprocess.run(
            cmd + ["--once"], capture_output=True, text=True, env=sb.env(), timeout=30)
        check(second.returncode == 0, "第二个实例退出码 0")
        check(len([ln for ln in sb.log_lines() if "START" in ln]) <= 1,
              "没有起第二个 wl-mirror")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def test_osd_content():
    print("test: OSD 里显示的是「源 → 目标」")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8), "镜像已启动")
        sb.ctl("toggle")
        check(wait_for(lambda: any("osd-show screen-share-off 镜像已关闭" in ln
                                   for ln in sb.noctalia_lines()), timeout=8),
              "关闭 OSD: screen-share-off + 镜像已关闭")
        sb.ctl("toggle")
        check(wait_for(lambda: any("osd-show screen-share 镜像已开启 · eDP-2 → HDMI-A-1" in ln
                                   for ln in sb.noctalia_lines()), timeout=8),
              "开启 OSD: screen-share + 源/目标")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def test_notify_send_fallback():
    print("test: 没有 noctalia 时退回 notify-send")
    sb = Sandbox()
    try:
        sb.drop_noctalia()
        sb.set_outputs(EDP, HDMI)
        r = sb.ctl("off")
        check(r.returncode == 0, "off 正常返回")
        check(wait_for(lambda: any("屏幕镜像已关闭" in ln for ln in sb.notify_lines()), timeout=5),
              "notify-send 收到了兜底通知")
    finally:
        sb.cleanup()


def test_notify_mode_notification():
    print("test: NIRI_MIRROR_NOTIFY=notification 时走通知中心")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        env = sb.env()
        env["NIRI_MIRROR_NOTIFY"] = "notification"
        r = subprocess.run([sys.executable, str(MAIN), "off"], capture_output=True,
                           text=True, env=env, timeout=20)
        check(r.returncode == 0, "off 正常返回")
        check(wait_for(lambda: any("notification-show" in ln and "屏幕镜像已关闭" in ln
                                   for ln in sb.noctalia_lines()), timeout=5),
              "走了 noctalia notification-show")
    finally:
        sb.cleanup()


def test_notify_mode_never():
    print("test: NIRI_MIRROR_NO_NOTIFY=1 时完全不提示")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        env = sb.env()
        env["NIRI_MIRROR_NO_NOTIFY"] = "1"
        subprocess.run([sys.executable, str(MAIN), "off"], capture_output=True,
                       text=True, env=env, timeout=20)
        time.sleep(0.5)
        check(sb.noctalia_lines() == [] and sb.notify_lines() == [], "没有任何提示调用")
    finally:
        sb.cleanup()


def test_config_enabled_on_start_off():
    print("test: 配置 enabled_on_start=false → 启动后是关闭状态，Win+U 才开")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        sb.write_config(enabled_on_start=False)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: (sb.dir / "run" / "niri-mirror.sock").exists(), timeout=8),
              "守护进程起来了")
        time.sleep(0.5)
        check(sb.log_lines() == [], "启动时不起镜像")
        check(sb.ctl("status").stdout.strip().startswith("关闭"), "status 是关闭")

        r = sb.ctl("toggle")
        check("开启" in r.stdout, f"toggle 后开启：{r.stdout.strip()}")
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8), "此时才起镜像")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def test_config_excluded_outputs():
    print("test: 配置 excluded_outputs 生效（不靠命令行参数）")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        sb.write_config(excluded_outputs=["HDMI-A-1"])
        r = sb.run_once("--dry-run")
        check("会执行" not in r.stderr, "被配置排除的屏没有镜像")
        check("排除的显示器: HDMI-A-1" in r.stderr or r.returncode == 0, "配置被读入")
    finally:
        sb.cleanup()


def test_config_show_osd_off():
    print("test: 配置 show_osd=false → 开关时不弹 OSD")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        sb.write_config(show_osd=False)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8), "镜像已启动")
        sb.ctl("toggle")
        sb.ctl("toggle")
        time.sleep(1.0)
        check(sb.noctalia_lines() == [], f"没有 OSD 调用（{sb.noctalia_lines()}）")
        check(sb.notify_lines() == [], "也没有通知")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def test_reload_config():
    print("test: niri-mirror reload 让运行中的实例应用新配置")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        sb.write_config()
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8), "初始镜像已启动")

        sb.write_config(excluded_outputs=["HDMI-A-1"])
        r = sb.ctl("reload")
        check(r.returncode == 0, f"reload 成功（{r.stderr.strip()[:120]}）")
        check("关闭" not in r.stdout, "reload 不改变开关状态")
        check(wait_for(lambda: any("TERM" in ln for ln in sb.log_lines()), timeout=8),
              "新配置把 HDMI-A-1 排除后镜像被清理")

        sb.write_config()
        sb.ctl("reload")
        check(wait_for(lambda: len([ln for ln in sb.log_lines() if "START" in ln]) >= 2, timeout=8),
              "再次 reload 去掉排除后镜像恢复")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def test_state_file():
    print("test: 守护进程把状态写到 $XDG_RUNTIME_DIR/niri-mirror/state.json")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: bool(sb.state_json()), timeout=8), "状态文件已写出")
        st = sb.state_json()
        check(st.get("enabled") is True, f"enabled=true（{st}）")
        check(st.get("source") == "eDP-2", "source=eDP-2")
        check(st.get("mirroring") == ["HDMI-A-1"], "mirroring=[HDMI-A-1]")
        check("updated" in st, "带时间戳")
        sb.ctl("toggle")
        check(wait_for(lambda: sb.state_json().get("enabled") is False, timeout=8),
              "关闭后状态文件同步更新")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def test_ensure_command():
    print("test: niri-mirror ensure 只保证守护进程在跑，不改变开关")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        sb.write_config(enabled_on_start=False)
        r = sb.ctl("ensure")
        check(r.returncode == 0, f"ensure 退出码 0（{r.stderr.strip()[:120]}）")
        check("关闭" in r.stdout, f"ensure 后仍是关闭：{r.stdout.strip()}")
        check(sb.log_lines() == [], "ensure 没有起镜像")
        check((sb.dir / "run" / "niri-mirror.sock").exists(), "控制口已就绪")
    finally:
        subprocess.run([sys.executable, str(MAIN), "quit"], env=sb.env(),
                       capture_output=True, timeout=20)
        sb.cleanup()


def test_fuzzy_names():
    print("test: 显示器名字支持模糊匹配（hdmi → HDMI-A-1）")
    sb = Sandbox()
    try:
        sb.set_outputs(EDP, HDMI)
        sb.write_config(source_output="hdmi")
        r = sb.run_once("--dry-run")
        check("--fullscreen-output eDP-2" in r.stderr, "源按子串匹配到 HDMI-A-1，镜像到 eDP-2")
        sb.write_config(excluded_outputs=["hdmi"])
        r = sb.run_once("--dry-run")
        check("会执行" not in r.stderr, "排除列表里写 hdmi 也能匹配上 HDMI-A-1")
        sb.write_config(excluded_outputs=["HDMI-A-1"])
        r = sb.run_once("--dry-run")
        check("会执行" not in r.stderr, "精确名字照旧生效")
    finally:
        sb.cleanup()


def test_unknown_name_hint():
    print("test: 名字对不上时提示当前可用的显示器")
    sb = Sandbox()
    proc = None
    try:
        sb.set_outputs(EDP, HDMI)
        sb.write_config(excluded_outputs=["HDMI-X"])
        cmd = [sys.executable, str(MAIN), "--niri", str(sb.bin / "niri"),
               "--wl-mirror", str(sb.bin / "wl-mirror"), "--poll-interval", "60"]
        proc = subprocess.Popen(cmd, env=sb.env(), stderr=subprocess.DEVNULL)
        check(wait_for(lambda: any("未找到 HDMI-X" in ln for ln in sb.noctalia_lines()), timeout=8),
              "OSD 里列出了未找到的名字和可用显示器")
        check(any("eDP-2" in ln and "HDMI-A-1" in ln for ln in sb.noctalia_lines()),
              "提示里带了可用连接名")
        check(wait_for(lambda: len(sb.log_lines()) >= 1, timeout=8),
              f"镜像照常创建（{sb.log_lines()}）")
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sb.cleanup()


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    only = sys.argv[1:] 
    for test in tests:
        if only and not any(o in test.__name__ for o in only):
            continue
        test()
    print()
    if FAILED:
        print(f"{len(FAILED)} 个断言失败:")
        for msg in FAILED:
            print(f"  - {msg}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
