# v0.2.4 · 新增 VGN 蜻蜓 F1 Pro 支持，配置现在可以用 AI 写

这是一个**配置与文档更新版本**。程序本体（exe）与 0.2.3 完全相同，**没有重新编译**，
已装 0.2.3 的用户只需替换 `configs/` 目录。

**发布日期**：2026-11-05
**适用系统**：Windows 10 / 11（64 位）
**配置文件版本**：`version: 1`（与 0.2.3 完全兼容，**已有配置不用改**）

---

### 下载

| 文件 | 说明 |
| --- | --- |
| `open24g-batmon-0.2.4-win64.zip` | 免安装，解压即用（exe + `_internal/` + `configs/`） |
| `open24g-batmon-0.2.4-win64.zip.sha256` | 校验和 |

解压后目录结构：

```
open24g-batmon-0.2.4-win64/
├── 设备电量监控.exe     # 双击这个
├── _internal/           # 运行时（Python + Qt），必须与 exe 同级
├── configs/
│   ├── devices/         # 实机配置 4 份，导入即用
│   └── templates/       # 模板与语法示例，自己加设备时照着改
├── quick-start.txt      # 使用说明
└── LICENSE
```

---

### 新增：VGN 蜻蜓 F1 Pro 鼠标

| 项目 | 值 |
| --- | --- |
| 识别方式 | VID `0x3554` / PID `0xF503`，私有接口 `usage_page 0xFF02` |
| 查询帧 | 17 字节，末字节取反校验（seed `0x4D`） |
| 电量位置 | 回包第 6 字节，直接是百分比 |
| 实测结果 | 60%，与回包里的 3950mV 电压自洽 |

它和仓库里已有的 VXE R1 SE+ 是**同厂同族协议**，帧结构一样，唯一区别是回包多了一个
报告 ID `0x08` 前缀——所以电量字节从第 5 位挪到第 6 位。这一份也因此成了
「同厂变体」的参考范例。

充放电标志位（回包第 7 字节）**尚未验证**，配置里没有启用，卡片不显示充电状态。

---

### 新增：用 AI 帮你写设备配置

仓库里多了一个 AI 技能包 [`skills/24g-device-config`](https://github.com/jiumo1436/open24g-batmon/tree/main/skills/24g-device-config)，
**它不在这个 zip 里**，需要在支持 skill 的 AI 客户端里安装后使用。

装好之后说一句「我有个 XX 设备，能加上吗」，它会：

1. 带你去设备管理器查 VID / PID、确认是 HID 还是串口
2. 拿抓到的十六进制帧分析出报文长度、锚点、电量字节、校验算法
3. 生成 `configs/devices/*.json` 并跑校验
4. 中途缺证据会停下来问，不会瞎编参数

里面两个脚本也能脱离 AI 单独用，**只依赖 Python，不需要装 PySide6**：

```bash
# 从两帧不同电量的回包推断电量字节与校验方式
python skills/24g-device-config/scripts/analyze_frames.py \
  --frame "08 04 00 00 00 02 3C 00 0F 6E 00 00 00 00 00 00 8E" --pct 60

# 校验写好的配置（与界面导入同一套规则）
python skills/24g-device-config/scripts/validate_config.py configs/devices/你的文件.json
```

---

### 需要你知道的

- **exe 与 0.2.3 完全一致**：版本号只动最后一位，就是因为这次一行程序代码都没改。
  已经装好 0.2.3 的话，把 `configs/` 整个覆盖过去就够，不必重下 46MB。
- **已有设备配置不用改**：`devices.json`、`user_settings.json`、`cache.json` 都在
  exe 同级，覆盖时注意别删掉这三个文件。
- **托盘最多画 3 个环**：现在支持 4 台设备，环里显示的是电量最低的三台，
  悬停图标能看到全部。这是原设计，不是这次改动带来的。
