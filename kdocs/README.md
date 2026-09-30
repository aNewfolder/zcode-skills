# kdocs

操作金山文档 / WPS 云文档（kdocs.cn / 365.kdocs.cn）的完整方案：官方 kdocs-cli 快速通道 + 浏览器自动化兜底的 AI 助手 skill。

## 功能与适用场景

覆盖两类场景：一是用官方 `kdocs-cli`（首选，快且稳）在自己账号下新建文档/表格并写入内容、读改表格单元格、读写 docx/otl、上传本地文件导入、重命名、分享设置、拿链接；二是用浏览器自动化匿名读取别人发的任意分享链接全文（含企业空间文档）、逐页核对表格、导出排版复刻的 Markdown 副本。当你的 AI 助手收到 kdocs.cn 链接，或被要求"读这篇云文档/转 Markdown/建个在线表格/填数据/分享拿链接"时使用。适合任何拥有 WPS/金山文档账号的用户；企业空间文件的写入只能走浏览器登录企业账号。

## 依赖

- 外部 CLI 工具：`kdocs-cli`（金山办公官方 CLI，自行从官方渠道获取安装，安装后加入 PATH，终端直接敲 `kdocs-cli` 可用）
- Node.js（SKILL.md 中的参数文件用 `node -e` 生成，规避 Windows 下命令行中文乱码；非 Windows 可用任意方式生成 UTF-8 JSON）
- 浏览器自动化能力：兜底通道需要 AI 助手具备浏览器操作技能（ZCode 为 `browser-use:control-browser`；其他框架用等价的浏览器自动化工具）
- 无 Python 依赖（本 skill 不含脚本，全部通过 CLI/浏览器完成）

## 安装部署

1. 把整个 `kdocs` 目录复制到你的 AI 助手技能目录（目录名不要改，SKILL.md 约定按目录名识别 skill）：
   - ZCode：`~/.zcode/skills/`（即 `~/.zcode/skills/kdocs/`）
   - 其他采用 SKILL.md 约定的框架（Claude Code 等）：放到其对应技能目录即可
2. 安装 `kdocs-cli` 并确认 `kdocs-cli --help` 可用。
3. 首次使用执行 `kdocs-cli auth login`，在弹出的浏览器授权页登录**个人** WPS 账号（企业账号会被拒绝：`enterprise account authorization denied`）。Token 存系统密钥链，有效期约 1 年。

## 需要配置的信息

| 配置项 | 说明 | 放哪里 |
|---|---|---|
| kdocs-cli Token（个人账号授权） | CLI 所有调用的凭据，存系统密钥链，有效期约 1 年 | `kdocs-cli auth login` 浏览器授权；过期（错误 400006）重新执行 |
| drive_id（可选） | 个人云盘 ID，重取角色列表等个别调用需要 | `kdocs-cli auth status` 查询，按需填入命令参数 |
| 企业账号登录态（可选） | 仅访问企业/组织空间共享文档时，在浏览器里手动登录企业账号 | 浏览器登录态（CLI 不可用，受企业管控） |

## 基本用法

```bash
# 新建表格并写入数据（含中文的参数走 UTF-8 JSON 文件）
node -e "require('fs').writeFileSync('p.json', JSON.stringify({name:'台账.xlsx',file_extension:'xlsx',sheet_name:'Sheet1',parent_id:'0',rangeData:[{row_from:0,row_to:0,col_from:0,col_to:1,formula:[['姓名','分数']]}]}),'utf8')"
kdocs-cli drive create-file-with-content --file p.json --compact

# 表格续写（camelCase + opType）
kdocs-cli sheet update-range-data --args '{"file_id":"<id>","worksheet_id":1,"rangeData":[{"opType":"formula","rowFrom":3,"rowTo":3,"colFrom":0,"colTo":0,"formula":"王五"}]}' --compact

# 分享为所有人可编辑并拿链接
kdocs-cli drive share-file --args '{"file_id":"<id>","scope":"anyone"}' --compact
kdocs-cli drive set-share-permission --args '{"file_id":"<id>","scope":"anyone","role_id":"21229356"}' --compact

# 上传本地文件导入云端
node -e "const fs=require('fs');fs.writeFileSync('p.json',JSON.stringify({name:'报告.docx',content_base64:fs.readFileSync('报告.docx').toString('base64'),parent_id:'0'}),'utf8')"
kdocs-cli drive upload-new-file --file p.json --compact

# 读取文档内容 / 搜文件 / 列目录
kdocs-cli drive read-file --args '{"url":"https://www.kdocs.cn/l/xxx"}'
kdocs-cli drive search-files keyword=周报
```

读取他人分享链接（匿名可读）与导出 Markdown 副本的浏览器流程见 `SKILL.md`。

## 注意事项

- Token 禁止明文出现在对话、日志、文件中；凭据只存本机系统密钥链。
- 不可逆操作（删除、取消分享、覆盖上传）先确认；写操作完成后必须独立读回验证，不信任 `code: 0`。
- 平台有限频/熔断（错误 429001/429002），触发后停止调用到恢复时间；WPS 编辑器页面对浏览器自动化不友好（截图管线易失败），验证一律用 DOM 断言。
- 分享默认设为"任何人可编辑"，公开前请确认文档不含敏感内容。
- 仅用于操作自己有权限的文档，遵守金山/WPS 服务条款。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
