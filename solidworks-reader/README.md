# solidworks-reader

不用人工打开 SolidWorks 软件，通过 COM API 只读提取 .sldprt 零件 / .sldasm 装配体 / .slddrw 工程图的数据（自定义属性、特征树、质量属性、BOM 表、配合、图纸视图等），输出 UTF-8 JSON。

## 功能与适用场景

- 让 AI 助手（或你自己）统计/分析 SolidWorks 文件而不启动软件界面：算 BOM、查质量属性、数配合、看工程图尺寸和表格、提取自定义属性做台账。
- 严格只读：固定以 `ReadOnly|Silent` 选项打开文件，从不调用 Save；若文件已被你在 SolidWorks 中打开，脚本附着到现有实例读取且不关闭它；自己拉起的隐藏进程退出时自动清理。
- 三类文档的输出要点：
  - **part**（.sldprt）：特征树、实体、包围盒、材料、质量属性（SI 单位）；
  - **assembly**（.sldasm）：组件树（路径/配置/变换矩阵）、配合（类型含中文对照）、整机质量属性；
  - **drawing**（.slddrw）：每张图纸的视图/表格（含 BOM 逐行逐格）/注释/尺寸。
- 拿不到 SW2015+ 文件的预览图（容器加密），需要看模型外观时请在 SolidWorks/eDrawings 中打开。

## 依赖

| 依赖 | 用途 |
| --- | --- |
| Windows + 已安装 SolidWorks | COM ProgID `SldWorks.Application`（作者环境为 SolidWorks 2018 SP5） |
| Python 3 + `pywin32` | `pip install pywin32`；脚本用 `win32com.client` / `pythoncom` 连接 COM |
| `olefile`（可选） | 仅读取 SW2014 及更早老文件的预览图时需要 |

## 安装部署

1. 把整个 `solidworks-reader/` 目录复制到你的 AI 助手技能目录（ZCode 为 `~/.zcode/skills/`，目录名保持不变；任何遵循 SKILL.md 约定的框架均可使用）。
2. `pip install pywin32`（可选：`pip install olefile`）。
3. 验证 COM 注册：`reg query HKCR\SldWorks.Application\CLSID`。

无需任何配置文件——凭据不存在，脚本只读本机已能打开的 SolidWorks 文件。

## 需要配置的信息

| 项 | 说明 |
| --- | --- |
| 无 | 本 skill 不含账号/密钥/配置文件；SolidWorks 许可证即全部前置条件 |

## 基本用法

```bash
# 读单个文件（UTF-8 JSON 输出到 stdout）
python ~/.zcode/skills/solidworks-reader/scripts/sw_read.py "D:\模型\某零件.sldprt"

# 批量读 + 结果存档（JSON 很大时避免撑爆上下文）
python ~/.zcode/skills/solidworks-reader/scripts/sw_read.py a.sldprt b.sldasm c.slddrw --json 结果.json

# 大装配体加速（轻化加载 / 工程图 RapidDraft）
python ~/.zcode/skills/solidworks-reader/scripts/sw_read.py 大装配.sldasm --fast

# 限制输出规模（特征/组件上限，默认各 5000）
python ~/.zcode/skills/solidworks-reader/scripts/sw_read.py 大装配.sldasm --max-components 200 --max-features 500
```

工作流程建议：先跑脚本拿 JSON（大文件用 `--json` 存档再分段读），BOM/质量/配合数量等汇总问题直接在 JSON 上分析回答。

## 注意事项

- 首次调用会冷启动 SolidWorks，耗时 20–60 秒属正常；同会话内后续调用很快。
- 质量属性按 SolidWorks 返回值输出（默认 SI：kg、m、m²、m³），与文档单位设置无关。
- `open_warnings: 128`（需重建等）可忽略；个别字段拿不到会是 `null`，不影响其余数据。
- 工程图 `referenced_model` 指向别机器的用户目录路径时，说明图纸引用未随文件走，属文件自身问题。
- 本 skill 无凭据，不涉及"密码不进 git"问题；但输出 JSON 可能含你本机文件路径，分享前自行检查。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
