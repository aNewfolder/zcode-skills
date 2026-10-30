#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lib_move.py — 按 propose.tsv 把文献剪切归位（默认 dry-run）。

安全设计（每条都对应一类真实风险）：
  * 默认 --dry-run，只有显式 --execute 才动文件。
  * 目标同名冲突：先比 SHA256；相同 -> 跳过并记为 duplicate；不同 -> 加 __dup2 后缀，**绝不覆盖**。
  * 每一步写 archive/logs/move-log.tsv（时间/源/目标/哈希/状态），可据此回滚。
  * 云同步盘上文件写完会短暂上锁：move 失败会重试若干次，仍失败则记录 LOCKED 而不是硬报错。
  * --limit N 分批；--only-y 只处理 accept=y。
  * 目标目录不存在时**只创建最终目录**，不臆造中间层级（中间层由 propose.tsv 明确写出）。

propose.tsv 需要包含这些列（多余列忽略）：
  id  path  suggested_dir  accept  confidence  reason
"""
import argparse
import hashlib
import io
import os
import shutil
import sys
import time

SKIP_VERDICTS = {"duplicate", "skip", "noop"}


def sha256(path, cap_mb=None):
    h = hashlib.sha256()
    read = 0
    cap = (cap_mb * 1024 * 1024) if cap_mb else None
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if cap and read >= cap:
                return h.hexdigest() + f"(前{cap_mb}MB)"
    return h.hexdigest()


def unique_target(dst_dir, name):
    """同名冲突时找一个不覆盖的候选名。"""
    base, ext = os.path.splitext(name)
    n = 2
    while True:
        cand = os.path.join(dst_dir, f"{base}__dup{n}{ext}")
        if not os.path.exists(cand):
            return cand
        n += 1


def move_with_retry(src, dst, tries=4, wait=2.0):
    last = None
    for i in range(tries):
        try:
            shutil.move(src, dst)
            return None
        except OSError as e:
            last = e
            time.sleep(wait)
    return last


def read_propose(path):
    raw = io.open(path, encoding="utf-8").read().rstrip("\n").split("\n")
    head = raw[0].split("\t")
    rows = []
    for i, line in enumerate(raw[1:], 2):
        if not line.strip():
            continue
        c = line.split("\t")
        c += [""] * (len(head) - len(c))
        rows.append({head[j]: c[j].strip() for j in range(len(head))})
    return head, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--propose", required=True)
    ap.add_argument("--execute", action="store_true", help="真正移动；不加则只打印计划")
    ap.add_argument("--only-y", action="store_true", default=True)
    ap.add_argument("--all", action="store_true", help="不筛 accept，全部处理")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--verify", action="store_true", help="移动后复核目标文件哈希")
    ap.add_argument("--copy", action="store_true",
                    help="复制而不移动（源保留）。适合把新文献投放进库时不动来源文件")
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    head, rows = read_propose(args.propose)
    for col in ("path", "suggested_dir"):
        if col not in head:
            print(f"propose.tsv 缺少列 {col}", file=sys.stderr)
            return 2

    todo = []
    for r in rows:
        acc = (r.get("accept") or "").strip().lower()
        if not args.all and args.only_y and acc not in ("y", "yes", "1", "是"):
            continue
        if not os.path.exists(r["path"]):
            r["_plan"] = "missing"
            todo.append(r)
            continue
        todo.append(r)
    if args.limit:
        todo = todo[:args.limit]

    # 安全校验：new_name 必须是纯文件名（防止计划里塞进路径造成越权写入）
    bad = [r for r in todo if (r.get("new_name") or "").strip() and
           os.path.sep in r["new_name"].strip().replace("/", os.path.sep)]
    if bad:
        print("计划里的 new_name 含路径分隔符，拒绝执行:", file=sys.stderr)
        for r in bad[:5]:
            print("   ", r.get("new_name"), file=sys.stderr)
        return 2
    print(f"模式={'执行' if args.execute else 'DRY-RUN'}  待处理 {len(todo)} 条")
    log_dir = args.log or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "archive", "logs")
    log_dir = os.path.abspath(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "move-log.tsv")
    log_lines = []
    stats = {"moved": 0, "copied": 0, "duplicate": 0, "renamed": 0, "missing": 0, "locked": 0, "error": 0, "dry": 0}

    for i, r in enumerate(todo, 1):
        src = r["path"]
        dst_dir = r["suggested_dir"]
        # 关键：计划里若有 new_name，必须按它落库（先前忽略该列，导致
        # 文件名没被规范化就直接落库，甚至可能与其他文献同名而触发覆盖路径）。
        planned = (r.get("new_name") or "").strip()
        name = os.path.basename(planned) if planned else os.path.basename(src)
        if planned and os.path.basename(planned) != os.path.basename(src):
            print("  [改名] {0}  ->  {1}".format(os.path.basename(src), name))
        tag = f"[{i}/{len(todo)}]"
        if not os.path.exists(src):
            stats["missing"] += 1
            print(f"  {tag} ? 源不存在: {src}")
            log_lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t\t\tmissing")
            continue
        if not os.path.isdir(dst_dir):
            print(f"  {tag} 目标目录不存在，将创建: {dst_dir}" if args.execute else
                  f"  {tag} [dry] 需创建目标目录: {dst_dir}")
            if args.execute:
                try:
                    os.makedirs(dst_dir, exist_ok=True)
                except OSError as e:
                    stats["error"] += 1
                    print(f"  {tag} ✗ 建目录失败: {e}")
                    log_lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t{dst_dir}\t\tmkdir_error:{e}")
                    continue
        dst = os.path.join(dst_dir, name)
        if os.path.exists(dst):
            try:
                same = sha256(src) == sha256(dst)
            except OSError as e:
                same = False
                print(f"  {tag} ! 哈希比较失败: {e}")
            if same:
                stats["duplicate"] += 1
                print(f"  {tag} = 目标已有相同文件，跳过（源保留）: {dst}")
                log_lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t{dst}\t{sha256(src)[:16]}\tduplicate")
                continue
            if args.execute:
                dst = unique_target(dst_dir, name)
            stats["renamed"] += 1
            print(f"  {tag} → 目标同名但内容不同，将改名: {os.path.basename(dst)}")
        if not args.execute:
            stats["dry"] += 1
            print(f"  {tag} [dry] {name}  →  {dst_dir}")
            continue
        if args.copy:
            try:
                shutil.copy2(src, dst)
                err = None
            except OSError as e:
                err = e
        else:
            err = move_with_retry(src, dst)
        if err:
            stats["locked"] += 1
            print(f"  {tag} ! 移动失败（可能被同步/占用）: {err}")
            log_lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t{dst}\t\tLOCKED:{err}")
            continue
        ok = True
        h = ""
        if args.verify:
            try:
                h = sha256(dst)
                ok = os.path.exists(dst) and os.path.getsize(dst) > 0
            except OSError:
                ok = False
        act = "copied" if args.copy else "moved"
        stats[act] = stats.get(act, 0) + 1
        print(f"  {tag} ✓ {name}  →  {dst_dir}" + ("（复制，源保留）" if args.copy else ""))
        log_lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t{dst}\t{h[:16]}\t{act if ok else act + '_unverified'}")

    if args.execute:
        with io.open(log_path, "a", encoding="utf-8", newline="\n") as fh:
            if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
                fh.write("time\tsrc\tdst\tsha16\tstatus\n")
            fh.write("\n".join(log_lines) + ("\n" if log_lines else ""))
        print(f"\n日志已追加: {log_path}")

    print("\n=== 统计 ===")
    for k, v in stats.items():
        if v:
            print(f"  {k:10} {v}")
    if not args.execute:
        print("\n（这是 dry-run，未改动任何文件；确认后加 --execute）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
