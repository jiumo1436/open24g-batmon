#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从十六进制回包里推断电量字段、锚点与校验方式。

这是「配置怎么写」里最费劲的一步：人眼在一堆字节里找哪个是电量，靠猜。
把几帧不同电量下的回包丢进来，脚本会给出可验证的候选值。

用法：
    # 直接给帧（建议带电量，能给推断质量带来数量级提升）
    python analyze_frames.py \
        --frame "06 A5 5A 01 00 00 00 00 00 00 57 00 00 9C" --pct 87 \
        --frame "06 A5 5A 01 00 00 00 00 00 00 3F 00 00 84" --pct 63

    # 从文件读：每行一帧，可选前缀 "87:" 或 "87%" 标电量，'#' 开头为注释
    python analyze_frames.py --file frames.txt

    # 只看接口/锚点/校验，不关心电量
    python analyze_frames.py --frame "..." --frame "..."

输入只接受干净的十六进制（空格 / 逗号 / 0x / 冒号分隔均可）。
Wireshark 的 "Hex Dump" 带左侧地址列，只取中间的十六进制部分再粘进来。

输出是中文分析报告 + 一段可直接采用（VID/PID 仍需填）的 JSON 片段。
"""
import argparse
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def parse_hex(text):
    """把 "06 A5 5A" / "06A55A" / "0x06,0xA5" 解析成 bytes；不合法抛 ValueError。"""
    raw = str(text).strip()
    for junk in ("0x", "0X"):
        raw = raw.replace(junk, " ")
    for sep in (",", ":", "-", "\t", "\n", "\r", "|"):
        raw = raw.replace(sep, " ")
    parts = [p for p in raw.split(" ") if p]
    out = bytearray()
    for p in parts:
        if len(p) % 2 or any(c not in "0123456789abcdefABCDEF" for c in p):
            raise ValueError("这一段不是十六进制：%r（把 Wireshark 的地址列和 ASCII 列去掉）" % p)
        out.extend(bytes.fromhex(p))
    return bytes(out)


def hexs(b):
    return " ".join("%02X" % x for x in b)


def common_prefix(frames):
    n = min(len(f) for f in frames)
    i = 0
    while i < n and len({f[i] for f in frames}) == 1:
        i += 1
    return i


def anchor_suggestion(frames):
    """给出两种锚点候选：(从首字节起, 跳过首字节)，以及共同前缀长度。

    首字节可能是 HID 报告 ID（VXE 那样 0x08 / 0xA5 前面的 0x06），也可能本身就是帧头
    （VXE R1 SE+ 的回包就是 0x04 打头）。两种都列出来，由 query 的结构判断。
    """
    k = common_prefix(frames)
    if k < 2:
        return None, None, k
    head = frames[0][:k]
    at_zero = head[:2]
    at_one = head[1:3] if k >= 3 else None
    return at_zero, at_one, k


def detect_checksum(frames):
    """检测末字节的 sum_tail / sum_neg 校验，返回可用的 (mode, frm, to, at, seed)。"""
    seeds = (0x00, 0x4D, 0xFF, 0x55, 0x5A)
    hits = []
    lens = {len(f) for f in frames}
    if len(lens) != 1:
        return []
    n = lens.pop()
    if n < 3:
        return []
    at = n - 1
    for frm in range(0, n - 1):
        for to in range(frm + 1, n):
            tail_ok = all((sum(f[frm:to]) & 0xFF) == f[at] for f in frames)
            if tail_ok:
                hits.append(("sum_tail", frm, to, at, None))
            for seed in seeds:
                neg_ok = all(((seed - sum(f[frm:to])) & 0xFF) == f[at] for f in frames)
                if neg_ok:
                    hits.append(("sum_neg", frm, to, at, seed))
    # 优先「算到校验位前一位」的区间（最常见），其次从 0/1 起算，最后取更长的区间
    hits.sort(key=lambda h: (0 if h[2] == h[3] else 1,
                             0 if h[1] in (0, 1) else 1,
                             -(h[2] - h[1])))
    return hits


def read_raw(frame, off, width, order):
    if off + width > len(frame):
        return None
    chunk = frame[off:off + width]
    return int.from_bytes(chunk, "big" if order == "big" else "little")


def battery_candidates(frames, pcts):
    """在「各帧取值不同」的偏移上找电量字段，按线性吻合度排序。"""
    lens = {len(f) for f in frames}
    if len(lens) != 1:
        return []
    n = lens.pop()
    cands = []
    for off in range(n):
        for width in (1, 2):
            if off + width > n:
                continue
            for order in (("big",) if width == 1 else ("big", "little")):
                raws = [read_raw(f, off, width, order) for f in frames]
                if any(r is None for r in raws) or len(set(raws)) == 1:
                    continue
                if pcts:
                    ratios = [p / r for p, r in zip(pcts, raws) if r > 0]
                    if len(ratios) < 2:
                        continue
                    lo, hi = min(ratios), max(ratios)
                    spread = (hi - lo) / (sum(ratios) / len(ratios)) if sum(ratios) else 9
                    err = 0.0
                    for p, r in zip(pcts, raws):
                        err += abs(r * (sum(ratios) / len(ratios)) - p)
                    cands.append({"off": off, "width": width, "order": order,
                                  "raws": raws, "k": sum(ratios) / len(ratios),
                                  "spread": spread, "err": err / len(pcts)})
                else:
                    cands.append({"off": off, "width": width, "order": order,
                                  "raws": raws, "k": None, "spread": 9, "err": 0})
    if pcts:
        cands.sort(key=lambda c: (c["spread"] > 0.05, c["width"], c["err"], c["off"]))
    else:
        cands.sort(key=lambda c: (c["width"], c["off"]))
    # 同一偏移上 1 字节与 2 字节往往都成立，只留信息量最大的那个
    seen, out = set(), []
    for c in cands:
        if c["off"] in seen:
            continue
        seen.add(c["off"])
        out.append(c)
    return out[:3]


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True, description="从回包推断电量字段/锚点/校验")
    ap.add_argument("--frame", action="append", default=[], help="一帧十六进制，可重复")
    ap.add_argument("--pct", action="append", type=float, default=[],
                    help="与 --frame 一一对应的厂商软件显示电量，可重复")
    ap.add_argument("--file", help="帧文件，每行一帧，可写 '87: 06 A5 ...'")
    args = ap.parse_args(argv)

    items = []
    for i, f in enumerate(args.frame):
        items.append((parse_hex(f), args.pct[i] if i < len(args.pct) else None))

    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                pct = None
                if ":" in s and "%" not in s.split(":")[0] and s.split(":")[0].strip().isdigit():
                    head, tail = s.split(":", 1)
                    pct = float(head.strip())
                    s = tail
                elif s.split("%")[0].strip().isdigit() and "%" in s:
                    pct = float(s.split("%")[0].strip())
                    s = s.split("%", 1)[1]
                items.append((parse_hex(s), pct))

    if not items:
        print("没有可分析的帧。用 --frame 或 --file 给几帧回包。")
        return 2

    frames = [b for b, _ in items]
    pcts = [p for _, p in items]
    have_pct = all(p is not None for p in pcts)

    print("=" * 62)
    print("共 %d 帧：%s" % (len(frames), " / ".join(str(len(f)) for f in frames)))
    for idx, (b, p) in enumerate(items):
        tag = ("  (电量 %g%%)" % p) if p is not None else ""
        print("  #%d %s%s" % (idx + 1, hexs(b), tag))

    # 1) 长度 / 报告 ID
    print("\n[1] 长度与报告 ID")
    if len({len(f) for f in frames}) == 1:
        print("    所有帧等长：%d 字节 —— match.min_in / min_out 与 pad_to 都按这个填。" % len(frames[0]))
    else:
        print("    长度不一致（%s）：可能是不同种类的报文，先确认抓的是同一组查询/回包。"
              % " / ".join(str(len(f)) for f in frames))
    first = {f[0] for f in frames}
    if len(first) == 1:
        print("    首字节恒为 0x%02X：可能是 HID 报告 ID，也可能本身就是帧头。" % frames[0][0])
        print("    判断方法：看 query 帧是不是也以同一个字节开头 —— 是则多半是报告 ID，配置里必须写进去")
        print("    （程序按配置原样发送，不会自动补报告 ID）。")

    # 2) 锚点
    print("\n[2] 锚点 reply.contains")
    at_zero, at_one, plen = anchor_suggestion(frames)
    if at_zero:
        print("    共同前缀 %d 字节：%s" % (plen, hexs(frames[0][:plen])))
        print('    A) contains = ["%s"]  → 锚点从偏移 0 起' % hexs(at_zero))
        if at_one:
            print('    B) contains = ["%s"]  → 锚点从偏移 1 起（把首字节当报告 ID）' % hexs(at_one))
        print("    怎么选：看 query 帧第一个字节是不是同一个值 —— 是的话它更可能是报告 ID（选 B），")
        print("    否则首字节就是帧头（选 A）。锚点越短越容易误匹配，2 字节起步比较稳。")
    else:
        print("    没有稳定的共同前缀 —— 这几帧可能不是同一种报文，或抓的是不同阶段。")

    # 3) 电量
    print("\n[3] 电量字段候选")
    if not have_pct:
        print("    没给电量对照，只能按「取值范围像百分比」粗略排：")
        cands = battery_candidates(frames, None)
        if not cands:
            print("    所有帧完全相同 —— 无法定位电量字节。请在两个不同电量下各抓一帧。")
        for c in cands:
            print("    偏移 %-3d %d 字节 %-6s 取值 %s"
                  % (c["off"], c["width"], c["order"], " / ".join(str(r) for r in c["raws"])))
        print("    拿到电量对照后再跑一次，能直接算出换算系数。")
    else:
        cands = battery_candidates(frames, pcts)
        if not cands:
            print("    所有帧完全相同 —— 无法定位电量字节。请在两个电量明显不同时各抓一帧。")
        best = None
        for c in cands:
            ok = c["spread"] <= 0.05
            line = "    偏移 %-3d %d 字节 %-6s 取值 %s" % (
                c["off"], c["width"], c["order"], " / ".join(str(r) for r in c["raws"]))
            print(line)
            if c["k"] is not None:
                if abs(c["k"] - 1) < 0.02:
                    print("            原始值 ≈ 百分比，直接取用；平均误差 %.1f%%" % c["err"])
                else:
                    print("            pct ≈ raw × %.4g（平均误差 %.1f%%）%s"
                          % (c["k"], c["err"], "" if ok else "  ← 比例不一致，多半不是电量"))
            if ok and best is None:
                best = c
        if best:
            print("\n    最可信的一处：偏移 %d，取值 %s"
                  % (best["off"], "/".join(str(r) for r in best["raws"])))
            # 锚点两种起点各给一个 byte 下标，避免「锚点选错、下标全错」
            for label, base in (("A 锚点从 0 起", 0), ("B 锚点从 1 起", 1)):
                rel = best["off"] - base
                if rel < 0:
                    continue
                if best["width"] == 2:
                    snippet = ('"battery": { "byte": %d, "bytes": 2, "order": "%s"'
                               % (rel, best["order"]))
                    if abs(best["k"] - 1) >= 0.02:
                        snippet += ', "scale": %.4g' % best["k"]
                    snippet += " }"
                elif abs(best["k"] - 1) < 0.02:
                    snippet = '"battery": { "byte": %d }' % rel
                else:
                    snippet = '"battery": { "byte": %d, "scale": %.4g }' % (rel, best["k"])
                print("    %s → %s" % (label, snippet))
            print("    两个都对不上时，先按 A 试读，读出来再对照厂商软件的数字微调。")
        else:
            print("    没有可用候选：先在两个电量明显不同时各抓一帧再分析。")

    # 4) 校验
    print("\n[4] 校验")
    hits = detect_checksum(frames)
    if hits:
        for i, (mode, frm, to, at, seed) in enumerate(hits[:3]):
            tag = "推荐" if i == 0 else "也可成立"
            if seed is None:
                print("    [%s] 末字节 = sum[%d:%d] & 0xFF" % (tag, frm, to))
                print('            "checksum": { "mode": "sum_tail", "from": %d, "to": %d, "at": %d }'
                      % (frm, to, at))
            else:
                print("    [%s] 末字节 = (0x%02X - sum[%d:%d]) & 0xFF" % (tag, seed, frm, to))
                print('            "checksum": { "mode": "sum_neg", "from": %d, "to": %d, "at": %d, "seed": "0x%02X" }'
                      % (frm, to, at, seed))
        if len(hits) > 1:
            print("    多个区间都能算出同一个末字节（零字节多时常见的巧合）。")
            print("    优先用「算到校验位前一位」的那条；仍不确定就先用 none 测读，读得通就不必配校验。")
    else:
        print("    未命中 sum_tail / sum_neg（也试了 seed 0x00/0x4D/0xFF/0x55/0x5A）。")
        print('    先写 "mode": "none" 试试 —— 很多接收器并不校验回包。')

    print("\n[5] 下一步")
    print("    把上面的结论填进配置，先跑 validate_config.py，再用界面「测试读取选中」验证。")
    print("    单帧或没有电量对照时，第 3 节只是猜测，必须以实测读数为准。")
    return 0


def anchor_offset(frames, anchor):
    """锚点在帧里的起始下标（找不到就退回 0）。"""
    for f in frames:
        i = f.find(anchor)
        if i >= 0:
            return i
    return 0


if __name__ == "__main__":
    sys.exit(main())
