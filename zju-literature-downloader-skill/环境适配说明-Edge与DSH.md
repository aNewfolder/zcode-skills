# DSH 环境适配说明（Edge / 全局 skill / 新工作区）

## 一、全局 skill 不需要任何声明

DSH 的 skill 由 `@deepseek-ai/dsh-skill-filesystem` 提供方扫描固定根目录，`~/.dsh/skills`（rank 400）是**用户级全局根**：放在这里的 skill **每个会话自动进入目录**，无需在任何配置文件里声明、无需注册、无需重启。

会话开始时系统会给出 `<available_skills>` 目录，形如：

```
- `zju-literature-downloader-skill`: Use this skill whenever the user wants ...
```

三种触发方式：
1. 用户直接说任务（如"用浙大图书馆下载文献"），模型按 description 判断匹配后调用 `skill` 工具加载；
2. 用户显式点名 skill 名；
3. 用户在输入框用 `/zju-literature-downloader-skill` 直接调用，指令注入当轮。

`skill` 工具返回的内容里会给出 `Base directory for this skill`，相对路径都相对它解析。加载一次后，同一轮不要重复加载。

**何时需要"声明"**：只有当你不想放在全局根、而要放进某个特定项目时，才需要关心位置——`<项目根>/.dsh/skills`（rank 100，优先级高于全局 400）。DSH 判定"项目根"为最近的含 `.git` 的祖先目录；没有 `.git` 时用当前工作目录。

## 二、Edge 与 Chrome 的区别：对这个 skill 基本没有影响

**结论：可以用 Edge，无需为此改 skill。**

原因是这个 skill 的脚本**从不自己启动浏览器**。实测 `scripts\browser_pdf_downloader.mjs` 与 `scripts\cdp_open_url.mjs` 都只做一件事：连 `--proxy`（默认 `http://127.0.0.1:3456`）的 HTTP 接口，由**代理**去对接浏览器。所以：

> 只要某个 Chromium 浏览器监听着 CDP，无论是 Chrome 还是 Edge，CDP 协议、`/json/list`、`Page.navigate`、`Runtime.evaluate`、PDF 查看器的 `chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai` 全都一致。

本机实测：Edge `151.0.4129.86` 通过 `--remote-debugging-port=9222` 跑通了上一条任务的全部流程（25 篇 PDF，含 Summon、IEEE、ScienceDirect、Optica、Science、Nature、ProQuest、AIP）。

### 真正要注意的差别（都很小）

| 项 | Chrome | 本机 Edge | 影响 |
|---|---|---|---|
| 浏览器是否安装 | skill 假设有 Chrome | **未安装 Chrome，只有 Edge** | 按 skill 原文"打开 Chrome"会直接失败；用 `start-debug-browser.ps1` 会自动选到 Edge |
| 内部页面 | `chrome://inspect/#remote-debugging` | `edge://inspect/#remote-debugging` | 只是入口名字不同，功能一致 |
| 调试端口 | 新版 Chrome 对**默认 profile** 忽略 `--remote-debugging-port` | 同样行为 | 两边都必须用**独立 `--user-data-dir`** |
| 已保存密码 | 独立 profile 里没有 | 同左 | 都要**人工登录一次**浙大 CAS（含 authcode），因为独立 profile 不共享原 profile 的密码 |
| 版本说明 | — | UA 为 `Edg/...`，`/json/version` 的 `Browser` 为 `Edg/151...` | 对取 PDF 无影响；若某脚本断言 `Browser` 含 "Chrome" 需放宽匹配 |
| 下载行为 | 同 | 同 | 若点"下载"会让浏览器存到 Downloads，反而更难自动化；仍推荐页面内 `fetch()` 取字节 |

### 启动命令（本机已验证）

```powershell
# 自动探测 Edge/Chrome，起一个带调试端口的独立 profile
powershell -ExecutionPolicy Bypass -File "…\_tools\start-debug-browser.ps1" -Port 9222
# 验证通道
node "…\_tools\cdp.mjs" list
```

脚本里的探测顺序是 Chrome → Edge，本机命中：
`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`

### 若将来想用回 Chrome

安装 Chrome 后无需改脚本（探测顺序优先 Chrome）。若要让 skill 原文的 `web-access-main` 代理路径生效，需要额外安装该 skill 并让它在 3456 端口提供 CDP 代理；否则继续用 `cdp.mjs` 直连 9222 即可。

## 三、换到群晖 Drive 路径后要注意

- 群晖 Drive 是**本地同步盘**（本机 G:/H: 等为本地盘符），不经过 RaiDrive 的 WebDAV 层。上一条任务踩到的四类故障（目录列表失真、批量写入静默丢失、不支持原子改名、相对路径解析错乱）**预计不再出现**，内置 write/edit 文件工具应能正常原子写入。
- 因此之前"必须写本地磁盘再复制到共享"的绕行做法**可以取消**；但两条纪律仍建议保留：
  1. 归档后**逐文件比对字节数**（一次 `Get-ChildItem | Compare Length` 即可）；
  2. 保留 `manifest.tsv` 记录来源与状态。
- 若出现 Synology Drive 客户端正在同步（`.syno`/临时文件、文件被占用）导致写入失败，处理方式同本地磁盘：串行、小批次、写入后校验。
- 全局 skill 路径 `~/.dsh/skills` 在同步盘之外，迁移不会影响它；若想让 skill 跟随新工作区，可再复制一份到 `<新工作区>\.dsh\skills\`（rank 100 覆盖全局 400）。
