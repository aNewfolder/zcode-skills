# cc98-crawler

在浙大校园网（或 WebVPN）环境下，按关键词搜索浙江大学 CC98 论坛帖子并抓取帖子正文（转 Markdown）与附件文件到本地；支持按兴趣推荐帖子（推送）、调整推送偏好、本地已抓取内容的全文检索。

## 功能与适用场景

- **搜索**：按关键词搜索帖子标题（CC98 搜索只匹配标题字面、无分词，脚本默认 spaced 模式逐字拆分以保证命中率，多关键词合并搜索去重）。
- **抓取**：把选中帖子的全部楼层正文（`content.md`）、原始 API 数据（`topic.json`）、`[upload]` 附件与文档/压缩包直链（`files/`）下载到本地，一次任务上限 20 帖。
- **兴趣推荐（推送）**：按兴趣关键词 + 关注版面 + 全站热帖打分推荐帖子，可随时增删偏好。
- **本地检索**：在已抓取正文里做多词 AND 全文检索，零联网请求。
- 适用场景：在校内论坛找资料、真题、课件、经验帖，下载帖子里的附件，订阅感兴趣版面的内容。**仅限浙大校园网（或 WebVPN）环境可用**；校外实时看帖请配合 cc98-viewer。

## 依赖

- **Python 3**：`scripts/cc98.py` 零第三方依赖（仅标准库 urllib/argparse/json 等），无需 pip 安装。
- **本地 CC98 抓取应用**：本目录只包含 AI 技能封装（SKILL.md + 包装脚本），`cc98.py` 是对本机 CC98 抓取 Web 应用（监听 `http://127.0.0.1:8765`）HTTP 接口的封装。**应用本体不在本仓库**，需自行部署到本机任意目录并用 `python app.py` 启动；可用环境变量 `CC98_APP_DIR` 指定应用目录，使脚本的启动提示更准确。

## 安装部署

将本目录（`cc98-crawler/`，目录名保持不变）复制到 AI 助手的技能目录：

```
~/.agents/skills/cc98-crawler/
```

本 skill 遵循 SKILL.md 约定，任何支持该约定的 AI 助手框架均可使用。

## 需要配置的信息

- **CC98 论坛账号**：首次使用时，在浏览器打开 `http://127.0.0.1:8765` 点「登录 / 换账号」自行登录（登录状态由应用管理并自动续期，AI 不接触账号密码）。
- 校外（无校园网）使用时还需**浙大上网账号**走 WebVPN，详见 cc98-viewer。

## 基本用法

```bash
# 确认应用在运行、已登录
python ~/.agents/skills/cc98-crawler/scripts/cc98.py login-status

# 搜索（关键词宁短勿长，多关键词各搜一次并合并）
python ~/.agents/skills/cc98-crawler/scripts/cc98.py search "大物实验" --pages 2
python ~/.agents/skills/cc98-crawler/scripts/cc98.py search 保研 推免 导师

# 抓取帖子（--wait 实时滚动进度，结束后打印输出目录）
python ~/.agents/skills/cc98-crawler/scripts/cc98.py crawl 6632367 --label "资料" --wait

# 兴趣推荐与偏好
python ~/.agents/skills/cc98-crawler/scripts/cc98.py feed
python ~/.agents/skills/cc98-crawler/scripts/cc98.py prefs add keyword 奖学金 --weight 4

# 本地已抓内容全文检索（零联网）
python ~/.agents/skills/cc98-crawler/scripts/cc98.py history 误差 --limit 10
```

## 注意事项

- **仅限浙大校园网环境**（校内直连或 WebVPN），其他网络无法访问 CC98。
- CC98 反爬严格：请求间隔默认 3 秒带抖动且不可调低，绝不并发跑多个抓取任务，不要反复刷新推荐；出现"疑似被限流"立即停手等待。
- 只用只读接口：绝不回帖、发帖、点赞、签到或调用任何写接口。
- 登录凭据只保存在本机应用内；爬取内容仅限个人学习用途，遵守平台条款。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
