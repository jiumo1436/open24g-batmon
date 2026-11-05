# v0.2.6 · AI 技能包能自己找设备了

这是一个**技能包更新版本**。程序本体（exe）一个字节没变，哈希与 0.2.3 / 0.2.4 / 0.2.5
完全相同，**没有重新编译**；设备配置也没有任何改动。

**这次改的是 `skills/24g-device-config`**：以前它要人去设备管理器翻 VID/PID、再去软件里
抓包，现在这两步 AI 自己就能做；配置写完还能自己先读一遍，读不到会直接说卡在哪。

**发布日期**：2026-11-05
**适用系统**：Windows 10 / 11（64 位）
**配置文件版本**：`version: 1`（与 0.2.5 完全兼容，**已有配置不用改**）

---

### 下载

| 文件 | 说明 |
| --- | --- |
| `open24g-batmon-0.2.6-win64.zip` | 免安装，解压即用（exe + `_internal/` + `configs/` + `skills/`） |
| `open24g-batmon-0.2.6-win64.zip.sha256` | 校验和 |

解压后目录结构：

```
open24g-batmon-0.2.6-win64/
├── 设备电量监控.exe     # 双击这个
├── _internal/           # 运行时（Python + Qt），必须与 exe 同级
├── configs/
│   ├── devices/         # 实机配置 4 份，导入即用
│   └── templates/       # 模板与语法示例，自己加设备时照着改
├── skills/              # AI 技能包，用不上可以删
│   ├── README.md
│   └── 24g-device-config/
├── quick-start.txt      # 使用说明
└── LICENSE
```

---

### 技能包这次多了什么

**1. 新增 `scripts/probe_device.py`：AI 自己找设备，不用你翻设备管理器**

一条命令列出本机所有 2.4G 接收器：VID / PID / 接口类型 / 报文长度，末尾直接给出
可以粘进配置的 `match` 片段；还能先发一帧查询、把真实回包打出来（很多接收器
不发帧就不回话）。

```bash
python skills/24g-device-config/scripts/probe_device.py --vendor
```

**2. 新增 `scripts/read_once.py`：写完配置先自己读一遍**

按配置里的 `match` / `query` / `reply` 真的发帧、真的解析，直接打出百分比，
不用打开软件。读不到时会写明卡在哪一步：没匹配到接口 / 发帧被拒 / 发出去没回 /
回包里没有锚点 / 回包校验不对 / 电量字节取错导致超范围。

```bash
python skills/24g-device-config/scripts/read_once.py configs/devices/你的文件.json --times 3
```

**3. 工作流重写：先自己探测，缺的才问人**

- 阶段 A 第一步改成跑 `probe_device.py`，不再让用户去设备管理器抄硬件 ID
- 阶段 B 加了「发一帧查询」的流程，并给出 Compx 系最常见的几帧作为起点
- 阶段 D 加了自测：先自己读一遍，再交给用户点「测试读取」核对
- 同 VID 有多台接收器（套装键盘+鼠标）时怎么区分，写进了排查路径
- 没有 Python 的情况也写清楚了：靠软件「捕获报文」+ 导入报错来改，自测那步做不了

**4. 修掉一个隐患**

探测脚本早期版本会把标准键鼠接口和厂商接口混在一起抓，导致 `hid.dll` 在进程退出时
崩溃（实测 5/5 必崩）。现在只碰厂商私有页（`usage_page ≥ 0xFF00`），问题消失。

**5. 两轮隔离测试**

拿掉作者本机的所有先验（仓库、历史配置、聊天记录），只给 AI「发布包 + 技能包」，
分别让它加一台键盘和一台鼠标：

| 设备 | 结果 |
| --- | --- |
| 狼蛛 F87 键盘 | 自己认出 20 字节私有通道、找到查询帧、定位电量字节，读到 42%（厂商软件 43%） |
| VXE R1 SE+ 鼠标 | 自己认出 17 字节通道，连读 3 次都是 65%，与厂商软件一致，回包校验自洽 |

两份配置都与仓库里手写的版本等价（`match` / 锚点 / 电量字节 / 校验一致），
`validate_config.py` 均 0 错误。

---

### 怎么用这个技能包

把 `skills/24g-device-config/` **整个文件夹**复制到 AI 客户端的技能目录，重启客户端：

| 客户端 | 放哪里 |
| --- | --- |
| WorkBuddy | `C:\Users\你的用户名\.workbuddy\skills\`（不存在就自己建） |
| Claude Code | 项目的 `.claude\skills\` 下 |
| 其他支持 `SKILL.md` 的 agent | 它约定的 skills 目录 |

装好后直接说「我有个 XX 键盘，能加上吗」，AI 会自己找设备、自己收数据、自己写配置；
你只需要保持接收器插着、设备开着，并告诉它厂商软件显示多少电。写完它会报一个数字，
和厂商软件对得上就能导入。

**不装 AI 也能用**。`skills/24g-device-config/scripts/` 里四个脚本独立可跑，
只依赖 Python 3，不需要装 PySide6：

```bash
# 看本机插了哪些接收器
python skills/24g-device-config/scripts/probe_device.py --vendor

# 从回包里推断电量字节与校验方式
python skills/24g-device-config/scripts/analyze_frames.py \
  --frame "08 04 00 00 00 02 3C 00 0F 6E 00 00 00 00 00 00 8E" --pct 60

# 按配置真读一次，确认能用
python skills/24g-device-config/scripts/read_once.py configs/devices/你的文件.json

# 校验配置（与界面导入同一套规则）
python skills/24g-device-config/scripts/validate_config.py configs/devices/你的文件.json
```

---

### 需要你知道的

- **exe 一个字节没变**：哈希与 0.2.3 / 0.2.4 / 0.2.5 一致。已经装好的用户**不必重下 46MB**，
  想用新版技能包的话，从仓库拿 `skills/` 目录效果完全一样。
- **`skills/` 不参与运行**：软件启动、读电量都不会碰它，删掉不影响任何功能。
- **已有设备配置不用改**：`devices.json`、`user_settings.json`、`cache.json` 都在
  exe 同级，覆盖时注意别删掉这三个文件。
- **自测通过 ≠ 你那边一定能用**：设备休眠、接收器批次不同都可能影响。技能里写死了
  这条规则：用户实测前不许说「已经能用」。
