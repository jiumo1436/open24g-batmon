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

产物：`dist/设备电量监控/` 目录（`设备电量监控.exe` + `_internal/`，合计约 116 MB）。
**整个目录都要分发**，exe 离开 `_internal/` 无法启动。

### 4.2 组装发布包

**一个版本一个目录**，不要把多个版本的 zip 平铺在 `release/` 根下——
版本一多就分不清哪份是当前版，容易把旧包传上去。

```
release/
└── v0.2.2/                              # 目录名 = tag 名
    ├── open24g-batmon-0.2.2-win64/      # 解压后的内容（zip 的根目录）
    ├── open24g-batmon-0.2.2-win64.zip   # 对外分发的包
    └── open24g-batmon-0.2.2-win64.zip.sha256
```

> **只保留当前版本**：新版本发布并核验通过后，把上一版的 `release/vX.Y.Z/`
> 整目录删掉。这个目录只是上传源与本地备份，GitHub 上已经是权威副本，
> 留着旧版只会占几十 MB 并增加「传错包」的机会。
> 对应地，GitHub Releases 也只保留最新一版（见 4.6）。

```bash
VER=0.3.0
BASE=open24g-batmon-${VER}-win64

mkdir -p release/v${VER}/${BASE}
cp -r dist/设备电量监控/. "release/v${VER}/${BASE}/"
cp -r configs "release/v${VER}/${BASE}/configs"
cp docs/quick-start.txt "release/v${VER}/${BASE}/"
cp LICENSE "release/v${VER}/${BASE}/"
cd "release/v${VER}" && 7z a ${BASE}.zip ${BASE}
```

**发布包要带完整 `configs/`（`devices/` + `templates/`）**，不要只带实机配置：

- `devices/` 是软件开箱可用的前提 —— 不带配置，用户解压后打开就是「暂无设备」，
  得先去 GitHub 翻仓库才能用，这是新用户流失最大的一步；
- `templates/` 只有十几 KB，却是用户自己加设备时唯一的抄写起点，删掉反而抬高门槛；
- `template.json` 自带 `"template": true`，导入界面会主动拒绝，**留着不会被误导入**，
  所以不再需要 rm 掉模板（旧版本有这一步，现已取消）。

`LICENSE` 要随包分发 —— MIT 要求分发时附上许可证副本。

> `release/` 已写进 `.gitignore`，整个目录不进版本库，只作为本地留档与上传源。
> 对外分发以 GitHub Releases 为准。

### 4.3 生成校验和

```bash
# Windows（PowerShell）
Get-FileHash ".\release\v0.3.0\open24g-batmon-0.3.0-win64.zip" -Algorithm SHA256

# Git Bash / Linux
cd release/v0.3.0 && sha256sum open24g-batmon-0.3.0-win64.zip
```

把结果贴进 Release 说明的「校验和」一节 —— 用户下载后可以自行核对。

上传后到 Releases 页面点资产名，GitHub 会显示 `digest`（形如 `sha256:6d76...`），
**与本地哈希逐字符比对**，一致才说明上传完整、可以公开。

### 4.4 打 tag 并发布

```bash
git tag -a v0.3.0 -m "open24g-batmon v0.3.0"
git push origin main
git push origin v0.3.0
```

上传资产两条路：

**A. 有 `gh` 时最省事**

```bash
gh release create v0.3.0 \
  release/v0.3.0/open24g-batmon-0.3.0-win64.zip \
  release/v0.3.0/open24g-batmon-0.3.0-win64.zip.sha256 \
  --title "v0.3.0 · 一句话主题" \
  --notes-file docs/release-notes-0.3.0.md
```

**B. 没有 `gh`：网页手动**

GitHub → Releases → Draft a new release → 选 tag → 粘贴
[`release-notes-template.md`](release-notes-template.md) 的内容 → 上传 zip → Publish。

**C. 没有 `gh` 但想自动化：REST API**

关键顺序是**先建草稿 → 传资产 → 核对 digest → 最后公开**。
直接建正式 Release 再传 46 MB 资产，一旦上传中断，页面上就会留一个没有附件的空壳链接。

```
POST /repos/{owner}/{repo}/releases            {"tag_name":"v0.3.0","draft":true,"body":"..."}
POST https://uploads.github.com/repos/{owner}/{repo}/releases/{id}/assets?name=xxx.zip
     （Content-Type: application/zip，body 为文件二进制）
PATCH /repos/{owner}/{repo}/releases/{id}      {"draft":false}
```

凭据可直接复用本机已存的 GitHub 登录态，不必新建 PAT：

```bash
printf "protocol=https\nhost=github.com\n\n" | git credential fill
# 输出里的 password= 就是 token，需要 repo 作用域
```

### 4.5 版本号约定

语义化版本 `v主.次.修`：

- **主**：配置文件格式不兼容（`version` 字段升级）、引擎大改；
- **次**：新增引擎能力（新校验 / 新换算）、界面功能；
- **修**：修 bug、补文档、新增设备配置。

新增设备配置本身不必发版，但建议每积累 10~20 份配置发一次次版本，
让用户能一次拿到最新协议库。

### 4.6 清理旧版本（每次发版后都要做）

**GitHub Releases 与 tag 只保留最新一版。**

理由很实际：这是个单人维护的小工具，用户只会下最新版。旧版本挂在 Releases
页面上只会让人站在下载页犹豫「我该下哪个」；更麻烦的是旧包里带着已经修掉的
bug，被下载走就是负资产。

```bash
# 1. 取旧 release 的 id
curl -s -H "Authorization: Bearer $T" \
  "https://api.github.com/repos/jiumo1436/open24g-batmon/releases?per_page=30" \
  | python -c "import sys,json;[print(r['id'], r['tag_name']) for r in json.load(sys.stdin)]"

# 2. 删 Release（资产随 Release 一起消失，不可撤销）
curl -X DELETE -H "Authorization: Bearer $T" \
  "https://api.github.com/repos/jiumo1436/open24g-batmon/releases/<id>"

# 3. 删本地与远程 tag
git tag -d v<旧版本>
git push origin --delete v<旧版本>
```

删 tag 不影响历史完整性 —— tag 只是指向某个 commit 的标签，commit 本身仍在
`git log` 里。要回溯旧版本代码，`git log` 或 `git diff v旧..v新` 之前先
`git tag` 临时打回来即可。

> 同时删掉 `docs/release-notes-<旧版本>.md` 和 `release/v<旧版本>/` 目录，
> 把 README 与 `release/README.md` 里的版本表改成一版。
> 只留「当前这版的说明 + 模板」，历史变更靠 commit message 追溯。

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
