---
name: zju-literature-downloader-skill
description: Use this skill whenever the user wants to use their own logged-in Zhejiang University Library, WebVPN, CAS SSO, 求是学术搜索, Summon, ScienceDirect, publisher, or browser (Edge/Chrome) session to legally search, download, organize, retry, and read academic PDFs and supporting information. Trigger on requests like “用浙大图书馆下载文献”, “WebVPN 下载 PDF”, “CAS 认证失败后重试下载”, “ScienceDirect 人机验证后继续”, “自动下载补充材料”, “读取全文和 supporting information”, “批量整理文献到项目文件夹”, or “导入 Zotero 后帮我读全文”.
metadata:
  compatibility: Requires a local Chromium session (Edge or Chrome) logged in by the user, remote debugging permission, and a shell with Node.js 22+ or a bundled Node runtime. Uses only user-authorized access. Claude Code may need installation under .claude/skills.
---

# ZJU Literature Downloader

This skill turns the verified workflow into a repeatable, legally scoped process for finding, downloading, and reading papers through the user's Zhejiang University Library / WebVPN access.

## 环境适配（Windows + Edge/Chrome，实测验证）

- **浏览器：Edge 或 Chrome 均可**。调试实例务必用**独立 profile 目录**（如 `%LOCALAPPDATA%\zju-debug-browser-edge`，Edge/Chrome 各自独立目录，避免 profile 串用损坏），不要动日常 profile。`scripts\start-debug-browser.ps1` 会自动探测浏览器。内部页面入口 Chrome 是 `chrome://inspect/#remote-debugging`，Edge 是 `edge://inspect/#remote-debugging`。
- **传输层**：直连 CDP `http://127.0.0.1:9222`。脚本自动探测，特殊部署可用 `ZJU_CDP` 环境变量指定。
- **运行时**：Node.js 22+（`node` 在 PATH）；Python 3 侧先 `pip install -r requirements.txt`（pdfplumber + pypdf）。
- **2026-10-12 修复的两个 bug**（已改在本仓库）：① `cdp_resolve.mjs` 直连传输对象补齐 `newTarget/navigate/info/evaluate/close/list` 方法——否则 `cdp_open_url.mjs`/`browser_pdf_downloader.mjs` 报 `t.newTarget is not a function`；② CLI 入口结束时 `dispose()` 关闭缓存的 WebSocket——否则脚本输出完成后 node 进程挂起不退出。
- **2026-10-12 端到端验证**：Edge 155 + 独立 profile 起 9222 → `cdp.mjs list` → `cdp_open_url.mjs` 打开 Summon `#!` 检索 URL（片段保留、标题正确、正常退出）→ `browser_pdf_downloader.mjs` 下载 OA 论文 `10.1038/s41467-024-49047-w`（7.3 MB，`%PDF-1.4`）→ `extract_pdf_text.py` 提取 12 页正文 → `pdf_identity_check.py` 判定 `OK, match=1.0`。注：`pdf_identity_check.py` 的 manifest **必须有 `pdf_path` 列**（按其 basename 做行匹配），缺列会全部报 `NO_MANIFEST_ROW`。

### 2026-10-13 实战新增（Micro-LED × AI 数据中心短距互连，8 条批次 8 成）

- **本机出口就是浙大校园网 IP**（实测 222.205.121.95）：Optica/IEEE 直连即有授权（页面含 "Zhejiang"/"Access provided by"），Summon 不报 `ip_not_authorized`；WebVPN 标签停在 `/login` 不影响。先验证网络位置再决定要不要 WebVPN/隧道。
- **新增三个已验证可复用脚本**（本批合计经它们拿下 5/8）：
  - `scripts/opg_fetch.mjs` — Optica 双标签签名 URL 下载（见出版商路线章节的坑位说明）；
  - `scripts/ieee_fetch.mjs` — IEEE 通用 DOI→arnumber→getPDF 下载（替代写死清单的 `ieee-download.mjs`）；
  - `scripts/sd_fetch.mjs` — ScienceDirect 文章 PDF 下载（pdfft→资产域同源 fetch，含 robot check 检测）。
- **Git Bash 三个坑**：
  1. 自带 `curl` 是 MinGW 原生构建，**写不了中文路径**（报 error 23 / 乱码）——先下到 ASCII 临时目录再 `mv`，或用 node fs 写文件；
  2. Write 工具的 `/tmp` 落在 cwd 盘根（如 `E:\tmp\`），MSYS 的 `/tmp` 是 `%TEMP%`——两者不一致，临时脚本一律用显式绝对路径；
  3. 复杂 CDP 调用别用 `node -e` 内联模板（引号嵌套必炸），写成 `.mjs` 文件再跑。
- **宿主 shell 被取消会连带杀掉调试 Edge**：重启 `start-debug-browser.ps1` 同 profile 即可恢复会话标签；但重启后**不要立刻程序化轰炸 ScienceDirect**（几步之内就会弹 "Are you a robot?"，本轮 #07 就这么触发的）。
- **用户会在轮次之间整棵移动文件夹**（本轮 `Micro-LED调研` 被移入 `学习/`，7 个 PDF 随之搬家）：写入前先验证目标目录仍存在，manifest 跟着新位置走。

### 2026-10-21 实战新增（微透镜 μLED–多模光纤耦合批次：40 条清单，29 成 11 挂）

- **"文章页先行"是过 Cloudflare 403 的通用替代路线**：`browser_pdf_downloader.mjs --url <PDF直链>` 直接打开+fetch 会 403（ACM `/doi/pdf/`、AIP `/article-pdf/` 实测），但先用 `cdp_open_url` 渲染**文章落地页**（被动过盾、拿到 clearance cookie）再同源 fetch 就能过——ACM 实测成。已沉淀为 `scripts/article_page_fetch.mjs`（支持传显式候选 PDF URL；同源探测时预读 4 字节，盾页也是 200+text/html，靠 `%PDF` 头+大小剔除）。
- **新增 `scripts/wiley_fetch.mjs`**：Wiley 全系通用 DOI 下载。关键点：doi.org 会 302 到**期刊自己的子域**（`advanced.` / `sid.` 等），pdfdirect 必须从**落地 href 的 origin** 发起，硬编码 onlinelibrary.wiley.com 撞跨域。本轮 4/4（含 SID Digest 子域）。
- **各社反爬/授权实测边界**（都按纪律一次失败即停）：
  - **SPIE**：`spiedigitallibrary.org` 文章页直接落在 **`/access-suspended`** —— 是机构订阅被暂停，不是反爬；记 waiting 等订阅恢复，Summon 同源无解（同一授权）。
  - **Optica**：验证码冷却很慢，实测约 40 分钟后重试仍立刻再中；触发过一次后当天别再程序化碰 viewmedia。OFC 等会议论文 viewmedia 拿不到签名 URL 是**无授权**（非反爬），直接判、不重试。
  - **RSC**：新版站点（`pubs.rsc.org/<刊码>/article-abstract/...`）页内**没有任何 .pdf 锚点**；文章页过了盾之后同源 fetch 任何 articlepdf 变体仍返回 ~156KB 的 perfdrive（Radware）挑战 HTML，旁开 `validate.perfdrive.com` 标签。fetch 被盾主动接管，停手。
  - **AIP**（Silverchair 架构）：`/article-pdf/` 直链 403；文章页无 .pdf 锚点（按钮 JS 渲染）；页内 fetch 大文件易 `Runtime.evaluate` 超时。OA 文章把 Unpaywall 的 PDF 直链写进目录待人工浏览器下载。
  - **IOP**：`/article/<doi>/pdf` 返回 HTML 挑战页，同停。
- **顺利路线回顾**（本轮零失败）：IEEE 8/8（含 ICEPT/NUSOD 会议论文）、Elsevier `sd_fetch` 3/3（先 `cdp_open_url` 开文章页等 8s 出授权横幅，篇间歇 15s，无 robot check）、Nature 2/2、MDPI 2/2、OET（oejournal）直连 curl 成。
- **Nature 授权显示误导**：文章页 `access:false`（页面 JS 检测不到），但 `nature.com/articles/<id>.pdf` 照样给全文（IP 授权静默生效）——别被 access 字样吓退，下完核对页数即可。
- **MDPI 两条直链互有成败**：`mdpi-res.com` CDN 直链与 `www.mdpi.com/<issn>/<vol>/<iss>/<art>/pdf?version=<ts>`（Unpaywall 给的）都试，本轮 Micromachines 走 CDN 成、Photonics 走 CDN 败但 mdpi.com 带浏览器 UA 的 curl 成（首轮 403，重试即过，属瞬时限流）。
- **老 IEEE 扫描件首页陷阱**：PNAS 式装订下 PDF 第 1 页可能是**上一篇的结尾页**（Nakagawa 1993 实测），identity check 仍会 OK；若首页文本可疑，看第 2 页的 running header（`<作者> et al.: <标题大写>`）再定。
- **`pdf_identity_check.py` 的 manifest 表头必须同时含 `title` 和 `id` 两列**才被当表头识别（只有 `pdf_path` 会全报 `NO_MANIFEST_ROW`）——本轮踩过，表头加 `id` 列即好。
- **临时 node 脚本 import 盘符路径**：ESM 静态 `import "C:/..."` 报 `ERR_UNSUPPORTED_ESM_URL_SCHEME`；用 `await import(pathToFileURL(p).href)` 动态导入，或把脚本直接放进本 skill `scripts/` 用相对导入（本轮后者的两个脚本已沉淀）。
- **CDP 杂项**：`/json/close/<id>` 只认**完整 tab id**（8 位前缀无效）；Git Bash 循环里 `"E:\tmp\...\$name"` 的 `\$` 被转义成字面 `$`——路径一律用正斜杠 `"E:/tmp/.../$name"`。

### 2026-10-22 实战新增（微透镜参数引文追踪轮：25 条清单，21 成 3 挂 1 备用）

- **Optica 大批量可行，关键是条间隔**：本轮 `opg_fetch.mjs` **9 连发**（AO×7 + OE×2），条间隔 25 s，**零验证码**——对比 10-21 轮高频触发。批量跑 Optica 时条间隔 ≥20 s，一旦中招仍按 10-21 纪律当天停手。
- **ScienceDirect 首次触碰即弹人机验证，搁置可自解**：本轮唯一 SD 条目在 `cdp_open_url` 打开文章页时就中 "Are you a robot?"（比 10-13 的"重启后轰炸才中"更敏感）。搁置约 25 分钟后**从文章页重进**再跑 `sd_fetch` 一发成——再次验证"搁置约半小时自行解除"，重进走文章页、别直连 pdfft。
- **OUP（academic.oup.com）**：`article_page_fetch.mjs` 落地页正常（无盾），但同源 fetch `article-pdf` 链接报 `Failed to fetch`（疑似 Content-Disposition 触发原生下载接管）；改用 `browser_pdf_downloader.mjs --url <article-pdf直链>` 直开即成。OUP 是软柿子，两条路总有一条通。
- **Hindawi 已整体迁 Wiley**：老域名 `downloads.hindawi.com` 直链现在返回 HTML 壳；`10.1155/*` DOI 照常落 `onlinelibrary.wiley.com`，**`wiley_fetch.mjs` 直接可用**（本轮 IJO 2010 实测成）。
- **MDPI 主站 411 字节 HTML = 瞬时限流**：`www.mdpi.com/.../pdf?version=` 首轮拿 411B HTML 时别硬重试，直接换 `mdpi-res.com/d_attachment/<journal>/<journal>-<卷>-<文章号>/article_deploy/<journal>-<卷>-<文章号>.pdf` CDN 模式 URL（本轮 Photonics、Nanomaterials 两条全走 CDN 成；文章号=DOI 尾段）。
- **Springer 老刊直链 curl 可用**：1975 年 J. Electron. Mater. 走 `link.springer.com/content/pdf/<doi>.pdf` 校网 IP curl 直接 200（机构授权静默生效，同 Nature 的表现）。
- **机构库镜像陷阱**：大学仓库（UCC CORA `cora.ucc.ie/bitstreams/<id>/download`）curl 拿到的是 755 字节 HTML 壳，不是 PDF——Unpaywall 给的绿色 OA 镜像链别耗时间，直接走出版商官源（本轮该篇 IEEE 官源一发成）。
- **老扫描件 identity check 依旧要人工过目**：1983 IEEE CHMT 扫描件首页 OCR 质量差但 identity check OK——拿不准时抽正文页核对标题。
- **引文追踪型清单从哪来**：本轮 25 条由 literature-survey skill 的种子被引回溯 + 关键词检索产出（`fetch_candidates.py` → 429 候选 → 人工筛 25 → `verify_dois.py` Crossref+Unpaywall 核验），再回到本 skill 按出版商分批下载——两个 skill 的分工见 literature-survey SKILL.md §0b。

### 2026-10-22 下午实战新增（补新轮：26 条清单，19 成 7 挂，H 批）

- **ACS（pubs.acs.org）是软柿子，走"文章页先行"一发即中**：`article_page_fetch.mjs "https://pubs.acs.org/doi/<doi>" "<out>"`——落地页渲染后同源 fetch 页内的 `/article-pdf/...` 直链直接 200 `application/pdf`。本轮 2/2（ACS Nano 金 OA + ACS Photonics 订阅文），无 Cloudflare、无人机验证。ACS 支持信息按既有 `/doi/suppl/` 模式另行取。
- **IOP 结论要分新老**：2026-10-21 挂掉的是 2009 年老刊订阅文；本轮 2023 gold OA（JPhys Photonics roadmap）用 `article_page_fetch.mjs "<文章页>" "<out>" "https://iopscience.iop.org/article/<doi>/pdf"`（第三个参数传显式候选 PDF URL）一发成。以后 IOP 先按新路线试一次再判死。
- **SD 书章（book series chapter）是另一套坑**：落地页是 `/science/chapter/bookseries/abs/pii/<PII>`，把 `/science/article/pii/<PII>` 输进去会被 302 弹回 chapter 路径；章页内**没有任何 pdfft 锚点**（只有"View full text"锚回 article 路径，点了也弹回）；手工构造 `article/pii/<PII>/pdfft` 直链撞 PerimeterX 挑战（403 + 831KB 参数 HTML）。`sd_fetch.mjs` 已补 chapter 路径的标签匹配（授权横幅检测可用），但书章没有 pdfft 可导航——**书章条目预期直接转人工**，别烧路径变体。
- **PMC/EPMC 的 OA 备份路线基本失效（2026-10-22 起）**：`europepmc.org/articles/<PMCID>?pdf=render` 现在落 Cloudflare "Just a moment"；`pmc.ncbi.nlm.nih.gov/.../pdf` curl 得 JS 跳板页（"Preparing to download..."）、调试浏览器页内 fetch 得 Cloudflare 挑战 HTML——curl/fetch 路全堵。PMC 备份改人工浏览器点击下载，或直接走出版商原发路线。
- **light-am.com（Light: Advanced Manufacturing，长春光机所）可纯 curl**：`https://www.light-am.com/article/pdf/preview/<ID>.pdf`（ID 形如 `LAM2024010011`）。拿法：`curl -sL https://doi.org/<doi>` 落 `/article/doi/<doi>`，页面 HTML 里 grep `article/pdf/preview/` 即得 ID。首次 0 字节别判死，重试即成（瞬时限流，同 MDPI 脾气）。
- **Optica viewmedia 的 uri 段（`ol-<vol>-<iss>-<page>`）不能猜**：先 `cdp_open_url` 走 `doi.org/<doi>` 拿到真实 `abstract.cfm?uri=...` 再拼 viewmedia URL。本轮猜错一次 + 随后一次正确尝试即触发验证码——错误 URI 的访问可能助推风控；一旦见 `/captcha/` 当天停手不变。
- **HAL（hal.univ-grenoble-alpes.fr）镜像有 bot 墙**（"Making sure you're not a bot!"），curl 得 HTML 壳或直接失败——别耗在镜像，IEEE/出版商官源走起。
- **增量 identity check 的噪音甄别**：`--manifest` 只给本轮清单时，库内旧文件全部报 `NO_MANIFEST_ROW` 属预期；顺带留意混进来的非论文 PDF（本轮用户并行放入 3 份厂商资料，首页文本一眼可辨，不要动）。
- **长流程脚本的 cwd**：ZCode 的 Bash 每次调用会重置 cwd（输出末尾有 "Shell cwd was reset" 提示）——多步脚本引用 verify.tsv / manifest 等中间文件一律显式绝对路径，别依赖上一条命令的 cd。

### 2026-10-22 晚实战新增（课题组追踪轮：58 条清单，12 成 46 挂——无校园网出口特情）

- **🔴 开工必验出口 IP，TUN 代理会静默毁掉一切授权**：本轮开跑前 `curl -s https://ifconfig.me` 返回香港 IP（用户开了 TUN 模式机场，国际流量整体接管）——出版商看到的是境外数据中心 IP，授权全灭。上午还在校园网（G/H 轮全成），下午/晚上环境就变了。**每次下载轮开工先验出口**；若非 222.205.x（浙大）或 WebVPN，直接按"无授权模式"走：只下 OA/绿色镜像，付费墙一律不硬试、记 pending。Debug Edge 的 `-Proxy` 也救不了（见下条）。
- **aTrust 隧道（zju-connect）的本地代理不提供出版商授权（实测更正）**：服务器上 `zju-connect` 服务 active ≠ 能访问出版商——其 `-http-bind 127.0.0.1:8888` 本地代理的出口是**服务器公网 IP**（实测即服务器公网出口 IP），只代理校园内网资源。本文件早前"-Proxy 让出版商看到浙大出口 IP"的表述对当前 zju-connect 配置（仅 `-custom-proxy-domain myarts.zju.edu.cn`）已失效。无校园网时的合法出路只剩：①OA 直链；②arXiv/机构库绿色镜像；③用户人工登录 WebVPN（CAS 会话不跨天，别指望 profile 里留着）。
- **OpenAlex/Unpaywall 的 `oa=True` 的 IEEE 可能是真 OA，别直接跳过**：本轮 TCPMT（10.1109/tcpmt.2025.3531206）、JLT（10.1109/jlt.2025.3563232）无校园网走 `ieee_fetch.mjs` stamp 路线照样拿到全文（OA 文章不校 IP）。先 Unpaywall 复核再决定 IEEE 条目的死活。
- **light-am.com 的预览 ID 不能按 DOI 尾段猜**：`10.37188/lam.2024.049` 的真实 ID 是 `LAM2024050041`（猜 `LAM2024030003` 得 0 字节）。必须 `curl -sL https://doi.org/<doi>` 落文章页后从 HTML grep `article/pdf/preview/`。
- **MDPI CDN 四位文章号模式 3/3**：`mdpi-res.com/d_attachment/photonics/photonics-12-01037/article_deploy/photonics-12-01037.pdf`（文章号 = DOI 尾段四位补零）无校园网可下，与 Unpaywall 给的 `www.mdpi.com/.../pdf?version=` 二选一。
- **IOP 的 gold OA 刊与混合 OA 要分开**：JPhys Photonics（纯 gold OA）`article_page_fetch + 显式 /pdf` 候选**无校园网也成**（本轮 4.1MB 一发中）；JPhys D / JJAP（混合 OA）同法 `/pdf` 返回 HTML（需订阅权益），Unpaywall 的 oa=True 在此会误导。
- **机构库镜像不是永远可达**：`biblio.ugent.be` 在 TUN 代理出口下 **TLS 握手直接被掐**（curl/python/浏览器三路一致，疑似代理节点按 SNI 拦截）；`research.tue.nl/files/` 拒数据中心 IP（403，浏览器同罪）。镜像挂了就走出版商原源，别反复试。
- **De Gruyter（degruyterbrill.com）的 /pdf 端点拒程序化 fetch**：文章页正常渲染（无盾），但 `/document/doi/<doi>/pdf` 同源 fetch 与 browser_pdf_downloader 都返回 **405**（要求真实导航的下载流）——`article_page_fetch` 与 `browser_pdf_downloader` 都不行，转人工或 curl 老域名（老域名 `www.degruyter.com` 亦 405）。
- **SD 对境外数据中心出口可以持续 Cloudflare 挑战**：本机校园网时的经验"搁置约 25–30 分钟自解"在境外出口下不成立——实测 28 分钟后从文章页重进仍是 "Just a moment..."。出口不对时 SD 直接记 pending，别烧重试。
- **Wiley pdfdirect 对可疑出口 403，重试有救但有上限**：LPR（onlinelibrary 主域）第 2 次重试成；`advanced.` 子域两条各 403×2–3 后仍挂——**同一条目最多重试 2 次**，不行就记 pending（换网络即下）。注意 wiley_fetch 超时（>2min）不等于失败，先查产物文件再决定是否重跑。
- **Optica Open 预印本是 OE/OL 新文的合法备份源**（本轮记录未用）：`preprints.opticaopen.org/articles/preprint/<id>` 可 curl，出版商版挂起时先查这里和 arXiv。

### 2026-10-22 夜·校园网恢复后的批量补下（挂起 46 条实测）

- **🔴 `sd_fetch.mjs` 第一个参数是 PII 不是 DOI**（用法注释里写了，很容易看漏）：传 DOI 会报"未找到文章页标签"。PII 从 Crossref `alternative-id` 字段拿（`api.crossref.org/works/<doi>`），形如 `S0030399225005316`。本轮 PII 修正后 4/4（OLT/Results in Optics/OFT/SNA）。文章页先用 `cdp_open_url` 打开（campus 下 doi.org 直落文章页，无 Cloudflare），再跑 sd_fetch。
- **Wiley pdfdirect 的 403 = 首触挑战，隔 30–40 秒重试一次即过（campus 下 4/4）**：lpor/adom/adfm/sdtp(SID 子域) 全是"第一发 403 → 第二发成"。之前"advanced 子域 403×3 仍挂"是境外出口的问题，不是子域规则。**同一条目第一发 403 后必重试一次再判死**。
- **IOP 混合 OA 在有校园权益时 article_page_fetch+显式 /pdf 照样成**：JPhys D 大综述（7MB）与 JJAP 2000 老文都拿下——白天"混合 OA 不行"的结论只在无授权出口下成立，有授权时混合 OA 就是普通订阅文。
- **IEEE arnumber 正则偶发失灵**（10.1109/50.54482 页面 `arn:null`）：兜底路线 = `article_page_fetch.mjs "https://ieeexplore.ieee.org/document/<docId>" "<out>" "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=<docId>&ref="`，document 页同源 fetch stampPDF 直链一发成（本轮 Karstensen 1990 实测）。docId 就是 URL 里 `/document/<id>` 的数字。
- **biblio.ugent.be 对国内网络整体不可达**（校园网直连与代理出口都握手失败）——不是代理 SNI 问题，是该站对 CN 网段的可达性问题；别再在镜像上烧时间，ugent 镜像条目一律回 IEEE 官源。research.tue.nl 的 /files/ 校园网 curl 仍得 403/HTML（有 referer 也没用），同样回官源。
- **AIP（pubs.aip.org）校园网依旧拦**：文章页能渲染但 PDF 锚点 JS 渲染/页内 fetch 被接管，与 10-21/22 结论一致——AIP 全系（含 OA）在无人工浏览器时判 pending。
- **De Gruyter 405 与出口无关**（campus 下同样 405）：`/document/doi/<doi>/pdf` 端点要求真实导航下载流，程序化路线全堵，转人工。
- **zju-connect 最终结论（排障记录）**：`--proxy-all` 标志存在且可登录，但 aTrust 隧道对互联网流量的出口是**浙江移动线路（39.174.144.35）**，出版商不认；HTTP 代理（8888）模式下非校内流量甚至直接黑洞。**zju-connect 只适合校内资源，出版商授权的离线替代是 WebVPN**（公共门户，浏览器登录一次）。服务已恢复原配置，备份在服务器 `/root/zju-connect.service.bak.20261022`。
- **🔴 Optica viewmedia URL 里的 `&html=true` 必须剥掉**：doi.org 对 OE/AO/OL 老文直接落在 `viewmedia.cfm?uri=<id>&html=true`——带 `html=true` 时页面渲染 HTML 版全文、**不跳签名 PDF**，opg_fetch 轮询 `directpdfaccess` 必超时（本轮 8 条全这么挂的，剥掉后同 URL 一发即中）。新文（OE 近年）则落 `fulltext.cfm?uri=<id>`，把 `fulltext` 换成 `viewmedia` 即可用。落地页是 `abstract.cfm?URI=OFC-...`（大写 URI、无参数尾巴）的会议文，手工拼 `viewmedia.cfm?uri=<小写id>` 也行，但 OFC 会议 proceedings 多半直接"未拿到新签名 URL"=无授权（与 F2/F3 先例一致），AO 1982 级别的老档案同样不在订阅内——判死别重试。
- **`opg_fetch.mjs` 第一参数是 viewmedia URL 不是 DOI**（用法注释在脚本头部）：拿到 DOI 先 `cdp_open_url` 走 doi.org 拿真实落地 URL 再拼 viewmedia。
- **viewmedia 连续命中 20+ 次后签名 URL 会停发**（本轮 OE/AO/OL 混合批量 ~20 连发后，后续全部"未拿到新签名 URL"且 href 停在 `viewmedia.cfm?r=1&rwjcode=xx` 不动）——疑似软限流而非验证码（无 /captcha/ 页面）。**单批 viewmedia 访问控制在 15 条以内**，超了换时间再来；没有验证码就不算触发"当天停手"纪律，可以次日续。
- **CDP evaluate 在页面导航期会抛 timeout 崩掉 opg_fetch**（`processTimers` 栈）——批内偶发 1–2 条这样死的，隔半分钟原样重跑即可（URL 没错就别改参数）。

### 2026-10-23 实战新增（第五轮补新：22 条清单 22 成；SPIE 订阅恢复 + Incapsula 新坑）

- **🔴 SPIE 订阅恢复了，但换成了 Incapsula（Distil）挑战**：文章页不再 302 `/access-suspended`（2026-10-21~22 的"订阅暂停"已解除，F4 单发即成）——但 **12 秒间隔连发约 6 条后 WAF 开始发 ~1KB 的 Incapsula HTML**（含 `distil_referrer`/`SWUDNSAI` 字样），curl/浏览器同拦，冷却 25 分钟后单发仍被拦。结论：SPIE 可下了但**条间隔必须 ≥60s 且单批 ≤4–5 条**；落盘前必须校验 `%PDF` 头（本轮 junk 文件差点混进库，已加 NOT_PDF gate）。
- **🔴 SPIE 裸 DOI 路径拿不到文章页**：`/conference-proceedings-of-spie/10.1117/12.xxxx` 会 302 到 proceedings **浏览 landing 页**（title="Proceedings"，无 PDF 锚点）。正确链路：`cdp_open_url` 走 `doi.org/<doi>` → 落地 **带卷号/文章号的 `.full` URL**（如 `/conference-proceedings-of-spie/13903/139030T/<标题slug>/10.1117/12.3079631.full`）→ 页内 `meta[name="citation_pdf_url"]`（或 `.full`→`.pdf` 替换）→ 同源 fetch。本轮沉淀的 `spie_fetch.mjs`/`probe_anchors.mjs`（探 `citation_pdf_url` 与 PDF 按钮的通用探针）在该轮目录：`课题/Micro-LED/文献/原始材料/文献自动下载/rounds/2026-10-23T1040_round5/`，验证可复用后可再提升进 `scripts/`。
- **Optica"未拿到新签名 URL"要分三态，别一刀切**（本轮 18 次尝试的实测）：① **老档案无订阅**（AO 1980/1982、OL 1986，href 停在原址）→ 判死转人工；② **CLEO/OFC 会议无授权**（同 href 停滞）→ 判死；③ **近期期刊文偶发 token 失败**（OE/OL 近两年的正常订阅文也可能碰上）→ **隔 30s 原样重跑一次**，本轮 5 条复跑成 3（F15 复旦 OL、F58 根特 OE、N7 OE 自由曲面）。判别只能靠"同卷期兄弟条目是否成功"试出来。
- **viewmedia 软限流的次日恢复**：昨日 20+ 连发停发，今日首条即正常——"单批 ≤15、次日续跑"的纪律实测有效；本轮全天 20 次 viewmedia 尝试未触发验证码。
- **新增 `resolve_optica.mjs` 模式**（本轮轮次目录）：`cdp_open_url` 走 doi.org → **前后标签列表 diff 找新开的 opg 标签**（比"取最新的 opg 标签"稳，旧标签不干扰）→ 落地 URL 正则抽 uri 段 → 剥 `html=true` 拼 `viewmedia.cfm?uri=<uri>&seq=0` → 用完关标签。注意个别 2026 新文落地是 `abstract.cfm?doi=10.1364/OE.610722` 形式（无 uri 段）——此时重开一次多半能落到 uri 形式，仍不行就记 pending。
- **Optica Open 预印本备份今天失效**：`preprints.opticaopen.org` 昨天还能 curl（10-22 记录），今天 WAF（awselb）对 curl 和调试浏览器都 403——该站不保证可达，别把它当可靠备份，预印本条目回出版商原源或记 pending。
- **manifest 追加行的列序必须对齐既有表头**：`csv.DictWriter` 按**自己的 fieldnames 顺序**写物理列，若与主 manifest 表头列序不同（如表头是 `id,project,title,doi,...` 而追加用 `id,project,doi,title,...`），新行 doi/title 全部装反——identity check 会因"清单标题=DOI"全线误报 SUSPECT。追加前读表头、按表头顺序写；出事后最稳的修复是**从源 verify/retry TSV 全量重建本轮行**（别逐列打补丁，越补越乱）。
- **IEEE 三种会议文（OECC/IPC/CLEO-Europe）`ieee_fetch.mjs` 照常 3/3**：CLEO/Europe 虽是 Optica 会议但 IEEE Xplore 也收录，走 IEEE 路线反而拿下（同日 Optica 侧 CLEO_AT/CLEO_SI 全部无授权）——**会议文先查 IEEE Xplore 有没有双收录**，再碰 Optica viewmedia。

### 2026-10-24 实战新增（第六轮补强：24/25 成；APS 新路线 + SPIE 会话标记跨天）

- **🔴 SPIE 的 Incapsula 会话标记会跨天持续**：昨天触发过挑战的调试浏览器 profile，今天**第一条**（65s 间隔、无前置连发）就被拦（~1KB Incapsula HTML）——不再是"连发 ~6 条才中招"。`%PDF` 硬门全部拦住未落盘。结论：**SPIE 挂起条目重试要隔 2–3 天**（而非次日），或换 profile/人工；别把"今天一天一条慢慢试"当指望。
- **🔴 known 去重源必须含"目录文档全量 DOI"**：manifest 建档**之前**的历史批次（本库 A–D/E 组 40 篇，2026-10-21 下载）从未写入 manifest，按 manifest 建 known 会漏——本轮首轮去重就漏了同文候选（Song 2026 SID）。修复：用正则 `\[(10\.\d{4,5}/[^\]\s\)\|]+)\]` 从 `00-文献目录.md` 提取全部 DOI（182 条）并入 known。**治本**：应把历史批次回填进主 manifest。
- **APS（journals.aps.org）是软柿子，但 curl 不行、浏览器行**：`curl journals.aps.org/prapplied/pdf/<doi>` 失败（文件不落盘），而 `browser_pdf_downloader.mjs --url "https://journals.aps.org/<journal>/pdf/<doi>"` 校网 IP 一发即中（PR Applied 2026 实测 2.5MB）。APS 无 Cloudflare 拦截，此前从未试过这条路。
- **SD 人机验证在校园网下也可能不自解（首例）**：首次触碰文章页即中招，搁置 30 分钟后从文章页重进**仍中招**——与 10-22"搁置约 25–30 分钟自解"的经验相反。判别失败后**再试一次即停**，记 pending 人工；别套用"半小时自解"的旧经验硬等。
- **oejournal 系（OEM 新刊）preview URL 变体不带 `/en/`**：OET 是 `oet/en/article/pdf/preview/<doi>.pdf`，OEM 是 `oemanuf/article/pdf/preview/<doi>.pdf`（无 /en/）——同族不同刊路径不一，一条 404 就试另一条；oejournal 全系 curl 直下依旧稳（本轮 OET+OEM 2/2）。
- **Optica "文章太新"第四态：三试无 uri 段即弃**：`abstract.cfm?doi=10.1364/OE.610722` 形式（无 uri 段）的文章，resolve 重开 + 直开文章页各试一次后页面确实无 uri 段（页内 uri 链接全是推荐文章的）——这是第四态，与前轮"重开一次多半能落到 uri 形式"不同；三试仍无 uri 段记 pending，别烧第四次。当天 9 连发 viewmedia（25s 间隔）零验证码零软限流，含前日挂起的 2 条全部补下成功（"次日重试"策略再次验证）。
- **Wiley pdfdirect 本轮 4/4 首发 403 零次**：与 10-22 晚"首发必 403"不同——同一出口/浏览器下该行为并不稳定，首发成功就继续，别机械等待重试；403 时再按"隔 30–40s 重试一次"处理。
- **作者消歧新招：已知论文 DOI → authorships 反查 author id**：OpenAlex 姓名搜索对 Strain/Dana/Le Maitre/Kośnic/Furman/Herrnsdorf 全部失灵（UNRESOLVED）——改从库内已知论文（如 G17 NC、H02 ACS Photonics）的 `authorships` 里按机构匹配抽 author id，3/3 成功，再拉 `from_publication_date` 之后的 works。比关键词组合猜姓名可靠得多。
- **SCIS（Science China Information Sciences）Letter 是 2 页格式**：295KB、2 页、`199303:1–199303:2` 页码，identity check 按标题匹配 OK——别按页数误判成预览片段删掉。
- **OEM 类新刊 PDF 的连字编码会让 identity check 假阳性**：Opto-Electronic Manufacturing 的 PDF 首页文本抽出是 CID 三重化字符（`fffemmtt...`），token 匹配 0.0 报 SUSPECT——PyMuPDF 重新抽取或人工看首两行即可辨真（标题其实吻合）。SUSPECT 先复核再判死。


### 校网 / 非校网（结合 zju-atrust-vpn skill）

| 场景 | 做法 |
|---|---|
| 校内网 | 直接用：Summon/出版商看到浙大 IP 段，自动授权 |
| 校外（首选） | 调试 Edge 里人工登录 **WebVPN**（`https://webvpn.zju.edu.cn/`，CAS 账号密码），走 WebVPN 改写 URL 访问资源；本 skill 的 Summon/出版商流程本身按 WebVPN 设计 |
| 校外且 Summon 报 `ip_not_authorized`（"IP is outside the authorized range"） | **⚠️ aTrust 隧道路线已实测失效（2026-10-22 晚）**：zju-connect 的本地代理出口是服务器公网 IP，不透传浙大授权，`-Proxy` 不能再解决出版商授权。现在可行：①校网直连；②WebVPN 人工登录；③OA/绿色镜像路线。隧道仅用于校园内网资源（详见 zju-atrust-vpn skill 与 2026-10-22 晚实战新增） |
| 隧道没起就带了 `-Proxy` | 浏览器所有请求直接失败——先确认 SSH 隧道窗口开着、服务器代理探活 `curl -sk -x http://127.0.0.1:8888 ...` 返回 200 |

调试 Edge 的独立 profile **不共享**日常 profile 的已存密码，首次使用需在该窗口里人工登录一次浙大 CAS（含 authcode 就人工输）。

## What this skill does, in three steps

`download -> parse -> file`. The user supplies (a) a paper list (titles and/or DOIs),
(b) **the root folder of their own library** (everyone's tree differs, so it is required and has no default),
and optionally (c) a per-user preference file kept in their workspace. The skill never invents library paths:
folders are the human's, and a new folder is created only when the human explicitly asks for that exact path.

See `README-使用说明.md` for the end-to-end walkthrough and the pitfalls each rule came from.

## Boundaries

Use only the user's legitimate institutional access. Do not bypass paywalls, DRM, CAPTCHA, Cloudflare, publisher bot checks, or two-factor authentication. If a page asks for CAPTCHA, QR login, SMS/OTP, Cloudflare, publisher bot checks, or a security challenge, stop and ask the user to complete it in Chrome.

Avoid mass downloading. Work in small batches, preferably after the user confirms the paper list. Leave a clear audit trail of what was downloaded, from where, and whether supporting information was found.

Do not ask the user to paste institutional passwords, CAS credentials, OTP codes, recovery codes, or session tokens into chat or terminal. If the user offers a password, decline and use the handoff-login workflow instead.

Exception for ZJU CAS saved-login pages: if the user explicitly says that Chrome has already filled the ZJU CAS credentials and authorizes clicking the login/confirm button, the agent may click that button once on the ZJU CAS/WebVPN/institutional SSO page without reading, copying, or typing any credential. This exception does not apply to CAPTCHA, QR login, SMS/OTP, publisher bot checks, or any page outside the expected institutional login flow.

Do not inspect or export cookies, passwords, local storage, browser profiles, or session files. Use the browser's already-authenticated page context only.

## Preconditions

Before attempting downloads, confirm these conditions:

1. A Chromium browser is open on the user's machine (Edge or Chrome both work).
   On this machine use **Edge** (user preference; a user-level Chrome also exists but
   is not used for this skill). `scripts\start-debug-browser.ps1` prefers Edge.
2. The user has personally logged in to Zhejiang University Library / WebVPN in Chrome.
   - Common pages: `https://libweb.zju.edu.cn/`, `https://webvpn.zju.edu.cn/`, `https://zju.summon.serialssolutions.com/`.
3. Chrome remote debugging is allowed for the current browser instance.
   - Ask the user to open the matching internal page: `edge://inspect/#remote-debugging`
     (Edge) or `chrome://inspect/#remote-debugging` (Chrome).
   - They must enable `Allow remote debugging for this browser instance`.
4. The environment can run Node.js 22+.
   - Try `node --version` first (this machine has `C:\Program Files\nodejs\node.exe`).
   - Only if `node` is absent from PATH, fall back to a bundled runtime such as
     `%LOCALAPPDATA%\OpenAI\Codex\bin\node.exe`.
5. A CDP transport is available: the web-access proxy, OR a browser debug port.
   - Proxy route: `%USERPROFILE%\.claude\skills\web-access-main\scripts\check-deps.mjs`,
     `%USERPROFILE%\.agents\skills\web-access-main\scripts\check-deps.mjs`, or
     `%USERPROFILE%\.codex\skills\web-access-main\scripts\check-deps.mjs`.
   - **Direct route (verified on this machine): the proxy is NOT installed and port 3456 is closed.**
     Start a browser with a separate debug profile instead:

     ```powershell
     # launches Edge or Chrome with --remote-debugging-port=9222 on an isolated profile
     powershell -ExecutionPolicy Bypass -File "<skill>\scripts\start-debug-browser.ps1" -Port 9222
     node "<skill>\scripts\cdp.mjs" list
     ```

     `scripts\cdp_open_url.mjs` and `scripts\browser_pdf_downloader.mjs` auto-detect the transport:
     they probe the proxy first, then fall back to the debug port. Force one with
     `ZJU_CDP=proxy|direct`, and change the port with `ZJU_CDP_DIRECT=http://127.0.0.1:<port>`.
     A separate `--user-data-dir` is mandatory: current Chrome/Edge builds ignore
     `--remote-debugging-port` for the default profile.
6. The user has approved the target output folder.

Install or copy this skill bundle to the skill root your agent scans:

```powershell
$env:USERPROFILE\.dsh\skills\zju-literature-downloader-skill     # DeepSeek Harness (this machine)
$env:USERPROFILE\.claude\skills\zju-literature-downloader-skill  # Claude Code
$env:USERPROFILE\.codex\skills\zju-literature-downloader-skill   # Codex
```

Treat those roots as install targets for different agents, not as different skill versions.
Keep exactly **one** real copy (this machine uses `.dsh`) and make the others junctions to it,
so a fix never has to be applied two or three times.

## Batch Scope

Small batches are supported when the user provides a definite DOI/title/PMID list.

Recommended limits:

- normal batch: 5-10 papers
- upper practical batch: 15-20 papers, with pauses and a manifest
- stop immediately if publisher checks, CAPTCHA, WebVPN expiry, or unusual download prompts appear

Do not turn a broad keyword search into unlimited automatic downloading. Do not download whole journal issues, volumes, or large result sets.

## Status Categories

Classify every paper into one of these statuses, and keep the status in the manifest:

```text
downloaded
downloaded_with_si
cas_waiting_user
cas_resolved_retry_needed
publisher_verification_waiting_user
sciencedirect_robot_check
retry_after_user_verification
do_not_auto_retry
url_needs_repair
summon_no_link
publisher_blocked_waiting_user
no_authorized_pdf_found
failed_after_retry
ip_not_authorized
purchase_required
```

Use `cas_waiting_user` only when the browser is visibly at Zhejiang University CAS / unified identity authentication or an equivalent institutional SSO step. Do not treat this as a final failure.

Use `publisher_verification_waiting_user` or `sciencedirect_robot_check` when a publisher page shows "Are you a robot?", CAPTCHA, Cloudflare, bot verification, or another anti-automation challenge. Do not treat this as a final failure, but do not try to solve it automatically.

## Start Chrome Control

Use the web-access CDP proxy when available.

On Windows PowerShell:

```powershell
$node = "node"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  $node = "node"  # this machine has C:\Program Files\nodejs\node.exe on PATH
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  $node = "$env:LOCALAPPDATA\OpenAI\Codex\bin\node.exe"  # only if node is truly absent
}
}
$checkDepsCandidates = @(
  "$env:USERPROFILE\.claude\skills\web-access-main\scripts\check-deps.mjs",
  "$env:USERPROFILE\.agents\skills\web-access-main\scripts\check-deps.mjs",
  "$env:USERPROFILE\.codex\skills\web-access-main\scripts\check-deps.mjs"
)
$checkDeps = $checkDepsCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $checkDeps) { throw "web-access-main/scripts/check-deps.mjs not found" }
& $node $checkDeps
```

Then test:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:3456/targets" -TimeoutSec 10
```

If this hangs or fails:

- Ask the user to confirm the remote debugging checkbox.
- Check `%TEMP%\cdp-proxy.log`.
- Do not attempt to read Chrome session files.

## Core CDP Proxy API

The usual web-access proxy is `http://127.0.0.1:3456`.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Proxy health and Chrome connection status |
| `/targets` | GET | List open Chrome tabs |
| `/new?url=...` | GET | Create a new background tab |
| `/navigate?target=...&url=...` | GET | Navigate an existing tab |
| `/close?target=...` | GET | Close a tab |
| `/info?target=...` | GET | Get page title, URL, and readyState |
| `/eval?target=...` | POST body=JS | Execute JavaScript in a tab and return `{ value: ... }` |
| `/clickAt?target=...` | POST body=CSS selector | Click the center of a visible element |

Use `/navigate` rather than `/new` for Summon URLs containing `#!` fragments. If a `#!` fragment is stripped, Chrome may open `about:blank` or a wrong page. If `curl` is unavailable, use PowerShell `Invoke-WebRequest` for simple checks or Node.js `fetch()` for proxy calls.

## Recommended Search Workflow

Prefer the library discovery route before direct publisher pages. It is more stable and less likely to trigger bot protection.

1. Search by DOI or exact title in ZJU Summon:
   - `https://zju.summon.serialssolutions.com/search?#!/search?pn=1&ho=t&include.ft.matches=f&l=en&q=<URL-encoded DOI or title>`
2. Open Summon URLs through `scripts/cdp_open_url.mjs` or otherwise fully URL-encode the nested URL when calling the CDP proxy. Summon uses `#!`; if the `#` fragment is stripped, Chrome may open `about:blank` or the wrong page.
3. Read the result page with `/eval`.
4. Extract links whose visible text or `aria-label` is:
   - `PDF`
   - `在线全文`
   - `Full Text`
   - `View PDF`
   - publisher-specific full-text entries.
5. Prefer the result's `PDF` link when present.
6. Open the PDF link in a new background tab through the CDP proxy.
7. If the publisher shows a security challenge, ask the user to complete it manually.
8. Once the PDF is visible in Chrome, use `scripts/browser_pdf_downloader.mjs` to save it from the authenticated browser context.

### Summon Field Notes

When extracting `PDF`, online full text, or publisher links from Summon:

1. Skip empty `href` values. The first online-full-text match can be a facet toggle rather than a real link.
2. Treat Summon as a slow SPA: wait about 6 seconds after navigation, then retry link extraction up to 3 times with short pauses.
3. If Summon redirects to `login.serialssolutions.com/sso/login` and says the IP is outside the authorized range, record `ip_not_authorized`. In that case the user may need the ZJU RVPN/SSLVPN client, not only WebVPN in a browser page, because CDP-controlled Chrome tabs may still use the local network IP.
4. If Summon has no PDF but has an online-full-text link, follow that route before constructing a publisher URL manually.

## Publisher-Specific Patterns

Use these as DOI-based repair hints only after confirming the paper identity. Do not claim access if the page or PDF is not actually verified.

### ACS Publications

For DOI prefix `10.1021/...`:

```text
https://pubs.acs.org/doi/pdf/<doi>
```

ACS supporting information often follows:

```text
https://pubs.acs.org/doi/suppl/<doi>/suppl_file/<journal-code>_si_001.pdf
```

The verified example `10.1021/acs.biomac.4c00102` used `bm4c00102_si_001.pdf`.

### Wiley

Both Edge and Chrome are normal: nothing in this skill depends on which one is installed, because every
script talks to a debug port through CDP rather than to a specific browser. Detect whichever exists
(Chrome first, then Edge), and use the matching internal page — `edge://inspect/#remote-debugging` for
Edge, `chrome://inspect/#remote-debugging` for Chrome. If the user says "用 Edge" or "use Chrome",
that is not a constraint to work around; it is just which binary gets launched.

For DOI prefixes such as `10.1002/...` or `10.1111/...`, first navigate to the authenticated Wiley article page, then fetch from that page's own origin:

```text
<location.origin>/doi/pdfdirect/<doi>?download=true
```

Do not hardcode `onlinelibrary.wiley.com`: Summon may authenticate through a subdomain such as `advanced.onlinelibrary.wiley.com`, and cross-origin fetches can fail. Also avoid navigating the tab directly to `pdfdirect`, because the browser may start a download and leave the page context unusable. Prefer page-context `fetch()`.

**自动化脚本**（2026-10-21 沉淀，4/4 实测含 `sid.` 子域）：`scripts/wiley_fetch.mjs "<doi>" "<out.pdf>"` —— 导航 doi.org 后自动取**落地 href 的 origin** 发起 pdfdirect 同源 fetch，含人机验证检测（中招退出码 9）。

### Springer Nature

For DOI prefixes such as `10.1007/...` and `10.1186/...`:

```text
https://link.springer.com/content/pdf/<doi>.pdf
```

This usually works after the institutional login is resolved. Use browser-context fetch if direct shell download returns `401`, `403`, or an HTML login page.

### Nature Communications

For OA DOI patterns such as `10.1038/s41467-...`, try:

```text
https://www.nature.com/articles/<article-id>.pdf
```

Extract `<article-id>` from the DOI suffix and still verify the PDF header and text.

2026-10-21 实测补充：**付费墙**的 nature.com 文章（Nat. Photonics、Nat. Electronics 等）同一模式照样走通——文章页上 `access` 检测可能显示 false（JS 检测不到校园网 IP 授权），但 `<id>.pdf` 会静默给全文；**下完务必核对页数**（付费墙拿不到授权时只给 1 页预览，页数一验就穿）。

### bioRxiv

For DOI prefix `10.1101/...`, try:

```text
https://www.biorxiv.org/content/<doi>v1.full.pdf
```

If version 1 returns 404, try the article page or a later version. Verify that the title matches.

### Frontiers

For DOI prefix `10.3389/...`, try:

```text
https://www.frontiersin.org/articles/<doi>/pdf
```

If it fails, open the article page first and use its visible PDF link.

### RSC Publishing

For DOI prefix `10.1039/...`, do not assume ZJU has authorized full-text access. If PDF links return 404 or purchase pages, inspect the article page and record `no_authorized_pdf_found` or `purchase_required` rather than retrying aggressively.

2026-10-21 实测（gold OA 的 Nanoscale 也一样拦）：新版站点 `pubs.rsc.org/<刊码>/article-abstract/...` 页内**没有任何 .pdf 锚点**（按钮 JS 渲染）；文章页过了盾之后，同源 fetch 任何 `articlepdf` 路径变体仍返回 ~156KB 的 **perfdrive（Radware）挑战 HTML**（`content-type: text/html`，旁开 `validate.perfdrive.com` 标签）——fetch 被盾主动接管，**停手记 `publisher_blocked_waiting_user`**，不要换第三种路径变体硬试。gold OA 文章把文章页链接写进目录待人工下载。

### AIP (pubs.aip.org, Silverchair 架构)

`/article-pdf/doi/...` 直链即使用浏览器上下文 fetch 也 403（Cloudflare）；文章落地页上**没有 .pdf 锚点**（PDF 按钮 JS 渲染）；页内 `fetch` 大文件易 `Runtime.evaluate` 超时。2026-10-21 实测 Far-UVC APL（OA）三种路都堵 → 记 `publisher_blocked_waiting_user`，把 Unpaywall 的 PDF 直链写进目录待人工浏览器下载。

### IOP (iopscience.iop.org)

`/article/<doi>/pdf` 返回 HTML 挑战页（非 PDF）。同 AIP 处理：停手，记 waiting，附文章页链接。

### SPIE (spiedigitallibrary.org)

2026-10-21 实测：文章落地页直接 302 到 **`/access-suspended`** —— 这是**机构订阅被暂停**（通常是用量触发），不是反爬，记 waiting 等订阅恢复；**不要走 Summon 重试**（同一授权源，结果相同）。SPIE proceedings 无 OA 通道。

### ACM (dl.acm.org)

For DOI prefix `10.1145/...`（以及其他 ACM 前缀）：**ACM 自 2026-01 起全面 OA**，直接：

```text
https://dl.acm.org/doi/pdf/<doi>
```

用 `browser_pdf_downloader.mjs` 打开即可拿到 PDF（实测 SIGCOMM'25 论文，无 Cloudflare、无人机验证）。

2026-10-21 补充：**同一批次里单条 URL 可能 403**（Cloudflare HTML，`<!DOCTYPE` 开头），而兄弟 URL 正常——别反复重试该直链，改走"文章页先行"：`scripts/article_page_fetch.mjs "https://dl.acm.org/doi/<doi>" "<out.pdf>"`（先渲染落地页拿 clearance cookie，再同源 fetch PDF，实测一发过）。

### MDPI

- 期刊代码（URL 里的 ISSN 段）**不要猜**：用 `curl -I https://doi.org/<doi>` 的 `Location` 头解析。实测 Photonics = `2304-6732`（猜 `2404-9450` 得 404），Nanomaterials = `2079-4991`。
- 文章页 `/pdf` 直链带 Content-Disposition：浏览器导航会触发**原生下载**（落 Downloads 的 `.crdownload`），页内 `fetch(location.href)` 必失败（`Failed to fetch` + 空壳 `downloads-hub` 标签）。
- 可 curl 的纯 CDN 直链（200，`application/pdf`，无反爬）：

```text
https://mdpi-res.com/d_attachment/<journal>/<journal>-<卷>-<文章号>/article_deploy/<journal>-<卷>-<文章号>.pdf
# 例: https://mdpi-res.com/d_attachment/photonics/photonics-12-00730/article_deploy/photonics-12-00730.pdf
```

2026-10-21 补充：**CDN 直链与 Unpaywall 给的 `www.mdpi.com/.../pdf?version=<ts>` 互有成败**（同一批 Micromachines 走 CDN 成、Photonics 走 CDN 败 548 字节 HTML 但 mdpi.com 带浏览器 UA 的 curl 成）——一条败了换另一条，别在一条上反复重试；mdpi.com 直链首轮 403 属瞬时限流，隔几秒重试即过。

### Optica (opg.optica.org) — 双标签签名 URL 法

viewmedia.cfm 先渲染 `Please wait...` 反爬中转页，再 302 到 `directpdfaccess/<token>/<file>.pdf` 签名 URL。**签名令牌被查看器渲染消耗一次**：在 PDF 查看器标签里 `fetch(location.href)` 只会拿到 text/html；从文章页跨域 fetch 资产 URL 又撞 CORS。

正确姿势（`scripts/opg_fetch.mjs`，OA 与有授权的订阅文章都走得通）：

1. B 标签**先导航再轮询**，等 href 变成 `directpdfaccess/...`；轮询前先记录旧 href 并要求变化——否则 tabB 若停在上一篇的签名 URL 上，会把旧 URL 当本篇的（产出"对的名字、错的论文"，identity check 能抓住但别靠它兜底，本轮就发生过一次）。
2. A 标签保持同源 HTML 上下文（`viewmedia.cfm` → `abstract.cfm` 同路径推导），用它对签名 URL `fetchPdfInPage`。

2026-10-21 补充两条边界：
- **验证码冷却极慢**：触发一次 captcha 后，实测约 40 分钟重试仍立刻再中。触发当天别再程序化碰 viewmedia，剩下的 Optica 条目直接记 `publisher_verification_waiting_user`。
- **会议论文（OFC 等）viewmedia 拿不到签名 URL 是无授权，不是反爬**：href 停在 viewmedia 原址不动即判 `no_authorized_pdf_found`，不重试。viewmedia URL 里的 uri 段大小写不敏感（`ofc-2014-M3K.1` 与小写均可定位）。

### OE Journal (oes / oea / oet, DOI 前缀 10.29026)

Gold OA。文章页是 Angular 模板，PDF 直链模式（可 curl）：

```text
https://www.oejournal.org/en/article/pdf/preview/<URL编码后的DOI>.pdf
# 例: https://www.oejournal.org/en/article/pdf/preview/10.29026%2Foes.2022.220020.pdf
```

### IEEE Xplore — 通用 DOI 路线（scripts/ieee_fetch.mjs）

1. `doi.org/<doi>` → document 页；Crossref `alternative-id` 常缺 arnumber，改从页面 HTML 正则 `arnumber[="' :]+(\d{6,9})` 取。
2. `stamp/stamp.jsp?tp=&arnumber=<N>` 的 iframe 是 `stampPDF/getPDF.jsp?tp=&arnumber=<N>&ref=`——**没有 .pdf 后缀**，按"iframe 里找 .pdf"过滤会一无所获；取 `frames[0]` 直接在 stamp 页上下文 `fetchPdfInPage`。
3. document 页文本含 `Zhejiang` / `Access provided by` 即机构授权有效；含 `/denied/` 或跳登录则记 `cas_waiting_user` / `publisher_verification_waiting_user`。
4. 会议论文（ICEPT、NUSOD 等）与期刊同一路线，2026-10-21 一批 8/8 全成。
5. **老卷扫描件首页陷阱**：PDF 第 1 页可能是上一篇的结尾页（Nakagawa 1993 PTL 实测，identity check 仍报 OK）；首页文本可疑时看第 2 页 running header（`<作者> et al.: <标题>`）再定，别急着删。

## Publisher Verification and ScienceDirect

ScienceDirect and some publisher platforms may show "Are you a robot?", CAPTCHA, Cloudflare, bot verification, or similar checks after repeated direct DOI navigation or automated tab opening. These pages are security and anti-automation challenges, not ordinary login confirmations.

Reduce the chance of triggering them by using a conservative access pattern:

1. Prefer ZJU Summon / WebVPN / library `在线全文` links before direct `doi.org -> publisher` navigation.
2. Process ScienceDirect and other sensitive publishers one article at a time.
3. Keep a visible audit trail in the manifest; do not open many publisher tabs in parallel.
4. Wait for each page to settle before looking for `Download PDF`, `View PDF`, or `PDF`.
5. Reuse the same tab after the user completes a verification step instead of opening repeated new tabs.
6. Avoid retry loops. One failed automatic attempt is enough before handing the page to the user.

When a publisher verification page appears:

1. Stop automated actions on that tab.
2. Record the paper in `publisher_verification.tsv` or the main manifest with status `publisher_verification_waiting_user`; use `sciencedirect_robot_check` for ScienceDirect's "Are you a robot?" page.
3. Tell the user which paper and tab need manual attention.
4. Do not click CAPTCHA, Cloudflare, "Are you a robot?", bot-check, or similar challenge controls automatically.
5. After the user says the verification is complete, continue from the same tab and try the visible article/PDF route once.
6. If verification immediately reappears, mark `do_not_auto_retry` and move on.

### ScienceDirect / Elsevier Field-Tested Workflow

ScienceDirect can show a "please wait" interstitial after CDP navigation. That page is not always a CAPTCHA. First wait briefly and inspect visible text: only treat it as `sciencedirect_robot_check` when it actually shows "Are you a robot?", CAPTCHA, Cloudflare, or another verification challenge.

For Elsevier papers, a practical workflow is:

1. Resolve institutional access once per browser session. From an article page, use "Access through Zhejiang University" or the Shibboleth handoff to ZJU CAS if needed.
2. After successful access, authenticated article pages often show `Brought to you by: Zhejiang University Library`.
3. For a modest manual-attention batch, open the remaining Elsevier DOI/article tabs, then notify the user that those tabs need manual clicks. The user handles interstitials, CAPTCHA, CAS, or "Access through Zhejiang University" buttons.
4. After the user confirms, scan tabs for article pages containing both `Brought to you by` and `Zhejiang`.
5. Find the main article `View PDF` link carefully. ScienceDirect pages may contain reference-section `View PDF` links far down the page. Normalize whitespace with `a.textContent.replace(/\s/g, " ").trim() === "View PDF"`, then prefer the link nearest the top toolbar or matching the article PII.
6. When the user clicks `View PDF`, Chrome may open a new `pdf.sciencedirectassets.com` tab with a time-limited signed PDF URL. Download from that PDF tab with `fetch(location.href, { credentials: "include" })` and close the PDF tab after success.
7. If the PDF tab returns `Failed to fetch`, the signed URL may have expired. Ask the user to re-click `View PDF` on the article tab.

This is still a human-in-the-loop workflow. Do not automate CAPTCHA, bot checks, or security prompts, and do not open very large ScienceDirect batches.

Field notes (2026-10-13, Edge 155, 浙大校园网出口):

- **触发过一次 "Are you a robot?"**：调试浏览器重启后立刻程序化访问文章页并连续尝试 pdfft 变体，几步之内就中招。一旦弹验证：停止自动化、保留该标签页、记 `sciencedirect_robot_check`，等人工完成后**建议从 Summon「在线全文」入口重进**，不要再直连 doi.org/pdfft。
- 文章页顶部工具栏的 `View PDF` href 是 `.../pdfft?md5=…&pid=…`。**推荐区也全是 "View PDF" 链接**，必须按本篇 PII 过滤（`sciencedirect.com/science/article/pii/<本篇PII>/pdfft`），否则会点进推荐论文。
- **实测可行的下载路线（`scripts/sd_fetch.mjs`）**：文章页 → 导航到本篇 pdfft URL → 标签直接落在 `pdf.sciencedirectassets.com/.../main.pdf?X-Amz-Security-Token=...` 签名 PDF → **在资产域自己的标签里**同源 `fetchPdfInPage(location.href)`。X-Amz 链接是时效性的、**非一次性**，重复 GET 没问题；但跨域 fetch（从文章页）撞 CORS、在非资产域上下文 fetch 报 `Failed to fetch`。
- **别跟弹窗较劲**：`el.click()` 无用户激活，`target=_blank` 的 View PDF 弹窗被拦；CDP `Input.dispatchMouseEvent` 也不授予 activation。直接 navigate 同标签等效且稳。
- `Browser.setDownloadBehavior` 对浏览器级 WS 在 Edge 155 上可能超时不可靠，别依赖。
- Unpaywall / Semantic Scholar 对 Elsevier OA 文章通常只回指本站，没有第三方镜像可绕。

Create or update `publisher_verification.tsv` when publisher checks interrupt a batch. Use this header:

```text
id	project	title	doi	year	venue	publisher	status	source_url	current_url	next_action	notes
```

Suggested `next_action` values:

```text
user_complete_publisher_verification
retry_same_tab_after_user_confirms
try_summon_route
try_authorized_oa_route
mark_do_not_auto_retry
```

PowerShell example for opening a Summon URL safely:

```powershell
$node = "node"  # this machine has C:\Program Files\nodejs\node.exe on PATH
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  $node = "$env:LOCALAPPDATA\OpenAI\Codex\bin\node.exe"  # only if node is truly absent
}
& $node "$env:USERPROFILE\.claude\skills\zju-literature-downloader-skill\scripts\cdp_open_url.mjs" `
  --url "https://zju.summon.serialssolutions.com/search?#!/search?pn=1&ho=t&include.ft.matches=f&l=en&q=10.1021%2Facs.biomac.4c00102" `
  --wait
```

## CAS SSO Handoff and Retry

Some publishers, especially Elsevier/ScienceDirect, Springer Nature, Nature Portfolio, Wiley, Taylor & Francis, Cell Press, and society platforms routed through Shibboleth/OpenAthens, may redirect to Zhejiang University CAS even when WebVPN is open. This is not a reason to ask for the user's password.

When a paper reaches a CAS or institutional SSO page:

1. Stop automated actions on that tab.
2. Record the paper in `cas_retry.tsv` with status `cas_waiting_user`.
3. Tell the user exactly which tab/page needs attention, for example: "This paper is at ZJU CAS. If Chrome has already filled the account and password, I can click the login/confirm button once with your authorization; otherwise please complete it in Chrome."
4. Do not read, store, or request the password, QR result, OTP, SMS code, CAPTCHA, cookie, or local/session storage.
5. If the user explicitly authorizes clicking because the CAS credentials are already filled in Chrome, click only the visible ZJU CAS/WebVPN/institutional SSO login/confirm button once. Do not type into fields or inspect hidden credential values.
6. If QR login, SMS/OTP, CAPTCHA, Cloudflare, or publisher bot verification appears, stop and let the user complete it manually.
7. After the login/confirm step completes, refresh or continue from the same tab.
8. Re-detect whether the page is now a publisher article page, a PDF viewer, or another institutional handoff.
9. If resolved, download and verify the PDF/SI, then update the manifest status to `downloaded` or `downloaded_with_si`.
10. If it loops back to CAS after a completed user login, record `failed_after_retry` with the observed reason and move on.

Known ZJU CAS details observed during use:

- The CAS page may be on `zjuam.zju.edu.cn`.
- Common fields include `id="username"`, `id="password"`, and sometimes `id="authcode"`.
- The `authcode` field is a manual verification step; do not attempt to solve or fill it.
- Chrome does not reliably expose or autofill ZJU CAS credentials in a way the agent can depend on. If the login button click does not advance within about 15 seconds, stop and let the user finish the login manually.

### Safe CAS Auto-Confirm

The agent may click a ZJU CAS saved-login confirmation button only when all conditions are true:

```text
1. The page is on an expected institutional domain such as zjuam.zju.edu.cn, webvpn.zju.edu.cn, libweb.zju.edu.cn, or an institution-redirect page reached from Summon/publisher access.
2. The user has explicitly authorized this action in the current conversation, for example: "可以点浙大 CAS 登录按钮".
3. The visible action is clearly a login/confirm/continue button, such as 登录, 登 录, 确认登录, 继续登录, Continue, Proceed, or Sign in.
4. There is no visible CAPTCHA, Cloudflare challenge, QR-only login, SMS/OTP field, push-approval prompt, password reset prompt, consent-to-share-new-data prompt, or account/security warning.
5. The agent does not read, reveal, copy, store, type, or modify credentials.
```

If any condition is unclear, pause and ask the user to handle that tab. Do not repeatedly click login; one click is enough to test whether the saved-login state works.

Create or update `cas_retry.tsv` whenever CAS blocks a batch. Use this header:

```text
id	project	title	doi	year	venue	publisher	failure_stage	status	source_url	current_url	next_action	notes
```

Suggested `next_action` values:

```text
user_complete_cas_in_chrome
retry_same_tab_after_user_confirms
repair_url_by_doi
inspect_summon_alternative_links
mark_no_authorized_pdf
```

For a CAS retry batch, process one or a few tabs at a time. Do not open many CAS/login tabs in parallel; it can confuse the user's session and increase publisher or SSO risk.

## User Notification

When the user needs to intervene for CAS login, CAPTCHA, publisher verification, or expired ScienceDirect PDF links, give a clear chat update and, when a local notification helper exists, use it to get the user's attention.

Known local helper used during testing:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\claude+codexwork\notify.ps1" "Title" "Message text"
```

If the helper is absent, continue with normal chat updates. Do not make notifications a hard dependency of the skill.

## Download PDF From Browser Context

Use the bundled script when a PDF URL opens in Chrome but direct shell download returns `403`, `401`, Cloudflare HTML, or a login page.

```powershell
$node = "node"  # this machine has C:\Program Files\nodejs\node.exe on PATH
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  $node = "$env:LOCALAPPDATA\OpenAI\Codex\bin\node.exe"  # only if node is truly absent
}
& $node "$env:USERPROFILE\.agents\skills\zju-literature-downloader-skill\scripts\browser_pdf_downloader.mjs" `
  --url "https://pubs.acs.org/doi/pdf/10.1021/acs.biomac.4c00102" `
  --out "D:\path\paper.pdf"
```

The script:

- Opens the URL in the user's controlled Chrome session unless `--target` is provided.
- Runs `fetch(location.href, { credentials: "include" })` inside the page.
- Transfers bytes in chunks through the local CDP proxy.
- Writes the binary file to disk.
- Verifies the `%PDF` signature by default.

Useful options:

```text
--url <url>          PDF URL to open and save
--target <targetId>  Existing Chrome target/tab id to use
--out <path>         Output PDF path
--proxy <url>        CDP proxy URL, default http://127.0.0.1:3456
--close              Close the tab after download if the script opened it
--allow-non-pdf      Save even when content does not start with %PDF
```

### Python helpers must be installed once

```powershell
python -m pip install -r "<skill>\requirements.txt"   # pdfplumber + pypdf
```

Without them `scripts\extract_pdf_text.py` and `scripts\pdf_identity_check.py` exit with
`ModuleNotFoundError`, and the entire verification stage degrades to "skipped". Install into a
**shared** Python (system interpreter or a venv on a non-synced local disk), never into a venv
inside a cloud-synced folder: sync engines lock the freshly created executables and `ensurepip` fails.

### Page-Context Fetch Pattern

For cases where the bundled script is not flexible enough, use the same idea directly in the authenticated tab:

```javascript
const init = await proxyEval(targetId, `(async()=>{
  const r = await fetch("${pdfUrl}", { credentials: "include" });
  const ab = await r.arrayBuffer();
  window.__zjuPdfBytes = new Uint8Array(ab);
  return {
    ok: r.ok,
    ct: r.headers.get("content-type"),
    n: window.__zjuPdfBytes.length,
    head: Array.from(window.__zjuPdfBytes.slice(0, 8))
  };
})()`);

const head = Buffer.from(init.value.head).toString("ascii");
if (!head.startsWith("%PDF")) throw new Error("Not a PDF response");
```

Then transfer `window.__zjuPdfBytes` in chunks through `/eval`. This pattern is useful for Wiley same-origin `pdfdirect` links and ScienceDirect signed PDF tabs.

## Supporting Information

Always try to download supporting information when the user wants complete reading.

Preferred method:

1. Open the article landing page, not only the PDF page.
2. Extract all links with text or href matching:
   - `Supporting Information`
   - `Supplementary`
   - `Supplemental`
   - `/doi/suppl/`
   - `/suppl_file/`
   - `_si_`
   - `_mmc`
   - `appendix`
3. Download every PDF/DOCX/XLSX/video/data file that is clearly a legitimate supplement, using the browser context if needed.

ACS fallback pattern, only after verifying the DOI and article page:

```text
https://pubs.acs.org/doi/suppl/<DOI>/suppl_file/<journal-code>_si_001.pdf
```

For example, the verified test case used:

```text
https://pubs.acs.org/doi/suppl/10.1021/acs.biomac.4c00102/suppl_file/bm4c00102_si_001.pdf
```

Do not invent supplement URLs as facts. If a guessed URL returns 404, record "not found" and inspect the article page.

For Elsevier/ScienceDirect, supporting files may use `_mmc` naming or appear as multimedia components on the article page. For RSC, supporting links often contain `suppdata`, but the main PDF may still be unavailable through institutional access.

## Verification and Reading

After downloading, verify every file.

For PDFs:

```powershell
$env:PYTHONUTF8='1'
python -X utf8 "$env:USERPROFILE\.claude\skills\zju-literature-downloader-skill\scripts\extract_pdf_text.py" `
  --pdf "D:\path\paper.pdf" `
  --pages 3
```

This should report page count and extracted text. The script also reconfigures stdout/stderr to UTF-8 internally to reduce Windows GBK failures. If extraction fails but the PDF is valid, try PyMuPDF, OCR, or the local `pdf` skill.

On Windows, always set `PYTHONUTF8=1` and use `python -X utf8` when extracting text. If console text still displays poorly, check the JSON or metadata output first; the PDF may be valid even when terminal rendering is not.

### Identity check (the step that catches "right format, wrong paper")

Signature, page count, and readable text all pass for a PDF of a *different* article.
Two such files were found in a completed 38-item batch this way. Always run:

```powershell
$env:PYTHONUTF8='1'
python -X utf8 "<skill>\scripts\pdf_identity_check.py" `
  --dir "<project>\PDFs" --manifest "<project>\manifest.tsv" --threshold 0.5 `
  --json "<project>\archive\logs\identity_report.json"
```

It compares each PDF's first-page text with the manifest title (token overlap) and judges
`OK` / `SUSPECT`. A `SUSPECT` row must **not** be recorded as `downloaded`; re-resolve the DOI
from the title first. Expect some false positives on multi-line titles and on Chinese journals,
so treat `SUSPECT` as "look at this one", not as "delete this one".

Minimum verification checklist:

- File exists and size is plausible.
- First bytes are `%PDF` for PDF files.
- Page count is nonzero.
- Extracted text includes the article title, abstract, or supporting information title.
- **The first page really is the requested paper** (`pdf_identity_check.py` reports `OK`),
  and `manifest.tsv` carries a `pdf_sha256` for every downloaded file.
- Supplements follow the `__SI##` suffix rule and sit next to the main PDF.
- The round directory exists, `input.txt` is the verbatim list, and `全局任务简报.md` has this round's row.
- Save a small manifest with DOI, title, source URL, download date, and supplement status when doing more than one paper.

## Zotero

Zotero import is useful for metadata, DOI, citation keys, and library organization, but it does not replace local PDF verification. If Zotero imports a paper, still check whether the PDF attachment is present and readable. If the user wants a project folder with full text, save PDFs explicitly to that folder.

## Synced / cloud-drive workspaces

When the output folder lives inside a cloud-sync folder (Synology Drive, OneDrive, Baidu Netdisk),
the WebDAV-style failures disappear but new ones appear. Field-tested facts:

- Stable `readdir`, real atomic rename, and correct relative-path resolution all work,
  so the built-in write/edit tools are usable and no local-disk detour is needed.
- Sync engines take a lock on a file right after it is written, so deleting or renaming it
  **immediately** fails with `EPERM`/`EBUSY` even though the write itself succeeded.
  Write the final filename directly, verify with `stat`, and write the manifest row last.
- Do not create `.tmp`/`.part` intermediates or "write placeholder then rename" in a synced folder.
- Cloud placeholder files (`ReparsePoint`) may be dehydrated; hashing a large PDF can trigger a
  full download or hang offline. Verify in a local staging folder, then copy finished files in.
- Do not put a Python venv inside the synced folder (`ensurepip` fails on a locked interpreter).
- Keep published PDFs out of third-party consumer-cloud sync scopes: copyright and data-egress risk.

## Per-user folder tree, per-user preferences, and what the human must provide

Different people file papers into completely different trees, so treat the library as **input, not output**:

**The human must provide, at the start of a task:**
1. a paper list (titles, DOIs, or both) - titles alone are fine;
2. the **root folder of their library** (the tree differs per person);
3. optionally their preference file (see below).

**Never create, rename, or reorganise the library tree yourself** - not even a "helpful" intermediate level.
Folders are the human's. The only exception: the human writes an explicit instruction such as
`请 AI 新建 <full path>` in the notes column (or passes `--allow-create`), which authorises that one path.
The tool refuses otherwise and prints how to authorise.

**Per-user preferences live outside the code, in a data file** (so the skill stays general and only the
person's habits are recorded). **Keep it in the user's workspace, NOT in this skill repo** - it is a
personal habit, not a general rule. Suggested name `归位偏好-<name>.tsv`, columns
`主题/说法（论文里可能出现） | 本人目录（去向） | 类型 | 来源`.
Mark it clearly as *a person's habit*, not a general rule. It should carry both positive rules and
**negative examples**, e.g. "do not route to `Optical Communications\Coherent OC\Coherent receiver` just
because the words integrated/receiver/coherent appear - COR means coherent receiver".

**Respect material-class gates.** Low-dimensional branches are only for papers that actually study that
material class. Do not route a paper into `LDM_2DM` unless it is genuinely 2D-material work (TMDC, MoS2, WS2,
phosphorene, h-BN, tellurene...), and do not route into `LDM_0DM` unless it is genuinely 0D (quantum dots,
CQD). The same care applies to `LDM_1DM` and `LDM_Graphene`. Generic words such as integrated, photonic,
device, detector are **not** evidence of a low-dimensional material.

**DOI policy: do not block on hypothetical ambiguity.** When the list has no DOI, resolve it from the
title via Crossref; only stop and ask the human when a **real** conflict appears (candidate clearly differs
from the requested paper, or one title maps to several conference/journal versions). Books, standards and
roadmaps (`[M]`, `[R]`, Specification, Roadmap, Implementation Agreement) have no article DOI by nature:
mark them `refuse` with the reason and move on. If the human supplies a DOI, always trust it and skip the
reverse lookup entirely.

**Subject beats keywords.** Decide the branch from the paper's real topic (title + abstract), then pick
the level inside that branch. Generic words (integrated, receiver, circuit, photonics) are weak evidence
and are what produce wrong routes.

**Document type must be judged from content.** Crossref reports review and research articles both as
`journal-article` with an empty `subtype` (verified on 5 papers), so citation counts and metadata alone
cannot tell them apart. Read the first page/abstract and let the model decide; keep a local hint only.

**Scan the tree again before every write action.** The library changes between rounds (the count moved
3995 -> 4023 -> 4034 during one task). Rescanning 4000 directories costs ~0.1 s and zero tokens, and it
is the only way to see a folder the human just created. Never reuse a stale directory list for a write.

**Reject invented paths.** Every suggested destination must be matched against the freshly scanned list;
if it is not in the list, drop it (or ask) instead of writing a plausible-looking path. A parser that
extracts a path from a note must anchor on real directory names, otherwise it truncates at the first
space (`集成光子学\LNOI Photonics` -> `集成光子学\LNOI`).

**Multi-target placements.** "Several numbers = place a copy in each". Implement this as: copy to all but
the last target, then move the last one - otherwise the first move consumes the source and the rest fail
with "source missing". When de-duplicating later, keep exactly the intended places and verify SHA256
before deleting anything.

**Clean the task folder after a push.** Moved content must not stay behind. Delete the staged copies only
after confirming the library holds the same SHA256 (one task went from 307 MB to 46 MB this way).

**Scripts: reuse vs rewrite.** Anything that is true for any user or library belongs in `scripts/`
(`lib_scan.py`, `annotate_papers.py`, `lib_move.py`, `ingest_manual.py`, `pdf_identity_check.py`,
`new_round.mjs`, `cdp.mjs`, `start-debug-browser.ps1`). Anything tied to one person's tree belongs in
their preference/vocabulary file. One-off bug patches stay in the task folder and are not copied forward.

## Round directories and the running briefing

The user gives a list each session and later **cuts the PDFs away** to their own library, so a flat
output folder loses both the batch identity and the "already downloaded" knowledge. Use a round directory
named by the moment the conversation starts:

```text
<project>/
  manifest.tsv                 # authoritative, survives every round
  全局任务简报.md               # append-only running log
  rounds/
    2026-09-12T1430_fix14-17/  # YYYY-MM-DDTHHmm + short label
      input.txt                # the user's list, verbatim
      PDFs/                    # this round's output, cut away as a whole
      解析/                     # per-paper extracted metadata
      round-note.md            # this round's summary
```

`scripts\new_round.mjs` creates the directory and `input.txt` for you.

### Always check for duplicates before downloading

Because finished PDFs get cut away, **the files on disk are not evidence of what was already downloaded**.
The only reliable memory is `manifest.tsv` (`doi`, `title`, `pdf_sha256`, `status`). At the start of every round:

1. Normalize each requested DOI and verify the title against Crossref; fix wrong DOIs before anything else.
2. Compare against `manifest.tsv`: a row already `downloaded` / `downloaded_with_si` is **已下载过** — do not re-download it silently.
3. Report one line, e.g. `本轮 12 条中 5 条已下载过（#2 #3 #8 #18 #26）；文件不在本工作区，可能已随上一轮剪切走；默认跳过，需要重下请说一声`.
4. Only re-download after the user says so, and record `用户要求重下` in the briefing.
5. If the user says where the old PDFs live, record the path and hash instead of downloading again.

After each round, append one row to `全局任务简报.md`:

```text
| 时间 | 轮次目录 | 清单来源 | 新增成功 | 失败及原因 | 已下过(跳过) | 备注 |
```

## External literature library (works outside any workspace)

These three helpers need no browser and no CDP: they take the library root as an **argument**, so the
same skill works for anyone else's folder layout without editing code.

```powershell
# 1) scan structure — metadata only, does NOT read PDFs, so it will not trigger cloud downloads
python -X utf8 "<skill>\scripts\lib_scan.py" --root "<library root>" --out-dir "<project>\解析"
#    -> inventory.json (machine) + dirs.tsv (one row per directory: counts, size, sample names)
#       + 结构报告.md (human). Feed dirs.tsv to the model: whole directories can be classified at once
#       instead of asking about every paper.

# 1b) optional: per-directory scale snapshot — direct and recursive counts side by side
python -X utf8 "<skill>\scripts\lib_scan.py" --root "<library root>" --out-dir "<project>\解析" `
  --scale-tsv "<library root>\_库地图\目录规模.tsv"
#    -> rel  depth  n_pdf_direct  n_pdf_all  n_caj_all  n_files_all  mb_all  sample
#       Includes empty directories; carries a `#` header with scan time, root and criteria.
#       Why both counts: most files sit deep, so a directory's *direct* count badly understates its
#       branch — one library held 63% of its PDFs below level 2, and 62% of them in just 142
#       directories. Beware double counting: a parent's n_pdf_all already includes its children's.
#       Never add columns to dirs.tsv for this: its 6 columns are fixed because lib_move_v2.py
#       --rescan rewrites it by position. Extra counts belong in this separate file.

# 2) annotate — writes a "parse card" as a NEW file; the original is never modified
python -X utf8 "<skill>\scripts\annotate_papers.py" --files "<a.pdf>" --out-dir "<out dir>"
#    -> <stem>__note.pdf (card inserted before page 1) + <stem>__note.md
#    Flags: --md-only (no PDF), --in-place (edit original; combine with --backup), --no-crossref, --limit N

# 3) file literature into folders per a reviewed plan — dry-run by default
python -X utf8 "<skill>\scripts\lib_move.py" --propose "<project>\解析\propose.tsv"
python -X utf8 "<skill>\scripts\lib_move.py" --propose "<project>\解析\propose.tsv" --execute --verify --limit 50
```

### `_库地图\` — the classification basis lives in the library, not in the skill

If `<library root>\_库地图\` exists, it is **that library's own** interpretation layer. Read it before
proposing where anything goes; it answers "what do these folders mean" without reading a single PDF.

| File | What it is | Who may write it |
|---|---|---|
| `目录语义.tsv` | one row per directory: 含义 / **准入（what belongs）** / 排除（what does not） / 别名 / 主判据 / 平级易混 / 状态 | human, with AI drafting; **scans must never touch it** |
| `目录规模.tsv` | scale snapshot, refreshed by `lib_scan.py --scale-tsv` | machine only — overwritten wholesale |
| `库语义地图.md` | human-readable guide derived from the table | human |

Rules:
- **The map belongs to the library, not to the skill.** Never bundle a map inside the skill and never
  let one person's map govern another person's library — every library gets its own `_库地图\`.
- Only rows whose 状态 is `人工确认` may be used to move files. `AI草稿` / `存疑` rows produce
  suggestions only; `遗留特例` rows (e.g. by-author folders) are kept for retrieval but are never a
  filing target; `待建` rows are empty directories.
- Empty 准入 / 排除 / 别名 / 主判据 cells **inherit the row named in 上位** — so a top-level fix
  propagates down the whole branch.
- If the folder is absent, say so and offer to draft one. Do not silently invent a taxonomy for
  someone else's library.

`propose.tsv` columns: `id  path  suggested_dir  accept  confidence  reason` (extra columns ignored).
Set `accept=y` only on the rows you approve; `lib_move.py` defaults to dry-run and will not act until
`--execute` is passed.

Safety rules these tools enforce (each maps to a failure seen in practice):

| Rule | Why |
|---|---|
| `lib_scan.py` reads metadata only by default | an OneDrive/cloud folder hydrates the whole file on read; scanning 4000 PDFs would pull ~10 GB |
| `lib_scan.py` prefixes long paths with `\\?\` (`fs_path()`), and reports a **metadata-read-failure count** | Windows `MAX_PATH` is 260: a real library had **27 files past 259 characters**, which `os.stat` dropped *silently*, so the scan reported 4053 PDFs as 4026 while every other counter said 4053. Never let a walker swallow an `OSError` without counting it. `inventory.json` now carries `counts.stat_failed` |
| `.caj` (and other non-PDF formats) are counted in a **separate** bucket | CNKI's `.caj` is invisible to pdfplumber/pypdf. Folding it into the PDF count hides it from parse batches; one library had 185 of them, and they accounted for most of the 1.7 GB that was not PDF |
| Scan output carries a timestamp | cloud-synced libraries **change under you**: one library measured 3995 PDFs, then 4023 after sync settled, then 4053 — never reuse a stale inventory for whole-library uniqueness checks. Drift is normal between runs: a 4-day gap moved one top-level branch from 3 to 8 papers and removed one file elsewhere |
| `annotate_papers.py` writes `__note.pdf` instead of editing in place | the original keeps its publisher hash, so it can be re-verified and rolled back |
| `lib_move.py` compares SHA256 before acting | same name + same content = skip (source kept); same name + different content = rename with `__dup2`, **never overwrite** |
| every move is appended to `archive/logs/move-log.tsv` | gives a rollback trail; a locked file is logged as `LOCKED` after retries instead of aborting the batch |
| only the final target directory is created | the model must not invent a whole intermediate taxonomy for someone else's library |

Typical flow: `lib_scan.py` -> propose rows (directory-level topic judgement) -> human edits `accept` ->
`annotate_papers.py` -> `lib_move.py --execute`.

## Naming Convention

Main paper and its supporting information live **in the same folder**, with the supplement expressed as a
suffix on the identical stem, so a rename or a cut-paste can never separate them and sorting keeps them adjacent:

```text
Wang_2024_Nature_lithium_tantalate_PICs.pdf
Wang_2024_Nature_lithium_tantalate_PICs__SI01.pdf
Wang_2024_Nature_lithium_tantalate_PICs__SI02.pdf
```

Rules:
- `FirstAuthor_Year_Journal_short-title.pdf` for the main text; no suffix when there is no supplement.
- `__SI01`, `__SI02`, ... for supplements, in the order the publisher lists them (zero-padded so `ls` sorts right).
- A peer-review file, appendix, or dataset is still supporting information: give it an `__SI##` suffix too.
- Do not keep a separate `SupportingInformation/` folder for new batches, and do not invent an unrelated
  name like `Feng_2024_peer-review-file.pdf` — the stem must match the main paper exactly.

For project work, keep a folder like:

```text
文献自动下载/
  manifest.tsv
  rounds/<timestamp_label>/
     input.txt
     PDFs/                 # main text and __SI## files together
     解析/
     round-note.md
  全局任务简报.md
```

## Failure Handling

If direct publisher navigation triggers ScienceDirect "Are you a robot?", Cloudflare, CAPTCHA, or another bot challenge:

- Do not bypass it.
- Do not auto-click the challenge.
- Record `publisher_verification_waiting_user` or `sciencedirect_robot_check`.
- Ask the user to solve it in Chrome.
- Then continue once from the same now-open page.
- If the same challenge immediately reappears, mark `do_not_auto_retry` and move on.

If shell `Invoke-WebRequest` or `curl` returns 403 but the PDF opens in Chrome:

- Use `browser_pdf_downloader.mjs`; this is the normal institutional-access case.

If a page shows publisher bot verification, CAPTCHA, Cloudflare, QR login, SMS/OTP, or another security challenge:

- Do not ask for or accept credentials in chat.
- Pause and ask the user to complete the verification in Chrome.
- Record `publisher_verification_waiting_user` in `publisher_verification.tsv`, or `sciencedirect_robot_check` for ScienceDirect.
- Continue only after the user says the browser step is complete.

If a page shows Zhejiang University CAS, unified identity authentication, Shibboleth, OpenAthens, SAML, or institutional sign-in:

- Do not ask for or accept credentials in chat.
- If the user has explicitly authorized it and Chrome has already filled the ZJU CAS fields, click the visible login/confirm button once.
- Otherwise pause and ask the user to complete the login in Chrome.
- Record `cas_waiting_user` or `cas_resolved_retry_needed` in `cas_retry.tsv` as appropriate.

If Summon shows no PDF:

- Try `在线全文`.
- Try DOI on the publisher page.
- Check open-access copies only from legitimate sources.
- Record "no authorized PDF found" rather than seeking unauthorized mirrors.

If Summon or another site opens as `about:blank`:

- Treat it as a URL-fragment/encoding problem first, especially when the original URL contains `#!`.
- Reopen through `scripts/cdp_open_url.mjs --url "<full URL>" --wait`.
- Do not paste fragment-heavy URLs unquoted into shell commands or manually concatenate them into `/new?url=...` without URL encoding.

If `curl` is unavailable:

- Use PowerShell `Invoke-WebRequest` for simple proxy checks.
- Prefer the bundled Node.js helper scripts for CDP proxy actions because Node's `URLSearchParams` preserves nested URL fragments correctly.

If the session expires:

- Ask the user to log in again through WebVPN.

Common pitfalls:

| Problem | Likely cause | Practical fix |
|---------|--------------|---------------|
| Summon opens `about:blank` | `#!` fragment stripped | Use `scripts/cdp_open_url.mjs` or `/navigate` with a fully encoded URL |
| First Summon online-full-text link does nothing | Empty `href` facet toggle | Skip empty links and retry after SPA rendering |
| Serials Solutions says IP is outside authorized range | CDP Chrome traffic is off-campus | Ask user to use RVPN/SSLVPN or another authorized network route |
| CAS auto-click does not work | Credentials not filled, authcode present, or login loop | Stop after one authorized click and ask the user to finish manually |
| Wiley fetch returns an empty object or CORS error | Wrong Wiley origin | Fetch `location.origin + "/doi/pdfdirect/<doi>?download=true"` from the authenticated article page |
| ScienceDirect `View PDF` opens the wrong article | Reference-section PDF link selected | Normalize whitespace and choose the top toolbar/main-article link |
| `pdf.sciencedirectassets.com` fetch fails | Signed S3 URL expired | Ask user to re-click `View PDF` |
| RSC PDF URL returns 404 or purchase page | No authorized access | Record `no_authorized_pdf_found` or `purchase_required` |
| Node `require()` fails inside `.mjs` | ES module context | Use `import` syntax |
| Python text extraction prints garbled Chinese | Windows console encoding | Use `PYTHONUTF8=1` and `python -X utf8` |
| Git Bash `curl` 写中文路径报 error 23 / 乱码 | MinGW 原生 curl 不认 UTF-8 参数 | 先下到 ASCII 临时目录再 `mv`，或用 node fs 写文件 |
| Summon 脚本首轮就把"在线全文" facet 当结果提前退出 | 空 href 的 facet 开关也命中 /全文/ 正则 | 过滤掉空 href；无真实结果行就继续等 SPA 渲染 |
| Optica 双标签法产出"对的名字、错的论文" | tabB 未先导航就轮询，把上一篇签名 URL 当本篇的 | 先 navigate 再轮询，要求 href 与导航前不同；必须跑 identity check |
| IEEE stamp 页找不到 PDF iframe | getPDF.jsp 没有 .pdf 后缀 | 取 frames[0] 直接 fetch，别按后缀过滤 |
| MDPI 页内 fetch `/pdf` 失败 + 出现 downloads-hub 空壳标签 | Content-Disposition 触发浏览器原生下载 | 改用 mdpi-res.com CDN 直链 curl（期刊代码以 doi.org Location 为准） |
| Write 工具与 MSYS 对 `/tmp` 解析不一致 | `/tmp` 由调用方各自解析 | 临时脚本用显式 ASCII 绝对路径（如 `E:\tmp\...`） |
| 复杂 CDP 调用 `node -e` 报引号错误 | 嵌套模板字符串经 bash 转义必炸 | 写成 `.mjs` 脚本文件再 `node` 运行 |
| 调试 Edge 意外消失、端口 9222 关闭 | 宿主 shell 命令被取消时连带杀掉子进程 | 重跑 `start-debug-browser.ps1` 同 profile 恢复；重启后别立刻程序化轰炸 ScienceDirect |
| 写 manifest 报 ENOENT，但昨天同一路径还在 | 用户在轮次之间整棵移动了文件夹 | 写入前先验证目录存在；manifest 跟着新位置走 |
| SD 签名 PDF fetch 得 HTML 或 `Failed to fetch` | 不在资产域上下文 fetch（跨域 CORS / 错误 origin） | navigate 到本篇 pdfft 让标签落在 `pdf.sciencedirectassets.com`，再同源 `fetchPdfInPage`（`sd_fetch.mjs`）；X-Amz 链接非一次性 |
| SD 弹 "Are you a robot?" 后束手无策 | 程序化访问过频（常见于浏览器重启后） | 停手、保留标签页、记 `sciencedirect_robot_check`；实测搁置约半小时可自行解除，解除后从文章页重进 |
| ACM `/doi/pdf/` 单条 403（Cloudflare HTML），同批兄弟 URL 正常 | 该 URL 触发 CF 规则，非全站封禁 | 别硬重试直链，走"文章页先行"：`article_page_fetch.mjs "<文章页>" "<out>"`（落地页拿 clearance 后同源 fetch） |
| Wiley fetch 报 CORS / 空对象 | pdfdirect 用了硬编码 onlinelibrary origin，而 doi.org 落在 `advanced.`/`sid.` 子域 | 从落地 href 的 origin 发起 pdfdirect（`wiley_fetch.mjs` 自动处理） |
| Nature 付费墙文章页 `access:false` 让人以为没授权 | 授权走校园网 IP，页面 JS 检测不到 | 照常 `articles/<id>.pdf`，下载后核对页数（无授权只给 1 页预览，页数一验就穿） |
| MDPI 下载一条直链失败就放弃 | CDN 与主站直链互有成败，且 403 常是瞬时限流 | `mdpi-res.com` CDN 与 `www.mdpi.com/.../pdf?version=` 两条都试；403 隔几秒带浏览器 UA 重试一次 |
| AIP `/article-pdf/` 浏览器 fetch 仍 403、文章页无 .pdf 锚点 | Silverchair 按钮 JS 渲染 + Cloudflare | 记 `publisher_blocked_waiting_user`；OA 文章把 Unpaywall PDF 直链写目录待人工 |
| RSC 同源 fetch 全返回 ~156KB text/html | perfdrive（Radware）盾主动接管 fetch（旁开 validate.perfdrive.com 标签） | 停手记 waiting；换路径变体无效，别硬试 |
| IOP `/article/<doi>/pdf` 拿到 HTML | 挑战页 | 同 AIP，停手记 waiting |
| SPIE 文章页落在 `/access-suspended` | 机构订阅被暂停（非反爬） | 记 waiting 等订阅恢复；Summon 同授权源无解，不要绕道重试 |
| Optica 验证码冷却后立刻再中 | 冷却期实测 >40 分钟 | 触发当天停止程序化访问 viewmedia；条目记 `publisher_verification_waiting_user` |
| OFC 等会议 viewmedia 拿不到签名 URL | 无授权（非反爬） | 直接判 `no_authorized_pdf_found`，不重试 |
| identity check 全部 `NO_MANIFEST_ROW` | manifest 表头缺 `id` 列（脚本要求表头同时含 `title` 和 `id` 才按表头解析） | 表头加 `id` 列（13/14 列兼容格式天然满足） |
| 临时 node 脚本 `import "C:/..."` 报 `ERR_UNSUPPORTED_ESM_URL_SCHEME` | ESM 静态导入不认 Windows 盘符路径 | `await import(pathToFileURL(p).href)` 动态导入，或把脚本放进 skill `scripts/` 用相对导入 |
| Git Bash 循环里输出文件全写到同一个怪名字文件 | `"E:\...\$name"` 的 `\$` 被转义成字面 `$` | 路径用正斜杠：`"E:/tmp/.../$name"` |
| `/json/close/<8位前缀>` 无效 | 该端点只认完整 tab id | 从 `/json/list` 取全 id 再关 |
| 老 IEEE 扫描件 identity check 首页文本不像标题 | 第 1 页是装订上一篇的结尾页 | 看第 2 页 running header（`<作者> et al.: <标题>`）再定，别急删 |
| OUP `article_page_fetch` 同源 fetch 报 `Failed to fetch` | article-pdf 带 Content-Disposition，fetch 被原生下载接管 | 改 `browser_pdf_downloader.mjs --url <article-pdf直链>` 直开（2026-10-22 实测成） |
| Hindawi 老域名 `downloads.hindawi.com` 返回 HTML 壳 | Hindawi 已整体迁入 Wiley | `10.1155/*` DOI 直接走 `wiley_fetch.mjs` |
| MDPI 主站 `/pdf` 首轮拿 411 字节 HTML | 瞬时限流，非封禁 | 换 `mdpi-res.com/d_attachment/<journal>/<journal>-<卷>-<DOI尾段>/article_deploy/....pdf` CDN 直链 |
| Optica 上轮中验证码、本轮 9 连发却全成 | 差异在条间隔（本轮 ≥25 s） | 批量 opg_fetch 条间隔 ≥20 s；中招仍按"当天停手"纪律 |
| SD 首次打开文章页就弹人机验证 | 会话敏感度波动，非必然 | 搁置约 25–30 分钟后**从文章页重进** `sd_fetch`，勿直连 pdfft |
| Unpaywall 给的大学仓库镜像链 curl 拿到 HTML 壳 | 机构库（如 cora.ucc.ie）下载需 JS/会话 | 别耗在镜像上，直接走出版商官源 |
| Optica viewmedia URL 猜错（uri 段编造）+ 正确尝试随即中验证码 | uri 段（vol-iss-page）猜错的访问会助推风控 | 先 `cdp_open_url` 走 doi.org 拿真实 `abstract.cfm?uri=`，再拼 viewmedia；见 `/captcha/` 当天停手 |
| SD 书章 sd_fetch 报"未找到文章页标签" | 书章落地页是 `/science/chapter/...`，article 路径被 302 弹回 | sd_fetch 已认 chapter 路径；但章页无 pdfft 锚点、构造直链撞 PerimeterX——书章直接转人工 |
| PMC/EPMC OA 备份三路全堵（2026-10-22 起） | Cloudflare 上了 europepmc.org 和 pmc.ncbi.nlm.nih.gov | 放弃 curl/fetch 备份：出版商原发路线或人工浏览器下载 |
| light-am.com 首次 curl 0 字节 | 瞬时限流（同 MDPI 脾气） | 重试即成；PDF ID 在文章页 HTML grep `article/pdf/preview/` |
| ACS 文章不知从哪下手 | `/doi/pdf/` 直链遇 CF 会 403 | `article_page_fetch.mjs "<文章页>" "<out>"` 一发即中（2026-10-22 实测 2/2） |
| IOP 一律判死 | 2026-10-21 的结论来自 2009 老订阅刊 | gold OA 新文先试 `article_page_fetch + 显式 /pdf 候选`（2026-10-22 实测成） |
| 增量 identity check 旧文件全报 NO_MANIFEST_ROW | --manifest 只含本轮清单 | 属预期噪音；顺带甄别混入的非论文 PDF（厂商资料等），不要动用户并行放入的文件 |
| 下载轮开工即 403/无授权连环挂 | 本机开着 TUN 模式代理（出口非浙大 IP），授权被静默毁掉 | 开工 `curl -s https://ifconfig.me` 验出口；非浙大按"无授权模式"走（OA-only），付费墙记 pending 别硬试 |
| aTrust 隧道代理验证"能通"却下不了出版商 | zju-connect 本地代理出口=服务器公网 IP，不透传校园授权 | 别用隧道救出版商授权；OA 直链/arXiv/WebVPN 人工登录三选一 |
| OpenAlex oa=True 的 IEEE 直接判付费墙跳过 | IEEE 确有真 OA 文章（TCPMT/JLT 实测） | Unpaywall 复核；真 OA 走 ieee_fetch stamp 路线无校园网也成 |
| light-am 预览 ID 按 DOI 尾段猜，0 字节 | ID 是独立编号（如 10.37188/lam.2024.049→LAM2024050041） | 必须从 doi.org 落地文章页 HTML grep `article/pdf/preview/` |
| IOP 混合 OA（JPhysD/JJAP）/pdf 返 HTML | Unpaywall oa=True 只说明有 OA 版本，不代表站点无权益放行 | 只有 gold OA 刊（JPhys Photonics 等）走 article_page_fetch+显式 /pdf；混合 OA 记 pending |
| biblio.ugent.be / research.tue.nl 镜像挂 | 前者被代理出口掐 TLS（SNI 级），后者拒数据中心 IP 403 | 镜像三路（curl/python/浏览器）一致失败即弃，转出版商原源或 pending |
| De Gruyter /document/doi/<doi>/pdf 全是 405 | 端点要求真实导航下载流，fetch 被拒 | article_page_fetch/browser_pdf_downloader 都不行；转人工 |
| SD 冷却 25–30 分钟后仍 "Just a moment..." | 境外数据中心出口被 Elsevier Cloudflare 硬标记，校园网经验不迁移 | 出口不对时 SD 直接记 pending，不烧重试 |
| sd_fetch 报"未找到文章页标签" | 第一个参数传了 DOI，该脚本要的是 PII | Crossref `alternative-id` 取 PII；文章页先 cdp_open_url 打开 |
| Wiley pdfdirect 首发就 403 | pdfdirect 的首触挑战（非封禁） | 隔 30–40 秒原样重试一次，campus 下 4/4 过；再挂才判 pending |
| IEEE 老文 arnumber 解析为 null | 部分老页面元数据缺 arnumber 字样 | 用 URL 里的 document id 拼 stampPDF getPDF 直链走 article_page_fetch |
| Optica 老文 viewmedia 一直"未拿到新签名 URL" | URL 带 `&html=true`（渲染 HTML 版，不跳签名 PDF） | 剥掉 html=true 再跑；abstract.cfm?URI= 大写的会议文手工拼小写 viewmedia，仍失败即无授权 |
| opg_fetch 报 "Cannot navigate to invalid URL" | 第一参数传了 DOI，该脚本要 viewmedia URL | 先 cdp_open_url 走 doi.org 拿落地页再换 viewmedia |
| 批量 viewmedia 跑到后面全部拿不到签名 URL（无 captcha 页） | 连续 20+ 次命中后的软限流 | 单批 ≤15 条；无验证码不算触发当天停手，次日续跑 |
| SPIE 下载连发几条后全是 ~1KB HTML（distil_referrer/SWUDNSAI 字样） | Incapsula WAF 被连发触发（订阅本身已恢复） | 停手；条间隔 ≥60s、单批 ≤4–5 条；落盘必须校验 %PDF 头，别信退出码 |
| SPIE 裸 DOI URL 落到 "Proceedings" 浏览页、无 PDF 锚点 | 裸 `/conference-proceedings-of-spie/10.1117/12.x` 不指向文章页 | 走 doi.org 落带卷号/文章号的 `.full` URL，读 `citation_pdf_url` meta 或 `.full`→`.pdf` 替换后同源 fetch |
| Optica 订阅文也报"未拿到新签名 URL"，以为全站坏了 | 三种情况混在一起：老档案无订阅 / CLEO·OFC 会议无授权 / 近期期刊文偶发 token 失败 | 前两类判死；第三类隔 30s 原样重跑一次（实测 3/5 复活）；用同卷期兄弟条目成败做判别参照 |
| Optica Open 预印本 curl 403（昨天还能下） | 该站 WAF（awselb）对机器访问不稳定 | 别当可靠备份源；回出版商原源或记 pending |
| identity check 全线 SUSPECT，清单标题显示的是 DOI | manifest 追加行的 DictWriter fieldnames 列序与主表头不一致，doi/title 物理装反 | 追加前按既有表头列序写行；修复时从源 verify/retry TSV 全量重建，别逐列打补丁 |
| CLEO 会议文在 Optica 全部无授权 | 会议 proceedings 无浙大订阅 | 先查 IEEE Xplore 是否双收录（CLEO/Europe 常在），走 ieee_fetch 反而能下 |
| APS（journals.aps.org）/pdf/ curl 下载失败 | APS 不给纯 curl 下载流，但无反爬 | browser_pdf_downloader 直开 `https://journals.aps.org/<journal>/pdf/<doi>`，校网 IP 一发即中（2026-10-24 实测） |
| SPIE 挂起条目次日重试第一条就被拦 | Incapsula 会话标记**跨天持续**在调试 profile 里 | 重试隔 2–3 天或换 profile/人工；%PDF 硬门必须有，别信退出码 |
| known 去重漏掉库内老批次、同文重复入池 | manifest 建档前的历史批次从没进 manifest | 从目录 md 正则提取全部 DOI 并入 known；治本=历史批次回填 manifest |
| Optica 新文 resolve 三次都是 abstract.cfm?doi=（无 uri 段） | 文章太新，系统未生成 uri 形式落地页 | 三试无 uri 段即记 pending（第四态）；别与"重开一次能落到 uri"的旧经验混同 |
| SCIS Letter 只有 2 页/295KB | Science China IS 正式 Letter 格式（`xxxx:1–xxxx:2` 页码） | 按标题 identity 判定，别按页数删 |
| identity check 对新刊 PDF 报 0.0 匹配 | CID 连字编码抽取成三重化乱码（OEM 实测） | PyMuPDF 重抽或人工看首两行再判，SUSPECT≠错文 |

## Verified Test Case

This workflow was verified with:

- Title: `Innovative Use of an Injectable, Self-Healing Drug-Loaded Pectin-Based Hydrogel for Micro- and Supermicro-Vascular Anastomoses`
- DOI: `10.1021/acs.biomac.4c00102`
- Source route: ZJU Summon `PDF` link -> ACS PDF page
- Output: main PDF and ACS supporting information PDF
- Verification: main PDF 17 pages, SI PDF 31 pages, both text-readable.

Additional field-tested cases recorded from later use:

- Wiley same-origin PDF fetch: DOI `10.1002/adhm.202405260`.
- Nature Communications OA direct PDF: DOI pattern `10.1038/s41467-...`.
- Springer direct PDF after CAS/institutional access: DOI pattern `10.1007/...`.
- ScienceDirect / Elsevier: Shibboleth plus user-handled verification, then download from `pdf.sciencedirectassets.com` tabs.
