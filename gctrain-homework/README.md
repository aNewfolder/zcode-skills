# gctrain-homework

自动完成浙江大学工程训练平台（http://10.71.32.157/）每周课前预习作业的取题、答题与提交的 AI 助手 skill。

## 功能与适用场景

对浙大工程训练中心 OA 系统（内网 `http://10.71.32.157/`，**仅校园网/VPN 可达**）做接口自动化的 API 客户端：列出本周有效预习作业、开考导出题目（判断/单选/多选）、编码答案交卷、查答题记录、并把历次题目与正确答案沉淀进本地题库 `题库.json`（题目历年重复率高）。适合配合 AI 助手做"查作业 → 取题 → 检索/推理答案 → 提交 → 沉淀题库"的全流程，也可只用它查询状态（"这周有没有作业"）。平台为浙大专属，其他学校不可用。

## 依赖

- Python 3.10+
- pip 包（见 `scripts/gctrain.py` 的 import）：
  - `requests`
  - `beautifulsoup4`

```bash
pip install requests beautifulsoup4
```

无外部 CLI 工具依赖。

## 安装部署

1. 把整个 `gctrain-homework` 目录复制到你的 AI 助手技能目录（目录名不要改，SKILL.md 约定按目录名识别 skill）：
   - ZCode：`~/.zcode/skills/`（即 `~/.zcode/skills/gctrain-homework/`）
   - 其他采用 SKILL.md 约定的框架（Claude Code 等）：放到其对应技能目录即可
2. 安装上述 pip 依赖。
3. 配置账号密码（见下），并在校园网/VPN 环境下使用。

## 需要配置的信息

| 配置项 | 说明 | 放哪里 |
|---|---|---|
| 平台账号 | 工训平台登录账号（通常为学号） | 环境变量 `GCTRAIN_USER`，或命令行 `-u/--user`（参数优先） |
| 平台密码 | 工训平台登录密码 | 环境变量 `GCTRAIN_PASSWORD`，或命令行 `-p/--password` |

两处都未提供时脚本会报错退出："请通过环境变量 GCTRAIN_USER/GCTRAIN_PASSWORD 或 -u/-p 提供账号密码"。例如（Git Bash）：

```bash
export GCTRAIN_USER=<你的学号>
export GCTRAIN_PASSWORD=<你的密码>
```

`题库.json` 无需手动创建：`bank` 子命令会在运行目录自动生成并按题目 ID 去重累积。

## 基本用法

```bash
cd ~/.zcode/skills/gctrain-homework/scripts

# 验证登录
python gctrain.py login

# 遍历全部工种，找本周有效期内且未做过的预习作业
python gctrain.py list --all-kinds

# 开考并导出题目到 exam.json（⚠️ 服务端从此刻开始倒计时）
python gctrain.py start <test_id> <paper_type> <recorder_id> --paper-id <paper_id> --out exam.json

# 交卷（先预演，确认后加 --yes 正式提交）
python gctrain.py submit <test_id> <recorder_id> <paper_type> answers.json
python gctrain.py submit <test_id> <recorder_id> <paper_type> answers.json --yes

# 查看答题记录 / 把本次题目+正确答案并入题库
python gctrain.py records
python gctrain.py bank <test_id> <recorder_id>
```

`test_id`、`paper_type`、`recorder_id`、`paper_id` 均来自 `list` 输出行内的"入口参数"（`--paper-id` 必须传，漏传会开考成功但题目为 0）。答案 JSON 格式：`[{"subject_id": 531, "answer": "true", "subject_type": 2}, ...]`，判断题必须发 `"true"`/`"false"` 布尔字符串，单选 `"A"~"D"`，多选拼字母如 `"AC"`；subject_type：2=判断、3=单选、4=多选。

## 注意事项

- 账号密码只在本机环境变量/命令行中使用，不落盘、不上传；请勿写入任何会提交到仓库的文件。
- 平台仅校园网/VPN 可达，校外使用需先连学校 VPN。
- 开考（`start`）后服务端开始倒计时，取题、作答、交卷请连贯完成。
- 本工具仅为个人完成课程预习作业之用，答案请自行核验；请遵守学校相关规定，使用产生的一切后果自负。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
