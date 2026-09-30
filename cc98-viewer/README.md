# cc98-viewer

在没有校园网的环境（家里/宿舍外/手机热点）经浙大 WebVPN 实时查看 CC98 论坛帖子和新帖动态：后台低频轮询保持新帖实时更新，页内阅读器直接看帖子正文，也可照常搜索和抓取。

## 功能与适用场景

- **实时动态**：聚合每日/每周热帖与关注版面最新帖，本地快照秒回零请求，`--refresh` 真实跑一轮（每轮 ≤5 个请求、间隔下限 5 分钟）。
- **页内阅读**：拉取帖子正文逐楼渲染（Markdown），不抓文件、只想看帖时用；服务端有 10 分钟缓存，重复读不重复耗请求。
- **搜索/抓取/推荐**：命令与 cc98-crawler 完全一致，走当前网络通道（校内直连 / WebVPN 反代 / 本地代理自动切换），校外稍慢属正常。
- 适用场景：校外追帖、实时看 98 新帖、页内看正文；在校内且要大量抓取/下载文件时用 cc98-crawler 更直接。
- 解决的本质问题：CC98 域名校外解析到内网地址直连物理不可达，必须走 WebVPN（webvpn.zju.edu.cn 反代）或本地代理（zju-connect/RVPN）。

## 依赖

- **Python 3**：共用脚本零第三方依赖（仅标准库），无需 pip 安装。
- **cc98-crawler skill（必须一起安装）**：本 skill 的辅助脚本就是 cc98-crawler 目录下的 `scripts/cc98.py`，两者共用同一个本地 CC98 应用与脚本。
- **本地 CC98 抓取应用**：同 cc98-crawler，应用本体不在本仓库，需自行部署并用 `python app.py` 启动（监听 `http://127.0.0.1:8765`）。

## 安装部署

将本目录（`cc98-viewer/`，目录名保持不变）**与 cc98-crawler 一起**复制到 AI 助手的技能目录：

```
~/.agents/skills/cc98-viewer/
~/.agents/skills/cc98-crawler/   ← 必须同时安装（提供共用脚本 cc98.py）
```

本 skill 遵循 SKILL.md 约定，任何支持该约定的 AI 助手框架均可使用。

## 需要配置的信息

- **浙大上网账号**（学号 + 密码，学校账号而非 CC98 账号）：在应用网页 `http://127.0.0.1:8765` 的「设置 → 网络接入」里登录 WebVPN；遇验证码/二次验证时改为在浏览器登录 webvpn.zju.edu.cn 后把 `wengine_vpn_ticket…` cookie 粘贴进兜底框。也可改用 zju-connect/RVPN 本地代理（填代理地址如 `socks5://127.0.0.1:2345`）。
- **CC98 论坛账号**：CC98 匿名读不了帖子（API 返回 401），需在应用页面登录 CC98 账号（token 只读使用）。

## 基本用法

```bash
# 查看网络通道状态（校网直连 / WebVPN / 不可用）
python ~/.agents/skills/cc98-crawler/scripts/cc98.py net-status

# 实时动态（本地快照秒回；--refresh 真实抓一轮）
python ~/.agents/skills/cc98-crawler/scripts/cc98.py live --limit 25
python ~/.agents/skills/cc98-crawler/scripts/cc98.py live --refresh

# 页内阅读帖子正文
python ~/.agents/skills/cc98-crawler/scripts/cc98.py read 6631929 --pages 2

# 搜索 / 抓取（与 cc98-crawler 完全一致，走当前通道）
python ~/.agents/skills/cc98-crawler/scripts/cc98.py search 关键词
python ~/.agents/skills/cc98-crawler/scripts/cc98.py crawl <帖子ID> --label "资料" --wait
```

## 注意事项

- **仅限浙大校园网/WebVPN 环境**；WebVPN 用的是学校账号，登录、掉线重登都要节制，程序化登录失败 2 次就停、改走浏览器。
- 限速内置不可调低（请求间隔 3 秒带抖动、轮询间隔下限 5 分钟）；出现"疑似被限流"立即停手，等几小时再试。
- 只读红线：绝不回帖、发帖、点赞、签到。
- 登录凭据只保存在本机应用内；爬取内容仅限个人学习用途，遵守平台条款。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
