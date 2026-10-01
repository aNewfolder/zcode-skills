#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浙大文艺生活·琴房自动预约脚本
================================
通过浙大统一身份认证(CAS)登录 myarts.zju.edu.cn，在每天放票时刻
(默认 08:00，提前 advance_days 天)自动提交预约。

默认目标:每周一、周二 08:00-10:00(连续两小时)，紫金港校区琴房。
修改 config.json 即可调整目标，无需改代码。

用法:
  python3 piano_booking.py                # 睡到放票时刻自动预约(给 systemd timer 用;
                                          #   启动时已过放票点则直接尝试)
  python3 piano_booking.py --run-now      # 立即尝试预约(手动触发)
  python3 piano_booking.py --status       # 查看账号现有预约和接下来可约的目标日期
  python3 piano_booking.py --dry-run      # 只走流程不真正提交(可加 --run-now)

原理(2026-09 抓包验证):
  1. CAS 登录: GET /cas/login 取 execution -> 公钥 RSA 加密(反转密码) -> POST 表单,
     302 链 zjuam -> itservice cas-proxy -> myarts sso callback, 得到会话 cookie。
  2. 业务接口(均带会话 cookie):
     - GET  /{APP}/v1/form/advancedSearch.json        查琴房/规则表单
     - POST /{APP}/v1/functions/mobile_booking_query_service/invoke.json
     - POST /{APP}/v1/functions/booking_commit_service/invoke.json  action=submit 预约
"""

import argparse
import json
import random
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "piano_booking.log"

# ---------------- 常量(抓包所得,勿随意改动) ----------------
CAS_LOGIN_URL = (
    "https://zjuam.zju.edu.cn/cas/login?service="
    "https%3A%2F%2Fitservice.zju.edu.cn%2Fzju-lowcode-api%2Fauth%2Fcas-proxy"
    "%2Fcallback%3FtargetService%3Dhttps%253A%252F%252Fmyarts.zju.edu.cn"
    "%252Fservice%252Fapi%252Fsso%252Fcas%252Fcallback%253FtenantId%253Ddefault"
    "%2526redirectUri%253Dhttps%25253A%25252F%25252Fmyarts.zju.edu.cn"
    "%25252Fview%25252FAPP_60DF05B95A5C448391B1%25252Fworkbench%25252Fmobile-booking"
)
CAS_PUBKEY_URL = "https://zjuam.zju.edu.cn/cas/v2/getPubKey"
SERVICE_BASE = "https://myarts.zju.edu.cn/service"
APP_TYPE = "APP_60DF05B95A5C448391B1"
FORM_CAMPUS = "FORM_FAAB321A99974383A8BEE622FD70199E"
FORM_PIANO_ROOM = "FORM_F34FD418D5F3466CB1E60DB9FFEAE437"
FORM_BOOKING_RULE = "FORM_3CDE21F9C10143389BA913A3C7A87AA6"
FORM_BOOKING_ORDER = "FORM_1176DC665A15473791DAE69C4CA92BFF"
FORM_ACCESS_PERM = "FORM_6D35EDA2CC30497CAD44E679F733D4BD"
FN_QUERY = "mobile_booking_query_service"
FN_COMMIT = "booking_commit_service"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")
WEEKDAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四",
                 5: "周五", 6: "周六", 7: "周日"}
MSG_NOT_OPEN = ("不可预约", "提前", "开放时间", "未到", "暂未")
MSG_CONFLICT = ("已被预约", "冲突", "已满", "有效预约", "占用")


# ---------------- 日志 ----------------
def _rotate_log():
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2 * 1024 * 1024:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.old"))
    except OSError:
        pass


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except OSError:
        pass  # pythonw/无控制台环境下 stdout 不可用
    try:
        _rotate_log()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------- 配置 ----------------
def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for key in ("username", "password"):
        if not cfg.get(key):
            raise SystemExit(f"config.json 缺少 {key}")
    cfg.setdefault("timezone", "Asia/Shanghai")
    cfg.setdefault("open_time", "08:00")
    cfg.setdefault("login_lead_seconds", 25)
    cfg.setdefault("advance_days", 3)
    cfg.setdefault("campus", "紫金港校区")
    cfg.setdefault("room_preference", [
        "西三B212-2", "西三B212-3", "西三B212-1", "西三B212-4",
        "西三B212-5", "西三B212-6", "西三B214", "西三B210-2",
    ])
    cfg.setdefault("targets", [
        {"weekday": 1, "start": "08:00", "end": "10:00"},
        {"weekday": 2, "start": "08:00", "end": "10:00"},
    ])
    cfg.setdefault("retry_seconds", 120)
    cfg.setdefault("http_timeout", 20)
    cfg.setdefault("proxy", "")  # 如 "http://127.0.0.1:8888" (EasyConnect 容器代理)
    return cfg


def get_tz(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        log(f"警告: 无法加载时区 {name}, 使用系统本地时间")
        return None


def now_in(cfg) -> datetime:
    tz = get_tz(cfg["timezone"])
    return datetime.now(tz) if tz else datetime.now()


# ---------------- CAS 登录 ----------------
def cas_login(username: str, password: str, timeout: int,
              proxy: str = "") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    t0 = time.time()

    r = s.get(CAS_LOGIN_URL, timeout=timeout)
    m = re.search(r'name="execution" value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError(f"CAS 登录页解析失败 (HTTP {r.status_code})")
    execution = m.group(1)

    pub = s.get(CAS_PUBKEY_URL, timeout=timeout).json()
    modulus, exponent = int(pub["modulus"], 16), int(pub["exponent"], 16)
    # 与页面 login.js 一致: 密码反转后按 little-endian 大整数做 RSA
    enc = pow(int.from_bytes(password[::-1].encode("utf-8"), "little"),
              exponent, modulus)
    enc_pwd = format(enc, "x")

    resp = s.post(CAS_LOGIN_URL,
                  data={"username": username, "password": enc_pwd,
                        "execution": execution, "_eventId": "submit"},
                  allow_redirects=True, timeout=timeout)
    if "sso_login=success" not in resp.url:
        raise RuntimeError(f"CAS 登录未成功, 最终跳转: {resp.url[:120]} "
                           f"(检查账号密码/是否需要验证码)")
    log(f"CAS 登录成功, 耗时 {time.time()-t0:.1f}s")
    return s


# ---------------- 业务接口封装 ----------------
def api_get(s, path, params=None, timeout=20):
    r = s.get(SERVICE_BASE + path, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def api_invoke(s, fn, payload, timeout=20):
    r = s.post(f"{SERVICE_BASE}/{APP_TYPE}/v1/functions/{fn}/invoke.json",
               json={"input": payload}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def search_form(s, form_uuid, timeout=20):
    j = api_get(s, f"/{APP_TYPE}/v1/form/advancedSearch.json",
                params={"appType": APP_TYPE, "formUuid": form_uuid,
                        "currentPage": 1, "pageSize": 100,
                        "conditionType": "AND"}, timeout=timeout)
    res = j.get("result") or {}
    return res.get("data") or []


def val(field):
    """表单字段有的是 {label,value} 结构"""
    if isinstance(field, dict):
        return field.get("value")
    return field


def campus_map(s, timeout=20):
    campuses = search_form(s, FORM_CAMPUS, timeout)
    return {c["formInstId"]: c.get("campus_name") for c in campuses}


def load_rooms(s, cfg, cmap, timeout=20):
    """候选琴房: active、免审批、目标校区, 按配置优先级排序。"""
    rooms = search_form(s, FORM_PIANO_ROOM, timeout)
    picked = []
    for r in rooms:
        if val(r.get("status")) != "active":
            continue
        if val(r.get("requires_approval")) == "yes":
            continue  # 普通琴房才免审批即时生效
        if cmap.get(r.get("campus_id")) != cfg["campus"]:
            continue
        picked.append(r)

    def sort_key(r):
        name = r.get("room_name") or ""
        for i, p in enumerate(cfg["room_preference"]):
            if p in name:
                return (0, i, name)
        return (1, 99, name)

    picked.sort(key=sort_key)
    return picked


def load_advance_rule(s, timeout=20):
    """读取已发布的普通预约规则, 返回 (advance_days, open_time)。"""
    try:
        rules = search_form(s, FORM_BOOKING_RULE, timeout)
        for rt in rules:
            if val(rt.get("rule_type")) == "normal" \
                    and val(rt.get("status")) == "published" \
                    and val(rt.get("apply_scope")) in ("all", None):
                days = int(rt.get("advance_days") or 3)
                open_t = rt.get("advance_open_time") or "08:00"
                return days, open_t
    except Exception as e:
        log(f"读取预约规则失败({e}), 使用配置默认值")
    return None, None


def list_my_orders(s, timeout=20):
    j = api_invoke(s, FN_QUERY, {"action": "list_my_orders",
                                 "currentPage": 1, "pageSize": 50,
                                 "statusTab": "all"}, timeout)
    data = j.get("data") or {}
    return (data.get("result") or {}).get("items") or []


def submit_booking(s, room, campus_name, date_str, start, end, timeout=20):
    payload = {
        "action": "submit",
        "requestId": f"booking-{int(time.time()*1000)}-{random.randrange(10**8)}",
        "roomId": room["formInstId"],
        "campusName": campus_name,
        "date": date_str,
        "startTime": start,
        "endTime": end,
        "price": 0,
    }
    j = api_invoke(s, FN_COMMIT, payload, timeout)
    code = j.get("code")
    msg = j.get("message") or ""
    order = ((j.get("data") or {}).get("result") or {}).get("order") or {}
    return code, msg, order


def form_update(s, form_uuid, instance_id, data, timeout=20):
    """更新一条表单实例(页面 SDK 的 form.update 等价实现)。

    注意: 平台对普通学生禁用表单直写(返回"无权限编辑数据"), 订单/权限
    等业务数据须走下方官方后台服务, 此函数仅保留备用。
    """
    r = s.post(f"{SERVICE_BASE}/{APP_TYPE}/v1/form/updateFormData.json",
               json={"formUuid": form_uuid, "formInstId": instance_id,
                     "updateFormDataJson": json.dumps(data)}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def connector_invoke(s, name, api, payload, timeout=25):
    r = s.post(f"{SERVICE_BASE}/{APP_TYPE}/v1/connectors/actions/invoke",
               json={"connector": name, "api": api, **payload}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def cancel_order(s, order, reason="", timeout=20, ignore_door_errors=False):
    """取消一条预约订单。返回 (ok: bool, message: str)。

    序列与官方页面一致: 门禁撤销(如有) -> 订单置 cancelled(后台服务) ->
    释放时段 -> 释放锁 -> 撤销门禁权限记录(后台服务)。
    ignore_door_errors=True 时门禁撤销失败不中止订单取消——金桥门禁
    deleteReserve 当前实测一律返回「删除失败」(官方 App 即因此报错),
    平台侧权限记录仍会正常撤销, 不产生爽约风险。
    """
    iid = order["instanceId"]
    status = str(val(order.get("order_status")) or "")
    if not reason:
        reason = "用户主动撤销申请" if status == "pending_approval" else "用户主动取消"

    # 门禁权限(如有): 先撤门禁, 失败默认中止(与官方页面一致)
    perm = None
    try:
        ctx = api_invoke(s, FN_QUERY, {"action": "get_context",
                                       "dates": [datetime.now().date().isoformat()]},
                         timeout)
        perms = ((ctx.get("data") or {}).get("result") or {}).get(
            "accessPermissions") or []
        perm = next((p for p in perms if p.get("order_id") == iid), None)
    except Exception as e:
        log(f"取消: 获取门禁权限失败({e}), 继续主流程")
    reserve_id = order.get("entrance_reserve_id") or \
        (perm or {}).get("entrance_reserve_id")
    door_note = ""
    if reserve_id:
        r = connector_invoke(s, "entrance", "deleteReserve",
                             {"query": {"reserveId": str(reserve_id)}},
                             timeout)
        # 连接器响应有两层: r.data 为连接器信封, r.data.data 才是厂商结果
        d = r.get("data") if isinstance(r, dict) else None
        inner = d.get("data") if isinstance(d, dict) else None
        ok = next((lvl.get("success") for lvl in (inner, d, r)
                   if isinstance(lvl, dict) and "success" in lvl), None)
        if ok is not False:
            pass  # 明确成功或结果不明确(厂商无返回), 继续
        else:
            msg = ((inner or d or {}).get("message")) or "门禁预约删除失败"
            if any(k in str(msg) for k in
                   ("已删除", "已取消", "已结束", "已过期", "不存在")):
                pass  # 官方页面将此类消息视为已撤销
            elif not ignore_door_errors:
                return False, f"门禁预约取消失败, 订单未取消: {msg}"
            else:
                log(f"取消: 门禁预约 {reserve_id} 撤销失败({msg}), 按设置继续取消订单")
                door_note = "(门禁服务商撤销失败, 订单已正常取消)"

    # 订单置为 cancelled(官方页面走后台服务 booking_admin_action_service;
    # 平台表单直写对普通学生一律 403"无权限编辑数据")
    ap = val(order.get("approval_status"))
    j = api_invoke(s, "booking_admin_action_service",
                   {"action": "save_booking_order_status", "instanceId": iid,
                    "data": {"order_status": "cancelled",
                             "approval_status":
                                 "cancelled" if ap == "pending" else (ap or "none"),
                             "cancel_reason": reason}}, timeout)
    rec = {}
    if isinstance(j, dict):
        d = j.get("data") or {}
        rec = (d.get("record") or (d.get("result") or {}).get("record")) or {}
    if not rec:
        msg = j.get("message") if isinstance(j, dict) else str(j)
        return False, f"订单状态更新失败: {msg}"

    # 释放时段与锁
    api_invoke(s, FN_QUERY, {"action": "release_order_slots", "orderId": iid},
               timeout)
    try:
        api_invoke(s, FN_COMMIT, {"action": "release_order_locks",
                                  "orderId": iid, "reason": reason}, timeout)
    except Exception as e:
        log(f"取消: 时段锁即时释放失败(将由后续提交自动回收): {e}")

    # 撤销门禁权限记录(官方走 attendance_admin_service; 不接受额外字段)
    if perm and perm.get("instanceId"):
        try:
            api_invoke(s, "attendance_admin_service",
                       {"action": "save_access_permission",
                        "instanceId": perm["instanceId"],
                        "data": {"permission_status": "revoked",
                                 "revoked_at": datetime.now(timezone.utc).strftime(
                                     "%Y-%m-%dT%H:%M:%S.000Z")}}, timeout)
        except Exception as e:
            log(f"取消: 门禁权限记录撤销失败(不影响订单): {e}")
    return True, "取消成功" + door_note


# ---------------- 目标日期计算 ----------------
def compute_targets(cfg, advance_days=None, open_time=None):
    """返回 [(use_date_str, start, end, days_ahead)]，已过滤未放票/已开场的。

    支持三种计划条目:
      {"date": "YYYY-MM-DD", "start", "end"}  指定日期预约(替代当天每周计划)
      {"date": "YYYY-MM-DD", "skip": true}    该日期不自动预约
      {"weekday": 1-7, "start", "end"}        每周重复
    """
    now = now_in(cfg)
    days = advance_days if advance_days is not None else cfg["advance_days"]
    skip_dates = {str(t["date"]) for t in cfg["targets"]
                  if t.get("date") and t.get("skip")}
    dated_dates = {str(t["date"]) for t in cfg["targets"]
                   if t.get("date") and not t.get("skip")}
    results, seen = [], set()

    def push(use_date, st, et, delta):
        key = (use_date, st, et)
        if key not in seen and use_date not in skip_dates:
            seen.add(key)
            results.append((use_date, st, et, delta))

    for t in cfg["targets"]:
        st, et = t.get("start"), t.get("end")
        if t.get("date"):
            if t.get("skip"):
                continue  # 跳过标记本身不产生预约
            try:
                use_d = datetime.fromisoformat(str(t["date"])).date()
            except ValueError:
                continue
            delta = (use_d - now.date()).days
            if delta < 0:
                continue
            if delta > days:
                continue  # 还没放票, 等进入窗口后再约
            if delta == 0 and st:
                h, m = map(int, st.split(":"))
                slot_start = now.replace(hour=h, minute=m, second=0,
                                         microsecond=0)
                if now > slot_start - timedelta(minutes=30):
                    continue
            push(use_d.isoformat(), st, et, delta)
        else:
            wd = int(t["weekday"])
            delta = (wd - now.isoweekday()) % 7  # 0=今天
            use_date = (now + timedelta(days=delta)).date().isoformat()
            if use_date in dated_dates:
                continue  # 当天被单次计划替代
            if delta > days:
                continue  # 还没放票
            if delta == 0:
                # 目标时段马上就开始了(提前不足30分钟)则放弃
                h, m = map(int, st.split(":"))
                slot_start = now.replace(hour=h, minute=m, second=0,
                                         microsecond=0)
                if now > slot_start - timedelta(minutes=30):
                    continue
            push(use_date, st, et, delta)
    results.sort()
    return results


def active_booked_keys(orders):
    """已有有效订单的 (日期, 起, 止) 集合。"""
    keys = set()
    for o in orders:
        d = (o.get("use_date") or "")[:10]
        status = str(val(o.get("order_status")) or "")
        if status in ("cancelled", "refunded", "rejected", "closed"):
            continue
        keys.add((d, o.get("start_time"), o.get("end_time")))
    return keys


# ---------------- 核心流程 ----------------
def book_run(cfg, dry_run=False, session=None):
    now = now_in(cfg)
    log(f"===== 开始预约流程 (dry_run={dry_run}, now={now.strftime('%F %T')}) =====")

    s = session or cas_login(cfg["username"], cfg["password"],
                             cfg["http_timeout"], cfg["proxy"])
    cmap = campus_map(s, cfg["http_timeout"])

    adv_days, adv_open = load_advance_rule(s, cfg["http_timeout"])
    if adv_days is None:
        adv_days, adv_open = cfg["advance_days"], cfg["open_time"]
    log(f"预约规则: 提前 {adv_days} 天, 每日 {adv_open} 放票")

    targets = compute_targets(cfg, adv_days)
    if not targets:
        log("今天没有待预约的目标日期(均未放票或已错过), 结束")
        return

    rooms = load_rooms(s, cfg, cmap, cfg["http_timeout"])
    log(f"候选琴房 {len(rooms)} 间: " +
        ", ".join(r.get("room_name") or "?" for r in rooms))
    if not rooms:
        log("没有候选琴房, 结束")
        return

    booked = active_booked_keys(list_my_orders(s, cfg["http_timeout"]))
    pending = []
    for date_str, st, et, delta in targets:
        wd = datetime.fromisoformat(date_str).isoweekday()
        desc = f"{date_str}({WEEKDAY_NAMES[wd]}) {st}-{et}, {delta} 天后"
        if (date_str, st, et) in booked:
            log(f"跳过 {desc}: 已有订单")
        else:
            log(f"待预约: {desc}")
            pending.append((date_str, st, et))

    if not pending:
        log("目标时段均已有订单, 无需操作")
        return

    deadline = time.time() + cfg["retry_seconds"]
    started = time.time()
    attempt = 0
    next_try = {}  # 全部琴房冲突的日期 -> 最早可再次尝试的时间(冷却15s,避免刷接口)
    fail_kind = {}  # 未完成目标的最后失败类别: not_open/conflict/other
    while pending and time.time() < deadline:
        attempt += 1
        for item in list(pending):
            date_str, st, et = item
            if time.time() < next_try.get((date_str, st, et), 0):
                continue
            conflict_count = 0
            for room in rooms:
                if dry_run:
                    log(f"[dry-run] 将提交: {date_str} {st}-{et} "
                        f"{room.get('room_name')}")
                    pending.remove(item)
                    break
                code, msg, order = submit_booking(
                    s, room, cmap.get(room.get("campus_id")) or cfg["campus"],
                    date_str, st, et, cfg["http_timeout"])
                if code == 200 and order:
                    log(f"预约成功! {date_str} {st}-{et} "
                        f"{order.get('room_name') or room.get('room_name')} "
                        f"订单号 {order.get('order_no')}")
                    pending.remove(item)
                    break
                if any(k in msg for k in MSG_NOT_OPEN):
                    fail_kind[item] = "not_open"
                    log(f"  {date_str} 尚未放票({msg}), 稍后重试")
                    break  # 换房间没用, 等下一轮
                if any(k in msg for k in MSG_CONFLICT):
                    conflict_count += 1
                    log(f"  {room.get('room_name')} {date_str} {st} 冲突: {msg}")
                    continue  # 试下一间
                fail_kind[item] = "other"
                log(f"  {room.get('room_name')} 提交失败(code={code}): {msg}")
                if code == 401 or "登录" in msg:
                    log("会话失效, 重新登录")
                    s = cas_login(cfg["username"], cfg["password"],
                                  cfg["http_timeout"], cfg["proxy"])
                    break
            else:
                if conflict_count == len(rooms):
                    # 所有琴房都被占: 冷却 15s 后再试(可能有人取消锁定)
                    fail_kind[item] = "conflict"
                    log(f"  {date_str} {st}-{et} 没有空余琴房"
                        f"(全部 {len(rooms)} 间已被约满), 15s 后再试")
                    next_try[(date_str, st, et)] = time.time() + 15
        if not pending or dry_run:
            break
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        gap = 2.0 + random.random() * 0.5 if time.time() - started < 30 \
            else 5.0 + random.random() * 1.0
        time.sleep(min(gap, remaining))

    REASONS = {
        "conflict": "没有空余琴房(全部琴房该时段已被约满)",
        "not_open": "尚未放票",
        "other": "接口持续报错",
    }
    for date_str, st, et in pending:
        kind = fail_kind.get((date_str, st, et))
        log(f"未完成: {date_str} {st}-{et}: "
            f"{REASONS.get(kind, '未放票/已被抢完/持续失败')}")
    log(f"===== 预约流程结束, 共尝试 {attempt} 轮 =====")


def seconds_until(cfg, hour, minute, minus=0):
    """距下一次 hour:minute 的秒数。恒为正: 整点刚过就算明天(注意, 不能用作
    "等到某时刻"的循环条件, 否则越过整点瞬间 remain 跳回 ~86400, 永不退出)。"""
    now = now_in(cfg)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)  # 今天的时刻已过 -> 明天的
    return (target - now).total_seconds() - minus


def wait_until_open(cfg) -> requests.Session | None:
    """睡到放票前 lead 秒登录, 再精确等到放票时刻。返回预登录会话(可失败)。

    目标时刻在进入时一次性锚定为"今天 {open_time}", 之后只与当前时间比较;
    不能用 seconds_until() 逐轮重算——它在 08:00:00 整点会把目标滚到明天,
    remain 从 1s 跳回 ~86400s, 循环永不退出(2026-09-24/25 线上事故根因)。
    启动时已过当天放票时刻则不等待, 直接返回交给 book_run 立即尝试。
    """
    open_h, open_m = map(int, cfg["open_time"].split(":"))
    lead = int(cfg["login_lead_seconds"])
    tgt = now_in(cfg).replace(hour=open_h, minute=open_m, second=0,
                              microsecond=0)
    if tgt <= now_in(cfg):
        log(f"已过今日放票时刻 {cfg['open_time']}, 跳过等待直接尝试")
        return None
    while True:
        remain = (tgt - now_in(cfg)).total_seconds() - lead
        if remain <= 0:
            break
        log(f"距登录时刻 {remain/3600:.2f} 小时, 先休眠 "
            f"(放票 {cfg['open_time']}, 提前 {lead}s 登录)")
        time.sleep(min(remain, 3600))
    session = None
    try:
        session = cas_login(cfg["username"], cfg["password"],
                        cfg["http_timeout"], cfg["proxy"])
    except Exception as e:
        log(f"提前登录失败(将到点重试): {e}")
    while True:
        now = now_in(cfg)
        if now >= tgt:
            break
        remain = (tgt - now).total_seconds()
        if remain > 2:
            log(f"距放票 {remain:.0f}s")
        time.sleep(min(remain, 1.0))
    return session


# ---------------- 状态查看 ----------------
def show_status(cfg):
    s = cas_login(cfg["username"], cfg["password"], cfg["http_timeout"],
                  cfg["proxy"])
    adv_days, adv_open = load_advance_rule(s, cfg["http_timeout"])
    if adv_days is None:
        adv_days, adv_open = cfg["advance_days"], cfg["open_time"]
    now = now_in(cfg)
    print(f"当前时间({cfg['timezone']}): {now.strftime('%F %T %A')}")
    print(f"规则: 提前 {adv_days} 天, 每日 {adv_open} 放票")
    print("目标时段配置:")
    for t in cfg["targets"]:
        if t.get("skip"):
            print(f"  {t['date']} 该天不约")
        elif t.get("date"):
            print(f"  {t['date']} {t.get('start')}-{t.get('end')} (单次)")
        else:
            print(f"  每{WEEKDAY_NAMES[int(t['weekday'])]} {t['start']}-{t['end']}")

    targets = compute_targets(cfg, adv_days)
    if targets:
        print("窗口内的目标日期:")
        for d, st, et, delta in targets:
            wd = datetime.fromisoformat(d).isoweekday()
            print(f"  {d}({WEEKDAY_NAMES[wd]}) {st}-{et} ({delta} 天后)")
    else:
        print("窗口内暂无目标日期")

    print("\n我的订单:")
    orders = list_my_orders(s, cfg["http_timeout"])
    if not orders:
        print("  (无)")
    for o in orders:
        d = (o.get("use_date") or "")[:10]
        print(f"  {d} {o.get('start_time')}-{o.get('end_time')} "
              f"{o.get('room_name')} 订单 {o.get('order_no')} "
              f"状态 {val(o.get('order_status'))}")


def main():
    ap = argparse.ArgumentParser(description="浙大琴房自动预约")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--run-now", action="store_true", help="立即执行预约")
    ap.add_argument("--status", action="store_true", help="查看账号状态")
    ap.add_argument("--dry-run", action="store_true", help="不真正提交")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))

    if args.status:
        show_status(cfg)
    elif args.run_now or args.dry_run:
        book_run(cfg, dry_run=args.dry_run)
    else:
        session = wait_until_open(cfg)
        book_run(cfg, dry_run=False, session=session)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log("未捕获异常:\n" + traceback.format_exc())
        sys.exit(1)
