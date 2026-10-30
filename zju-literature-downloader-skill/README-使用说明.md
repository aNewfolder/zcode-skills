# ZJU 文献下载 → 解析 → 归位（三步流程）

用你自己已登录的**浙江大学图书馆 / WebVPN / CAS** 会话（Chrome 或 Edge），把论文清单变成
**归档到你自己文献库目录树里的 PDF**。全程只用你的合法机构权限：不绕过付费墙、DRM、验证码、
Cloudflare 或双因素认证；遇到人机验证会停下来交给你处理。

> 适用：DeepSeek Harness / Claude Code / Codex 等能读 `SKILL.md` 的 agent。
> 本仓库是该 skill 的**完整包**，可直接被 agent 加载。

---

## 你需要提供什么（每个人只配一次）

| 必须提供 | 说明 |
|---|---|
| **1. 论文清单** | 题目、DOI，或两者混合都行。直接贴一段列表即可 |
| **2. 你的文献库根目录** | 例如 `D:\我的文献库`。**每个人的目录树不同**，所以必须由你给出；工具不会替你发明目录 |
| 3.（可选）**你的归位偏好文件** | `归位偏好-<你的名字>.tsv`，记录"论文里的说法 → 你的目录"。放在你的工作区里，**不放进本仓库**（它是个人习惯，不是通用规则） |

**核心约定：文件夹由你创建。** 只有你明确说"请 AI 新建 `<完整路径>`"，AI 才会创建目录；
否则目标不存在时工具会拒绝执行并提示怎么授权。AI 不会自行发明路径或中间层级。

---

## 三步流程

### 第 1 步：下载（Download）

```powershell
# 0) 一次性准备：Node 22+、Python 3.10+、依赖、一个带调试端口的浏览器
python -m pip install -r requirements.txt          # pdfplumber + pypdf（解析与校验用）
powershell -ExecutionPolicy Bypass -File scripts\start-debug-browser.ps1 -Port 9222
#    在弹出的窗口里人工登录浙大图书馆 / WebVPN（CAS 含 authcode），之后长期有效

# 1) 建本轮任务目录（结果按对话时刻归档）
node scripts\new_round.mjs --list "你的清单.txt" --label 第一批

# 2) 下载（走 Summon/出版社，脚本在页面上下文取 PDF 字节）
node scripts\cdp.mjs list                          # 确认 CDP 通道
#    按出版社选用：batch-download / ieee-download / proquest-download / oa_download 等

# 3) 四步校验：%PDF 签名 → 页数 → 文本可读 → **身份校验**
python -X utf8 scripts\pdf_identity_check.py --dir "PDFs" --manifest "manifest.tsv"
```

> **为什么要身份校验**：格式对、页数对、文本能读，仍可能是**另一篇论文**。
> 实测一个 38 条批次里抓出 2 篇下错（同一 DOI 指向了不相关的论文）。这一步不能省。

### 第 1 步（通用版）：清单驱动下载 `fetch_pdfs.py`

不想每次改数据脚本，就用这一个通用入口。它**只做路由决策，默认不取任何字节**：

```powershell
# L1 路由干跑（默认）：读清单 -> 输出"每篇该走哪条路、PDF 地址是什么"
python -X utf8 scripts\fetch_pdfs.py --list list.txt `
  --md "03-move\下载路由计划.md" --json "03-move\下载路由计划.json"

# L2 轻量探测：对能直接算出的 URL 只读响应头与 8 字节（不落盘）
python -X utf8 scripts\fetch_pdfs.py --list list.txt --check-url

# L3 真下指定篇（其余仍是干跑）
python -X utf8 scripts\fetch_pdfs.py --list list.txt --execute `
  --out "01-下载\PDFs" --only 10.1109/JLT.2015.2465382
```

**清单格式**：DOI 或题录引文都行（如 `1. Xiang C, et al. High-performance…[J]. IEEE JSTQE, 2022.`）——
引文会自动抽出标题再用 Crossref 反查 DOI。

**路由表 `routes.yaml`** 是唯一需要维护的地方：按 DOI 前缀声明"PDF 怎么拿"
（IEEE 走 `getPDF.jsp`、Optica 走 `viewmedia`、Nature 读 `meta[citation_pdf_url]`、
Wiley 同源 `pdfdirect`、Springer `content/pdf`、Elsevier 走签名 `pdfft`…），
并把已知不可得的（专著、标准、无权限刊）标 `refuse` 以免白试。**出版社改版只改这一个文件。**

实测（本机 38 条真实清单）：**可路由 30 条**，其中 Nature 6 / Optica 7 / IEEE 6 / AIP 2 /
Science 2 / Elsevier 2 / Wiley 1 / 未匹配 4；其余 8 条为"需人工确认 DOI"或 `refuse`。

**DOI 空缺的处理策略（人工定的规矩）**：不要为"可能歧义"提前阻塞流程。
清单里没写 DOI 时，脚本按题录反查；只有**真的出现争议**（反查候选与题目明显不符、
或同一题名对应多篇会议/期刊论文）才停下来提醒人工明确 DOI。

实务口径：
- **专著 / 标准 / 路线图**（`[M]`、`[R]`、Specification、Roadmap、Implementation Agreement）：
  这类本来就没有论文 DOI，**直接标 `refuse` 并写清原因**，不必让人补 DOI。
- **论文**：反查给出候选但相似度不足时，标 `需人工确认 DOI` 并把候选一并列出，
  由人工决定"补 DOI 重跑"还是"跳过"。
- 反过来，如果人工在清单里**直接给了 DOI**，脚本一律优先采信，不再反查（最省事也最准）。

**已知局限（重要）**：题录反查 DOI 对"会议论文 vs 期刊论文"仍有误配风险
（实测 *Heterogeneous silicon photonic integrated circuits* 被配到 Optica 会议论文而非 JLT 期刊论文）。
因此脚本内置三道护栏（F1、长度比、关键实词覆盖），仍不确定时**留空并标 `需人工确认 DOI`**——
宁可让你填，也不写错 DOI。看到"需人工确认"的行，请把正确 DOI 补进清单再跑。

### 第 2 步：解析（Parse）

```powershell
# 对已下载的 PDF 出结构化题录（题目/作者/年/刊/DOI/摘要/文献类型线索）
python -X utf8 scripts\parse_papers.py --dir "01-下载\PDFs" --out-dir "02-解析\输出" `
  --json "02-解析\输出\parse.json" --dirs-from "lib-map\库目录清单.tsv"
```

- 信息源优先级：**首页 DOI → Crossref** 为主，PDF 文本兜底；
  首页没有 DOI 时才用"标题+作者+刊名"去 Crossref 反查，且**歧义就不猜**（留空待人工确认）。
- 文献类型（综述 / 研究论文 / 学位论文…）**Crossref 区分不了**（实测全部是 `journal-article`、
  `subtype` 为空），所以只给线索，最终由模型读首页/摘要判断。
- 解析是独立步骤：**你要求时才跑**，不会自动接在下载后面。

### 第 3 步：归位（Move）

```powershell
# 1) 扫描你的库目录树（只读元数据，不读 PDF 内容，0.1 秒）
python -X utf8 scripts\lib_scan.py --root "D:\我的文献库" --out-dir "lib-map"

# 2) 生成主题词表（可选，但强烈建议；填一次受益很久）
python -X utf8 scripts\make_vocab.py --dirs "lib-map\库目录清单.tsv" --out-dir "lib-map"

# 3) 生成归位建议（只用库里已存在的目录做候选）
python -X utf8 scripts\suggest_dirs.py --parse "02-解析\输出\parse.json" `
  --dirs "lib-map\库目录清单.tsv" --vocab "lib-map\词表-vocab.tsv" --out "03-move\propose.tsv"

# 4) 人工在表里填：选择（数字=推几处）/ 备注（理由，或"请 AI 新建 <路径>"）/ 人工推荐

# 5) 执行（默认 dry-run；--execute 才真正移动）
python -X utf8 scripts\lib_move_v2.py --library "D:\我的文献库" --excel "03-move\建议表.xlsx"
python -X utf8 scripts\lib_move_v2.py --library "D:\我的文献库" --excel "03-move\建议表.xlsx" --execute
```

**归位环节的硬规矩（都是踩过坑写进去的）**

| 规矩 | 原因 |
|---|---|
| **动作前重爬目录** | 你的库会变（一次任务里从 3995 篇变成 4034 篇）。重爬 0.1 秒、零 token，是唯一能发现你刚建目录的办法 |
| **只认库里已有的路径** | 建议里的目录必须与刚扫出来的清单匹配；不在清单里就丢掉（或问你），绝不写一个"看起来合理"的路径 |
| **主题优先** | 先定大分支（集成光子学 / 微波光子 / 低维材料…），再在分支下选层。泛词（integrated、receiver、circuit）是弱证据，误推多由此产生 |
| **多目标 = 每处一份** | 填几个数字就放几份。实现必须是"除最后一个外先复制、最后一个才移动"，否则第一个 move 就把源吃掉 |
| **同名冲突绝不覆盖** | 比 SHA256：相同则跳过，不同则加 `__dup2` |
| **推送后清理任务区** | 移动走的不留在任务区；删前先确认库里有同 SHA 的副本 |

---

## 目录职责（推荐）

```
<项目>/
├── 01-下载/            本轮下载产物 + 原始清单
├── 02-解析/            解析输出（每篇一份 md + 汇总 json）
├── 03-move/            建议表 + 执行日志
├── lib-map/            你的库目录清单、扫描数据、词表
├── PDFs/               项目内权威合集（跨轮次）
├── archive/            历史留痕（日志、旧计划、历史轮次）
├── tools/              本仓库 scripts/ 的副本（便于在项目内直接跑）
├── manifest.tsv        权威清单（含 sha256 / 页数 / 归位去向）
├── 任务总结表.xlsx      每轮任务的实时状态（人看）
└── 全局任务简报.md      跨轮次累积（未完成原因、人工推荐）
```

---

## 脚本怎么用：三类，别重复抄一份

实测过一轮任务里脚本会散落成三份并**版本分叉**（skill 一份、任务目录一份、解析引擎又来一份，其中 4 个同名文件内容已经不同）。
所以明确分三类，各归其位：

| 类别 | 判断标准 | 有哪些 | 下一轮怎么办 | 放哪 |
|---|---|---|---|---|
| **① 通用工具（直接复用，不改）** | 换人、换库、换机器都成立 | `check_env.py`（环境自检）、`check_portable.py`（可移植性检查）、`cjk_font.py`、`pdf_identity_check.py`、`extract_pdf_text.py`、`annotate_papers.py`、`parse_papers.py`、`lib_scan.py`、`make_vocab.py`、`suggest_dirs.py`、`lib_move_v2.py`、`ingest_manual.py`、`new_round.mjs`、`cdp.mjs`、`cdp_resolve.mjs`、`cdp_open_url.mjs`、`browser_pdf_downloader.mjs`、`paths.mjs`、`start-debug-browser.ps1` | 原样调用；库根、项目根都用参数或环境变量传入 | 本仓库 `scripts/` |
| **② 可改写模板（复制一份改数据）** | 脚本骨架通用，但里面**写死了本轮的 DOI/论文名** | `batch-download.mjs`(25 条 DOI)、`ieee-download.mjs`(7)、`ieee-retry.mjs`(3)、`proquest-download.mjs`(6)、`final-pass.mjs`(7)、`oa_download.mjs`(28)、`openalex.mjs`(28)、`si-download.mjs`(5)、`summon_search.mjs`(1) | 复制到任务目录，只改数据表；**不要把改过数据的版本提交回本仓库** | 任务目录 `tools/` |
| **③ 一次性（留档，不带走）** | 只修本轮某个问题、或只为本轮做表 | 各种 `fix_*.py`、`build_task_summary.py`、`finalize_round.py`、`dedupe_*.py`、`move_clean.py` 等 | 跑完就归档；其中**通用化的部分已回灌 ①** | 任务目录 `archive/one-off scripts/` |

**想让"工作区有一个函数库"而不是每轮抄一份**，用联接：

```powershell
# Windows：把工作区的 scripts 指向 skill 的 scripts（同一份内容，不会分叉）
cmd /c mklink /J "<工作区>\scripts" "%USERPROFILE%\.dsh\skills\zju-literature-downloader-skill\scripts"
# macOS / Linux
ln -s "$HOME/.dsh/skills/zju-literature-downloader-skill/scripts" "<工作区>/scripts"
```

于是：**改一处 = 全项目生效**，任务目录只留 ② 的改写版和 ③ 的留档。

## 换电脑 / 给别人用

```powershell
# 第一条命令：环境自检（不修改任何文件，只告诉你缺什么）
python -X utf8 scripts\check_env.py
# 第二条：脚本可移植性自检（确保没有某个人的本机路径）
python -X utf8 scripts\check_portable.py
```

跨平台说明：核心逻辑是 Python + Node，**Windows / macOS / Linux 都能跑**；
差异只在三处，都已兼容：

| 差异点 | 处理 |
|---|---|
| 浏览器可执行文件位置 | `check_env.py` 按平台探测 Chrome/Edge/Chromium；`start-debug-browser.ps1` 是 Windows 版，mac/Linux 用等价命令行（见下） |
| 中文字体（插解析卡用） | `cjk_font.py` 覆盖 Win/mac/Linux 候选；都没装时自动退回 PyMuPDF 内置 `china-s`，不报错 |
| 路径分隔符 | 脚本一律用 `os.path.join` / `path.join`，不手工拼 `\` |

macOS / Linux 起带调试端口的浏览器：

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.zju-debug-browser" \
  https://libweb.zju.edu.cn/ https://webvpn.zju.edu.cn/
```

## 脚本一览

| 脚本 | 作用 | 是否依赖浏览器 |
|---|---|---|
| `start-debug-browser.ps1` | 用独立 profile 启动带调试端口的 Chrome/Edge | — |
| `cdp.mjs` / `cdp_resolve.mjs` | 直连浏览器调试端口的 CDP 客户端（3456 代理不可用时自动回落 9222） | 是 |
| `browser_pdf_downloader.mjs` / `cdp_open_url.mjs` | 在已授权页面里取 PDF 字节 / 打开带 `#!` 的 URL | 是 |
| `pdf_identity_check.py` | **身份校验**：首页文本 vs 清单标题，抓"下错论文" | 否 |
| `extract_pdf_text.py` / `annotate_papers.py` | 抽文本 / 给 PDF 生成解析卡（默认不改原件） | 否 |
| `parse_papers.py` | 结构化题录（Crossref 优先） | 否 |
| `lib_scan.py` | 扫描文献库目录树（只读元数据） | 否 |
| `make_vocab.py` / `suggest_dirs.py` | 主题词表 / 归位建议（IDF 加权） | 否 |
| `lib_move_v2.py` | 按人工批注执行归位（重爬目录 + 授权建目录 + 哈希校验） | 否 |
| `ingest_manual.py` | 人工下载的 PDF：识别 → 重命名 → 查重 → 转存 | 否 |
| `new_round.mjs` | 建本轮任务目录 | 否 |

---

## 常见问题

**下载时提示找不到 CDP 代理（3456）？**
本仓库的设计是**直连浏览器调试端口**（默认 9222），3456 只是可选的回落路径。用
`start-debug-browser.ps1` 起浏览器即可；也可用 `ZJU_CDP=proxy|direct` 强制指定。

**只用 Edge、没装 Chrome 可以吗？**
可以。脚本不绑定具体浏览器，探测顺序是 Chrome → Edge；内部页面相应换成 `edge://inspect/#debugging`。

**Summon 打开变成 about:blank？**
URL 里有 `#!`，必须整体 URL 编码后再打开（用 `cdp_open_url.mjs`，别手工拼接）。

**库里删不掉目录（拒绝访问）？**
可能是继承的 `Everyone:(DENY)(DC)` 权限。用 `icacls "<目录>" /reset /T /C` 重置后再删。
不影响放文件与删文件。

**解析出的摘要为空？**
不少出版社（Nature/IEEE）在 Crossref 不下发摘要，且摘要可能跨到第 2 页。工具默认会读到第 2 页；
仍为空时属于正常情况，可让模型读首页片段补一个"简介"，但不要编造。

**建议的目录看起来不相关？**
先补词表的"同义/别名"列（`lib-map/词表-vocab.tsv`）。只填含义帮助有限，
**别名列（论文里会出现的说法）才是关键**。

---

## 合规底线

只使用你本人已登录的机构会话；不绕过付费墙 / DRM / 验证码 / Cloudflare / 双因素；
不索取或保存任何账号密码；CAS 登录（含 authcode）由你本人完成；
不做批量滥下（建议单批 5–20 篇，并留下 manifest 审计）。
