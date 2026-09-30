# zjuem-mail-draft

浙江大学邮箱（zjuem.zju.edu.cn / mail.zju.edu.cn，Coremail）草稿箱工具：统一认证 API 登录后，把结构化信件 JSON 自动排版成标准公文/书信格式存入草稿箱——**只存草稿，绝不发送**。

## 功能与适用场景

- 目标邮箱为浙大邮箱（@zju.edu.cn），走浙大统一认证（zjuam CAS）SSO 登录，纯 HTTP 实现，无需浏览器。
- 适合让 AI 助手代笔的正式邮件场景：给老师的申请信、公务函、正式书信。你口述要点，AI 起草成得体的公文文字，脚本渲染成排版规范的 HTML 存进草稿箱；你到网页邮箱核对排版后**手动发送**。
- 排版规格（脚本内置）：中文仿宋、英文数字 Times New Roman（字体栈顺序混排）、小四（12pt）、1.5 倍行距、称呼顶格、正文每段首行缩进 2 字符、"此致"缩进/"敬礼！"顶格、署名与日期右对齐；全部内联 style，邮件客户端打开即所见。
- 安全设计：保存请求的 `action` 写死 `"save"`，代码中不存在任何发送动作；全局限速（相邻请求 ≥2.5 秒）+ 单实例锁；登录失败额度控制（1 小时内凭据类失败 2 次即锁定）；要求图形验证码时立即中止。

## 依赖

| 依赖 | 用途 |
| --- | --- |
| Python 3.8+ | 运行 `zjuem_mail.py` |
| `requests` | 唯一的第三方库（`pip install requests`） |
| 浙大统一认证账号 | 需在校网/VPN 环境访问 zju.edu.cn 域名 |

## 安装部署

1. 把整个 `zjuem-mail-draft/` 目录复制到你的 AI 助手技能目录（ZCode 为 `~/.zcode/skills/`，目录名保持不变；任何遵循 SKILL.md 约定的框架均可使用）。
2. `pip install requests`。

## 需要配置的信息

首次使用：复制 `scripts/config.example.json` 为 `scripts/config.json`，按下表填写。

| 字段 | 说明 |
| --- | --- |
| `username` | 浙大学号（统一认证用户名） |
| `password` | 统一认证密码（仅支持 ASCII，非 ASCII 本地直接拒绝） |
| `display_name` | 草稿发件人显示名，如 `张三`；留空则不加显示名 |
| `password_verified` | 布尔值；首次填写保持 `false`，登录成功后脚本自动置 `true` |

运行时自动生成/维护的文件（均在本目录，不要提交）：`session.json`（登录会话，有效期内复用）、`state.json`（登录失败计数）、`.lock`（单实例锁）、`last_login_fail.html`（登录失败页面存档）。

## 基本用法

```bash
cd ~/.zcode/skills/zjuem-mail-draft/scripts

# 首次登录验证密码（此后会话自动复用）
python zjuem_mail.py login --unverified-ok

# 校验/恢复会话
python zjuem_mail.py check

# 存草稿：结构化 JSON（推荐，字段见下），--verify 保存后回读核对
python zjuem_mail.py draft --json-file letter.json --verify

# 存草稿：命令行参数快速用法
python zjuem_mail.py draft --to "prof@zju.edu.cn" --subject "关于XXX的申请" \
  --salutation "尊敬的X老师：" --body "第一段
第二段" --signature "学生：<你的姓名>" --verify

# 看草稿箱最近 20 封
python zjuem_mail.py list-drafts
```

letter.json 格式（完整示例参考 skill 内文档；`closing: true` 自动加"此致/敬礼！"，`date` 缺省自动填今天中文格式）：

```json
{
  "to": ["prof@zju.edu.cn"],
  "subject": "关于XXX的申请",
  "salutation": "尊敬的X老师：",
  "paragraphs": ["您好！我是xxx。……（第一段）", "……（第二段）"],
  "closing": true,
  "signature": "学生：<你的姓名>",
  "date": "2026年9月30日"
}
```

## 注意事项

- **凭据安全**：`config.json` 含明文密码，只存本机、不进 git；`session.json` 含登录 Cookie，同样不要外传。
- **登录尝试很昂贵**：统一认证连续错约 5 次会锁账号。脚本已内置失败锁定与"密码未验证不自动重登"保护，不要绕过；密码错误时绝不反复重试。
- **绝不发送**：本 skill 的能力边界就是"存草稿"；发送永远由用户在网页邮箱手动完成。
- 限速与串行是硬约束：不要并发请求、不要循环轮询、不要并行运行多个命令。
- 无收件人/无主题的草稿默认拒绝保存（防误存），确需时才用 `--allow-empty-to`。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
