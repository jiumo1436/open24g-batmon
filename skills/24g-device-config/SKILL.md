---
name: 24g-device-config
description: 制作、修改、校验 2.4G 无线外设（鼠标/键盘/耳机/手柄）的电量监控配置文件。当用户要适配一台新设备、为它写 dianliang-device-pack 格式的 JSON、把抓包里的十六进制帧翻译成配置、排查「配置导入后读不到电量」时使用。自带设备探测脚本，VID/PID/usage_page/报文长度由 AI 自己枚举，不要求用户去设备管理器翻。覆盖 HID 与串口两种通道、单帧/握手/被动上报等协议形态、校验算法推断、电量字节定位与换算（scale/voltage/map/formula），以及提交前的字段校验。
agent_created: true
---

# 2.4G 设备电量配置生成

把「一台没被适配过的 2.4G 外设」变成一份能读数的配置 JSON。
产品是 open24g-batmon：程序里没有任何设备协议硬编码，设备支持完全由 `configs/*.json` 决定。

核心是三个问题，配置就是这三个答案：

1. **认接口**：`vid` / `pid` / `usage_page` / 报文长度
2. **发对帧**：`query`（+ 可选 `pre` 握手、`checksum` 校验）
3. **取对字节**：`reply.contains` 锚点 + `battery.byte` + 换算方式

## 铁律

1. **不许编造协议参数。** `vid` / `pid` / 帧内容必须来自**你自己跑出来的枚举或抓包**。
   编出来的配置能通过校验但永远读不到数，比没有更浪费时间。
   缺证据时输出「待填清单 + 抓包步骤」，不要输出假装能用的配置。
   VID/PID 这类信息**不要让用户去翻设备管理器**，先自己跑 `scripts/probe_device.py`。
2. **不许声称已验证。** 校验器只保证字段合法。只有用户在真实硬件上「测试读取」成功，
   才可以说这份配置能用。交付时必须写明「还未实测」或「已实测，读数 X%」。
3. **`zero_is_off` 必须问。** 协议层无法区分「关机假帧的 0」和「真的用到 0%」，
   猜错会把彻底没电显示成「只是关机了」。没得到用户明确回答前一律不写 `true`。
4. **配置里不放隐私。** 不要 MAC、序列号、用户名、本机路径、抓包文件名。
   VID/PID、Usage Page、报文长度是产品级公开标识，可以放。

## 工作流

### 阶段 A：先自己探测，缺的才问人

**第一件事是跑脚本，不是让用户去设备管理器翻。** 这台机器的 HID 接口长什么样，
你自己就能看到：

```bash
# 先 cd 到本技能目录，下面所有 scripts/... 都相对它
python scripts/probe_device.py            # 全部 HID 接口，厂商私有页打 ✓
python scripts/probe_device.py --vendor   # 只看厂商私有接口，一屏就够
```

输出里的 `vid` / `pid` / `usage_page` / `min_in` / `min_out` 就是 `match` 需要的全部字段，
末尾还会给一段可以直接粘进配置的 `match`。

- 清单里有多台对不上型号：让用户**拔掉接收器再跑一次**，少掉的那几行就是它。
- 同 VID 有多台（套装键盘+鼠标很常见）：看 `usage_page` / 报文长度 / 产品名的差别，
  一般每台占一个私有页；还是分不清就让用户拔插一次。
- 只插了一个接收器：`--vendor` 一次全抓，不用猜。
- 接口全是标准页（`usage_page < 0xFF00`）：说明电量不走 HID，先问是不是串口设备。

**机器上没有 Python 时**（脚本跑不起来）改用系统命令，仍然不要让用户自己翻：

```
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\*' } |
  Select-Object Status,Class,FriendlyName,InstanceId
```

这样只能拿到 VID/PID 和产品名，`usage_page` 和报文长度要用户点一次软件顶部
「捕获报文」→「一键复制」贴给你（那份报告第一段就是完整 HID 列表）。

**没有 Python 时**：三个脚本都跑不了，配置照样能写，但要跟用户说明白——
只能靠「捕获报文」拿到帧、靠软件导入时的报错来改，自测那一步做不了，
让用户多试几次并回报结果。

剩下真正需要问用户的只有三样，一次问完，别挤牙膏：

| 需要的 | 从哪来 | 缺失时怎么办 |
| --- | --- | --- |
| 设备名 / 型号 / 类型 | 问用户 | 直接问（鼠标/键盘/耳机/手柄都要问到，不要默认鼠标） |
| 回包的十六进制 | `probe_device.py --send "查询帧"`；抓不到再用软件「捕获报文」或 Wireshark + USBPcap | 按阶段 B 的查帧流程走 |
| 电量对照（厂商软件显示 %） | 问用户 | 强烈建议要；没有只能靠猜 |

证据齐了就不要再问，直接往下走。

**认通道**：`probe_device.py` 能枚举到的 = HID；串口设备枚举不到，
在设备管理器里位于「端口（COM 和 LPT）」。串口不需要 `vid` / `match`。

### 阶段 B：抓一帧，再分析

很多接收器**不发查询帧就不回话**，纯被动监听会是空的。先试着发一帧：

```bash
python scripts/probe_device.py --vid 0x3554 --pid 0xF503 --send "13 4A 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 5E" --frames 2
```

查询帧不知道写什么时，先去软件目录 `configs\devices\` 里翻**同 VID**（同方案）设备的
`read.query`，这类方案通常通用，改个报告 ID 就能用。发布包自带的那几份就是现成样例。
没有同 VID 可抄时，按 `references/recipes.md` 里最常见的几帧先试：

```
08 04 00 00 ...        （Compx 系鼠标，报告 ID 08）
13 4A 01 00 ...        （Compx 系键盘，报告 ID 13）
55 65 01 00            （耳机类短帧）
```

脚本会把发送帧和回应帧并排列出来，被系统拒掉的发送会明确标「发送失败」。

拿到两帧不同电量的回包后跑：

```bash
python scripts/analyze_frames.py \
  --frame "<回包1>" --pct 87 \
  --frame "<回包2>" --pct 63
```

脚本会给出：报文长度、首字节含义、锚点候选（两种起点）、电量字段候选
（含两种锚点下的 `battery.byte`）、校验方式。「比例一致」的候选才可信，单帧只能得到猜测。

拿到结论后按 `references/recipes.md` 挑最接近的形态套骨架：
单帧查询 / 握手+查询 / 仅被动 / 大报文通道 / 多字节精度 / 电压 / 串口 / 多设备同文件。

**关键换算**：`battery.byte` 是**相对锚点**的下标。锚点在偏移 0 时才是绝对偏移，
这是全流程最容易错的一处，写完必须自己核对一遍。

### 阶段 C：写配置

- 一台设备一个文件，放到**软件目录下的** `configs\devices\`（和 exe 同一层的那个 configs）
- 文件名：全小写英文 + 连字符，不含 `template`，不含中文/空格/下划线
- 正式配置**不留**注释：程序只认 `_` 开头的键作为注释，不要自创 `note` / `remark` 这类
  顶层键，交付前把注释整行删掉；`template: true` 也不能留
- `name` 用人话（`"VXE R1 SE+ 鼠标"`），`key` 用短横线小写
- 先写最小可用版本：`match` 只给 `vid`（+ `pid`）、`checksum` 用 `none`、`battery` 只给 `byte`。
  能读到数再加细节 —— 一次加五个字段，出问题就不知道错在哪
- 写完立刻用 `scripts/read_once.py` 自己读一遍（见阶段 D），不要直接丢给用户

### 阶段 D：自测、校验，再交付

写完**先自己读一遍**，不要写完就丢给用户试：

```bash
python scripts/read_once.py ../../configs/devices/<文件名>.json
```

它按配置里的 `match` / `query` / `reply` 真的发帧、真的解析，直接打出百分比；
读不到会写明卡在哪一步（没匹配到接口 / 发帧被拒 / 有去无回 / 锚点没有 / 校验不对 /
超出 range）。连读几次看稳不稳定：

```bash
python scripts/read_once.py ../../configs/devices/xxx.json --times 3 --raw
```

`--raw` 会把收发的原始帧打出来，方便核对字节。

然后跑校验器：

```bash
python scripts/validate_config.py configs/devices/<文件名>.json
```

有错误就按报错改，改完重跑，直到「可用设备 1 台；错误 0 处」。

**注意**：`read_once.py` 通过只说明「在这台机器上按这份配置能解析出数字」，
仍然不等于用户那边能用（设备休眠、接收器不同批次、读数与实际差一截都可能）。
交付时把两件事都说清楚：

1. 你这边自测读到多少（`read_once.py` 的输出）
2. 让用户在软件里点一次「测试读取」，和他厂商软件的数字对一下，差 ±1~2% 以内算成功

**不要**在用户回报实测结果之前说「已经可以用了」。

交付话术里的「实测三步」：

1. 软件 →「＋ 导入设备」→「浏览 configs」→ 勾选这台 → 导入
2. 右键卡片 →「测试读取」
3. 读到的数和厂商软件比对；读不到按 `references/troubleshooting.md` 的现象表排查

## 用户回报读不到时

按 `references/troubleshooting.md` 处理，原则是**一次只改一个变量**：

`match` 放宽 → `checksum` 改 none → `query` 补报告 ID → `contains` 改短 →
`battery.byte` 换偏移 → 最后才加 `charging` / `range` / `zero_is_off`

每改一处，让用户再测一次，别攒着改。

## 资源

| 文件 | 什么时候读 |
| --- | --- |
| `references/schema.md` | 查字段取值范围、换算优先级、被拒原因的原文 |
| `references/recipes.md` | 按协议形态套骨架（8 种形态，含完整 JSON） |
| `references/troubleshooting.md` | 读不到 / 数值不对 / 用户报错时 |
| `references/walkthrough.md` | 不确定该问什么、怎么开口时，照它走一轮 |
| `scripts/probe_device.py` | 阶段 A 先跑它：自己枚举 VID/PID/usage_page/长度，能发查询帧收回应 |
| `scripts/analyze_frames.py` | 有十六进制帧，要定位电量字节与校验 |
| `scripts/read_once.py` | 配置写完先跑它：按配置真发帧读一次，不用打开软件就能知道能不能用 |
| `scripts/validate_config.py` | 每次写完配置都要跑；零依赖，不需要装 PySide6 |

`scripts/validate_config.py` 与程序内「＋ 导入设备」同一套规则：它通过，界面基本就能导入。
退出码 0 = 全部通过（模板文件自动跳过）。

## 交付话术

写完之后按这个顺序说，别甩一坨 JSON：

1. 一句话结论：什么设备、走什么通道、电量在第几个字节
2. 文件路径 + 关键字段说人话（3~5 行）
3. 实测三步（导入 → 测试读取 → 对比厂商软件）
4. 还缺什么、哪里是猜的（例如「换算按 0.1% 精度推断，实测差 10 倍就改 scale」）
