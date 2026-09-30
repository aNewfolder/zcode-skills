---
name: wechat-article-reader
description: 完整阅读并归档微信公众号推文（mp.weixin.qq.com），并支持免登录读取一个公众号的最新推送生成日报。输入一篇或多篇推文链接，或指定公众号/RSS 源，每篇生成一个文件夹：Markdown 正文 + 本地化图片/视频/音频，frontmatter 含标题、发布日期、公众号名称、原文链接等元数据；被风控时可走浏览器兜底流程。当用户给出微信推文或公众号文章链接，要求"读这篇推文 / 保存 / 转成 Markdown / 抓取内容 / 存进笔记 / 下载公众号文章图片视频 / 从推文里整理清单"，或要求"看某个公众号最新推送 / 做公众号日报 / 订阅公众号更新 / 批量抓最新文章"等时使用。
---

# 微信公众号推文完整阅读与归档

面向**用户提供链接的任意公众号单篇（或多篇）推文**。不需要登录任何账号即可抓单篇正文。

## 用法

脚本（纯标准库，无需 pip 安装）：

```
~/.agents/skills/wechat-article-reader/scripts/fetch_wechat_article.py
```

```bash
# 单篇，导出到指定目录（推荐显式给 -o）
python ~/.agents/skills/wechat-article-reader/scripts/fetch_wechat_article.py \
  "https://mp.weixin.qq.com/s/xxxxx" -o "<输出目录>"

# 批量：txt 文件每行一条链接
python "...fetch_wechat_article.py" links.txt -o "目录"

# 只提取文字、不下载媒体
python "...fetch_wechat_article.py" "url" --text-only -o "目录"

# 风控兜底：转换已保存到本地的页面 html（见下文兜底流程）
python "...fetch_wechat_article.py" --html-file "保存的页面.html" -o "目录"
```

输出结构（文件夹名 = 发布日期_标题，全部文件名做 Windows 安全处理）：

```
2026-08-26_某篇文章标题/
├── 某篇文章标题.md      ← 正文 Markdown
├── media/               ← img_0001.jpg…、video_001.mp4…、snap_cover_1000.jpg…
└── source.html          ← 原始网页快照（--no-source-html 可关）
```

md 开头 frontmatter（标题、公众号、作者、发布时间、摘要、统计都在）：

```yaml
---
title: "文章标题"
account: "公众号名称"
author: ""
publish_time: "2026-08-26 20:00"
source_url: "https://mp.weixin.qq.com/s/xxx"
fetched_at: "2026-08-27 13:30"
digest: "摘要前 150 字"
counts: {images: 12, videos_saved: 1, voice_saved: 0}
---
```

随后是 `# 标题`、一行信息引言（📰公众号 🗓时间 🔗原文链接），然后是正文。图片以相对路径引用：`![](media/img_0001.jpg)`。

## 媒体处理边界（尽力而为）

| 内容 | 结果 |
|---|---|
| 正文插图 | 全部下载到 media/ 并本地化引用 |
| mpvideo.qpic.cn 直链视频 | 直接离线为 video_xxx.mp4 |
| 腾讯视频 iframe（vid） | md 里留在线观看链接 |
| 视频号卡片 | 尽量存封面图 + 标注《标题》@频道名 |
| 公众号语音/音乐 | 尝试直链下载，失败则标注 |

无法离线的都会在 md 对应位置标注，并在文末汇总提示。如用户只要文字，加 `--text-only` 即可。

## 执行要点

1. **先确认输出位置**：用户指定了目录就用它；没指定就默认 `-o .` 输出到当前工作目录，完成后把绝对路径告诉用户。
2. **看退出码与输出**：
   - `[OK]` 开头 → 完成。向用户报告文件夹绝对路径 + 标题/日期/媒体统计。
   - `[RISK]`（退出码 2）→ 被风控，走下面的兜底流程。
   - `[FAIL]`（退出码 1）→ 读报错（常见：文章被删、需要微信客户端打开）；把原因转告用户，建议从微信里重新复制链接。
3. **批量时**脚本自带 1.5~3 秒随机间隔，不要另开并发、也不要把几十条塞进一次请求之外猛重试。

## 日报模式：读取公众号最新推送（公众平台 Cookie 路线，唯一通用方法）

免登录的枚举路线（搜狗/profile_ext/feeddd/搜索引擎）已全部验证失效，**不要再尝试**。
对任意公众号，唯一可行路线是登录微信公众平台的搜索接口（searchbiz → appmsg）。

第二个脚本（同样零依赖）：

```
~/.agents/skills/wechat-article-reader/scripts/fetch_latest.py
~/.agents/skills/wechat-article-reader/scripts/mp_cookie.json   ← 凭证（需自行创建，获取方式见下文）
```

```bash
# 单账号最新 1 篇（测试用，请求最少）
python ".../fetch_latest.py" --mp ".../mp_cookie.json" "浙江大学学生会" --limit 1 -o "日报目录"

# 多账号（每次 --mp 一对，cookie.json 复用）
python ".../fetch_latest.py" --mp ".../mp_cookie.json" "光电成像" --mp ".../mp_cookie.json" "光学工程" --limit 3 -o "目录"

# 已知 biz 的账号可跳过搜索直接取列表（少一次接口调用）
python ".../fetch_latest.py" --mp ".../mp_cookie.json" "biz:Mzg3MzcwOTM5Ng==" --limit 1
```

其他参数：`--since 2026-08-28`（只处理该日后）、`--no-export`（只出清单）、
`--no-dedupe`（默认已导出的自动跳过，状态存 `.wechat_articles_state.json`）、`--links-file`。
输出：每篇文章照旧一个文件夹 + `YYYY-MM-DD_公众号日报.md` 索引（✅/⚠️ 标记导出状态）。

### 凭证获取（一次性，失效后重做）

1. 用个人微信号免费注册订阅号并登录 mp.weixin.qq.com（个人主体即可，无需认证）；
2. 地址栏 URL 复制 `token=xxx`；
3. F12 → Network → 刷新页面 → 点第一条请求 → Request Headers → 复制整串 `Cookie:` 值；
4. 存为 `mp_cookie.json`：`{"token": "xxx", "cookie": "整串Cookie"}`。

### 限流红线（封号风险，务必遵守）

- **频率**：脚本已内置接口间 3~6 秒、文章间 2~4 秒间隔，不要调小、绝不全量并发；
- **200013 触发频率限制 = 当天停手**，不要换姿势重试；隔天再试；
- **新账号权重低**：列表接口可能一开始就 200013。解法是"暖号"：登录后台 →
  新建图文 → 超链接 → 查找文章，手动搜目标账号名并点开一篇，之后再跑脚本；
- **额度纪律**：每天每账号最多 1~2 次列表调用，单次 `--limit ≤ 5`；
  账号是免费注册的，被封可再注册，但频繁封号会连累登录微信。

### 长链被风控的兜底

平台接口给的文章链接是 `s?__biz=...` 长链，本机直连偶发跳 `wappoc` 验证页。
用浏览器打开该链接（真浏览器执行 JS 后一般直接通过），滚动到底触发懒加载，
存 DOM 为 html 后 `--html-file` 转换，作者等元数据反而更全。

## 风控兜底流程（[RISK] 时）

微信对无 Cookie 的直连偶尔返回"环境异常"验证页。依次尝试：

1. **重试一次**脚本调用（有时换个时间点就好了）。
2. **浏览器方案**（用 browser-use 的 control-browser 打开该链接，等正文渲染完），把 DOM 存成本地文件后转本地模式：
   - 在页面执行 JS 取整个文档：`document.documentElement.outerHTML`，把返回字符串写进临时 `.html`（注意写 UTF-8 编码）；
   - 或直接请用户手动 Ctrl+S 保存"网页，仅 HTML"，拿到路径；
   - 然后：`python ...fetch_wechat_article.py --html-file "临时.html" -o 目标目录`
3. 还不行 → 请用户在手机微信里打开确认文章是否仍存在，或让其在 PC 微信里打开后复制真实链接再试。

## 注意事项

- 抓取仅供个人学习记录，尊重原公众号版权，别二次分发全文。
- 支持 `https://mp.weixin.qq.com/s/...` 和 `/s?__biz=...&mid=...&idx=...` 两类形式；微信聊天的中间跳转短链可能失败。
- 图片域名 mmbiz.qpic.cn 一般可无 Referer 直连；若所有图片都失败大概率也是风控，走兜底流程。

## 扩展方向（暂未实现）

要拉取**某个公众号的全部历史文章列表**（而非最近的 N 篇），在日报模式 `--mp` 的基础上
把 `fetch_latest.py` 里 `mp_list_articles` 的 `--limit` 放大并循环翻页即可
（接口单次 5 篇，`begin` 递增；文章多时注意限频与 token 有效期）。
