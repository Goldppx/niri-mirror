# niri-mirror

让 niri 下**热插拔的外接显示器只显示主屏的镜像**，并用 `Win+U` 一键开/关（带 toast 通知）。

> niri 自己**不支持** output mirroring：26.04 的 output 配置里没有 `mirror` 节点，
> mutter DisplayConfig 那边也写死 `Mirroring is not yet supported`。
> 所以这里用 [wl-mirror](https://github.com/Ferdi265/wl-mirror)（走 wlr-screencopy /
> ext-image-copy-capture）把源 output 的画面渲染成一个窗口，再用
> `--fullscreen-output` 让它在目标屏上全屏 —— 全屏窗口会盖住该屏其它窗口，
> 视觉上就是「这块屏只显示镜像」。

## 依赖

```bash
sudo pacman -S wl-mirror      # 必需
# notify-send 已是 libnotify，一般都有；通知服务用 noctalia / dunst / mako 都行
```

## 安装

```bash
./install.sh        # 装到 ~/.local/bin，并写 Mod+U 绑定 + 自启项
niri msg action load-config-file
```

装完按 **Win+U** 就能开/关，提示走 **noctalia 的 OSD 弹窗**（图标 + 一行字，2 秒自动消失）：

- 开：`screen-share` 图标 + `镜像已开启 · eDP-2 → HDMI-A-1`（还没接屏时是 `镜像已开启 · 等待外接屏`）
- 关：`screen-share-off` 图标 + `镜像已关闭`
- 缺依赖：`alert-triangle` + `缺少 wl-mirror`

想改用别的提示渠道，设环境变量 `NIRI_MIRROR_NOTIFY`：

| 值 | 效果 |
|---|---|
| `auto`（默认） | 有 `noctalia` 就用 OSD，否则退回 `notify-send` |
| `osd` | `noctalia msg osd-show`（OSD 弹窗） |
| `notification` | `noctalia msg notification-show`（进通知中心/历史，受 DND 影响） |
| `never` | 静音（等价于 `NIRI_MIRROR_NO_NOTIFY=1`） |

```bash
NIRI_MIRROR_NOTIFY=notification niri-mirror toggle   # 临时改成通知中心
```

## Noctalia 插件（推荐用法）

[`Goldppx/noctalia-screen-mirror`](https://github.com/Goldppx/noctalia-screen-mirror) 里有配套的 Noctalia 插件：控制中心磁贴 +
可选栏挂件，**设置页里能配**「是否弹 OSD / 开机是否默认开启 / 排除哪些显示器 / 镜像源 /
缩放方式 / 终端程序 / 刷新间隔」。插件只是 UI，引擎仍是这里的守护进程。

显示器名字支持模糊匹配（`hdmi` → `HDMI-A-1`：精确 → 忽略大小写 → 唯一子串），
写错时会把当前可用的连接名列出来（OSD + 日志）。想先看名字用
`niri-mirror --list-outputs`。

```sh
noctalia msg plugins source add local-dev path /path/to/noctalia-plugins   # 只需一次
noctalia msg plugins enable goldesquemoon/screen-mirror
noctalia msg settings-open-plugin goldesquemoon/screen-mirror           # 打开设置页
```

然后 Settings → Control Center → Shortcuts 里加一块 "Screen Mirror"：

```toml
[[control_center.shortcuts]]
type = "goldesquemoon/screen-mirror:mirror"
```

数据流：设置页 → `~/.config/niri-mirror/config.json` →`niri-mirror reload` → 守护进程；
守护进程反过来把状态写到 `$XDG_RUNTIME_DIR/niri-mirror/state`（key=value）与
`state.json`，插件读它来更新磁贴/挂件。

## 配置文件

`~/.config/niri-mirror/config.json`（插件自动维护；也可以手写，Win+U 与自启都会读）：

```json
{
  "enabled_on_start": true,      // 启动时是否默认开启
  "show_osd": true,              // 开关时是否弹 OSD
  "feedback": "auto",            // auto | osd | notification | never
  "source_output": "auto",       // 或固定某个连接名（支持 hdmi 这样的模糊写法）
  "excluded_outputs": ["HDMI-A-1"],   // 列在这里的屏：接上也不创建镜像窗口
  "scaling": "fit",              // fit | cover | exact
  "hide_cursor": false
}
```

命令行参数（`--source/--exclude/--feedback/...`）优先于配置文件。

## 怎么工作

常驻守护进程（随 niri 自启）：

1. 用 `niri msg --json outputs` / `focused-output` 选举「源 output」——启动时聚焦的那块屏，之后粘住；
2. 其余活动 output 各跑一个 `wl-mirror --fullscreen-output <目标> --title niri-mirror:<目标> <源>`；
3. 监听 `niri msg event-stream`（niri 没有 `OutputsChanged` 事件，热插拔表现为
   `WorkspacesChanged`），另有定时轮询兜底；事件用 socketpair 唤醒，响应在百毫秒级；
4. 拔掉/关闭屏幕 → 杀掉对应 wl-mirror；源屏消失 → 重新选举；wl-mirror 崩溃 → 指数退避重试；
5. 控制口 `$XDG_RUNTIME_DIR/niri-mirror.sock`：`toggle`/`on`/`off`/`status`/`quit`；
   `kill -USR1` 也能切换（备用绑定）；开关提示走 noctalia OSD；
6. 退出时清理所有子进程，启动时回收上次异常退出留下的镜像窗口（按标题前缀识别）；
7. 实例锁 `$XDG_RUNTIME_DIR/niri-mirror.lock`，重复启动不会起两份。

守护进程没在跑时按 `Win+U`，客户端会自动把它拉起来（`setsid` 脱离终端，日志写
`~/.local/state/niri-mirror/daemon.log`）。

## 常用命令

```bash
niri-mirror status            # 看当前状态
niri-mirror toggle            # 开/关（Win+U 就是这条）
niri-mirror on / off
niri-mirror quit              # 停掉守护进程
niri-mirror --list-outputs    # 列显示器（带 * 的是当前聚焦屏）

niri-mirror --source eDP-2                 # 固定源为笔记本屏（默认 auto）
niri-mirror --exclude HDMI-A-1             # 某块屏永不镜像
niri-mirror --targets HDMI-A-1             # 只镜像到指定屏
niri-mirror --backend screencopy           # 强制走 wlr-screencopy（默认 auto）
niri-mirror --scaling cover --hide-cursor  # 铺满裁切 / 镜像里不画鼠标
niri-mirror --once --dry-run               # 只打印打算执行的命令

niri-mirror ensure            # 只保证守护进程在跑（不改开关，插件用这个）
niri-mirror reload            # 重新读 ~/.config/niri-mirror/config.json 并应用
niri-mirror --feedback never toggle         # 本次不提示

NIRI_MIRROR_NO_NOTIFY=1 niri-mirror toggle # 临时关掉提示
```

## 限制（都是 wl-mirror 方案的固有代价，先知道不踩坑）

- 有额外延迟：走捕获 → 合成 → 再上屏，180Hz 源降到 60Hz 输出，鼠标会有拖影；不适合游戏。
- 全屏直通（direct scanout）、HDR 用不上。
- bar（noctalia 的顶栏）之类的 layer-shell 面板仍会画在镜像之上。
- 镜像窗口是普通窗口：手动关闭它 / 退出全屏，守护进程会在下一次事件或轮询时补上；
  想彻底关掉就按 `Win+U`。
- 演示建议：正式场合直接 `Win+U` 关掉镜像，把窗口 `move-window-to-monitor-right` 丢到外接屏全屏，画质与延迟都是原生的。

## 排错

```bash
tail -f ~/.local/state/niri-mirror/daemon.log   # 守护进程日志
niri-mirror -v toggle                           # 本次操作的详细日志
niri msg outputs                                # 确认 niri 认得这块屏
```

日志里出现 `警告: 镜像窗口没有落在 <目标>`：说明该 niri 版本没按 `--fullscreen-output`
摆放窗口，可以加窗口规则兜底：

```kdl
window-rule {
    match app-id="wl-mirror"
    open-fullscreen true
}
```

## 测试

```bash
python3 tests/run_tests.py        # 用假 niri / 假 wl-mirror 跑热插拔、toggle、信号、实例锁等场景
```
