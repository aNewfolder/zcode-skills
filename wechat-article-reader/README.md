# wechat-article-reader

完整阅读并归档微信公众号推文（mp.weixin.qq.com）：每篇生成一个文件夹，Markdown 正文 + 本地化的图片/视频/音频，frontmatter 含标题、发布日期、公众号名称、原文链接等元数据；另支持免登录读取指定公众号的最新推送生成日报。

## 功能与适用场景

- **单篇/批量归档**：输入一篇或多篇推文链接（或 txt 链接清单），抓取正文转 Markdown，正文插图、直链视频、语音等媒体尽量下载到本地 `media/` 并改写为相对路径引用，同时保留原始网页快照 `source.html` 备查。
- **媒体处理（尽力而为）**：mpvideo 直链视频离线为 mp4；腾讯视频 iframe 留在线链接；视频号卡片存封面并标注；无法离线的在对应位置标注并在文末汇总。
- **日报模式（读公众号最新推送）**：通过微信公众平台的搜索接口（searchbiz → appmsg）枚举任意公众号的最新文章列表，逐篇归档并生成 `YYYY-MM-DD_公众号日报.md` 索引，自动去重（状态存 `.wechat_articles_state.json`）。
- **风控兜底**：被风控时（`[RISK]`）可把浏览器另存的页面 HTML 用 `--html-file` 离线转换，元数据反而更全。
- 适用场景：保存推文进笔记、下载公众号文章里的图片视频、从推文整理清单、订阅/日报式跟踪公众号更新。

## 依赖

- **Python 3**：两个脚本（`scripts/fetch_wechat_article.py`、`scripts/fetch_latest.py`）均为纯标准库实现（urllib/argparse/html.parser 等），**无需 pip 安装任何第三方包**。
- 日报模式的 `fetch_latest.py` 会动态加载同目录的 `fetch_wechat_article.py`，两个脚本需放在同一目录。

## 安装部署

将本目录（`wechat-article-reader/`，目录名保持不变）复制到 AI 助手的技能目录：

```
~/.agents/skills/wechat-article-reader/
```

本 skill 遵循 SKILL.md 约定，任何支持该约定的 AI 助手框架均可使用。

## 需要配置的信息

- **单篇抓取：零配置**，无需任何账号即可抓取任意公众号的公开推文。
- **日报模式：微信公众平台凭证 `mp_cookie.json`**（一次性，失效后重做）：
  1. 用个人微信号免费注册一个订阅号（个人主体即可，无需认证）并登录 mp.weixin.qq.com；
  2. 从地址栏 URL 复制 `token=xxx`；
  3. F12 → Network → 刷新页面 → 点第一条请求 → Request Headers → 复制整串 `Cookie:` 值；
  4. 存为 `scripts/mp_cookie.json`，格式：`{"token": "xxx", "cookie": "整串Cookie"}`。
- 该文件是登录凭证，**只保存在本机，绝不能提交到仓库或分享**。

## 基本用法

```bash
# 单篇归档到指定目录
python ~/.agents/skills/wechat-article-reader/scripts/fetch_wechat_article.py \
  "https://mp.weixin.qq.com/s/xxxxx" -o "<输出目录>"

# 批量：txt 文件每行一条链接；--text-only 只提取文字不下载媒体
python ~/.agents/skills/wechat-article-reader/scripts/fetch_wechat_article.py links.txt -o "<输出目录>"
python ~/.agents/skills/wechat-article-reader/scripts/fetch_wechat_article.py "url" --text-only -o "<输出目录>"

# 风控兜底：转换浏览器另存的页面 HTML
python ~/.agents/skills/wechat-article-reader/scripts/fetch_wechat_article.py --html-file "保存的页面.html" -o "<输出目录>"

# 日报模式：读公众号最新 3 篇并生成日报索引
python ~/.agents/skills/wechat-article-reader/scripts/fetch_latest.py \
  --mp ~/.agents/skills/wechat-article-reader/scripts/mp_cookie.json "公众号名称" --limit 3 -o "<日报目录>"
```

## 注意事项

- 凭证（mp_cookie.json）只存本机，不要提交仓库；抓取仅供个人学习记录，尊重原公众号版权，不要二次分发全文。
- **限流红线（封号风险）**：脚本已内置接口间 3~6 秒、文章间 2~4 秒间隔，不要调小、不要并发；`ret=200013` 触发频率限制 = 当天停手，隔天再试；每天每账号最多 1~2 次列表调用、单次 `--limit ≤ 5`；新注册账号需先在后台网页手动搜一次目标账号"暖号"。
- 支持 `https://mp.weixin.qq.com/s/...` 和 `/s?__biz=...` 两类链接；微信聊天里的中间跳转短链可能失败，从 PC 微信复制真实链接再试。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
