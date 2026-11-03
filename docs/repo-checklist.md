# 仓库自检核对清单

用于确认本仓库符合两条底线：**外部配置驱动**、**社区共建**。
每次发版前、以及每次合并配置 PR 前跑一遍。全部命令在仓库根目录执行（Windows Git Bash / PowerShell 均可）。

---

## 一、七项核心检查

### 1. 源码中不存在硬编码的 2.4G 设备协议参数；UI / 设置类功能代码完整

**检查什么**：`app.py` 里不能出现任何具体设备的 VID / PID / 帧头 / 换算公式 / 专用读取函数；
托盘、开机自启、低电量弹窗、电量配色、`user_settings.json` 读写这些**软件本体功能必须保留**。

```bash
# ① 内置设备表、专用读取器必须为空
grep -nE "^\s*(BUILTIN_DEVICES|READERS|BUILTIN_READ_SPECS|UNKNOWN_VIDS)\s*=" app.py
#   期望：BUILTIN_DEVICES = []   READERS = {}   BUILTIN_READ_SPECS = {}   UNKNOWN_VIDS = set()

# ② 不允许出现 VID / PID 字面量（0x + 4 位十六进制的字符串）；注释里的写法示例不算
grep -nE '"(0x)?[0-9A-Fa-f]{4}"' app.py | grep -vE "^[0-9]+:\s*#" || echo "无设备 ID 字面量"

# ③ 不允许出现示例设备用过的帧头（同上，排除注释）
grep -nE "A5 5A|7E 01|55 65|AA BB" app.py | grep -vE "^[0-9]+:\s*#" || echo "无帧头字面量"

# ④ 不允许残留专用读取函数
grep -nE "read_fixed17|read_session20|read_private64|PRIV_DEV" app.py || echo "无专用读取器"

# ⑤ 不允许出现真实设备名 / 品牌名
grep -niE "logitech|razer|vxe|atk|aula|xiberia|狼蛛|西伯利亚" app.py || echo "无设备名残留"

# ⑥ 软件本体功能必须还在（这些关键字都应命中）
for k in TrayController sync_autostart USER_SETTINGS_PATH _check_low_battery SettingsDialog RefreshWorker CaptureDialog; do
  grep -q "$k" app.py && echo "OK  $k" || echo "缺失 $k"
done
```

**判定**：①~⑤ 全部无输出（或输出 `无…`）；⑥ 全部 `OK`。

> 唯一允许出现的十六进制常量是协议**无关**的系统常量（Win32 句柄标志、HID 路径前缀、
> 位运算掩码等）；`app.py` 顶部的协议字段注释里也允许出现 `0x1234` 之类的**格式示例**。
> 审阅时逐条确认用途即可。

---

### 2. `configs/` 下 3 套配置为模拟样例，无私人真实调试配置

**检查什么**：示例必须是虚构值，不能带真实设备的 VID / PID / 帧头 / 校验种子 / 设备名；
目录里不能有抓包日志、临时脚本、私人备注。

```bash
# ① 目录里只应有模板 + 3 套示例
ls configs/

# ② 示例的 VID 必须统一为明显的虚构值（本仓库用 0x1234）
grep -n '"vid"' configs/example-*.json

# ③ 不允许出现真实品牌名
grep -niE "logitech|razer|vxe|atk|aula|xiberia|狼蛛|西伯利亚|罗技|雷蛇" configs/*.json \
  || echo "无真实品牌名"

# ④ 不允许抓包 / 日志 / 临时文件
find configs -type f ! -name "*.json" ! -name "*.yaml" ! -name "*.yml" ! -name "README.md"
```

**判定**：`configs/` 恰好是 `README.md`、`template.json`、`serial-template.json`、
`example-mouse.json`、`example-keyboard.json`、`example-headphone.json`；VID 全为 `0x1234`；
③④ 无输出。

---

### 3. 贡献指南写明 PR 必须附抓包与实测素材，并声明维护者无法完成全部硬件实测

```bash
grep -n "抓包" docs/contribute-guide.md | head
grep -n "实测" docs/contribute-guide.md | head
grep -n "不做实物硬件验证\|不可能拥有全部硬件\|无法持有全部硬件" docs/contribute-guide.md
grep -n "不予合并\|不会被合并" docs/contribute-guide.md
```

**判定**：四类表述都能命中：①必须提供抓包日志；②贡献者须持有硬件并实测；
③维护者不做实物验证 / 无法拥有全部硬件；④缺证据不予合并。

---

### 4. README 含 AI 辅助开发说明、非专业维护提示、完整免责声明

```bash
grep -n "AI 辅助开发\|DeepSeek" README.md
grep -n "并非软件开发专业\|非专业" README.md
grep -n "免责声明" README.md
grep -n "不用于破解\|不用于商用\|商业用途" README.md
grep -n "仅供个人硬件学习研究" README.md
```

**判定**：五项全部命中，且「项目说明」「免责声明」是两个独立小节。

---

### 5. `.gitignore` 已包含 `user_settings.json`，私有偏好不会被提交

```bash
grep -n "user_settings.json" .gitignore
git check-ignore -v user_settings.json devices.json cache.json error.log 协议库/
git ls-files | grep -E "user_settings|devices\.json|cache\.json|error\.log|\.pcap" || echo "干净"
```

**判定**：`grep` 命中，`check-ignore` 四条规则全部列出，`git ls-files` 无匹配。

---

### 6. 目录结构完整：`configs/`、`docs/`、`assets/` 齐全，`template.json` 存在

```bash
find . -maxdepth 2 -not -path "./.git*" -not -path "./dist*" -not -path "./build*" | sort
test -f configs/template.json && echo "模板存在"
test -d assets && test -d docs && test -d configs && echo "三目录齐全"
```

**判定**：与 [README 目录结构](../README.md#目录结构) 一致；`configs/template.json` 与
`configs/serial-template.json` 均存在。

---

### 7. 所有文件名均为小写横线命名，无中文文件名

```bash
python tools/check-filenames.py
```

**判定**：输出「全部符合命名规范」。允许的例外只有 Git 生态强制要求的三个名字：
`README.md`、`LICENSE`、`.github/ISSUE_TEMPLATE`（GitHub 首页、许可证识别、issue 表单依赖它们的大写形式）。

---

## 二、进阶检查（发版前）

| 项 | 命令 | 判定 |
| --- | --- | --- |
| 配置全部可解析 | `python tools/validate-config.py` | 无 `[错误]`，模板显示 `[跳过]` |
| 源码可编译 | `python -m py_compile app.py` | 无输出 |
| 无控制台残留 | `grep -n "print(" app.py` | 仅允许出现在注释 / 日志函数中 |
| 无隐私路径 | `grep -niE "C:\\\\Users|桌面|D:\\\\|G:\\\\" app.py` | 无输出 |
| 首次启动为空 | 清空 `devices.json` 后启动 | 显示 0 台设备、不报错 |
| 损坏配置不崩溃 | 导入一份坏 JSON | 中文提示、程序继续运行 |
| 托盘与自启 | 见 [`verify.md`](verify.md) 第 6 节 | 三环图标、自启生效 |
| 打包产物不入库 | `git status --ignored --short \| grep dist` | 显示为 ignored |

---

## 三、当前状态（2026-11-03 自检）

| # | 检查项 | 结果 |
| --- | --- | --- |
| 1 | 源码无硬编码协议，UI / 设置功能完整 | ✅ 内置设备表为空、无 VID/帧头字面量、托盘与设置类齐全 |
| 2 | `configs/` 三套示例为虚构样例 | ✅ 统一使用 `0x1234`，无真实品牌与抓包文件 |
| 3 | 贡献指南含 PR 硬性条件与免责范围 | ✅ 三项材料 + 「不做实物验证」+「不予合并」均已写明 |
| 4 | README 含 AI 说明 / 非专业提示 / 免责声明 | ✅ 三个小节独立成段 |
| 5 | `.gitignore` 覆盖 `user_settings.json` 等私有文件 | ✅ 逐条 `git check-ignore` 验证通过 |
| 6 | 目录结构完整 | ✅ `configs/` `docs/` `assets/` `tools/` `.github/` 齐全 |
| 7 | 文件名规范 | ✅ `check-filenames.py` 通过 |

---

## 四、合并配置 PR 前的快速门禁

```bash
git fetch origin pull/<PR号>/head:pr-<PR号> && git checkout pr-<PR号>
python tools/validate-config.py configs/
python tools/check-filenames.py
git diff --stat origin/main...HEAD        # 只应新增 configs/ 下的文件
```

再人工确认 PR 里**确实附带**了抓包日志与实测截图（附件形式），缺一不合并。
