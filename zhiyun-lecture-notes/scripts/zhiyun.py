# -*- coding: utf-8 -*-
"""zhiyun.py —— 浙大智云课堂客户端：课程/课次/回放、PPT 时间轴截图、官方语音转写字幕。

端点口径（来自 Anchorite-no/zju-skill-public 的 lib/zhiyun，2026-09/10 实测）：
- 登录：GET tgmedia.cmc.zju.edu.cn/index.php?r=auth%2Flogin&forward=classroom.zju.edu.cn/
  → 30x 链路经统一认证 CAS → 成功后在 .zju.edu.cn 域拿到 JWTUser cookie（JWT）。
- API 鉴权：cookie `_token`（URL 解码后的 JWT）作 `Authorization: Bearer <token>`；
  Accept: application/json + Referer classroom.zju.edu.cn。
- 失效表现：HTTP 401 / body code==401 / 302 到 cas 或 auth/login / 返回 HTML。
- 我的课程 education.cmc.zju.edu.cn/personal/courseapi/vlabpassportapi/v1/account-profile/course
    ?nowpage=&per-page=&force_mycourse=1 → params.result.{total, data:[{Id,Title,Teacher|realname,TermName}]}
- 全站搜索 classroom.zju.edu.cn/pptnote/v1/searchlist?tenant_id&user_id&user_name&title&realname&page&per_page&source=1
    → data.total.{total, list:[{course_id, sub_id, subject_title, ...}]}
- 课次目录 yjapi.cmc.zju.edu.cn/courseapi/v2/course/catalogue?course_id=
    → data.result.data[]：{type∈course_live/live/ilive, sub_id, title, start_at(epoch s), status,
      lecturer_name, content(JSON: playback/audio_url/file_list/download)}
    按开课时间升序编号为第 1..N 次课；status=="6" 且有音视频 = 可回放。
- PPT 时间轴 classroom.zju.edu.cn/pptnote/v1/schedule/search-ppt?course_id&sub_id&page&per_page
    → {list:[{content(JSON: pptimgurl, created), created_sec}], total}，按 created_sec 排序即课堂板书时间线。
- 语音转写 yjapi.cmc.zju.edu.cn/courseapi/v3/web-socket/search-trans-result?sub_id=&format=json
    （Referer: classroom.zju.edu.cn/livingroom?course_id=..&sub_id=..）
    → {list:[{all_content:[{BeginSec,EndSec,Text}]}]}；无转写时站点返回"未查询到语音数据"。
- 回放信息 yjapi.cmc.zju.edu.cn/courseapi/v3/portal-home-setting/get-sub-info?course_id&sub_id
    → data.playurl（字符串或 {清晰度:url}）。

fetch 命令把一节课的 PPT 截图（去重：相同内容只保留最后一次出现，即"最终版"）与转写文本
落到输出目录，并打印 manifest JSON；讲义撰写是 agent 的工作，本脚本不产出讲义。
限速：API 请求相邻 >=1.2s；PPT 图片为静态 CDN 资源，允许 4 并发下载。
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, unquote

import requests

import zjuauth
from zjuauth import (log, rate_sleep, acquire_lock, release_lock, persist_session,
                    restore_session, clear_session)

BASE = "https://classroom.zju.edu.cn"
API = "https://yjapi.cmc.zju.edu.cn"
MY_COURSES = "https://education.cmc.zju.edu.cn/personal/courseapi/vlabpassportapi/v1/account-profile/course"
LOGIN = "https://tgmedia.cmc.zju.edu.cn/index.php?r=auth%2Flogin&forward=https%3A%2F%2Fclassroom.zju.edu.cn%2F"
HOSTS = ("classroom.zju.edu.cn", "yjapi.cmc.zju.edu.cn", "education.cmc.zju.edu.cn", "tgmedia.cmc.zju.edu.cn")
SESSION_NAME = "zhiyun"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, default=str))
    sys.exit(code)


class Zhiyun:
    def __init__(self):
        self.s = zjuauth.new_session()
        self.logged_in = False
        if restore_session(SESSION_NAME, self.s):
            self.logged_in = self._has_jwt()

    def _has_jwt(self):
        return any(c.name == "JWTUser" and (c.domain or "").endswith("zju.edu.cn") for c in self.s.cookies)

    def _ok(self, s, r):
        return any(c.name == "JWTUser" and (c.domain or "").endswith("zju.edu.cn") for c in s.cookies) \
            and "classroom.zju.edu.cn" in r.url

    def login(self, force=False):
        if self.logged_in and not force:
            return
        clear_session(SESSION_NAME)
        for c in list(self.s.cookies):
            if c.name in ("JWTUser", "_token", "login_cmc_id", "login_cmc_tid", "group_code",
                          "login_cmc_url", "login_cmc_type", "cmc_version", "tenant_code"):
                try:
                    self.s.cookies.clear(c.domain, c.path, c.name)
                except Exception:
                    pass
        s, r, reused = zjuauth.cas_sso(LOGIN, self._ok, fresh=force)
        self.s = s
        self.logged_in = True
        persist_session(SESSION_NAME, s, HOSTS + ("zju.edu.cn",))
        log("[zhiyun] 登录成功（%s）" % ("复用会话" if reused else "CAS 新登录"))

    def _token(self):
        raw = next((c.value for c in self.s.cookies if c.name == "_token"), None)
        if not raw:
            return None
        m = TOKEN_RE.search(unquote(raw))
        return m.group(0) if m else None

    def user(self):
        raw = next((c.value for c in self.s.cookies if c.name == "JWTUser"), None)
        if not raw:
            raise RuntimeError("JWTUser cookie 缺失，会话异常")
        try:
            j = json.loads(unquote(raw))
            return {"id": str(j["id"]), "account": str(j.get("account")), "tenant_id": str(j["tenant_id"])}
        except Exception:
            raise RuntimeError("JWTUser cookie 解析失败，请 zhiyun.py login --fresh")

    def api_get(self, url, params=None, referer=BASE + "/", attempt_auth=True):
        self.login()
        full = url + ("&" if "?" in url else "?") + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in (params or {}).items())
        headers = {"Accept": "application/json", "Referer": referer}
        tok = self._token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        rate_sleep(1.2)
        r = self.s.get(full, headers=headers, timeout=30)
        body = r.text or ""
        is_html = re.match(r"^\s*<(?:!doctype|html)", body, re.I) is not None
        if r.status_code == 401 or (r.is_redirect and re.search(r"/cas/|auth%2[fF]login|auth/login", r.headers.get("Location", ""))) or is_html:
            # 会话失效（注意：个别接口字幕响应的 Content-Type 是 text/html 但 body 是 JSON，按 body 判断）
            if not attempt_auth:
                raise RuntimeError("智云会话失效且重登被禁止")
            log("[zhiyun] 会话失效，重新登录")
            self.login(force=True)
            return self.api_get(url, params, referer, attempt_auth=False)
        if r.status_code >= 400:
            raise RuntimeError(f"智云 HTTP {r.status_code}: {r.url.split('?')[0]}")
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"智云响应不是 JSON：{body[:200]}")
        if isinstance(j, dict):
            if j.get("success") is False:
                msg = j.get("msg") or j.get("message") or j.get("code")
                raise RuntimeError(f"智云拒绝：{msg}（{r.url.split('?')[0]}）")
            code = j.get("code")
            if code is not None and str(code) not in ("0", "200", "1000") and ("data" in j or "result" in j or "list" in j or "total" in j):
                msg = j.get("msg") or j.get("message") or code
                raise RuntimeError(f"智云拒绝：{msg}（{r.url.split('?')[0]}）")
        return j

    # ---- 业务 ----
    def my_courses(self, keyword=None):
        rows, seen, page = [], set(), 1
        while True:
            if page > 20:
                raise RuntimeError("我的课程分页超限")
            j = self.api_get(MY_COURSES, {"nowpage": page, "per-page": 100, "force_mycourse": 1})
            result = ((j.get("params") or {}).get("result")) or {}
            data = result.get("data") or []
            total = int(result.get("total") or 0)
            for c in data:
                cid = str(c.get("Id") or c.get("course_id") or c.get("id"))
                if cid in seen:
                    continue
                seen.add(cid)
                rows.append({"id": cid,
                             "title": str(c.get("Title") or c.get("title") or ""),
                             "teacher": str(c.get("Teacher") or c.get("realname") or c.get("lecturer_name") or ""),
                             "term": str(c.get("TermName") or c.get("term_name") or ""),
                             "url": f"{BASE}/coursedetail?course_id={cid}"})
            if len(rows) >= total or not data:
                break
            page += 1
        if keyword:
            kw = keyword.lower()
            rows = [c for c in rows if kw in (c["title"] + " " + c["teacher"] + " " + c["term"]).lower()]
        return rows

    def search_all(self, keyword=None, teacher=None, page=1, per_page=50):
        if not keyword and not teacher:
            raise RuntimeError("全站搜索需要 --keyword 或 --teacher")
        u = self.user()
        j = self.api_get(BASE + "/pptnote/v1/searchlist",
                         {"tenant_id": u["tenant_id"], "user_id": u["id"], "user_name": u["account"],
                          "title": keyword or "", "realname": teacher or "",
                          "page": page, "per_page": per_page, "source": 1})
        total_obj = j.get("total")
        listing = total_obj.get("list") if isinstance(total_obj, dict) else None
        t = int(total_obj.get("total") or 0) if isinstance(total_obj, dict) else 0
        items = []
        for c in listing or []:
            items.append({"course_id": str(c.get("course_id")), "sub_id": str(c.get("sub_id")),
                          "title": str(c.get("subject_title") or ""),
                          "course": str(c.get("Title") or c.get("title") or ""),
                          "teacher": str(c.get("Teacher") or c.get("realname") or ""),
                          "term": str(c.get("TermName") or "")})
        return {"total": t, "page": page, "items": items}

    def resolve_course(self, query, teacher=None):
        if query.isdigit():
            mine = self.my_courses()
            hit = next((c for c in mine if c["id"] == query), None)
            return {"id": query, "course": hit}
        mine = self.my_courses(keyword=query)
        if teacher:
            mine = [c for c in mine if teacher in c["teacher"]]
        if not mine:
            # 我的课程按简称没找到 → 逐门看简称（计组→计算机组成）
            abbrev = query in ("计组", "数电", "模电", "光电子", "物光", "应光", "微机", "电磁场", "数信")
            if abbrev:
                allc = self.my_courses()
                mine = [c for c in allc if query in c["title"]]
        if mine:
            return {"id": mine[0]["id"], "course": mine[0], "matches": len(mine)}
        site = self.search_all(keyword=query, teacher=teacher)
        if site["items"]:
            titles = sorted({(i["course"], i["teacher"], i["term"], i["course_id"]) for i in site["items"]})
            if len(titles) == 1:
                return {"id": titles[0][3], "course": {"title": titles[0][0], "teacher": titles[0][1], "term": titles[0][2]}}
            raise RuntimeError("匹配到多门课，请指定 --teacher 或用课程 id："
                               + json.dumps([{"id": t[3], "course": t[0], "teacher": t[1], "term": t[2]} for t in titles[:10]], ensure_ascii=False))
        raise RuntimeError(f"智云未找到课程「{query}」（该课可能没有智云录播）")

    def catalogue(self, course_id):
        j = self.api_get(API + "/courseapi/v2/course/catalogue", {"course_id": course_id})
        data = ((j.get("result") or {}).get("data")) or []
        rows, unknown = [], set()
        for r in data:
            if str(r.get("sub_delete", "0")) != "0" or r.get("sub_show") == "no":
                continue
            typ = str(r.get("type") or "")
            if typ not in ("course_live", "live", "ilive"):
                unknown.add(typ)
                continue
            try:
                start = float(r.get("start_at"))
            except (TypeError, ValueError):
                continue
            content = {}
            try:
                content = json.loads(r.get("content") or "{}")
            except ValueError:
                pass
            assets = []
            pb = content.get("playback")
            if isinstance(pb, dict) and pb.get("url"):
                assets.append({"kind": "video", "url": pb["url"]})
            if content.get("audio_url"):
                assets.append({"kind": "audio", "url": content["audio_url"]})
            for f in content.get("file_list") or []:
                if isinstance(f, dict) and str(f.get("file_name", "")).startswith("http"):
                    ext = str(f["file_name"]).split("?")[0].rsplit(".", 1)[-1].lower()
                    assets.append({"kind": "audio" if ext in ("mp3", "m4a", "aac", "wav") else "video",
                                   "url": f["file_name"]})
            dl = content.get("download")
            if isinstance(dl, dict):
                for k, v in dl.items():
                    if isinstance(v, dict) and (v.get("path_name") or v.get("url")):
                        assets.append({"kind": "courseware", "url": v.get("path_name") or v.get("url")})
            rows.append({"sub_id": str(r["sub_id"]), "title": str(r.get("title") or ""),
                         "teacher": str(r.get("lecturer_name") or ""),
                         "start_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(start)),
                         "_ts": start, "status": str(r.get("status")),
                         "playable": str(r.get("status")) == "6" and any(a["kind"] in ("video", "audio") for a in assets),
                         "assets": assets})
        if not rows and unknown:
            raise RuntimeError(f"课次目录全是未识别类型：{sorted(unknown)}，平台可能改版")
        rows.sort(key=lambda x: x["_ts"])
        out_rows = []
        for i, r in enumerate(rows, 1):
            r2 = {k: v for k, v in r.items() if k != "_ts"}
            r2["lesson"] = i
            out_rows.append(r2)
        return out_rows

    def slides(self, course_id, sub_id):
        items, seen, page = [], set(), 1
        while True:
            if page > 200:
                raise RuntimeError("PPT 分页超过 200 页")
            j = self.api_get(BASE + "/pptnote/v1/schedule/search-ppt",
                             {"course_id": course_id, "sub_id": sub_id, "page": page, "per_page": 100})
            lst = j.get("list")
            if not isinstance(lst, list):
                raise RuntimeError("PPT 响应缺少 list")
            added = 0
            for raw in lst:
                content = raw.get("content")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except ValueError:
                        continue
                if not isinstance(content, dict):
                    continue
                url_ = content.get("pptimgurl")
                sec = raw.get("created_sec", content.get("created", 0))
                try:
                    sec = float(sec)
                except (TypeError, ValueError):
                    continue
                if not isinstance(url_, str) or not url_.startswith("http"):
                    continue
                key = f"{sec}:{url_}"
                if key in seen:
                    continue
                seen.add(key)
                added += 1
                items.append({"url": url_, "sec": sec})
            total = j.get("total")
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = len(items)
            if not lst or len(seen) >= total:
                break
            if not added:
                raise RuntimeError("PPT 分页重复，未能取完时间轴")
            page += 1
        items.sort(key=lambda x: x["sec"])
        return items

    def transcript(self, course_id, sub_id):
        referer = f"{BASE}/livingroom?course_id={course_id}&sub_id={sub_id}"
        try:
            j = self.api_get(API + "/courseapi/v3/web-socket/search-trans-result",
                             {"sub_id": sub_id, "format": "json"}, referer=referer)
        except RuntimeError as e:
            if "未查询到语音数据" in str(e):
                return []
            raise
        caps = []
        for raw in j.get("list") or []:
            entries = raw.get("all_content") if isinstance(raw.get("all_content"), list) else [raw]
            for e in entries:
                try:
                    start = float(e.get("BeginSec", e.get("start_time")))
                    text = e.get("Text", e.get("text"))
                except (TypeError, ValueError):
                    continue
                if not isinstance(text, str) or not text.strip():
                    continue
                end = e.get("EndSec", e.get("end_time"))
                try:
                    end = float(end) if end is not None else None
                except (TypeError, ValueError):
                    end = None
                caps.append({"start": start, "end": end, "text": text.strip()})
        caps.sort(key=lambda x: x["start"])
        return caps

    def sub_info(self, course_id, sub_id):
        j = self.api_get(API + "/courseapi/v3/portal-home-setting/get-sub-info",
                         {"course_id": course_id, "sub_id": sub_id})
        data = j.get("data") or {}
        play = data.get("playurl")
        urls = []
        if isinstance(play, str):
            urls.append(play)
        elif isinstance(play, dict):
            urls = [v for v in play.values() if isinstance(v, str)]
        return {"playurls": urls, "raw": {k: data.get(k) for k in ("course_id", "sub_id", "title")}}


def clock(seconds):
    s = int(seconds)
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


def download_images(items, out_dir, shared_session):
    out_dir.mkdir(parents=True, exist_ok=True)
    ok_items = []

    def fetch(i, it):
        name = f"slide-{i:04d}-{int(it['sec'])}s.jpg"
        path = out_dir / name
        for attempt in range(3):
            try:
                r = shared_session.get(it["url"], timeout=30,
                                       headers={"Referer": BASE + "/"})
                if r.status_code == 200 and len(r.content) > 1024:
                    path.write_bytes(r.content)
                    return {"index": i, "file": str(path), "sec": it["sec"], "url": it["url"]}
                if r.status_code == 200:
                    return None  # 响应过小视为坏图
            except Exception:
                pass
            time.sleep(0.6 * (attempt + 1))
        return None

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(fetch, i, it) for i, it in enumerate(items, 1)]
        for f in futures:
            r = f.result()
            if r:
                ok_items.append(r)
    ok_items.sort(key=lambda x: x["sec"])
    return ok_items


def dedupe_final(downloaded):
    """相同内容（md5）只保留最后一次出现 → 每页 PPT 留最终版；按时间排序。"""
    last_by_hash = {}
    for d in downloaded:
        h = hashlib.md5(Path(d["file"]).read_bytes()).hexdigest()
        last_by_hash[h] = d
    final = sorted(last_by_hash.values(), key=lambda x: x["sec"])
    for i, d in enumerate(final, 1):
        newname = f"final-{i:03d}-{int(d['sec'])}s.jpg"
        target = Path(d["file"]).with_name(newname)
        Path(d["file"]).rename(target)
        d["file"] = str(target)
    return final


def cmd_login(a, zc):
    zc.login(force=a.fresh)
    u = zc.user()
    out({"ok": True, "user": u})


def cmd_courses(a, zc):
    if a.scope == "all":
        res = zc.search_all(keyword=a.keyword, teacher=a.teacher)
        items = res["items"]
        out({"ok": True, "scope": "all", "total": res["total"], "items": items,
             "hint": "全站结果按 course_id 聚合展示；用 lessons --course <课程名或id> 进入课次"})
    else:
        items = zc.my_courses(keyword=a.keyword)
        out({"ok": True, "scope": "mine", "count": len(items), "items": items})


def cmd_lessons(a, zc):
    c = zc.resolve_course(a.course, teacher=a.teacher)
    rows = zc.catalogue(c["id"])
    out({"ok": True, "course_id": c["id"], "course": c.get("course"), "count": len(rows), "lessons": rows,
         "hint": "lesson 按开课时间升序编号；status=6 且 playable=true 才有回放/字幕"})


def cmd_fetch(a, zc):
    c = zc.resolve_course(a.course, teacher=a.teacher)
    rows = zc.catalogue(c["id"])
    lesson = next((r for r in rows if r["lesson"] == a.lesson), None)
    if not lesson:
        raise RuntimeError(f"没有第 {a.lesson} 次课（共 {len(rows)} 次）")
    course_id, sub_id = c["id"], lesson["sub_id"]
    out_dir = Path(a.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    log("[zhiyun] 拉取字幕…")
    caps = zc.transcript(course_id, sub_id)
    chars = sum(len(x["text"]) for x in caps)
    with open(out_dir / "transcript.txt", "w", encoding="utf-8") as f:
        for x in caps:
            f.write(f"[{clock(x['start'])}] {x['text']}\n")
    (out_dir / "transcript.json").write_text(json.dumps(caps, ensure_ascii=False, indent=1), "utf-8")

    log("[zhiyun] 拉取 PPT 时间轴…")
    slides = zc.slides(course_id, sub_id)
    raw_dir = out_dir / "slides_raw"
    final_dir = out_dir / "slides_final"
    final = []
    if slides:
        downloaded = download_images(slides, raw_dir, zc.s)
        final = dedupe_final(downloaded) if downloaded else []
        if final:
            final_dir.mkdir(parents=True, exist_ok=True)
            for d in final:
                newp = final_dir / Path(d["file"]).name
                shutil.move(d["file"], newp)
                d["file"] = str(newp)
        shutil.rmtree(raw_dir, ignore_errors=True)

    log("[zhiyun] 读取回放信息…")
    try:
        info = zc.sub_info(course_id, sub_id)
    except Exception as e:
        info = {"playurls": [], "error": str(e)}
    audio_url = next((u for u in info.get("playurls", [])), None)

    if a.audio and audio_url:
        if shutil.which("ffmpeg"):
            log("[zhiyun] 下载回放音频（ffmpeg，可能较久）…")
            r = subprocess.run(["ffmpeg", "-y", "-i", audio_url, "-vn", "-c:a", "copy",
                                str(out_dir / "audio.m4a")], capture_output=True)
            if r.returncode != 0:
                log("[zhiyun] 音频下载失败：" + r.stderr.decode("utf-8", "ignore")[-300:])
        else:
            log("[zhiyun] 未安装 ffmpeg，跳过音频下载（URL 已记录在 manifest）")

    # 转写质量粗筛：给 agent 一个信号
    quality = "ok"
    if not caps:
        quality = "none"
    elif chars < 800:
        quality = "poor"
    manifest = {
        "ok": True, "course_id": course_id, "sub_id": sub_id,
        "course": c.get("course") or a.course, "lesson": lesson["lesson"],
        "title": lesson["title"], "teacher": lesson["teacher"], "date": lesson["start_at"],
        "slides_total": len(slides), "slides_final": final,
        "transcript": {"sentences": len(caps), "chars": chars, "quality": quality},
        "audio_url": audio_url, "out_dir": str(out_dir),
        "hint": "transcript.txt 为带时间戳的原始转写（仅供撰写讲义参考，不是成品）；"
                "slides_final 为去重后的最终版板书；讲义成稿后删除本目录中间文件"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    manifest["slides_final"] = [{"file": d["file"], "sec": d["sec"]} for d in final]
    out(manifest)


def main():
    zjuauth.force_utf8()
    p = argparse.ArgumentParser(description="智云课堂客户端")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("login"); sp.add_argument("--fresh", action="store_true"); sp.set_defaults(fn=cmd_login)
    sp = sub.add_parser("courses")
    sp.add_argument("--keyword", default=None); sp.add_argument("--teacher", default=None)
    sp.add_argument("--scope", default="mine", choices=["mine", "all"]); sp.set_defaults(fn=cmd_courses)
    sp = sub.add_parser("lessons")
    sp.add_argument("--course", required=True); sp.add_argument("--teacher", default=None)
    sp.set_defaults(fn=cmd_lessons)
    sp = sub.add_parser("fetch")
    sp.add_argument("--course", required=True); sp.add_argument("--lesson", type=int, required=True)
    sp.add_argument("--teacher", default=None)
    sp.add_argument("--out", required=True, help="素材输出目录（中间目录，成稿后删除）")
    sp.add_argument("--audio", action="store_true", help="同时下载回放音频（需 ffmpeg）")
    sp.set_defaults(fn=cmd_fetch)

    a = p.parse_args()
    try:
        acquire_lock("zhiyun")
    except zjuauth.LockBusy as e:
        out({"ok": False, "error": "LockBusy", "message": str(e)}, 5)
    try:
        zjuauth.load_credentials()
        zc = Zhiyun()
        a.fn(a, zc)
    except Exception as e:
        out({"ok": False, "error": type(e).__name__, "message": str(e)}, 1)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
