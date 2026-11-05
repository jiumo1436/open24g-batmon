#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验 2.4G 设备电量监控的配置文件（CONFIG_SCHEMA v1），零第三方依赖。

与程序内「＋ 导入设备」用的是同一套规则：字段范围、十六进制帧、公式白名单、
range 顺序、串口参数等。校验通过基本就能在界面里导入成功。

用法：
    python validate_config.py 配置.json
    python validate_config.py 配置.yaml
    python validate_config.py configs/            # 递归校验整个目录

退出码：0 = 全部通过（模板文件跳过，不算错误）；1 = 至少一个文件有错误；2 = 用法/路径问题。

YAML 需要本机装有 pyyaml；没有则提示改用 JSON，不影响 JSON 校验。
"""
import ast
import json
import math
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PACK_FORMAT = "dianliang-device-pack"
PACK_VERSION = 1
DEVICE_TYPES = ("mouse", "keyboard", "headphone", "gamepad", "other")
SERIAL_PARITY = ("N", "O", "E", "M", "S")
SERIAL_STOPBITS = ("1", "1.5", "2")

FORMULA_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
                 ast.Load, ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div,
                 ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd)
FORMULA_FUNCS = ("abs", "min", "max", "round", "int", "float")
FORMULA_IMPL = {"abs": abs, "min": min, "max": max, "round": round,
                "int": int, "float": float}


# --------------------------------------------------------------------------
#  基础解析 —— 与 app.py 逐字对应的语义
# --------------------------------------------------------------------------
def hx(s):
    """把 "AA BB" / "AABB" / "0xAA,0xBB" 解析成 bytes。空串返回 b""。"""
    if not s:
        return b""
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
            raise ValueError("不是合法的十六进制：%r" % (s,))
    return bytes(out)


def num(v, default=0):
    """"0x1234" / "1234" / 4660 都接受。4 位以内当十进制，更长当十六进制。"""
    if v is None:
        return default
    if isinstance(v, int):
        return v
    t = str(v).strip()
    if not t:
        return default
    return int(t, 16) if t.lower().startswith("0x") else int(t, 16 if len(t) > 4 else 10)


def cn_int(value, field, name, lo=None, hi=None, default=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise ValueError("「%s」的 %s 必填，而且必须是整数" % (name, field))
    try:
        if isinstance(value, str):
            txt = value.strip()
            got = int(txt, 16) if txt.lower().startswith(("0x", "-0x")) else int(txt, 10)
        else:
            got = int(value)
    except (TypeError, ValueError):
        raise ValueError("「%s」的 %s 必须是整数，现在是 %r" % (name, field, value))
    if lo is not None and got < lo:
        raise ValueError("「%s」的 %s 不能小于 %s（现在是 %s）" % (name, field, lo, got))
    if hi is not None and got > hi:
        raise ValueError("「%s」的 %s 不能大于 %s（现在是 %s）" % (name, field, hi, got))
    return got


def cn_num(value, field, name, default=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        return num(value, 0)
    except (TypeError, ValueError):
        raise ValueError("「%s」的 %s 不是合法的数字/十六进制：%r" % (name, field, value))


def cn_float(value, field, name, default=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise ValueError("「%s」的 %s 必填，而且必须是数字" % (name, field))
    try:
        got = float(value)
    except (TypeError, ValueError):
        raise ValueError("「%s」的 %s 必须是数字，现在是 %r" % (name, field, value))
    if not math.isfinite(got):
        raise ValueError("「%s」的 %s 不是有效数字：%r" % (name, field, value))
    return got


# --------------------------------------------------------------------------
#  电量换算：公式白名单 / 查表可用性
# --------------------------------------------------------------------------
def _apply_op(op, a, b):
    if isinstance(op, ast.Add):
        return a + b
    if isinstance(op, ast.Sub):
        return a - b
    if isinstance(op, ast.Mult):
        return a * b
    if isinstance(op, (ast.Div, ast.FloorDiv, ast.Mod)) and b == 0:
        raise ValueError("除以 0")
    if isinstance(op, ast.Div):
        return a / b
    if isinstance(op, ast.FloorDiv):
        return a // b
    if isinstance(op, ast.Mod):
        return a % b
    if isinstance(op, ast.Pow):
        return a ** b
    raise ValueError("不支持的运算符")


def _eval(node, x):
    if isinstance(node, ast.Expression):
        return _eval(node.body, x)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ("x", "raw"):
            return x
        return FORMULA_IMPL[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, x)
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BinOp):
        return _apply_op(node.op, _eval(node.left, x), _eval(node.right, x))
    if isinstance(node, ast.Call):
        fn = _eval(node.func, x)
        return fn(*[_eval(a, x) for a in node.args])
    raise ValueError("不支持的表达式")


def safe_formula(expr, x):
    """按程序同一套 AST 白名单检查并试算，失败抛 ValueError（中文）。"""
    try:
        tree = ast.parse(str(expr), mode="eval")
    except SyntaxError:
        raise ValueError("公式语法错误：%r" % (expr,))
    for node in ast.walk(tree):
        if not isinstance(node, FORMULA_NODES):
            raise ValueError("公式里只能做加减乘除、取余、乘方和括号")
        if isinstance(node, ast.Name) and node.id not in ("x", "raw") \
                and node.id not in FORMULA_FUNCS:
            raise ValueError("公式里有未知变量：%s" % node.id)
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in FORMULA_FUNCS):
                raise ValueError("公式里只能调用 abs / min / max / round / int / float")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("公式里只允许数字常量")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exp = node.right
            if not (isinstance(exp, ast.Constant) and isinstance(exp.value, (int, float))
                    and abs(exp.value) <= 8):
                raise ValueError("乘方的指数必须是 -8~8 之间的数字")
    try:
        val = _eval(tree, x)
    except ZeroDivisionError:
        raise ValueError("公式算不出来：除以 0")
    except Exception as e:
        raise ValueError("公式算不出来：%s" % e)
    if isinstance(val, complex) or not isinstance(val, (int, float)):
        raise ValueError("公式结果不是数字")
    return float(val)


def map_usable(table):
    """查表至少要有一个可用的 [原始值, 百分比] 点。"""
    pts = 0
    if isinstance(table, dict):
        for k, v in table.items():
            try:
                float(k), float(v)
                pts += 1
            except (TypeError, ValueError):
                continue
    elif isinstance(table, list):
        for item in table:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    float(item[0]), float(item[1])
                    pts += 1
                except (TypeError, ValueError):
                    continue
    return pts > 0


# --------------------------------------------------------------------------
#  设备级校验 —— 对应 app.normalize_spec
# --------------------------------------------------------------------------
def normalize_spec(raw):
    """校验一台设备并返回摘要；不合法抛 ValueError（中文）。"""
    if not isinstance(raw, dict):
        raise ValueError("设备定义必须是一个 JSON 对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("缺少 name（设备名称）")

    # ---- 传输方式 ----
    tr_raw = raw.get("transport") or {}
    if not isinstance(tr_raw, dict):
        raise ValueError("「%s」的 transport 必须是对象" % name)
    ttype = str(tr_raw.get("type") or "hid").strip().lower()
    if ttype not in ("hid", "serial"):
        raise ValueError("「%s」的 transport.type 只能是 hid 或 serial（现在是 %r）"
                         % (name, ttype))
    if ttype == "serial":
        parity = str(tr_raw.get("parity") or "N").strip().upper()
        if parity not in SERIAL_PARITY:
            raise ValueError("「%s」的 transport.parity 只能是 N/O/E/M/S" % name)
        cn_int(tr_raw.get("baud"), "transport.baud", name, lo=300, hi=4000000, default=115200)
        cn_int(tr_raw.get("bytesize"), "transport.bytesize", name, lo=5, hi=8, default=8)
        cn_int(tr_raw.get("in_len"), "transport.in_len", name, lo=1, hi=65536, default=512)
        cn_int(tr_raw.get("out_len"), "transport.out_len", name, lo=0, hi=65536, default=0)
        if str(tr_raw.get("stopbits") or "1").strip() not in SERIAL_STOPBITS:
            raise ValueError("「%s」的 transport.stopbits 只能是 1 / 1.5 / 2" % name)

    rd_raw = raw.get("read")
    if not isinstance(rd_raw, dict):
        raise ValueError("「%s」缺少 read 段（发什么帧、从哪取电量）" % name)

    # ---- 识别条件 ----
    match = raw.get("match") or {}
    if not isinstance(match, dict):
        raise ValueError("「%s」的 match 必须是对象" % name)
    vid = cn_num(match.get("vid"), "match.vid", name)
    cn_num(match.get("pid"), "match.pid", name)
    cn_num(match.get("usage_page"), "match.usage_page", name)
    cn_int(match.get("min_in"), "match.min_in", name, lo=0, default=0)
    cn_int(match.get("min_out"), "match.min_out", name, lo=0, default=0)
    product_contains = [str(x) for x in (match.get("product_contains") or [])]
    if ttype == "hid" and vid is None and not product_contains:
        raise ValueError("「%s」的 match 里至少有 vid 或 product_contains" % name)

    # ---- 回包 ----
    reply = rd_raw.get("reply") or {}
    if not isinstance(reply, dict):
        raise ValueError("「%s」的 reply 必须是对象" % name)
    battery = reply.get("battery") or {}
    if not isinstance(battery, dict) or battery.get("byte") is None:
        raise ValueError("「%s」的 reply.battery.byte 必填（电量在第几个字节）" % name)

    rng = reply.get("range") or [0, 100]
    if not isinstance(rng, (list, tuple)) or len(rng) < 2:
        raise ValueError("「%s」的 reply.range 必须是 [最小, 最大] 两个整数" % name)
    lo = cn_int(rng[0], "reply.range[0]", name, default=0)
    hi = cn_int(rng[1], "reply.range[1]", name, default=100)
    if lo > hi:
        raise ValueError("「%s」的 reply.range 最小值不能大于最大值" % name)

    cn_int(battery.get("byte"), "reply.battery.byte", name, lo=0)
    cn_int(battery.get("bytes"), "reply.battery.bytes", name, lo=1, hi=4, default=1)
    if str(battery.get("order") or "big").strip().lower() not in ("big", "little"):
        raise ValueError("「%s」的 reply.battery.order 只能是 big 或 little" % name)
    if str(battery.get("mode") or "raw").strip().lower() not in ("raw", "hi", "lo"):
        raise ValueError("「%s」的 reply.battery.mode 只能是 raw / hi / lo" % name)
    cn_float(battery.get("scale"), "reply.battery.scale", name, default=1.0)
    cn_float(battery.get("offset"), "reply.battery.offset", name, default=0.0)

    if battery.get("mask") not in (None, ""):
        if not cn_num(battery.get("mask"), "reply.battery.mask", name, default=0):
            raise ValueError("「%s」的 reply.battery.mask 不能是 0" % name)
    cn_int(battery.get("shift"), "reply.battery.shift", name, lo=-32, hi=32, default=0)

    if battery.get("formula"):
        try:
            safe_formula(battery["formula"], 50)
        except ValueError as e:
            raise ValueError("「%s」的 reply.battery.formula 有问题：%s" % (name, e))
    if battery.get("map"):
        if not isinstance(battery["map"], (list, dict)):
            raise ValueError("「%s」的 reply.battery.map 必须是数组或对象" % name)
        if not map_usable(battery["map"]):
            raise ValueError("「%s」的 reply.battery.map 里没有可用的 [原始值, 百分比] 点" % name)
    read_as = str(battery.get("read_as") or "percent").strip().lower()
    if read_as not in ("percent", "voltage", "raw"):
        raise ValueError("「%s」的 reply.battery.read_as 只能是 percent / voltage / raw" % name)
    if read_as == "voltage":
        cn_float(battery.get("voltage_scale"), "reply.battery.voltage_scale", name, default=0.01)
        vmin = cn_float(battery.get("voltage_min"), "reply.battery.voltage_min", name, default=0.0)
        vmax = cn_float(battery.get("voltage_max"), "reply.battery.voltage_max", name, default=100.0)
        if vmax == vmin:
            raise ValueError("「%s」的 voltage_max 不能等于 voltage_min" % name)

    ch_raw = reply.get("charging") or None
    if ch_raw is not None and not isinstance(ch_raw, dict):
        raise ValueError("「%s」的 reply.charging 必须是对象" % name)
    if isinstance(ch_raw, dict):
        cn_int(ch_raw.get("byte"), "reply.charging.byte", name, default=0)
        if ch_raw.get("mask") not in (None, ""):
            cn_num(ch_raw.get("mask"), "reply.charging.mask", name, default=0)

    # ---- 读取流程 ----
    def frames(v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v]

    query = frames(rd_raw.get("query"))
    pre = frames(rd_raw.get("pre"))
    cn_int(rd_raw.get("pre_delay_ms"), "read.pre_delay_ms", name, lo=0, hi=10000, default=0)
    cn_int(rd_raw.get("pad_to"), "read.pad_to", name, lo=0, hi=65536, default=0)
    send = str(rd_raw.get("send") or "auto").strip().lower()
    if send not in ("auto", "write", "setoutput"):
        raise ValueError("「%s」的 read.send 只能是 auto / write / setoutput" % name)
    cn_int(rd_raw.get("attempts"), "read.attempts", name, lo=1, hi=5, default=1)
    cn_int(rd_raw.get("listen_ms"), "read.listen_ms", name, lo=50, hi=5000, default=600)
    passive = cn_int(rd_raw.get("passive_ms"), "read.passive_ms", name, lo=0, hi=3000, default=0)
    if not query and not passive:
        raise ValueError("「%s」的 read.query 为空，就必须给 passive_ms（只听设备主动推）" % name)

    for q in query + pre:
        try:
            hx(q)
        except ValueError:
            raise ValueError("「%s」的帧不是合法的十六进制：%r" % (name, q))
    for c in frames(reply.get("contains")):
        try:
            hx(c)
        except ValueError:
            raise ValueError("「%s」的 reply.contains 不是合法的十六进制：%r" % (name, c))

    dtype = str(raw.get("type") or "other").lower()
    warns = []
    if dtype not in DEVICE_TYPES:
        warns.append("type=%r 不在推荐值里，卡片会用默认图标" % dtype)
    if not frames(reply.get("contains")):
        warns.append("没有 reply.contains 锚点：任何回包都会被当候选，容易取错字节")
    if battery.get("zero_is_off") is True:
        warns.append("zero_is_off 已开启：确认过「关机后仍在回包且电量为 0」，否则真 0% 会被当成关机")
    if not frames(reply.get("range")):
        warns.append("range 用默认 [0, 100]：0 会被如实显示为 0%，建议按设备表现收紧")

    return {
        "name": name,
        "type": dtype,
        "transport": ttype,
        "frames": len(query) + len(pre),
        "passive_ms": passive,
        "battery_byte": battery.get("byte"),
        "warns": warns,
    }


# --------------------------------------------------------------------------
#  文件级校验 —— 对应 app.load_pack
# --------------------------------------------------------------------------
def load_pack(path):
    """返回 (specs, errors)。单台设备写错不影响其它设备。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return [], ["读不了这个文件：%s" % e]

    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            return [], ["这是 YAML 文件，但本机没装 pyyaml；改用 JSON，或先 pip install pyyaml"]
        try:
            data = yaml.safe_load(text)
        except Exception as e:
            return [], ["YAML 解析失败：%s" % e]
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return [], ["JSON 语法错误（第 %d 行第 %d 列）：%s" % (e.lineno, e.colno, e.msg)]

    if data is None:
        return [], ["文件是空的，里面没有任何设备定义"]

    if isinstance(data, dict):
        if data.get("template") is True:
            return [], ["这是配置模板（template: true），不是可导入的设备；"
                        "请复制一份、把参数改成真实值后再导入"]
        fmt = data.get("format")
        if fmt not in (None, "", PACK_FORMAT):
            return [], ["这不是本程序的协议文件（format = %r，应为 %r）" % (fmt, PACK_FORMAT)]
        ver = data.get("version")
        if ver not in (None, ""):
            try:
                txt = str(ver).strip()
                got = (int(txt, 16) if txt.lower().startswith("0x")
                       else int(txt, 10)) if isinstance(ver, str) else int(ver)
            except (TypeError, ValueError):
                return [], ["version 必须是整数，现在是 %r" % (ver,)]
            if got != PACK_VERSION:
                return [], ["配置版本 %s 不受支持：本程序支持 version %s。"
                            "请升级软件，或把配置改成 version %s 再导入"
                            % (got, PACK_VERSION, PACK_VERSION)]

    items = data.get("devices") if isinstance(data, dict) else None
    if items is None:
        items = data if isinstance(data, list) else [data]
    if not isinstance(items, list):
        return [], ["文件里没有 devices 数组"]

    specs, errors = [], []
    for i, one in enumerate(items, 1):
        try:
            specs.append(normalize_spec(one))
        except ValueError as e:
            errors.append("第 %d 台：%s" % (i, e))
        except Exception as e:      # 兜底：字段类型离谱时也给出可读原因
            errors.append("第 %d 台：%s" % (i, e))
    if not specs and not errors:
        errors.append("文件里没有任何设备定义")
    return specs, errors


def main(argv):
    target = argv[1] if len(argv) > 1 else "configs"
    if not os.path.exists(target):
        print("路径不存在：%s" % target)
        return 2

    if os.path.isfile(target):
        files = [target]
    else:
        files = []
        for base, _dirs, names in os.walk(target):
            for n in names:
                if os.path.splitext(n)[1].lower() in (".json", ".yaml", ".yml"):
                    files.append(os.path.join(base, n))
        files.sort()
    if not files:
        print("%s 里没有 .json / .yaml 配置" % target)
        return 2

    total_ok = total_err = bad_files = 0
    for path in files:
        try:
            rel = os.path.relpath(path)
        except ValueError:          # 配置在别的盘符，相对路径算不出来
            rel = path
        specs, errors = load_pack(path)
        if not specs and errors and "配置模板" in errors[0]:
            print("[跳过] %s（配置模板）" % rel)
            continue
        if specs:
            print("[通过] %s：%d 台设备" % (rel, len(specs)))
            for s in specs:
                print("        · %s（%s · %s）" % (s["name"], s["type"], s["transport"]))
                for w in s["warns"]:
                    print("          ! %s" % w)
            total_ok += len(specs)
        else:
            bad_files += 1
        for msg in errors:
            print("[错误] %s：%s" % (rel, msg))
            total_err += 1

    print("-" * 60)
    print("可用设备 %d 台；错误 %d 处；整份失败的文件 %d 个"
          % (total_ok, total_err, bad_files))
    return 0 if bad_files == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
