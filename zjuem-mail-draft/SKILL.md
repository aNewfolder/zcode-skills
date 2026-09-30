---
name: zjuem-mail-draft
description: 浙江大学邮箱（zjuem.zju.edu.cn / mail.zju.edu.cn，Coremail）草稿箱工具：统一认证 API 登录后，把邮件文稿按标准公文/书信格式排版好直接存入草稿箱。当用户说"给xx写封邮件"、"把这封信放进草稿箱"、"用浙大邮箱写邮件"、"起草邮件"、"写封正式的信给老师"等，且目标邮箱是浙大邮箱时使用。排版自动满足：中文仿宋、英文数字 Times New Roman、小四、1.5 倍行距、称呼顶格、正文首行缩进两字符、署名日期右对齐。绝对只存草稿、绝不发送；发送由用户在网页邮箱手动完成。
---

# 浙大邮箱公文草稿（zjuem-mail-draft）

**核心资产（勿改动位置）：**

- 脚本：`~/.zcode/skills/zjuem-mail-draft/scripts/zjuem_mail.py`
- 凭据：`scripts/config.json`（username / password / display_name；password_verified 由脚本自动维护）。首次使用：复制 `scripts/config.example.json` 为 `scripts/config.json` 并填入你的账号
- 会话：`scripts\session.json`（login 后自动生成，有效期内复用，不重复登录）

## ⚠️ 安全红线（任何情况下不得违反）

1. **绝不发送**：本工具只存草稿，代码里不存在发送动作（保存请求 action 写死 `"save"`）。**不要**给它添加任何 send 功能；草稿发送永远由用户在网页邮箱手动完成。写完草稿后提醒用户去草稿箱核对并自行发送。
2. **限速与串行是硬约束**：脚本内置任意两次请求间隔 ≥2.5 秒 + 单实例锁。不要绕过脚本并发请求、不要循环轮询、不要并行运行多个命令。
3. **登录尝试很昂贵**：统一认证连续错约 5 次锁账号。脚本已内置：密码未验证（password_verified=false）时拒绝自动重登；1 小时内凭据类失败 2 次即锁定，`--allow-retry` 只能在人工确认失败原因并修复后用一次；要求图形验证码时立即中止。**密码错误时绝不反复重试。**
4. 无收件人 / 无主题的草稿默认拒绝保存（防误存），确需时才用 `--allow-empty-to`。
5. 密码仅支持 ASCII（统一认证加密算法本身对非 ASCII 有损，本地直接拒绝）。

## 快速使用

```bash
cd ~/.zcode/skills/zjuem-mail-draft/scripts

python zjuem_mail.py check          # 校验/恢复会话（失效会自动重登）
python zjuem_mail.py list-drafts    # 看草稿箱最近 20 封
```

### 存草稿（标准用法：结构化 JSON，推荐）

先根据用户口述把信写成 JSON（字段：to、cc、subject、salutation、paragraphs、closing、signature、date），再调用：

```bash
python zjuem_mail.py draft --json-file letter.json --verify
```

letter.json 示例（paragraphs 是正文段落数组，每段自动首行缩进 2 字符）：

```json
{
  "to": ["prof@zju.edu.cn"],
  "subject": "关于XXX的申请",
  "salutation": "尊敬的X老师：",
  "paragraphs": [
    "您好！我是xxx。……（第一段）",
    "……（第二段）"
  ],
  "closing": true,
  "signature": "学生：<你的姓名>",
  "date": "2026年9月21日"
}
```

- `closing: true`（默认）自动加"此致（缩进 2 字符）/ 敬礼！（顶格）"；不需要时设 false。
- `date` 缺省自动填今天中文格式；传 `""` 可去掉日期行。
- `--verify` 保存后回读草稿箱确认存在（建议总是带上）。

### 存草稿（快速用法：命令行参数）

```bash
python zjuem_mail.py draft \
  --to "prof@zju.edu.cn" \
  --subject "关于XXX的申请" \
  --salutation "尊敬的X老师：" \
  --body "第一段内容
第二段内容" \
  --signature "学生：<你的姓名>" \
  --verify
```

多收件人用逗号/分号分隔：`--to "a@x.com, b@y.com"`。

**给用户的文案约定**：正文内容由你（agent）根据用户口述起草成得体的公务/书信文字，再走上述命令；用户明确给出的关键句子必须原样保留。保存成功后向用户报告草稿主题和收件人，并提醒：**去网页邮箱草稿箱核对排版后自行发送**。

## 排版规格（脚本内置，勿改）

- 中文：仿宋（FangSong）；英文和数字：Times New Roman —— 由字体栈 `"Times New Roman", FangSong, 仿宋, ...` 顺序实现自动混排
- 字号：统一小四（12pt）；行距：统一 1.5 倍
- 书信缩进：称呼顶格 → 正文每段首行缩进 2 字符 → "此致"缩进 2 字符 → "敬礼！"顶格 → 署名、日期右对齐
- 以上全部以内联 style 写进 HTML，邮件客户端（含 Outlook）打开即所见

## API 要点（排查异常时读）

1. **登录链**（复用统一认证加密，与 xzzd-homework 同源）：`GET zjuem.zju.edu.cn/coremail/cmcu_addon/sso.jsp` → 302 到 `zjuam.zju.edu.cn/cas/oauth2.0/authorize`（client_id=dhcjnYjB1X8o15hDqh）→ 302 到 `/cas/login`（取 `execution`）→ `GET /cas/v2/getPubKey` RSA 公钥 → 密码 UTF-16 码元反转 + ohdave RSA 分块加密 → POST 表单 → `callbackAuthorize?ticket` → `sso.jsp?code` → **mail.zju.edu.cn 域 Cookie `Coremail.sid`**（其值即 sid，登录成功的标志）。
2. **写草稿两步**：① `POST /coremail/s/json?sid=X&func=mbox:compose` body `{}` → S_OK，`var` 即服务器生成的 composeId（毫秒时间戳字符串；带陌生 id 调用会报 FA_ID_NOT_FOUND）；② `POST /coremail/common/mbox/compose.jsp?isUserConfirmed=true&sid=X`，JSON body `{"id":"<composeId>","attrs":{account,to,cc,bcc,subject,isHtml:true,content,attachments:[],saveSentCopy:true,...},"returnInfo":true,"encryptPassword":"","action":"save"}`。`account` 必须是完整地址（`"显示名" <user@zju.edu.cn>` 或 `user@zju.edu.cn`），纯学号报 FA_INVALID_ACCOUNT。
3. **to/cc/bcc 格式**：纯地址字符串数组，如 `["<学号>@zju.edu.cn"]`（已实测 UI 回读一致）。
4. **草稿箱列表**：`POST /coremail/s/json?sid=X&func=mbox:listMessages` body `{"start":0,"limit":20,"order":"receivedDate","desc":true,"returnTotal":true,"fid":2,"mboxa":""}`（fid：1收件箱 2草稿箱 3已发送）。
5. **会话失效特征**：API 返回非 JSON / 302 → 重新登录即可（Coremail 会话与 CAS 会话独立，多端登录互不踢）。
6. 依赖：`requests`（已装）。Python 3.14。

## 已验证案例

- 2026-09-21 全链路端到端：登录 → 存草稿 → 草稿箱回读存在 → 浏览器打开草稿编辑器逐项核对计算样式：fontFamily 保留字体栈、fontSize 16px（=12pt 小四）、lineHeight 24px（=1.5 倍）、正文段 text-indent 32px（=2 字符）、署名/日期 text-align right——全部符合公文排版要求。收件人 to 回读格式与保存一致。
- 2026-09-21 会话复用与自动重登：session.json 失效后 check 自动走完整登录链并恢复。
