#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按配置文件真读一次电量：不用打开软件，就能验证这份配置到底能不能用。

写配置的时候最怕两件事：校验器说没问题，实际上读不到；或者读到了，但取的字节
是错的，数字看着像模像样。这个脚本把「导入 → 测试读取」那一步搬到命令行，
按配置里的 match / query / reply 真的发帧、真的解析，直接把百分比打出来。

用法：

    # 读一次，看能不能读到
    python read_once.py ../../configs/devices/你的文件.json

    # 连读 3 次，看稳不稳定（顺便看电量会不会跳）
    python read_once.py ../../configs/devices/你的文件.json --times 3

    # 同时把收发的原始帧打出来，方便对字节
    python read_once.py ../../configs/devices/你的文件.json --raw

退出码：0 = 全部设备都读到了；1 = 有设备读不到（会写明卡在哪一步）。

只读：只发配置里写的那几帧，不改任何设备状态，不需要管理员权限。仅 Windows。
串口配置请直接在软件里「测试读取」，本脚本只处理 HID。
"""
import argparse
import ast
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import probe_device as P          # noqa: E402  同目录，共用一套枚举与收发

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 公式求值白名单，和程序内 safe_formula 一致：只允许算术与几个数学函数
_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
          ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
          ast.Pow, ast.USub, ast.UAdd)
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "int": int, "float": float}


# ---------------- 小工具 ----------------
def _hx(text):
    return P.parse_hex(text)


def _fmt(data):
    return " ".join(f"{b:02X}" for b in data) if data else "无"


def _num(v, default=0):
    """配置里的数字可能是 "0x4D" 这种字符串。"""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return int(v)
    t = str(v).strip()
    if not t:
        return default
    return int(t, 16) if t.lower().startswith("0x") else int(t, 16 if len(t) > 4 else 10)


def apply_checksum(frame, rule):
    """发送前按规则算好校验字节并写进去（同程序 _apply_checksum）。"""
    if not rule or str(rule.get("mode") or "none").lower() == "none":
        return frame
    b = bytearray(frame)
    i0 = int(rule.get("from", 0))
    i1 = int(rule.get("to", len(b)))
    at = int(rule.get("at", len(b) - 1))
    if not (0 <= i0 <= i1 <= len(b)) or not (0 <= at < len(b)):
        return bytes(b)
    s = sum(b[i0:i1]) & 0xFF
    b[at] = ((_num(rule.get("seed")) - s) & 0xFF
             if str(rule.get("mode")).lower() == "sum_neg" else s)
    return bytes(b)


def check_checksum(frame, rule):
    """回包校验：算一遍再看帧里那一位对不对（同程序 _check_checksum）。"""
    if not rule or str(rule.get("mode") or "none").lower() == "none":
        return True
    i0 = int(rule.get("from", 0))
    i1 = int(rule.get("to", len(frame)))
    at = int(rule.get("at", len(frame) - 1))
    if not (0 <= i0 <= i1 <= len(frame)) or not (0 <= at < len(frame)):
        return False
    s = sum(frame[i0:i1]) & 0xFF
    want = ((_num(rule.get("seed")) - s) & 0xFF
            if str(rule.get("mode")).lower() == "sum_neg" else s)
    return frame[at] == want


def _formula(expr, x):
    tree = ast.parse(str(expr), mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise ValueError("公式里有不允许的写法")
        if isinstance(node, ast.Name) and node.id not in ("x", "raw") and node.id not in _FUNCS:
            raise ValueError(f"公式里有未知变量：{node.id}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name)
                                              and node.func.id in _FUNCS):
            raise ValueError("公式里只能调用 abs / min / max / round / int / float")
    return float(eval(compile(tree, "<formula>", "eval"),     # noqa: S307 - AST 已白名单
                      {"__builtins__": {}}, dict(_FUNCS, x=x, raw=x)))


def battery_percent(body, bt):
    """把回包字节换算成百分比（同程序 battery_percent，顺序完全一致）。"""
    idx = int(bt.get("byte", 3))
    n = max(1, min(4, int(bt.get("bytes") or 1)))
    if idx < 0 or idx + n > len(body):
        return None
    order = "little" if str(bt.get("order") or "big").lower().startswith("l") else "big"
    raw = int.from_bytes(bytes(body[idx:idx + n]), order)
    mask = bt.get("mask")
    if mask not in (None, ""):
        mv = _num(mask)
        if mv:
            raw &= mv
    shift = int(bt.get("shift") or 0)
    if shift > 0:
        raw >>= shift
    elif shift < 0:
        raw <<= -shift
    mode = str(bt.get("mode") or "raw").lower()
    if mode == "hi":
        raw = (raw >> 4) & 0x0F
    elif mode == "lo":
        raw = raw & 0x0F
    try:
        if bt.get("formula"):
            val = _formula(bt["formula"], raw)
        elif bt.get("map"):
            pts = bt["map"]
            pts = ([(float(k), float(v)) for k, v in pts.items()] if isinstance(pts, dict)
                   else [(float(a), float(b)) for a, b in pts])
            pts.sort()
            if raw <= pts[0][0]:
                val = pts[0][1]
            elif raw >= pts[-1][0]:
                val = pts[-1][1]
            else:
                val = next(y0 + (y1 - y0) * (raw - x0) / (x1 - x0)
                           for (x0, y0), (x1, y1) in zip(pts, pts[1:]) if x0 <= raw <= x1)
        elif str(bt.get("read_as") or "percent").lower() == "voltage":
            vs = float(bt.get("voltage_scale") or 0.01)
            vmin = float(bt.get("voltage_min") or 0)
            vmax = float(bt.get("voltage_max") or 100)
            if vmax == vmin:
                return None
            val = (raw * vs - vmin) / (vmax - vmin) * 100.0
        else:
            val = raw * float(bt.get("scale") or 1) + float(bt.get("offset") or 0)
    except (ValueError, TypeError, ZeroDivisionError, KeyError, StopIteration):
        return None
    return int(round(val))


# ---------------- 匹配接口 ----------------
def match_hids(spec, devs):
    """同程序 spec_hits：match 里给了什么就卡什么，能写的接口排前面。"""
    m = spec.get("match") or {}
    vid, pid = m.get("vid"), m.get("pid")
    up = m.get("usage_page")
    min_in = int(m.get("min_in") or 0)
    min_out = int(m.get("min_out") or 0)
    kws = [str(k).upper() for k in (m.get("product_contains") or [])]
    hits = []
    for d in devs:
        if vid is not None and d["vid"] != _num(vid):
            continue
        if pid is not None and d["pid"] != _num(pid):
            continue
        if up is not None and d["usage_page"] != _num(up):
            continue
        if min_in and (d.get("in_len") or 0) < min_in:
            continue
        if min_out and (d.get("out_len") or 0) < min_out:
            continue
        if kws and not any(k in (d.get("product") or "").upper() for k in kws):
            continue
        hits.append(d)
    hits.sort(key=lambda d: (d.get("out_len") or 0) <= 0, reverse=False)
    return hits


# ---------------- 读一次 ----------------
def _send(h, frame, out_len, pad_to=0):
    """先走 SetOutputReport（厂商接口基本都认），不成就走 WriteFile。"""
    size = max(len(frame), out_len or 0, pad_to or 0, 8)
    data = bytes(frame).ljust(size, b"\x00")
    ok = P._send(h, data, out_len)
    if ok:
        return True, "SetOutputReport"
    return False, "SetOutputReport 被拒"


def _send_write(h, frame, out_len, pad_to=0):
    import ctypes
    size = max(len(frame), out_len or 0, pad_to or 0, 8)
    buf = ctypes.create_string_buffer(bytes(frame).ljust(size, b"\x00"), size)
    written = ctypes.c_ulong(0)
    ok = P._K.WriteFile(h, buf, size, ctypes.byref(written), None)
    return bool(ok), ("WriteFile" if ok else "WriteFile 被拒")


def read_once(spec, dev, raw=False):
    """按 spec 读一次。返回 dict：ok / pct / charging / 过程信息 / 失败原因。"""
    rd = spec.get("read") or {}
    reply = rd.get("reply") or {}
    contains = reply.get("contains") or []
    if isinstance(contains, str):
        contains = [contains]
    patterns = [_hx(c) for c in contains]
    rng = reply.get("range") or [0, 100]
    lo, hi = int(rng[0]), int(rng[1])
    pad_to = int(rd.get("pad_to") or 0)
    in_len = dev.get("in_len") or 64
    out_len = dev.get("out_len") or 0
    listen = int(rd.get("listen_ms") or 600)
    attempts = max(1, int(rd.get("attempts") or 1))
    trace = []
    seen_note = {"no_reply": False, "no_anchor": False, "bad_checksum": False, "out_range": False}

    def parse(buf):
        if not buf:
            return None
        buf = bytes(buf)
        base = next((buf.find(p) for p in patterns if p and buf.find(p) >= 0), -1) \
            if patterns else 0
        if base < 0:
            seen_note["no_anchor"] = True
            return None
        body = buf[base:]
        if reply.get("checksum") and not check_checksum(body, reply["checksum"]):
            seen_note["bad_checksum"] = True
            return None
        pct = battery_percent(body, reply.get("battery") or {})
        if pct is None or not (lo <= pct <= hi):
            seen_note["out_range"] = True
            return None
        charging = False
        ck = reply.get("charging") or {}
        if ck:
            ci = int(ck.get("byte", -1))
            if 0 <= ci < len(body):
                charging = (body[ci] & _num(ck.get("mask"), 0xFF)) != 0
        return pct, charging

    def collect(deadline):
        """在 deadline 前一直收，收到能解析的就返回。"""
        while time.time() < deadline:
            got = P._read_overlapped(h, in_len, max(50, min(250, int(
                (deadline - time.time()) * 1000) or 50)))
            if got and any(b != 0 for b in got):
                trace.append(("收", bytes(got)))
                r = parse(got)
                if r:
                    return r
            else:
                seen_note["no_reply"] = True
                time.sleep(0.004)
        return None

    h = P._open_rw(dev["path"])
    if h is None:
        return {"ok": False, "why": "打不开句柄（被别的程序独占？先关掉厂商驱动或本软件）"}
    try:
        # 0) 只收不发
        passive = int(rd.get("passive_ms") or 0)
        if passive > 0:
            r = collect(time.time() + passive / 1000.0)
            if r:
                return {"ok": True, "pct": r[0], "charging": r[1], "trace": trace}
        # 1) 握手
        for pre in rd.get("pre") or []:
            frame = apply_checksum(_hx(pre), rd.get("checksum"))
            ok, how = _send(h, frame, out_len, pad_to)
            if not ok:
                ok, how = _send_write(h, frame, out_len, pad_to)
            trace.append(("发", frame))
            trace.append(("通道", how.encode()))
        if rd.get("pre") and rd.get("pre_delay_ms"):
            time.sleep(int(rd["pre_delay_ms"]) / 1000.0)
        # 2) 查询
        for _ in range(attempts):
            for q in rd.get("query") or []:
                frame = apply_checksum(_hx(q), rd.get("checksum"))
                sent_ok, how = _send(h, frame, out_len, pad_to)
                if not sent_ok:
                    sent_ok, how = _send_write(h, frame, out_len, pad_to)
                trace.append(("发", frame))
                trace.append(("通道", how.encode()))
                if not sent_ok:
                    continue
                r = collect(time.time() + listen / 1000.0)
                if r:
                    return {"ok": True, "pct": r[0], "charging": r[1], "trace": trace}
            # 换通道再来一遍
            for q in rd.get("query") or []:
                frame = apply_checksum(_hx(q), rd.get("checksum"))
                sent_ok, how = _send_write(h, frame, out_len, pad_to)
                trace.append(("发", frame))
                trace.append(("通道", (how + "（重试）").encode()))
                if not sent_ok:
                    continue
                r = collect(time.time() + listen / 1000.0)
                if r:
                    return {"ok": True, "pct": r[0], "charging": r[1], "trace": trace}
    finally:
        P._K.CloseHandle(h)

    if not (rd.get("query") or rd.get("passive_ms")):
        why = "配置里既没有 query 也没有 passive_ms，等于什么都没做"
    elif seen_note["no_reply"]:
        why = ("发帧有去无回：接收器没插好 / 设备关机或休眠 / 这台设备要先唤醒"
               "（试 read.pre）/ 查询帧不对")
    elif seen_note["no_anchor"]:
        why = "收到回包，但里面没有配置的 contains 锚点，锚点可能写错了"
    elif seen_note["bad_checksum"]:
        why = "收到回包但回包校验对不上，reply.checksum 的区间/种子写错了"
    elif seen_note["out_range"]:
        why = "回包解析出来了但数值超出 range 范围，多半是 battery.byte 取错了字节"
    else:
        why = "没读到（原因不明，加 --raw 看原始帧）"
    return {"ok": False, "why": why, "trace": trace}


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description="按配置真读一次电量，验证配置能不能用（仅 Windows）")
    ap.add_argument("config", help="配置文件路径，如 configs/devices/你的设备.json")
    ap.add_argument("--times", type=int, default=1, help="连读几次，默认 1")
    ap.add_argument("--raw", action="store_true", help="打印收发的原始帧")
    ap.add_argument("--index", type=int, default=0, help="文件里有多台设备时只读第几台（从 1 起）")
    args = ap.parse_args()

    if not P.IS_WIN:
        print("这个脚本只能在 Windows 上跑（要调 hid.dll / setupapi.dll）。")
        return 2
    if not os.path.isfile(args.config):
        print(f"找不到配置文件：{args.config}")
        return 2
    try:
        with open(args.config, encoding="utf-8") as f:
            pack = json.load(f)
    except Exception as e:
        print(f"配置读不了（不是合法 JSON？）：{e}")
        return 2

    devs_spec = pack.get("devices") if isinstance(pack, dict) else None
    if not devs_spec:
        print("这个文件里没有 devices 数组。")
        return 2
    if args.index:
        if not (1 <= args.index <= len(devs_spec)):
            print(f"--index 超范围，文件里只有 {len(devs_spec)} 台设备。")
            return 2
        devs_spec = [devs_spec[args.index - 1]]

    hid_devs = P.enumerate_hid()
    print(f"配置：{os.path.basename(args.config)}（{len(devs_spec)} 台设备）")
    print(f"本机 HID 接口：{len(hid_devs)} 个\n")

    ok_cnt = 0
    for i, spec in enumerate(devs_spec, 1):
        name = spec.get("name") or f"第 {i} 台（没有 name）"
        print(f"[{i}/{len(devs_spec)}] {name}")
        transport = str((spec.get("transport") or {}).get("type") or "hid").lower()
        if transport == "serial":
            print("  串口配置：本脚本不处理，请到软件里「＋ 导入设备」→「测试读取」。\n")
            continue
        hits = match_hids(spec, hid_devs)
        if not hits:
            print("  ✗ 没匹配到接口。match 写宽一点先试：只留 vid（+ pid），"
                  "去掉 usage_page / min_in / min_out 再读一次。")
            print("  看本机都有什么：python probe_device.py --vendor\n")
            continue
        dev = hits[0]
        print(f"  接口：0x{dev['vid']:04X}:0x{dev['pid']:04X} usagePage=0x{dev['usage_page']:04X} "
              f"in={dev['in_len']} out={dev['out_len']} | {dev['product'] or '(无产品名)'}")
        if len(hits) > 1:
            print(f"  （另有 {len(hits) - 1} 个同条件接口，用第一个）")

        results = []
        for n in range(max(1, args.times)):
            r = read_once(spec, dev, args.raw)
            results.append(r)
            if args.raw:
                for kind, data in r.get("trace", []):
                    if kind == "通道":
                        print(f"    通道：{data.decode('utf-8', 'replace')}")
                    else:
                        print(f"    {kind}：{_fmt(data)}")
            if r["ok"]:
                charge = "充电中" if r.get("charging") else "未充电"
                print(f"  ✓ 第 {n + 1} 次读到：{r['pct']}%（{charge}）")
            else:
                print(f"  ✗ 第 {n + 1} 次没读到：{r['why']}")
        good = [r for r in results if r["ok"]]
        if len(good) == len(results):
            pcts = [r["pct"] for r in good]
            spread = max(pcts) - min(pcts)
            if spread > 3:
                print(f"  注意：{len(pcts)} 次读数在 {min(pcts)}~{max(pcts)}% 之间跳动，"
                      "可能是取错字节或设备在充电时上报不同口径。")
            ok_cnt += 1
        elif good:
            print(f"  {len(good)}/{len(results)} 次读到，不稳定：先看是不是设备会休眠，"
                  "把 listen_ms 调大、attempts 调成 2 再试。")
        print()

    print(f"结果：{ok_cnt}/{len(devs_spec)} 台读到了电量。")
    if ok_cnt:
        print("下一步：打开软件「＋ 导入设备」→ 勾选 →「测试读取」，再跟厂商软件的数字对一下。")
    else:
        print("都读不到时，按上面每台的失败原因改一处再读，别一次改三个字段。")
    return 0 if ok_cnt == len(devs_spec) else 1


if __name__ == "__main__":
    sys.exit(main())
