# 设备配置字段说明（CONFIG_SCHEMA v1）

本文档是 `configs/*.json` / `*.yaml` 的完整字段参考。程序里**没有任何设备协议硬编码**，
新增设备只需要写一份配置文件，不需要改源码。

- 复制 `configs/template.json` 开始写，改完用「＋ 导入设备 → 测试读取选中」验证。
- 一个文件可以只放一台设备，也可以放多台（`devices` 数组）。
- 文件格式：JSON（推荐）或 YAML（需要 `pip install pyyaml`）。
- 以 `_` 开头的键是注释，程序会忽略；`template: true` 的文件会被拒绝导入（防止误导入模板）。

---

## 1. 文件级字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `format` | string | 否 | 建议写 `"dianliang-device-pack"`；写了别的值会被拒绝并中文提示 |
| `version` | int | 否 | 配置结构版本，当前只支持 `1`；写 `2` 等更高版本会提示「版本不受支持」 |
| `template` | bool | 否 | `true` 表示这是模板，导入时直接拒绝，避免把占位数据当设备 |
| `devices` | array | 是 | 设备定义数组；也可以是顶层直接写一个数组或单个对象 |

---

## 2. 设备展示信息

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | **是** | 卡片上显示的设备名 |
| `key` | string | 否 | 内部唯一标识，省略则用 `name`；同名但不同设备会自动加 `-2` 后缀 |
| `type` | string | 否 | `mouse` / `keyboard` / `headphone` / `gamepad` / `other`，决定卡片图标 |
| `wire` | string | 否 | 副标题里的连接方式，默认 `2.4G`（串口设备默认 `串口`） |
| `protocol` | string | 否 | 协议名，显示在「协议一览」里 |

---

## 3. 传输方式 `transport`

不写 `transport` 时按 `hid` 处理。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `type` | string | `hid` | `hid`（2.4G 接收器走 USB HID）或 `serial`（串口接收器） |
| `port` | string | 空 | **串口专用**。留空 = 用软件「设置 → 串口接收器」里手动选的端口；再没有就逐个尝试本机串口。**不要写死 COM 号**，除非这台设备必须独占某个口 |
| `baud` | int | `115200` | 波特率，300 ~ 4000000 |
| `bytesize` | int | `8` | 数据位，5 ~ 8 |
| `parity` | string | `N` | `N` 无 / `O` 奇 / `E` 偶 / `M` 标记 / `S` 空格 |
| `stopbits` | string | `1` | `1` / `1.5` / `2` |
| `in_len` | int | `512` | 单次读取缓冲上限（字节） |
| `out_len` | int | `0` | 单帧发送长度下限（字节），0 表示按实际帧长发送 |

串口示例：

```json
"transport": {
  "type": "serial",
  "port": "",
  "baud": 115200,
  "parity": "N",
  "stopbits": "1"
}
```

> HID 与串口走同一套解析引擎：`read` / `reply` 的写法完全一样，只有「字节怎么进出」不同。

---

## 4. 识别条件 `match`（HID 专用）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `vid` | 十六进制字符串 | 厂商 ID，如 `"0x3554"`；`hid` 设备至少要给 `vid` 或 `product_contains` |
| `pid` | 十六进制字符串 | 产品 ID；不写表示该 VID 下所有产品 |
| `usage_page` | 十六进制字符串 | 接口的 usagePage，如 `"0xFF02"`；厂商私有接口常用 `0xFF00` 以上 |
| `min_in` / `min_out` | int | 输入 / 输出报告长度下限，用来在同一 VID 的多个接口里挑对那个 |
| `product_contains` | string[] | 产品名包含其中任一关键词才算匹配（VID 被复用时用） |

串口设备不需要 `match`，整个串口就是一个通道。

---

## 5. 读取流程 `read`

按这个顺序执行：

1. `passive_ms`：先纯听（不发送），设备主动推电量时用；
2. `pre`：发握手 / 唤醒帧，等 `pre_delay_ms`；
3. `query`：依次发查询帧，每帧最多听 `listen_ms`；
4. 整套重复 `attempts` 次；HID 的 `send: auto` 还会换 WriteFile / SetOutputReport 两条通道各试一遍。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `query` | string[] | 空 | 查询帧，十六进制字符串（`"AA BB 01"` / `"AA BB 01"` 写法都行）；空数组时必须给 `passive_ms` |
| `pre` | string[] | `[]` | 查询前先发的握手 / 唤醒帧 |
| `pre_delay_ms` | int | `0` | 发完 `pre` 等待多久 |
| `passive_ms` | int | `0` | 先纯听多久（0~3000） |
| `listen_ms` | int | `600` | 每发一帧最多等多久（50~5000） |
| `attempts` | int | `1` | 整套重试次数（1~5） |
| `pad_to` | int | `0` | 发送前把帧补齐到这个长度（很多接收器要求定长报文） |
| `send` | string | `auto` | `auto` / `write` / `setoutput`（HID 发送通道；串口忽略） |
| `checksum` | object | 无 | 发送帧的校验规则，见下 |

### 校验规则 `checksum`

| 字段 | 说明 |
| --- | --- |
| `mode` | `none` / `sum_tail`（求和取低 8 位）/ `sum_neg`（`(seed - 求和) & 0xFF`） |
| `from` / `to` | 参与计算的切片，含 `from` 不含 `to` |
| `at` | 校验字节写在第几位 |
| `seed` | `sum_neg` 的种子，如 `"0x4D"` |

---

## 6. 回包解析 `read.reply`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `contains` | string[] | 锚点字节序列，回包里出现它才开始解析；`battery.byte` 的下标从锚点算起 |
| `checksum` | object | 回包校验规则，写法同上；算不对的回包直接丢弃 |
| `battery` | object | 电量换算，见下 |
| `charging` | object | 充电标志：`{"byte": 6, "mask": "0x10"}`，该位非 0 视为充电中 |
| `range` | [int,int] | 允许的百分比范围，越界视为「没读到」 |

### 6.1 电量换算 `battery`

按以下优先级取第一个可用的换算方式：**`formula` > `map` > `read_as=voltage` > `scale`/`offset`**。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `byte` | int | — | **必填**，电量起始字节（相对锚点） |
| `bytes` | int | `1` | 连续读几个字节（1~4），配合 `order` 拼成整数 |
| `order` | string | `big` | 多字节端序：`big` / `little` |
| `mask` | 十六进制字符串 | 无 | 先与掩码，例如 `"0x7F"`、`"0xFF00"` |
| `shift` | int | `0` | 再右移（负数则左移），-32~32 |
| `mode` | string | `raw` | `raw` 整字节；`hi` / `lo` 取半字节（兼容旧配置） |
| `scale` | number | `1` | 线性系数：`百分比 = 原始值 × scale + offset` |
| `offset` | number | `0` | 线性偏移 |
| `map` | array / object | 无 | 查找表 `[[原始值, 百分比], ...]`，点之间线性插值 |
| `formula` | string | 无 | 公式，变量 `x` = 原始值；只允许算术运算和 `abs/min/max/round/int/float` |
| `read_as` | string | `percent` | `percent` 直接用；`voltage` 先按电压换算；`raw` 原样 |
| `voltage_scale` | number | `0.01` | `read_as=voltage` 时：电压 = 原始值 × 该系数 |
| `voltage_min` / `voltage_max` | number | `0` / `100` | 电压区间线性映射到 0~100% |
| `zero_is_off` | bool | `false` | 读到 0 视为「关机假帧」→ 界面显示「已关机 + 上次读数」，绝不显示成 0%，也不触发低电量提醒 |

常用写法：

```json
// 1) 单字节直接用
"battery": { "byte": 5 }

// 2) 两字节小端，0.1% 精度
"battery": { "byte": 5, "bytes": 2, "order": "little", "scale": 0.1 }

// 3) 抠位：取高位字节的低 7 位
"battery": { "byte": 5, "bytes": 2, "mask": "0x7F00", "shift": 8 }

// 4) 电压 → 百分比
"battery": { "byte": 5, "read_as": "voltage", "voltage_scale": 0.001,
             "voltage_min": 3300, "voltage_max": 4200 }

// 5) 查表（原始值 0/1/2/3 对应 0/25/60/100%）
"battery": { "byte": 5, "map": [[0, 0], [1, 25], [2, 60], [3, 100]] }

// 6) 公式
"battery": { "byte": 5, "formula": "min(100, max(0, x * 2 - 10))" }

// 7) 半字节
"battery": { "byte": 5, "mode": "lo" }
```

> **公式为什么安全**：程序用 AST 白名单求值，只允许数字、`x`、四则运算、取余、乘方
> （指数限 -8~8）和 `abs/min/max/round/int/float`。`__import__`、属性访问、字符串、
> 推导式、条件表达式一律拒绝 —— 配置文件往往来自陌生人，不能当可信代码执行。

---

## 7. 完整示例（虚构设备，勿直接使用）

```json
{
  "format": "dianliang-device-pack",
  "version": 1,
  "devices": [
    {
      "name": "示例无线鼠标",
      "type": "mouse",
      "key": "example-mouse",
      "wire": "2.4G",
      "transport": { "type": "hid" },
      "match": {
        "vid": "0x1234",
        "pid": "0x5678",
        "usage_page": "0xFF00",
        "min_in": 17,
        "min_out": 17
      },
      "read": {
        "query": ["AA BB 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00"],
        "checksum": { "mode": "sum_neg", "from": 1, "to": 16, "at": 16, "seed": "0x00" },
        "pad_to": 17,
        "attempts": 2,
        "listen_ms": 800,
        "reply": {
          "contains": ["AA BB"],
          "checksum": { "mode": "sum_tail", "from": 0, "to": 19, "at": 19 },
          "battery": { "byte": 5 },
          "charging": { "byte": 6, "mask": "0x10" },
          "range": [1, 100]
        }
      }
    }
  ]
}
```

---

## 8. 校验与报错

导入时会依次检查：文件能否解析 → `format` / `version` / `template` → 每台设备的必填字段、
类型、取值范围、帧能否按十六进制解析、公式是否合法。

所有错误都是**中文**并带字段名，例如：

- 「我的设备」的 match.vid 不是合法的数字/十六进制：'0xZZZZ'
- 「我的设备」的 reply.battery.bytes 不能大于 4（现在是 9）
- 「我的设备」的 reply.battery.formula 有问题：公式里只能调用 abs / min / max / round / int / float
- JSON 语法错误（第 3 行第 5 列）：请检查引号、逗号、括号是否完整
- 配置版本 2 不受支持：本程序支持 version 1

单台设备写错不会影响同一文件里的其它设备；导入失败也不会写入 `devices.json`。

---

## 9. 目录约定

```
configs/
├── template.json          # 模板（template: true，导入时被拒绝）
├── serial-template.json   # 串口模板
├── example-mouse.json     # 语法示例（虚构值）
├── logitech/              # 可以按厂商建子文件夹（目录名同样用小写英文）
│   └── g304.json
├── razer/
│   └── viper-v2-pro.json
└── serial/
    └── some-dongle.yaml
```

「＋ 导入设备 → 浏览 configs」会**递归**读取全部 `.json` / `.yaml` / `.yml`（文件名含 `template`
的自动跳过），勾选后再导入。导入的协议会复制一份到 `协议库/`，`devices.json` 记录当前监控列表。
