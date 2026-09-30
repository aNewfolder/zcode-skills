---
name: solidworks-reader
description: 只读读取 SolidWorks 文件数据（.sldprt 零件 / .sldasm 装配体 / .slddrw 工程图），不用人工打开软件。提取自定义属性、特征树、实体、材料、质量属性、装配体组件树/配合、工程图视图/尺寸/图纸规格等，输出 JSON。Use whenever the user mentions SolidWorks, sldprt, sldasm, slddrw, or wants to 读取/查看/分析/统计/提取 SolidWorks 零件、装配体、工程图、BOM、质量属性、配合信息 — even if they don't explicitly say "read".
---

# SolidWorks 文件只读读取

用打包的脚本通过 SolidWorks COM API 提取文件数据，绝不修改、绝不保存文件。

## 前置条件

- Windows + 已安装 SolidWorks（本机为 SolidWorks 2018 SP5，COM ProgID `SldWorks.Application`）。
- Python 3 + `pywin32`（本机已装）。`olefile` 可选，仅用于读 SW2014 及更早老文件的预览图。
- 脚本位置：本技能目录下 `scripts/sw_read.py`，直接用 `python` 运行。

## 用法

```bash
# 读单个文件（输出 UTF-8 JSON 到 stdout）
python "<技能目录>/scripts/sw_read.py" "D:\模型\某零件.sldprt"

# 批量读 + 结果存档
python "<技能目录>/scripts/sw_read.py" a.sldprt b.sldasm c.slddrw --json 结果.json

# 大装配体加速（轻化加载 / 工程图 RapidDraft）
python "<技能目录>/scripts/sw_read.py" 大装配.sldasm --fast

# 限制输出规模（特征/组件上限，默认各 5000）
python "<技能目录>/scripts/sw_read.py" 大装配.sldasm --max-components 200 --max-features 500
```

工作流程建议：

1. 先跑脚本拿到 JSON。JSON 很大时加 `--json 文件路径`，再用其他工具分段读取，避免撑爆上下文。
2. 汇总/统计类问题（BOM、质量、配合数量）直接在 JSON 上分析回答。
3. 需要向用户展示模型外观时，本脚本拿不到 SW2015+ 文件的预览图（容器已加密），可建议用户在 SolidWorks/eDrawings 里打开查看。

## 输出结构（按文档类型）

所有类型都含：`doc_type`、`configurations`、`active_configuration`、`custom_properties`（按配置分组，含 value/resolved）、`file_format`。

- **part**（.sldprt）：`features`（特征树：名称/类型/抑制状态）、`feature_count`、`solid_bodies`、`bounding_box`、`material`、`mass_properties`（mass/volume/surface_area/density/center_of_mass，SI 单位：kg、m）。
- **assembly**（.sldasm）：`components`（Name2、文件路径、配置、抑制/隐藏、4x4 变换矩阵原始值）、`mates`（配合名、类型及中文对照、抑制状态）、`mass_properties`（整机汇总）。
- **drawing**（.slddrw）：`sheets`（每张图纸的 `views`：视图名、引用模型路径、比例；`tables`：表格逐行逐格文本，含 BOM；`notes`：注释文本；`dimensions`：尺寸名，值可能缺失）。

顶层 `solidworks` 字段记录 COM 连接信息（版本号、是否附着到用户已开的实例）。

## 重要行为准则

- **只读**：脚本固定用 `swOpenDocOptions_ReadOnly|Silent` 打开，从不调用 Save。若文件已被用户在 SolidWorks 中打开，脚本会附着到现有实例读取，且**不会关闭**该文件。
- 脚本自己拉起的隐藏 SolidWorks 进程，退出时会自动 `ExitApp()` 清理。
- 首次调用会冷启动 SolidWorks，可能耗时 20–60 秒，属正常，耐心等待；同会话内后续调用很快。
- 单位：质量属性按 SolidWorks 返回值输出（默认 SI：kg、m、m²、m³），与文档单位设置无关。

## 排错

- 报“无法连接 SolidWorks COM”：检查 SolidWorks 是否安装、`SldWorks.Application` 是否注册（`reg query HKCR\SldWorks.Application\CLSID`）。
- `open_warnings: 128`：文件需要重建等非致命警告，可忽略；`open_error` 出现时优先确认文件路径与引用缺失情况。
- 工程图 `referenced_model` 指向别机器的用户目录路径（如另一台电脑上的绝对路径）：说明图纸引用未随文件走，属文件自身问题，如实告知用户。
- 引用了缺失零部件的工程图仍可读取（错误码会体现在 `open_errors`），视图/表格数据通常完整。
- 脚本对每个 API 访问都做了容错，个别字段拿不到时会是 `null` 或缺失，不影响其余数据；`processing_error` 字段出现时把内容报给用户。

## 技术要点（改脚本前必读）

- pywin32 下 SW 对象是**动态/类型化混合绑定**：顶层对象（SldWorks、ModelDoc2）是动态 CDispatch，属性访问即调用零参方法；子对象多为 gen_py 类型化包装，方法须显式 `()` 调用。脚本里的 `g()` 已统一处理，新增字段一律走 `g(obj, "Name", args..., default=...)`。
- ByRef 出参必须用 `VARIANT(VT_BYREF|VT_I4/_BSTR)` 包装（`OpenDoc6` 的 errors/warnings、`GetMaterialPropertyName2` 的 database、`CustomPropertyManager.Get3` 的值）。
- 装配体配合不在 `IAssemblyDoc` 上，而在特征树 `MateGroup` 特征下，用 `GetFirstSubFeature`/`GetNextSubFeature` 链遍历（`GetSubFeatures` 不存在）。
- 工程图视图用 `GetFirstView`/`GetNextView` 链最可靠（首个视图是图纸本身）；`GetViews(sheet)` 在部分文件上返回空。
- SW2015+ 文件是加密的自定义 chunk 容器（不再是 OLE2），纯 Python 只能读格式标记；完整数据必须走 COM。开源参考：openswx（C++20，可读元数据/BOM，需自行编译）。
