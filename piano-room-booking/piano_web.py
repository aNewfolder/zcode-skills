#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
琴房预约助手 - 手机网页控制台
================================
运行在阿里云服务器上, 提供一个手机友好的网页:
  - 查看已有预约订单(只读; 取消请在 浙大钉-我的文艺生活 手动操作)
  - 添加/删除预约计划: "每周重复" 与 "指定日期" 两种
  - 指定日期计划会自动替代当天所有每周计划; 也可标记某天"不自动预约"
  - 对已放票的计划可"立即预约"
  - 对未使用的订单可"取消"(走官方后台服务; 门禁撤销失败自动继续)
  - 每天 08:00 的自动抢票仍由 piano-booking.timer 执行, 读取同一份 config.json

访问地址: http://<服务器IP>:<端口>/t/<token>/
token 首次启动自动生成在 web_token.txt, 仅凭此链接可操作。

仅用 Python 标准库, 无额外依赖。
"""

import json
import os
import secrets
import threading
import time
import traceback
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import piano_booking as pb

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TOKEN_PATH = BASE_DIR / "web_token.txt"
WEBLOG = BASE_DIR / "web.log"
HOST = "0.0.0.0"
PORT = int(os.environ.get("PIANO_WEB_PORT", "8039"))

CFG_LOCK = threading.Lock()   # 保护 config.json 与共享会话
BOOK_LOCK = threading.Lock()  # 预约操作串行化
SESSION = {"s": None, "ts": 0.0}
SESSION_MAX_AGE = 4 * 3600


def wlog(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(WEBLOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def get_token() -> str:
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(token, encoding="utf-8")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass
    return token


def get_session(force=False):
    """共享 CAS 会话: 过期则重新登录(线程安全)。"""
    with CFG_LOCK:
        age = time.time() - SESSION["ts"]
        if force or SESSION["s"] is None or age > SESSION_MAX_AGE:
            cfg = pb.load_config(CONFIG_PATH)
            wlog("(web) 重新登录 CAS ...")
            SESSION["s"] = pb.cas_login(cfg["username"], cfg["password"],
                                        cfg["http_timeout"], cfg.get("proxy", ""))
            SESSION["ts"] = time.time()
        return SESSION["s"]


# ---------------- API 逻辑 ----------------
def api_state():
    cfg = pb.load_config(CONFIG_PATH)
    s = get_session()
    adv_days, adv_open = pb.load_advance_rule(s, cfg["http_timeout"])
    if adv_days is None:
        adv_days, adv_open = cfg["advance_days"], cfg["open_time"]
    orders = pb.list_my_orders(s, cfg["http_timeout"])
    booked = pb.active_booked_keys(orders)
    now = pb.now_in(cfg)

    skip_dates, dated_dates = set(), set()
    for t in cfg["targets"]:
        if t.get("date"):
            (skip_dates if t.get("skip") else dated_dates).add(str(t["date"]))

    targets = []
    for i, t in enumerate(cfg["targets"]):
        st, et = t.get("start"), t.get("end")
        if t.get("date"):
            try:
                d0 = datetime.fromisoformat(str(t["date"])).date()
            except ValueError:
                continue
            delta = (d0 - now.date()).days
            label = str(t["date"])
            kind = "skip" if t.get("skip") else "date"
            if kind == "skip":
                targets.append({"index": i, "kind": "skip", "label": label,
                                "date": str(t["date"]), "start": "", "end": "",
                                "next_date": label, "delta": delta,
                                "in_window": False,
                                "expired": delta < 0, "booked": False,
                                "suppressed": False})
                continue
        else:
            wd = int(t["weekday"])
            delta = (wd - now.isoweekday()) % 7
            d0 = (now + timedelta(days=delta)).date()
            label = f"每周{pb.WEEKDAY_NAMES[wd]}"
            kind = "weekly"
        next_date = d0.isoformat()
        expired = bool(t.get("date")) and delta < 0
        passed = delta == 0 and st and now > now.replace(
            hour=int(st.split(":")[0]), minute=int(st.split(":")[1]),
            second=0, microsecond=0) - timedelta(minutes=30)
        in_window = (0 <= delta <= adv_days) and not passed
        suppressed = (kind == "weekly"
                      and (next_date in skip_dates
                           or next_date in dated_dates))
        targets.append({
            "index": i, "kind": kind, "label": label,
            "weekday": t.get("weekday"), "date": t.get("date"),
            "start": st or "", "end": et or "",
            "next_date": next_date, "delta": delta,
            "in_window": in_window and not expired and not suppressed,
            "expired": expired,
            "booked": (next_date, st, et) in booked if st else False,
            "suppressed": suppressed,
        })
    order_kind = {"skip": 0, "date": 1, "weekly": 2}
    targets.sort(key=lambda x: (order_kind[x["kind"]], x["next_date"],
                                x["start"]))

    order_list = []
    for o in orders:
        status = str(pb.val(o.get("order_status")) or "")
        d = (o.get("use_date") or "")[:10]
        order_list.append({
            "order_no": o.get("order_no"), "room": o.get("room_name"),
            "date": d, "start": o.get("start_time"), "end": o.get("end_time"),
            "status": status,
        })
    order_list.sort(key=lambda x: (x["date"], x["start"] or ""), reverse=True)

    drop_remain = pb.seconds_until(cfg, *map(int, cfg["open_time"].split(":")))
    if drop_remain < 0:
        drop_remain += 86400
    h, rem = divmod(int(drop_remain), 3600)
    return {
        "ok": True,
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": pb.WEEKDAY_NAMES[now.isoweekday()],
        "rules": {"advance_days": adv_days, "open_time": adv_open,
                  "campus": cfg["campus"],
                  "rooms": "、".join(cfg["room_preference"][:6])},
        "drop_countdown": f"{h}小时{rem // 60:02d}分",
        "targets": targets,
        "orders": order_list[:15],
    }


def validate_target(t, max_hours=2.0):
    """校验并规范化一条计划, 返回 (ok, 规范化条目或错误信息)。"""
    def ptime(v):
        parts = str(v).split(":")
        if len(parts) != 2:
            return None
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h < 24 and m in (0, 30)):
            return None
        return f"{h:02d}:{m:02d}"

    if "date" in t and t.get("date"):
        try:
            datetime.fromisoformat(str(t["date"]))
        except ValueError:
            return False, "日期格式有误"
        if t.get("skip"):
            return True, {"date": str(t["date"]), "skip": True}
        st, et = ptime(t.get("start")), ptime(t.get("end"))
        if not st or not et or st >= et:
            return False, "时间格式有误(需 HH:MM, 30 分钟为步长, 且开始早于结束)"
        h1, m1 = map(int, st.split(":"))
        h2, m2 = map(int, et.split(":"))
        if (h2 * 60 + m2 - h1 * 60 - m1) / 60 > max_hours + 1e-9:
            return False, f"单次最长 {max_hours:g} 小时"
        return True, {"date": str(t["date"]), "start": st, "end": et}
    st, et = ptime(t.get("start")), ptime(t.get("end"))
    if not st or not et or st >= et:
        return False, "时间格式有误(需 HH:MM, 30 分钟为步长, 且开始早于结束)"
    h1, m1 = map(int, st.split(":"))
    h2, m2 = map(int, et.split(":"))
    if (h2 * 60 + m2 - h1 * 60 - m1) / 60 > max_hours + 1e-9:
        return False, f"单次最长 {max_hours:g} 小时"
    try:
        wd = int(t.get("weekday"))
    except (TypeError, ValueError):
        return False, "weekday 需为 1-7"
    if not 1 <= wd <= 7:
        return False, "weekday 需为 1-7"
    return True, {"weekday": wd, "start": st, "end": et}


def api_targets_replace(body):
    items = body.get("targets")
    if not isinstance(items, list):
        return {"ok": False, "error": "缺少 targets 数组"}
    normalized, seen = [], set()
    for t in items[:50]:
        ok, r = validate_target(t)
        if not ok:
            return {"ok": False, "error": r}
        key = json.dumps(r, sort_keys=True)
        if key not in seen:
            seen.add(key)
            normalized.append(r)
    with CFG_LOCK:
        cfg = pb.load_config(CONFIG_PATH)
        cfg["targets"] = normalized
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
    wlog(f"(web) 计划已更新, 共 {len(normalized)} 条")
    return {"ok": True, "count": len(normalized)}


def api_book(body):
    cfg = pb.load_config(CONFIG_PATH)
    idx = body.get("index")
    if idx is None or not isinstance(cfg["targets"], list) \
            or not (0 <= int(idx) < len(cfg["targets"])):
        return {"ok": False, "error": "无效的计划编号"}
    target = cfg["targets"][int(idx)]
    ok, r = validate_target(target)
    if not ok:
        return {"ok": False, "error": r}
    if target.get("skip"):
        return {"ok": False, "error": "跳过类计划无需预约"}
    with BOOK_LOCK:
        cfg2 = dict(cfg)
        cfg2["targets"] = [r]
        cfg2["retry_seconds"] = 30  # 网页交互保持快速
        s = get_session()
        log_before = pb.LOG_FILE.stat().st_size if pb.LOG_FILE.exists() else 0
        pb.book_run(cfg2, dry_run=False, session=s)
        tail = ""
        try:
            with open(pb.LOG_FILE, "r", encoding="utf-8",
                      errors="replace") as f:
                f.seek(log_before)
                tail = f.read()
        except OSError:
            pass
    lines = [ln.split("] ", 1)[-1] for ln in tail.strip().splitlines()
             if ln.strip()
             and not ln.split("] ", 1)[-1].startswith("=====")]
    return {"ok": True, "log": lines[-8:]}


CANCELLABLE = ("waiting_use", "pending_approval")


def api_cancel(body):
    order_no = str(body.get("order_no") or "").strip()
    if not order_no:
        return {"ok": False, "error": "缺少订单号"}
    with BOOK_LOCK:
        cfg = pb.load_config(CONFIG_PATH)
        s = get_session()
        orders = pb.list_my_orders(s, cfg["http_timeout"])
        order = next((o for o in orders if o.get("order_no") == order_no), None)
        if not order:
            return {"ok": False, "error": "未找到该订单(请刷新后重试)"}
        status = str(pb.val(order.get("order_status")) or "")
        if status not in CANCELLABLE:
            return {"ok": False, "error": f"订单状态为 {status or '未知'}, 无需取消"}
        log_before = pb.LOG_FILE.stat().st_size if pb.LOG_FILE.exists() else 0
        ok, msg = pb.cancel_order(s, order, timeout=cfg["http_timeout"],
                                  ignore_door_errors=True)
        tail = ""
        try:
            with open(pb.LOG_FILE, "r", encoding="utf-8",
                      errors="replace") as f:
                f.seek(log_before)
                tail = f.read()
        except OSError:
            pass
    lines = [ln.split("] ", 1)[-1] for ln in tail.strip().splitlines()
             if ln.strip()]
    wlog(f"(web) 取消订单 {order_no}: {'成功' if ok else '失败'} - {msg}")
    return {"ok": ok, "message": msg, "log": lines[-8:]}


# ---------------- HTTP 服务 ----------------
PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>琴房预约助手</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#1c1e21;--sub:#65676b;--pri:#2563eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);font-size:15px;padding:12px;max-width:520px;margin:0 auto;padding-bottom:80px}
h1{font-size:19px;margin:6px 2px 2px}
.sub{color:var(--sub);font-size:12.5px;margin:0 2px 12px}
.card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card h2{font-size:15px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
.row{display:flex;align-items:center;gap:10px;padding:10px 4px;border-top:1px solid #f0f0f2;flex-wrap:wrap}
.row:first-of-type{border-top:none}
.row .main{flex:1;min-width:150px}
.t1{font-weight:600}
.t2{color:var(--sub);font-size:12.5px;margin-top:2px}
.badge{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:20px;background:#eef2ff;color:var(--pri);margin-right:4px}
.badge.g{background:#ecfdf5;color:var(--ok)}.badge.o{background:#fffbeb;color:var(--warn)}.badge.r{background:#fef2f2;color:var(--bad)}.badge.gr{background:#f3f4f6;color:var(--sub)}
button{border:none;border-radius:9px;padding:9px 14px;font-size:14px;background:#eef2ff;color:var(--pri);font-weight:600}
button.danger{background:#fef2f2;color:var(--bad)}
button.pri{background:var(--pri);color:#fff}
button:active{opacity:.7}
select,input{padding:9px;border:1px solid #d9dbe0;border-radius:9px;font-size:15px;background:#fff;color:var(--ink);max-width:100%}
label.ck{display:flex;align-items:center;gap:6px;font-size:13.5px;color:var(--sub);margin:6px 0}
.grid{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.grid>*{flex:1;min-width:100px}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tabs button{flex:1;background:#f3f4f6;color:var(--sub)}
.tabs button.on{background:var(--pri);color:#fff}
.empty{color:var(--sub);text-align:center;padding:14px 0;font-size:13.5px}
.tip{font-size:12px;color:var(--sub);margin-top:8px;line-height:1.5}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#111827ee;color:#fff;padding:11px 18px;border-radius:10px;font-size:14px;max-width:88%;display:none;z-index:9}
</style></head><body>
<h1>🎹 琴房预约助手</h1>
<p class="sub" id="meta">加载中…</p>

<div class="card"><h2>我的预约 <button onclick="load()">刷新</button></h2><div id="orders"></div>
<div class="tip">点「取消」即可取消预约（需在开始前 30 分钟以上）。若门禁撤销失败会自动继续完成取消，不影响订单。</div></div>

<div class="card"><h2>预约计划 <button onclick="showAdd()">+ 添加</button></h2>
<div id="targets"></div>
<div class="tip">每天 <b id="ruleOpen">08:00</b> 放出 <b id="ruleAdv">3</b> 天内的新票，自动按计划抢票。加一条「单次日期」计划会自动<b>替代当天</b>的每周计划；想跳过某天就加一条「该天不约」。</div></div>

<div class="card" id="addCard" style="display:none">
<h2>添加计划</h2>
<div class="tabs">
<button id="tabWeekly" onclick="setTab('weekly')">每周重复</button>
<button id="tabDate" onclick="setTab('date')">指定日期</button></div>
<div class="grid" id="dateWrap" style="display:none"><input type="date" id="fDate"></div>
<div class="grid" id="wdWrap"><select id="fWd"></select></div>
<label class="ck" id="skipWrap" style="display:none"><input type="checkbox" id="fSkip"> 这天不约（跳过自动预约）</label>
<div class="grid" id="timeWrap">
<select id="fStart"></select>
<select id="fDur"><option value="60">1 小时</option><option value="90">1.5 小时</option><option value="120" selected>2 小时</option></select>
</div>
<button class="pri" style="width:100%" onclick="addTarget()">添加计划</button>
<div class="tip">时段 08:00-22:00，单次最长 2 小时。</div>
</div>

<div id="toast"></div>
<script>
let S=null, tab='weekly';
const WD={1:'周一',2:'周二',3:'周三',4:'周四',5:'周五',6:'周六',7:'周日'};
const $=id=>document.getElementById(id);
function toast(m,ms=2600){const t=$('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',ms)}
async function api(p,body){const r=await fetch('api/'+p,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json()}
function stBadge(s){const m={'waiting_use':['待使用','g'],'pending_approval':['待审批','o'],'checked_in':['已使用','gr'],'cancelled':['已取消','gr'],'no_show':['爽约','r'],'refunded':['已退款','gr'],'rejected':['已拒绝','r']};const x=m[s]||[s,'gr'];return `<span class="badge ${x[1]}">${x[0]}</span>`}
function render(){
  $('meta').textContent=`${S.now} ${S.weekday} · 下次放票约 ${S.drop_countdown} 后`;
  $('ruleOpen').textContent=S.rules.open_time; $('ruleAdv').textContent=S.rules.advance_days;
  const oEl=$('orders');
  if(!S.orders.length){oEl.innerHTML='<div class="empty">暂无预约记录</div>'}
  else{oEl.innerHTML=S.orders.map(o=>{
    const cc=o.status==='waiting_use'||o.status==='pending_approval';
    return `<div class="row"><div class="main"><div class="t1">${o.date} ${o.start}-${o.end}</div>
    <div class="t2">${o.room||''} · ${o.order_no||''}</div></div>${stBadge(o.status)}${cc?`<button class="danger" onclick="cancelOrder('${o.order_no}')">取消</button>`:''}</div>`}).join('')}
  const tEl=$('targets');
  if(!S.targets.length){tEl.innerHTML='<div class="empty">还没有计划, 点右上角 + 添加</div>'}
  else{tEl.innerHTML=S.targets.map(t=>{
    let state, t1;
    if(t.kind==='skip'){t1=`<span class="t1">${t.label} 不约</span>`;state='<span class="badge gr">跳过该天</span>'}
    else{
      t1=`<span class="t1">${t.kind==='weekly'?'每周'+WD[t.weekday]:t.label} ${t.start}-${t.end}</span>`;
      if(t.expired) state='<span class="badge gr">已过期</span>';
      else if(t.booked) state='<span class="badge g">已约到</span>';
      else if(t.suppressed) state='<span class="badge gr">当天已由其他计划替代</span>';
      else if(t.in_window) state=`<span class="badge o">可约 ${t.next_date}</span>`;
      else state=`<span class="badge gr">等放票 ${t.next_date}</span>`;
    }
    const bk=(t.kind!=='skip'&&!t.expired&&!t.booked&&!t.suppressed)?`<button onclick="bookTarget(${t.index})">立即预约</button>`:'';
    return `<div class="row"><div class="main">${t1}<div class="t2">${t.kind==='skip'?'该天自动预约将被跳过':'最近一次: '+t.next_date}</div></div>${state}${bk}
    <button class="danger" onclick="delTarget(${t.index})">删除</button></div>`}).join('')}
}
async function load(){try{S=await api('state');render()}catch(e){toast('加载失败: '+e)}}
function planOf(t){if(t.kind==='skip')return{date:t.date,skip:true};if(t.kind==='date')return{date:t.date,start:t.start,end:t.end};return{weekday:t.weekday,start:t.start,end:t.end}}
async function addTarget(){
  const b={};
  if(tab==='weekly'){b.weekday=+$('fWd').value}
  else{b.date=$('fDate').value;if(!b.date){toast('请选择日期');return}}
  if($('fSkip').checked&&tab==='date'){b.skip=true}
  else{b.start=$('fStart').value;const mins=+$('fDur').value,[h,m]=b.start.split(':').map(Number);
    const e=new Date(2000,0,1,h,m+mins);b.end=String(e.getHours()).padStart(2,'0')+':'+String(e.getMinutes()).padStart(2,'0')}
  const cur=S.targets.map(planOf);cur.push(b);
  const r=await api('targets',{targets:cur});if(r.ok){toast('已添加');hideAdd();load()}else toast(r.error||'失败',3500)}
async function delTarget(i){if(!confirm('确定删除这条计划? (已约到的订单不受影响)'))return;const cur=S.targets.filter(t=>t.index!==i).map(planOf);const r=await api('targets',{targets:cur});if(r.ok){toast('已删除');load()}else toast(r.error||'失败')}
async function bookTarget(i){toast('提交中, 请稍候…',10000);const r=await api('book',{index:i});if(r.ok&&r.log){toast(r.log[r.log.length-1]||'完成',5000);console.log(r.log)}else toast(r.error||'失败',3500);load()}
async function cancelOrder(no){
  if(!confirm('确定取消这条预约? 时段会立即释放; 请在开始前 30 分钟以上操作。'))return;
  toast('取消中, 请稍候…',12000);
  const r=await api('cancel',{order_no:no});
  if(r.ok)toast(r.message||'已取消',5000);else toast(r.error||r.message||'取消失败',4500);
  load()}
function setTab(k){tab=k;$('tabWeekly').classList.toggle('on',k==='weekly');$('tabDate').classList.toggle('on',k==='date');$('wdWrap').style.display=k==='weekly'?'flex':'none';$('dateWrap').style.display=k==='date'?'flex':'none';$('skipWrap').style.display=k==='date'?'block':'none';$('timeWrap').style.display=(k==='weekly'||!$('fSkip').checked)?'flex':'none'}
function showAdd(){$('addCard').style.display='block';$('addCard').scrollIntoView({behavior:'smooth'})}
function hideAdd(){$('addCard').style.display='none'}
(function init(){
  const wd=$('fWd');for(let i=1;i<=7;i++){const o=document.createElement('option');o.value=i;o.textContent='每'+WD[i];wd.appendChild(o)}
  const st=$('fStart');for(let h=8;h<=21;h++)for(const m of [0,30]){const v=String(h).padStart(2,'0')+':'+String(m).padStart(2,'0');if(h===21&&m===30)continue;const o=document.createElement('option');o.value=v;o.textContent=v;st.appendChild(o)}
  $('fDate').min=new Date().toISOString().slice(0,10);
  $('fSkip').addEventListener('change',()=>setTab(tab));
  setTab('weekly');load();setInterval(load,60000);
})();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        try:
            path = urlparse(self.path).path
            parts = path.strip("/").split("/")
            token = get_token()
            if len(parts) < 2 or parts[0] != "t" or parts[1] != token:
                self._send(404, "not found", "text/plain; charset=utf-8")
                return
            rest = "/".join(parts[2:])
            if rest in ("",):
                self._send(200, PAGE, "text/html; charset=utf-8")
                return
            if rest == "api/state" and method == "GET":
                self._json(api_state)
                return
            if rest in ("api/targets", "api/book", "api/cancel") \
                    and method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                fn = {"api/targets": api_targets_replace,
                      "api/book": api_book,
                      "api/cancel": api_cancel}[rest]
                self._json(lambda: fn(body))
                return
            self._send(404, "not found", "text/plain; charset=utf-8")
        except BrokenPipeError:
            pass
        except Exception:
            wlog("请求处理异常:\n" + traceback.format_exc())
            try:
                self._send(500, {"ok": False, "error": "服务器内部错误"})
            except Exception:
                pass

    def _json(self, fn):
        try:
            result = fn()
        except Exception as e:
            wlog("API 异常:\n" + traceback.format_exc())
            result = {"ok": False, "error": f"操作失败: {e}"}
        self._send(200, json.dumps(result, ensure_ascii=False))

    def log_message(self, fmt, *args):
        pass


def main():
    token = get_token()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    wlog(f"琴房预约助手启动: http://0.0.0.0:{PORT}/t/{token}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
