#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mineru_parse.py · MinerU 精准解析 API 包装器（mineru-md skill，通用版）

把 PDF / 图片(png/jpg/jpeg/jp2/webp/gif/bmp) / doc(docx) / ppt(pptx) / xls(xlsx)
转成 Markdown。本地文件走「申请上传链接 → PUT 上传 → 批量解析 → 轮询 → 下载 zip」全流程，
产出 <out-dir>/<stem>/full.md + images/（哈希名图）+ layout.json 等官网 zip 原样内容。

子命令：
  parse        解析单个本地文件：parse --file <f> --out-dir <dir> [--model vlm|pipeline]
               [--is-ocr] [--lang ch] [--timeout 900] [--poll 10] [--no-formula] [--no-table]
  store-token  一次性存令牌（PowerShell 掩码输入，DPAPI 加密，令牌不经 AI）

令牌解析顺序：环境变量 MINERU_API_TOKEN
  → %LOCALAPPDATA%\\mineru-md\\mineru-cred.xml
令牌以 SecureString XML（DPAPI，当前 Windows 账户域）存于 cred 文件，由 PowerShell 存/解。
注意：沙箱里 Python 直调 DPAPI 会被硬杀（exit 127），必须经 powershell 子进程解密，别改回 ctypes。

信任域红线：MinerU API 是云端服务——只发可公开的文档；敏感/未发布材料走本地引擎，别碰本脚本。
"""
import argparse
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

API = "https://mineru.net/api/v4"
CRED_XML = os.path.join(os.environ.get("LOCALAPPDATA", ""), "mineru-md", "mineru-cred.xml")


def _cred_path():
    return CRED_XML if CRED_XML and os.path.isfile(CRED_XML) else None


def load_token():
    env = (os.environ.get("MINERU_API_TOKEN") or "").strip()
    if env:
        return env
    cred = _cred_path()
    if not cred:
        sys.exit("mineru_parse：找不到令牌——先跑 store-token 子命令，或设环境变量 MINERU_API_TOKEN（见 SKILL.md）")
    ps = (
        "$s = Import-Clixml '%s'; "
        "$b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); "
        "$p = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b); "
        "[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b); Write-Output $p" % cred
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        sys.exit("mineru_parse：非 Windows 环境无 DPAPI，请改设环境变量 MINERU_API_TOKEN")
    tok = (r.stdout or "").strip()
    if not tok:
        sys.exit("mineru_parse：令牌解密失败 " + (r.stderr or "")[-200:])
    return tok


def cmd_store_token(args):
    if sys.platform != "win32":
        sys.exit("store-token 仅支持 Windows；其他平台请设环境变量 MINERU_API_TOKEN")
    os.makedirs(os.path.dirname(CRED_XML), exist_ok=True)
    ps = ("$t = Read-Host -AsSecureString '粘贴 MinerU API Token'; "
          "Export-Clixml -InputObject $t -Path '%s'; Write-Output '已存储'" % CRED_XML)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps])
    sys.exit(r.returncode)


def api_req(url, token, payload=None, method="GET", raw=None, ctype="application/json"):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if raw is not None:
        # 注意：预签名 PUT 不可附带额外头（Content-Type 会破坏 OSS 签名 → 403），ctype 传 None
        if ctype:
            req.add_header("Content-Type", ctype)
        body = raw
    elif payload is not None:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(payload).encode("utf-8")
    else:
        body = None
    # 连接级抖动（SSL EOF / 10060 超时）重试 3 次——实测：本机外网代理开关切换期间
    # mineru.net 会被截流瞬断，网络恢复后同请求即可成功
    last = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, data=body, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError("HTTP %s @ %s" % (e.code, url[:120])) from None
        except (urllib.error.URLError, OSError, ssl.SSLError) as e:
            last = e
            print("  [重试 %d/3] %s: %s" % (attempt, type(e).__name__, str(e)[:80]))
            time.sleep(5 * attempt)
    raise RuntimeError("连接失败（重试 3 次后放弃）@ %s: %s" % (url[:120], last))


def cmd_parse(args):
    token = load_token()
    src = os.path.abspath(args.file)
    if not os.path.isfile(src):
        sys.exit("mineru_parse：文件不存在 %s" % src)
    size_mb = os.path.getsize(src) / 1048576
    if size_mb > 200:
        sys.exit("mineru_parse：文件 %.0f MB 超上限 200 MB" % size_mb)
    stem = os.path.splitext(os.path.basename(src))[0]
    out_dir = os.path.join(os.path.abspath(args.out_dir), stem)
    if os.path.isdir(out_dir):
        sys.exit("mineru_parse：输出目录已存在（不覆盖）：%s —— 若 full.md 已在即先前解析成功，直接复用" % out_dir)

    # ① 申请上传链接（批量接口，单文件当批）
    body = {
        "enable_formula": not args.no_formula,
        "enable_table": not args.no_table,
        "language": args.lang,
        "model_version": args.model,
        "files": [{"name": os.path.basename(src), "is_ocr": args.is_ocr,
                   "data_id": stem[:120]}],
    }
    resp = json.loads(api_req(API + "/file-urls/batch", token, payload=body, method="POST"))
    if resp.get("code") != 0:
        sys.exit("mineru_parse：申请上传链接失败 %s" % json.dumps(resp, ensure_ascii=False)[:300])
    batch_id = resp["data"]["batch_id"]
    upload_url = resp["data"]["file_urls"][0]

    # ② PUT 上传（预签名 URL：不带 Authorization、不带 Content-Type，裸传字节）。
    #    urllib 的默认头会破坏 OSS 签名（403），用 curl 最小头裸传最稳。
    print("上传中（%.1f MB）…" % size_mb)
    cr = subprocess.run(["curl", "-sS", "-X", "PUT", "-H", "Content-Type;",
                         "--data-binary", "@" + src, "-o", os.devnull,
                         "-w", "%{http_code}", upload_url],
                        capture_output=True, text=True, timeout=600)
    if cr.stdout.strip() not in ("200", "201", ""):
        sys.exit("mineru_parse：OSS 上传失败 HTTP %s %s" % (cr.stdout.strip()[:40], cr.stderr[:200]))

    # ③ 轮询批量结果
    deadline = time.time() + args.timeout
    state = last = None
    while time.time() < deadline:
        time.sleep(args.poll)
        j = json.loads(api_req(API + "/extract-results/batch/" + batch_id, token))
        if j.get("code") != 0:
            sys.exit("mineru_parse：轮询失败 %s" % json.dumps(j, ensure_ascii=False)[:300])
        item = (j["data"].get("extract_result") or [{}])[0]
        state = item.get("state")
        prog = item.get("extract_progress") or state
        if prog != last:
            print("  %s" % prog)
            last = prog
        if state in ("done", "failed"):
            break
    if state != "done":
        sys.exit("mineru_parse：任务未完成（state=%s）" % state)
    zip_url = item["full_zip_url"]
    if item.get("err_msg"):
        print("  err_msg: %s" % item["err_msg"])

    # ④ 下载 zip 并解包（zip 名带 pid：多进程并发解析不互抢同一个临时文件）
    zdata = api_req(zip_url, None)
    os.makedirs(out_dir, exist_ok=True)
    zpath = os.path.join(tempfile.gettempdir(), "mineru_result_%d.zip" % os.getpid())
    open(zpath, "wb").write(zdata)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out_dir)
    os.remove(zpath)

    mds = [f for f in os.listdir(out_dir) if f.endswith(".md")]
    md = os.path.join(out_dir, mds[0]) if mds else ""
    n_img = len([f for f in os.listdir(os.path.join(out_dir, "images"))]) \
        if os.path.isdir(os.path.join(out_dir, "images")) else 0
    print(json.dumps({"out_dir": out_dir, "md": md, "images": n_img,
                      "batch_id": batch_id, "model": args.model}, ensure_ascii=False))
    print("下一步：md 内图片引用为相对路径 images/…（哈希名）；如需语义命名，逐张看图重命名并同步改 md 引用")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("store-token", help="一次性存 MinerU API 令牌（掩码输入，DPAPI 加密）")
    p.set_defaults(func=cmd_store_token)

    p = sub.add_parser("parse", help="本地文件 → MinerU 解析 → md + images")
    p.add_argument("--file", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--model", default="vlm", choices=["vlm", "pipeline"])
    p.add_argument("--is-ocr", action="store_true", help="强制 OCR（扫描件/图片型 PDF 开）")
    p.add_argument("--lang", default="ch")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--poll", type=int, default=10)
    p.add_argument("--no-formula", action="store_true")
    p.add_argument("--no-table", action="store_true")
    p.set_defaults(func=cmd_parse)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
