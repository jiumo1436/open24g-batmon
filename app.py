# -*- coding: utf-8 -*-
r"""
2.4G 无线设备电量监控（Windows 桌面版）

直接对本机的 2.4G 接收器 / 设备发 HID 查询帧读电量：不装驱动、不开厂商软件、
不读蓝牙、不联网。原生 Qt 窗口，无控制台，打开即显示。

代码分四层，从下往上：

  1) 传输层      HID（SetupAPI 枚举 + 重叠 IO）与串口（COM 口，kernel32 直调）
                 两条通道对上层完全等价，只负责「字节怎么进出」
  2) 读取层      通用解释器：按一份 JSON/YAML「协议文件」逐条执行，新设备不用改代码
  3) 设备表      程序不内置任何设备；所有设备都来自外部配置（导入后写进 devices.json）
  4) 界面        卡片列表 / 协议一览 / 导入导出 / 捕获报文

加一台新设备的标准流程：

  ① 点顶部「捕获报文」：拿到 VID / PID / usagePage 和真实收发帧；
  ② 照「协议制作教程」写成一份配置，用「＋ 导入设备」导入并点「测试读取」。

本文件是「纯净版」：不含任何设备名 / VID / PID / 帧头 / 换算公式等设备协议参数，
所有设备的识别与解析规则都写在外部配置文件里，需要时自行导入。
协议字段的含义写在「通用协议引擎」那一段注释里，完整说明见 docs/config-schema.md。
"""

import os
import sys
import time
import json
import math
import ast
import ctypes
import threading
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QDialog,
    QPlainTextEdit, QFrame, QSizePolicy, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QGraphicsDropShadowEffect,
    QLineEdit, QFileDialog, QSystemTrayIcon, QMenu,
)
from PySide6.QtCore import (Qt, QTimer, QThread, Signal, QRectF, QPointF, QPoint,
                            QSize, QRect, QEvent, QObject, QPropertyAnimation,
                            QByteArray)
from PySide6.QtGui import (QFont, QCloseEvent, QPainter, QColor, QIcon, QPixmap,
                           QPen, QPainterPath)
from PySide6.QtSvg import QSvgRenderer

# ------------------------- 路径 -------------------------
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CACHE_PATH = os.path.join(APP_DIR, "cache.json")

# 协议库：导入的协议、导出的协议都存这里，一台设备一个 .json，文件名就是设备名。
PACK_DIR = os.path.join(APP_DIR, "协议库")

# configs：随软件分发的「待导入」配置目录，可以按厂商建子文件夹放一大批设备配置。
# 程序不会自动把这些文件当成已监控设备，只在「导入设备」里浏览 / 勾选 ——
# 这样社区拉下来几十份配置也不会把主界面塞满。
CONFIGS_DIR = os.path.join(APP_DIR, "configs")


def ensure_configs_dir():
    """确保 configs 目录存在（没有就建），返回路径。"""
    try:
        os.makedirs(CONFIGS_DIR, exist_ok=True)
    except Exception as e:
        log_err("创建 configs 目录失败: " + repr(e))
    return CONFIGS_DIR


def ensure_pack_dir():
    """确保协议库目录存在（没有就建），返回路径。"""
    try:
        os.makedirs(PACK_DIR, exist_ok=True)
    except Exception as e:
        log_err("创建协议库目录失败: " + repr(e))
    return PACK_DIR


# ------------------------- 本机用户偏好 -------------------------
# 用户自己的界面/功能偏好：颜色阈值、开机自启动、托盘、低电量提醒。
# 这是「这台电脑」的私有配置，跟设备协议无关 —— 协议在 协议库/ 里，两者严格分开。
USER_SETTINGS_PATH = os.path.join(APP_DIR, "user_settings.json")

USER_DEFAULTS = {
    "autostart": False,       # 开机自启动（写 HKCU 启动项，--tray 静默进托盘）
    "tray": True,             # 启动托盘图标（默认开：关窗口后还能从托盘找回）
    "low_alert": False,       # 低电量提醒
    "low_alert_pct": 20,      # 低于这个电量提醒
    "green_pct": 70,          # ≥ 这个值显示绿色
    "red_pct": 30,            # < 这个值显示红色，中间是橙色
    "serial_port": "",        # 串口接收器手动选定的端口（空 = 自动尝试全部串口）
}

USER = dict(USER_DEFAULTS)

# 电量语义色：克制、接近 Windows 系统色，不再用高饱和荧光色
COLOR_RED = "#A6463B"        # 低电量（低饱和红）
COLOR_ORANGE = "#A8752E"     # 中等（低饱和琥珀）
COLOR_GREEN = "#4F7A5B"      # 充足（低饱和绿）
COLOR_NONE = "#A6A6A6"       # 无读数（中性灰）
# 外 → 内 三层不同灰度：16px 下即使「全部离线」也能一眼分出三层，
# 且每一档在浅色 / 深色任务栏上都保持可辨识（不靠白底板）。
TRAY_TRACKS = ("#9A9A9A", "#C4C4C4", "#767676")

# 黑色简约风主色：近黑文字 / 中性灰底
INK = "#1A1A1A"              # 主文字 / 主操作（近黑）
INK_SOFT = "#5C5C5C"        # 次要文字
INK_FAINT = "#8A8A8A"       # 标签 / 占位
LINE = "#E6E6E6"            # 分隔线 / 卡片描边
LINE_STRONG = "#D0D0D0"     # 悬停描边
PANEL = "#FFFFFF"           # 卡片 / 弹窗底
SURFACE = "#F3F3F3"         # 窗口底（中性浅灰，贴近 Windows 11）
HOVER = "#F5F5F5"           # 幽灵按钮悬停底


def _as_bool(v, default):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on", "是", "开"):
            return True
        if s in ("0", "false", "no", "off", "否", "关"):
            return False
    return default


def _as_int(v, default):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def norm_color_pair(red, green):
    """把两个阈值收进 0 < 红 < 绿 < 100；越界/顺序颠倒就整体退回默认值。"""
    red = max(1, min(98, _as_int(red, USER_DEFAULTS["red_pct"])))
    green = max(2, min(99, _as_int(green, USER_DEFAULTS["green_pct"])))
    if red >= green:
        return USER_DEFAULTS["red_pct"], USER_DEFAULTS["green_pct"]
    return red, green


def load_user_settings():
    """启动时读 user_settings.json：缺字段补默认，文件坏了也照常启动并重建。"""
    raw = {}
    if os.path.exists(USER_SETTINGS_PATH):
        try:
            with open(USER_SETTINGS_PATH, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
        except Exception as e:
            log_err("user_settings.json 读取失败，改用默认值：" + repr(e))
            raw = {}
    if not isinstance(raw, dict):
        log_err("user_settings.json 格式不对（不是对象），改用默认值")
        raw = {}

    s = dict(USER_DEFAULTS)
    s["autostart"] = _as_bool(raw.get("autostart"), USER_DEFAULTS["autostart"])
    s["tray"] = _as_bool(raw.get("tray"), USER_DEFAULTS["tray"])
    s["low_alert"] = _as_bool(raw.get("low_alert"), USER_DEFAULTS["low_alert"])
    s["low_alert_pct"] = max(1, min(99, _as_int(
        raw.get("low_alert_pct"), USER_DEFAULTS["low_alert_pct"])))
    s["red_pct"] = _as_int(raw.get("red_pct"), USER_DEFAULTS["red_pct"])
    s["green_pct"] = _as_int(raw.get("green_pct"), USER_DEFAULTS["green_pct"])
    s["red_pct"], s["green_pct"] = norm_color_pair(s["red_pct"], s["green_pct"])
    USER.update(s)
    # 文件不存在 / 缺字段 / 写过乱码：立刻落一份完整的，用户随时能看到、能备份
    if raw != s:
        save_user_settings()
    return USER


def save_user_settings():
    """任何一项偏好改动都立刻落盘。写失败只记日志，不打断界面操作。"""
    try:
        with open(USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({k: USER[k] for k in USER_DEFAULTS}, f,
                      ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log_err("写 user_settings.json 失败: " + repr(e))
        return False


def pct_color(pct):
    """全局电量配色：低于红阈值=红，红~绿之间=橙，达到绿阈值=绿。"""
    if pct is None:
        return COLOR_NONE
    try:
        pct = int(pct)
    except Exception:
        return COLOR_NONE
    if pct < USER["red_pct"]:
        return COLOR_RED
    if pct < USER["green_pct"]:
        return COLOR_ORANGE
    return COLOR_GREEN


# ------------------------- 单实例 -------------------------
# 双击两次 / 开机自启动 + 手动再打开，都不能出现第二个托盘图标。
SINGLE_KEY = "dianliang-monitor-open24g"
_SINGLE_SERVER = None
_MAIN_WIN = None


def _on_second_instance():
    """第二个实例连进来：它想「打开界面」就把已有窗口叫出来，然后自己退出。"""
    global _SINGLE_SERVER
    srv = _SINGLE_SERVER
    while srv is not None and srv.hasPendingConnections():
        c = srv.nextPendingConnection()
        try:
            c.waitForReadyRead(200)
            data = bytes(c.readAll())
            if b"show" in data and _MAIN_WIN is not None:
                _MAIN_WIN.show_from_tray()
        except Exception as e:
            log_err("处理第二个实例失败: " + repr(e))
        try:
            c.disconnectFromServer()
            c.deleteLater()
        except Exception:
            pass


def claim_single_instance(silent=False):
    """第一个实例返回 True（并开始监听）；已有实例返回 False（顺手通知它显示窗口）。"""
    global _SINGLE_SERVER
    try:
        from PySide6.QtNetwork import QLocalSocket, QLocalServer
    except Exception as e:
        log_err("单实例检查跳过（QtNetwork 不可用）：" + repr(e))
        return True
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_KEY)
    if sock.waitForConnected(300):
        try:
            sock.write(b"noop" if silent else b"show")
            sock.flush()
            sock.waitForBytesWritten(300)
        except Exception:
            pass
        sock.disconnectFromServer()
        return False
    try:
        QLocalServer.removeServer(SINGLE_KEY)      # 清掉上次崩溃留下的名字
    except Exception:
        pass
    _SINGLE_SERVER = QLocalServer()
    _SINGLE_SERVER.newConnection.connect(_on_second_instance)
    if not _SINGLE_SERVER.listen(SINGLE_KEY):
        log_err("单实例监听失败：" + _SINGLE_SERVER.errorString())
    return True


# ------------------------- 开机自启动（HKCU 启动项） -------------------------
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "设备电量监控"


def autostart_command():
    """开机时执行的命令行：带 --tray，只进托盘、不弹主窗口。"""
    if getattr(sys, "frozen", False):
        return f'"{os.path.abspath(sys.executable)}" --tray'
    exe = os.path.abspath(sys.executable)
    base = os.path.dirname(exe)
    for name in ("pythonw.exe", "python.exe"):     # 优先无控制台版本
        cand = os.path.join(base, name)
        if os.path.exists(cand):
            exe = cand
            break
    return f'"{exe}" "{os.path.abspath(__file__)}" --tray'


def autostart_registered():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as k:
            winreg.QueryValueEx(k, RUN_VALUE_NAME)
        return True
    except Exception:
        return False


def set_autostart(on):
    """写/删用户启动项。返回 (成功?, 错误说明)。"""
    try:
        import winreg
    except Exception:
        return False, "当前系统不支持注册表启动项（仅 Windows）"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as k:
            if on:
                winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as e:
        log_err("写开机启动项失败: " + repr(e))
        return False, str(e)


def sync_autostart():
    """启动时让「设置里的开关」与「系统里真实存在的启动项」保持一致。
    软件被挪过目录、启动项被安全软件清掉，都能在这里自愈。"""
    want = bool(USER.get("autostart"))
    has = autostart_registered()
    if want:
        ok, err = set_autostart(True)      # 每次重写一遍，路径跟着 exe 走
        if not ok:
            log_err("自启动项同步失败：" + err)
            USER["autostart"] = False
            save_user_settings()
    elif has:
        set_autostart(False)


# 自动刷新的时间档位：(秒, 文案)。0 = 不自动刷新。
# 想改档位只改这一张表，界面下拉框会自动跟着变。
REFRESH_CHOICES = [
    (5,   "每 5 秒"),
    (10,  "每 10 秒"),
    (15,  "每 15 秒"),
    (20,  "每 20 秒"),
    (30,  "每 30 秒"),
    (60,  "每 1 分钟"),
    (120, "每 2 分钟"),
    (300, "每 5 分钟"),
    (600, "每 10 分钟"),
    (0,   "不自动刷新"),
]
REFRESH_DEFAULT_S = 20

# 运行时设置（会跟读数一起写进 cache.json，下次启动沿用）
SETTINGS = {"interval": REFRESH_DEFAULT_S}


def res_path(name):
    """打包后资源在 sys._MEIPASS，开发时就在脚本目录。"""
    base = getattr(sys, "_MEIPASS", APP_DIR)
    return os.path.join(base, name)


ICON_PATH = res_path("icon.ico")

# ------------------------- 字体 -------------------------
# 全软件统一一套字体：中文用微软雅黑 UI，没有就退到雅黑/苹方，西文退 Segoe UI。
# 以前只在 QSS 里写了 font-family，QMessageBox 这类由 Qt 自己 setFont 的窗口
# 拿的是 QApplication 的默认字体（Segoe UI 没有中文字形 → 回落成宋体，看着很割裂），
# 所以 main() 里还要 app.setFont() 一次，两处都指向同一份列表。
FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI"]
FONT_CSS = '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif'
MONO_CSS = '"Cascadia Mono", Consolas, "Microsoft YaHei UI", monospace'


def base_font():
    """QApplication 的基准字体（字号不动，各处 QSS 已经按 px 调过）。"""
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    return f


# ⚠️ 关键：--windowed 打包后没有控制台，sys.stdout / sys.stderr 都是 None。
# 任何 sys.stderr.write(...) 都会抛 AttributeError；如果这行发生在刷新线程里，
# 线程会直接崩掉、finished 信号永远发不出来，界面就会一直卡在"正在读取设备电量…"。
# 这里统一兜底成 devnull，彻底断掉这条崩溃路径。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


LOG_MAX_BYTES = 1 * 1024 * 1024      # 单个日志 1MB 上限


def log_err(msg):
    """把错误信息同时写到 stderr 和 APP_DIR/error.log，便于排查（无控制台窗口时也能留痕）。
    超过 1MB 自动滚存为 error.log.1，避免长期运行把磁盘写满。
    绝不向外抛异常 —— 日志失败不能影响主流程。"""
    path = os.path.join(APP_DIR, "error.log")
    try:
        if os.path.getsize(path) > LOG_MAX_BYTES:
            # 只保留一代旧日志：滚动时直接覆盖，磁盘占用固定为 2MB 以内
            os.replace(path, path + ".1")
    except OSError:
        pass
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
    except Exception:
        pass


_CRASH_LOG = None


def _install_crash_handlers():
    """捕获「进程级崩溃」留痕。

    Python 异常（哪怕在线程里）会走 log_err，但 0xc0000409 / 访问冲突这类
    fail-fast 崩溃根本不给解释器抛异常的机会，进程直接没了，error.log 里
    一个字都不会留。faulthandler 挂在进程上，崩溃瞬间能把各线程的 C/Python
    栈写进 crash.log，是排查这类「无声退出」唯一有效的线索。

    返回 True 表示已启用；任何一步失败都不影响主流程。
    """
    global _CRASH_LOG
    try:
        import faulthandler
        path = os.path.join(APP_DIR, "crash.log")
        try:
            if os.path.getsize(path) > LOG_MAX_BYTES:
                # 与 error.log 同样只留一代，磁盘占用封顶 2MB
                os.replace(path, path + ".1")
        except OSError:
            pass
        _CRASH_LOG = open(path, "a", encoding="utf-8", errors="replace")
        faulthandler.enable(file=_CRASH_LOG, all_threads=True)
        _CRASH_LOG.write(
            "\n===== 启动 %s  pid=%s =====\n"
            % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid())
        )
        _CRASH_LOG.flush()
        return True
    except Exception:
        return False


def _thread_excepthook(args):
    """线程里没被接住的异常：别静默，记一行（Python 3.8+ 才有这个钩子）。"""
    try:
        log_err(
            "线程未捕获异常 %s: %r\n%s"
            % (getattr(args.thread, "name", "?"), args.exc_value,
               "".join(__import__("traceback").format_exception(
                   args.exc_type, args.exc_value, args.exc_traceback)))
        )
    except Exception:
        pass


def load_cache():
    """上次的读数：让软件一打开就有数字，而不是先空一片再慢慢刷。"""
    try:
        import json
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(results, ok_at=None):
    """把这一轮的读数存下来。ok_at: key -> 上次真读到电量的时间戳。

    `_ok_at` 必须一起存：设备打盹时这一轮没数字，但「十分钟前真读到 91%」
    和「从来不知道」是两回事 —— 下次启动要靠它判断还能不能把旧数字标成「上次读数」。
    """
    try:
        import json
        slim = {}
        for k, v in results.items():
            if not isinstance(v, dict):
                continue
            slim[k] = {kk: v[kk] for kk in ("state", "battery", "charging", "approx", "level")
                       if kk in v}
            if v.get("battery") is not None and ok_at and k in ok_at:
                slim[k]["_ok_at"] = round(float(ok_at[k]), 1)
        # 记住用户选的自动刷新间隔
        try:
            slim["_interval"] = int(SETTINGS.get("interval", REFRESH_DEFAULT_S))
        except Exception:
            pass
        # 记住被右键移除的内置设备，重启后不要再冒出来
        try:
            if HIDDEN_BUILTIN:
                slim["_hidden"] = list(HIDDEN_BUILTIN)
        except Exception:
            pass
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False)
    except Exception as e:
        log_err("save_cache 失败: " + repr(e))

# 设备类型 → 图标 / 中文名 / 图标底色 / 图标前景
# 设备类型 → 矢量图标名 / 中文名 / 图标底色 / 图标前景（中性黑灰，不再用彩色圆圈）
TYPES = {
    "keyboard":  ("keyboard", "键盘", "#F0F0F0", INK),
    "mouse":     ("mouse", "鼠标", "#F0F0F0", INK),
    "headphone": ("headphone", "耳机", "#F0F0F0", INK),
    "gamepad":   ("gamepad", "手柄", "#F0F0F0", INK),
    "other":     ("plug", "设备", "#F0F0F0", INK),
}

# ===========================================================================
#  设备表（纯净版：为空）
#  ---------------------------------------------------------------------------
#  本纯净版不内置任何设备。所有设备都来自外部 JSON 配置文件：
#  用「＋ 导入设备」导入的协议会写进 devices.json，下次启动自动加载。
#  界面其余部分全部由这张表推导：卡片、台数、协议一览。
#
#  换成自己的设备：照「协议制作教程」写成 JSON，用「＋ 导入设备」导入即可。
BUILTIN_DEVICES = []

# 运行期设备表：导入 / 移除都就地改它，界面所有地方都以它为准。
PROFILES = [{"key": d["key"], "name": d["name"], "type": d["type"],
             "wire": d["wire"], "protocol": d["protocol"]}
            for d in BUILTIN_DEVICES]

# 内置设备的原始清单（PROFILES 会被导入/移除就地改动，恢复内置设备时用它对照）
DEFAULT_PROFILES = [dict(p) for p in PROFILES]

# key → 完整设备定义（读取器、识别条件、协议说明都在这）
PROFILE_BY_KEY = {d["key"]: d for d in BUILTIN_DEVICES}

# 顶部「协议一览」：key → 六行说明
PROTOCOL_INFO = {d["key"]: d["info"] for d in BUILTIN_DEVICES}

# 捕获报文要关注的 VID（内置设备的全部 VID）
ALL_VIDS = {v for d in BUILTIN_DEVICES for (v, _p) in d["pairs"]}

# 只靠产品名识别的关键字（没有设备用到时为空，捕获窗口照样能用）
KEYWORDS = tuple(k for d in BUILTIN_DEVICES for k in d.get("keywords", ()))

# 设备台数：一律由 PROFILES 推导，界面上所有"X 台"都读它。
# 以后在 PROFILES 里加一台 / 删一台（或导入协议文件），标题、说明、协议清单都会跟着变。
N_DEVICES = len(PROFILES)


def sync_device_count():
    """设备表变动后（导入/移除）重新算台数，返回新值。"""
    global N_DEVICES
    N_DEVICES = len(PROFILES)
    return N_DEVICES



# =========================================================
#  Windows HID 直连（ctypes 调系统 hid.dll / setupapi.dll / kernel32.dll）
#  结构体字段严格对齐 Windows SDK：HIDD_ATTRIBUTES / HIDP_CAPS 中
#  VendorID/Usage/UsagePage/长度 均为 USHORT(2字节)，不是 DWORD！
#  HidP_GetCaps 返回 NTSTATUS，成功码为 0x00110000（不是 0）。
# =========================================================
try:
    _K = ctypes.windll.kernel32
    _HID = ctypes.windll.hid
    _SETUP = ctypes.windll.setupapi
    _WIN_HID = True
except Exception:
    _WIN_HID = False

if _WIN_HID:
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_SHARE_READ = 0x1
    _FILE_SHARE_WRITE = 0x2
    _OPEN_EXISTING = 3
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _DIGCF_PRESENT = 0x2
    _DIGCF_DEVICEINTERFACE = 0x10
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _ERROR_IO_PENDING = 997
    _WAIT_OBJECT_0 = 0
    _HIDP_STATUS_SUCCESS = 0x00110000

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _GUID),
                    ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

    class _SP_DEV_INTERFACE_DETAIL(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("DevicePath", wintypes.WCHAR * 1024)]

    class _HIDD_ATTR(ctypes.Structure):
        _fields_ = [("Size", wintypes.DWORD), ("VendorID", wintypes.USHORT),
                    ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]

    class _HIDP_CAPS(ctypes.Structure):
        _fields_ = [
            ("Usage", wintypes.USHORT),
            ("UsagePage", wintypes.USHORT),
            ("InputReportByteLength", wintypes.USHORT),
            ("OutputReportByteLength", wintypes.USHORT),
            ("FeatureReportByteLength", wintypes.USHORT),
            ("Reserved", wintypes.USHORT * 17),
            ("NumberLinkCollectionNodes", wintypes.USHORT),
            ("NumberInputButtonCaps", wintypes.USHORT),
            ("NumberInputValueCaps", wintypes.USHORT),
            ("NumberInputDataIndices", wintypes.USHORT),
            ("NumberOutputButtonCaps", wintypes.USHORT),
            ("NumberOutputValueCaps", wintypes.USHORT),
            ("NumberOutputDataIndices", wintypes.USHORT),
            ("NumberFeatureButtonCaps", wintypes.USHORT),
            ("NumberFeatureValueCaps", wintypes.USHORT),
            ("NumberFeatureDataIndices", wintypes.USHORT),
        ]

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    # ---- 函数原型 ----
    _HID.HidD_GetHidGuid.restype = None
    _HID.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
    _HID.HidD_GetAttributes.restype = ctypes.c_byte
    _HID.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTR)]
    _HID.HidD_GetPreparsedData.restype = ctypes.c_byte
    _HID.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _HID.HidD_FreePreparsedData.restype = ctypes.c_byte
    _HID.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _HID.HidP_GetCaps.restype = ctypes.c_int
    _HID.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
    _HID.HidD_GetProductString.restype = ctypes.c_byte
    _HID.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    _HID.HidD_GetFeature.restype = ctypes.c_byte
    _HID.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    _HID.HidD_SetOutputReport.restype = ctypes.c_byte
    _HID.HidD_SetOutputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    _HID.HidD_GetInputReport.restype = ctypes.c_byte
    _HID.HidD_GetInputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    _SETUP.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    _SETUP.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), ctypes.c_wchar_p,
                                            wintypes.HANDLE, wintypes.DWORD]
    _SETUP.SetupDiEnumDeviceInterfaces.restype = ctypes.c_int
    _SETUP.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                                  ctypes.POINTER(_GUID), wintypes.DWORD,
                                                  ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
    _SETUP.SetupDiGetDeviceInterfaceDetailW.restype = ctypes.c_int
    _SETUP.SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE,
                                                       ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
                                                       ctypes.POINTER(_SP_DEV_INTERFACE_DETAIL),
                                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                       ctypes.c_void_p]
    _SETUP.SetupDiDestroyDeviceInfoList.restype = ctypes.c_int
    _SETUP.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    _K.CreateFileW.restype = wintypes.HANDLE
    _K.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _K.CloseHandle.restype = ctypes.c_int
    _K.CloseHandle.argtypes = [wintypes.HANDLE]
    _K.GetLastError.restype = wintypes.DWORD
    _K.GetLastError.argtypes = []
    _K.ReadFile.restype = ctypes.c_int
    _K.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED)]
    _K.WriteFile.restype = ctypes.c_int
    _K.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED)]
    _K.CreateEventW.restype = wintypes.HANDLE
    _K.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _K.WaitForSingleObject.restype = wintypes.DWORD
    _K.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _K.GetOverlappedResult.restype = ctypes.c_int
    _K.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED),
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_int]
    _K.CancelIo.restype = ctypes.c_int
    _K.CancelIo.argtypes = [wintypes.HANDLE]

    def _open_meta(path):
        """仅读元数据：访问权限=0（最不易被系统拒绝）。"""
        h = _K.CreateFileW(ctypes.c_wchar_p(path), 0,
                          _FILE_SHARE_READ | _FILE_SHARE_WRITE, None,
                          _OPEN_EXISTING, 0, None)
        return h if h != _INVALID_HANDLE else None

    def _open_rw(path):
        """读写句柄（FILE_FLAG_OVERLAPPED，便于带超时读写；失败则回退同步句柄）。"""
        args = (ctypes.c_wchar_p(path), _GENERIC_READ | _GENERIC_WRITE,
                _FILE_SHARE_READ | _FILE_SHARE_WRITE, None, _OPEN_EXISTING)
        h = _K.CreateFileW(*args, _FILE_FLAG_OVERLAPPED, None)
        if h == _INVALID_HANDLE or h is None:
            h = _K.CreateFileW(*args, 0, None)
        return h if (h and h != _INVALID_HANDLE) else None

    def _send_report(h, data, out_len):
        """发一帧：先走 WriteFile（多数 2.4G 接收器的中断 OUT 通道），
        失败再回落到 HidD_SetOutputReport（部分设备独占 WriteFile 不可用）。"""
        size = max(len(data), out_len if out_len else 0)
        frame = bytes(data).ljust(size, b"\x00")
        if _write_overlapped(h, frame):
            return True
        buf = ctypes.create_string_buffer(frame, size)
        return _HID.HidD_SetOutputReport(h, buf, size) != 0

    def _send_setoutput(h, data, out_len):
        size = max(len(data), out_len if out_len else 0)
        buf = ctypes.create_string_buffer(bytes(data).ljust(size, b"\x00"), size)
        return _HID.HidD_SetOutputReport(h, buf, size) != 0

    def _write_overlapped(h, data):
        size = len(data)
        buf = ctypes.create_string_buffer(bytes(data), size)
        ov = _OVERLAPPED()
        ov.hEvent = _K.CreateEventW(None, 1, 0, None)
        if not ov.hEvent or ov.hEvent == _INVALID_HANDLE:
            return False
        nw = wintypes.DWORD(0)
        ok = False
        try:
            rc = _K.WriteFile(h, buf, size, ctypes.byref(nw), ctypes.byref(ov))
            if rc:
                ok = True
            elif _K.GetLastError() == _ERROR_IO_PENDING:
                if _K.WaitForSingleObject(ov.hEvent, 1000) == _WAIT_OBJECT_0:
                    if _K.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(nw), False):
                        ok = True
                else:
                    _K.CancelIo(h)
                    _K.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(nw), True)
        finally:
            if ov.hEvent and ov.hEvent != _INVALID_HANDLE:
                _K.CloseHandle(ov.hEvent)
        return ok

    def _read_file_overlapped(h, size, timeout_ms):
        buf = ctypes.create_string_buffer(size)
        ov = _OVERLAPPED()
        ov.hEvent = _K.CreateEventW(None, 1, 0, None)
        if not ov.hEvent or ov.hEvent == _INVALID_HANDLE:
            return None
        nread = wintypes.DWORD(0)
        got = None
        try:
            rc = _K.ReadFile(h, buf, size, ctypes.byref(nread), ctypes.byref(ov))
            if rc:
                got = buf.raw[:nread.value]
            else:
                if _K.GetLastError() == _ERROR_IO_PENDING:
                    if _K.WaitForSingleObject(ov.hEvent, timeout_ms) == _WAIT_OBJECT_0:
                        if _K.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(nread), False):
                            got = buf.raw[:nread.value]
                    else:
                        _K.CancelIo(h)
                        _K.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(nread), True)
        finally:
            if ov.hEvent and ov.hEvent != _INVALID_HANDLE:
                _K.CloseHandle(ov.hEvent)
        return got

    def _grab(h, rid, in_len):
        """一次抓取若干输入缓冲：先 HidD_GetInputReport，再 ReadFile（重叠）。"""
        size = in_len if (in_len and in_len >= 17) else 64
        res = []
        buf = ctypes.create_string_buffer(size)
        buf[0] = rid & 0xFF
        if _HID.HidD_GetInputReport(h, buf, size):
            res.append(buf.raw[:size])
        rf = _read_file_overlapped(h, size, 250)
        if rf:
            res.append(rf)
        return res

    def enumerate_hid():
        """枚举本机所有 HID 设备，返回 (path, vid, pid, usage_page, in/out/feat_len, product)。"""
        out = []
        g = _GUID()
        _HID.HidD_GetHidGuid(ctypes.byref(g))
        hDev = _SETUP.SetupDiGetClassDevsW(ctypes.byref(g), None, None,
                                          _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE)
        if hDev == _INVALID_HANDLE:
            return out
        did = _SP_DEVICE_INTERFACE_DATA()
        did.cbSize = ctypes.sizeof(did)
        i = 0
        try:
            while _SETUP.SetupDiEnumDeviceInterfaces(hDev, None, ctypes.byref(g), i, ctypes.byref(did)):
                i += 1
                needed = wintypes.DWORD()
                _SETUP.SetupDiGetDeviceInterfaceDetailW(hDev, ctypes.byref(did), None, 0,
                                                       ctypes.byref(needed), None)
                if needed.value <= 0:
                    continue
                # 必须按结构体实际大小分配，否则下面 cast 读取 DevicePath(WCHAR[1024])
                # 会越界读取相邻内存，在 Windows 上可能直接让线程崩溃。
                buf = ctypes.create_string_buffer(
                    max(needed.value, ctypes.sizeof(_SP_DEV_INTERFACE_DETAIL)))
                detail = ctypes.cast(buf, ctypes.POINTER(_SP_DEV_INTERFACE_DETAIL))[0]
                detail.cbSize = 8
                if not _SETUP.SetupDiGetDeviceInterfaceDetailW(hDev, ctypes.byref(did),
                                                             ctypes.byref(detail), needed.value, None, None):
                    continue
                h = _open_meta(detail.DevicePath)
                if h is None:
                    continue
                try:
                    attr = _HIDD_ATTR()
                    attr.Size = ctypes.sizeof(attr)
                    if not _HID.HidD_GetAttributes(h, ctypes.byref(attr)):
                        continue
                    up = inl = outl = featl = 0
                    pp = ctypes.c_void_p()
                    if _HID.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                        caps = _HIDP_CAPS()
                        if _HID.HidP_GetCaps(pp, ctypes.byref(caps)) == _HIDP_STATUS_SUCCESS:
                            up = int(caps.UsagePage)
                            inl = int(caps.InputReportByteLength)
                            outl = int(caps.OutputReportByteLength)
                            featl = int(caps.FeatureReportByteLength)
                        _HID.HidD_FreePreparsedData(pp)
                    prod = ctypes.create_unicode_buffer(128)
                    pname = prod.value if _HID.HidD_GetProductString(h, prod, 256) else ""
                    out.append({"path": detail.DevicePath, "vid": int(attr.VendorID),
                                "pid": int(attr.ProductID), "usage_page": up,
                                "in_len": inl, "out_len": outl, "feat_len": featl,
                                "product": pname})
                finally:
                    _K.CloseHandle(h)
        except Exception:
            pass
        finally:
            try:
                _SETUP.SetupDiDestroyDeviceInfoList(hDev)
            except Exception:
                pass
        return out

    def _find_device(devs, pairs, up=None, min_in=0, min_out=0, keywords=None):
        for d in devs:
            hit = False
            if pairs:
                for (v, p) in pairs:
                    if d["vid"] == v and (p is None or d["pid"] == p):
                        hit = True
                        break
            if not hit and keywords:
                prod = (d["product"] or "").upper()
                if any(kw in prod for kw in keywords):
                    hit = True
            if not hit:
                continue
            if up is not None and d["usage_page"] != up:
                continue
            if min_in and d["in_len"] < min_in:
                continue
            if min_out and d["out_len"] < min_out:
                continue
            return d
        return None


# ===========================================================================
#  串口底层（ctypes 直调 kernel32，不依赖 pyserial）
#  ---------------------------------------------------------------------------
#  与 HID 完全对等的第二条传输通道：打开 COM 口 / 配置波特率 / 读写原始字节。
#  只做「字节搬运」，不含任何设备协议 —— 帧头、校验、电量位置依旧由 JSON 提供。
#
#  超时策略：读超时设成「立即返回已收到的字节」，由上层按 listen_ms 自己轮询，
#  这样拔掉接收器时读到的永远是空串，不会把刷新线程阻塞在 ReadFile 上。
# ===========================================================================
_SERIAL_PARITY = {"N": 0, "O": 1, "E": 2, "M": 3, "S": 4}     # NOPARITY…SPACEPARITY
_SERIAL_STOPBITS = {"1": 0, "1.5": 1, "2": 2}
_PURGE_ALL = 0x000F                                          # TX/RX 的 abort + clear


class _COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [("ReadIntervalTimeout", wintypes.DWORD),
                ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                ("ReadTotalTimeoutConstant", wintypes.DWORD),
                ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                ("WriteTotalTimeoutConstant", wintypes.DWORD)]


class _DCB(ctypes.Structure):
    """Win32 DCB，28 字节；flags 用位域展开后的整型，避免 ctypes 位域跨平台歧义。"""
    _fields_ = [("DCBlength", wintypes.DWORD),
                ("BaudRate", wintypes.DWORD),
                ("flags", wintypes.DWORD),          # fBinary/fParity/DTR/RTS 位
                ("wReserved", wintypes.WORD),
                ("XonLim", wintypes.WORD),
                ("XoffLim", wintypes.WORD),
                ("ByteSize", ctypes.c_byte),
                ("Parity", ctypes.c_byte),
                ("StopBits", ctypes.c_byte),
                ("XonChar", ctypes.c_char),
                ("XoffChar", ctypes.c_char),
                ("ErrorChar", ctypes.c_char),
                ("EofChar", ctypes.c_char),
                ("EvtChar", ctypes.c_char),
                ("wReserved1", wintypes.WORD)]


if _WIN_HID:
    _K.GetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(_DCB)]
    _K.GetCommState.restype = ctypes.c_int
    _K.SetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(_DCB)]
    _K.SetCommState.restype = ctypes.c_int
    _K.SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(_COMMTIMEOUTS)]
    _K.SetCommTimeouts.restype = ctypes.c_int
    _K.SetupComm.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    _K.SetupComm.restype = ctypes.c_int
    _K.PurgeComm.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _K.PurgeComm.restype = ctypes.c_int


def list_serial_ports():
    """枚举本机串口（读注册表 SERIALCOMM），返回 ["COM3", "COM10", ...]。

    只做枚举，不打开 —— 打不开的口（被占用）在真正连接时才会报错，
    这样「接收器拔了」和「口被别的软件占了」在界面上是同一种表现，不会崩溃。
    """
    ports = set()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            i = 0
            while True:
                try:
                    _name, value, _typ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if value:
                    ports.add(str(value).strip().upper())
    except Exception:
        pass
    return sorted(ports, key=lambda s: (len(s), s))


def _serial_open(port, baud=115200, bytesize=8, parity="N", stopbits="1"):
    """打开并配置串口，失败返回 None（不抛异常）。"""
    if not _WIN_HID or not port:
        return None
    try:
        h = _K.CreateFileW(ctypes.c_wchar_p("\\\\.\\" + str(port).upper()),
                           _GENERIC_READ | _GENERIC_WRITE, 0, None,
                           _OPEN_EXISTING, 0, None)
        if not h or h == _INVALID_HANDLE:
            return None
        dcb = _DCB()
        dcb.DCBlength = ctypes.sizeof(_DCB)
        if not _K.GetCommState(h, ctypes.byref(dcb)):
            _K.CloseHandle(h)
            return None
        dcb.BaudRate = max(300, min(4000000, int(baud or 115200)))
        dcb.ByteSize = 8 if int(bytesize or 8) not in (5, 6, 7, 8) else int(bytesize or 8)
        dcb.Parity = _SERIAL_PARITY.get(str(parity or "N").upper(), 0)
        dcb.StopBits = _SERIAL_STOPBITS.get(str(stopbits or "1"), 0)
        # fBinary(bit0) + 可选 fParity(bit1) + DTR_CONTROL_ENABLE(bits4-5=01)
        # + RTS_CONTROL_ENABLE(bits12-13=01)
        flags = 0x1 | 0x10 | 0x1000
        if dcb.Parity:
            flags |= 0x2
        dcb.flags = flags
        if not _K.SetCommState(h, ctypes.byref(dcb)):
            _K.CloseHandle(h)
            return None
        # 读：立即返回已收到的字节（避免阻塞刷新线程）；写：最多等 1 秒
        to = _COMMTIMEOUTS(0xFFFFFFFF, 0, 0, 0, 1000)
        _K.SetCommTimeouts(h, ctypes.byref(to))
        _K.SetupComm(h, 8192, 8192)
        _K.PurgeComm(h, _PURGE_ALL)
        return h
    except Exception as e:
        log_err("打开串口失败 " + str(port) + ": " + repr(e))
        return None


def _serial_write(h, data):
    if not h or not data:
        return False
    try:
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        n = wintypes.DWORD(0)
        ok = _K.WriteFile(h, buf, len(data), ctypes.byref(n), None)
        return bool(ok) and int(n.value) == len(data)
    except Exception:
        return False


def _serial_read(h, size=512):
    if not h:
        return b""
    try:
        buf = ctypes.create_string_buffer(max(1, int(size)))
        n = wintypes.DWORD(0)
        if not _K.ReadFile(h, buf, max(1, int(size)), ctypes.byref(n), None):
            return b""
        return buf.raw[:int(n.value)]
    except Exception:
        return b""


class _HidChannel:
    """HID 传输通道：两次读（GetInputReport + 重叠 ReadFile），与原来行为一致。"""

    kind = "hid"

    def __init__(self, dev):
        self.dev = dev
        self.h = None
        self.out_len = int(dev.get("out_len") or 0)
        self.in_len = max(int(dev.get("in_len") or 0), 64)

    def open(self):
        if not _WIN_HID:
            return False
        self.h = _open_rw(self.dev.get("path") or "")
        return bool(self.h)

    def send(self, data, mode="auto"):
        if not self.h:
            return False
        size = max(len(data), self.out_len)
        frame = bytes(data).ljust(size, b"\x00")
        if mode == "setoutput":
            return _send_setoutput(self.h, frame, self.out_len)
        if mode == "write":
            return _write_overlapped(self.h, frame)
        return _send_report(self.h, frame, self.out_len)

    def read(self, timeout_ms=30):
        if not self.h:
            return b""
        return _read_file_overlapped(self.h, self.in_len, timeout_ms) or b""

    def close(self):
        if self.h:
            try:
                _K.CloseHandle(self.h)
            except Exception:
                pass
            self.h = None


class _SerialChannel:
    """串口传输通道：write 发原始字节，read 取当前缓冲区里的全部内容。"""

    kind = "serial"

    def __init__(self, port, tr=None):
        tr = tr or {}
        self.port = str(port or "").upper()
        self.cfg = tr
        self.h = None
        self.out_len = int(tr.get("out_len") or 0)
        self.in_len = max(int(tr.get("in_len") or 0), 256)

    def open(self):
        c = self.cfg
        self.h = _serial_open(self.port, baud=c.get("baud") or 115200,
                              bytesize=c.get("bytesize") or 8,
                              parity=c.get("parity") or "N",
                              stopbits=c.get("stopbits") or "1")
        return bool(self.h)

    def send(self, data, mode="auto"):
        # 串口没有 report id / SetOutputReport 的概念，直接发原始字节
        return _serial_write(self.h, bytes(data))

    def read(self, timeout_ms=30):
        return _serial_read(self.h, self.in_len)

    def close(self):
        if self.h:
            try:
                _K.CloseHandle(self.h)
            except Exception:
                pass
            self.h = None


def open_channel(spec, dev):
    """按设备定义里的 transport.type 选一条通道；打不开返回 None。"""
    tr = spec.get("transport") or {}
    if str(tr.get("type") or "hid").lower() == "serial":
        ch = _SerialChannel(dev.get("port"), tr)
    else:
        ch = _HidChannel(dev)
    return ch if ch.open() else None


def spec_serial_ports(tr=None):
    """串口设备要尝试的端口列表：配置指定 > 用户在设置里手动选 > 全部本机串口。"""
    tr = tr or {}
    fixed = str(tr.get("port") or "").strip().upper()
    if fixed:
        return [fixed]
    chosen = str(USER.get("serial_port") or "").strip().upper()
    if chosen:
        return [chosen]
    return list_serial_ports()


# ===========================================================================
#  读取器注册表（纯净版为空）
#  ---------------------------------------------------------------------------
#  本纯净版不内置任何设备协议，READERS 故意留空 —— 所有设备都走下面的
#  「通用协议引擎」，读取规则来自外部 JSON 配置文件。
#
#  注册表的签名依然保留，方便将来需要在「通用引擎表达不了的时序策略」上做扩展：
#
#      reader(prof, devs) -> (电量%, 是否充电) | None
#
#  读不到就返回 None，绝不抛异常 —— 这里跑在刷新线程里，抛出去会拖垮整轮刷新。
#  要新增读取器，写成函数后注册进 READERS，并在设备定义里用 impl 指向它；
#  但请注意：那等于把设备协议写回源码，与「配置驱动」的目标相反，
#  更推荐先给通用引擎补字段（见 docs/config-schema.md）。
# ===========================================================================


def _pick_iface(prof, devs):
    """按 prof['iface'] 的条件从枚举结果里挑出这台设备要用的那个接口。"""
    iface = prof.get("iface") or {}
    return _find_device(devs, prof.get("pairs") or [],
                        up=iface.get("usage_page"),
                        min_in=iface.get("min_in") or 0,
                        min_out=iface.get("min_out") or 0,
                        keywords=prof.get("keywords"))


READERS = {}


# 内置读取器已移除：本纯净版不内置任何设备协议，所有设备都走外部 JSON 配置。


# =========================================================
#  通用协议引擎 —— 让「协议」变成一种可导入的文件格式
#  ---------------------------------------------------------
#  本纯净版没有内置读取器，所有设备都走这个「协议解释器」：
#  用一份 JSON 描述设备的识别方式 + 发什么帧 + 从回包哪个位置取电量，
#  程序照单执行。把这样的 JSON 存成文件，就是可分享、可导入的「设备包」。
#
#  文件格式（.json，UTF-8）：
#  {
#    "format": "dianliang-device-pack",     // 可选，用于识别本程序的文件
#    "version": 1,                          // 可选
#    "devices": [ { 设备1 }, { 设备2 } ]     // 一个文件可含多台设备
#  }
#
#  单台设备：
#  {
#    "name": "某某无线鼠标",                  // 必填，卡片上显示的名字
#    "type": "mouse",                       // mouse/keyboard/headphone/gamepad/other
#    "key":  "my-mouse",                    // 可选，唯一标识；省略则按名字生成
#    "wire": "2.4G",                        // 可选，副标题里显示的连接方式
#    "match": {                             // 怎么在系统里认出它（先按 VID，再按可选条件筛）
#      "vid": "0x1234",                     // 必填（也可只写 product_contains）
#      "pid": "0x5678",                     // 可选
#      "usage_page": "0xFF02",              // 可选，厂商接口的 usagePage
#      "min_in": 17, "min_out": 17,         // 可选，输入/输出报告最小长度
#      "product_contains": ["WIRELESS"]      // 可选，产品名包含其中任一词
#    },
#    "read": {
#      "query": ["AA BB CC ..."],           // 必填，可多帧（按顺序试），空数组=只听设备主动推
#      "pre": ["AA BB 01 ..."],             // 可选，发查询前先发的握手/唤醒帧
#      "pre_delay_ms": 50,
#      "checksum": {"mode":"sum_neg","from":1,"to":16,"at":16,"seed":"0x00"},
#      "pad_to": 17,                        // 可选，帧补齐到多少字节
#      "send": "auto",                      // auto / write / setoutput
#      "attempts": 2,
#      "listen_ms": 800,                    // 每帧等回包的总时长
#      "passive_ms": 200,                   // 可选，先纯听这么久（有些设备会主动推）
#      "reply": {
#        "contains": ["AA BB", "BB"],       // 可选，回包里必须出现的字节序列（可多个候选）
#        "checksum": {"mode":"sum_tail","from":0,"to":19,"at":19},
#        "battery": {"byte":5},              // 必填，电量所在字节（相对命中位置）
#        "charging": {"byte":6,"mask":"0x10"},  // 可选，该位非 0 视为充电中
#        "range": [1,100]                   // 可选，超出范围视为没读到
#      }
#    }
#  }
#  checksum 的 from/to 是切片（含 from、不含 to），at 是校验字节写在哪个位置；
#  mode: sum_tail = 求和取低 8 位；sum_neg = (seed - 求和) & 0xFF。
# =========================================================
PACK_FORMAT = "dianliang-device-pack"
PACK_VERSION = 1                  # 配置结构版本：字段不兼容变更时才 +1
DEVICES_PATH = os.path.join(APP_DIR, "devices.json")   # 导入的设备存这里，启动自动加载
SPEC_DEVICES = {}          # key -> 规范化后的 spec（导入的设备）
USER_SPEC_KEYS = []        # 保持导入顺序
# 被用户右键「移除」的内置设备 —— 只是不显示，随时可以用导入行的「恢复内置设备」加回来，
# 记录写进 cache.json 的 _hidden，重启后依然保持移除状态。
HIDDEN_BUILTIN = []


def hide_builtin(key):
    """把一台内置设备从列表里摘掉，并记住这个决定。"""
    if key not in HIDDEN_BUILTIN:
        HIDDEN_BUILTIN.append(key)
    for i, p in enumerate(PROFILES):
        if p["key"] == key:
            del PROFILES[i]
            break


def unhide_builtin(key):
    """恢复一台被移除的内置设备（插回原来那台的位置）。"""
    if key in HIDDEN_BUILTIN:
        HIDDEN_BUILTIN.remove(key)
    if any(p["key"] == key for p in PROFILES):
        return False
    origin = DEFAULT_PROFILES
    idx = next((i for i, p in enumerate(origin) if p["key"] == key), None)
    if idx is None:
        return False
    prof = dict(origin[idx])
    # 插回它在原始顺序里的位置：找到它后面第一个还在列表里的设备，插到它前面
    pos = len(PROFILES)
    for later in origin[idx + 1:]:
        for i, p in enumerate(PROFILES):
            if p["key"] == later["key"]:
                pos = i
                break
        else:
            continue
        break
    PROFILES.insert(pos, prof)
    sync_device_count()
    return True


def apply_hidden_builtins(keys):
    """启动时按 cache.json 里记的 _hidden 摘掉被移除的内置设备。"""
    for k in keys or []:
        if any(p["key"] == k for p in DEFAULT_PROFILES):
            hide_builtin(k)
    sync_device_count()


def _hx(s):
    """把 "AA BB" / "AABB" / "0xAA,0xBB" 之类统一解析成 bytes。空串返回 b""。"""
    if not s:
        return b""
    if isinstance(s, (bytes, bytearray)):
        return bytes(s)
    txt = str(s).replace("0x", " ").replace("0X", " ").replace(",", " ")
    txt = txt.replace("-", " ").replace(":", " ")
    parts = txt.split()
    if len(parts) == 1 and len(parts[0]) > 2 and len(parts[0]) % 2 == 0:
        p = parts[0]
        parts = [p[i:i + 2] for i in range(0, len(p), 2)]
    out = bytearray()
    for p in parts:
        try:
            out.append(int(p, 16) & 0xFF)
        except ValueError:
            raise ValueError(f"不是合法的十六进制：{s!r}")
    return bytes(out)


def _num(v, default=0):
    """"0x1234" / "1234" / 4660 都接受。"""
    if v is None:
        return default
    if isinstance(v, int):
        return v
    t = str(v).strip()
    if not t:
        return default
    return int(t, 16) if t.lower().startswith("0x") else int(t, 16 if len(t) > 4 else 10)


def _apply_checksum(frame, rule, for_reply=False):
    """按 rule 算出校验字节并写进 frame（返回新 bytes）。规则本身不合法就原样返回。"""
    if not rule:
        return frame
    mode = (rule.get("mode") or "none").lower()
    if mode == "none":
        return frame
    b = bytearray(frame)
    i0 = int(rule.get("from", 0))
    i1 = int(rule.get("to", len(b)))
    at = int(rule.get("at", len(b) - 1))
    if not (0 <= i0 <= i1 <= len(b)) or not (0 <= at < len(b)):
        return bytes(b)
    s = sum(b[i0:i1]) & 0xFF
    if mode == "sum_neg":
        v = (_num(rule.get("seed"), 0) - s) & 0xFF
    else:                                   # sum_tail / 其它一律按求和
        v = s
    b[at] = v
    return bytes(b)


def _check_checksum(frame, rule):
    """校验回包：先按规则算一遍，再和帧里那个字节比。"""
    if not rule:
        return True
    mode = (rule.get("mode") or "none").lower()
    if mode == "none":
        return True
    i0 = int(rule.get("from", 0))
    i1 = int(rule.get("to", len(frame)))
    at = int(rule.get("at", len(frame) - 1))
    if not (0 <= i0 <= i1 <= len(frame)) or not (0 <= at < len(frame)):
        return False
    s = sum(frame[i0:i1]) & 0xFF
    want = ((_num(rule.get("seed"), 0) - s) & 0xFF) if mode == "sum_neg" else s
    return frame[at] == want


def _find_base(buf, contains):
    """在回包里找锚点字节序列，返回起始下标；找不到返回 -1。"""
    for pat in contains:
        if not pat:
            continue
        i = buf.find(pat)
        if i >= 0:
            return i
    return -1


# ---------------------------------------------------------------------------
#  电量换算
#  ---------------------------------------------------------------------------
#  只允许「算术 + 少量函数」的白名单 AST，绝不用 eval 直接跑用户字符串：
#  配置文件是社区共享的，任何一份都可能来自陌生人，必须假定它是不可信的。
_FORMULA_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
                  ast.Load, ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div,
                  ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd)
_FORMULA_FUNCS = {"abs": abs, "min": min, "max": max, "round": round,
                  "int": int, "float": float}


def safe_formula(expr, x):
    """把 formula 字符串当成单变量表达式求值；不合法抛 ValueError（中文原因）。"""
    try:
        tree = ast.parse(str(expr), mode="eval")
    except SyntaxError:
        raise ValueError(f"公式语法错误：{expr!r}")
    for node in ast.walk(tree):
        if not isinstance(node, _FORMULA_NODES):
            raise ValueError("公式里只能做加减乘除、取余、乘方和括号")
        if isinstance(node, ast.Name) and node.id not in ("x", "raw") \
                and node.id not in _FORMULA_FUNCS:
            raise ValueError(f"公式里有未知变量：{node.id}")
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in _FORMULA_FUNCS):
                raise ValueError("公式里只能调用 abs / min / max / round / int / float")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("公式里只允许数字常量")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exp = node.right
            if not (isinstance(exp, ast.Constant) and isinstance(exp.value, (int, float))
                    and abs(exp.value) <= 8):
                raise ValueError("乘方的指数必须是 -8~8 之间的数字")
    try:
        val = eval(compile(tree, "<formula>", "eval"),     # noqa: S307 - AST 已白名单
                   {"__builtins__": {}},
                   dict(_FORMULA_FUNCS, x=x, raw=x))
    except Exception as e:
        raise ValueError(f"公式算不出来：{e}")
    if isinstance(val, complex) or not isinstance(val, (int, float)):
        raise ValueError("公式结果不是数字")
    return float(val)


def map_lookup(table, raw):
    """查表（[[原始值, 百分比], ...] 或 {"原始值": 百分比}），点之间线性插值。"""
    pts = []
    if isinstance(table, dict):
        for k, v in table.items():
            try:
                pts.append((float(k), float(v)))
            except (TypeError, ValueError):
                continue
    elif isinstance(table, list):
        for item in table:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    pts.append((float(item[0]), float(item[1])))
                except (TypeError, ValueError):
                    continue
    if not pts:
        return None
    pts.sort()
    if raw <= pts[0][0]:
        return pts[0][1]
    if raw >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= raw <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (raw - x0) / (x1 - x0)
    return None


def battery_percent(body, bt):
    """按 battery 段把回包里的原始字节换算成百分比；越界/不合法返回 None。

    换算优先级：formula > map > read_as=voltage > scale/offset。
    每一步都可单独使用，旧配置（只有 byte / mode / scale）行为完全不变。
    """
    idx = int(bt.get("byte", 3))
    n = max(1, min(4, int(bt.get("bytes") or 1)))
    if idx < 0 or idx + n > len(body):
        return None
    order = "little" if str(bt.get("order") or "big").lower().startswith("l") else "big"
    raw = int.from_bytes(bytes(body[idx:idx + n]), order)
    mask = bt.get("mask")
    if mask not in (None, ""):
        mv = _num(mask, 0)
        if mv:
            raw &= mv
    shift = int(bt.get("shift") or 0)
    if shift > 0:
        raw >>= shift
    elif shift < 0:
        raw <<= -shift
    mode = (bt.get("mode") or "raw").lower()
    if mode == "hi":
        raw = (raw >> 4) & 0x0F
    elif mode == "lo":
        raw = raw & 0x0F

    try:
        if bt.get("formula"):
            val = safe_formula(bt["formula"], raw)
        elif bt.get("map"):
            got = map_lookup(bt["map"], raw)
            if got is None:
                return None
            val = got
        elif str(bt.get("read_as") or "percent").lower() == "voltage":
            # 原始值先当电压（毫伏/0.01V 之类由 voltage_scale 决定），再线性映射到 0~100%
            vs = float(bt.get("voltage_scale") or 0.01)
            vmin = float(bt.get("voltage_min") or 0)
            vmax = float(bt.get("voltage_max") or 100)
            if vmax == vmin:
                return None
            volts = raw * vs
            val = (volts - vmin) / (vmax - vmin) * 100.0
        else:
            val = raw * float(bt.get("scale") or 1) + float(bt.get("offset") or 0)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
    if not math.isfinite(val):
        return None
    return int(round(val))


def read_by_spec(spec, dev):
    """按 spec 描述读一次电量，成功返回 (百分比, 是否充电)，失败返回 None。绝不抛异常。

    HID 与串口走同一段流程，差异只在 channel：发帧、收帧的细节被 _HidChannel /
    _SerialChannel 吸收掉，所以「时序策略」只写一份。
    """
    ch = None
    try:
        rd = spec["read"]
        ch = open_channel(spec, dev)
        if not ch:
            return None
        out_len = ch.out_len
        in_len = ch.in_len
        pad_to = int(rd.get("pad_to") or 0)
        send_mode = (rd.get("send") or "auto").lower()
        reply = rd.get("reply") or {}
        contains = reply.get("contains") or []
        if isinstance(contains, str):
            contains = [contains]
        contains = [_hx(c) for c in contains]
        rng = reply.get("range") or [0, 100]
        lo, hi = int(rng[0]), int(rng[1])

        def _send(frame_bytes):
            size = max(len(frame_bytes), pad_to, out_len)
            frame = bytes(frame_bytes).ljust(size, b"\x00")
            return ch.send(frame, send_mode)

        def _try_parse(buf):
            if not buf:
                return None
            buf = bytes(buf)
            base = _find_base(buf, contains) if contains else 0
            if base < 0:
                return None
            body = buf[base:]
            ck = reply.get("checksum")
            if ck and not _check_checksum(body, ck):
                return None
            bt = reply.get("battery") or {}
            pct = battery_percent(body, bt)
            if pct is None or not (lo <= pct <= hi):
                return None
            charging = False
            chk = reply.get("charging") or {}
            if chk:
                ci = int(chk.get("byte", -1))
                if 0 <= ci < len(body):
                    charging = (body[ci] & _num(chk.get("mask"), 0xFF)) != 0
            return pct, charging

        # 0) 有些设备自己会推电量帧，先纯听一段（不发帧）
        passive = int(rd.get("passive_ms") or 0)
        if passive > 0:
            deadline = time.time() + passive / 1000.0
            while time.time() < deadline:
                got = ch.read(20)
                r = _try_parse(got)
                if r:
                    return r
                if not got:
                    time.sleep(0.004)

        # 1) 握手帧（开会话/唤醒），发完等一下让设备处理
        for pre in rd.get("pre") or []:
            _send(_apply_checksum(_hx(pre), rd.get("checksum")))
        if rd.get("pre") and rd.get("pre_delay_ms"):
            time.sleep(int(rd["pre_delay_ms"]) / 1000.0)

        queries = rd.get("query") or []
        attempts = max(1, int(rd.get("attempts") or 1))
        listen = int(rd.get("listen_ms") or 600)
        for _ in range(attempts):
            for q in queries:
                if not _send(_apply_checksum(_hx(q), rd.get("checksum"))):
                    continue
                deadline = time.time() + listen / 1000.0
                while time.time() < deadline:
                    got = ch.read(30)
                    r = _try_parse(got)
                    if r:
                        return r
                    if not got:
                        time.sleep(0.004)
            # 换通道再来一遍：HID 的 write / SetOutputReport 两条路，串口无此区别
            if queries and send_mode == "auto" and ch.kind == "hid":
                for q in queries:
                    if not ch.send(_apply_checksum(_hx(q), rd.get("checksum")), "setoutput"):
                        continue
                    deadline = time.time() + listen / 1000.0
                    while time.time() < deadline:
                        got = ch.read(30)
                        r = _try_parse(got)
                        if r:
                            return r
                        if not got:
                            time.sleep(0.004)
    except Exception as e:
        log_err("通用协议读取异常: " + repr(e))
    finally:
        if ch is not None:
            ch.close()
    return None


def spec_hits(spec, devs):
    """按 spec.match 过滤出候选接口（按可写性排序，能写的优先）。

    串口设备没有 VID/PID，改为返回「要尝试的串口列表」：
    配置里写死 port → 只用它；否则用用户在设置里选的端口；再否则试全部串口。
    """
    if str((spec.get("transport") or {}).get("type") or "hid").lower() == "serial":
        tr = spec.get("transport") or {}
        return [{"kind": "serial", "port": p, "vid": None, "pid": None,
                 "usage_page": None,
                 "in_len": int(tr.get("in_len") or 0),
                 "out_len": int(tr.get("out_len") or 0),
                 "product": f"串口 {p}"} for p in spec_serial_ports(tr)]
    m = spec["match"]
    vid = m.get("vid")
    pid = m.get("pid")
    up = m.get("usage_page")
    min_in = int(m.get("min_in") or 0)
    min_out = int(m.get("min_out") or 0)
    kws = [k.upper() for k in (m.get("product_contains") or [])]
    hits = []
    for d in devs:
        if vid is not None and d["vid"] != vid:
            continue
        if pid is not None and d["pid"] != pid:
            continue
        if up is not None and d["usage_page"] != up:
            continue
        if min_in and (d.get("in_len") or 0) < min_in:
            continue
        if min_out and (d.get("out_len") or 0) < min_out:
            continue
        if kws:
            pr = (d.get("product") or "").upper()
            if not any(k in pr for k in kws):
                continue
        hits.append(d)
    hits.sort(key=lambda d: (0 if (d.get("out_len") or 0) > 0 else 1, -(d.get("out_len") or 0)))
    return hits


def probe_spec(key, devs=None):
    """导入设备的读取入口：与 probe_device 返回同结构，界面无需区分来源。"""
    spec = SPEC_DEVICES.get(key)
    if not spec:
        return {"state": "disconnected"}
    is_serial = str((spec.get("transport") or {}).get("type") or "hid").lower() == "serial"
    if not is_serial and not _WIN_HID:
        return {"state": "nohid"}
    if devs is None:
        devs = enumerate_hid() if not is_serial else []
    hits = spec_hits(spec, devs)
    if not hits:
        return {"state": "disconnected"}
    for d in hits:
        r = read_by_spec(spec, d)
        if r:
            # 电量读到 0：对声明了 zero_is_off 的设备，0 是「关机时的假帧」，
            # 不是真实电量 —— 一律按「未响应」处理，绝不显示成 0%。
            if r[0] <= 0 and spec["read"]["reply"]["battery"].get("zero_is_off"):
                return {"state": "connected", "note": "implnoread"}
            return {"state": "connected", "battery": r[0], "charging": r[1],
                    "source": "spec"}
    return {"state": "connected", "note": "specnoread"}


def _cn_int(value, field, name, lo=None, hi=None, default=None):
    """把配置值转成整数，失败时抛中文错误（带字段名和设备名，方便定位）。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise ValueError(f"「{name}」的 {field} 必填，而且必须是整数")
    try:
        if isinstance(value, str):
            txt = value.strip()
            # 十进制优先（兼容 "08" 这种带前导零的写法），0x 开头按十六进制
            got = int(txt, 16) if txt.lower().startswith(("0x", "-0x")) else int(txt, 10)
        else:
            got = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"「{name}」的 {field} 必须是整数，现在是 {value!r}")
    if lo is not None and got < lo:
        raise ValueError(f"「{name}」的 {field} 不能小于 {lo}（现在是 {got}）")
    if hi is not None and got > hi:
        raise ValueError(f"「{name}」的 {field} 不能大于 {hi}（现在是 {got}）")
    return got


def _cn_num(value, field, name, default=None):
    """VID/PID 这类「十六进制或十进制都可」的整数。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        return _num(value, 0)
    except (TypeError, ValueError):
        raise ValueError(f"「{name}」的 {field} 不是合法的数字/十六进制：{value!r}")


def _cn_float(value, field, name, default=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise ValueError(f"「{name}」的 {field} 必填，而且必须是数字")
    try:
        got = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"「{name}」的 {field} 必须是数字，现在是 {value!r}")
    if not math.isfinite(got):
        raise ValueError(f"「{name}」的 {field} 不是有效数字：{value!r}")
    return got


def normalize_spec(raw):
    """把用户写的 JSON 设备定义规范成内部结构；有问题抛 ValueError（中文原因）。"""
    if not isinstance(raw, dict):
        raise ValueError("设备定义必须是一个 JSON 对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("缺少 name（设备名称）")

    # ---------------- 传输方式 ----------------
    tr_raw = raw.get("transport") or {}
    if not isinstance(tr_raw, dict):
        raise ValueError(f"「{name}」的 transport 必须是对象")
    ttype = str(tr_raw.get("type") or "hid").strip().lower()
    if ttype not in ("hid", "serial"):
        raise ValueError(f"「{name}」的 transport.type 只能是 hid 或 serial（现在是 {ttype!r}）")
    transport = {"type": ttype}
    if ttype == "serial":
        parity = str(tr_raw.get("parity") or "N").strip().upper()
        if parity not in _SERIAL_PARITY:
            raise ValueError(f"「{name}」的 transport.parity 只能是 N/O/E/M/S")
        transport.update({
            "port": str(tr_raw.get("port") or "").strip().upper(),
            "baud": _cn_int(tr_raw.get("baud"), "transport.baud", name,
                            lo=300, hi=4000000, default=115200),
            "bytesize": _cn_int(tr_raw.get("bytesize"), "transport.bytesize", name,
                                lo=5, hi=8, default=8),
            "parity": parity,
            "stopbits": str(tr_raw.get("stopbits") or "1").strip(),
            "in_len": _cn_int(tr_raw.get("in_len"), "transport.in_len", name,
                              lo=1, hi=65536, default=512),
            "out_len": _cn_int(tr_raw.get("out_len"), "transport.out_len", name,
                               lo=0, hi=65536, default=0),
        })
        if transport["stopbits"] not in _SERIAL_STOPBITS:
            raise ValueError(f"「{name}」的 transport.stopbits 只能是 1 / 1.5 / 2")

    rd_raw = raw.get("read")
    if not isinstance(rd_raw, dict):
        raise ValueError(f"「{name}」缺少 read 段（发什么帧、从哪取电量）")
    match = raw.get("match") or {}
    if not isinstance(match, dict):
        raise ValueError(f"「{name}」的 match 必须是对象")
    m = {
        "vid": _cn_num(match.get("vid"), "match.vid", name),
        "pid": _cn_num(match.get("pid"), "match.pid", name),
        "usage_page": _cn_num(match.get("usage_page"), "match.usage_page", name),
        "min_in": _cn_int(match.get("min_in"), "match.min_in", name, lo=0, default=0),
        "min_out": _cn_int(match.get("min_out"), "match.min_out", name, lo=0, default=0),
        "product_contains": [str(x) for x in (match.get("product_contains") or [])],
    }
    if ttype == "hid" and m["vid"] is None and not m["product_contains"]:
        raise ValueError(f"「{name}」的 match 里至少有 vid 或 product_contains")

    reply = rd_raw.get("reply") or {}
    if not isinstance(reply, dict):
        raise ValueError(f"「{name}」的 reply 必须是对象")
    battery = reply.get("battery") or {}
    if not isinstance(battery, dict) or battery.get("byte") is None:
        raise ValueError(f"「{name}」的 reply.battery.byte 必填（电量在第几个字节）")

    # 帧字段统一转成规范十六进制串，导出/展示时可直接读
    def _frames(v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v]

    rng = reply.get("range") or [0, 100]
    if not isinstance(rng, (list, tuple)) or len(rng) < 2:
        raise ValueError(f"「{name}」的 reply.range 必须是 [最小, 最大] 两个整数")
    lo = _cn_int(rng[0], "reply.range[0]", name, default=0)
    hi = _cn_int(rng[1], "reply.range[1]", name, default=100)
    if lo > hi:
        raise ValueError(f"「{name}」的 reply.range 最小值不能大于最大值")

    # ---------------- 电量换算 ----------------
    bt = {
        "byte": _cn_int(battery.get("byte"), "reply.battery.byte", name, lo=0),
        "bytes": _cn_int(battery.get("bytes"), "reply.battery.bytes", name,
                         lo=1, hi=4, default=1),
        "order": str(battery.get("order") or "big").strip().lower(),
        "mode": str(battery.get("mode") or "raw").strip().lower(),
        "scale": _cn_float(battery.get("scale"), "reply.battery.scale", name, default=1.0),
        "offset": _cn_float(battery.get("offset"), "reply.battery.offset", name, default=0.0),
        # 有些设备关机时回的是「电量字节 = 0」的假帧（并非真实 0%）。
        # zero_is_off: true 时，读到 0 一律按「未响应」处理，不显示成 0%。
        "zero_is_off": bool(battery.get("zero_is_off")),
    }
    if bt["order"] not in ("big", "little"):
        raise ValueError(f"「{name}」的 reply.battery.order 只能是 big 或 little")
    if bt["mode"] not in ("raw", "hi", "lo"):
        raise ValueError(f"「{name}」的 reply.battery.mode 只能是 raw / hi / lo")
    if battery.get("mask") not in (None, ""):
        bt["mask"] = _cn_num(battery.get("mask"), "reply.battery.mask", name, default=0)
        if not bt["mask"]:
            raise ValueError(f"「{name}」的 reply.battery.mask 不能是 0")
    shift = _cn_int(battery.get("shift"), "reply.battery.shift", name,
                    lo=-32, hi=32, default=0)
    if shift:
        bt["shift"] = shift
    if battery.get("formula"):
        # 先拿 x=50 试算一次：语法/未知变量/危险调用在这里就报出来，不用等到运行时
        try:
            safe_formula(battery["formula"], 50)
        except ValueError as e:
            raise ValueError(f"「{name}」的 reply.battery.formula 有问题：{e}")
        bt["formula"] = str(battery["formula"])
    if battery.get("map"):
        if not isinstance(battery["map"], (list, dict)):
            raise ValueError(f"「{name}」的 reply.battery.map 必须是数组或对象")
        if map_lookup(battery["map"], 0) is None:
            raise ValueError(f"「{name}」的 reply.battery.map 里没有可用的 [原始值, 百分比] 点")
        bt["map"] = battery["map"]
    read_as = str(battery.get("read_as") or "percent").strip().lower()
    if read_as not in ("percent", "voltage", "raw"):
        raise ValueError(f"「{name}」的 reply.battery.read_as 只能是 percent / voltage / raw")
    if read_as == "voltage":
        bt["read_as"] = "voltage"
        bt["voltage_scale"] = _cn_float(battery.get("voltage_scale"),
                                        "reply.battery.voltage_scale", name, default=0.01)
        bt["voltage_min"] = _cn_float(battery.get("voltage_min"),
                                      "reply.battery.voltage_min", name, default=0.0)
        bt["voltage_max"] = _cn_float(battery.get("voltage_max"),
                                      "reply.battery.voltage_max", name, default=100.0)
        if bt["voltage_max"] == bt["voltage_min"]:
            raise ValueError(f"「{name}」的 voltage_max 不能等于 voltage_min")

    ch_raw = reply.get("charging") or None
    if ch_raw is not None and not isinstance(ch_raw, dict):
        raise ValueError(f"「{name}」的 reply.charging 必须是对象")
    if isinstance(ch_raw, dict):
        _cn_int(ch_raw.get("byte"), "reply.charging.byte", name, default=0)
        if ch_raw.get("mask") not in (None, ""):
            _cn_num(ch_raw.get("mask"), "reply.charging.mask", name, default=0)

    rd = {
        "query": _frames(rd_raw.get("query")),
        "pre": _frames(rd_raw.get("pre")),
        "pre_delay_ms": _cn_int(rd_raw.get("pre_delay_ms"), "read.pre_delay_ms", name,
                                lo=0, hi=10000, default=0),
        "checksum": rd_raw.get("checksum") or None,
        "pad_to": _cn_int(rd_raw.get("pad_to"), "read.pad_to", name, lo=0, hi=65536, default=0),
        "send": str(rd_raw.get("send") or "auto").strip().lower(),
        "attempts": max(1, min(5, _cn_int(rd_raw.get("attempts"), "read.attempts", name,
                                          lo=1, hi=5, default=1))),
        "listen_ms": _cn_int(rd_raw.get("listen_ms"), "read.listen_ms", name,
                             lo=50, hi=5000, default=600),
        "passive_ms": _cn_int(rd_raw.get("passive_ms"), "read.passive_ms", name,
                              lo=0, hi=3000, default=0),
        "reply": {
            "contains": _frames(reply.get("contains")),
            "checksum": reply.get("checksum") or None,
            "battery": bt,
            "charging": ch_raw,
            "range": [lo, hi],
        },
    }
    if rd["send"] not in ("auto", "write", "setoutput"):
        raise ValueError(f"「{name}」的 read.send 只能是 auto / write / setoutput")
    if not rd["query"] and not rd["passive_ms"]:
        raise ValueError(f"「{name}」的 read.query 为空，就必须给 passive_ms（只听设备主动推）")
    # 先试一下帧能不能解析，早报错比运行时报错好
    for q in rd["query"] + rd["pre"]:
        try:
            _hx(q)
        except ValueError:
            raise ValueError(f"「{name}」的帧不是合法的十六进制：{q!r}")
    if rd["reply"]["contains"]:
        for c in rd["reply"]["contains"]:
            try:
                _hx(c)
            except ValueError:
                raise ValueError(f"「{name}」的 reply.contains 不是合法的十六进制：{c!r}")

    key = str(raw.get("key") or name).strip()
    key = "u:" + key
    return {
        "key": key,
        "name": name,
        "type": str(raw.get("type") or "other").lower(),
        "wire": str(raw.get("wire") or ("串口" if ttype == "serial" else "2.4G")),
        "protocol": str(raw.get("protocol") or "自定义协议"),
        "transport": transport,
        "match": m,
        "read": rd,
        "builtin": False,
    }


def _read_pack_file(path):
    """读协议文件（JSON 或 YAML），返回 (data, 错误)。所有错误都是中文。"""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            return None, ("读取 YAML 需要先安装 PyYAML（pip install pyyaml）；"
                          "也可以把配置另存为 .json 再导入")
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return yaml.safe_load(f), None
        except Exception as e:
            # YAML 库的异常文本是英文，这里只取行列位置，统一换成中文说明
            mark = getattr(e, "problem_mark", None)
            if mark is not None:
                return None, (f"YAML 语法错误（第 {mark.line + 1} 行第 {mark.column + 1} 列）："
                              f"请检查缩进、冒号和括号是否完整")
            return None, "YAML 语法错误：请检查缩进、冒号和括号是否完整"
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except json.JSONDecodeError as e:
        return None, (f"JSON 语法错误（第 {e.lineno} 行第 {e.colno} 列）："
                      f"请检查引号、逗号、括号是否完整")
    except UnicodeDecodeError:
        return None, "文件编码不对：请另存为 UTF-8（带不带 BOM 都可以）"
    except Exception as e:
        return None, f"打不开文件：{e}"


def load_pack(path):
    """读一个协议文件，返回 (specs, 错误列表)。单个设备写错不影响其它设备。"""
    specs, errors = [], []
    data, err = _read_pack_file(path)
    if err:
        return [], [err]
    if data is None:
        return [], ["文件是空的，里面没有任何设备定义"]

    # ---- 文件级校验：format / version ----
    if isinstance(data, dict):
        if data.get("template") is True:
            return [], ["这是配置模板（template: true），不是可导入的设备；"
                        "请复制一份、把参数改成真实值后再导入"]
        fmt = data.get("format")
        if fmt not in (None, "", PACK_FORMAT):
            return [], [f"这不是本程序的协议文件（format = {fmt!r}，"
                        f"应为 {PACK_FORMAT!r}）"]
        ver = data.get("version")
        if ver not in (None, ""):
            try:
                txt = str(ver).strip()
                got = (int(txt, 16) if txt.lower().startswith("0x")
                       else int(txt, 10)) if isinstance(ver, str) else int(ver)
            except (TypeError, ValueError):
                return [], [f"version 必须是整数，现在是 {ver!r}"]
            if got != PACK_VERSION:
                return [], [f"配置版本 {got} 不受支持：本程序支持 version {PACK_VERSION}。"
                            f"请升级软件，或把配置改成 version {PACK_VERSION} 再导入"]

    items = data.get("devices") if isinstance(data, dict) else None
    if items is None:
        items = data if isinstance(data, list) else [data]
    if not isinstance(items, list):
        return [], ["文件里没有 devices 数组"]
    for i, raw in enumerate(items, 1):
        try:
            specs.append(normalize_spec(raw))
        except Exception as e:
            errors.append(f"第 {i} 台：{e}")
    if not specs and not errors:
        errors.append("文件里没有任何设备定义")
    return specs, errors


def spec_to_dict(s):
    """内部 spec → 可写进文件的 JSON 对象（VID/PID 用 0x 形式，人能看懂）。"""
    def _hxstr(v):
        return None if v is None else f"0x{v:04X}"

    m = s["match"]
    out = {
        "key": s["key"][2:] if s["key"].startswith("u:") else s["key"],
        "name": s["name"],
        "type": s["type"],
        "wire": s["wire"],
        "protocol": s.get("protocol") or "自定义协议",
        "match": {
            "vid": _hxstr(m.get("vid")),
            "pid": _hxstr(m.get("pid")),
            "usage_page": _hxstr(m.get("usage_page")),
            "min_in": m.get("min_in") or 0,
            "min_out": m.get("min_out") or 0,
            "product_contains": m.get("product_contains") or [],
        },
        "read": dict(s["read"]),
    }
    tr = s.get("transport") or {}
    if tr and tr.get("type") == "serial":
        out["transport"] = dict(tr)
    return out


def one_device_pack(dev):
    """一台设备一个文件 —— 单独分享、单独备份都方便。"""
    return {"format": PACK_FORMAT, "version": PACK_VERSION, "devices": [dev]}


def spec_filename(s):
    """文件名 = 设备名，例如「无线鼠标.json」。
    协议名只写在文件内容里，不参与命名 —— 文件名越短越好看，也方便按设备找。"""
    raw = str(s.get("name") or "未命名设备")
    for ch in '<>:"/\\|?*\n\r\t':
        raw = raw.replace(ch, "-")
    raw = raw.strip().strip(".")
    return (raw[:80] or "未命名设备") + ".json"


def exportable_packs():
    """把当前列表里的设备整理成「一台一个文件」的 [(key, 文件名, 文件内容)]。"""
    out = []
    builtin = {d["key"]: d for d in builtin_pack()["devices"]}
    for p in PROFILES:
        k = p["key"]
        if k in builtin:
            dev = dict(builtin[k])
            out.append((k, spec_filename(dev), one_device_pack(dev)))
        elif k in SPEC_DEVICES:
            s = SPEC_DEVICES[k]
            out.append((k, spec_filename(s), one_device_pack(spec_to_dict(s))))
    return out


def save_pack_to_library(spec):
    """把一台设备的协议写成「协议库/<设备名>.json」。
    导入的协议都在这里留一份 —— 以后想分享、想改、想重新导入都有原文件。"""
    ensure_pack_dir()
    path = os.path.join(PACK_DIR, spec_filename(spec))
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(one_device_pack(spec_to_dict(spec)), f, ensure_ascii=False, indent=2)
        return path
    except Exception as e:
        log_err("写入协议库失败: " + repr(e))
        return None


def save_builtin_to_library(key):
    """把一台内置设备的协议写进协议库（移除内置设备时先留一份，方便加回来）。"""
    for d in builtin_pack()["devices"]:
        if d["key"] != key:
            continue
        ensure_pack_dir()
        path = os.path.join(PACK_DIR, spec_filename(d))
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(one_device_pack(d), f, ensure_ascii=False, indent=2)
            return path
        except Exception as e:
            log_err("写入协议库失败: " + repr(e))
            return None
    return None


def seed_library():
    """协议库初始化：内置设备的协议各写一份进去（已存在的文件不覆盖）。"""
    ensure_pack_dir()
    for d in builtin_pack()["devices"]:
        path = os.path.join(PACK_DIR, spec_filename(d))
        if os.path.exists(path):
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(one_device_pack(d), f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_err("初始化协议库失败: " + repr(e))


def save_user_specs():
    """把导入的设备写回 devices.json（下次启动自动加载）。"""
    try:
        payload = {"format": PACK_FORMAT, "version": PACK_VERSION,
                   "devices": [spec_to_dict(SPEC_DEVICES[k]) for k in USER_SPEC_KEYS]}
        with open(DEVICES_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log_err("保存 devices.json 失败: " + repr(e))
        return False


def _spec_sig(spec):
    """设备的「身份指纹」：同一份指纹视为同一台设备，用于区分配置更新与同名冲突。"""
    tr = spec.get("transport") or {}
    m = spec.get("match") or {}
    return (str(tr.get("type") or "hid"), str(tr.get("port") or ""),
            m.get("vid"), m.get("pid"), m.get("usage_page"),
            m.get("min_in"), m.get("min_out"), tuple(m.get("product_contains") or []))


def _unique_spec_key(base, spec):
    """同名设备不再互相覆盖：同一台设备 → 复用原 key（视为更新配置）；
    真的是两台不同设备却取了同一个名字 → 自动加 -2 / -3 后缀。"""
    sig = _spec_sig(spec)
    if base not in SPEC_DEVICES:
        return base
    if _spec_sig(SPEC_DEVICES[base]) == sig:
        return base
    i = 2
    while True:
        cand = f"{base}-{i}"
        if cand not in SPEC_DEVICES:
            return cand
        if _spec_sig(SPEC_DEVICES[cand]) == sig:
            return cand
        i += 1


def add_user_spec(spec):
    """把 spec 加进内存与全局设备表（同一台设备重复导入 = 更新配置）。"""
    k = _unique_spec_key(spec["key"], spec)
    spec["key"] = k
    if k not in SPEC_DEVICES:
        USER_SPEC_KEYS.append(k)
    SPEC_DEVICES[k] = spec
    # 协议一览也要能看到导入的设备，否则那一行全是「—」
    m, rd = spec["match"], spec["read"]
    tr = spec.get("transport") or {}
    ids = []
    if tr.get("type") == "serial":
        ids.append("串口 " + (tr.get("port") or "手动选择 / 自动"))
    if m.get("vid") is not None:
        ids.append(f"0x{m['vid']:04X}")
    if m.get("pid") is not None:
        ids.append(f"/ 0x{m['pid']:04X}")
    if m.get("product_contains"):
        ids.append("名含 " + "、".join(m["product_contains"]))
    iface = []
    if tr.get("type") == "serial":
        iface.append(f"{tr.get('baud', 115200)} / {tr.get('bytesize', 8)}"
                     f"{tr.get('parity', 'N')}{tr.get('stopbits', '1')}")
    if m.get("usage_page") is not None:
        iface.append(f"usagePage 0x{m['usage_page']:04X}")
    if m.get("min_in") or m.get("min_out"):
        iface.append(f"in/out ≥ {m.get('min_in') or 0} / {m.get('min_out') or 0}")
    q = rd["query"][0] if rd["query"] else "(只听设备主动推送)"
    bt = rd["reply"]["battery"]
    field = f"byte[{bt['byte']}]"
    if int(bt.get("bytes") or 1) > 1:
        field += f"..{bt['byte'] + int(bt['bytes']) - 1}（{bt.get('order')} 端序）"
    if bt.get("mask"):
        field += f" & {bt['mask']}"
    if bt.get("shift"):
        field += f" >> {bt['shift']}"
    if bt.get("formula"):
        field += f"\n公式 {bt['formula']}"
    elif bt.get("map"):
        field += "\n查表映射"
    elif bt.get("read_as") == "voltage":
        field += (f"\n{bt.get('voltage_scale')}×V → "
                  f"{bt.get('voltage_min')}~{bt.get('voltage_max')}V")
    else:
        if (bt.get("mode") or "raw") != "raw" or float(bt.get("scale") or 1) != 1 \
                or float(bt.get("offset") or 0) != 0:
            field += f"\n取 {bt.get('mode')} ×{bt.get('scale')} +{bt.get('offset')}"
    ch = rd["reply"].get("charging") or {}
    if ch:
        field += f"\nbyte[{ch.get('byte')}] & {ch.get('mask')} → 充电"
    PROTOCOL_INFO[k] = {
        "ids":  "\n".join(ids) or "—",
        "iface": "\n".join(iface) or "—",
        "proto": "串口协议\n(JSON 描述)" if tr.get("type") == "serial" else "导入的协议\n(JSON 描述)",
        "query": q + (f"\n(共试 {len(rd['query'])} 帧)" if len(rd["query"]) > 1 else ""),
        "field": field,
        "note": "来自协议文件\n可右键移除",
    }
    for i, p in enumerate(PROFILES):
        if p["key"] == k:
            PROFILES[i] = {"key": k, "name": spec["name"], "type": spec["type"],
                           "wire": spec["wire"], "spec": True}
            break
    else:
        PROFILES.append({"key": k, "name": spec["name"], "type": spec["type"],
                         "wire": spec["wire"], "spec": True})


def remove_user_spec(key):
    SPEC_DEVICES.pop(key, None)
    if key in USER_SPEC_KEYS:
        USER_SPEC_KEYS.remove(key)
    for i, p in enumerate(PROFILES):
        if p["key"] == key:
            del PROFILES[i]
            break


def load_user_devices():
    """启动时加载 devices.json。返回 (加载台数, 错误列表)。"""
    if not os.path.exists(DEVICES_PATH):
        return 0, []
    specs, errors = load_pack(DEVICES_PATH)
    n = 0
    for s in specs:
        add_user_spec(s)
        n += 1
    # devices: []（把导入的设备都移除过）属于正常空表，不当错误记日志
    real = [e for e in errors if e != "文件里没有任何设备定义"]
    if real:
        log_err("devices.json 有 " + str(len(real)) + " 处问题: " + "; ".join(real))
    return n, errors


# 内置设备的「声明式读取参数」——纯净版为空：没有任何内置设备。
# 保留这个空表是为了让导出 / 恢复内置设备的代码路径继续可用；
# 导入的设备全部存在 SPEC_DEVICES（来自 devices.json 或手动导入的 JSON）。
BUILTIN_READ_SPECS = {}


def builtin_pack():
    """把内置设备导出成标准协议包 —— 既是备份，也是照着写新协议的模板。

    设备名 / VID / PID / 接口条件全部取自 BUILTIN_DEVICES，这里只补「怎么读」，
    所以改设备表时不用再改一遍导出内容。
    """
    devices = []
    for d in BUILTIN_DEVICES:
        read = BUILTIN_READ_SPECS.get(d["key"])
        if not read:
            continue
        pairs = d.get("pairs") or [(0, 0)]
        vid, pid = pairs[0]
        iface = d.get("iface") or {}
        devices.append({
            "key": d["key"], "name": d["name"], "type": d["type"],
            "wire": d["wire"], "protocol": d["protocol"],
            "match": {
                "vid": f"0x{vid:04X}",
                "pid": None if pid is None else f"0x{pid:04X}",
                "usage_page": (None if iface.get("usage_page") is None
                               else f"0x{iface['usage_page']:04X}"),
                "min_in": iface.get("min_in") or 0,
                "min_out": iface.get("min_out") or 0,
            },
            "read": read,
        })
    return {"format": PACK_FORMAT, "version": 1,
            "note": "内置设备的协议定义，改几个字段就能当新设备的模板",
            "devices": devices}


def probe_device(key, devs=None):
    """读一台设备的电量，返回界面要的结果字典。

    返回的 state 只有三种：connected / disconnected / nohid。
    读到电量时带 battery（%）与 charging；没读到时用 note 说明原因：

        implnoread   接口找到了，但设备没回包（多半是休眠 / 没开机）
        specnoread   导入的协议没读到（帧格式可能不匹配这个固件）
        noread       这台设备没有可用的读取器（只在协议一览里展示）

    内置设备与导入的设备在这里合流：前者按 BUILTIN_DEVICES 的 impl 找读取器，
    后者按 JSON 协议执行，界面完全不区分来源。
    """
    if not _WIN_HID:
        return {"state": "nohid"}
    if key in SPEC_DEVICES:                 # 导入的设备：走通用解释器
        return probe_spec(key, devs)
    prof = PROFILE_BY_KEY.get(key)
    if prof is None:
        return {"state": "disconnected"}
    if devs is None:
        devs = enumerate_hid()

    # 先判在不在（不打开句柄、不发帧，毫秒级）
    if _pick_iface(prof, devs) is None and _find_device(
            devs, prof.get("pairs") or [], keywords=prof.get("keywords")) is None:
        return {"state": "disconnected"}

    reader = READERS.get(prof.get("impl") or "")
    if reader is None:
        return {"state": "connected", "note": "noread"}
    try:
        got = reader(prof, devs)
    except Exception as e:
        log_err(f"读取 {key} 异常: " + repr(e))
        got = None
    if got:
        return {"state": "connected", "battery": got[0], "charging": bool(got[1])}
    return {"state": "connected", "note": "implnoread"}


def quick_state(key, devs):
    """只判"在不在"，不发查询帧 —— 让界面毫秒级先变绿，电量随后逐台补上。"""
    if not _WIN_HID:
        return {"state": "nohid"}
    if key in SPEC_DEVICES:
        spec = SPEC_DEVICES[key]
        return ({"state": "connected"} if spec_hits(spec, devs)
                else {"state": "disconnected"})
    prof = PROFILE_BY_KEY.get(key)
    if prof and _find_device(devs, prof.get("pairs") or [],
                             keywords=prof.get("keywords")):
        return {"state": "connected"}
    return {"state": "disconnected"}


DEEP_RIDS = (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
             0x08, 0x0A, 0x10, 0x13, 0x1E, 0x5A)
UNKNOWN_VIDS = set()   # 厂商私有页上的实验 VID：深挖时一并抓
# 注：这里只是「捕获时多看一眼」，不影响读取；需要别的 VID 直接往里加。


def _fmt(data):
    return " ".join(f"{b:02X}" for b in data)


def _hint(data):
    idx = [str(i) for i, b in enumerate(data) if 0 <= b <= 100]
    return f"  <- 0-100 的字节[{','.join(idx)}]" if idx else ""


def capture_raw(vids=None, keywords=None, deep=False):
    if not _WIN_HID:
        return "（当前平台不支持 HID 直连，需在 Windows 上运行）"
    all_devs = enumerate_hid()

    def _count(pairs, kws=None):
        n = 0
        for d in all_devs:
            hit = any(d["vid"] == v and (p is None or d["pid"] == p) for (v, p) in pairs)
            if not hit and kws:
                pr = (d["product"] or "").upper()
                hit = any(kw in pr for kw in kws)
            if hit:
                n += 1
        return n

    lines = ["===== 本机全部 HID 设备（用于核对 VID/PID）====="]
    if all_devs:
        for d in all_devs:
            lines.append(f"VID=0x{d['vid']:04X} PID=0x{d['pid']:04X} "
                         f"usagePage=0x{d['usage_page']:04X} in={d['in_len']} out={d['out_len']} "
                         f"| {d['product']}")
    else:
        lines.append("（枚举为空：HID 枚举可能失败，请告知我）")
    lines.append("")

    lines.append("===== 目标设备体检 =====")
    for prof in BUILTIN_DEVICES:
        n = _count(prof.get("pairs") or [], prof.get("keywords"))
        if n:
            lines.append(f"OK   {prof['name']}：找到 {n} 个接口")
        else:
            lines.append(f"MISS {prof['name']}：未在 HID 列表中出现")
    for spec in SPEC_DEVICES.values():
        hits = spec_hits(spec, all_devs)
        if hits:
            lines.append(f"OK   {spec['name']}：找到 {len(hits)} 个接口（导入的协议）")
        else:
            lines.append(f"MISS {spec['name']}：未在 HID 列表中出现（导入的协议）")
    lines.append("（MISS 常见原因：接收器没插好 / 设备没切到 2.4G 档 / 设备已休眠）")
    lines.append("")

    # 逐台真发一次帧，把结果写进报告 —— 读不到时，这一段最能说明问题出在哪
    lines.append("===== 直读尝试（每台发一次真实查询帧）=====")
    for prof in BUILTIN_DEVICES:
        reader = READERS.get(prof.get("impl") or "")
        if reader is None:
            lines.append(f"{prof['name']}：没有内置读取器（走导入的协议）")
            continue
        t0 = time.time()
        try:
            got = reader(prof, all_devs)
        except Exception as e:
            got = None
            lines.append(f"{prof['name']}：读取异常 {e!r}")
        dt = (time.time() - t0) * 1000
        if got:
            lines.append(f"{prof['name']}：读到 {got[0]}%"
                         + ("（充电中）" if got[1] else "") + f"，耗时 {dt:.0f} ms")
        else:
            lines.append(f"{prof['name']}：无有效回包，耗时 {dt:.0f} ms"
                         f"（设备可能休眠 / 没开机 / 帧格式不匹配这个固件）")
    for spec in SPEC_DEVICES.values():
        hits = spec_hits(spec, all_devs)
        got = None
        for d in hits:
            got = read_by_spec(spec, d)
            if got:
                break
        lines.append(f"{spec['name']}（导入的协议）："
                     + (f"读到 {got[0]}%" if got else "无有效回包"))
    lines.append("")

    def _match(d):
        if vids and d["vid"] in vids:
            return True
        if keywords:
            p = (d["product"] or "").upper()
            if any(kw in p for kw in keywords):
                return True
        return False

    lines.append("===== 目标设备的原始报文" + ("（深度探测）" if deep else "（feature + input）") + "=====")

    matched = False
    for d in all_devs:
        if not _match(d):
            continue
        matched = True
        vid = f"0x{d['vid']:04X}"; pid = f"0x{d['pid']:04X}"
        lines.append(f"--- VID {vid} PID {pid} | {d['product']} | usagePage=0x{d['usage_page']:04X} "
                     f"in={d['in_len']} out={d['out_len']} feat={d['feat_len']}")
        # 只对厂商自定义接口做深度抓取（标准键鼠/音频接口没有电量报文）
        if d["usage_page"] < 0xFF00:
            lines.append("  (标准接口，跳过)")
            lines.append("")
            continue
        h = _open_rw(d["path"])
        if h is None:
            lines.append("  (无法打开句柄，跳过)")
            lines.append("")
            continue
        try:
            ins, outs, feats = report_ids(d["path"])
            _hx = lambda s: " ".join(f"0x{x:02X}" for x in sorted(s)) if s else "无"
            lines.append(f"  报告ID：输入={_hx(ins)} 输出={_hx(outs)} 特征={_hx(feats)}")
            # feature 报文：feat_len 为 0 时按 64 试（有些设备 caps 不报 feature 长度）
            flen = d["feat_len"] if d["feat_len"] else 64
            fids = sorted(feats) if feats else list(range(0, 8))
            for rid in fids:
                data = _get_feature(h, flen, rid)
                if data and any(b != 0 for b in data[1:]):
                    lines.append(f"  feat rid={rid:02X}: " + _fmt(data) + _hint(data))
            ilen = d["in_len"] if d["in_len"] else 64
            iids = sorted(ins) if ins else list(range(0, 8))
            for rid in iids:
                buf = ctypes.create_string_buffer(ilen)
                buf[0] = rid & 0xFF
                if _HID.HidD_GetInputReport(h, buf, ilen):
                    data = buf.raw[:ilen]
                    if any(b != 0 for b in data[1:]):
                        lines.append(f"  in   rid={rid:02X}: " + _fmt(data) + _hint(data))
            # 被动监听：抓设备周期性主动上报的帧
            seen = set()
            polls = 25 if deep else 5
            for _ in range(polls):
                rf = _read_file_overlapped(h, ilen, 200)
                if rf and any(b != 0 for b in rf):
                    kb = bytes(rf)
                    if kb not in seen:
                        seen.add(kb)
                        lines.append("  push        : " + _fmt(rf) + _hint(rf))
            # 主动探测：协议未知的设备，用描述符里的真实报告 ID 发空载荷查询帧
            if deep and d["vid"] in UNKNOWN_VIDS and d["out_len"] > 0:
                olen = d["out_len"]
                oids = sorted(outs) if outs else [0]
                probe_ids = oids + [x for x in DEEP_RIDS if x not in oids]
                for rid in probe_ids:
                    for variant in (0, 1):
                        frame = bytearray(olen)
                        frame[0] = rid
                        if variant:
                            frame[1] = 0x01
                        if not _send_report(h, bytes(frame), olen):
                            lines.append(f"  probe rid={rid:02X} -> 系统拒绝（报告ID不对或无输出报文）")
                            continue
                        got = _read_file_overlapped(h, ilen, 150)
                        if got and any(b != 0 for b in got[1:]):
                            tag = "" if variant == 0 else " (载荷+01)"
                            lines.append(f"  probe rid={rid:02X}{tag} → " + _fmt(got) + _hint(got))
        finally:
            _K.CloseHandle(h)
        lines.append("")
    if not matched:
        lines.append("（未匹配到目标 VID/关键字，请参考上方全部设备列表）")
    return "\n".join(lines)


def _get_feature(h, length, rid=0):
    buf = ctypes.create_string_buffer(max(length, 8))
    buf[0] = rid & 0xFF
    if _HID.HidD_GetFeature(h, buf, len(buf)):
        return buf.raw[:len(buf)]
    return None


class _VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wintypes.USHORT), ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte), ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT), ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT), ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte), ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte), ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT), ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG), ("Units", wintypes.ULONG),
        ("LogicalMin", ctypes.c_long), ("LogicalMax", ctypes.c_long),
        ("PhysicalMin", ctypes.c_long), ("PhysicalMax", ctypes.c_long),
        ("u", wintypes.ULONG * 3),
    ]


def report_ids(path):
    """从 HID 描述符里读出真实的报告 ID 集合。
    ⚠️ 很多厂商接口的报告 ID 不是 0（常见 0x06 / 0x08 / 0x13 这类厂商自定义值），
    按 0 发帧会被系统直接拒掉（ERROR_INVALID_PARAMETER=87），等于白发。"""
    if not _WIN_HID:
        return set(), set(), set()
    h = _open_meta(path)
    if h is None:
        return set(), set(), set()
    inp, outp, feat = set(), set(), set()
    try:
        pp = ctypes.c_void_p()
        if not _HID.HidD_GetPreparsedData(h, ctypes.byref(pp)):
            return inp, outp, feat
        try:
            caps = _HIDP_CAPS()
            if _HID.HidP_GetCaps(pp, ctypes.byref(caps)) != _HIDP_STATUS_SUCCESS:
                return inp, outp, feat
            for kind, n, dst in ((0, caps.NumberInputValueCaps, inp),
                                 (1, caps.NumberOutputValueCaps, outp),
                                 (2, caps.NumberFeatureValueCaps, feat)):
                if not n:
                    continue
                arr = (_VALUE_CAPS * n)()
                ln = wintypes.USHORT(n)
                if _HID.HidP_GetValueCaps(kind, arr, ctypes.byref(ln), pp) != _HIDP_STATUS_SUCCESS:
                    continue
                for i in range(ln.value):
                    dst.add(arr[i].ReportID)
        finally:
            _HID.HidD_FreePreparsedData(pp)
    except Exception:
        pass
    finally:
        _K.CloseHandle(h)
    return inp, outp, feat


# =========================================================
#  刷新线程
# =========================================================
class RefreshWorker(QThread):
    """并行读全部设备：先秒级推"连接状态"，再逐台推真实电量。"""

    one = Signal(str, dict)       # key, result（单台完成即推）
    finished = Signal(list, str)  # 汇总结果, 错误信息

    def run(self):
        out = []
        err = ""
        try:
            out, err = self._run_inner()
        except Exception as e:
            # 任何意外都不能让线程静默死掉：界面靠 finished 收尾，收不到就永远"正在读取"
            log_err("刷新线程异常: " + repr(e))
            err = err or ("读取出错：" + str(e))
        finally:
            if not out:
                out = [{"key": p["key"], "result": {"state": "disconnected"}} for p in PROFILES]
            try:
                self.finished.emit(out, err)
            except Exception as e:
                log_err("finished.emit 失败: " + repr(e))

    def _run_inner(self):
        out = []
        err = ""
        try:
            devs = enumerate_hid()
        except Exception as e:
            devs = []
            err = "枚举 HID 失败：" + str(e)
            log_err("enumerate_hid 异常: " + repr(e))
        if not devs:
            # 没有 HID 设备不代表没设备可读：串口接收器不走 HID 枚举
            if not any((s.get("transport") or {}).get("type") == "serial"
                       for s in SPEC_DEVICES.values()):
                log_err("enumerate_hid 返回空（未识别到任何 HID 设备）")

        # 第一段：立刻把"在不在"推给界面（不发查询帧，毫秒级）
        for p in PROFILES:
            try:
                q = quick_state(p["key"], devs)
            except Exception:
                q = {"state": "disconnected"}
            self.one.emit(p["key"], q)

        # 第二段：全部设备并行读电量，谁先回来谁先上屏
        # （纯净版可能 0 台设备：此时直接返回空结果，避免 max_workers=0 崩溃）
        if not PROFILES:
            return [], err

        def work(p):
            key = p["key"]
            t0 = time.time()
            try:
                res = probe_device(key, devs)
            except Exception as e:
                log_err(f"probe {key} 异常: " + repr(e))
                return key, {"state": "disconnected", "error": str(e)}
            dt = (time.time() - t0) * 1000
            if dt >= 700:      # 只记慢的，便于看是哪台拖后腿
                log_err(f"probe {key} 耗时 {dt:.0f}ms")
            return key, res

        results = {}
        # 线程数封顶：设备再多也只开 8 条线程。
        # 每台设备本来就要发帧 + 等回包（几百毫秒），开满 N 条线程只会让
        # 系统调度变慢、串口/HID 句柄暴涨，并不会更快。
        workers = max(1, min(len(PROFILES), 8))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, p) for p in PROFILES]
            for fu in as_completed(futs):
                try:
                    key, res = fu.result()
                except Exception as e:
                    log_err("probe 汇总异常: " + repr(e))
                    continue
                results[key] = res
                self.one.emit(key, res)

        for p in PROFILES:
            out.append({"key": p["key"], "result": results.get(p["key"], {"state": "disconnected"})})
        return out, err


# =========================================================
#  现代风格 UI
# =========================================================
STYLE = """
/* ---- 黑色简约风：中性浅灰底 / 近黑文字 / 克制的语义色 ---- */
QWidget {
    background: #F3F3F3;
    color: #1A1A1A;
    font-family: __FONT__;
}
QPushButton {
    font-family: __FONT__;
}
QFrame#card {
    background: #FFFFFF;
    border: 1px solid #E6E6E6;
    border-radius: 12px;
}
QFrame#card:hover {
    border: 1px solid #D0D0D0;
}
QLabel#title { font-size: 17px; font-weight: 700; color: #1A1A1A; background: transparent; }
QLabel#subtitle { font-size: 12px; color: #5C5C5C; }
QLabel#name { font-size: 14px; font-weight: 700; color: #1A1A1A; }
QLabel#meta { font-size: 12px; color: #5C5C5C; }
QLabel#pct { font-size: 22px; font-weight: 800; color: #1A1A1A; }
QProgressBar {
    background: #E6E6E6;
    border-radius: 6px;
    border: none;
    text-align: center;
}
QProgressBar::chunk { border-radius: 6px; }
/* 主操作：近黑实底（黑色简约风的唯一强调色） */
QPushButton#primary {
    background: #1A1A1A; color: #FFFFFF; border: none;
    border-radius: 8px; padding: 8px 16px; font-weight: 600; font-size: 13px;
}
QPushButton#primary:hover { background: #333333; }
QPushButton#primary:pressed { background: #000000; }
/* 次操作：白底细边，悬停极淡灰 */
QPushButton#ghost {
    background: #FFFFFF; color: #1A1A1A; border: 1px solid #E0E0E0;
    border-radius: 8px; padding: 8px 14px; font-weight: 600; font-size: 13px;
}
QPushButton#ghost:hover { background: #F5F5F5; border: 1px solid #D0D0D0; }
QPushButton#ghost:pressed { background: #ECECEC; }
/* 兼容旧类名：与 ghost 一致 */
QPushButton#capturetop {
    background: #FFFFFF; color: #1A1A1A; border: 1px solid #E0E0E0;
    border-radius: 8px; padding: 8px 14px; font-weight: 600; font-size: 13px;
}
QPushButton#capturetop:hover { background: #F5F5F5; border: 1px solid #D0D0D0; }
QScrollArea { border: none; background: transparent; }
QScrollArea QWidget { background: transparent; }
/* 报文框保持深色（像终端日志），刻意与浅色界面拉开层次 */
QPlainTextEdit { background: #0F172A; color: #E2E8F0; border-radius: 10px;
    border: 1px solid #334155; padding: 8px; font-family: __MONO__; }
QLabel#hint { font-size: 12px; color: #5C5C5C; }

/* 右键菜单改成自绘 CardMenu（见 CardMenu 类），不再用 QMenu */
/* ---- 卡片下方那行「导入设备」：虚线边框，像一张待填的空卡片 ---- */
QFrame#adddev {
    background: rgba(255, 255, 255, 0.6);
    border: 1px dashed #C8C8C8;
    border-radius: 12px;
}
QFrame#adddev:hover {
    background: #F5F5F5;
    border: 1px dashed #1A1A1A;
}
QLabel#adddevtext { font-size: 13px; font-weight: 600; color: #1A1A1A; background: transparent; }
QLabel#adddevhint { font-size: 11px; color: #8A8A8A; background: transparent; }

QDialog#importdlg { background: #F3F3F3; }
QTableWidget {
    background: #FFFFFF; border: 1px solid #E6E6E6; border-radius: 10px;
    gridline-color: #F0F0F0; font-size: 12px;
}
QHeaderView::section {
    background: #F8F8F8; color: #5C5C5C; border: none;
    border-bottom: 1px solid #E6E6E6; padding: 6px 8px; font-weight: 600; font-size: 12px;
}

/* ---- 滚动条：全局统一成细圆角滑块，不再用系统那个粗灰条 ---- */
QScrollBar:vertical {
    background: transparent; width: 10px;
    margin: 2px 2px 2px 0; border: none;
}
QScrollBar::handle:vertical {
    background: #C8C8C8; border-radius: 4px; min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: #A0A0A0; }
QScrollBar:horizontal {
    background: transparent; height: 10px;
    margin: 0 2px 2px 2px; border: none;
}
QScrollBar::handle:horizontal {
    background: #C8C8C8; border-radius: 4px; min-width: 32px;
}
QScrollBar::handle:horizontal:hover { background: #A0A0A0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; background: none; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar::up-arrow, QScrollBar::down-arrow,
QScrollBar::left-arrow, QScrollBar::right-arrow { width: 0; height: 0; background: none; }

/* 深色报文框里换一套：轨道同底、滑块中灰，不然会露出外层浅色 */
QPlainTextEdit QScrollBar:vertical {
    background: #0F172A; width: 10px; margin: 0; border: none;
}
QPlainTextEdit QScrollBar::handle:vertical {
    background: #334155; border-radius: 4px; min-height: 30px; margin: 4px 3px;
}
QPlainTextEdit QScrollBar::handle:vertical:hover { background: #475569; }
QPlainTextEdit QScrollBar::add-line:vertical,
QPlainTextEdit QScrollBar::sub-line:vertical { height: 0; background: none; }
QPlainTextEdit QScrollBar::add-page:vertical,
QPlainTextEdit QScrollBar::sub-page:vertical { background: transparent; }
QPlainTextEdit QScrollBar:horizontal {
    background: #0F172A; height: 10px; margin: 0; border: none;
}
QPlainTextEdit QScrollBar::handle:horizontal {
    background: #334155; border-radius: 4px; min-width: 30px; margin: 3px 4px;
}

/* ---- 下拉列表（自绘的弹出层） ---- */
QListWidget#combopop {
    background: #FFFFFF; border: 1px solid #E6E6E6; border-radius: 12px;
    padding: 6px; outline: none; font-size: 13px; color: #1A1A1A;
}
QListWidget#combopop::item {
    border-radius: 8px; padding: 7px 10px; margin: 1px 2px;
}
QListWidget#combopop::item:hover { background: #F5F5F5; }
QListWidget#combopop::item:selected { background: #EFEFEF; color: #1A1A1A; font-weight: 600; }

QToolTip {
    background: #1A1A1A; color: #F3F3F3; border: none;
    border-radius: 6px; padding: 5px 8px; font-size: 12px;
}

/* ---- 统一小弹窗 AppDialog（替代 QMessageBox）---- */
QDialog#appdlg { background: #FFFFFF; }
QLabel#dlgbody {
    color: #333333; font-size: 13px; background: transparent;
}
QPushButton#danger {
    background: #C42B1C; color: #FFFFFF; border: none;
    border-radius: 8px; padding: 8px 18px; font-weight: 600; font-size: 13px;
}
QPushButton#danger:hover { background: #A82318; }
QPushButton#danger:pressed { background: #8C1C12; }

/* ---- 右下角提示条 Toast（替代低电量弹窗）---- */
QFrame#toastcard {
    background: #FFFFFF; border: 1px solid #E6E6E6; border-radius: 12px;
}
QLabel#toasttitle {
    font-size: 13px; font-weight: 700; color: #C77A00; background: transparent;
}
QLabel#toastbody {
    font-size: 12px; color: #5C5C5C; background: transparent;
}
QLabel#toastrow {
    font-size: 12px; color: #333333; background: transparent;
}
QLabel#toastpct {
    font-size: 12px; font-weight: 700; color: #C77A00; background: transparent;
}

QTableWidget {
    background: #FFFFFF; border: 1px solid #E6E6E6; border-radius: 12px;
    gridline-color: #F0F0F0; font-size: 12px; color: #333333;
}
QTableWidget::item { padding: 6px 8px; }
QHeaderView::section {
    background: #F8F8F8; color: #5C5C5C; border: none;
    border-bottom: 1px solid #E6E6E6; padding: 8px 6px;
    font-size: 12px; font-weight: 700;
}

/* ---- 设置面板 ---- */
QDialog#settingsdlg { background: #FFFFFF; }
QLabel#secthead {
    font-size: 12px; font-weight: 700; color: #8A8A8A;
    background: transparent; padding-top: 2px;
}
QLabel#settitle { font-size: 14px; font-weight: 600; color: #1A1A1A; background: transparent; }
QLabel#pctsuffix { font-size: 12px; color: #5C5C5C; background: transparent; }
QFrame#setsep { background: #EAEAEA; border: none; }
QLineEdit#numin {
    background: #F8F8F8; border: 1px solid #E0E0E0; border-radius: 8px;
    padding: 5px 4px; font-size: 14px; font-weight: 700; color: #1A1A1A;
    selection-background-color: #E0E0E0; selection-color: #1A1A1A;
}
QLineEdit#numin:focus { border: 1px solid #1A1A1A; background: #FFFFFF; }
QPushButton#mini {
    background: #FFFFFF; color: #1A1A1A; border: 1px solid #E0E0E0;
    border-radius: 8px; padding: 6px 12px; font-weight: 600; font-size: 12px;
}
QPushButton#mini:hover { background: #F5F5F5; border: 1px solid #D0D0D0; }

/* ---- 托盘右键菜单：用系统原生菜单，只补一层边框和内边距 ---- */
QMenu {
    background: #FFFFFF; border: 1px solid #E6E6E6; padding: 5px;
}
QMenu::item {
    padding: 7px 26px 7px 14px; border-radius: 6px;
    font-size: 13px; color: #1A1A1A;
}
QMenu::item:selected { background: #F0F0F0; }
QMenu::item:disabled { color: #8A8A8A; }
QMenu::separator { height: 1px; background: #EAEAEA; margin: 5px 8px; }
"""

STYLE = STYLE.replace("__FONT__", FONT_CSS).replace("__MONO__", MONO_CSS)


class _ComboPopup(QFrame):
    """下拉框的弹出层：自己画圆角白卡，不用系统那个方角灰框。"""

    picked = Signal(int)
    ITEM_H = 32

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)   # 留出投影位置，否则会被窗口裁掉
        lay.setSpacing(0)
        self.list = QListWidget()
        self.list.setObjectName("combopop")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.itemClicked.connect(self._on_click)
        shadow = QGraphicsDropShadowEffect(self.list)
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(15, 23, 42, 26))
        self.list.setGraphicsEffect(shadow)
        lay.addWidget(self.list)

    def _on_click(self, item):
        self.picked.emit(int(item.data(Qt.UserRole)))
        self.close()

    def open_for(self, combo):
        self.list.clear()
        for i in range(combo.count()):
            it = QListWidgetItem(combo.itemText(i))
            it.setData(Qt.UserRole, i)
            it.setSizeHint(QSize(0, self.ITEM_H))
            self.list.addItem(it)
        cur = combo.currentIndex()
        if cur >= 0:
            self.list.setCurrentRow(cur)
            self.list.scrollToItem(self.list.item(cur), QAbstractItemView.PositionAtCenter)
        n = max(combo.count(), 1)
        inner_h = min(n * (self.ITEM_H + 2) + 14, 366)
        pad = 12                                  # 与上面 layout 的边距对应
        self.setFixedSize(combo.width() + pad * 2, inner_h + pad * 2)
        base = combo.mapToGlobal(QPoint(0, 0))
        top = QPoint(base.x() - pad, base.y() + combo.height() + 6 - pad)
        scr = combo.screen().availableGeometry()
        if top.y() + self.height() > scr.bottom():
            top.setY(base.y() - inner_h - 6 + pad)
        if top.x() + self.width() > scr.right():
            top.setX(scr.right() - self.width())
        # 极端情况（窗口被拖到屏幕边缘）也保证整块露在屏内
        top.setX(max(top.x(), scr.left()))
        top.setY(min(max(top.y(), scr.top()), scr.bottom() - self.height()))
        self.move(top)
        self.show()
        self.setFocus()

    def keyPressEvent(self, e):
        k = e.key()
        n = self.list.count()
        row = self.list.currentRow()
        if k == Qt.Key_Escape:
            self.close()
            return
        if k in (Qt.Key_Return, Qt.Key_Enter):
            if 0 <= row < n:
                self.picked.emit(row)
            self.close()
            return
        if k in (Qt.Key_Up, Qt.Key_Down):
            row = row - 1 if k == Qt.Key_Up else row + 1
            row = max(0, min(n - 1, row))
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.item(row))
            return
        if k in (Qt.Key_Home, Qt.Key_End):
            row = 0 if k == Qt.Key_Home else n - 1
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.item(row))
            return
        super().keyPressEvent(e)


class Combo(QComboBox):
    """自绘下拉框：圆角、手绘 chevron、自绘弹出层。

    不用 Qt 原生 popup，是因为它在 Windows 下是方角 + 系统滚动条，
    和这套圆角卡片放在一起很扎眼。"""

    H = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.setMinimumWidth(124)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._popup = _ComboPopup()
        self._popup.picked.connect(self._on_picked)
        self._popup.installEventFilter(self)
        self._open = False
        self._hover = False
        self._focus = False

    def focusInEvent(self, e):
        self._focus = True
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._focus = False
        self.update()
        super().focusOutEvent(e)

    def _on_picked(self, idx):
        self.setCurrentIndex(idx)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bg = QColor("#F8F8F8") if (self._open or self._hover) else QColor("#FFFFFF")
        border = (QColor("#1A1A1A") if (self._open or self._focus) else
                  QColor("#D0D0D0") if self._hover else QColor("#E0E0E0"))
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0), 10, 10)

        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#1A1A1A"))
        tw = self.width() - 12 - 28
        fm = p.fontMetrics()
        text = fm.elidedText(self.currentText(), Qt.ElideRight, int(max(tw, 10)))
        p.drawText(QRectF(12, 0, tw, self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, text)

        cx, cy = self.width() - 16.0, self.height() / 2.0
        pen = QPen(QColor("#1A1A1A") if (self._hover or self._open)
                   else QColor("#8A8A8A"))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        if self._open:
            path.moveTo(cx - 4.5, cy + 2.0)
            path.lineTo(cx, cy - 2.2)
            path.lineTo(cx + 4.5, cy + 2.0)
        else:
            path.moveTo(cx - 4.5, cy - 2.0)
            path.lineTo(cx, cy + 2.2)
            path.lineTo(cx + 4.5, cy - 2.0)
        p.drawPath(path)
        p.end()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.showPopup()
            return
        super().mousePressEvent(e)

    def wheelEvent(self, e):
        e.ignore()   # 鼠标划过时误滚别改档位

    def keyPressEvent(self, e):
        if not self._open and e.key() in (Qt.Key_Space, Qt.Key_Return,
                                          Qt.Key_Enter, Qt.Key_Down):
            self.showPopup()
            return
        super().keyPressEvent(e)

    def showPopup(self):
        if self._open:
            self.hidePopup()
            return
        self._open = True
        self.update()
        self._popup.open_for(self)

    def hidePopup(self):
        self._open = False
        # 退出瞬间 Qt 可能已经把弹出层销毁了，这里兜一下，别在关闭时抛异常
        try:
            self._popup.close()
            self.update()
        except RuntimeError:
            pass

    def eventFilter(self, obj, ev):
        if obj is self._popup and ev.type() == QEvent.Hide:
            self._open = False
            self.update()
        return super().eventFilter(obj, ev)


class Bar(QWidget):
    """自绘圆角电量条。不用 QProgressBar，避免 QSS 圆角把两端裁掉。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(136, 8)
        self._pct = 0
        self._col = QColor("#CBD5E1")

    def set_value(self, pct, col):
        self._pct = 0 if pct is None else max(0, min(100, int(pct)))
        self._col = QColor(col)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        r = h / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E8EDF3"))
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        if self._pct > 0:
            bw = max(w * self._pct / 100.0, float(h))
            p.setBrush(self._col)
            p.drawRoundedRect(QRectF(0, 0, bw, h), r, r)
        p.end()


class Badge(QWidget):
    """状态徽章：圆角胶囊 + 矢量小圆点 + 文字。

    以前用 emoji（🟢/🟡/⚪）当圆点，Qt 按字形宽度裁切彩色 emoji，
    11px 时右侧会缺一块、看着不圆；这里全部自绘，圆一定是正圆。
    """

    PAD_L = 8
    PAD_R = 9
    GAP = 5
    DOT = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._bg = QColor("#F1F5F9")
        self._fg = QColor("#64748B")
        self._dot = QColor("#94A3B8")
        f = QFont(self.font())
        f.setFamilies(FONT_FAMILIES)
        f.setPixelSize(11)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)
        self.setAttribute(Qt.WA_Hover, False)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_badge(self, text, bg, fg, dot):
        self._text = text
        self._bg = QColor(bg)
        self._fg = QColor(fg)
        self._dot = QColor(dot)
        self.setToolTip(text)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        w = (self.PAD_L + self.DOT + self.GAP
             + fm.horizontalAdvance(self._text) + self.PAD_R)
        return QSize(w, fm.height() + 5)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.height() / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(self._bg)
        p.drawRoundedRect(r, rad, rad)

        d = float(self.DOT)
        cx = r.left() + self.PAD_L + d / 2.0
        cy = r.center().y()
        p.setBrush(self._dot)
        p.drawEllipse(QPointF(cx, cy), d / 2.0, d / 2.0)

        tx = cx + d / 2.0 + self.GAP
        p.setPen(self._fg)
        p.drawText(QRectF(tx, r.top(), max(r.right() - tx - self.PAD_R, 1.0), r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self._text)
        p.end()


# ------------------------- 右键菜单（自绘） -------------------------
# Windows 原生菜单是方角 + 系统灰，跟这套圆角卡片放一起很突兀，
# 干脆自己画：圆角白卡 + 投影 + 手绘线性图标 + 危险项标红。
# ------------------------- 矢量线性图标（黑色简约风） -------------------------
# ---------------------------------------------------------------------------
#  矢量图标系统 —— 标准开源 outline 图标
#  Tabler Icons (MIT) / Material Icons Outlined (Apache-2.0)，可免费商用。
#  与交付效果图使用同一套 24×24 路径：fill=none、stroke-width=2、
#  round cap + round join、currentColor 单色描边，线条粗细完全统一。
#  渲染走 QSvgRenderer 按目标尺寸矢量光栅化，不是位图缩放。
# ---------------------------------------------------------------------------
_SVG_GLYPHS = {
    # ── 顶部功能按钮 ──
    "antenna":  '<path d="M4 18v-3M8 18v-6M12 18v-9M16 18v-5M20 18v-7"/><path d="M3 21h18"/>',
    "list":     '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    "info":     '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "sliders":  '<path d="M4 6h6M14 6h6M4 12h2M10 12h10M4 18h8M16 18h4"/>'
                '<circle cx="12" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="14" cy="18" r="2"/>',
    "power":    '<path d="M12 3v9"/><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/>',
    "refresh":  '<path d="M20 11a8.1 8.1 0 0 0-15.5-2M4 5v4h4"/>'
                '<path d="M4 13a8.1 8.1 0 0 0 15.5 2M20 19v-4h-4"/>',
    # ── 设备类型 ──
    "keyboard": '<rect x="2" y="6" width="20" height="12" rx="2"/>'
                '<path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M6 14h10M18 14h.01"/>',
    "mouse":    '<rect x="6" y="2" width="12" height="20" rx="6"/><path d="M12 6v4"/>',
    "headphone": '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/>'
                 '<rect x="3" y="13" width="4" height="7" rx="1.5"/>'
                 '<rect x="17" y="13" width="4" height="7" rx="1.5"/>',
    "gamepad":  '<path d="M6.5 7h11a4.5 4.5 0 0 1 4.4 5.4l-1 4.3a2.4 2.4 0 0 1-4.2.9L15.4 16H8.6l-1.3 1.6a2.4 2.4 0 0 1-4.2-.9l-1-4.3A4.5 4.5 0 0 1 6.5 7z"/>'
                '<path d="M8 11v3M6.5 12.5h3M16 11.5h.01M18 13.5h.01"/>',
    "plug":     '<path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0V8zM12 16v5"/>',
    "battery":  '<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M21 10v4"/>'
                '<rect x="4" y="9" width="7" height="6" rx="1" fill="currentColor" stroke="none"/>',
    # ── 通用控件 ──
    "plus":     '<path d="M12 5v14M5 12h14"/>',
    "minus":    '<path d="M5 12h14"/>',
    "x":        '<path d="M6 6l12 12M18 6L6 18"/>',
    "chevron":  '<path d="M6 9l6 6 6-6"/>',
    "chevron_right": '<path d="M9 6l6 6-6 6"/>',
    "download": '<path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2M7 11l5 5 5-5M12 4v12"/>',
    "trash":    '<path d="M4 7h16M10 11v6M14 11v6M5 7l1 13a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1l1-13"/>'
                '<path d="M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"/>',
}
# 旧调用名 → 标准图标名
_SVG_ALIAS = {
    "export": "download", "close": "x", "headphones": "headphone",
    "settings": "sliders", "capture": "antenna",
}
_GLYPH_CACHE = {}


def _svg_bytes(name, color, px):
    """拼一张指定像素尺寸的标准 SVG（currentColor 换成实际颜色）。"""
    nm = _SVG_ALIAS.get(name, name)
    body = _SVG_GLYPHS.get(nm)
    if not body:
        return None
    g = ('<g fill="none" stroke="%s" stroke-width="2" stroke-linecap="round" '
         'stroke-linejoin="round">%s</g>') % (color, body.replace("currentColor", color))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            'width="%d" height="%d">%s</svg>' % (px, px, g)).encode("utf-8")


def _glyph_pixmap(name, color, size=24, scale=1):
    """SVG 矢量 → 按目标尺寸光栅化。scale>1 时标注 DPR（仅供 QLabel 直贴高清用）。

    注意：QIcon 里只登记 DPR=1 的位图（见 _glyph）。给 QIcon 塞高 DPR 位图会让 Qt
    把「设备像素」当成「逻辑尺寸」登记，availableSizes 全变成放大倍数，托盘取图错配。
    """
    big = int(size * scale)
    pm = QPixmap(big, big)
    pm.fill(Qt.transparent)
    data = _svg_bytes(name, color, big)
    if data:
        r = QSvgRenderer(QByteArray(data))
        if r.isValid():
            p = QPainter(pm)
            r.render(p)
            p.end()
            if scale > 1:
                pm.setDevicePixelRatio(scale)
            return pm
    # 兜底：SVG 未收录时仍用老的手绘路径，保证不出现空白图标
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(big / 24.0, big / 24.0)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    _draw_glyph(p, name)
    p.end()
    if scale > 1:
        pm.setDevicePixelRatio(scale)
    return pm


def _glyph(name, color=INK, size=24):
    """QIcon：登记 1× / 2× 两张 DPR=1 位图，Qt 按屏幕 DPR 自动取，不做位图缩放。"""
    ck = (name, color, size)
    if ck in _GLYPH_CACHE:
        return _GLYPH_CACHE[ck]
    icon = QIcon()
    icon.addPixmap(_glyph_pixmap(name, color, size))
    icon.addPixmap(_glyph_pixmap(name, color, size * 2))
    _GLYPH_CACHE[ck] = icon
    return icon


def _draw_glyph(p, name):
    """在 24×24 坐标系里描边绘制。name 取下列之一。"""
    if name == "export":
        name = "download"
    if name == "keyboard":
        rr = QPainterPath()
        rr.addRoundedRect(QRectF(2.5, 7.5, 19, 9.5), 2, 2)
        p.drawPath(rr)
        for x in (6, 9.5, 13, 16.5):
            p.drawLine(QPointF(x, 9.8), QPointF(x, 11.4))
        p.drawLine(QPointF(7, 14.2), QPointF(17, 14.2))
    elif name == "mouse":
        rr = QPainterPath()
        rr.addRoundedRect(QRectF(8, 3, 8, 18), 4, 4)
        p.drawPath(rr)
        p.drawLine(QPointF(8, 8), QPointF(16, 8))
        p.drawLine(QPointF(12, 3.5), QPointF(12, 6.5))
    elif name == "headphone":
        p.drawArc(QRectF(5, 6, 14, 14), 180 * 16, -180 * 16)   # 头梁（上半弧）
        p.drawRoundedRect(QRectF(3.5, 13, 4, 6), 1.5, 1.5)     # 左耳罩
        p.drawRoundedRect(QRectF(16.5, 13, 4, 6), 1.5, 1.5)    # 右耳罩
    elif name == "gamepad":
        rr = QPainterPath()
        rr.addRoundedRect(QRectF(3, 9, 18, 6.5), 3, 3)
        p.drawPath(rr)
        p.drawLine(QPointF(7.5, 11), QPointF(7.5, 14))         # 左十字
        p.drawLine(QPointF(6, 12.5), QPointF(9, 12.5))
        p.drawEllipse(QPointF(16, 12), 1.1, 1.1)               # 右按键
    elif name == "plug":
        rr = QPainterPath()
        rr.addRoundedRect(QRectF(8.5, 8, 7, 8), 1.5, 1.5)      # 插头壳
        p.drawPath(rr)
        p.drawLine(QPointF(10.5, 8), QPointF(10.5, 5))         # 插脚
        p.drawLine(QPointF(13.5, 8), QPointF(13.5, 5))
        p.drawLine(QPointF(12, 16), QPointF(12, 20))          # 线缆
    elif name == "battery":
        rr = QPainterPath()
        rr.addRoundedRect(QRectF(3, 8, 16, 8), 2, 2)          # 电池身
        p.drawPath(rr)
        p.drawLine(QPointF(19, 11), QPointF(20.5, 11))         # 正极
        p.drawLine(QPointF(19, 13), QPointF(20.5, 13))
        p.setBrush(QColor(p.pen().color()))                   # 电量块（填充）
        p.drawRoundedRect(QRectF(5, 10, 9, 4), 1, 1)
        p.setBrush(Qt.NoBrush)
    elif name == "refresh":
        p.drawArc(QRectF(5, 5, 14, 14), 60 * 16, 300 * 16)     # 近整圆（右侧留口）
        p.drawLine(QPointF(16, 12), QPointF(19, 12))           # 箭头（朝右）
        p.drawLine(QPointF(17.5, 10.5), QPointF(19, 12))
        p.drawLine(QPointF(17.5, 13.5), QPointF(19, 12))
    elif name == "antenna":
        p.drawEllipse(QPointF(12, 18), 1.3, 1.3)              # 信号源
        p.drawArc(QRectF(8, 14, 8, 8), 200 * 16, 140 * 16)    # 内弧
        p.drawArc(QRectF(4, 10, 16, 16), 200 * 16, 140 * 16)  # 外弧
    elif name == "list":
        for y, x2 in ((7, 18), (12, 18), (17, 14)):
            p.drawLine(QPointF(6, y), QPointF(x2, y))
        p.drawEllipse(QPointF(19, 7), 1, 1)
    elif name == "info":
        p.drawEllipse(QPointF(12, 12), 8, 8)
        p.drawLine(QPointF(12, 10.5), QPointF(12, 16))         # i 竖
        p.drawEllipse(QPointF(12, 8), 0.9, 0.9)                # i 点
    elif name == "sliders":
        for y, x in ((7, 9), (12, 14), (17, 11)):
            p.drawLine(QPointF(5, y), QPointF(19, y))
            p.drawEllipse(QPointF(x, y), 2, 2)
    elif name == "power":
        p.drawArc(QRectF(5, 5, 14, 14), 135 * 16, 270 * 16)    # 环（顶部留口）
        p.drawLine(QPointF(12, 3.5), QPointF(12, 12))
    elif name == "plus":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(5, 12), QPointF(19, 12))
    elif name == "download":
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        p.drawLine(QPointF(8.5, 11.5), QPointF(12, 15))
        p.drawLine(QPointF(15.5, 11.5), QPointF(12, 15))
        p.drawLine(QPointF(6, 18), QPointF(18, 18))            # 托盘底
    elif name == "trash":
        p.drawLine(QPointF(5, 7), QPointF(19, 7))             # 盖
        p.drawRoundedRect(QRectF(10, 4.5, 4, 2.5), 0.5, 0.5) # 把手
        p.drawLine(QPointF(7, 7), QPointF(8, 19))
        p.drawLine(QPointF(17, 7), QPointF(16, 19))
        p.drawLine(QPointF(8, 19), QPointF(16, 19))
    elif name == "close":
        p.drawLine(QPointF(6, 6), QPointF(18, 18))
        p.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif name == "chevron":
        p.drawLine(QPointF(9, 6), QPointF(15, 12))
        p.drawLine(QPointF(15, 12), QPointF(9, 18))
    else:
        p.drawEllipse(QPointF(12, 12), 3, 3)                  # 兜底：圆点


# 旧名兼容（右键菜单仍通过 _menu_glyph 调用）
def _menu_glyph(kind, color):
    return _glyph(kind, color, 16)


class _CtxHeader(QWidget):
    """菜单抬头：设备名 + 一行灰色小字（类型 · 状态）+ 底部发丝线。"""

    def __init__(self, name, sub="", parent=None):
        super().__init__(parent)
        self._name = name
        self._sub = sub
        self.setFixedHeight(46 if sub else 32)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)   # 抬头不参与点击

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width() - 24
        f = QFont(self.font())
        f.setFamilies(FONT_FAMILIES)
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#0F172A"))
        fm = p.fontMetrics()
        y = 7 if self._sub else 0
        h = 18 if self._sub else self.height()
        p.drawText(QRectF(12, y, w, h), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self._name, Qt.ElideRight, int(w)))
        if self._sub:
            f2 = QFont(self.font())
            f2.setFamilies(FONT_FAMILIES)
            f2.setPixelSize(10)
            p.setFont(f2)
            p.setPen(QColor("#94A3B8"))
            p.drawText(QRectF(12, y + 19, w, 15), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self._sub, Qt.ElideRight, int(w)))
            # 发丝线：画出来比塞个 1px 的 QFrame 靠谱（QFrame 会被拉粗）
            pen = QPen(QColor("#EEF2F7"))
            pen.setWidthF(1.0)
            p.setPen(pen)
            p.drawLine(QPointF(6, self.height() - 0.5),
                       QPointF(self.width() - 6, self.height() - 0.5))
        p.end()


class _CtxRow(QWidget):
    """菜单里的一行：图标 + 标题 + 可选小字说明，悬停整行淡淡上色。"""

    clicked = Signal()
    H = 38
    H_HINT = 50

    def __init__(self, kind, title, hint="", danger=False, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._title = title
        self._hint = hint
        self._danger = danger
        self._hover = False
        self.setFixedHeight(self.H_HINT if hint else self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        f = QFont(self.font())
        f.setFamilies(FONT_FAMILIES)
        self.setFont(f)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#FBEDEC" if self._danger else "#F5F5F5"))
            p.drawRoundedRect(r, 9, 9)
        if self._danger:
            ic_col = "#C42B1C" if self._hover else "#DC2626"
        else:
            ic_col = "#1A1A1A" if self._hover else "#5C5C5C"
        _menu_glyph(self.kind, ic_col).paint(
            p, QRect(13, int(r.center().y() - 8), 16, 16))
        tx = 13 + 16 + 11
        tw = max(self.width() - tx - 13, 10)
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#C42B1C" if self._danger else "#1A1A1A"))
        fm = p.fontMetrics()
        if self._hint:
            p.drawText(QRectF(tx, r.top() + 8, tw, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(self._title, Qt.ElideRight, int(tw)))
            f2 = QFont(self.font())
            f2.setPixelSize(10)
            p.setFont(f2)
            p.setPen(QColor("#94A3B8"))
            p.drawText(QRectF(tx, r.top() + 26, tw, 15), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self._hint, Qt.ElideRight, int(tw)))
        else:
            p.drawText(QRectF(tx, r.top(), tw, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(self._title, Qt.ElideRight, int(tw)))
        p.end()


class CardMenu(QFrame):
    """右键菜单：圆角白卡 + 投影，条目自绘（替代 QMenu 的系统方角菜单）。"""

    PANEL_W = 240
    PAD = 12          # 四周留白给投影，否则投影会被窗口边缘裁掉

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.PAD, self.PAD, self.PAD, self.PAD)
        outer.setSpacing(0)
        self.panel = QFrame(self)
        self.panel.setObjectName("ctxpanel")
        self.panel.setFixedWidth(self.PANEL_W - self.PAD * 2)
        self.panel.setStyleSheet(
            "QFrame#ctxpanel { background:#FFFFFF; border:1px solid #E3E9F2;"
            " border-radius:13px; }")
        sh = QGraphicsDropShadowEffect(self.panel)
        sh.setBlurRadius(22)
        sh.setOffset(0, 7)
        sh.setColor(QColor(15, 23, 42, 40))
        self.panel.setGraphicsEffect(sh)
        outer.addWidget(self.panel)
        self.v = QVBoxLayout(self.panel)
        self.v.setContentsMargins(7, 7, 7, 7)
        self.v.setSpacing(1)

    def set_header(self, name, sub=""):
        self.v.addWidget(_CtxHeader(name, sub))

    def add_item(self, kind, title, hint="", callback=None, danger=False):
        row = _CtxRow(kind, title, hint, danger, self.panel)
        row.clicked.connect(lambda: self._activate(callback))
        self.v.addWidget(row)

    def _activate(self, cb):
        self.close()
        if cb:
            QTimer.singleShot(0, cb)     # 先收起菜单，再弹保存框/确认框

    def popup_at(self, gpos):
        self.setFixedSize(self.PANEL_W, self.panel.sizeHint().height() + self.PAD * 2)
        scr = QApplication.screenAt(gpos) or QApplication.primaryScreen()
        av = scr.availableGeometry()
        x = gpos.x() - self.PAD + 4
        y = gpos.y() - self.PAD + 4
        x = max(min(x, av.right() - self.width() + 1), av.left())
        y = max(min(y, av.bottom() - self.height() + 1), av.top())
        self.move(QPoint(int(x), int(y)))
        self.show()
        self.setFocus()


class DeviceCard(QFrame):
    """一行设备卡片：左图标 / 中名称+状态徽章 / 右电量百分比+电量条。
    只构建一次，刷新时仅调用 apply() 更新内容，避免每次刷新重建导致闪烁。"""

    W_RIGHT = 136
    remove_requested = Signal(str)      # 右键「移除设备」→ 交给主窗口处理
    export_requested = Signal(str)      # 右键「导出协议」→ 交给主窗口处理

    def __init__(self, profile):
        super().__init__()
        self.setObjectName("card")
        self.setFixedHeight(96)
        self.profile = profile
        self._state_text = "未连接"
        self.icon, self.tname, self.bg, self.fg = TYPES.get(
            profile["type"], ("plug", "设备", "#F0F0F0", INK))

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(14)

        self.icon_box = QLabel()
        self.icon_box.setFixedSize(48, 48)
        self.icon_box.setAlignment(Qt.AlignCenter)
        self.icon_box.setStyleSheet(
            f"background:{self.bg};border-radius:24px;")
        self.icon_box.setPixmap(_glyph_pixmap(self.icon, self.fg, 26))
        root.addWidget(self.icon_box, 0)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        mid.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.name_lbl = QLabel(profile["name"])
        self.name_lbl.setObjectName("name")
        self.badge = Badge()
        self.badge.setObjectName("badge")
        top.addWidget(self.name_lbl)
        top.addWidget(self.badge)
        top.addStretch(1)
        self.meta = QLabel("")
        self.meta.setObjectName("meta")
        # 副标题可能很长：不让它把卡片撑出边界，被截断时靠 tooltip 看全文
        self.meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.meta.setMinimumWidth(0)
        mid.addLayout(top)
        mid.addWidget(self.meta)
        root.addLayout(mid, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.setContentsMargins(0, 0, 0, 0)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pct_lbl = QLabel("—")
        self.pct_lbl.setObjectName("pct")
        self.pct_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pct_lbl.setFixedWidth(self.W_RIGHT)
        self.pct_lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.bar = Bar()
        right.addWidget(self.pct_lbl, 0, Qt.AlignRight)
        right.addWidget(self.bar, 0, Qt.AlignRight)
        root.addLayout(right, 0)

        self.apply({"state": "disconnected"})

    def apply(self, res):
        res = res or {"state": "disconnected"}
        state = res.get("state", "disconnected")
        battery = res.get("battery")
        charging = bool(res.get("charging", False))
        note = res.get("note")
        detail = res.get("detail")          # 读取器可以附一句"怎么读到的"
        approx = bool(res.get("approx"))    # 只报档位、只能给估算值的设备
        stale = bool(res.get("stale"))      # 这轮没回话，显示的是上次读数

        if state == "connected" and battery is not None and not stale:
            badge = ("已连接", "#F2F2F2", "#1A1A1A", "#0E700E")
            if detail:
                sub = "已连接 · " + detail + (" · 充电中" if charging else "")
            else:
                sub = "已连接" + (" · 充电中" if charging else "")
            pct_text = (f"≈{battery}%" if approx else f"{battery}%")
            pct = battery
        elif state == "connected" and battery is not None:
            # 上一次读到的值：设备这轮没回话（多半休眠 / 关机了），不能写成「已连接」
            badge = ("未响应", "#F2F2F2", "#1A1A1A", "#C77A00")
            sub = f"设备这轮没回话（可能休眠或关机）· 显示上次读数 {battery}%"
            pct_text, pct = f"{battery}%", battery
        elif state == "connected" and note == "implnoread":
            badge = ("未响应", "#F2F2F2", "#1A1A1A", "#8A8A8A")
            sub = "接收器在，但设备没回话（可能已关机或休眠，开机后点刷新）"
            pct_text, pct = "—", None
        elif state == "connected" and note == "specnoread":
            badge = ("未响应", "#F2F2F2", "#1A1A1A", "#8A8A8A")
            sub = "接收器在，但协议没读回电量（帧格式可能不匹配这个固件）"
            pct_text, pct = "—", None
        elif state == "connected" and note == "noread":
            badge = ("已接入 · 待配置", "#F2F2F2", "#1A1A1A", "#C77A00")
            sub = "接收器在 · 这台设备还没配读取方式（点「捕获报文」看协议）"
            pct_text, pct = "—", None
        elif state == "connected":
            # 兜底：接收器在、但这轮没有任何电量信息（含刚启动读缓存那一瞬）。
            # 一律按「未响应」显示，绝不写成「已连接」，也不该说「接收器未插入」。
            badge = ("未响应", "#F2F2F2", "#1A1A1A", "#8A8A8A")
            sub = "接收器在，但设备没回话（可能已关机或休眠，开机后点刷新）"
            pct_text, pct = "—", None
        elif state == "capture":
            badge = ("已接入 · 待解析", "#F2F2F2", "#1A1A1A", "#C77A00")
            sub = "接收器在 · 待捕获协议（点顶部「捕获报文」）"
            pct_text, pct = "待解析", None
        elif state == "capture_unknown":
            badge = ("未识别", "#F2F2F2", "#1A1A1A", "#C42B1C")
            sub = "未识别接收器（点顶部「捕获报文」）"
            pct_text, pct = "待解析", None
        elif state == "nohid":
            badge = ("不可用", "#F2F2F2", "#1A1A1A", "#8A8A8A")
            sub = "仅 Windows 支持"
            pct_text, pct = "—", None
        else:
            badge = ("未连接", "#F2F2F2", "#1A1A1A", "#8A8A8A")
            sub = "接收器未插入"
            pct_text, pct = "—", None

        # 没回话时数字用灰色，一眼能看出这不是「当前电量」
        col = self._color(pct)
        if stale or pct is None:
            col = INK_FAINT
        self.badge.set_badge(badge[0], badge[1], badge[2], badge[3])
        self._state_text = badge[0]        # 右键菜单抬头用
        meta_text = f"{self.tname} · {sub}"
        self.meta.setText(meta_text)
        self.meta.setToolTip(meta_text)   # 文案过长被截断时，悬停可看全文
        self.pct_lbl.setText(pct_text)
        if pct is None:
            self.pct_lbl.setStyleSheet(
                f"color:{col};font-size:15px;font-weight:600;background:transparent;")
        else:
            self.pct_lbl.setStyleSheet(
                f"color:{col};font-size:26px;font-weight:700;background:transparent;")
        self.bar.set_value(None if pct is None else pct,
                           "#CBD5E1" if pct is None else col)

    @staticmethod
    def _color(pct):
        # 统一走全局配色：设置里改阈值后，卡片颜色立刻跟着变
        return pct_color(pct)

    def contextMenuEvent(self, event):
        """右键任意一台设备：导出这台的协议 / 移除这台设备。"""
        key = self.profile["key"]
        menu = CardMenu(self.window())
        menu.set_header(self.profile["name"], f"{self.tname} · {self._state_text}")
        menu.add_item("export", "导出协议…", "单独存成 .json 协议文件",
                      lambda: self.export_requested.emit(key))
        menu.add_item("trash", "移除设备", "从列表去掉，可再导入恢复",
                      lambda: self.remove_requested.emit(key), danger=True)
        menu.popup_at(event.globalPos())


class Switch(QWidget):
    """开关：自绘圆角轨道 + 白圆点，跟界面其他部分同一套视觉。"""

    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def isChecked(self):
        return self._checked

    def setChecked(self, v, emit=False):
        v = bool(v)
        if v == self._checked:
            return
        self._checked = v
        self.update()
        if emit:
            self.toggled.emit(v)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.setChecked(not self._checked, emit=True)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.setChecked(not self._checked, emit=True)
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        track = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1A1A1A" if self._checked else "#D0D0D0"))
        p.drawRoundedRect(track, h / 2, h / 2)
        d = h - 6
        x = (w - d - 3) if self._checked else 3
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 3, d, d))
        if self.hasFocus():
            pen = QPen(QColor("#1A1A1A"))
            pen.setWidthF(1.2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track, h / 2, h / 2)
        p.end()


class TrayController(QObject):
    """系统托盘：全程只有一个图标（不是一台设备一个）。

    同心圆最多三层，取电量最低的三台 —— 最外圈最低、最内圈是这三台里最高的；
    不足三台就有几台画几层。颜色直接跟随设置里的红/绿阈值。
    """

    # ---- 固定三层同心圆 ----
    RING_LAYERS = 3

    # ---- 光学尺寸（optical sizing）----
    # Windows 托盘只按 SM_CXSMICON 给物理尺寸（100% 缩放下就是 16×16），程序改不了。
    # 能改的是「在 16px 里怎么画」：以前把 64 设计稿线性缩到 16，线宽只剩 1.2~1.4px，
    # 抗锯齿一糊就成了发丝，看着又小又虚。现在按尺寸重算几何：
    #   · 16px：线宽系数拉到 0.231（≈1.85px 实线），三环几乎填满 16×16，和 32px 一样醒目；
    #   · ≥32px：回落到 0.181，保持规范图里那种清爽通透的比例，不显笨重。
    # 外缘固定落在半画布的 0.975 处，任何尺寸都不会被裁切。
    _RING_TAPER = (1.0, 0.95, 0.85)   # 外 → 内 线宽递减系数
    _RING_A_BOLD = 0.250              # size = 16 时的线宽系数（相对半画布）≈2.0px 实线
    _RING_A_AIRY = 0.181              # size ≥ 32 时的线宽系数
    _RING_IN = 0.11                   # 最内环起点（相对半画布）
    _RING_OUT = 0.975                 # 最外环外缘（相对半画布）

    TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64, 80, 96, 128)

    @classmethod
    def _ring_geometry(cls, size, count=3):
        """按目标尺寸算同心圆的 (半径, 线宽)，由外到内。

        count = 实际要画的层数（有几台设备画几层，最多三层）：
          · 3 台：沿用调好的三环几何，16px 下也是实打实的粗环；
          · 1~2 台：把可用半径平均分带，环比三环时更粗、居中更醒目，
            不会出现「只画内圈、外圈空着」的怪异观感。
        """
        h = size / 2.0
        t = max(0.0, min(1.0, (32.0 - size) / 16.0))   # 16→1，32及以上→0
        a = cls._RING_A_AIRY + (cls._RING_A_BOLD - cls._RING_A_AIRY) * t
        n = max(1, min(3, int(count or 3)))
        if n == 3:
            ws = [f * a * h for f in cls._RING_TAPER]
            span = cls._RING_OUT - cls._RING_IN
            gap = max(0.35, (span - 2.8 * a) / 2.0 * h)   # 环间距，留个下限别糊在一起
            r3 = cls._RING_IN * h + ws[2] / 2.0
            r2 = r3 + ws[2] / 2.0 + gap + ws[1] / 2.0
            r1 = r2 + ws[1] / 2.0 + gap + ws[0] / 2.0
            return [(r1, ws[0]), (r2, ws[1]), (r3, ws[2])]
        band = (cls._RING_OUT - cls._RING_IN) * h / n
        fatten = 1.35 if n == 1 else 1.15
        geo = []
        for i in range(n):
            w = min(a * h * cls._RING_TAPER[i] * fatten, band * 0.62)
            geo.append((cls._RING_IN * h + band * (n - i - 0.5), w))
        return geo

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.icon = QSystemTrayIcon(win)
        self.icon.setToolTip("设备电量监控")
        # 初始用矢量「空表盘」（三层浅灰底环），不依赖栅格 ico，彻底无锯齿
        self.icon.setIcon(self._render([], []))

        menu = QMenu()
        act_open = menu.addAction("打开主界面")
        act_open.triggered.connect(win.show_from_tray)
        self.dev_menu = menu.addMenu("设备电量")
        menu.addSeparator()
        act_quit = menu.addAction("退出软件")
        act_quit.triggered.connect(win.quit_app)
        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activated)
        self._menu_sig = None

    def _on_activated(self, reason):
        # 左键单击（Trigger）/ 双击：唤起主界面
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.win.show_from_tray()

    def is_visible(self):
        return self.icon.isVisible()

    def show(self):
        self.icon.show()

    def hide(self):
        self.icon.hide()

    def message(self, title, text):
        try:
            self.icon.showMessage(title, text, QSystemTrayIcon.Information, 4000)
        except Exception as e:
            log_err("托盘气泡失败: " + repr(e))

    def _draw_rings(self, size, levels, colors):
        """按目标尺寸画「透明底 + 中灰底环 + 电量进度弧」。

        每个尺寸用 **原生 size×size、DPR=1** 直接矢量光栅化：
        · QIcon 登记的尺寸就是真实尺寸 → 系统托盘要 16 就正好给 16，不缩放、不错配；
        · 正方形 QRectF（宽高都 = 2r）+ 抗锯齿 → 每层都是正圆，不椭圆、不变形、无锯齿；
        · 几何按尺寸现算（光学尺寸）：16px 下环线宽 ≈2.0px 实线且几乎填满画布，
          不再是从大稿缩出来的 1.2px 发丝；≥32px 自动收窄，保持清爽比例。
        · 三层底环用不同灰度，全部离线时也能一眼分出三环；完全透明底，不加任何底板。
        绝不给 QIcon 塞高 DPR 位图：那会让 Qt 把「设备像素」当「逻辑尺寸」登记，
        availableSizes 变成 64/80/96…，托盘只能从错档缩放，显示成残缺碎片。
        """
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx = cy = size / 2.0
        # 有几台设备画几层；0 台时画三层浅灰空表盘（否则托盘图标会完全透明看不见）
        layers = min(self.RING_LAYERS, len(levels)) if levels else self.RING_LAYERS
        for i, (radius, width) in enumerate(self._ring_geometry(size, layers)):
            r = radius
            # 宽高都等于 2r 的正方形 → drawEllipse / drawArc 必为正圆，不会变椭圆
            box = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            # ① 底环：三层不同中灰，浅色/深色任务栏上都看得见，离线态也能分层
            pen = QPen(QColor(TRAY_TRACKS[i % len(TRAY_TRACKS)]))
            pen.setWidthF(width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(box)
            # ② 进度环：弧长 = 电量%，从 12 点方向顺时针增长
            pct = levels[i] if i < len(levels) else 0
            if not pct or pct <= 0:
                continue
            col = colors[i] if i < len(colors) and colors[i] else COLOR_NONE
            pen = QPen(QColor(col))
            pen.setWidthF(width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            span = -int(360 * 16 * min(100, max(0, int(pct))) / 100.0)
            p.drawArc(box, 90 * 16, span)
        p.end()
        return pm

    def _render(self, levels, colors):
        """把一组电量渲染成多尺寸矢量图标（覆盖托盘常用尺寸，避免系统拉伸出锯齿）。"""
        icon = QIcon()
        for size in self.TRAY_SIZES:
            icon.addPixmap(self._draw_rings(size, levels, colors))
        return icon

    def _set_rings(self, levels, colors):
        """levels: 电量百分比（外圈最低 → 内圈最高）；无设备时只画浅灰空表盘。"""
        self.icon.setIcon(self._render(
            (levels or [])[:self.RING_LAYERS],
            (colors or [])[:self.RING_LAYERS]))

    def update(self, rows, colors, idle=0):
        """rows: [(名称, 电量)]，按电量升序（低 → 高）；idle: 接收器在但没回话的台数。"""
        levels = [p for _, p in rows][:self.RING_LAYERS]
        self._set_rings(levels, colors)
        # 悬停提示：全部读到电量的设备，电量高的在上面、最低的在最底下
        if not rows:
            tip = ("设备电量监控\n暂无已连接设备" if idle == 0
                   else f"设备电量监控\n暂无已连接设备（{idle} 台未响应）")
        else:
            lines = ["设备电量监控", "────────────"]
            for name, pct in reversed(rows):
                lines.append(f"{name}  {pct}%")
            tip = "\n".join(lines)
            if idle > 0:
                tip += f"\n另有 {idle} 台未响应（可能已关机）"
        self.icon.setToolTip(tip)
        # 右键菜单里再放一份完整列表：Tooltip 被系统截断时还能在这里看全。
        # 内容没变就不重建 —— 否则用户正开着菜单时，一次自动刷新会把菜单清空。
        sig = (tuple(f"{n} {p}" for n, p in rows), idle)
        if sig == self._menu_sig:
            return
        self._menu_sig = sig
        self.dev_menu.clear()
        if not rows:
            a = self.dev_menu.addAction("暂无已连接设备")
            a.setEnabled(False)
        else:
            for name, pct in reversed(rows):
                a = self.dev_menu.addAction(f"{name}   {pct}%")
                a.setEnabled(False)
        if idle > 0:
            a = self.dev_menu.addAction(f"另有 {idle} 台未响应（可能已关机）")
            a.setEnabled(False)


class SettingsDialog(QDialog):
    """右上角「设置」：开机自启动 / 启动托盘 / 低电量提醒 / 电量颜色。
    每一项改动都即时生效，并立刻写进 user_settings.json。"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setObjectName("settingsdlg")
        self.setWindowTitle("设置")
        self.setWindowIcon(QIcon(ICON_PATH))
        self.setModal(True)
        self.setFixedWidth(548)
        try:
            self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, True)
        except Exception:
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 16)
        root.setSpacing(10)

        title = QLabel("设置")
        title.setObjectName("title")
        root.addWidget(title)

        # ---------- 1. 系统与托盘 ----------
        root.addWidget(self._section("系统与托盘"))
        self.sw_auto = Switch(USER["autostart"])
        self.sw_auto.toggled.connect(self._on_autostart)
        root.addWidget(self._row("开机自启动", self.sw_auto))
        self.sw_tray = Switch(USER["tray"])
        self.sw_tray.toggled.connect(self._on_tray)
        root.addWidget(self._row("启动托盘", self.sw_tray))
        # 串口接收器：这里手动选端口，配置里不写死任何 COM 号
        self.cb_serial = Combo()
        self.cb_serial.setMinimumWidth(200)
        self.cb_serial.setToolTip("串口接收器用哪个端口；选「自动」会逐个尝试本机串口。"
                                  "协议里自己写了 port 的，以协议为准。")
        self._fill_serial_ports()
        self.cb_serial.currentIndexChanged.connect(self._on_serial_port)
        root.addWidget(self._row("串口接收器", self.cb_serial))
        root.addWidget(self._sep())

        # ---------- 2. 低电量提醒 ----------
        root.addWidget(self._section("低电量提醒"))
        self.sw_alert = Switch(USER["low_alert"])
        self.sw_alert.toggled.connect(self._on_alert_switch)
        root.addWidget(self._row("低电量提醒", self.sw_alert))

        self.ed_low = self._num_edit(USER["low_alert_pct"], 58)
        self.ed_low.editingFinished.connect(self._on_low_pct)
        self.ed_low.setEnabled(USER["low_alert"])
        low_row = QWidget()
        low_row.setStyleSheet("background:transparent;")
        lr = QHBoxLayout(low_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(6)
        lr.addWidget(self.ed_low)
        lr.addWidget(self._pct())
        lr.addSpacing(8)
        b_prev = QPushButton("模拟弹窗")
        b_prev.setObjectName("mini")
        b_prev.setCursor(Qt.PointingHandCursor)
        b_prev.setToolTip("马上在右下角弹一条低电量提示，看看效果（不影响真实设备，不用关这个窗口）")
        b_prev.clicked.connect(self._preview)
        lr.addWidget(b_prev)
        root.addWidget(self._row("提醒阈值", low_row))
        root.addWidget(self._sep())

        # ---------- 3. 电量颜色 ----------
        root.addWidget(self._section("电量颜色"))
        self.ed_green = self._num_edit(USER["green_pct"], 58)
        self.ed_red = self._num_edit(USER["red_pct"], 58)
        root.addWidget(self._row("大于多少显示绿色", self._num_row(self.ed_green)))
        root.addWidget(self._row("小于多少显示红色", self._num_row(self.ed_red)))
        bar = QHBoxLayout()
        bar.setSpacing(10)
        b_apply = QPushButton("保存并应用")
        b_apply.setObjectName("primary")
        b_apply.setCursor(Qt.PointingHandCursor)
        b_apply.clicked.connect(self._apply_colors)
        bar.addStretch(1)
        bar.addWidget(b_apply)
        root.addLayout(bar)

    # ---------- 小工具 ----------
    @staticmethod
    def _section(text):
        lbl = QLabel(text)
        lbl.setObjectName("secthead")
        return lbl

    @staticmethod
    def _sep():
        f = QFrame()
        f.setObjectName("setsep")
        f.setFixedHeight(1)
        return f

    @staticmethod
    def _row(title, control):
        """一行 = 一个标题 + 一个控件；不铺小字说明，保持极简。"""
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        t = QLabel(title)
        t.setObjectName("settitle")
        lay.addWidget(t, 1)
        lay.addWidget(control, 0, Qt.AlignVCenter)
        return w

    @staticmethod
    def _num_edit(value, width=58):
        e = QLineEdit(str(value))
        e.setObjectName("numin")
        e.setFixedWidth(width)
        e.setAlignment(Qt.AlignCenter)
        e.setMaxLength(3)
        return e

    @staticmethod
    def _pct():
        lbl = QLabel("%")
        lbl.setObjectName("pctsuffix")
        return lbl

    def _num_row(self, edit):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(edit)
        h.addWidget(self._pct())
        return w

    # ---------- 交互 ----------
    def _on_autostart(self, on):
        self.win.apply_autostart(on)
        # 打开自启动会连带打开托盘；关闭自启动不动托盘 —— 把真实状态写回开关
        self.sw_auto.setChecked(bool(USER["autostart"]))
        self.sw_tray.setChecked(bool(USER["tray"]))

    def _on_tray(self, on):
        self.win.apply_tray(on)
        self.sw_tray.setChecked(bool(USER["tray"]))

    def _fill_serial_ports(self):
        """填串口下拉框：第一项是「自动」，后面是本机当前枚举到的串口。"""
        self.cb_serial.blockSignals(True)
        self.cb_serial.clear()
        self.cb_serial.addItem("自动选择（逐个尝试）", "")
        for port in list_serial_ports():
            self.cb_serial.addItem(port, port)
        cur = str(USER.get("serial_port") or "").strip().upper()
        idx = self.cb_serial.findData(cur) if cur else 0
        self.cb_serial.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_serial.blockSignals(False)

    def _on_serial_port(self, _idx):
        port = self.cb_serial.currentData() or ""
        if port == (USER.get("serial_port") or ""):
            return
        USER["serial_port"] = port
        save_user_settings()
        # 端口换了立刻重读一轮，用户不用自己点刷新来验证
        self.win.refresh()

    def _on_alert_switch(self, on):
        USER["low_alert"] = bool(on)
        save_user_settings()
        self.ed_low.setEnabled(bool(on))
        self.win.reset_alerts()

    def _on_low_pct(self):
        txt = self.ed_low.text().strip()
        n = _as_int(txt, -1)
        if n < 1 or n > 99:
            AppDialog.tell(
                self, "阈值不对",
                f"低电量阈值要填 1~99 之间的整数（现在填的是「{txt}」）。\n\n"
                "设置没有改动，请重新输入。", width=380)
            self.ed_low.setText(str(USER["low_alert_pct"]))
            return
        if n == USER["low_alert_pct"]:
            return
        USER["low_alert_pct"] = n
        save_user_settings()
        self.win.reset_alerts()

    def _preview(self):
        self.win.preview_low_alert()

    def _apply_colors(self):
        gt = self.ed_green.text().strip()
        rt = self.ed_red.text().strip()
        g, r = _as_int(gt, -999), _as_int(rt, -999)
        msg = ""
        if g == -999 or r == -999:
            msg = ("两个阈值都要填整数，例如绿色 70、红色 30。\n\n"
                   f"现在填的是：绿色「{gt}」，红色「{rt}」。")
        elif not (0 < r < 100):
            msg = f"红色阈值必须大于 0 且小于 100，现在填的是 {r}。"
        elif not (0 < g < 100):
            msg = f"绿色阈值必须大于 0 且小于 100，现在填的是 {g}。"
        elif g <= r:
            msg = (f"绿色阈值必须大于红色阈值。\n\n"
                   f"现在填的是：绿色 {g}、红色 {r}。\n"
                   f"要么把绿色调大到 {r} 以上，要么把红色调小到 {g} 以下。")
        if msg:
            AppDialog.tell(self, "数值不符合要求",
                           msg + "\n\n设置没有改动，请重新输入。", width=420)
            return
        USER["green_pct"], USER["red_pct"] = g, r
        save_user_settings()
        self.win.apply_colors()


class MainWindow(QMainWindow):
    STALE_KEEP = 1800         # 上次读数最多保留多久（秒）：够覆盖设备打盹，又不会挂一整天

    def __init__(self):
        super().__init__()
        self.setWindowTitle("设备电量监控")
        self.setWindowIcon(QIcon(ICON_PATH))
        # 高度跟着设备台数走：多一台就多一行卡片（+1 台 = +108px：卡片 96 + 间距 12）
        # 末尾那行「＋ 导入设备」固定占 48 + 12。
        self.resize(760, 158 + 108 * N_DEVICES + 60)
        self.setMinimumSize(680, 400)
        self.cards = {}
        self.results = {p["key"]: {"state": "disconnected"} for p in PROFILES}
        self._last_ok = {}              # key -> 上次真读到电量的时间（stale 保鲜用）
        cached = load_cache()
        for k, v in cached.items():
            if k in self.results and isinstance(v, dict):
                v = dict(v)
                ok_at = v.pop("_ok_at", None)
                has_b = v.get("battery") is not None
                fresh = bool(has_b and ok_at
                             and time.time() - float(ok_at) < self.STALE_KEEP)
                if has_b and ok_at and not fresh:
                    # 明确超过保鲜期：这个数字太旧了，别挂着冒充当前电量
                    v.pop("battery", None)
                    v.pop("charging", None)
                elif has_b:
                    # 还能用（或老版本缓存没记时间）：一律先按「上次读数」灰色显示，
                    # 不写成绿色「已连接」—— 设备可能已经关机了，等这一轮刷新确认。
                    # 老缓存没有时间戳时，保鲜期从本次启动重新起算，避免数字被抹成「—」。
                    v["stale"] = True
                    self._last_ok[k] = float(ok_at) if ok_at else time.time()
                self.results[k] = v
        # 沿用上次选的刷新间隔（不在档位表里就回默认值）
        try:
            iv = int(cached.get("_interval", REFRESH_DEFAULT_S))
        except Exception:
            iv = REFRESH_DEFAULT_S
        if iv not in [s for s, _ in REFRESH_CHOICES]:
            iv = REFRESH_DEFAULT_S
        SETTINGS["interval"] = iv
        self._from_cache = bool(cached)
        self.auto_refresh = iv > 0
        self._busy = False
        self._busy_since = 0.0
        # 退出 / 托盘 / 排序 / 低电量提醒 的运行时状态
        self._quitting = False          # 只有置 True 时关窗口才真退出
        self._tray = None               # TrayController，全程只有一个托盘图标
        self._order = [p["key"] for p in PROFILES]   # 当前卡片排列，变了才重排
        self._alerted = {}              # key -> 上次提醒时的电量
        self._alert_dlg = None
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog)
        self._build_ui()
        self._render()  # 先立刻画出卡片（有上次读数就直接显示），避免空白
        if USER.get("tray"):
            self._setup_tray()          # 托盘：读数和圆环都靠它常驻
        # 先让窗口画出来再说，别在启动瞬间就跟 HID 抢时间（否则会有"点了没反应"的错觉）
        QTimer.singleShot(120, self.refresh)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        if iv > 0:
            self.timer.start(iv * 1000)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(8)
        # 顶部不再放软件图标与「设备电量监控」标题，只保留一行设备状态说明
        # （软件图标仍用于窗口标题栏 / 任务栏 / EXE / 桌面快捷方式）
        self.subtitle = QLabel(f"你的 {N_DEVICES} 台 2.4G 设备 · 实时电量")
        self.subtitle.setObjectName("subtitle")
        header.addWidget(self.subtitle)
        header.addStretch(1)

        # ---- 第一行右侧：全局动作（矢量线性图标 + 文本，去掉彩色 emoji）----
        self.btn_capture = QPushButton("捕获报文")
        self.btn_capture.setObjectName("capturetop")
        self.btn_capture.setIcon(_glyph("antenna", INK, 16))
        self.btn_capture.setIconSize(QSize(16, 16))
        self.btn_capture.setToolTip("抓取本机全部 HID 设备与原始报文，用于逆向未知设备的电量字段")
        self.btn_capture.clicked.connect(self.capture)
        self.btn_proto = QPushButton("协议一览")
        self.btn_proto.setObjectName("capturetop")
        self.btn_proto.setIcon(_glyph("list", INK, 16))
        self.btn_proto.setIconSize(QSize(16, 16))
        self.btn_proto.setToolTip(f"查看这 {N_DEVICES} 台设备各自的识别方式、接口、帧格式与电量字段（可一键复制）")
        self.btn_proto.clicked.connect(self.show_protocols)
        self.btn_about = QPushButton("说明")
        self.btn_about.setObjectName("ghost")
        self.btn_about.setIcon(_glyph("info", INK, 16))
        self.btn_about.setIconSize(QSize(16, 16))
        self.btn_about.clicked.connect(self.show_about)
        self.btn_settings = QPushButton("设置")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setIcon(_glyph("sliders", INK, 16))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setToolTip("开机自启动 / 托盘 / 低电量提醒 / 电量颜色")
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_quit = QPushButton("退出")
        self.btn_quit.setObjectName("ghost")
        self.btn_quit.setIcon(_glyph("power", INK, 16))
        self.btn_quit.setIconSize(QSize(16, 16))
        self.btn_quit.setToolTip("彻底退出程序（点窗口的 × 只会缩到托盘）")
        self.btn_quit.clicked.connect(self.confirm_quit)

        header.addWidget(self.btn_capture)
        header.addWidget(self.btn_proto)
        header.addWidget(self.btn_about)
        header.addWidget(self.btn_settings)
        header.addWidget(self.btn_quit)
        root.addLayout(header)

        # ---- 第二行：刷新 + 刷新间隔下拉 + 状态 ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.btn_refresh = QPushButton("立即刷新")
        self.btn_refresh.setObjectName("primary")
        self.btn_refresh.setIcon(_glyph("refresh", "#FFFFFF", 16))
        self.btn_refresh.setIconSize(QSize(16, 16))
        self.btn_refresh.clicked.connect(self.refresh)

        interval_lbl = QLabel("自动刷新")
        interval_lbl.setObjectName("hint")

        self.cbo_interval = Combo()
        self.cbo_interval.setMinimumWidth(124)
        for secs, text in REFRESH_CHOICES:
            self.cbo_interval.addItem(text, secs)
        idx = self.cbo_interval.findData(SETTINGS["interval"])
        self.cbo_interval.setCurrentIndex(idx if idx >= 0 else 0)
        self.cbo_interval.setToolTip("每隔多久自动读一次电量（选择「不自动刷新」可关掉定时器）")
        self.cbo_interval.currentIndexChanged.connect(self._on_interval_changed)

        toolbar.addWidget(self.btn_refresh)
        toolbar.addWidget(interval_lbl)
        toolbar.addWidget(self.cbo_interval)
        self.status = QLabel("就绪")
        self.status.setObjectName("hint")
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        toolbar.addWidget(self.status, 1)   # 占满剩余宽度，避免右侧文字被左截断
        root.addLayout(toolbar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(12)
        self.scroll.setWidget(self.list_widget)
        root.addWidget(self.scroll, 1)

        self._build_cards()

    def _build_cards(self):
        """按 PROFILES 重建全部卡片（导入 / 移除设备后调用）。"""
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.cards = {}
        for p in PROFILES:
            card = DeviceCard(p)
            card.remove_requested.connect(self.remove_device)
            card.export_requested.connect(self.export_device)
            self.cards[p["key"]] = card
            self.list_layout.addWidget(card)
        self.add_row = AddDeviceRow()
        self.add_row.clicked.connect(self.open_import)
        self.list_layout.addWidget(self.add_row)
        self.list_layout.addStretch(1)
        self._order = [p["key"] for p in PROFILES]
        self.resize(760, 158 + 108 * sync_device_count() + 60)

    def _refresh_count_text(self):
        n = sync_device_count()
        self.subtitle.setText(f"你的 {n} 台 2.4G 设备 · 实时电量")
        self.btn_proto.setToolTip(f"查看这 {n} 台设备各自的识别方式、接口、帧格式与电量字段（可一键复制）")

    def open_import(self):
        """卡片最下方那行「＋ 导入设备」。"""
        dlg = ImportDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        for p in PROFILES:
            self.results.setdefault(p["key"], {"state": "disconnected"})
        self._build_cards()
        self._refresh_count_text()
        self._render()
        save_cache(self.results, self._last_ok)      # 立刻记住「谁被移除 / 谁被恢复」
        self.status.setText(f"已导入设备 · 当前共 {N_DEVICES} 台")
        QTimer.singleShot(120, self.refresh)

    def export_device(self, key):
        """右键「导出协议」：把这台设备的协议单独存成 .json（默认写进协议库）。"""
        hit = next(((f, payload) for k, f, payload in exportable_packs() if k == key), None)
        if not hit:
            self.status.setText("这台设备的协议暂时导不出来")
            return
        fname, payload = hit
        path, _ = QFileDialog.getSaveFileName(
            self, "导出这台设备的协议", os.path.join(ensure_pack_dir(), fname),
            "协议文件 (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self.status.setText(f"✅ 协议已导出：{os.path.basename(path)}")
        except Exception as e:
            log_err("导出单台协议失败: " + repr(e))
            AppDialog.tell(self, "导出失败", f"写文件失败：{e}")

    def remove_device(self, key):
        spec = SPEC_DEVICES.get(key)
        prof = next((p for p in PROFILES if p["key"] == key), None)
        if not spec and not prof:
            return
        name = spec["name"] if spec else prof["name"]
        is_builtin = spec is None
        if is_builtin:
            text = (f"要从监控列表里移除「{name}」吗？\n\n"
                    f"它的协议文件已经存在「协议库\\{name}.json」。"
                    "以后想加回来：点列表最下面「＋ 导入设备」，选这份文件即可。")
        else:
            text = (f"要从监控列表里移除「{name}」吗？\n\n"
                    "设备本身不受影响，协议文件仍留在「协议库」里，"
                    "只是不再显示、不再读取（devices.json 里这条会删掉）。")
        if not AppDialog.ask(self, "移除设备", text, ok_text="移除",
                             cancel_text="取消", danger=True, width=398):
            return
        if is_builtin:
            # 内置设备只做「隐藏」，协议先存一份到协议库，方便随时导回来
            save_builtin_to_library(key)
            hide_builtin(key)
        else:
            remove_user_spec(key)
            save_user_specs()
        self.results.pop(key, None)
        self._build_cards()
        self._refresh_count_text()
        self._render()
        save_cache(self.results, self._last_ok)      # 立刻把「移除了哪台」记下来，别等这轮刷新
        self.refresh()

    def _online_count(self):
        """当前有几台真的读到了电量，用于状态栏的 在线 X/N。

        接收器插着但设备关了机，不算在线 —— 否则「在线 3/3」会误导人。
        """
        n = 0
        for p in PROFILES:
            r = self.results.get(p["key"]) or {}
            if (r.get("state") == "connected" and r.get("battery") is not None
                    and not r.get("stale")):
                n += 1
        return n

    def _render(self):
        # 卡片只建一次，这里仅更新内容 —— 不会闪烁
        for key, card in self.cards.items():
            card.apply(self.results.get(key, {"state": "disconnected"}))
        self._resort_cards()      # 按电量升序重排（低的在上面）
        self._update_tray()       # 托盘图标 / Tooltip / 圆环颜色同步

    # --------------------- 列表排序 ---------------------
    def _sort_key(self, prof):
        """电量低的排前面；读不到电量的（休眠 / 关机 / 未连接）统一沉到最后。"""
        r = self.results.get(prof["key"]) or {}
        b = r.get("battery")
        if r.get("state") == "connected" and b is not None and not r.get("stale"):
            return (0, int(b), prof["name"])
        return (1, 0, prof["name"])

    def _resort_cards(self):
        """只动布局顺序，不重建控件 —— 排完不会闪、也不会丢焦点。"""
        order = [p["key"] for p in sorted(PROFILES, key=self._sort_key)]
        if order == self._order:
            return
        self._order = order
        for i, key in enumerate(order):
            card = self.cards.get(key)
            if card is not None:
                self.list_layout.insertWidget(i, card)

    # --------------------- 托盘 ---------------------
    def _battery_rows(self):
        """真正读到电量的设备，按电量升序（低 → 高）。

        只算「这一轮确实回了话」的：接收器插着但设备关了机 / 睡着了，
        上一次的读数（stale）不算 —— 否则托盘会替一台关着的设备报电量。
        """
        rows = []
        for p in PROFILES:
            r = self.results.get(p["key"]) or {}
            if r.get("state") != "connected" or r.get("stale"):
                continue
            b = r.get("battery")
            if b is None:
                continue
            rows.append((p["name"], int(b)))
        rows.sort(key=lambda x: (x[1], x[0]))
        return rows

    def _idle_count(self):
        """接收器插着、但这一轮没回话的台数（多半是关机了）。"""
        n = 0
        for p in PROFILES:
            r = self.results.get(p["key"]) or {}
            if r.get("state") == "connected" and (r.get("battery") is None
                                                  or r.get("stale")):
                n += 1
        return n

    def _setup_tray(self):
        if self._tray is not None:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log_err("系统托盘不可用，跳过托盘初始化")
            return
        self._tray = TrayController(self)
        self._tray.show()
        self._update_tray()

    def _update_tray(self):
        if self._tray is None or not self._tray.is_visible():
            return
        rows = self._battery_rows()
        # 取电量最低的前三台，外层→内层按「低→高」上色
        colors = [pct_color(b) for _, b in rows][:3]
        self._tray.update(rows, colors, self._idle_count())

    def apply_tray(self, on):
        """托盘开关：关掉只是不显示图标，进程照常在后台跑。"""
        on = bool(on)
        USER["tray"] = on
        save_user_settings()
        if on:
            self._setup_tray()
            if self._tray is not None:
                self._tray.show()
                self._update_tray()
        else:
            if self._tray is not None:
                self._tray.hide()
            # 图标没了就必须让窗口可见，否则用户找不到这个程序
            if not self.isVisible():
                self.show_from_tray()

    # --------------------- 开机自启动 ---------------------
    def apply_autostart(self, on):
        """开启：写 HKCU 启动项，并顺手打开托盘（开机后只靠托盘待命）。
        关闭：只删启动项，托盘开关保持原样不动。"""
        if on:
            ok, err = set_autostart(True)
            if not ok:
                AppDialog.tell(
                    self, "开机自启动没设置成功",
                    f"写系统启动项失败：{err}\n\n"
                    "如果软件放在 C:\\Program Files 这类受保护目录，"
                    "换到 D 盘或用户目录下再试。", width=440)
                USER["autostart"] = False
                save_user_settings()
                return False
            USER["autostart"] = True
            if not USER.get("tray"):
                self.apply_tray(True)     # 规则：开自启动 → 托盘一并打开
            save_user_settings()
            self.status.setText("开机自启动已开启 · 开机后只在托盘后台运行")
            return True
        set_autostart(False)
        USER["autostart"] = False
        save_user_settings()              # 注意：不碰 USER["tray"]
        self.status.setText("开机自启动已关闭 · 托盘设置保持原样")
        return False

    # --------------------- 颜色设置 ---------------------
    def apply_colors(self):
        """主界面卡片 + 托盘圆环立刻换成新配色。"""
        self._render()
        self.status.setText(
            f"颜色已更新：<{USER['red_pct']}% 红 · "
            f"{USER['red_pct']}~{USER['green_pct']}% 橙 · "
            f"≥{USER['green_pct']}% 绿")

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()
        self._render()      # 关掉设置后再对齐一次（开关可能改了托盘）

    # --------------------- 低电量提醒 ---------------------
    def reset_alerts(self):
        self._alerted.clear()

    def _check_low_battery(self):
        if not USER.get("low_alert"):
            self._alerted.clear()
            return
        th = int(USER.get("low_alert_pct", 20))
        hits = []
        for p in PROFILES:
            r = self.results.get(p["key"]) or {}
            if r.get("state") != "connected":
                continue
            b = r.get("battery")
            if b is None:
                continue
            b = int(b)
            if r.get("stale"):                  # 上次读数不代表现在，别拿它提醒
                self._alerted.pop(p["key"], None)
                continue
            if b >= th:
                self._alerted.pop(p["key"], None)   # 回到阈值以上，下次跌破再提醒
                continue
            old = self._alerted.get(p["key"])
            # 同一台只在「刚跌破」或「又掉了 5% 以上」时提醒，避免每 20 秒弹一次
            if old is None or b <= old - 5:
                self._alerted[p["key"]] = b
                hits.append((p["name"], b))
        for k in list(self._alerted):        # 设备被移除后清掉旧记录
            if not any(p["key"] == k for p in PROFILES):
                self._alerted.pop(k, None)
        if hits:
            self._show_low_alert(hits)

    def _show_low_alert(self, items, preview=False):
        """右下角系统通知样式的提示：带提示音、几秒后自己消失，不用点。"""
        th = int(USER.get("low_alert_pct", 20))
        if len(items) == 1:
            name, b = items[0]
            title = "低电量提醒 · 模拟" if preview else "低电量提醒"
            body = [f"「{name}」电量 {b}%，低于你设的 {th}%。",
                    "建议尽快充电，避免使用中断电。"]
            life = 6000
        else:
            title = f"低电量提醒 · {len(items)} 台设备" + ("（模拟）" if preview else "")
            body = [f"这几台电量都低于 {th}%："]
            body += [(n, f"{b}%") for n, b in items]
            body += ["建议尽快充电。"]
            life = 6500 + 900 * min(len(items), 4)
        Toast.show_toast(title, body, life=life)

    def preview_low_alert(self):
        """设置里「模拟弹窗」：拿当前最低的一台（没有就用示例）演一次。"""
        th = int(USER.get("low_alert_pct", 20))
        rows = self._battery_rows()
        if rows:
            name, b = min(rows, key=lambda x: x[1])
            demo = min(int(b), max(1, th - 4))
        else:
            name = PROFILES[0]["name"] if PROFILES else "示例设备"
            demo = max(1, th - 4)
        self._show_low_alert([(name, demo)], preview=True)

    # --------------------- 退出 / 托盘唤起 ---------------------
    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def confirm_quit(self):
        if AppDialog.ask(self, "退出软件",
                         "要彻底退出「设备电量监控」吗？\n\n"
                         "退出后托盘图标会消失，后台也不再读取电量。",
                         ok_text="退出", cancel_text="取消", danger=True, width=380):
            self.quit_app()

    def quit_app(self):
        """唯一的真退出通道：软件上的「退出」或托盘右键「退出软件」。"""
        self._quitting = True
        try:
            self.timer.stop()
        except Exception:
            pass
        if self._tray is not None:
            self._tray.hide()
        Toast.close_all()
        QApplication.quit()

    def refresh(self):
        if self._busy:
            # 上一轮还没回来：超过 18 秒就认为卡死，强制解锁，避免按钮永远失灵
            if time.time() - self._busy_since > 18:
                log_err("上一轮刷新超时，强制解锁")
                self._busy = False
            else:
                return
        self._busy = True
        self._busy_since = time.time()
        self.status.setText("正在读取设备电量…" + ("（已显示上次读数）" if self._from_cache else ""))
        self.worker = RefreshWorker()
        self.worker.one.connect(self._on_one)
        self.worker.finished.connect(self._on_refreshed)
        self.worker.start()
        self._watchdog.start(20000)   # 兜底：万一线程没回话，20 秒后解除卡死

    def _on_watchdog(self):
        if self._busy:
            log_err("刷新看门狗触发：20 秒无汇总，强制解锁")
            self._busy = False
            self.status.setText("读取超时 · 已保留上次读数，可再点「刷新」重试")

    def _on_one(self, key, res):
        """单台设备读完就立刻更新那一张卡（不等其它设备）。"""
        self.results[key] = self._merge_result(key, res)
        card = self.cards.get(key)
        if card is not None:
            card.apply(self.results[key])

    def _merge_result(self, key, res):
        """新的结果没带电量（还没读到 / 设备这轮没回话）时，保留上次的真实读数，
        标成 stale —— 但只保留 STALE_KEEP 秒：
          · 短时间掉一下（鼠标打盹）不会把数字抹成「—」再慢慢回来；
          · 关机半小时的耳机也不会一直挂着那个旧数字冒充「现在还有电」。
        超过时限就直接用新结果（卡片显示「未响应」+「—」）。
        """
        old = self.results.get(key) or {}
        present = ("connected", "capture", "capture_unknown")
        if res.get("battery") is not None:
            self._last_ok[key] = time.time()      # 记下「这次真读到了」的时间
        if (res.get("battery") is None and old.get("battery") is not None
                and res.get("state") in present and old.get("state") in present
                and time.time() - self._last_ok.get(key, 0.0) < self.STALE_KEEP):
            merged = dict(res)
            merged["battery"] = old["battery"]
            merged["charging"] = old.get("charging", False)
            merged["approx"] = old.get("approx", False)
            if old.get("level"):
                merged["level"] = old["level"]
            merged["stale"] = True
            return merged
        return res

    def _on_refreshed(self, out, err):
        self._watchdog.stop()
        for o in out:
            self.results[o["key"]] = self._merge_result(o["key"], o["result"])
        self._render()
        self._from_cache = False
        save_cache(self.results, self._last_ok)
        self._check_low_battery()
        ts = time.strftime("%H:%M:%S")
        iv = self._interval_seconds()
        auto = ("每 %d 秒" % iv) if 0 < iv < 60 else ("每 %d 分钟" % (iv // 60) if iv else "关")
        base = (f"最后更新 {ts} · 在线 {self._online_count()}/{N_DEVICES} 台"
                + f" · 自动刷新 {auto}")
        if err:
            base += f" · ⚠️ {err}"
        self.status.setText(base)
        self.status.setToolTip(base)
        self._busy = False

    def _on_tick(self):
        if self.auto_refresh:
            self.refresh()

    def _interval_seconds(self):
        """当前下拉框选中的间隔（秒）；0 表示不自动刷新。"""
        try:
            return int(SETTINGS.get("interval", REFRESH_DEFAULT_S))
        except Exception:
            return REFRESH_DEFAULT_S

    def _on_interval_changed(self, _index=None):
        """下拉框换了档位：立刻生效并写进 cache.json，下次启动沿用。"""
        secs = self.cbo_interval.currentData()
        try:
            secs = int(secs)
        except Exception:
            secs = REFRESH_DEFAULT_S
        SETTINGS["interval"] = secs
        self.auto_refresh = secs > 0
        timer = getattr(self, "timer", None)   # 启动早期可能还没建好定时器
        if timer is not None:
            if self.auto_refresh:
                timer.start(secs * 1000)
            else:
                timer.stop()
        save_cache(self.results, self._last_ok)
        text = (f"自动刷新已设为每 {secs} 秒" if 0 < secs < 60 else
                f"自动刷新已设为每 {secs // 60} 分钟" if secs >= 60 else
                "自动刷新已关闭（仍可点「立即刷新」）")
        self.status.setText(text)
        self.status.setToolTip(text)

    def capture(self):
        # 顶部「捕获报文」按钮：一次抓取全部设备（含本机完整 HID 列表），
        # 用来核对 VID/PID、看清收发帧，是给新设备写协议的第一步。
        dlg = CaptureDialog(self, ALL_VIDS, KEYWORDS)
        dlg.exec()

    def show_protocols(self):
        """顶部「协议一览」：把这 N 台设备各自的识别方式/接口/帧格式/电量字段列出来，
        并支持一键复制，方便贴到文档或发给别人。"""
        dlg = ProtocolDialog(self)
        dlg.exec()

    def show_about(self):
        # 设备清单和台数都从 PROFILES 现场生成，不再写死
        lines = [f"设备电量监控（当前 {N_DEVICES} 台 2.4G 设备）", "",
                 "每行：左图标 / 中名称 / 右电量百分比（带电量条）。", "",
                 "已接入设备与直读协议："]
        for p in PROFILES:
            info = PROTOCOL_INFO.get(p["key"], {})
            proto = (info.get("proto") or "").replace("\n", " ")
            field = (info.get("field") or "").split("\n")[0]
            lines.append(f"· {p['name']} —— {proto}；{field}")
        lines += [
            "  （键盘/鼠标/耳机休眠时这一轮读不到，会保留上次读数并标「正在更新」）", "",
            "连接判定：读到电量才算「已连接」；接收器插着但设备关了机，",
            "          会显示「未响应 · 可能已关机」，不会冒充已连接、也不会报假电量。",
            "电量一律按各家私有协议直读，不打开、也不依赖任何厂商软件（装没装驱动都不影响读数）。",
            f"读取方式：{N_DEVICES} 台并行、逐台出结果；启动先用上次读数秒显，随后 1~2 秒内更新。",
            "刷新间隔：顶部第二行下拉框可选 5 秒 ~ 10 分钟，或选「不自动刷新」；",
            "          选择会被记住，下次启动沿用。",
            "协议明细：点顶部「协议一览」，可看到每台设备的 VID/PID、接口、",
            "          查询帧与电量字段，支持一键复制。",
            "添加新设备：点列表最下面「＋ 导入设备」，选一个 .json 协议文件，",
            "          文件里写清 VID/PID、发什么帧、电量在第几个字节，程序照单读；",
            "          没有现成文件就在导入窗口点「导出协议文件」，照着改一台即可。",
            "          导入的协议都留在程序目录的「协议库」里，一台一个文件。",
            "右键设备：可以「导出协议」（单独存成 .json）或「移除设备」。",
            f"电量配色：≥{USER['green_pct']}% 绿，"
            f"{USER['red_pct']}~{USER['green_pct']}% 橙，<{USER['red_pct']}% 红"
            "（右上角「设置」可改）。",
            "设置：右上角「设置」里有开机自启动、启动托盘、低电量提醒、",
            "      电量颜色四项，改完立刻生效并保存在 user_settings.json。",
            "低电量提醒：低于阈值时在屏幕右下角弹一条提示，带系统提示音，",
            "      几秒后自己消失，不用点任何按钮；提示弹出时照常可以操作软件。",
            "关闭窗口：点 × 只会缩到任务栏托盘的圆环图标继续后台读取，",
            "          不会退出；要真正退出请点右上角「退出」或右键托盘图标选「退出软件」。",
            "托盘圆环：最多三层，外圈是电量最低的那台，鼠标悬停看全部设备电量。",
        ]
        # 统一弹窗：正文按宽度换行，按钮只有一个「知道了」
        AppDialog.tell(self, "使用说明", "\n".join(lines), width=560)

    def closeEvent(self, event: QCloseEvent):
        """点 × 不等于退出：有托盘就藏进托盘，没托盘就最小化，绝不悄悄结束进程。
        真正的退出只有两条路 —— 软件上的「退出」和托盘右键「退出软件」。"""
        if self._quitting:
            self.timer.stop()
            event.accept()
            return
        event.ignore()
        if self._tray is not None and self._tray.is_visible():
            # 静默缩到托盘：不弹任何系统通知
            self.hide()
        else:
            self.showMinimized()
            self.status.setText("托盘已关闭 · 点 × 只会最小化，退出请点右上角「✕ 退出」")


def _toast_glyph(size=24):
    """画提示条左上的小图标：琥珀色圆角方块 + 白色感叹号。"""
    k = 3                                    # 先按 3 倍画，缩下去才不糊
    s = size * k
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(COLOR_ORANGE))
    p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.30, s * 0.30)
    pen = QPen(QColor("#FFFFFF"))
    pen.setWidthF(s * 0.13)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.5, s * 0.28), QPointF(s * 0.5, s * 0.58))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QPointF(s * 0.5, s * 0.78), s * 0.072, s * 0.072)
    p.end()
    pm.setDevicePixelRatio(k)
    return pm


def play_notify_sound():
    """放系统「通知」提示音（异步，不阻塞界面）。取不到就退化成默认提示音。"""
    try:
        import winsound
        winsound.PlaySound("SystemNotification",
                           winsound.SND_ALIAS | winsound.SND_ASYNC)
        return
    except Exception:
        pass
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


class Toast(QWidget):
    """右下角系统通知样式的提示条。

    三个设计要点（对应「弹窗」的三个毛病）：
      · 位置：贴在屏幕工作区右下角，多条自动往上堆；
      · 交互：无边框、不抢焦点、鼠标可穿透 —— 弹出时照样能点设置里的按钮；
      · 退场：到点自己淡出，不用点「确定」，也没有按钮。
    """

    CARD_W = 384          # 卡片宽度
    PAD = 18              # 四周留给投影的透明边
    MARGIN = 14           # 距屏幕工作区右下角
    GAP = 8               # 多条提示之间的间距
    LIFE = 6500           # 默认停留毫秒数

    _live = []            # 当前在场的提示条（含正在淡出的），用于堆叠

    def __init__(self, title, body, life=None, tone=COLOR_ORANGE):
        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowTitle(title)
        self._fading = False

        shell = QVBoxLayout(self)
        shell.setContentsMargins(self.PAD, self.PAD, self.PAD, self.PAD)
        card = QFrame()
        card.setObjectName("toastcard")
        sh = QGraphicsDropShadowEffect(card)
        sh.setBlurRadius(22)
        sh.setOffset(0, 5)
        sh.setColor(QColor(15, 23, 42, 58))
        card.setGraphicsEffect(sh)
        shell.addWidget(card)

        row = QHBoxLayout(card)
        row.setContentsMargins(16, 13, 16, 14)
        row.setSpacing(12)

        ico = QLabel()
        ico.setFixedSize(24, 24)
        ico.setPixmap(_toast_glyph(24))
        ico.setStyleSheet("background: transparent;")
        row.addWidget(ico, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        lb_t = QLabel(title)
        lb_t.setObjectName("toasttitle")
        lb_t.setStyleSheet(f"color: {tone};")
        col.addWidget(lb_t)
        # 正文：一段文字（自动换行）/ 多段文字 / ("名称", "8%") 这样的左右两栏
        for item in ([body] if isinstance(body, str) else list(body)):
            if isinstance(item, (tuple, list)):
                name, right = item[0], item[1]
                rr = QHBoxLayout()
                rr.setContentsMargins(0, 0, 0, 0)
                rr.setSpacing(10)
                lb_n = QLabel(str(name))
                lb_n.setObjectName("toastrow")
                lb_r = QLabel(str(right))
                lb_r.setObjectName("toastpct")
                lb_r.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                rr.addWidget(lb_n, 1)
                rr.addWidget(lb_r, 0)
                col.addLayout(rr)
            else:
                lb = QLabel(str(item))
                lb.setObjectName("toastbody")
                lb.setWordWrap(True)
                lb.setTextFormat(Qt.PlainText)
                col.addWidget(lb)
        col.addSpacing(1)
        row.addLayout(col, 1)

        self.setFixedWidth(self.CARD_W + self.PAD * 2)
        self.adjustSize()

        self._life = QTimer(self)
        self._life.setSingleShot(True)
        self._life.timeout.connect(self.fade_out)
        self._life.start(int(life or self.LIFE))

    # ---- 摆放 ----
    def _screen_geo(self):
        scr = self.screen() or QApplication.primaryScreen()
        return scr.availableGeometry() if scr is not None else QRect(0, 0, 1280, 720)

    @classmethod
    def _restack(cls):
        """最新的贴最下面，先来的依次往上顶。"""
        alive = [t for t in cls._live if t.isVisible() and not t._fading]
        cls._live = alive
        if not alive:
            return
        g = alive[0]._screen_geo()
        x_right = g.right() - cls.MARGIN
        y = g.bottom() - cls.MARGIN
        for t in reversed(alive):
            y -= t.height()
            t.move(x_right - t.width() + 1, y)
            y -= cls.GAP

    # ---- 出场 / 退场 ----
    def showEvent(self, event):
        super().showEvent(event)
        if self not in Toast._live:
            Toast._live.append(self)
        self.setWindowOpacity(0.0)
        Toast._restack()
        self._fade(0.0, 1.0, 170)

    def _fade(self, a, b, ms, then=None):
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(ms)
        anim.setStartValue(a)
        anim.setEndValue(b)
        if then is not None:
            anim.finished.connect(then)
        self._anim = anim
        anim.start()

    def fade_out(self):
        if self._fading:
            return
        self._fading = True
        self._life.stop()
        self._fade(self.windowOpacity(), 0.0, 260, self.close)

    def closeEvent(self, event):
        if self in Toast._live:
            Toast._live.remove(self)
        super().closeEvent(event)
        Toast._restack()

    @classmethod
    def close_all(cls):
        for t in list(cls._live):
            t._life.stop()
            t.close()
        cls._live = []

    @classmethod
    def show_toast(cls, title, body, life=None, tone=COLOR_ORANGE):
        """弹一条，返回实例。不阻塞、不抢焦点。"""
        t = cls(title, body, life, tone)
        t.show()
        play_notify_sound()
        return t


class AppDialog(QDialog):
    """统一风格的小弹窗：正文按宽度自动换行（绝不切字），按钮右对齐。

    原来用 QMessageBox 有两个毛病：正文一长就被窗口右边缘切掉；
    两个按钮还都是同一个紫色，看不出哪个是「危险操作」。
    """

    def __init__(self, parent, title, text, ok_text="确定", cancel_text="",
                 danger=False, width=430):
        super().__init__(parent)
        self.setObjectName("appdlg")
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(ICON_PATH))
        self.setModal(True)
        try:
            self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, True)   # 不让拉伸
        except Exception:
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(18)

        body = QLabel(text)
        body.setObjectName("dlgbody")
        body.setTextFormat(Qt.PlainText)
        body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        # 先量一遍「不换行要多宽」，再决定窗口宽度：短句不会拖一条大白边，
        # 长句到 width 就换行（换行后高度由 QLabel 自己算，不会被窗口切掉）。
        body.setWordWrap(False)
        natural = body.sizeHint().width()
        body.setWordWrap(True)
        bw = max(min(natural + 2, width), min(240, width))
        body.setFixedWidth(bw)
        root.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        if cancel_text:
            b_no = QPushButton(cancel_text)
            b_no.setObjectName("ghost")
            b_no.setCursor(Qt.PointingHandCursor)
            b_no.setMinimumWidth(84)
            b_no.clicked.connect(self.reject)
            row.addWidget(b_no)
        b_ok = QPushButton(ok_text)
        b_ok.setObjectName("danger" if danger else "primary")
        b_ok.setCursor(Qt.PointingHandCursor)
        b_ok.setMinimumWidth(84)
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        row.addWidget(b_ok)
        root.addLayout(row)

        self.setFixedWidth(bw + 44)        # 左右各 22 的内边距

    @classmethod
    def ask(cls, parent, title, text, ok_text="确定", cancel_text="取消",
            danger=False, width=430):
        """返回 True 表示点了确定。"""
        dlg = cls(parent, title, text, ok_text, cancel_text, danger, width)
        return bool(dlg.exec())

    @classmethod
    def tell(cls, parent, title, text, ok_text="知道了", width=430):
        dlg = cls(parent, title, text, ok_text, "", False, width)
        return bool(dlg.exec())


class ProtocolDialog(QDialog):
    """顶部「协议一览」：一台设备一行，列出识别方式、接口、帧格式与电量字段。
    表格内容完全由 PROFILES + PROTOCOL_INFO 生成，可一键复制成纯文本或 Markdown，
    方便直接贴到文档/聊天窗口里。"""

    COLS = ("设备", "VID / PID", "HID 接口 / 通道", "协议",
            "查询帧（发送）", "电量字段（回包）", "备注 / 来源")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设备与协议一览")
        self.resize(1080, 470)
        self.setMinimumSize(800, 430)
        self.rows = self._collect()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tip = QLabel(
            f"共 {N_DEVICES} 台设备 · 一行一台，就是程序实际在用的读取链路。\n"
            "「查询帧」是程序发给设备的原始字节，「电量字段」是回包里取值的位置 —— "
            "以后换设备/排查问题直接照这张表对。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        table = QTableWidget(len(self.rows), len(self.COLS))
        table.setHorizontalHeaderLabels(list(self.COLS))
        table.verticalHeader().setVisible(False)
        table.setWordWrap(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setAlternatingRowColors(False)
        table.setShowGrid(True)
        table.setFocusPolicy(Qt.NoFocus)
        for r, vals in enumerate(self.rows):
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                it.setToolTip(val)
                if c == 0:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                table.setItem(r, c, it)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, w in enumerate((132, 122, 132, 132, 178, 186)):
            table.setColumnWidth(c, w)
        table.resizeRowsToContents()
        table.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(table, 1)

        row = QHBoxLayout()
        row.setSpacing(10)
        b_txt = QPushButton("复制纯文本")
        b_txt.setObjectName("primary")
        b_txt.clicked.connect(lambda: self._copy(False))
        b_md = QPushButton("复制 Markdown")
        b_md.setObjectName("capturetop")
        b_md.clicked.connect(lambda: self._copy(True))
        b_close = QPushButton("关闭")
        b_close.setObjectName("ghost")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_txt)
        row.addWidget(b_md)
        row.addStretch(1)
        row.addWidget(b_close)
        layout.addLayout(row)

    def _collect(self):
        """表格用原始文本（保留手工换行，排版更好看）。"""
        rows = []
        for p in PROFILES:
            info = PROTOCOL_INFO.get(p["key"], {})
            rows.append([p["name"]] + [info.get(k) or "—" for k in
                                       ("ids", "iface", "proto", "query", "field", "note")])
        return rows

    def _flat_rows(self):
        """复制到剪贴板时把换行拍平成一行，粘贴到别处不会串行。"""
        return [[v.replace("\n", " ") for v in row] for row in self.rows]

    def _as_text(self):
        out = [f"设备与协议一览（共 {N_DEVICES} 台）", "=" * 40, ""]
        for vals in self._flat_rows():
            for col, val in zip(self.COLS, vals):
                out.append(f"{col}：{val}")
            out.append("-" * 40)
        return "\n".join(out)

    def _as_markdown(self):
        out = [f"## 设备与协议一览（共 {N_DEVICES} 台）", "",
               "| " + " | ".join(self.COLS) + " |",
               "|" + "|".join(["---"] * len(self.COLS)) + "|"]
        for vals in self._flat_rows():
            out.append("| " + " | ".join(v.replace("|", "\\|") for v in vals) + " |")
        return "\n".join(out)

    def _copy(self, markdown):
        text = self._as_markdown() if markdown else self._as_text()
        try:
            QApplication.clipboard().setText(text)
            AppDialog.tell(self, "已复制", "已复制到剪贴板，可直接粘贴。", width=300)
        except Exception as e:
            log_err("复制协议表失败: " + repr(e))


class AddDeviceRow(QFrame):
    """列表最下面那行「＋ 导入设备」：虚线卡片，点一下打开导入对话框。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("adddev")
        self.setFixedHeight(48)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("导入 .json 协议文件，把新设备加进监控列表")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)
        plus = QLabel()
        plus.setPixmap(_glyph_pixmap("plus", INK, 16))
        plus.setStyleSheet("background:transparent;")
        txt = QLabel("导入设备")
        txt.setObjectName("adddevtext")
        hint = QLabel("（协议文件 .json · 导入后自动匹配本机设备并读取）")
        hint.setObjectName("adddevhint")
        lay.addStretch(1)
        lay.addWidget(plus)
        lay.addWidget(txt)
        lay.addWidget(hint)
        lay.addStretch(1)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _SpecTestWorker(QThread):
    """后台逐台试读：慢设备（等回包 1~5 秒）也不会卡住导入对话框。"""

    row = Signal(int, str)        # 行号, 该行的状态文案
    progress = Signal(str)        # 「正在测试 2/3…」
    done = Signal(list)           # [(名称, 电量|null, ok/missing/noread)]

    def __init__(self, picked, devs, parent=None):
        super().__init__(parent)
        self.picked = list(picked)
        self.devs = list(devs or [])

    def run(self):
        rows = []
        total = len(self.picked)
        for i, (r, spec) in enumerate(self.picked, 1):
            if self.isInterruptionRequested():
                break
            self.progress.emit(f"正在测试 {i}/{total}：「{spec['name']}」…")
            try:
                hits = spec_hits(spec, self.devs)
            except Exception as e:
                log_err("测试读取匹配异常: " + repr(e))
                hits = []
            if not hits:
                self.row.emit(r, "⚠️ 本机未找到（没插？）")
                rows.append((spec["name"], None, "missing"))
                continue
            got = None
            for d in hits:
                if self.isInterruptionRequested():
                    break
                got = read_by_spec(spec, d)
                if got:
                    break
            if got:
                self.row.emit(r, f"✅ 读到 {got[0]}%" + ("（充电中）" if got[1] else ""))
                rows.append((spec["name"], got[0], "ok"))
            else:
                self.row.emit(r, "⚠️ 找到设备但没读到电量")
                rows.append((spec["name"], None, "noread"))
        self.done.emit(rows)


class ImportDialog(QDialog):
    """导入设备：选一个 .json 协议文件，按里面的 VID/PID 自动匹配本机设备，
    勾选后加入监控列表；顺手可以把内置设备的协议导出成模板来改。"""

    COLS = ("设备名", "类型 / 连接", "识别条件", "本机状态")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("importdlg")
        self.setWindowTitle("导入设备（协议文件）")
        self.resize(780, 540)
        self.specs = []
        self.errors = []
        try:
            self.devs = enumerate_hid() if _WIN_HID else []
        except Exception as e:
            log_err("导入对话框枚举 HID 失败: " + repr(e))
            self.devs = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        tip = QLabel(
            "选协议文件（.json，可多选）：一台设备一个文件也行，一个文件装多台也行。\n"
            "程序按文件里写的 VID/PID 自动在电脑里找设备；勾选后导入，立刻开始读电量，"
            "并记到 devices.json，下次启动自动加载。\n"
            f"导入过的协议都会在「协议库」里留一份（{PACK_DIR}）。")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        root.addWidget(tip)

        frow = QHBoxLayout()
        frow.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("还没有选择协议文件…")
        self.path_edit.setStyleSheet(
            "background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;"
            "padding:8px 10px;font-size:12px;color:#334155;")
        b_pick = QPushButton("选择协议文件")
        b_pick.setObjectName("primary")
        b_pick.clicked.connect(self.pick_file)
        frow.addWidget(self.path_edit, 1)
        frow.addWidget(b_pick)
        root.addLayout(frow)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(list(self.COLS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("QTableWidget::item { padding: 6px 8px; }")
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        root.addWidget(self.table, 1)

        self.msg = QLabel("")
        self.msg.setObjectName("hint")
        self.msg.setWordWrap(True)
        root.addWidget(self.msg)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        b_tpl = QPushButton("导出协议文件（每台一个）")
        b_tpl.setObjectName("ghost")
        b_tpl.setToolTip("把当前列表里每台设备各导出一个 .json，文件名就是设备名，默认存到「协议库」")
        b_tpl.clicked.connect(self.export_template)
        b_cfg = QPushButton("浏览 configs")
        b_cfg.setObjectName("ghost")
        b_cfg.setToolTip(f"递归读取 {CONFIGS_DIR} 下的全部 .json / .yaml（可以按厂商建子文件夹）")
        b_cfg.clicked.connect(self.load_configs)
        b_dir = QPushButton("打开协议库")
        b_dir.setObjectName("ghost")
        b_dir.setToolTip(f"导入 / 导出的协议都在这儿：{PACK_DIR}")
        b_dir.clicked.connect(self.open_folder)
        b_test = QPushButton("测试读取选中")
        b_test.setObjectName("capturetop")
        b_test.setToolTip("按协议文件真的发一帧，验证能不能读到这台设备的电量")
        b_test.clicked.connect(self.test_selected)
        b_cancel = QPushButton("取消")
        b_cancel.setObjectName("ghost")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("导入选中")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self.do_import)
        self.b_test = b_test
        self.b_ok = b_ok
        brow.addWidget(b_tpl)
        brow.addWidget(b_cfg)
        brow.addWidget(b_dir)
        brow.addStretch(1)
        brow.addWidget(b_test)
        brow.addWidget(b_cancel)
        brow.addWidget(b_ok)
        root.addLayout(brow)
        self._tester = None

    # ---------------- 文件 ----------------
    def _start_dir(self):
        """优先定位到 configs（社区配置都在这里），没有就回到协议库。"""
        cfg = ensure_configs_dir()
        try:
            has = any(os.scandir(cfg))
        except Exception:
            has = False
        return cfg if has else ensure_pack_dir()

    def configs_files(self):
        """递归列出 configs 下的所有配置文件（支持按厂商分文件夹）。"""
        root = ensure_configs_dir()
        out = []
        for base, _dirs, files in os.walk(root):
            for name in files:
                low = name.lower()
                if "template" in low:       # 模板不是设备，别混进待导入列表
                    continue
                if os.path.splitext(name)[1].lower() in (".json", ".yaml", ".yml"):
                    out.append(os.path.join(base, name))
        return sorted(out)

    def load_configs(self):
        """一键把 configs 目录里的设备配置全部读进表格，勾选后再导入。"""
        files = self.configs_files()
        if not files:
            self.msg.setText(f"configs 目录里没有设备配置：{CONFIGS_DIR}（template.json 不算）")
            return
        self.load_files(files)

    def pick_file(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择协议文件（可多选，一次导入多台设备）", self._start_dir(),
            "协议文件 (*.json *.yaml *.yml);;JSON (*.json);;YAML (*.yaml *.yml);;所有文件 (*.*)")
        if not paths:
            return
        self.load_files(paths)

    def load_file(self, path):
        """兼容单个文件（脚本/拖放用）。"""
        self.load_files([path])

    def load_files(self, paths):
        specs, errors = [], []
        for path in paths:
            s, e = load_pack(path)
            specs.extend(s)
            for msg in e:
                errors.append(msg if len(paths) == 1 else f"{os.path.basename(path)}：{msg}")
        self.specs = specs
        self.errors = errors
        if len(paths) == 1:
            self.path_edit.setText(paths[0])
            self.path_edit.setToolTip(paths[0])
        else:
            self.path_edit.setText(f"已选 {len(paths)} 个文件 · {len(specs)} 台设备")
            self.path_edit.setToolTip("\n".join(paths))
        self.fill_table()
        if errors:
            self.msg.setText("⚠️ 有 " + str(len(errors)) + " 处问题被跳过："
                             + "；".join(errors[:3]))
        elif specs:
            self.msg.setText(f"✅ 读到 {len(specs)} 台设备，勾选后点「导入选中」")
        else:
            self.msg.setText("⚠️ 选的文件里没有可用的设备定义")

    # ---------------- 表格 ----------------
    def fill_table(self):
        self.table.setRowCount(0)
        for spec in self.specs:
            r = self.table.rowCount()
            self.table.insertRow(r)
            it = QTableWidgetItem(spec["name"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.table.setItem(r, 0, it)
            wire = spec.get("wire") or "—"
            self.table.setItem(r, 1, QTableWidgetItem(
                f"{TYPES.get(spec['type'], ('', '设备'))[1]} / {wire}"))
            m = spec["match"]
            ids = []
            if m.get("vid") is not None:
                ids.append(f"VID 0x{m['vid']:04X}")
            if m.get("pid") is not None:
                ids.append(f"PID 0x{m['pid']:04X}")
            if m.get("product_contains"):
                ids.append("名含 " + "/".join(m["product_contains"]))
            self.table.setItem(r, 2, QTableWidgetItem(" ".join(ids) or "—"))
            hits = spec_hits(spec, self.devs)
            if hits:
                st = f"✅ 找到 {len(hits)} 个接口"
                if not any(d.get("out_len") for d in hits):
                    st += "（只读，可能发不出帧）"
            else:
                st = "⚠️ 本机未找到"
            self.table.setItem(r, 3, QTableWidgetItem(st))
            self.table.setRowHeight(r, 34)
        if not self.specs:
            self.msg.setText("先选一个协议文件。没有文件？点「导出协议文件」照着改。")

    def checked_specs(self):
        out = []
        for r, spec in enumerate(self.specs):
            it = self.table.item(r, 0)
            if it is not None and it.checkState() == Qt.Checked:
                out.append((r, spec))
        return out

    # ---------------- 动作 ----------------
    def test_selected(self):
        """后台逐台试读所有勾选的设备（不是只测第一台），每行单独更新状态。

        读取要发帧 + 等回包，慢设备一台就是好几秒；放在 UI 线程会直接卡死窗口，
        所以丢给 QThread，结果用信号回填表格。
        """
        if self._tester is not None and self._tester.isRunning():
            self.msg.setText("上一轮测试还在跑，等它结束再点。")
            return
        picked = self.checked_specs()
        if not picked:
            self.msg.setText("先在表格里勾选设备，再点测试。")
            return
        self.b_test.setEnabled(False)
        self.b_ok.setEnabled(False)
        w = _SpecTestWorker(picked, self.devs, self)
        w.progress.connect(self.msg.setText)
        w.row.connect(self._on_test_row)
        w.done.connect(self._on_test_done)
        w.finished.connect(self._on_test_finished)
        self._tester = w
        w.start()

    def _on_test_row(self, row, text):
        if 0 <= row < self.table.rowCount():
            self.table.setItem(row, 3, QTableWidgetItem(text))

    def _on_test_finished(self):
        self.b_test.setEnabled(True)
        self.b_ok.setEnabled(True)

    def _on_test_done(self, rows):
        ok = [(n, p) for n, p, st in rows if st == "ok"]
        missing = [n for n, _p, st in rows if st == "missing"]
        noread = [n for n, _p, st in rows if st == "noread"]
        parts = []
        if ok:
            parts.append(f"✅ 读到 {len(ok)} 台：" + "、".join(f"{n} {p}%" for n, p in ok))
        if missing:
            parts.append(f"⚪ 本机没找到：{'、'.join(missing)}（接收器没插 / 设备关机）")
        if noread:
            parts.append(f"⚠️ 找到但没读到：{'、'.join(noread)}（设备休眠，或帧格式与固件不符）")
        self.msg.setText("；".join(parts) + "。" if parts else "测试完成，没有结果。")

    def done(self, result):
        """关闭窗口前等后台测试收尾：QThread 还在跑就被析构会直接崩溃。"""
        self._stop_tester()
        super().done(result)

    def closeEvent(self, event):
        self._stop_tester()
        super().closeEvent(event)

    def _stop_tester(self):
        w = getattr(self, "_tester", None)
        if w is not None and w.isRunning():
            w.requestInterruption()
            if not w.wait(12000):
                # 极慢设备仍未结束：断开父子关系，让它跑完自己退出，不随对话框析构
                w.setParent(None)

    def export_template(self):
        """导出协议文件：一台设备一个 .json，文件名就是设备名。"""
        items = exportable_packs()
        if not items:
            self.msg.setText("当前没有可导出的设备。")
            return
        start = ensure_pack_dir()
        folder = QFileDialog.getExistingDirectory(
            self, f"选择导出文件夹（将生成 {len(items)} 个协议文件）", start)
        if not folder:
            return
        saved, failed = [], []
        for _k, fname, payload in items:
            path = os.path.join(folder, fname)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                saved.append(fname)
            except Exception as e:
                failed.append(f"{fname}（{e}）")
        if saved:
            names = "、".join(saved[:4]) + ("…" if len(saved) > 4 else "")
            self.msg.setText(f"✅ 已导出 {len(saved)} 个协议文件到 {folder}：{names}")
        if failed:
            self.msg.setText(self.msg.text() + "；⚠️ 失败：" + "、".join(failed))

    def open_folder(self):
        try:
            os.startfile(ensure_pack_dir())      # 仅 Windows
        except Exception as e:
            self.msg.setText(f"打不开文件夹：{e}")

    def do_import(self):
        picked = self.checked_specs()
        if not picked:
            self.msg.setText("还没有勾选设备。")
            return
        # 导入的协议 key 会加 u: 前缀；去掉前缀若正好等于某台内置设备，
        # 说明导入的就是内置设备的协议模板，直接跳过，避免列表里出现两份。
        # 如果文件里的 key 去前缀后正好等于内置设备，说明是「内置协议的模板」，
        # 直接跳过，免得同一台设备在列表里出现两次。
        builtin_keys = {p["key"] for p in PROFILES if not p.get("spec")}
        n, skipped, restored = 0, [], []
        for _r, spec in picked:
            bare = spec["key"][2:] if spec["key"].startswith("u:") else spec["key"]
            if bare in builtin_keys:
                skipped.append(spec["name"])
                continue
            # 之前右键移除掉的内置设备，重新导入这份协议就等于把它恢复回来
            if bare in HIDDEN_BUILTIN and unhide_builtin(bare):
                restored.append(spec["name"])
                n += 1
                continue
            add_user_spec(spec)
            save_pack_to_library(spec)      # 协议库留一份，随时能再导入 / 分享
            n += 1
        sync_device_count()
        if n and not save_user_specs():
            self.msg.setText("⚠️ 已加入本次运行，但写 devices.json 失败（磁盘/权限），"
                             "下次启动要重新导入。")
            return
        if skipped and not n:
            self.msg.setText("这些设备本来就在列表里了：" + "、".join(skipped)
                             + "，不用重复导入。")
            return
        if not n:
            self.msg.setText("没有可导入的设备。")
            return
        self._skipped = skipped
        self.accept()


class CaptureDialog(QDialog):
    _result = Signal(str)

    def __init__(self, parent, vids, keywords):
        super().__init__(parent)
        self.setWindowTitle("捕获原始报文（全部设备）")
        self.resize(680, 520)
        self.vids = set(vids) if vids else None
        self.keywords = keywords
        self._result.connect(self._set_text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel(
            "「开始捕获」= 快速抓取（约 1 秒）；「深度探测」= 对协议未知的设备额外做\n"
            "长时间监听 + 主动发查询帧（约 20 秒，用于协议未知的设备）。\n"
            "每个厂商接口都会先列出描述符里的真实报告 ID，再按它发帧。\n"
            "设备读不到时，先看下面「直读尝试」那几行写的耗时与回应。\n"
            "点「一键复制」可把下面全部内容拷走。"
        ))
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        _mf = QFont()
        _mf.setFamilies(["Cascadia Mono", "Consolas", "Microsoft YaHei UI"])
        _mf.setPointSizeF(9.0)
        self.text.setFont(_mf)
        layout.addWidget(self.text, 1)
        row = QHBoxLayout()
        row.setSpacing(10)
        btn = QPushButton("开始捕获")
        btn.setObjectName("primary")
        btn.clicked.connect(lambda: self.run(False))
        btn2 = QPushButton("深度探测（约 20 秒）")
        btn2.setObjectName("capturetop")
        btn2.clicked.connect(lambda: self.run(True))
        btn3 = QPushButton("一键复制")
        btn3.setObjectName("capturetop")
        btn3.setToolTip("把文本框里的全部内容复制到剪贴板")
        btn3.clicked.connect(self._copy_all)
        row.addWidget(btn)
        row.addWidget(btn2)
        row.addWidget(btn3)
        row.addStretch(1)
        layout.addLayout(row)
        self.run(False)

    def _copy_all(self):
        """一键复制全部报文（点完在按钮上回一句「已复制」，避免点了没反应）。"""
        QApplication.clipboard().setText(self.text.toPlainText())
        btn = self.sender()
        if btn is not None:
            old = btn.text()
            btn.setText("✅ 已复制")
            QTimer.singleShot(1200, lambda: btn.setText(old))

    def _set_text(self, t):
        self.text.setPlainText(t)

    def run(self, deep=False):
        self._result.emit("探测中…（深度探测约需 20 秒，请稍候）" if deep else "捕获中…")
        threading.Thread(target=self._cap, args=(deep,), daemon=True).start()

    def _cap(self, deep=False):
        try:
            if not _WIN_HID:
                self._result.emit("当前平台不支持 HID 直连（需在 Windows 上运行）。")
                return
            result = capture_raw(self.vids, self.keywords, deep=deep)
            self._result.emit(result)
        except Exception as e:
            self._result.emit("捕获出错：" + str(e))


def _global_excepthook(etype, exc, tb):
    import traceback as _tb
    msg = "".join(_tb.format_exception(etype, exc, tb))
    log_err(msg)
    try:
        from PySide6.QtWidgets import QMessageBox
        if QApplication.instance():
            QMessageBox.critical(None, "出错了", msg[-2000:])
    except Exception:
        pass


def main():
    # 崩溃留痕先挂上：后面任何一步崩了都能在 crash.log 里看到栈（不占编号，免得打乱下面的步骤号）
    _install_crash_handlers()
    try:
        threading.excepthook = _thread_excepthook
    except Exception:
        pass
    # 0) 本机偏好先就位：配色、托盘、提醒都要用它；再让系统启动项跟它对齐
    try:
        load_user_settings()
        sync_autostart()
    except Exception as e:
        log_err("加载用户设置失败: " + repr(e))
    # 0.1) 协议库就绪：内置设备的协议各写一份进去（已存在的文件不覆盖）
    try:
        seed_library()
    except Exception as e:
        log_err("初始化协议库失败: " + repr(e))
    # 0.5) 恢复「被右键移除的内置设备」记录 —— 删掉的别再自己冒出来
    try:
        apply_hidden_builtins(load_cache().get("_hidden"))
    except Exception as e:
        log_err("恢复隐藏设备失败: " + repr(e))
    # 先加载用户导入的设备（devices.json），再建窗口 —— 台数、卡片、标题一次到位
    try:
        n_user, errs = load_user_devices()
        if n_user:
            sync_device_count()
            log_err(f"已从 devices.json 加载 {n_user} 台导入设备")
    except Exception as e:
        log_err("加载 devices.json 失败: " + repr(e))
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(ICON_PATH))   # 任务栏/Alt-Tab 图标
    app.setFont(base_font())              # 全局基准字体：所有窗口（含弹窗）都用雅黑
    sys.excepthook = _global_excepthook
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    app.setStyleSheet(STYLE)
    # 已经有实例在跑了：把那个窗口叫出来（--tray 静默启动则什么都不做），当前进程退出
    if not claim_single_instance(silent=("--tray" in sys.argv)):
        return
    win = MainWindow()
    globals()["_MAIN_WIN"] = win
    # 开机自启动带 --tray 进来：只留托盘、绝不弹主窗口。
    # （托盘没建成 / 用户把托盘关了，就正常显示窗口，避免开机后既没窗口也没图标）
    tray_only = ("--tray" in sys.argv) and (win._tray is not None)
    if tray_only:
        win.hide()
    else:
        win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
