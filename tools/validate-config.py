#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验设备配置，不需要图形界面（提交 PR 前跑一遍）。

用法：
    python tools/validate-config.py              # 校验 configs/ 下全部配置
    python tools/validate-config.py 某个目录       # 校验指定目录
    python tools/validate-config.py 某个文件.json   # 校验单个文件

退出码：全部文件至少有一台可用设备 → 0；有文件全部解析失败 → 1。
模板文件（template: true）会被跳过，不算错误。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


def collect(target):
    if os.path.isfile(target):
        return [target]
    out = []
    for base, _dirs, names in os.walk(target):
        for name in names:
            if os.path.splitext(name)[1].lower() in (".json", ".yaml", ".yml"):
                out.append(os.path.join(base, name))
    return sorted(out)


def main(argv):
    target = argv[1] if len(argv) > 1 else app.CONFIGS_DIR
    if not os.path.exists(target):
        print(f"路径不存在：{target}")
        return 2
    files = collect(target)
    if not files:
        print(f"{target} 里没有 .json / .yaml 配置")
        return 2

    total_ok = total_fail = 0
    bad_files = 0
    for path in files:
        rel = os.path.relpath(path, os.path.dirname(target) or ".")
        specs, errors = app.load_pack(path)
        # 模板被拒绝是设计行为，不算错误
        if not specs and errors and "配置模板" in errors[0]:
            print(f"[跳过] {rel}（配置模板）")
            continue
        if specs:
            print(f"[通过] {rel}：{len(specs)} 台设备")
            for s in specs:
                tr = (s.get("transport") or {}).get("type", "hid")
                print(f"        · {s['name']}（{s['type']} · {tr}）")
            total_ok += len(specs)
        else:
            bad_files += 1
        for msg in errors:
            print(f"[错误] {rel}：{msg}")
            total_fail += 1

    print("-" * 60)
    print(f"可用设备 {total_ok} 台；错误 {total_fail} 处；整份失败的文件 {bad_files} 个")
    return 0 if bad_files == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
