---
name: wjx-survey
description: 通过问卷星官方 OpenAPI（wjx-cli，npm 全局已装）操作问卷星账号：创建问卷/投票/表单/考试（JSONL，70+ 题型）、列出和读取问卷、修改问卷设置、发布/暂停、查询答卷明细、统计报表、导出答卷数据（CSV/SAV/Word）。当用户提到问卷星、wjx、问卷、调查问卷、投票、报名表、收集表、表单、导出问卷/答卷结果、问卷统计、班级调查，或给出 wjx.cn / sojump.com 链接要求读取内容、统计结果、导出数据时使用。首次使用需配置 API Key（wjx init --api-key 或写入 ~/.wjxrc），配置后持久化在本机。
---

# 问卷星 API 操作（wjx-cli）

**核心资产（勿改动位置）：**

- CLI：`wjx`（npm 全局包 `wjx-cli`，升级用 `npm install -g wjx-cli@latest`）
- API Key：`~/.wjxrc`（**首次使用需配置**：`wjx init --api-key <key>` 或将 Key 写入 `~/.wjxrc`；配置后长期有效，失效处理见下方规则 7）
- 公开问卷内容抓取脚本：`scripts\wjx_read_public.py`（读他人问卷，无需 Key）
- 命令参考文档：本目录 `references\`（survey-commands / response-commands / question-types / analytics-commands / contacts-commands / formula-helper）
- CLI 自带 skill（官方，含安装模板）：npm 包内 `bundled\wjx-cli-use\SKILL.md`

## 快速使用

```bash
# 账号与诊断
wjx whoami                    # 验证 Key，显示问卷总数
wjx doctor                    # 连接诊断

# 问卷
wjx survey list               # 列问卷（JSON 含 total_count/page_index/page_size/activitys）
wjx survey list --format table            # 仅人工查看
wjx survey get --vid <vid>                # 问卷详情（题目、状态、答卷数）
wjx survey create --file s.jsonl --type 1 # 创建问卷（见下方 JSONL）
wjx survey status --vid <vid> --state 2 --yes   # 1=发布 2=暂停 3=删除进回收站
wjx survey settings --vid <vid>           # 读设置
wjx survey update-settings --vid <vid> --after_submit_setting '<json>' --yes
wjx survey preview-url --vid <vid>        # 预览链接
wjx survey url --mode edit --activity <vid>   # 网页编辑器链接

# 答卷
wjx response query --vid <vid> --page_size 50 --page 1   # 明细（分页）
wjx response report --vid <vid>           # 统计报表（每题频次等）
wjx response count --vid <vid>            # 答卷数
wjx response download --vid <vid>         # 导出（返回签名下载URL）
wjx response download --vid <vid> --suffix 1   # 1=SAV 2=Word 0=CSV(默认)

# 导出 URL 下载到本地（CSV 用 utf-8-sig 读）
python -c "import json,urllib.request; u=json.load(open('r.json',encoding='utf-8'))['data']['download_url']; urllib.request.urlretrieve(u,'out.csv')"

# 分析（本地计算，不需要 Key）
wjx analytics nps --scores "[9,10,7,3]"
```

## 读取他人创建的公开问卷（无需登录）

问卷填写页是公开的，可用 `scripts\wjx_read_public.py` 抓取**题目内容**（题干/选项/题型/必填/状态），不需要 API Key：

```bash
python ~/.zcode/skills/wjx-survey/scripts/wjx_read_public.py "https://tp.wjx.com/vm/Q0Q3h38.aspx" --md
# 也支持简写：python wjx_read_public.py wjx:Q0Q3h38 --out out.json
```

- 输出 JSON（默认）或 `--md` Markdown；`state` 字段自动识别 已暂停/已停止/需登录/密码保护/已满额。
- 实现：解析填写页 HTML 里 `div.field`（`topic`=题号、`type`=题型、`req`=必填、选项在 `div.label`），纯 urllib 无第三方依赖。
- **边界（实测确认）**：他人的**答卷数据/统计结果拿不到**——填写页不含任何数据（`activityCommonInfo` 为空、无统计接口），问卷星 API 也只对创建者账号鉴权。只有创建者开启"公开统计/显示投票结果"的问卷，其填写页会自带统计展示，那种直接抓页面即可。不要尝试绕过。

问卷类型 `--type`：1 调查（默认）2 测评 3 投票 4/5 360评估 6 考试 7 表单 9 教学评估 10 量表 11 民主评议。

## 创建问卷 JSONL

先取骨架再改：`wjx survey jsonl-template --type 1 --raw > s.jsonl`。每行一个 JSON 对象，首行必须是问卷基础信息：

```jsonl
{"qtype":"问卷基础信息","title":"标题","introduction":"说明"}
{"qtype":"单选","title":"你倾向的日期？","select":["周六","周日"]}
{"qtype":"多选","title":"想去的地点类型？（可多选）","select":["爬山","游乐园","博物馆"]}
{"qtype":"量表题","title":"期待程度（1=毫无, 5=非常）","select":["1","2","3","4","5"]}
{"qtype":"简答题","title":"还有什么想说的？"}
```

- 题型 `qtype` 用中文，只允许 `references\question-types.md` 里列出的（投票题用 `投票单选`/`投票多选` + `--type 3`；考试题用 `考试单选` 等 + `--type 6`，正确答案用 `correctselect`、分值 `quizscore`）。
- 矩阵题用 `rowtitle + select`；地区题用 `多级下拉` + `leveldata`。
- **创建即自动发布**（status=1），创建响应里有 `vid`/`sid`/`activity_domain`/`pc_path`。
- 高风险写操作（status/update-settings/delete 等）必须加 `--yes`，否则报 CONFIRMATION_REQUIRED。

## 关键规则（实测踩坑）

1. **填写链接**：用创建响应或 survey list 里的 `fill_url`，或 `activity_domain + pc_path`（路径是短 `sid` 如 `/vm/mRhBSic.aspx`）。**禁止**用数字 `vid` 手拼 `/vm/<vid>.aspx`。
2. **机器处理一律用默认 JSON 输出**，读 `data.total_count` / `page_index` / `page_size`，逐页取全；`--format table` 只给人工看。
3. **编辑已有问卷的题目：个人免费账号不可用**（`dsl query/update`、`response submit`、`survey delete` 均报"权限不足，请联系客户顾问获取权限"）。改题目的替代方案：① 打开 `wjx survey url --mode edit --activity <vid>` 用浏览器自动化操作网页编辑器；② 问卷还没人填时直接用改好的 JSONL 重建一份。
4. `update-settings` 可能返回"读回验证未能证明写入结果，结果未知"——**实际通常已生效**，用 `wjx survey settings --vid <vid>` 读回确认即可。
5. `status --state 2` 暂停后立即读回可能仍是旧状态（服务端延迟），重试或稍后 `survey get` 确认。
6. 导出超过 3000 条转异步任务，用返回的 `taskid` 轮询 `response download --vid <vid> --taskid <id>`。
7. Key 失效（报 `Invalid API Key`/`appkey error`）时：让用户打开 `https://www.wjx.cn/weixinlogin.aspx?redirecturl=%2Fnewwjx%2Fmanage%2Fuserinfo.aspx%3FshowApiKey%3D1` 微信扫码复制新 Key，然后 `wjx init --api-key <key>`（一次性操作，Key 持久化在 `~/.wjxrc`）。
8. 通讯录/部门/子账号（contacts/department/admin/account）需要企业版 `WJX_CORP_ID`，个人账号不可用。

## 能力矩阵（个人免费账号实测）

| 功能 | 状态 | 命令 |
|------|------|------|
| 列问卷/读详情/搜索 | ✅ | `survey list/get` |
| 创建问卷（70+题型） | ✅ | `survey create`（创建即发布） |
| 发布/暂停控制 | ✅ | `survey status --state 1/2 --yes` |
| 读写问卷设置 | ✅ | `survey settings / update-settings --yes` |
| 答卷明细/统计/计数 | ✅ | `response query/report/count` |
| 导出 CSV/SAV/Word | ✅ | `response download`（194 份实测成功） |
| 预览/编辑链接 | ✅ | `survey preview-url / url --mode edit` |
| 读取他人公开问卷的内容 | ✅ | `scripts\wjx_read_public.py`（无需 Key；答卷数据不可得） |
| NPS/CSAT 等本地分析 | ✅ | `analytics` |
| 修改已有问卷题目（DSL） | ❌ 权限不足 | 走网页编辑器或重建 |
| 程序化提交答卷 | ❌ 权限不足 | — |
| 删除问卷 | ❌ 权限不足 | 暂停（state 2）替代 |
| 通讯录/部门/子账号 | ❌ 需企业 CORP_ID | — |

## 实测记录（个人免费账号）

- 功能链路验证：`wjx init` 配置 Key 后 whoami 正常。创建测试卷（5 题含单选/多选/量表/简答，创建即发布），settings 修改提交后文案读回确认生效，暂停/发布切换成功，`response report`/`query` 正常；导出含 194 份答卷的问卷 CSV 成功（含序号/提交时间/所用时间/来源/IP/各题答案）。测试卷已暂停留档（删除为企业版接口）。
- 公开问卷内容抓取实测——某公开班委投票问卷（5 题全部题目/候选人提取成功，含纯选项候选名单）；已暂停的测试卷正确识别 `state=paused`。确认填写页无任何答卷数据暴露，他人答卷明细不可公开获取。
