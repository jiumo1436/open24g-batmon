# 发布与仓库配置指南

本文件是给**维护者**看的：怎么把仓库挂到 GitHub、怎么填仓库简介与 topics、怎么打 Release。
普通用户不需要读。

---

## 一、仓库简介（About）

GitHub 仓库首页右上角「About → Edit」里的 Description，建议用下面这段（约 120 字，远小于 350 字符上限）：

**中文版（推荐）**

```
配置驱动的通用 2.4G 外设电量监控（Windows）。帧头/偏移/换算/校验全部来自外部 JSON，
新增设备只需加一份配置、无需改源码。社区共建设备协议库。
```

**英文版（国际用户友好）**

```
Config-driven battery monitor for 2.4G wireless peripherals on Windows.
Frame headers, offsets, scaling and checksums all live in external JSON packs —
add a device by adding a config file, no code changes. Community-built protocol library.
```

Website 一栏可留空，或填 Release 页面：

```
https://github.com/<owner>/open24g-batmon/releases
```

---

## 二、Topics 标签

GitHub 的 topics 只允许**小写字母、数字、连字符**，最多 20 个。推荐这一组（复制粘贴即可）：

```
battery-monitor  2-4ghz  wireless-mouse  wireless-keyboard  wireless-headphone
usb-hid  hid  serial-port  dongle  peripheral
pyside6  qt  python  windows  desktop-app
config-driven  reverse-engineering  hardware  community  open-source
```

设置方式：仓库首页 → About 右侧齿轮 → Topics 输入框逐个添加。
主题尽量不超过 20 个，堆砌无关标签对搜索没有帮助。

---

## 三、首次上传：完整命令

### 3.1 如果本地已经有这个目录（当前情况）

```bash
cd /g/open24g-batmon

# 1) 确认身份（只需一次；用你自己的 GitHub 邮箱）
git config user.name  "你的名字"
git config user.email "you@example.com"

# 2) 初始化（本仓库已初始化过，提示 reinitialized 可忽略）
git init -b main
#    如果现有分支叫 master，改名即可：git branch -m main

# 3) 先看清楚将要提交什么 —— 必须确认没有 user_settings.json / devices.json / dist
git add -A
git status

# 4) 提交
git commit -m "chore: initial open-source release of open24g-batmon"

# 5) 关联远程（先在 GitHub 网页建一个空仓库，不要勾选 README/LICENSE/gitignore）
git remote add origin https://github.com/<owner>/open24g-batmon.git

# 6) 推送
git push -u origin main
```

### 3.2 如果你想用 GitHub CLI 一步到位

```bash
# 需要先安装 gh 并 gh auth login
gh repo create open24g-batmon --public \
  --description "配置驱动的通用 2.4G 外设电量监控；新增设备只需加一份 JSON 配置" \
  --source . --remote origin --push
```

### 3.3 推送前必做的三件事

```bash
# ① 私有文件绝不能被跟踪（应该只有 .gitignore 里的规则命中，没有输出才正常）
git check-ignore -v user_settings.json devices.json cache.json error.log dist/

# ② 确认没有把真实抓包 / 私有配置带进去
git ls-files | grep -Ei "user_settings|devices\.json|cache\.json|\.log$|pcap|dist/|build/" || echo "干净"

# ③ 配置能过校验
python tools/validate-config.py
python tools/check-filenames.py
```

### 3.4 仓库设置建议

| 设置 | 位置 | 建议 |
| --- | --- | --- |
| Description / Topics | About | 见本文第一、二节 |
| License 识别 | 仓库首页右侧 | 已含 `LICENSE`，应显示 MIT |
| Issues | Settings → General | 开启（社区反馈设备问题的主要入口） |
| Discussions | Settings → General | 可选开启，用于「求配置 / 晒设备」类话题 |
| Actions | Settings → Actions | 允许运行工作流（配置校验 CI） |
| 分支保护 | Settings → Branches | `main` 要求 PR + CI 通过，禁止直接 push |
| Releases | 右侧 Releases | 按第四节流程发布 |

---

## 四、Release 发布流程

### 4.1 打包

```bash
pip install -r requirements.txt pyinstaller
python -m PyInstaller --clean --noconfirm open24g-batmon.spec
```

产物：`dist/设备电量监控.exe`（单文件、无控制台、图标内嵌）。

### 4.2 组装发布包

```bash
mkdir -p release/open24g-batmon-1.0.0-win64
cp dist/设备电量监控.exe release/open24g-batmon-1.0.0-win64/
cp -r configs release/open24g-batmon-1.0.0-win64/configs
cp docs/quick-start.txt release/open24g-batmon-1.0.0-win64/
rm -f release/open24g-batmon-1.0.0-win64/configs/template.json \
      release/open24g-batmon-1.0.0-win64/configs/serial-template.json
cd release && 7z a open24g-batmon-1.0.0-win64.zip open24g-batmon-1.0.0-win64
```

发布包内只放**能直接导入的配置**，模板文件留在仓库里即可。

### 4.3 生成校验和

```bash
# Windows（PowerShell）
Get-FileHash .\open24g-batmon-1.0.0-win64.zip -Algorithm SHA256

# Git Bash / Linux
sha256sum open24g-batmon-1.0.0-win64.zip
```

把结果贴进 Release 说明的「校验和」一节 —— 用户下载后可以自行核对。

### 4.4 打 tag 并发布

```bash
git tag -a v1.0.0 -m "open24g-batmon v1.0.0"
git push origin v1.0.0

gh release create v1.0.0 \
  release/open24g-batmon-1.0.0-win64.zip \
  --title "v1.0.0 · 首个公开版本" \
  --notes-file docs/release-notes-template.md
```

不用 `gh` 也可以：GitHub → Releases → Draft a new release → 选 tag → 粘贴
[`release-notes-template.md`](release-notes-template.md) 的内容 → 上传 zip → Publish。

### 4.5 版本号约定

语义化版本 `v主.次.修`：

- **主**：配置文件格式不兼容（`version` 字段升级）、引擎大改；
- **次**：新增引擎能力（新校验 / 新换算）、界面功能；
- **修**：修 bug、补文档、新增设备配置。

新增设备配置本身不必发版，但建议每积累 10~20 份配置发一次次版本，
让用户能一次拿到最新协议库。

---

## 五、发布检查清单

- [ ] `python tools/validate-config.py` 全绿
- [ ] `python tools/check-filenames.py` 全绿
- [ ] 仓库里没有 `user_settings.json` / `devices.json` / `error.log` / `dist/` / `.pcap`
- [ ] `configs/` 里没有私人真实调试配置，3 套 `example-*.json` 仍为虚构值
- [ ] exe 在干净的 Windows 上双击可启动，首次打开显示「暂无设备」
- [ ] 发布包内 `configs/` 与 exe 同级，导入后能读到至少一台真实设备
- [ ] Release 说明填了「新增设备配置」「已知问题」「校验和」
- [ ] README 截图与当前界面一致
