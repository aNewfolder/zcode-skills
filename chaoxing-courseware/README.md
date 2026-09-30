# chaoxing-courseware

通过超星学习通（chaoxing.com / xuexi.cn）Web API 爬取课程章节列表并批量下载章节内的文件课件（pptx/docx/pdf 等）的 AI 助手 skill。

## 功能与适用场景

给课程页 URL，自动解析章节树、列出每章课件，并按章节关键词批量下载文件型附件。适用于"把这学期的课件全部下下来"、"下载绪论那章的 PPT"等场景；登录走账号密码 API（AES-CBC 加密），Cookie 持久化后数周内无需重复登录。适用于任何接入超星学习通/泛雅平台的高校课程（平台本身无校园网限制，但课程须在你的账号权限内）；不用于自动刷课、答题或签到。

## 依赖

- Python 3.10+
- pip 包（见 `scripts/chaoxing_dl.py` 的 import）：
  - `requests`（必需）
  - `pycryptodome`（仅 `login` 子命令需要，用于 AES 加密账号密码）

```bash
pip install requests pycryptodome
```

无外部 CLI 工具依赖。

## 安装部署

1. 把整个 `chaoxing-courseware` 目录复制到你的 AI 助手技能目录（目录名不要改，SKILL.md 约定按目录名识别 skill）：
   - ZCode：`~/.zcode/skills/`（即 `~/.zcode/skills/chaoxing-courseware/`）
   - 其他采用 SKILL.md 约定的框架（Claude Code 等）：放到其对应技能目录即可
2. 安装上述 pip 依赖。
3. 首次使用先执行 `login` 子命令生成 Cookie（见下）。

## 需要配置的信息

| 配置项 | 说明 | 放哪里 |
|---|---|---|
| 超星账号（手机号/超星号） | 你的学习通登录账号 | `python chaoxing_dl.py login <账号> "<密码>"` 交互式传入；登录成功后 Cookie 自动存到 `scripts/cookies.json` |
| 超星密码 | 同上，只在登录时使用一次 | 同上（命令行参数） |
| Cookie（可选） | 也可从已登录浏览器 F12 → Application → Cookies 复制 `UID`/`_d`/`fid` 等字段 | `--cookie 'UID=...; _d=...; fid=...'`，或直接写入 `scripts/cookies.json` |

注意：手工复制浏览器 Cookie 往往缺 HttpOnly 的 `vc3` 字段，优先用 `login` 子命令。`cookies.json` 含登录凭据，已加入忽略清单的话不要提交到仓库。

## 基本用法

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

# 包含视频等全部附件（体积大，慎用）
python chaoxing_dl.py download "<课程页URL>" --type all --out "<输出目录>"
```

`<课程页URL>` 为课程首页（`/mooc2-ans/mycourse/stu?courseid=...&clazzid=...`）或章节学习页（`/mycourse/studentstudy?chapterId=...`）均可，脚本自动取参。`--chapter` 支持标题关键词、编号（如 2.1）或 chapterId。

## 注意事项

- Cookie/账号密码只保存在本机 `scripts/cookies.json`，不会上传；请勿把该文件分享或提交到公开仓库。
- 下载频率过高可能触发平台风控，脚本已内置请求间隔，请勿改装成并发爬虫。
- 仅限下载自己有权限访问的课程资料，供个人学习使用；请遵守学校与平台的使用条款，勿传播受版权保护的课件。
- Cookie 有效期数周量级；`list` 报"未找到章节/可能过期"时重新 `login` 即可。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
