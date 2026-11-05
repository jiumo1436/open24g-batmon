# 配置字段速查（CONFIG_SCHEMA v1）

一台设备的配置 = 「认哪个接口」+「发什么帧」+「从哪个字节取电量」三件事。
写配置时按本文件的**取值范围**填写，越界会被校验器拒绝。

## 1. 文件级

```json
{ "format": "dianliang-device-pack", "version": 1, "devices": [ { ... } ] }
```

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `format` | 否 | 只能是 `"dianliang-device-pack"`，写错会被拒 |
| `version` | 否 | 只能是 `1` |
| `template` | 否 | 写 `true` 会被拒绝导入；正式配置**不要**带这个键 |
| `devices` | 是 | 数组；单台设备也建议写成数组 |

`_` 开头的键是注释，程序忽略。**输出给用户的正式配置里不要留注释键**，保持干净。

## 2. 设备信息

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | **是** | 卡片标题，如 `"VXE R1 SE+ 鼠标"` |
| `key` | 否 | 内部唯一名，默认取 `name`；同型号多设备时才需要 |
| `type` | 否 | `mouse` / `keyboard` / `headphone` / `gamepad` / `other` |
| `wire` | 否 | 默认 `2.4G`；串口设备默认 `串口` |
| `protocol` | 否 | 显示在「协议一览」，如 `"私有协议 V1"` |

## 3. 传输方式 `transport`

不写 = `hid`。

| 字段 | 默认 | 取值范围 |
| --- | --- | --- |
| `type` | `hid` | `hid` / `serial` |
| `port` | `""` | **留空**。写死 COM 号会让别人用不了 |
| `baud` | 115200 | 300 ~ 4000000 |
| `bytesize` | 8 | 5 ~ 8 |
| `parity` | `N` | `N` `O` `E` `M` `S` |
| `stopbits` | `1` | `1` / `1.5` / `2` |
| `in_len` | 512 | 1 ~ 65536 |
| `out_len` | 0 | 0 ~ 65536，0 = 按实际帧长发送 |

## 4. 识别条件 `match`（仅 HID）

| 字段 | 说明 |
| --- | --- |
| `vid` | 如 `"0x3554"`。hid 设备必须给 `vid` 或 `product_contains` |
| `pid` | 不写 = 该 VID 下所有产品 |
| `usage_page` | 厂商私有接口常用 `0xFF00` 以上（`0xFF02`、`0xFF90` 都见过） |
| `min_in` / `min_out` | 报文长度**下限**（含报告 ID 的整段长度） |
| `product_contains` | 产品名包含任一关键词，VID 被复用时用 |

> **宁可少写，不要多写**。条件越多越可能一台都匹配不上。先 `vid` + `pid` 跑通，
> 再按需补 `usage_page` / 长度收窄。

## 5. 读取流程 `read`

执行顺序：`passive_ms` 纯听 → 发 `pre` 并等 `pre_delay_ms` → 依次发 `query`，
每帧最多等 `listen_ms` → 整套重复 `attempts` 次。

| 字段 | 默认 | 取值范围 | 说明 |
| --- | --- | --- | --- |
| `query` | `[]` | 十六进制字符串数组 | 空数组时必须给 `passive_ms` |
| `pre` | `[]` | 同上 | 握手 / 唤醒帧 |
| `pre_delay_ms` | 0 | 0 ~ 10000 | 发完 `pre` 等多久 |
| `passive_ms` | 0 | 0 ~ 3000 | 只收不发 |
| `listen_ms` | 600 | 50 ~ 5000 | 单帧等待上限 |
| `attempts` | 1 | 1 ~ 5 | 整套重试次数 |
| `pad_to` | 0 | 0 ~ 65536 | 发送前补齐到的总长度 |
| `send` | `auto` | `auto`/`write`/`setoutput` | HID 发送通道；串口忽略 |
| `checksum` | 无 | 见下 | 发送帧的校验 |

### 校验 `checksum`

| 字段 | 说明 |
| --- | --- |
| `mode` | `none` / `sum_tail`（求和低 8 位）/ `sum_neg`（`(seed - 求和) & 0xFF`） |
| `from` / `to` | 参与计算的切片，**含 from 不含 to** |
| `at` | 校验字节写在第几位（发送）；回包时就是比对这一位 |
| `seed` | 仅 `sum_neg`，如 `"0x4D"` |

发送时程序**算好后覆写第 `at` 位**；回包时算一遍和帧里第 `at` 位比对，不等就丢弃这帧。

## 6. 回包 `read.reply`

| 字段 | 说明 |
| --- | --- |
| `contains` | 锚点字节，找到它才开始解析；`battery.byte` 从锚点起算 |
| `checksum` | 回包校验，写法同上 |
| `battery` | 电量换算，见 §7 |
| `charging` | `{ "byte": 6, "mask": "0x01" }`，该位非 0 = 充电中 |
| `range` | `[最小, 最大]`，越界视为没读到；默认 `[0, 100]` |

> **锚点下标是最容易错的地方**：`contains: ["A5 5A"]` 且电量在绝对偏移 10，则
> `battery.byte = 10 - 锚点起始偏移`。锚点在开头时就是绝对值本身。

## 7. 电量换算 `battery`

优先级：**`formula` > `map` > `read_as=voltage` > `scale`/`offset`**，
同时给了多个只有优先级最高的那个生效。

| 字段 | 默认 | 取值范围 | 说明 |
| --- | --- | --- | --- |
| `byte` | — | ≥ 0 | **必填**，电量起始字节（相对锚点） |
| `bytes` | 1 | 1 ~ 4 | 连续读几个字节 |
| `order` | `big` | `big`/`little` | 多字节端序 |
| `mask` | 无 | 非 0 | 先与掩码，如 `"0x7F00"` |
| `shift` | 0 | -32 ~ 32 | 再右移（负数为左移） |
| `mode` | `raw` | `raw`/`hi`/`lo` | 取整字节或半字节 |
| `scale` | 1 | 数值 | `pct = 原始值 × scale + offset` |
| `offset` | 0 | 数值 | 线性偏移 |
| `map` | 无 | 数组/对象 | `[[原始值, 百分比], ...]`，点间线性插值 |
| `formula` | 无 | 见下 | 变量 `x` = 原始值 |
| `read_as` | `percent` | `percent`/`voltage`/`raw` | `voltage` 才需下面三项 |
| `voltage_scale` | 0.01 | 数值 | 电压 = 原始值 × 该系数 |
| `voltage_min` / `voltage_max` | 0 / 100 | 数值，不能相等 | 电压区间线性映射到 0~100% |
| `zero_is_off` | `false` | 布尔 | 读到 0 视为关机假帧 |

### formula 白名单

只允许：数字、`x`、`+ - * / // % **`、括号，以及 `abs/min/max/round/int/float`。
乘方指数必须是 -8~8 的常量。字符串、属性访问、`__import__`、条件表达式、推导式一律拒绝。

```json
"formula": "min(100, max(0, x * 2 - 10))"
```

### zero_is_off 的判断规则（必须问用户，不能猜）

| 设备关机时的表现 | 要不要开 |
| --- | --- |
| 接收器掉线 / 设备消失 | 不需要（读不到 0） |
| 接收器在、不回包 | 不需要（超时） |
| **接收器在、正常回包、电量字节为 0** | **要开 `true`** |

猜错的代价：把「真的用到 0%」显示成「只是关机了」，用户会以为还有电。
所以**默认不开**，只在用户确认过「关机后仍回包且电量为 0」时才写 `true`。

## 8. 常见拒绝原因（写的时候就避开）

| 报错 | 原因 |
| --- | --- |
| 缺少 name（设备名称） | `name` 没写或为空 |
| match 里至少有 vid 或 product_contains | hid 设备两个都没给 |
| reply.battery.byte 必填 | 忘了写电量字节 |
| read.query 为空，就必须给 passive_ms | 既不发帧也不听 |
| 帧不是合法的十六进制 | 有 `GG`、`0x` 混写、奇数个字符 |
| reply.battery.mask 不能是 0 | 掩码写了 `0x00` |
| attempts 不能大于 5 | 超出范围 |
| reply.range 最小值不能大于最大值 | 写反了 |
| 公式里只能调用 abs / min / max / round / int / float | 用了 `sqrt`、`pow` 等 |
| 配置版本 2 不受支持 | `version` 写错 |
