# 协议形态速查：先归类，再套骨架

2.4G 外设的电量读取基本落在下面几种形态里。先判断属于哪一种，再改骨架，
比从空白开始写快得多。

判据来自三件事：**抓包里有几帧**、**是主机先问还是设备主动推**、**报文多长**。

---

## 形态 1：单帧查询 + 单字节电量（最常见）

**特征**：厂商软件开着时，每隔几秒出现一对 out/in；回包 17~20 字节；电量就是一个字节，
数值直接等于百分比（厂商软件显示 87%，回包里就有 `0x57`）。

```json
{
  "format": "dianliang-device-pack",
  "version": 1,
  "devices": [
    {
      "key": "brand-model",
      "name": "品牌 型号",
      "type": "mouse",
      "wire": "2.4G",
      "protocol": "私有协议 V1",
      "transport": { "type": "hid" },
      "match": {
        "vid": "0x1234",
        "pid": "0x1001",
        "usage_page": "0xFF00",
        "min_in": 17,
        "min_out": 17
      },
      "read": {
        "query": ["08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"],
        "checksum": { "mode": "sum_neg", "from": 1, "to": 16, "at": 16, "seed": "0x4D" },
        "pad_to": 17,
        "attempts": 2,
        "listen_ms": 800,
        "reply": {
          "contains": ["04"],
          "battery": { "byte": 5 },
          "charging": { "byte": 6, "mask": "0xFF" },
          "range": [2, 100]
        }
      }
    }
  ]
}
```

**关键点**

- `query` 第一个字节往往就是报告 ID（这里是 `08`），必须写。
- `pad_to` = 含报告 ID 的整段长度，和 `min_in` / `min_out` 同一个数。
- `contains` 只有 1 字节时，`battery.byte` 就是绝对偏移；有 2 字节锚点时才是相对下标。
- `range` 下限写 2 而不是 0：很多设备读到 `0x00` 是没读到，不是 0%。

参考实机配置：`configs/devices/vxe-r1-se-plus.json`

---

## 形态 2：握手 + 查询（键盘常见）

**特征**：抓包里先出现一帧短的（4~8 字节）唤醒/握手，隔几十毫秒才出现查询帧和回包。
只发查询帧时设备不理你。

```json
"read": {
  "pre": ["08 01 00 00 00 00 00 00"],
  "pre_delay_ms": 50,
  "query": ["08 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"],
  "attempts": 1,
  "listen_ms": 900,
  "reply": {
    "contains": ["0A 5A"],
    "battery": { "byte": 4 },
    "range": [1, 100]
  }
}
```

**关键点**

- `attempts` 特意写 1：键盘唤醒一次要几十毫秒，反复重试会明显增加耗电，
  也会让「轮询间隔」变得不稳定。宁可 `listen_ms` 给宽一点。
- 握手帧也可能带校验，用 `analyze_frames.py` 单独算一遍。

参考实机配置：`configs/devices/aula-f87.json`

---

## 形态 3：只被动上报（不主动查询）

**特征**：设备开机就周期性往外推电量，主机一帧都不用发。抓包里只有 in 没有 out。

```json
"read": {
  "passive_ms": 1500,
  "query": [],
  "reply": {
    "contains": ["AA 55"],
    "battery": { "byte": 3 },
    "range": [1, 100]
  }
}
```

**关键点**

- `query` 为空时**必须**给 `passive_ms`（上限 3000），否则校验器直接拒。
- 刷新会变慢：每次刷新至少要等 `passive_ms`，别设成 3000 还配 20 秒轮询。

---

## 形态 4：大报文输出通道（耳机 / 固件信息通道）

**特征**：回包 64 字节甚至更长，走 `usage_page 0xFF90` 这类厂商通道；
输入侧没有固定长度（设备不一定回）。

```json
"match": {
  "vid": "0x36F9",
  "pid": "0xF008",
  "usage_page": "0xFF90",
  "min_in": 0,
  "min_out": 64
},
"read": {
  "query": ["03 00 00 00 ... （64 字节全帧）"],
  "checksum": { "mode": "none" },
  "pad_to": 64,
  "listen_ms": 1200,
  "reply": {
    "contains": ["03 81"],
    "battery": { "byte": 2, "zero_is_off": true },
    "range": [0, 100]
  }
}
```

**关键点**

- `min_in: 0` 表示输入侧不卡长度，别硬写 64 —— 有些设备回的包更短。
- 长帧里零字节多，`analyze_frames.py` 会给出多个「都成立」的校验区间，
  这时**先用 none 测读**，读得通就不必配校验。
- 关机假帧（回包仍在、电量为 0）在这种设备上很常见，`zero_is_off` 要认真确认。

参考实机配置：`configs/devices/xiberia-t7.json`

---

## 形态 5：两字节 / 0.1% 精度

**特征**：厂商显示 87%，回包里是 `66 03`（= 870）或 `57 00`（= 87 但占两字节）。

```json
"battery": { "byte": 5, "bytes": 2, "order": "little", "scale": 0.1 }
```

判断方法：`analyze_frames.py` 会同时试 1 字节和 2 字节、两种端序，
把「比例一致」的那组排在前面；比例 `pct ≈ raw × 0.1` 就说明是 0.1% 精度。

---

## 形态 6：电压换算

**特征**：电量字段其实是电池电压（如 `0F A0` = 4000），随放电从 4.2V 平滑降到 3.3V。

```json
"battery": {
  "byte": 3,
  "bytes": 2,
  "order": "big",
  "read_as": "voltage",
  "voltage_scale": 0.001,
  "voltage_min": 3.3,
  "voltage_max": 4.2
}
```

**关键点**

- `voltage_scale` 单位和 `voltage_min/max` 必须一致：原始值 4000 配 `0.001` + `3.3/4.2`，
  或原始值 4000 配 `1` + `3300/4200`，两种都对，混用就全错。
- 电压映射是线性的，锂电池实际放电曲线不是直线，中段会偏 5~10%。
  如果厂商软件显示明显更准（比如它做了查表），改用 `map` 更贴近。

---

## 形态 7：串口接收器

**特征**：设备管理器里是 `USB-SERIAL CH340 (COM4)` 这类虚拟串口，不是 HID。

```json
"transport": {
  "type": "serial",
  "port": "",
  "baud": 115200,
  "bytesize": 8,
  "parity": "N",
  "stopbits": "1",
  "in_len": 512,
  "out_len": 0
},
"read": {
  "query": ["AA 55 01 00"],
  "listen_ms": 800,
  "reply": { "contains": ["AA 55"], "battery": { "byte": 3 }, "range": [1, 100] }
}
```

**关键点**

- 串口设备**不需要 `match`**（整个串口就是一个通道）。
- `port` 一定留空：用户在「设置 → 串口接收器」里选，或程序逐个尝试。
- 波特率先试 115200，读不到再试 9600。
- 串口没有报告 ID，`query` 就是厂商协议本身。

---

## 形态 8：一个文件放多台设备

同一套接收器带键盘 + 鼠标（双模套装）时适用。单台写错只跳过那一台。

```json
{
  "format": "dianliang-device-pack",
  "version": 1,
  "devices": [
    { "name": "某套装 键盘", "type": "keyboard", "match": { "vid": "0x1234", "pid": "0x2001" }, "read": { "...": "..." } },
    { "name": "某套装 鼠标", "type": "mouse",    "match": { "vid": "0x1234", "pid": "0x2002" }, "read": { "...": "..." } }
  ]
}
```

**关键点**

- 每台的 `name` / `key` 必须唯一。
- 两份 `read` 通常只有 `pid` 和电量字节不同，其余照抄。
