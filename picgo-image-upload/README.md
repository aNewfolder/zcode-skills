# picgo-image-upload

把本地图片上传到本机 PicGo 图床（腾讯云 COS），拿到外链 URL 后以 `![描述](URL)` 插入 markdown——文档里只放图床链接，不放本地路径。

## 功能与适用场景

- 适合所有"往 markdown/文档里插图"的场景：课堂笔记配图、文章配图、截图、照片等。
- 首选走 PicGo 本地服务 API（`http://127.0.0.1:36677/upload`）逐张上传；上传响应被"上传前重命名"对话框挂起超时时，用 `--resolve` 模式凭对话框里的文件名反查外链并做 MD5 内容校验，防止张冠李戴。
- 备用方案：剪贴板快捷键上传；PicGo 没运行时用腾讯云 COS SDK 直传（兜底）。

## 依赖

| 依赖 | 用途 |
| --- | --- |
| Python 3.6+ | `upload.py` 仅用标准库（argparse/hashlib/json/os/sys/urllib.request） |
| [PicGo](https://github.com/Molunerfinn/PicGo) 客户端 | 必须已安装、配置好腾讯云 COS 图床并在运行（`tasklist` 可见 `PicGo.exe`） |
| `cos-python-sdk-v5` | 仅备用直传需要（`pip install cos-python-sdk-v5`），能走 PicGo 就不需要 |

## 安装部署

1. 把整个 `picgo-image-upload/` 目录复制到你的 AI 助手技能目录（ZCode 为 `~/.zcode/skills/`，目录名保持不变；任何遵循 SKILL.md 约定的框架均可使用）。
2. 确认 Python 可用；主脚本无需第三方库。
3. 确认 PicGo 已安装并配置了腾讯云 COS 图床（桶为公有读，否则外链无法直接访问）。

## 需要配置的信息

| 项 | 位置 | 说明 |
| --- | --- | --- |
| COS 凭据（secretId/secretKey/bucket/area/path） | `%APPDATA%\picgo\data.json` 的 `picBed.tcyun` | 由 PicGo 客户端维护，**含明文密钥，不要提交到 git** |
| `PICGO_COS_BASE`（可选） | 环境变量 | `--resolve` 反查外链时的前缀覆盖，如 `https://<桶名>.cos.<区域>.myqcloud.com/img`；不设则自动从 PicGo 配置拼出 |

本 skill 无需自己的 config 文件：上传走本机 PicGo 服务，凭据全部由 PicGo 管理。

## 基本用法

```bash
# 逐张上传（PicGo 需在运行）
python ~/.zcode/skills/picgo-image-upload/scripts/upload.py "D:\photos\图1.jpg" "D:\photos\图2.jpg"

# 上传超时后（改名对话框挂起请求属预期，上传照常完成），凭对话框里的文件名反查 URL 并校验
python ~/.zcode/skills/picgo-image-upload/scripts/upload.py --resolve "本地图.jpg" --name 20260917211026371.jpg
```

典型工作流：

1. AI 助手调用脚本上传图片，拿到外链 URL。
2. 若开了"上传前重命名"：用电脑控制逐个确认弹出的"文件改名"对话框并记下文件名，超时后用 `--resolve` 找回 URL（MD5 校验通过才可用）。
3. 在文档中用 `![描述图片实际内容的alt文字](URL)` 插入，不留任何本地路径。

## 注意事项

- **凭据安全**：`%APPDATA%\picgo\data.json` 含 COS 明文密钥，只存本机、不进 git。
- "上传前重命名"开着时，API 请求会被对话框挂起直至超时——响应丢了不要紧，上传照常完成，**不要盲目重发（会传重）**。
- 多张图按上传顺序逐个弹对话框，名字与图片的对应关系以 MD5 校验结果为准。
- 上传后至少 GET 一次确认 URL 可访问（200）且内容正确再用。
- 站点若有 CSP `img-src` 白名单，记得把你的 COS 桶域名加进去。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
