# mail163-draft

把结构化信件 JSON 自动排版成标准公文/书信格式（中文仿宋、英文数字 Times New Roman、小四、1.5 倍行距、称呼顶格、正文首行缩进两字符、署名日期右对齐），通过浏览器自动化登录 163 邮箱后直接存入草稿箱——**只存草稿，绝不发送**。

## 功能与适用场景

- 目标邮箱为网易免费邮（163 / 126 / yeah.net，mail.163.com，Coremail js6 架构）。
- 适合让 AI 助手代笔的正式邮件场景：给老师/单位的申请信、公务函、正式书信。你口述要点，AI 起草成得体的公文文字，渲染成排版规范的 HTML 草稿存进草稿箱；你到网页邮箱核对排版后**手动发送**。
- 安全设计：保存请求的 `action` 写死 `"save"`，代码路径中不存在发送/定时发送动作；只操作草稿箱（fid=2），不动已有邮件。
- 技术路线（实测定案）：163 的 URS 网页登录有指纹/PoW/风控反爬，纯 HTTP 登录不可行，因此登录必须走浏览器自动化；登录成功后在 webmail 页面内用 `fetch` 同源调 js6 API 存草稿。

## 依赖

| 依赖 | 用途 |
| --- | --- |
| Python 3.8+ | `mail163_format.py` 仅用标准库（argparse/json/re/html/pathlib） |
| 浏览器自动化能力（如 ZCode 的 browser-use / Playwright） | Step 3 登录 + Step 4 页面内 fetch 存草稿，均须在真实浏览器里执行 |
| 主流桌面浏览器 | 163 风控对无头/非浏览器环境不友好 |

## 安装部署

1. 把整个 `mail163-draft/` 目录复制到你的 AI 助手技能目录（ZCode 为 `~/.zcode/skills/`，目录名保持不变；任何遵循 SKILL.md 约定的框架均可使用）。
2. 确认 Python 可用；排版脚本无需第三方库。
3. 确认你的 AI 助手具备浏览器自动化工具（登录环节必需）。

## 需要配置的信息

首次使用：复制 `scripts/config.example.json` 为 `scripts/config.json`，按下表填写。

| 字段 | 说明 |
| --- | --- |
| `username` | 完整邮箱地址，如 `yourname@163.com` |
| `password` | 邮箱密码或客户端授权码 |
| `display_name` | 草稿发件人显示名，如 `张三`；留空则用邮箱账号默认名 |
| `password_verified` | 布尔值，表示密码是否已登录实测通过；首次填写保持 `false`，登录成功后由流程维护 |

`config.json` 含明文密码，只存本机，不要提交到 git。

## 基本用法

1. 起草信件 JSON（格式见 `scripts/letter.example.json`）：

   ```json
   {
     "to": ["teacher@example.com"],
     "cc": [],
     "subject": "关于XXX的申请",
     "salutation": "尊敬的X老师：",
     "paragraphs": ["第一段……", "第二段……"],
     "closing": true,
     "signature": "学生：<你的姓名>",
     "date": "2026年9月30日"
   }
   ```

2. 渲染公文 HTML：

   ```bash
   cd ~/.zcode/skills/mail163-draft/scripts
   python mail163_format.py letter.json -o letter.html --pretty
   ```

3. AI 助手浏览器自动化打开 `https://mail.163.com/` 登录（只尝试 1 次；遇滑块/验证码立即中止，转人工登录）。
4. 在已登录页面内用 `fetch` 调 `mbox:compose` 保存草稿（`action: "save"`），流程细节见 SKILL.md Step 4。
5. 回读草稿箱（`fid=2`）核对主题命中，提醒用户去网页邮箱核对排版并自行发送。

## 注意事项

- **凭据安全**：`config.json` 只存本机、不进 git；换机需重新配置。
- **登录克制**：163 风控严格，登录自动化只试 1 次，绝不反复重试密码；出现滑块/二次验证立即中止，请用户手动登录后继续。
- **绝不发送**：本 skill 的能力边界就是"存草稿"；发送永远由用户在网页邮箱手动完成。
- 存草稿失败不自动重试超过 1 次，如实报告错误。
- 无收件人/无主题的草稿一律拒绝保存（防误存）。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
