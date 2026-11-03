#!/usr/bin/env bash
# 把 niri-mirror 装到 ~/.local/bin，并（可选）把自启/绑定写进 niri 配置。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="${HOME}/.local/bin"
niri_cfg="${HOME}/.config/niri/cfg"

mkdir -p "$bin" "${HOME}/.local/state/niri-mirror"
install -m 755 "$here/niri_mirror.py" "$bin/niri-mirror"
echo "已安装: $bin/niri-mirror"

if ! command -v wl-mirror >/dev/null; then
    echo "⚠ 还缺 wl-mirror，请运行: sudo pacman -S wl-mirror" >&2
fi

if [[ -d "$niri_cfg" ]]; then
    if ! grep -q "niri-mirror toggle" "$niri_cfg/keybinds.kdl" 2>/dev/null; then
        cp -n "$niri_cfg/keybinds.kdl" "$niri_cfg/keybinds.kdl.bak.$(date +%Y%m%d-%H%M%S)"
        python3 - "$niri_cfg/keybinds.kdl" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text()
bind = ('    // ─── Screen Mirroring ───\n'
        '    Mod+U                               hotkey-overlay-title="Toggle Screen Mirror" '
        '{ spawn-sh "~/.local/bin/niri-mirror toggle"; }\n\n')
marker = "    // ─── Emergency Escape Key ───"
s = s.replace(marker, bind + marker, 1) if marker in s else s.replace("\n}\n", "\n" + bind + "}\n")
p.write_text(s)
print("已写入 Mod+U 绑定")
PY
    else
        echo "keybinds.kdl 里已有 Mod+U 绑定，跳过"
    fi

    if ! grep -q "niri-mirror --log-file" "$niri_cfg/autostart.kdl" 2>/dev/null; then
        cp -n "$niri_cfg/autostart.kdl" "$niri_cfg/autostart.kdl.bak.$(date +%Y%m%d-%H%M%S)"
        cat >> "$niri_cfg/autostart.kdl" <<'KDL'

    // 屏幕镜像守护：热插拔的外接屏自动全屏镜像主屏（Win+U 开关）
    spawn-sh-at-startup "exec ~/.local/bin/niri-mirror --log-file ~/.local/state/niri-mirror/daemon.log"
KDL
        echo "已写入 spawn-sh-at-startup"
    else
        echo "autostart.kdl 里已有自启项，跳过"
    fi

    niri msg action load-config-file 2>/dev/null || true
fi

echo "完成。按 Win+U 开/关镜像（会弹 toast），日志: ~/.local/state/niri-mirror/daemon.log"
