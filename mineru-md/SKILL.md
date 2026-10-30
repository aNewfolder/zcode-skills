---
name: mineru-md
description: 用 MinerU 精准解析 API 把 PDF（纯文字/双栏/公式/复杂表格/扫描件）、图片（png/jpg/jpeg/jp2/webp/gif/bmp）、Office（doc/docx/ppt/pptx/xls/xlsx）转成 Markdown，图片本地化随产物输出。凡"把这个 PDF 转成 md""用 MinerU 解析""扫描件/截图 OCR 成 markdown""复杂排版 PDF 高保真转 md""论文/专利 PDF 提取成 markdown"等任务一律先用本 skill。纯 MinerU，不绑定任何项目；令牌 DPAPI 本机存储。
---

# mineru-md · MinerU 转 Markdown

> **一句话**：文件 → MinerU 精准解析 API → `full.md + images/`，公式、表格、双栏、扫描件高保真，图片全部本地落盘（md 内为相对路径引用）。
> 逐条命令封装在 `scripts/mineru_parse.py`，本文件只留用法与规矩。

## 红线（动笔前先过一遍）

1. **信任域红线：API 是云端服务，只发可公开的文档**。敏感/未发布材料走本地引擎（docling / pymupdf4llm / pandoc），不许碰本 skill。
2. 限制：单文件 ≤200 MB、≤200 页；每账号每天 1000 页高优先级额度（超出自动降优先级，仍可继续跑）。
3. 默认只用官方 API，不装本地 MinerU——无 GPU 环境跑不动，API 额度对日常用量足够。
4. 产物图片是哈希名（如 `images/xxx.jpg`），无语义——需要语义命名时逐张看图重命名并同步改 md 引用。

## 令牌（一次性配置）

解析顺序：环境变量 `MINERU_API_TOKEN` → `%LOCALAPPDATA%\mineru-md\mineru-cred.xml`（Windows DPAPI 存储）。令牌在 [MinerU 官网](https://mineru.net/) 注册后于「API Token」页申请。

```powershell
# 首选：脚本子命令（PowerShell 掩码输入，令牌不经 AI；在自己开的终端里跑，别放非交互 shell）
python <本skill>\scripts\mineru_parse.py store-token

# 等价手动版（DPAPI 加密，绑定当前 Windows 账户）：
$tok = Read-Host -AsSecureString "粘贴 MinerU API Token"
New-Item -ItemType Directory -Force -Path "$env:LOCALAPPDATA\mineru-md" | Out-Null
Export-Clixml -InputObject $tok -Path "$env:LOCALAPPDATA%\mineru-md\mineru-cred.xml"
```

⚠️ 沙箱里 Python 直调 DPAPI 会被硬杀（exit 127），脚本已改为经 powershell 子进程解密——别改回 ctypes。

## 基本用法

```bash
python <本skill>\scripts\mineru_parse.py parse --file "<in.pdf>" --out-dir "<输出目录>" --model vlm
# 产出：<输出目录>/<stem>/full.md + images/（哈希名 jpg）+ layout.json 等
```

| 参数 | 说明 |
|---|---|
| `--model vlm\|pipeline` | 默认 `vlm`（官方推荐；复杂排版/公式/表格实测都好） |
| `--is-ocr` | 强制 OCR（扫描件/图片型 PDF 开） |
| `--lang` | 默认 `ch`；英文文献传 `en` |
| `--no-formula` / `--no-table` | 关公式/表格识别（默认全开） |
| `--timeout` / `--poll` | 轮询总时限（秒，默认 900）与间隔（默认 10） |

- **`--out-dir` 会自动追加一层 `<stem>` 子目录**（zip 内含同名文件夹）。
- 已存在的输出目录**不覆盖直接退出**——这是批量断点续作的守卫，见下。

## 批量

```bash
for f in *.pdf; do
  python <本skill>\scripts\mineru_parse.py parse --file "$f" --out-dir "<共享out目录>" --model vlm --lang en --timeout 540
done
```

- **完成判据 = `<out>/<stem>/full.md` 在不在，不看 rc**：输出目录已存在的守卫报错 rc=1，但产物已在 = 先前已解析成功，直接复用别重解析。驱动脚本据此跳过已完成文件（断点续作）。
- born-digital PDF 实测约 25 秒/篇；解析失败等 30s 重试一次；**md 文本 <1KB 加 `--is-ocr` 重试**。
- 并发安全：临时 zip 名已带 pid，多进程并发解析不互抢临时文件；但**同一文件写同一 out-dir 仍互斥**，且放量前先跑通 1 份确认网络/额度正常。

## 实测坑（每条都踩过）

| 坑 | 处置 |
|---|---|
| 🔴 外网代理截流 mineru.net（SSL EOF / 10060 超时） | `curl -w "%{http_code}" https://mineru.net` 诊断——通=端点没问题；关代理后立刻恢复。脚本 `api_req` 已带 3 次连接级重试 |
| 预签名 OSS 上传带任何额外头（连 Content-Type 都不行）→ 403 | 脚本已用 curl 最小头裸传（`-H "Content-Type;"`），别改回 urllib 直传 |
| 扫描件/老 PDF 别预判加 OCR | 先 `vlm` 直解（老扫描件公式段落俱佳），md 文本 <1KB 再 `--is-ocr` 重试；很多"看起来是扫描件"的其实是电子版 |
| 表格内嵌图偶发输出为 HTML `<img src="images/…">` 而非 markdown | 后处理 md 时两种引用都要数、都要处理 |
| 图片哈希名无语义 | 看图（或读图前 260 字符语境）重命名为内容概括，文件与 md 引用同步改 |
| 令牌失效/找不到 | 重跑 `store-token`；非 Windows 用环境变量 `MINERU_API_TOKEN` |

## 排查顺序

1. `curl -w "%{http_code}" https://mineru.net` —— 不通=本地网络/代理问题，先关代理。
2. 令牌在不在（`%LOCALAPPDATA%\mineru-md\mineru-cred.xml`）—— 不在跑 `store-token`。
3. 单文件小样试跑 —— 通了再放量批量。
