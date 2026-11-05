# skills —— 让 AI 帮你写设备配置

`configs/` 里每多一份配置，软件就多支持一台设备。但写配置要读抓包、算校验、
数字节下标，对大多数人来说门槛不低。

这个目录放的是一个 **AI 技能包**：把它交给支持 skill 的 AI，你只要说「我有个 XX 键盘，
能加上吗」，AI 会带着你查 VID/PID、分析抓包、定位电量字节、生成 JSON 并校验。

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
    ├── analyze_frames.py          # 从回包推断电量字节 / 锚点 / 校验
    └── validate_config.py         # 校验配置，零依赖
```

两个脚本也能单独用，不装 AI 也行：

```bash
# 分析抓到的两帧（带上厂商软件显示的电量，结果准得多）
python skills/24g-device-config/scripts/analyze_frames.py \
  --frame "0A 5A A5 12 00 00 00 00 55 00 00 00 00 00 00 00 00 00 00 66" --pct 85 \
  --frame "0A 5A A5 12 00 00 00 00 2F 01 00 00 00 00 00 00 00 00 00 41" --pct 47

# 校验写好的配置
python skills/24g-device-config/scripts/validate_config.py configs/devices/你的文件.json
```

`validate_config.py` 与软件内「＋ 导入设备」用的是同一套规则，通过它基本就能导入。

## 怎么装

不同 AI 客户端的技能目录不一样，选一个对应做法：

| 工具 | 做法 |
| --- | --- |
| WorkBuddy | 复制 `24g-device-config/` 到 `~/.workbuddy/skills/`（全局）或项目的 `.workbuddy/skills/` |
| Claude Code | 复制到项目的 `.claude/skills/24g-device-config/` |
| 其他支持 SKILL.md 的 agent | 放进它约定的 skills 目录 |
| 不支持 skill 的对话式 AI | 把 `SKILL.md` 正文贴在对话开头，再把 `references/` 里相关的文件按需贴给它 |

`SKILL.md` 开头的 `description` 决定 AI 什么时候自动用它，安装后不用手动喊，
说「帮我适配一台设备」就会触发。

## 说明

- `skills/` **不参与打包**：发布包只含 `设备电量监控.exe`、`_internal/`、`configs/`、
  `quick-start.txt`、`LICENSE`，这个目录只在仓库里给做配置的人用。
- AI 生成的配置**必须实机验证**再提 PR。技能里写死了这条：不允许在用户实测前声称「能用」。
- 配置的字段定义以 `docs/config-schema.md` 为准；本目录的 `references/schema.md` 是给 AI 的速查版。
