# zcode-skills

我为自己日常使用而写的一批 AI 助手 skills，现在开源出来。

每个子目录是一个独立的 skill：`SKILL.md` 是给 AI 助手看的操作手册（触发条件、工作流、命令、踩坑记录），`scripts/` 是配套脚本，`references/` 是参考资料，`README.md` 是给人看的部署指南（依赖、配置、用法）。

## 先说清楚

- **这些 skill 全部由 AI（ZCode + GLM）辅助编写**，我只是提需求、测试、迭代。
- **按"现状"提供，不保证及时更新，不保证质量**——平台接口一变就可能失效，我也未必有空修。
- 但凡放在这个仓库里的，**都是我自己日常在用、并且觉得好用有帮助的**，不是玩具 demo。
- 涉及账号凭据的 skill（邮箱、图床、问卷星、论坛等），凭据一律只存在你本机的配置文件里，本仓库不含任何凭据，也不要把你自己的凭据提交进 git。

## Skills 一览

| Skill | 干什么 | 备注 |
| --- | --- | --- |
| [chaoxing-courseware](chaoxing-courseware/) | 超星学习通课件下载：按章节爬取课程文件（pptx/docx/pdf） | 账号密码登录，Cookie 持久化 |
| [circuit-lab-report](circuit-lab-report/) | 浙大《电子电路基础及实验》实验报告撰写（XeLaTeX 版式复刻 Word 模板） | 深度绑定浙大课程模板 |
| [gctrain-homework](gctrain-homework/) | 浙大工程训练平台每周预习作业自动取题、答题、提交 | 仅校园网可达 |
| [kdocs](kdocs/) | 金山文档 / WPS 云文档操作：建表写入、读单元格、分享设置（kdocs-cli） | 部分场景需浏览器自动化 |
| [mail163-draft](mail163-draft/) | 163 邮箱公文排版存草稿（仿宋/Times New Roman、首行缩进、署名右对齐） | 只存草稿绝不发送 |
| [physics-lab-report](physics-lab-report/) | 浙大《大学物理实验》预习报告与实验报告（不确定度评定、有效数字规范） | 深度绑定浙大课程规范 |
| [picgo-image-upload](picgo-image-upload/) | 调本机 PicGo（腾讯云 COS）上传图片拿外链，插入 Markdown | 需本机已配置 PicGo |
| [solidworks-reader](solidworks-reader/) | 免开软件只读提取 SolidWorks 零件/装配体/工程图数据，输出 JSON | 需本机装有 SolidWorks |
| [wjx-survey](wjx-survey/) | 问卷星全流程：创建问卷（70+ 题型）、发布、答卷导出、统计报表 | 需 wjx-cli + API Key |
| [zjuem-mail-draft](zjuem-mail-draft/) | 浙大邮箱公文排版存草稿（统一认证 API 登录 + Coremail 接口） | 只存草稿绝不发送 |
| [cc98-crawler](cc98-crawler/) | 浙大 CC98 论坛帖子搜索、正文与附件抓取、本地全文检索 | 仅校园网 / WebVPN |
| [cc98-viewer](cc98-viewer/) | 校外经 WebVPN 实时看 CC98 新帖、页内阅读 | 与 cc98-crawler 共用脚本 |
| [piano-room-booking](piano-room-booking/) | 浙大琴房自动预约：放票时刻自动登录抢琴房，含手机网页控制台管理计划与订单 | 需自有服务器 + 校园网隧道 |
| [wechat-article-reader](wechat-article-reader/) | 微信公众号推文完整归档（Markdown + 本地化图片音视频），免登录读最新推送 | 被风控时有浏览器兜底 |

## 怎么用

### 1. 你需要一个支持 skills 的 AI 助手

skills 是一种通用的 agent 扩展约定：一个带 frontmatter（name / description）的 `SKILL.md` 目录，AI 助手读到后就知道什么时候该用它、怎么用。ZCode、Claude Code 等都兼容这个格式。

### 2. 把 skill 装进技能目录

```bash
git clone https://github.com/aNewfolder/zcode-skills.git
# 挑你需要的，整个目录原样复制过去（目录名不要改）：
cp -r zcode-skills/wjx-survey ~/.zcode/skills/        # ZCode
# 或 ~/.agents/skills/，按你的助手约定
```

具体装哪个目录、要装哪些依赖、要配什么账号，**看每个 skill 的 README.md**，里面写了人和 AI 都能照做的部署步骤。

### 3. 依赖与配置

- 大多数脚本只依赖 `requests` 等常见 Python 包，各 README 里有清单。
- 涉及账号的 skill 首次使用需要配置你的凭据（config 文件、环境变量或交互登录），README 的"需要配置的信息"一节有完整清单。

## 作者

- GitHub：[aNewfolder](https://github.com/aNewfolder)
- 个人网站：<https://alight404.top>

## License

[MIT](LICENSE)
