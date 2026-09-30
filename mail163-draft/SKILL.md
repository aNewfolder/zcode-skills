---
name: mail163-draft
description: 163邮箱（mail.163.com，网易Coremail）草稿箱工具：浏览器自动化登录后，把邮件文稿按标准公文/书信格式排版好直接存入草稿箱。当用户说"用163邮箱给xx写封邮件"、"把这封信放进163草稿箱"、"163邮箱起草邮件"、"写封正式的信"等，且目标邮箱是163/126/yeah.net等网易免费邮时使用。排版自动满足：中文仿宋、英文数字 Times New Roman、小四、1.5 倍行距、称呼顶格、正文首行缩进两字符、署名日期右对齐。绝对只存草稿、绝不发送；发送由用户在网页邮箱手动完成。
---

# 163邮箱公文草稿（mail163-draft）

**核心资产（勿改动位置）：**

- 排版脚本：`~/.zcode/skills/mail163-draft/scripts/mail163_format.py`（信件 JSON → 公文 HTML）
- 凭据：`scripts/config.json`（username 全称 / password / display_name；password_verified 由登录流程维护）。首次使用：复制 `scripts/config.example.json` 为 `scripts/config.json` 并填入你的账号
- 信件示例：`scripts/letter.example.json`

**技术路线（2026-09-21 实测定案）：** 163 的 URS 网页登录有指纹/PoW/风控反爬，纯 HTTP（requests）登录不可行（所有手工构造请求均被 401/02 拒绝，含从真实页面同源发起的请求）。因此登录走**浏览器自动化**；登录成功后 webmail 的 cookie 全部非 HttpOnly、sid 在 URL 里，写信/存草稿在**页面内用 fetch 直接调 js6 API**（同源、自动带 cookie），已端到端实测 S_OK。**不要**尝试用 requests 复刻 URS 登录链，浪费时间。

## ⚠️ 安全红线（任何情况下不得违反）

1. **绝不发送**：保存请求的 `action` 字段写死 `"save"`。**不存在也不得调用** `"deliver"`、`"schedule"`、`"verify"`、定时发送、发送按钮（UI 里的"发送"按钮绝不点击）。草稿发送永远由用户在网页邮箱手动完成。写完提醒用户去草稿箱核对并自行发送。
2. **登录尝试克制**：163 风控严格，密码错误或环境异常会触发滑块/短信验证。登录自动化**只尝试 1 次**；出现滑块验证码、二次验证、报错弹窗时**立即中止**，转告用户手动登录浏览器后继续。绝不反复重试密码。
3. 存草稿失败不自动重试超过 1 次；如实报告错误。
4. 在草稿编辑器页面只读不动：不点击"发送/存草稿/取消"等任何按钮，离开时点左侧文件夹导航。
5. 只操作草稿箱（fid=2）；不移动/删除/修改用户已有邮件。

## 标准流程（五步）

### Step 1：起草信件 JSON

根据用户口述把信写成得体的公务/书信文字，存为 JSON（如 `scripts/letter.json`）。用户明确给出的关键句子必须原样保留。字段：

```json
{
  "to": ["addr@example.com"],
  "cc": [],
  "subject": "关于XXX的申请",
  "salutation": "尊敬的X老师：",
  "paragraphs": ["第一段……", "第二段……"],
  "closing": true,
  "signature": "学生：<你的姓名>",
  "date": "2026年9月21日"
}
```

- `to` 也可直接给字符串（逗号/分号分隔多地址，脚本会切分并校验 @）。
- `closing: true`（默认）自动加"此致（缩进2字符）/ 敬礼！（顶格）"。
- `date` 缺省时应显式写今天中文格式；传 `""` 去掉日期行。
- 无收件人/无主题的草稿一律拒绝保存（防误存）。

### Step 2：渲染公文 HTML

```bash
cd ~/.zcode/skills/mail163-draft/scripts
python mail163_format.py letter.json -o letter.html --pretty
```

排版由脚本内置（勿改）：中文仿宋、英文数字 Times New Roman（字体栈 `"Times New Roman", FangSong, 仿宋, …` 顺序混排）、小四（12pt）、1.5 倍行距、称呼顶格、正文每段首行缩进 2em、"此致"缩进 2em/"敬礼！"顶格、署名与日期右对齐。全部以内联 style 写入 HTML。

### Step 3：浏览器登录（browser-use，iab 后端）

1. 打开 `https://mail.163.com/`。若 URL 自动落到 `https://mail.163.com/js6/main.jsp?sid=…` 即已登录，直接进 Step 4。
2. 未登录则自动化登录（只试一次）：
   - 登录表单在跨域 iframe 里：`tab.playwright.frameLocator('iframe[id^="x-URS-iframe"]')`；
   - 账号框 `getByRole("textbox", { name: "邮箱账号或手机号码" })`，**只填 @ 前缀**（如 `<你的邮箱前缀>`，"@163.com" 是页面后缀标签）；iframe 内 input 无法跨域 evaluate，全部用 frameLocator 定位器操作；
   - 密码框 `frameLocator(...).locator('input[name="password"]')`（type=password 的那个；另一个 `pwdtext` 是明文镜像框）；
   - 建议勾选"30天内免登录"复选框；
   - 点"登 录"：frameLocator 的 link 点击在 iab 里常超时，**用截图定位后 `tab.cua.click({x,y})` 点蓝色登录按钮**（1280x720 视口约在 993,430，以实际截图为准）；
   - 成功标志：页面跳转到 `js6/main.jsp?sid=<SID>`。
3. 出现滑块/验证码/错误提示 → 立即中止，请用户手动登录后重新执行本 skill。

### Step 4：页面内 fetch 存草稿（唯一的写操作）

在已登录的 webmail 标签页执行 `tab.playwright.evaluate`（同源自动带 cookie），两步走：

```js
const sid = location.search.match(/sid=([^&]+)/)[1];
const gw = `/js6/s?sid=${sid}&func=mbox:compose`;
const post = (body) => fetch(gw, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
}).then(r => r.text());
// ① 创建写信会话，从 XML 里取 composeId
const t1 = await post({});
const cid = t1.match(/<string name="var">([^<]+)<\/string>/)[1];
// ② 保存草稿 —— action 写死 "save"，绝不允许其他值
const attrs = {
  account: '"<显示名>" <user@163.com>',
  to: ["...@..."], cc: [], bcc: [], showOneRcpt: false,
  subject: "...", isHtml: true, content: <Step2 的 HTML>,
  priority: 3, requestReadReceipt: false, saveSentCopy: true,
  charset: "UTF-8", attachments: [],
};
const t2 = await post({ id: cid, attrs, returnInfo: true, action: "save" });
// 成功标志：<code>S_OK</code>
```

响应是 **XML**（非 JSON）。`/js6/common/mbox/compose.jsp` 老端点在 163 **不存在**（返回错误页），别用；163 也没有 `mbox:moveMessages`。

### Step 5：回读验证 + 报告

```js
const list = await post2("mbox:listMessages",
  { start: 0, limit: 5, order: "receivedDate", desc: true, returnTotal: true, fid: 2, mboxa: "" });
// fid：1收件箱 2草稿箱 3已发送 4已删除。核对 subject 命中即可
```

向用户报告草稿主题与收件人，并提醒：**去网页邮箱草稿箱核对排版后自行发送**。

## API 速查（排查异常时读）

1. 网关：`POST https://mail.163.com/js6/s?sid=<SID>&func=<func>`，body JSON，响应 XML（`<code>S_OK|FA_…</code>`）。
2. `func=mbox:compose`：body `{}` 创建写信会话返回 composeId（`var` 字符串）；带 `{id:<composeId>, attrs, returnInfo:true, action:"save"}` 保存草稿。attrs 必含 `account`（`"显示名" <user@163.com>` 格式）、`to`（纯地址字符串数组）、`subject`、`isHtml:true`、`content`（HTML 字符串，服务器原样入库）。
3. `FA_SECURITY` = 会话/cookie 无效（纯 sid 不够，必须带浏览器 cookie）→ 回 Step 3 重新登录。
4. `mbox:readMessage` body `{id:"<mid>"}` 可读草稿元信息；正文以 quoted-printable text/html 存储。
5. URS 登录协议（仅备查，勿试图纯 HTTP 复刻）：`POST dl.reg.163.com/dl/zj/mail/gt`（拿 tk）→ `POST /dl/zj/mail/l`（pw = 内置 RSA 公钥 PKCS#1 加密）。公钥与参数格式见 urs 组件分析；缺 `rtid`/指纹等字段会被 401/02 拒绝。
6. 会话寿命：勾选"30天内免登录"后浏览器 profile 内登录态可跨运行复用；失效则重跑 Step 3。

## 已验证案例

- 2026-09-21 全链路端到端：浏览器自动登录 → 页面内 fetch 存公文排版草稿 → S_OK → 草稿箱回读命中 → 打开草稿编辑器截图核对：称呼顶格、正文首行缩进两字符、英文/数字 Times New Roman、中文仿宋、小四、1.5 倍行距、此致缩进/敬礼顶格、署名日期右对齐——全部符合。
- 2026-09-21 反爬实测：requests 直连 `/dl/zj/mail/ini`、`/dl/zj/mail/gt` 均 401/02（补 rtid、Origin/Referer、从真实 URS 页面同源发起、agent iframe 发起均无效）；`/js6/s/json`、`/js6/common/mbox/compose.jsp`、`mbox:moveMessages` 在 163 不存在。结论：登录必须走浏览器，API 必须页面内 fetch。
- 遗留：草稿箱内有两封 2026-09-21 的"【测试】…可删除"测试草稿（一封公文排版演示、一封简陋格式），可手动删除。
