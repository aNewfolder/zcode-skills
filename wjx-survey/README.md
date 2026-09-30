# wjx-survey

通过问卷星官方 OpenAPI 的命令行工具 `wjx-cli` 操作问卷星账号，覆盖问卷全流程：创建问卷/投票/表单/考试、列出与读取问卷、修改设置、发布/暂停、查询答卷明细、统计报表、导出答卷数据（CSV/SAV/Word）；附赠一个无需登录的公开问卷内容抓取脚本。

## 功能与适用场景

- **建卷**：用 JSONL 描述题目（70+ 题型：单选/多选/量表/矩阵/考试题等），一条命令创建并自动发布调查、投票、表单、考试等各类问卷。
- **管理与控制**：列问卷、读详情与设置、发布/暂停切换、生成预览与网页编辑器链接。
- **答卷导出与统计**：答卷明细分页查询、每题频次统计报表、答卷计数、导出下载链接（CSV 默认，可选 SAV/Word），超过 3000 条自动转异步任务轮询。
- **读他人公开问卷**：抓取公开填写页的题目内容（题干/选项/题型/必填/状态），无需任何 Key；他人的答卷数据不可也不尝试获取。
- **本地分析**：NPS/CSAT 等得分计算，本地完成，不消耗 API。
- 适用场景：班级调查、活动报名投票、问卷数据导出分析、批量读取问卷结果等。

## 依赖

- **Node.js + npm**：命令行工具 `wjx-cli` 为 npm 全局包（`npm install -g wjx-cli`）。
- **Python 3**：`scripts/wjx_read_public.py` 使用纯标准库（urllib），无需 pip 安装任何第三方包。
- **问卷星账号 + API Key**：见下文配置。

## 安装部署

将本目录（`wjx-survey/`，目录名保持不变）复制到 AI 助手的技能目录：

```
~/.zcode/skills/wjx-survey/
```

本 skill 遵循 SKILL.md 约定，任何支持该约定的 AI 助手框架均可使用。安装后安装 CLI：

```bash
npm install -g wjx-cli
```

## 需要配置的信息

- **问卷星 API Key**（首次使用必配，一次性操作）：
  1. 打开 `https://www.wjx.cn/weixinlogin.aspx?redirecturl=%2Fnewwjx%2Fmanage%2Fuserinfo.aspx%3FshowApiKey%3D1`，用问卷星账号微信扫码，复制 API Key；
  2. 执行 `wjx init --api-key <key>`（或将 Key 手动写入 `~/.wjxrc`）。
- Key 配置后长期有效；若报 `Invalid API Key`，按上述步骤重新获取并 init 一次。
- 读取他人公开问卷内容（`wjx_read_public.py`）**不需要**任何配置。

## 基本用法

```bash
# 验证配置
wjx whoami                                    # 显示账号与问卷总数
wjx doctor                                    # 连接诊断

# 列出与查看问卷
wjx survey list                               # JSON 输出（机器处理用默认 JSON）
wjx survey get --vid <vid>                    # 问卷详情

# 创建问卷（每行一个 JSON 对象，首行为问卷基础信息）
wjx survey jsonl-template --type 1 --raw > s.jsonl   # 取骨架再改
wjx survey create --file s.jsonl --type 1            # 创建即发布

# 答卷统计与导出
wjx response report --vid <vid>               # 每题频次统计
wjx response download --vid <vid>             # 导出（返回签名下载 URL）

# 读他人公开问卷的题目内容（无需 Key）
python ~/.zcode/skills/wjx-survey/scripts/wjx_read_public.py "https://tp.wjx.com/vm/xxxx.aspx" --md
```

## 注意事项

- API Key 只保存在本机 `~/.wjxrc`，不要提交到仓库或分享给他人。
- 仅操作自己账号下的问卷；他人的答卷数据属于创建者私有数据，本 skill 不提供也不尝试绕过权限获取。
- 个人免费账号下，修改已有问卷题目（DSL）、程序化提交答卷、删除问卷、通讯录等企业功能不可用（详情见 SKILL.md 能力矩阵）。
- 遵守问卷星平台条款，合理使用接口频率。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
