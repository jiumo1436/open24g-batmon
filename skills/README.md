# skills —— 让 AI 帮你写设备配置

`configs/` 里每多一份配置，软件就多支持一台设备。但写配置要读抓包、算校验、
数字节下标，对大多数人来说门槛不低。

这个目录放的是一个 **AI 技能包**：把它交给支持 skill 的 AI，你只要说「我有个 XX 键盘，
能加上吗」，AI 会自己枚举出 VID/PID、发查询帧、分析回包、定位电量字节，生成 JSON 并校验。
鼠标、键盘、耳机、手柄都一样，不限于鼠标。

## 里面有什么

```
skills/24g-device-config/
├── SKILL.md                       # 技能主文件：铁律 + 四阶段工作流
├── references/
│   ├── schema.md                  # 字段速查（取值范围、换算优先级、报错原文）
│   ├── recipes.md                 # 8 种协议形态的完整骨架
│   ├── troubleshooting.md         # 读不到数时的排查表
│   └── walkthrough.md             # 一次完整对话示例
└── scripts/
    ├── probe_device.py            # 枚举本机 HID 接口（VID/PID/usage_page/长度）+ 发帧收回应
    ├── analyze_frames.py          # 从回包推断电量字节 / 锚点 / 校验
    ├── read_once.py               # 按配置真读一次，不用开软件就知道能不能用
    └── validate_config.py         # 校验配置，零依赖
```

四个脚本也能单独用，不装 AI 也行：

```bash
# 看这台机器上插了哪些接收器（厂商私有接口打 ✓，末尾给可用的 match）
python skills/24g-device-config/scripts/probe_device.py --vendor

# 发一帧查询，把回应打出来（不发帧很多设备根本不回话）
python skills/24g-device-config/scripts/probe_device.py \
  --vid 0x3554 --pid 0xFA09 --send "13 4A 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 5E"

# 分析抓到的两帧（带上厂商软件显示的电量，结果准得多）
python skills/24g-device-config/scripts/analyze_frames.py \
  --frame "0A 5A A5 12 00 00 00 00 55 00 00 00 00 00 00 00 00 00 00 66" --pct 85 \
  --frame "0A 5A A5 12 00 00 00 00 2F 01 00 00 00 00 00 00 00 00 00 41" --pct 47

# 校验写好的配置
python skills/24g-device-config/scripts/validate_config.py configs/devices/你的文件.json

# 真的读一次：按配置发帧、解析，直接打出电量（读不到会说卡在哪一步）
python skills/24g-device-config/scripts/read_once.py configs/devices/你的文件.json
```

`validate_config.py` 与软件内「＋ 导入设备」用的是同一套规则，通过它基本就能导入；
`read_once.py` 再往前一步，按这份配置真去读一次，能读到数就说明配置是通的。

## 怎么装

### 最省事：一句话让 AI 装（推荐）

**已经解压了发布包**（`skills\24g-device-config` 这个文件夹在电脑上）——
打开 WorkBuddy，把那个文件夹**拖进输入框**，再补一句话发出去：

```
帮我把它装成技能：把 skills/24g-device-config 整个文件夹复制到
%USERPROFILE%\.workbuddy\skills\ 下（已有同名目录就整体覆盖），
装完确认 SKILL.md、references、scripts 都在，然后告诉我。
```

AI 会自己建目录、拷贝、核对文件数。装完**重启一次 WorkBuddy**，然后直接说
「我有个 XX 键盘，能加上吗」，技能就会自动触发。

**还没下载发布包**——用这段，让 AI 自己下：

```
帮我装一个技能：打开 https://github.com/jiumo1436/open24g-batmon/releases/latest
下载最新的 open24g-batmon-*-win64.zip，解压后把里面的 skills/24g-device-config
整个文件夹复制到 %USERPROFILE%\.workbuddy\skills\ 下，装完告诉我。
```

### 手动装

| 工具 | 做法 |
| --- | --- |
| WorkBuddy | 复制 `24g-device-config/` 到 `~/.workbuddy/skills/`（全局）或项目的 `.workbuddy/skills/` |
| Claude Code | 复制到项目的 `.claude/skills/24g-device-config/` |
| 其他支持 SKILL.md 的 agent | 放进它约定的 skills 目录 |
| 不支持 skill 的对话式 AI | 把 `SKILL.md` 正文贴在对话开头，再把 `references/` 里相关的文件按需贴给它 |

`SKILL.md` 开头的 `description` 决定 AI 什么时候自动用它，安装后不用手动喊，
说「帮我适配一台设备」就会触发。

## 说明

- `skills/` **随发布包一起分发**：从 Releases 下载的 zip 解压后，`skills/` 就在
  `configs/` 旁边，不用再回仓库找。装法见上一节「怎么装」，装好后重启 AI 客户端即可。
  不需要 AI 的话，这个目录放着不影响软件运行，删掉也没关系。
- AI 生成的配置**必须实机验证**再提 PR。技能里写死了这条：不允许在用户实测前声称「能用」。
- 配置的字段定义以 `docs/config-schema.md` 为准；本目录的 `references/schema.md` 是给 AI 的速查版。
