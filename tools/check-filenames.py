#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查仓库文件名是否符合命名规范，不需要图形界面。

规范（见 docs/repo-checklist.md 第 7 条）：
  - 文件名 / 目录名一律小写英文 + 数字 + . _ -
  - 禁止中文、空格、大写字母（GitHub 约定的 README.md / LICENSE 例外）
  - 不检查 .git、build、dist 等自动生成目录

用法：
    python tools/check-filenames.py            # 检查整个仓库
    python tools/check-filenames.py configs    # 只检查某个目录

退出码：0 全部合规；1 存在不合规命名。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Git 生态约定俗成、必须保持大写的名字（GitHub 首页 / 许可证 / issue 模板依赖它们）
ALLOW = {"README.md", "LICENSE", "ISSUE_TEMPLATE", "PULL_REQUEST_TEMPLATE.md"}
# 自动生成 / 无需检查的目录
SKIP_DIRS = {".git", "build", "dist", "release", "__pycache__", ".venv", "venv",
             "node_modules", ".idea", ".vscode"}
# 允许的字符：小写字母、数字、点、下划线、连字符（下划线仅兼容既有 Python 模块名）
OK_CHARS = re.compile(r"^[.a-z0-9_-]+$")


def walk(target):
    for base, dirs, files in os.walk(target):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS]
        for name in sorted(files):
            yield os.path.join(base, name), name
        for name in dirs:
            yield os.path.join(base, name), name


def why(name):
    if name in ALLOW:
        return None
    bad = []
    if re.search(r"[A-Z]", name):
        bad.append("有大写字母")
    if re.search(r"[\u4e00-\u9fff]", name):
        bad.append("有中文")
    if " " in name:
        bad.append("有空格")
    if not OK_CHARS.match(name):
        bad.append("含不支持的字符")
    return "、".join(bad) if bad else None


def main(argv):
    target = os.path.abspath(argv[1]) if len(argv) > 1 else ROOT
    if not os.path.isdir(target):
        print(f"目录不存在：{target}")
        return 2

    checked = 0
    bad = []
    for path, name in walk(target):
        # 跳过隐藏目录本身（.git / .github 里的文件仍要检查）
        if name in SKIP_DIRS:
            continue
        checked += 1
        reason = why(name)
        if reason:
            bad.append((os.path.relpath(path, ROOT).replace("\\", "/"), reason))

    if not bad:
        print(f"已检查 {checked} 个文件 / 目录：全部符合命名规范（小写、无中文、无空格）。")
        return 0

    print(f"已检查 {checked} 个，发现 {len(bad)} 处不合规：\n")
    for rel, reason in bad:
        print(f"  [不合规] {rel}  —— {reason}")
    print("\n请改成小写英文 + 连字符，例如：configs/logitech-g304.json")
    print("（例外：README.md、LICENSE 需要保持大写，GitHub 依赖它们识别首页与许可证）")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
