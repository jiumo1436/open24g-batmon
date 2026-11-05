# v0.2.5 · AI 技能包直接放进发布包

这是一个**打包与文档更新版本**。程序本体（exe）与 0.2.3 / 0.2.4 完全相同，
**没有重新编译**，设备配置也没有任何改动。

**这次只做了一件事**：把 `skills/24g-device-config` 一并打进下载包。以前它只在仓库里，
想用的人得先找到仓库、再单独下载、再研究该放哪；现在解压就能看到，装法写在
`quick-start.txt` 里，照抄路径即可。

**发布日期**：2026-11-05
**适用系统**：Windows 10 / 11（64 位）
**配置文件版本**：`version: 1`（与 0.2.4 完全兼容，**已有配置不用改**）

---

### 下载

| 文件 | 说明 |
| --- | --- |
| `open24g-batmon-0.2.5-win64.zip` | 免安装，解压即用（exe + `_internal/` + `configs/` + `skills/`） |
| `open24g-batmon-0.2.5-win64.zip.sha256` | 校验和 |

解压后目录结构：

```
open24g-batmon-0.2.5-win64/
├── 设备电量监控.exe     # 双击这个
├── _internal/           # 运行时（Python + Qt），必须与 exe 同级
├── configs/
│   ├── devices/         # 实机配置 4 份，导入即用
│   └── templates/       # 模板与语法示例，自己加设备时照着改
├── skills/              # 新增：AI 技能包，用不上可以删
│   ├── README.md
│   └── 24g-device-config/
├── quick-start.txt      # 使用说明
└── LICENSE
```

---

### 怎么用这个技能包

把 `skills/24g-device-config/` **整个文件夹**复制到 AI 客户端的技能目录，重启客户端：

| 客户端 | 放哪里 |
| --- | --- |
| WorkBuddy | `C:\Users\你的用户名\.workbuddy\skills\`（不存在就自己建） |
| Claude Code | 项目的 `.claude\skills\` 下 |
| 其他支持 `SKILL.md` 的 agent | 它约定的 skills 目录 |

装好后不用手动喊，直接说「我有个 XX 设备，能加上吗」，它会：

1. 带你去设备管理器查 VID / PID、确认是 HID 还是串口
2. 拿抓到的十六进制帧分析出报文长度、锚点、电量字节、校验算法
3. 生成 `configs/devices/*.json` 并跑校验
4. 中途缺证据会停下来问，**不会瞎编参数**

**不装 AI 也能用**。`skills/24g-device-config/scripts/` 里两个脚本独立可跑，
只依赖 Python 3，不需要装 PySide6：

```bash
# 从两帧不同电量的回包推断电量字节与校验方式
python skills/24g-device-config/scripts/analyze_frames.py \
  --frame "08 04 00 00 00 02 3C 00 0F 6E 00 00 00 00 00 00 8E" --pct 60

# 校验写好的配置（与界面导入同一套规则）
python skills/24g-device-config/scripts/validate_config.py configs/devices/你的文件.json
```

---

### 需要你知道的

- **exe 一个字节没变**：哈希与 0.2.3、0.2.4 一致。已经装好的用户**不必重下 46MB**，
  想用技能包的话，从仓库拿 `skills/` 目录效果完全一样。
- **`skills/` 不参与运行**：软件启动、读电量都不会碰它，删掉不影响任何功能。
- **已有设备配置不用改**：`devices.json`、`user_settings.json`、`cache.json` 都在
  exe 同级，覆盖时注意别删掉这三个文件。
- **托盘最多画 3 个环**：现在支持 4 台设备，环里显示的是电量最低的三台，
  悬停图标能看到全部。这是原设计，不是这次改动带来的。
