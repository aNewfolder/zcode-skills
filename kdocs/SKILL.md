---
name: kdocs
description: 操作金山文档 / WPS云文档（kdocs.cn / 365.kdocs.cn）的完整方案。首选官方 kdocs-cli（已装好、个人账号已授权）：云端新建文档/表格并写入内容、读表格单元格、读写 docx/otl、上传本地文件导入、重命名、分享设置（默认所有人可编辑）、获取链接——快且稳。浏览器自动化兜底：匿名读取任意分享链接全文（含企业空间文档）、逐页截图核对表格、自动导出排版复刻的 Markdown 副本。当用户给出 kdocs.cn 链接，或要求"金山文档 / WPS云文档 / 云文档：读取、全文提取、转 Markdown、新建、写表格、填数据、上传导入、分享、拿链接"时使用。触发词：金山文档、WPS云文档、kdocs、365.kdocs.cn、分享链接、协作文档、在线文档、表格、周报。
---

# 金山文档（kdocs）操作

## 通道选择（先看这段）

| 场景 | 用哪条通道 |
|---|---|
| 新建文档/表格并写入内容、读改表格数据、上传本地文件、改名、分享、拿链接 | **kdocs-cli**（首选，快且稳） |
| 读取别人发的分享链接全文（匿名可读，含企业空间文档如社团文档） | **浏览器自动化** |
| 企业空间（如学校/公司组织）里文件的写入/编辑 | 浏览器（CLI 的 Token 是个人账号，企业空间不可达） |

## 铁律

1. Token 禁止明文出现在对话、日志、文件中（已存在系统密钥链，永远不要 `auth status` 以外的方式打印）。
2. 不可逆操作（删除、取消分享、覆盖上传）先确认；写操作完成后必须**独立读回验证**，不信任 `code: 0`。
3. 用户发起"新建 xxx"即视为允许创建；建完**必须在对话里给出链接**，并追加到 `<工作目录>/kdocs-export/links.md`。
4. 新建文件的长期偏好（默认约定，用户另有说明时从其说明）：放个人空间根目录（`parent_id: "0"`），建好后自动分享为**所有人可编辑**。
5. 对既有文件做分享/编辑前仍需当次确认。

## kdocs-cli（官方 CLI，需自行安装）

- 可执行文件：`kdocs-cli`（从金山官方获取安装，安装后确保其在 PATH 中，任意终端直接敲 `kdocs-cli` 可用）。
- 认证：Token 保存在系统密钥链（`kdocs-cli auth login` 浏览器授权获得，有效期约 1 年）。过期（错误 400006）→ 重新 `kdocs-cli auth login`，**必须用个人账号**（浏览器授权页登录个人 WPS 账号）；企业账号会被拒绝（`enterprise account authorization denied`）。
- 账号事实：CLI Token 对应你的个人 WPS 账号（自己的 `drive_id` 可用 `kdocs-cli auth status` 或 `drive get-file-info` 查询）；企业账号只在浏览器里登录，CLI 授权被企业策略禁止。
- 升级：`kdocs-cli upgrade -y`（不可用时 `--rollback`）。

### 调用格式

```
kdocs-cli <service> <action> '参数JSON'      # 简单英文参数
kdocs-cli <service> <action> --args '<json>'
kdocs-cli <service> <action> --file payload.json   # 含中文/多行/大内容：必须走文件（UTF-8）
```

- **含中文的参数禁止 key=value**（Windows 会破坏 UTF-8），用 `node -e "fs.writeFileSync('p.json', JSON.stringify({...}),'utf8')"` 生成后 `--file` 传入，用完删除。
- 输出加 `--compact`（纯 JSON）；`--silent` 只输出 data。
- `--file`/stdin 的 JSON 是**该工具的完整参数对象**；>1MB 的 base64 用脚本生成，禁止逐字符生成。

### 已验证的常用链路（参数形状实测正确，勿改）

**① 新建表格并写入数据（一步）** —— rangeData 用 **snake_case** + 二维数组：
```bash
node -e "require('fs').writeFileSync('p.json', JSON.stringify({
  name: '九月台账.xlsx', file_extension: 'xlsx', sheet_name: 'Sheet1', parent_id: '0',
  rangeData: [
    { row_from: 0, row_to: 0, col_from: 0, col_to: 1, formula: [['姓名','分数']] },
    { row_from: 1, row_to: 1, col_from: 0, col_to: 1, formula: [['张三','98']] }
  ]}),'utf8')"
kdocs-cli drive create-file-with-content --file p.json --compact
```
响应 `data.link_url` 就是链接，`data.file_id`、`data.sheet_id` 后续要用。新建 docx/otl 同一 action，传 `content: "# Markdown 正文"`（代替 rangeData）。

**② 表格续写** —— update-range-data 用 **camelCase** + `opType` + **formula 字符串**（逐区域，一格一条）：
```bash
kdocs-cli sheet update-range-data --args '{"file_id":"<id>","worksheet_id":1,"rangeData":[
  {"opType":"formula","rowFrom":3,"rowTo":3,"colFrom":0,"colTo":0,"formula":"王五"},
  {"opType":"formula","rowFrom":3,"rowTo":3,"colFrom":1,"colTo":1,"formula":"100"}]}' --compact
```
opType 可选 `formula / format / merge / picture`。读选区：`sheet get-range-data --args '{"file_id":"<id>","worksheet_id":1,"range":{"rowFrom":0,"rowTo":3,"colFrom":0,"colTo":1}}'`，返回 `cellText` 数组。
验证读回后按行提取：`... --compact | grep -o '"cellText":"[^"]*"'`。

**③ 分享（新建后默认执行）**：
```bash
kdocs-cli drive share-file --args '{"file_id":"<id>","scope":"anyone"}' --compact
kdocs-cli drive set-share-permission --args '{"file_id":"<id>","scope":"anyone","role_id":"21229356"}' --compact
```
个人云盘角色 ID：`21229356`=可编辑、`21229355`=可查看、`21229357`=可评论（平台全局角色，若失效用 `kdocs-cli call list_drive_roles --args '{"drive_id":"<你的drive_id>","file_id":"<id>"}'` 重取，drive_id 用 `auth status` 查询）。验证：`drive get-share-info`。`data.link_url` / `data.url` 即链接。

**④ 上传本地文件导入**（"本地写好再导入"的正解）：
```bash
node -e "const fs=require('fs');fs.writeFileSync('p.json',JSON.stringify({name:'报告.docx',content_base64:fs.readFileSync('报告.docx').toString('base64'),parent_id:'0'}),'utf8')"
kdocs-cli drive upload-new-file --file p.json --compact
```
支持 .docx/.xlsx/.pptx/.pdf/.md/.csv/.txt/.html/.png 等。本地生成 docx/xlsx 用 docx/xlsx skill 做，然后此命令导入；全量更新已有文件用 `drive upload-replace-file`。

**⑤ 读取 / 管理**：
- 读文档内容：`kdocs-cli drive read-file --args '{"url":"https://www.kdocs.cn/l/xxx"}'`（Markdown/纯文本/结构化）
- 改名 `drive rename-file`、移动 `drive move-file`、信息 `drive get-file-info`、搜文件 `drive search-files keyword=xx`、列目录 `drive list-my-files`
- 链接：响应里一般有 `link_url`；没有就 `drive get-file-link`

### 错误速查

| 错误 | 处理 |
|---|---|
| `400006` | Token 过期 → `auth login`（个人账号！） |
| `400001` 参数错误 | 参数形状不对，对照本文档的实测形状；不确定看 `kdocs-cli <service> <action> --help`（自带完整参数说明） |
| `429001/429002` 限频/熔断 | 停止调用到恢复时间 |
| `unknown action/service` | `kdocs-cli upgrade -y` |
| Timeout | 慢操作（上传/导出/大读取）加 `--timeout=120000`，幂等则重试 1 次 |

## 浏览器兜底（读取分享链接等）

用 browser-use:control-browser 技能（IAB）。**注意：WPS 编辑器页面截图管线经常失败**（`activity capture failed for guest`，卡死后须关标签页重开），验证状态一律用 `evaluate` + DOM 断言，别依赖截图；标签页反复重置成 about:blank 就整个关掉开新页。

### 读取分享链接全文（已验证）

1. `tab.goto("https://www.kdocs.cn/l/xxxx")` → 等 3-4s。**匿名可读，无需登录**。
2. 关闭「xx 邀请你协作编辑文档」弹窗：它吞滚轮事件，Escape 无效，cua 点击对话框右上角 X（坐标以当次截图为准）。
3. 提取正文：`evaluate(() => document.body.innerText)` —— **ARIA 快照看不到正文**，但文字在 DOM 里。
4. 翻页：WPS 只渲染可见页（状态栏`页面 : n/N`）。滚动容器 `#workspace`；**只响应真实滚轮事件**（`cua.scroll`），程序设 `scrollTop` 后要补一个小滚轮（`scrollY:-50`）触发渲染。逐屏收集行数组、按噪声清单过滤、有序去重。
5. 噪声清单：工具栏按钮文字、`可评论`、`立即登录`、`大纲`、`字数 : n`、`页面 : n/N`、字体预览 `mmmmmlli`、协作者名、AI 伴写等。
6. 表格用逐页截图目视还原（innerText 会压扁单元格）；纯文本与截图互相印证。
7. 字数核对：状态栏`字数 : n`，用于确认提取完整。

### 导出排版复刻的 Markdown（读完后默认执行）

落盘 `<工作目录>/kdocs-export/<文档标题>.md`（同名先问再覆盖），frontmatter 记 title/source/exported/pages/words/permission/owner + 映射说明。复刻规则：居中标题→`#`；编号小节→`##`（保留原编号）；黄色高亮→`==高亮==`；表格→GFM；☐ 勾选框→`- [ ]`；红色字→**加粗**；批注→`>` 引用块；协作者光标名忽略。

### 浏览器编辑（仅企业空间文档或 CLI 不可达时）

- 文字编辑器：`cua.click` 画布聚焦后 `cua.type` 可直接输入**中文**；自动保存。重命名/删除：顶栏左侧第三个按钮（汉堡菜单，约 x=96,y=19）→ 文档管理 → 重命名/删除文件（删除进回收站）。
- **表格编辑器不接受任何合成输入**（cua.type/dom_cua.type/keypress/元素 fill/剪贴板粘贴/模型 API 共 8 种方式实测全部无效）——表格内容操作**一律走 CLI**，别浪费时间试。
- Playwright 的 role-locator 点击在 WPS 页面容易超时，优先 `evaluate` 里找可见叶子元素 `.click()` 或 `cua` 坐标。
- 企业账号登录态：浏览器里登录你的企业账号（所在组织），用于访问组织内共享文档。

## 账号与边界

- **个人 WPS 账号**（CLI）：个人空间全功能；企业空间不可达。
- **企业账号（你的组织，如学校/公司）**（浏览器）：组织内共享文档可读写（文字），但官方 API/CLI 受企业管控（`enterprise account authorization denied`），不要反复尝试。
- CLI 无删除文件的 action（回收站只有 list/restore）；确需删除走浏览器「文档管理 → 删除文件」，或让用户手动删。
- 分享参数文档：`references/drive/share.md`（github.com/kdocs-app/kdocs-skill，官方仓库，含各类型完整 reference，拿不准先查）。
