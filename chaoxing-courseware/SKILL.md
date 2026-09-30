---
name: chaoxing-courseware
description: 通过超星学习通（chaoxing.com / xuexi.cn，mooc1/mooc2-ans 域名）Web API 爬取课程章节列表并下载章节里的文件课件（pptx/docx/pdf 等）。当用户给出 mooc2-ans.chaoxing.com 或 mooc1.chaoxing.com 的课程/学习链接，要求"下载课件"、"爬课程文件"、"下载绪论/某章 ppt"、"把超星资料存下来"，或提到学习通课程下载时使用。登录走账号密码 API（AES-CBC），Cookie 持久化后无需重复登录。不用于自动刷课、答题或签到。
---

# 超星学习通课程课件下载

**核心资产（勿改动位置）：**

- 脚本：`~/.zcode/skills/chaoxing-courseware/scripts/chaoxing_dl.py`（即 `<skill 安装目录>/scripts/chaoxing_dl.py`）
- Cookie：`~/.zcode/skills/chaoxing-courseware/scripts/cookies.json`（首次 `login` 后自动生成；过期重新 login）

## 快速使用

```bash
cd ~/.zcode/skills/chaoxing-courseware/scripts

# 首次或 Cookie 过期时：账号密码登录（保存 cookies.json）
python chaoxing_dl.py login <手机号/超星号> "<密码>"

# 列出课程全部章节
python chaoxing_dl.py list "<课程页URL>"

# 列出匹配章节及其中的文件
python chaoxing_dl.py list "<课程页URL>" --chapter 绪论 --files

# 下载匹配章节的全部文件课件（--flat 直接放目标目录，否则建章节子目录）
python chaoxing_dl.py download "<课程页URL>" --chapter 绪论 --out "<输出目录>" --flat
```

- `<课程页URL>`：课程首页（`/mooc2-ans/mycourse/stu?courseid=...&clazzid=...&cpi=...&enc=...`）或章节学习页（`/mycourse/studentstudy?chapterId=...`）均可，脚本自动取参数。
- `--chapter` 支持标题关键词（如"绪论"）、编号（如 2.1）或 chapterId，可多次出现的部分匹配会全部选中；省略则处理全部章节。
- `--type document|video|all` 默认只下文件课件；`--type all` 包含视频等全部附件（体积大，慎用）。
- 依赖：`requests`；login 模式额外需要 `pycryptodome`（安装方式见本目录 README.md）。

## 协议要点（排查异常时读）

1. **登录**：POST `passport2.chaoxing.com/fanyalogin`。与官方 login.js 一致：`uname` 和 `password` 都要用 AES-128-**CBC** 加密（key=iv=`u2oh6Vu^HWe4_AES`，PKCS7，结果 base64），参数名是 `forbidotherlogin`（不是 forbidother）。fid 从登录页表单读取。成功后 Session 里会拿到完整 Cookie，**必须保留 `vc3`**（HttpOnly，浏览器 F12 里 document.cookie 读不到，所以手工复制浏览器 Cookie 会缺字段——用 login 子命令最稳）。
2. **章节列表**：GET `mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid=&clazzid=&cpi=&ut=s&t=<毫秒>&stuenc=<enc>`，HTML 里每个章节是 `<div class="chapter_item" id="cur<chapterId>" ... title="<标题>">`，编号在紧跟其后的 `catalog_sbar` span 里。
3. **知识卡片**：章节正文按卡片编号遍历：GET `mooc1.chaoxing.com/mooc-ans/knowledge/cards?clazzid=&courseid=&knowledgeid=<chapterId>&num=<0,1,2...>&ut=s&cpi=&mooc2=1`。卡片 HTML 内嵌 `mArg = {json}`（注意页面里先有 `mArg = "";` 空声明，要匹配 `mArg = {`），`attachments[].property` 里有 objectid/name/type/size；`attachments[].type == "document"` 为文件课件。num 超出卡片数时解析不到 mArg，以此作为停止条件。
4. **下载直链**：GET `mooc1.chaoxing.com/ananas/status/<objectid>?k=&flag=4` 返回 JSON，`download` 字段即直链，`filename` 是真实文件名。**此接口校验 Referer 防盗链**，必须带 `Referer: https://mooc1.chaoxing.com/mooc-ans/knowledge/cards`，否则 403。
5. Cookie 有效期较长（数周量级）；若 list 报"未找到章节/可能过期"，直接重新 login。

## 已验证案例

- 2026-09 某高校课程（courseid 从课程页 URL 中取得，可用 `list` 命令确认）：约 30 个章节解析正常；按标题关键词（如"绪论"）匹配章节后成功批量下载全部 pptx 课件（含 14 页与 112 页两份），文件名与页数完好。
