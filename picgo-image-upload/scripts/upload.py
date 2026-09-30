#!/usr/bin/env python3
"""把本地图片上传到用户的 PicGo 图床（腾讯云 COS）。

用法：
  python upload.py <图片路径> [<图片路径> ...]     # 逐张上传
  python upload.py --resolve <本地图片> --name <图床文件名> [<图床文件名> ...]
                                                   # 上传响应超时后，用图床文件名反查并校验 URL

图床外链前缀（--resolve 用）的确定顺序：
  1. 环境变量 PICGO_COS_BASE（如 https://<桶名>.cos.<区域>.myqcloud.com/img）
  2. 本机 PicGo 配置 %APPDATA%\\picgo\\data.json 的 picBed.tcyun
     （bucket/area/path 字段），拼出 https://{bucket}.cos.{area}.myqcloud.com/{path}

依赖：仅 Python 标准库。PicGo 必须在运行（tasklist 查 PicGo.exe）。
注意：用户开了"上传前重命名"，每张图会弹"文件改名"对话框等待确认；
API 请求会因此挂起直至超时——用电脑控制点掉对话框后，上传照常完成，
此时用 --resolve 模式凭对话框里显示的文件名找回 URL 并校验。
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request

SERVER = "http://127.0.0.1:36677/upload"
UPLOAD_TIMEOUT = 240  # 改名对话框挂着时请求会等到超时，属预期


def _cos_base():
    """图床外链前缀：PICGO_COS_BASE 优先，否则从本机 PicGo 配置动态拼出。

    PicGo 的 tcyun 配置含 bucket（桶名）、area（区域，如 ap-shanghai）、
    path（Key 前缀，如 "img/"）。取不到时返回 None，由调用方报错。
    """
    env = os.environ.get("PICGO_COS_BASE")
    if env:
        return env.rstrip("/")
    appdata = os.environ.get("APPDATA") or os.path.expanduser(
        os.path.join("~", "AppData", "Roaming"))
    cfg_path = os.path.join(appdata, "picgo", "data.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            tcyun = json.load(f)["picBed"]["tcyun"]
        prefix = str(tcyun.get("path") or "img").strip("/")
        return f"https://{tcyun['bucket']}.cos.{tcyun['area']}.myqcloud.com/{prefix}"
    except Exception:
        return None


def md5_of(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def upload_one(path):
    req = urllib.request.Request(
        SERVER,
        data=json.dumps({"list": [path]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        body = urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT).read().decode("utf-8")
    except Exception as e:
        print(f"TIMEOUT_OR_ERROR {path} ({e})")
        print("处理：用电脑控制确认 PicGo 的\"文件改名\"对话框（记下其中显示的文件名），")
        print(f"然后运行: python upload.py --resolve \"{path}\" --name <对话框里的文件名>")
        return False
    resp = json.loads(body)
    if resp.get("success") and resp.get("result"):
        print(f"OK {path} -> {resp['result'][0]}")
        return True
    print(f"FAILED {path} -> {body}")
    return False


def resolve(local, names):
    base = _cos_base()
    if not base:
        print("ERROR 无法确定图床外链前缀：请设置环境变量 PICGO_COS_BASE，")
        print("      或确认 %APPDATA%\\picgo\\data.json 的 picBed.tcyun 含 bucket/area 字段。")
        return False
    local_md5 = md5_of(local)
    for name in names:
        url = f"{base}/{name}"
        try:
            data = urllib.request.urlopen(url, timeout=30).read()
        except Exception:
            print(f"MISS {url}")
            continue
        if hashlib.md5(data).hexdigest() == local_md5:
            print(f"OK {local} -> {url}")
            return True
        print(f"MISMATCH {url}（存在但内容不符，别用这张）")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--resolve", metavar="本地图片")
    ap.add_argument("--name", action="append", default=[])
    args = ap.parse_args()

    if args.resolve:
        sys.exit(0 if resolve(args.resolve, args.name) else 1)
    if not args.paths:
        ap.error("需要至少一个图片路径")
    ok = all(upload_one(p) for p in args.paths)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
