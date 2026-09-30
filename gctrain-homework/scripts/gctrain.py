# -*- coding: utf-8 -*-
"""
浙江大学工程训练中心 OA 系统（http://10.71.32.157/）API 客户端
用途：查询并完成每周的"课前预习"作业（选择/判断题）。

探测结论（2026-09）：
  * 登录：POST ajax/ajax_do_login.ashx  data: userId/password/act=1(学生)  → JSON {status, target_url}
  * 该站校验 Referer：每个页面都要求来自"上一页"的 Referer，否则 302 回登录页。
  * 课前预习列表：Tester/Prepare/TestList.aspx（WebForms 回传 + __VIEWSTATE/__EVENTVALIDATION）
  * 答题入口：Tester/Prepare/confirm_test.aspx?test_id&record_id&paper_type&paper_id&has_tested
              Tester/Prepare/exam.aspx?test_id&paper_type&recorder_id&paper_id
用法示例：
  python gctrain.py login                  # 验证登录
  python gctrain.py kinds                  # 列出工种下拉框
  python gctrain.py list                   # 预习作业列表（默认筛选）
  python gctrain.py list --all-kinds       # 遍历全部工种查作业
  python gctrain.py list --kind 69         # 只查某个工种（69=导论）
  python gctrain.py myclass                # 我的批次
  python gctrain.py records                # 预习答题记录
  python gctrain.py raw Tester/Prepare/TestList.aspx   # 调试任意页面

账号密码来源（优先级从高到低）：
  1. -u/--user、-p/--password 命令行参数
  2. 环境变量 GCTRAIN_USER / GCTRAIN_PASSWORD
  两者都未提供时脚本会报错退出。
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "http://10.71.32.157/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
USER = os.environ.get("GCTRAIN_USER", "")
PASSWORD = os.environ.get("GCTRAIN_PASSWORD", "")


class GcClient:
    """带会话与 Referer 链的客户端。每个页面的合法 Referer 见 REFERERS。"""

    # 目标路径(小写) -> 应携带的 Referer 路径
    REFERERS = {
        "tester/default.aspx": "Login.aspx",
        "tester/tester_menu.aspx": "Tester/Default.aspx",
        "mastertop.aspx": "Tester/Default.aspx",
        "tester/viewannouncement.aspx": "Tester/Tester_Menu.aspx",
        "tester/myclass.aspx": "Tester/Tester_Menu.aspx",
        "tester/prepare/testlist.aspx": "Tester/Tester_Menu.aspx",
        "tester/prepare/testrecorderlist.aspx": "Tester/Tester_Menu.aspx",
        "tester/mistakehint.aspx": "Tester/Prepare/TestRecorderList.aspx",
        "tester/prepare/confirm_test.aspx": "Tester/Prepare/TestList.aspx",
        "tester/prepare/exam.aspx": "Tester/Prepare/TestList.aspx",
    }

    def __init__(self, user=USER, password=PASSWORD, verbose=False):
        self.user, self.password, self.verbose = user, password, verbose
        self.s = requests.Session()
        self.s.trust_env = False  # 内网地址，绕过系统代理
        self.s.headers.update({"User-Agent": UA})

    # ---------- 基础请求 ----------
    def _referer_for(self, path):
        p = path.lower().split("?")[0]
        return urljoin(BASE, self.REFERERS.get(p, "")) or None

    def get(self, path, referer=None, **kw):
        ref = referer or self._referer_for(path)
        headers = {"Referer": ref} if ref else {}
        r = self.s.get(urljoin(BASE, path), headers=headers, timeout=15, **kw)
        r.encoding = "utf-8"
        self._log(r)
        return r

    def post(self, path, referer=None, headers=None, **kw):
        ref = referer or self._referer_for(path) or urljoin(BASE, path)
        merged = {"Referer": ref, "X-Requested-With": "XMLHttpRequest"}
        if headers:
            merged.update(headers)
        r = self.s.post(urljoin(BASE, path), headers=merged, timeout=15, **kw)
        r.encoding = "utf-8"
        self._log(r)
        return r

    def _log(self, r):
        if self.verbose:
            print(f"[{r.status_code}] {r.url}  ({len(r.text)} bytes)", file=sys.stderr)

    @staticmethod
    def _check_login(r):
        """302 或出现登录跳转页说明会话失效。"""
        if r.status_code == 302 or "跳转到登陆页面" in r.text:
            sys.exit("会话已失效（302 回登录页），请重新运行（脚本会自动重新登录）。")

    # ---------- 登录 ----------
    def login(self):
        # 先访问一次根路径，让服务器种下 AspxAutoDetectCookieSupport + 初始 SessionId
        # （注意：不要直接访问 /?AspxAutoDetectCookieSupport=1，会自重定向死循环）
        r = self.get("/", referer=None)
        if r.status_code != 200:
            self.get(r.headers.get("Location", "/"), referer=None)
        r = self.post("ajax/ajax_do_login.ashx",
                      data={"userId": self.user, "password": self.password, "act": "1"},
                      referer=urljoin(BASE, "Login.aspx"))
        j = r.json()
        if j.get("status") != 1:
            sys.exit(f"登录失败：{j}")
        return j

    # ---------- 通用 WebForms 回传 ----------
    @staticmethod
    def _hidden_fields(html):
        fields = {}
        for name, value in re.findall(
                r'<input type="hidden" name="([^"]+)"[^>]*value="([^"]*)"', html):
            fields[name] = value
        return fields

    @staticmethod
    def _soup(html):
        return BeautifulSoup(html, "html.parser")

    # ---------- 各页面 ----------
    def kinds(self):
        """工种下拉框 {value: 名称}"""
        r = self.get("Tester/Prepare/TestList.aspx")
        self._check_login(r)
        out = {}
        for opt in self._soup(r.text).select("select option"):
            out[opt.get("value", "")] = opt.get_text(strip=True)
        return out

    def prepare_list(self, kind=""):
        """查询课前预习作业列表。kind='' 用默认筛选。返回(总条数, 行列表)"""
        r = self.get("Tester/Prepare/TestList.aspx")
        self._check_login(r)
        data = self._hidden_fields(r.text)
        data.update({"DropDownList_TrainKind": kind, "btnSearch": "查 询"})
        r2 = self.post("Tester/Prepare/TestList.aspx", data=data,
                       referer=urljoin(BASE, "Tester/Prepare/TestList.aspx"))
        self._check_login(r2)
        return self._parse_prepare_list(r2.text)

    def _parse_prepare_list(self, html):
        soup = self._soup(html)
        total = None
        m = re.search(r"总共\s*</span>\s*(\d+)|总共.*?(\d+)\s*条记录", soup.get_text(), re.S)
        # 页面以分页控件显示 "总共 N 条记录"，直接从文本抓
        t = re.sub(r"\s+", " ", soup.get_text())
        m = re.search(r"总共\s*(\d+)\s*条记录", t)
        if m:
            total = int(m.group(1))
        rows = []
        for tr in soup.select("tr"):
            onclick = tr.get("onclick", "") or (tr.select_one("[onclick]") or {}).get("onclick", "")
            cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
            if not cells:
                continue
            args = re.search(r"showConfirmTest\(([^)]*)\)", tr.decode() if hasattr(tr, "decode") else str(tr))
            row = {"cells": cells}
            if args:
                row["args"] = [a.strip().strip("'") for a in args.group(1).split(",")]
            # 行内可能有按钮而非 tr onclick
            for el in tr.select("[onclick]"):
                a2 = re.search(r"showConfirmTest\(([^)]*)\)", el.get("onclick", ""))
                if a2:
                    row["args"] = [a.strip().strip("'") for a in a2.group(1).split(",")]
            rows.append(row)
        rows = [r for r in rows if r["cells"] and not set(r["cells"][0]) <= {"编号"}]
        return total, rows

    def myclass(self):
        r = self.get("Tester/MyClass.aspx")
        self._check_login(r)
        return r.text

    def records(self):
        """课前预习-答题记录"""
        r = self.get("Tester/Prepare/TestRecorderList.aspx")
        self._check_login(r)
        return r.text

    def raw(self, path):
        r = self.get(path)
        self._check_login(r)
        return r.text

    def mistake(self, test_id, recorder_id, test_type="prepare"):
        """查看某次答题的错题页"""
        r = self.get(f"Tester/MistakeHint.aspx?testID={test_id}&recorderID={recorder_id}&testType={test_type}")
        self._check_login(r)
        return r.text

    def mistake_api(self, test_id, recorder_id, test_type="prepare"):
        """错题数据接口（含每题的正确答案与所选答案）"""
        r = self.post("ajax/ajax_api.ashx?act=MistakeHint",
                      json={"testID": str(test_id), "recorderID": str(recorder_id), "testType": test_type},
                      referer=urljoin(BASE, "Tester/MistakeHint.aspx"))
        return r.json()

    # ---------- 考试核心流程（逆向自 Tester/style/js/exam.js） ----------
    ASHX = {
        "prepare": "ajax/ajax_do_prepare.ashx",
        "report": "ajax/ajax_do_report.ashx",
        "stage": "ajax/ajax_do_stage_test.ashx",
        "test": "ajax/ajax_do_test.ashx",
    }

    def exam_start(self, test_id, paper_type, recorder_id, paper_id="0", kind="prepare"):
        """act=st：开始考试/作业，返回题目 JSON。
        注意：这会在服务端开始计时（live_time 倒计时），调用前请确认要开考。"""
        url = self.ASHX[kind]
        r = self.get(f"{url}?test_id={test_id}&paper_type={paper_type}&recorder_id={recorder_id}"
                     f"&paper_id={paper_id}&act=st", referer=urljoin(BASE, "Tester/Prepare/exam.aspx"))
        self._check_login(r)
        return r.json()

    @staticmethod
    def _jquery_param(params):
        """按 jQuery $.ajax 的方式序列化含数组的参数（服务器按此格式解析）。
        {answers:[{subject_id:1,...}]} -> answers[0][subject_id]=1&..."""
        out = []

        def enc(s):
            from urllib.parse import quote
            return quote(str(s), safe="")

        def walk(prefix, val):
            if isinstance(val, dict):
                for k, v in val.items():
                    walk(f"{prefix}[{enc(k)}]" if prefix else enc(k), v)
            elif isinstance(val, list):
                for i, v in enumerate(val):
                    walk(f"{prefix}[{i}]", v)
            else:
                out.append(f"{prefix}={enc(val)}")

        walk("", params)
        return "&".join(out)

    def exam_submit(self, test_id, recorder_id, paper_type, answers, used_time=60,
                    submit_type=1, kind="prepare"):
        """act=jj：交卷。answers 形如 [{"subject_id":531,"answer":"1","subject_type":2}, ...]
        judge: "1"=对 "0"=错；single: "A"~"D"；multi: "AC" 等。
        used_time 单位秒；submit_type 1=手动提交。"""
        params = {
            "test_id": test_id, "recorder_id": recorder_id, "paper_type": paper_type,
            "used_time": used_time, "answers": answers, "answers_cnt": len(answers),
            "submit_type": submit_type, "act": "jj",
        }
        body = self._jquery_param(params)
        r = self.post(self.ASHX[kind], data=body,
                      headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                      referer=urljoin(BASE, "Tester/Prepare/exam.aspx"))
        return r.json()

    # ---------- 结构化解析 ----------
    def records_parsed(self, kind="prepare"):
        """答题记录 → [{record_id, name, test_id, recorder_id, test_type, submitted, ...}]"""
        page = "Tester/Prepare/TestRecorderList.aspx" if kind == "prepare" else "Tester/Report/TestRecorderList.aspx"
        r = self.get(page)
        self._check_login(r)
        html = r.text
        rows = []
        # 每条记录的查看错题链接
        for m in re.finditer(r"MistakeHint\.aspx\?testID=(\d+)&recorderID=(\d+)&testType=(\w+)", html):
            test_id, recorder_id, test_type = m.groups()
            seg = html[max(0, m.start() - 3000):m.start()]
            name = re.findall(r"<td[^>]*>\s*([^<]*(?:作业|预习|考试|报告)[^<]*)\s*</td>", seg)
            rows.append({
                "test_id": test_id, "recorder_id": recorder_id, "test_type": test_type,
                "name": name[-1].strip() if name else "?",
            })
        return rows

    def save_mistake_to_bank(self, test_id, recorder_id, test_type="prepare", bank_path="题库.json"):
        """把一次答题的完整题目+正确答案并入本地题库（按 subjectId 去重）"""
        data = self.mistake_api(test_id, recorder_id, test_type)["Data"]
        bank = {"tests": [], "questions": {}}
        try:
            bank = json.load(open(bank_path, encoding="utf-8"))
        except Exception:
            pass
        entry = {"testName": data.get("testName"), "creatorTime": data.get("creatorTime"),
                 "testID": test_id, "recorderID": recorder_id}
        if entry not in bank.setdefault("tests", []):
            bank["tests"].append(entry)
        qs = bank.setdefault("questions", {})
        for section in ("judgeInfo", "singleInfo", "multiInfo"):
            for s in data.get(section, {}).get("subjects", []):
                qs[str(s["subjectId"])] = {
                    "type": {"judgeInfo": "判断", "singleInfo": "单选", "multiInfo": "多选"}[section],
                    "question": s.get("subjectName"),
                    "options": [o["selectOption"] for o in s.get("options", [])] or None,
                    "answer": s.get("answer"),
                }
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return bank


# ---------- 命令行 ----------
def show_table(total, rows, kindmap=None):
    print(f"共 {total if total is not None else '?'} 条记录")
    for i, row in enumerate(rows, 1):
        cells = row["cells"]
        print(f"--- 行{i}: {' | '.join(cells)}")
        if "args" in row:
            print(f"    入口参数: {row['args']}")


def main():
    ap = argparse.ArgumentParser(description="工程训练 OA 预习作业客户端")
    ap.add_argument("-u", "--user", default=USER)
    ap.add_argument("-p", "--password", default=PASSWORD)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("kinds")
    lp = sub.add_parser("list")
    lp.add_argument("--kind", default="", help="工种编号（kinds 命令可查）")
    lp.add_argument("--all-kinds", action="store_true", help="遍历所有工种")
    sub.add_parser("myclass")
    sub.add_parser("records")
    rp = sub.add_parser("raw")
    rp.add_argument("path")
    mp = sub.add_parser("mistake")
    mp.add_argument("test_id")
    mp.add_argument("recorder_id")
    mp.add_argument("--type", default="prepare", dest="test_type")
    sp = sub.add_parser("start", help="开考并导出题目到 JSON（服务端会开始计时！）")
    sp.add_argument("test_id")
    sp.add_argument("paper_type")
    sp.add_argument("recorder_id")
    sp.add_argument("--paper-id", default="0")
    sp.add_argument("--kind", default="prepare", choices=list(GcClient.ASHX))
    sp.add_argument("--out", default="")
    jp = sub.add_parser("submit", help="交卷（需 --yes 才真正提交）")
    jp.add_argument("test_id")
    jp.add_argument("recorder_id")
    jp.add_argument("paper_type")
    jp.add_argument("answers", help="答案 JSON 文件：[{subject_id, answer, subject_type}]")
    jp.add_argument("--kind", default="prepare", choices=list(GcClient.ASHX))
    jp.add_argument("--used-time", type=int, default=60)
    jp.add_argument("--yes", action="store_true")
    bp = sub.add_parser("bank", help="把一次答题的题目+正确答案存入题库.json")
    bp.add_argument("test_id")
    bp.add_argument("recorder_id")
    bp.add_argument("--type", default="prepare", dest="test_type")
    args = ap.parse_args()

    if not args.user or not args.password:
        ap.error("请通过环境变量 GCTRAIN_USER/GCTRAIN_PASSWORD 或 -u/-p 提供账号密码")

    c = GcClient(args.user, args.password, verbose=args.verbose)
    c.login()
    print(f"# 已登录：{args.user}")

    if args.cmd == "login":
        print("OK")
    elif args.cmd == "kinds":
        for v, name in c.kinds().items():
            print(f"{v or '(空)'}\t{name}")
    elif args.cmd == "list":
        if args.all_kinds:
            kinds = c.kinds()
            for v, name in kinds.items():
                if not v:
                    continue
                total, rows = c.prepare_list(v)
                if rows:
                    print(f"== 工种 {v} {name} ==")
                    show_table(total, rows)
                else:
                    print(f"== 工种 {v} {name}: 0 条 ==", file=sys.stderr)
        else:
            total, rows = c.prepare_list(args.kind)
            show_table(total, rows)
    elif args.cmd == "myclass":
        print(c.myclass())
    elif args.cmd == "records":
        for row in c.records_parsed():
            print(f"{row['name']}\ttest_id={row['test_id']}\trecorder_id={row['recorder_id']}\ttype={row['test_type']}")
    elif args.cmd == "start":
        j = c.exam_start(args.test_id, args.paper_type, args.recorder_id, args.paper_id, args.kind)
        out = args.out or f"exam_{args.test_id}.json"
        json.dump(j, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"已保存到 {out}，内容摘要：")
        d = j.get("data", [{}])[0] if j.get("status") else {}
        for sec in ("judge_list", "single_list", "multi_list"):
            lst = d.get(sec, [])
            print(f"  {sec}: {len(lst)} 题")
        if not d and j.get("message"):
            print("  message:", j.get("message"))
    elif args.cmd == "submit":
        answers = json.load(open(args.answers, encoding="utf-8"))
        preview = [(a["subject_id"], a["answer"]) for a in answers]
        print("将要提交的答案：", preview)
        if not args.yes:
            print("（预演模式，未提交。确认无误请加 --yes 重新运行）")
        else:
            print(c.exam_submit(args.test_id, args.recorder_id, args.paper_type, answers,
                                used_time=args.used_time, kind=args.kind))
    elif args.cmd == "bank":
        bank = c.save_mistake_to_bank(args.test_id, args.recorder_id, args.test_type)
        print(f"题库现有 {len(bank['tests'])} 次记录 / {len(bank['questions'])} 题（已写入 题库.json）")
    elif args.cmd == "raw":
        print(c.raw(args.path))
    elif args.cmd == "mistake":
        print(c.mistake(args.test_id, args.recorder_id, args.test_type))


if __name__ == "__main__":
    main()
