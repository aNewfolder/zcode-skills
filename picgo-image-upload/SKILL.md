---
name: picgo-image-upload
description: 把本地图片上传到用户的 PicGo 图床（腾讯云 COS），拿到外链后插入 markdown。凡是需要往 markdown/文档里插入图片的场景一律走本 skill——包括课堂笔记配图、文章配图、截图、照片等，即使用户只说"插图""贴图""把图放进去""上传图片"也要触发；文档里只放图床链接，不放本地路径、不把图片塞进仓库。也用于给 MkDocs 站点（alight404.top）的文章插图。
---

# 图片上传 PicGo 图床

把本地图片上传到用户的 PicGo 图床，文档中只放图床链接。

**核心规则**：凡需要往 markdown 里插入图片，一律先经本 skill 上传图床，再用 `![描述](图床URL)` 插入。不落本地相对/绝对路径，不把图片文件放进 git 仓库（例外：站点设计素材如首页背景、Logo 归 mkdocs-site-manager 管，放 `docs/images/`）。

## 图床信息

- 后端：腾讯云 COS（公有读桶），前缀 `img/`，文件名为时间戳（如 `20260917211026371.jpg`）。
- 外链形如 `https://<你的COS桶域名>/img/<时间戳>.<原扩展名>`（即 `https://<桶名>.cos.<区域>.myqcloud.com/...`）；若你的站点有 CSP `img-src` 白名单，需把该桶域名加进去。
- PicGo 配置（含 COS 凭据）：`%APPDATA%\picgo\data.json` 的 `picBed.tcyun`。

## 上传流程（首选：本地服务 API + 脚本）

PicGo 必须在运行（`tasklist | grep -i picgo` 查 `PicGo.exe`；没运行就启动它）。它开着本地 HTTP 服务：

```
POST http://127.0.0.1:36677/upload
Content-Type: application/json
{"list": ["图片绝对路径", ...]}
成功 → {"success": true, "result": ["URL", ...]}
```

用打包好的脚本逐张上传（依赖仅标准库）：

```bash
python ~/.zcode/skills/picgo-image-upload/scripts/upload.py "D:\photos\图1.jpg" "D:\photos\图2.jpg"
```

### 必看：上传前重命名对话框

用户开了"上传前重命名"，**每张图会弹出"文件改名"对话框**（预填时间戳文件名，点"确定"或回车确认）。后果与处理：

- API 请求会被对话框挂起直至超时——**响应丢了不要紧，上传照常完成**，不要盲目重发（会传重）。
- 正确姿势：请求发出后用电脑控制（get_app_state 观察 PicGo 窗口）找到改名对话框，**记下对话框里显示的文件名**，逐个点"确定"。
- 响应超时后用 resolve 模式凭文件名找回 URL 并做内容校验（GET 后比对 MD5，防止张冠李戴）。外链前缀自动从本机 PicGo 配置（`picBed.tcyun` 的 bucket/area/path）拼出，也可用环境变量 `PICGO_COS_BASE` 覆盖：

  ```bash
  python ~/.zcode/skills/picgo-image-upload/scripts/upload.py --resolve "本地图.jpg" --name 20260917211026371.jpg
  ```

- 多张图按上传顺序逐个弹对话框，名字与图片的对应关系以 MD5 校验结果为准（顺序可能和直觉不一致）。

### 备用方法

1. **剪贴板快捷键**：把图片复制到剪贴板（位图），按 `Ctrl+Shift+U`（用户配置的 PicGo 快捷上传键），上传成功后 URL 自动进剪贴板，用 `read_clipboard` 读取。同样会弹改名对话框。
2. **COS SDK 直传**（PicGo 没运行时）：`pip install cos-python-sdk-v5`，凭据读 `%APPDATA%\picgo\data.json` 的 `picBed.tcyun`，`put_object` 到 `img/<时间戳>.<ext>`，ContentType 按图片类型给。这是兜底，能走 PicGo 就走 PicGo。

## 上传后

1. 校验 URL 可访问且内容正确（脚本 resolve 模式已做 MD5 校验；其他方法至少 GET 一次确认 200）。
2. 文档里用 `![描述图片内容的alt文字](URL)` 插入；描述写图片实际内容（如"课本图9.10 高斯定理证明用图"），不写"图片1"这种废话。
3. 不留任何本地路径。

## 插入位置约定

课堂笔记场景（配合 class-notes-integrator）：图片插在笔记点名要它的位置（如"FA：图9.10""FA：图片，综例"），次序跟随手写笔记；手写笔记页本身只转文字不插图；用户明确说"单纯转文字"时不插图。
