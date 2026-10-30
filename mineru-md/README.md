# mineru-md（MinerU 转 Markdown）

用 [MinerU](https://mineru.net/) 官方精准解析 API，把 PDF / 图片 / Office 文档高保真转成 Markdown：公式、复杂表格、双栏排版、扫描件都能处理，产物里的图片全部本地落盘（md 内为相对路径引用）。

**给 AI 看的操作手册（触发条件、红线、实测坑）在同目录 [`SKILL.md`](SKILL.md)；本 README 写给人看：怎么装、要配什么、怎么用。**

## 依赖

- Python 3.8+（仅标准库，**无第三方依赖**）
- `curl` 在 PATH 里（上传文件用最小头裸传，绕过 OSS 签名对额外头的限制）
- Windows 下存令牌用 PowerShell（DPAPI）；非 Windows 直接用环境变量 `MINERU_API_TOKEN`
- 一个 MinerU API Token：官网注册后在「API Token」页申请

## 安装

把整个目录复制到 AI 助手技能目录（ZCode：`~/.zcode/skills/`；其他框架按各自约定），或当作普通脚本项目放任意位置。目录名保持 `mineru-md` 不变。

## 需要配置的信息

只有一样：**MinerU API Token**。三选一：

1. **推荐（Windows）**：在你自己开的终端里跑掩码输入，令牌不经过 AI：
   ```powershell
   python scripts\mineru_parse.py store-token
   ```
   令牌以 DPAPI 加密存到 `%LOCALAPPDATA%\mineru-md\mineru-cred.xml`，绑定当前 Windows 账户。
2. **环境变量**（跨平台）：`set MINERU_API_TOKEN=...`（或 export），脚本自动优先读取。
3. 手动生成 cred 文件（等价 1）：`Export-Clixml` 存 SecureString 到上面那个路径。

## 用法

```bash
# 单文件（产出 <out-dir>/<stem>/full.md + images/ + layout.json 等）
python scripts/mineru_parse.py parse --file paper.pdf --out-dir out --model vlm

# 英文文献 / 扫描件
python scripts/mineru_parse.py parse --file scan.pdf --out-dir out --is-ocr --lang en

# 批量（断点续作：已完成的输出目录存在即跳过）
for f in *.pdf; do
  python scripts/mineru_parse.py parse --file "$f" --out-dir out --model vlm --lang en
done
```

常用参数：`--model vlm|pipeline`（默认 vlm）、`--is-ocr` 强制 OCR、`--lang ch|en`、`--no-formula` / `--no-table`、`--timeout` / `--poll`。完整表见 SKILL.md。

## 注意事项

- **信任域红线**：MinerU 是云端 API，只上传可公开的文档；敏感/未发布材料请用本地引擎（docling / pymupdf4llm / pandoc）。
- 单文件 ≤200 MB、≤200 页；每账号每天 1000 页高优先级额度。
- 产物图片是哈希名（`images/xxx.jpg`），需要语义命名时逐张看图重命名并同步改 md 引用。
- 完成判据看 `<out>/<stem>/full.md` 在不在，别看退出码（输出目录已存在的守卫报 rc=1 是正常的断点续作守卫）。
- 代理开着时 mineru.net 可能被截流（SSL EOF / 10060），先 `curl -w "%{http_code}" https://mineru.net` 诊断。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
