# -*- coding: utf-8 -*-
"""zyframes.py —— 智云"纯板书课"板书重建：从回放视频抽帧恢复黑板状态序列。

背景：部分课程全程黑板手写、无 PPT，智云
pptnote 时间轴为空（只有 1 张教室桌面截图）。此时讲义素材只能从回放视频
（黑板机位 1080p 录制）抽帧重建。

流程（三个探索版本迭代后的最终口径，2026-11-05 实测）：
  1. 每 interval 秒用 ffmpeg -ss 快速 seek 抽候选帧（本地/远程 URL 均可）。
  2. 自动检测黑板区域（暗绿色主导像素 bbox），可 --crop 覆盖。
  3. 粉笔掩码 = 灰度>150（黑板深绿 ~90-110，粉笔白 ~180-230）；
     掩码占比（粉笔量）骤降 > era-drop 视为"擦板"，切分板书时代（era）。
     注意：不能用相邻帧差分做分段——增量写字每 8s 只变化 0.1-0.5%，
     远低于人走动的变化量，会把几十分钟并成一个状态（已踩坑）。
  4. 每时代取粉笔量最丰富的 top-k 帧（相互 xor 去重）+ 时代首帧。
  5. 输出联络表（contact sheet，3x2 板区大图）供人工挑选讲义配图；
     挑中的帧可再对局部区域 2-3x 放大裁剪以读清数字。

用法：
  python zyframes.py <video.mp4 或 URL> <out_dir> [--interval 8] [--era-drop 0.6]
                     [--topk 4] [--crop x,y,w,h] [--start N] [--end N]

依赖：requests、numpy、opencv-python、imageio-ffmpeg（自带 ffmpeg 二进制，
无需系统安装 ffmpeg）、Pillow（联络表，缺了只跳过联络表）。

输出：
  out/cand/cand-NNNN.jpg          候选帧（全画幅）
  out/era-EEE-K-mmsss-chalkX.jpg  板书时代代表帧（板区裁剪）
  out/eras.json                   时代时间轴（与转写时间对齐用）
  out/sheet-NN.jpg                联络表
stdout 末行为 JSON。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

CHALK_LO = 150


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, default=str))
    sys.exit(code)


def find_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    p = subprocess.run(["where", "ffmpeg"], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().splitlines()[0]
    out({"ok": False, "error": "NoFFmpeg",
         "message": "pip install imageio-ffmpeg（自带 ffmpeg 二进制）或安装系统 ffmpeg"}, 1)


def probe_duration(ff, video):
    r = subprocess.run([ff, "-i", str(video), "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    for line in (r.stderr or "").splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].strip().split(":")[:3]
            s = re.match(r"[\d.]+", s).group(0)
            return int(h) * 3600 + int(m) * 60 + float(s)
    out({"ok": False, "error": "NoDuration", "message": "无法读取视频时长"}, 1)


def grab_frame(ff, video, t, out_path):
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}",
                        "-i", str(video), "-frames:v", "1", "-q:v", "2", "-y", str(out_path)],
                       capture_output=True)
    return r.returncode == 0 and Path(out_path).exists()


def board_bbox(img):
    small = cv2.resize(img, (480, 270))
    b, g, r = (small[:, :, i].astype(int) for i in range(3))
    mask = ((g > r + 20) & (g > b + 15) & (g > 60) & (g < 200)).astype(np.uint8)
    ys, xs = np.where(mask)
    if len(xs) < 500:
        return None
    x0, x1 = np.percentile(xs, [1, 99])
    y0, y1 = np.percentile(ys, [1, 99])
    sx, sy = img.shape[1] / 480, img.shape[0] / 270
    x, y = max(0, int(x0 * sx) - 8), max(0, int(y0 * sy) - 8)
    w = min(img.shape[1] - x, int((x1 - x0) * sx) + 16)
    h = min(img.shape[0] - y, int((y1 - y0) * sy) + 16)
    return (x, y, w, h)


def contact_sheets(rows, out_dir, cand_dir, cols=3, rows_n=2, tw=620):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return []
    th = int(tw * 359 / 1604)
    per = cols * rows_n
    sheets = []
    for si in range(0, len(rows), per):
        grp = rows[si:si + per]
        sh = Image.new("RGB", (cols * tw, rows_n * (th + 20)), "white")
        dr = ImageDraw.Draw(sh)
        for i, r in enumerate(grp):
            im = Image.open(out_dir / r["file"])
            im.thumbnail((tw, th))
            cx, cy = (i % cols) * tw, (i // cols) * (th + 20)
            sh.paste(im, (cx, cy))
            t = r["t"]
            dr.text((cx + 4, cy + th + 4),
                    f"E{r['era']}#{r['rank']} {int(t)//60}:{int(t)%60:02d} chalk={r['chalk']}%",
                    fill="black")
        p = out_dir / f"sheet-{len(sheets)+1:02d}.jpg"
        sh.save(p, quality=85)
        sheets.append(str(p))
    return sheets


def main():
    zjuauth_force_utf8()
    ap = argparse.ArgumentParser(description="智云纯板书课：回放抽帧重建板书序列")
    ap.add_argument("video", help="回放视频路径或 URL（URL 需带鉴权参数）")
    ap.add_argument("out", help="输出目录")
    ap.add_argument("--interval", type=float, default=8.0, help="候选帧间隔秒")
    ap.add_argument("--era-drop", type=float, default=0.6, help="粉笔量降幅视为擦板(%%)")
    ap.add_argument("--topk", type=int, default=4, help="每时代代表帧数")
    ap.add_argument("--crop", default=None, help="黑板区域 x,y,w,h（默认自动检测）")
    ap.add_argument("--start", type=float, default=2.0)
    ap.add_argument("--end", type=float, default=None)
    a = ap.parse_args()

    ff = find_ffmpeg()
    out_dir = Path(a.out)
    cand_dir = out_dir / "cand"
    cand_dir.mkdir(parents=True, exist_ok=True)

    dur = probe_duration(ff, a.video)
    end = a.end if a.end is not None else dur - 2.0
    ts = list(np.arange(a.start, end, a.interval))
    cands = []
    for i, t in enumerate(ts, 1):
        p = cand_dir / f"cand-{i:04d}.jpg"
        if not p.exists():
            if not grab_frame(ff, a.video, t, p):
                continue
        cands.append(p)
    if not cands:
        out({"ok": False, "error": "NoFrames", "message": "候选帧抽取失败"}, 1)

    first = cv2.imread(str(cands[0]))
    crop = tuple(int(v) for v in a.crop.split(",")) if a.crop else board_bbox(first)
    if not crop:
        crop = (0, 0, first.shape[1], first.shape[0])

    masks, chalk = [], []
    for p in cands:
        im = cv2.imread(str(p))
        board = im[crop[1]:crop[1] + crop[3], crop[0]:crop[0] + crop[2]]
        g = cv2.cvtColor(cv2.resize(board, (512, 115)), cv2.COLOR_BGR2GRAY)
        m = (g > CHALK_LO).astype(np.uint8)
        masks.append(m)
        chalk.append(float(m.mean() * 100))

    # 时代切分：粉笔量骤降 = 擦板（勿用相邻帧差分，会被人走动打碎/被增量写字糊死）
    era_id, e = [0] * len(cands), 0
    for i in range(1, len(cands)):
        if chalk[i - 1] - chalk[i] > a.era_drop:
            e += 1
        era_id[i] = e
    eras = {}
    for i, eid in enumerate(era_id):
        eras.setdefault(eid, []).append(i)

    def mmss(t):
        return f"{int(t)//60:02d}m{int(t)%60:02d}s"

    rows = []
    for eid in sorted(eras):
        ids = eras[eid]
        if max(chalk[i] for i in ids) < 0.35:
            continue  # 全程空板
        ranked = sorted(ids, key=lambda i: -chalk[i])
        picked = []
        for i in ranked:
            if all(np.logical_xor(masks[i], masks[j]).mean() * 100 > 1.0 for j in picked):
                picked.append(i)
            if len(picked) >= a.topk:
                break
        if ids[0] not in picked:
            picked.append(ids[0])
        for k, i in enumerate(sorted(picked), 1):
            im = cv2.imread(str(cands[i]))
            board = im[crop[1]:crop[1] + crop[3], crop[0]:crop[0] + crop[2]]
            name = f"era-{eid+1:03d}-{k}-{mmss(ts[i])}-chalk{chalk[i]:.1f}.jpg"
            cv2.imwrite(str(out_dir / name), board, [cv2.IMWRITE_JPEG_QUALITY, 88])
            rows.append({"era": eid + 1, "rank": k, "t": round(ts[i], 1),
                         "chalk": round(chalk[i], 2), "file": name,
                         "era_start": round(ts[ids[0]], 1), "era_end": round(ts[ids[-1]], 1)})

    (out_dir / "eras.json").write_text(json.dumps(
        {"interval": a.interval, "era_drop": a.era_drop, "crop": list(crop),
         "duration": dur, "n_candidates": len(cands), "frames": rows},
        ensure_ascii=False, indent=1), "utf-8")
    sheets = contact_sheets(rows, out_dir, cand_dir)
    out({"ok": True, "duration": dur, "crop": list(crop), "candidates": len(cands),
         "eras": len(eras), "frames": len(rows), "sheets": sheets, "out_dir": str(out_dir)})


def zjuauth_force_utf8():
    try:
        import zjuauth
        zjuauth.force_utf8()
    except Exception:
        pass


if __name__ == "__main__":
    main()
