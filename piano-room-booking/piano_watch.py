#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
琴房候补守护进程(捡漏)
======================
持续扫描 watch.json 中的约满时段, 一旦有琴房空出立即自动补约,
成功/放弃通过飞书群机器人推送(需 config.json 配置 feishu_webhook)。

  - watch.json 条目来源:
      1) 抢票(定时器/网页立即预约)失败且约满时由 book_run 自动写入
      2) 网页控制台对任意计划点「候补」手动写入
  - 每轮: 清理已到期条目 -> 移除已有订单的条目 -> 逐条按琴房优先级
    真实提交(与正常预约同一接口, 满员返回冲突无副作用, 空出即约上,
    属原子"测试即预约", 不会错过空位)
  - 扫描窗口: 默认开始前 2 小时停止(watch_stop_lead_min, 学校规则
    取消需提前 30 分钟, 之后仍可能有人取消但价值低); 可设
    watch_start_lead_min 让远期条目只在开始前 N 分钟内才扫描
  - config.json 每轮重新加载: 改 feishu_webhook/watch_interval 一分钟内生效
  - 会话常驻, 失效/VPN 抖动自动重登录; systemd 服务 piano-watch

用法:
  python3 piano_watch.py          # 前台常驻(give systemd 用)
"""

import random
import time
import traceback

import piano_booking as pb

RULE_TTL = 1800   # 预约规则缓存(秒)
ROOM_TTL = 600    # 琴房列表缓存(秒)
HEARTBEAT = 600   # 扫描心跳日志间隔(秒), 仅在队列非空时输出
SESSION_MAX_AGE = 3 * 3600
SESSION = {"s": None, "ts": 0.0}
_last_heartbeat = 0.0
_cache = {"adv_days": None, "adv_ts": 0.0, "rooms": None, "cmap": None,
          "rooms_ts": 0.0}


def get_session(cfg, force=False):
    """常驻会话: 过期/强制时重新登录(登录自带重试)。"""
    age = time.time() - SESSION["ts"]
    if force or SESSION["s"] is None or age > SESSION_MAX_AGE:
        SESSION["s"] = pb.login_with_retry(cfg)
        SESSION["ts"] = time.time()
    return SESSION["s"]


def _in_window(start_lead, it, naive_now):
    """时段是否已进入扫描窗口(开始前 start_lead 分钟内; 0=入队即扫)。"""
    if not start_lead:
        return True
    try:
        start_dt = pb.datetime.fromisoformat(f"{it['date']}T{it['start']}")
    except ValueError:
        return False
    return start_dt - naive_now <= pb.timedelta(minutes=start_lead)


def get_rule_rooms(s, cfg):
    """(advance_days, rooms, cmap), 带缓存, 减少每轮接口调用。"""
    if _cache["adv_days"] is None or time.time() - _cache["adv_ts"] > RULE_TTL:
        try:
            days, _ = pb.load_advance_rule(s, cfg["http_timeout"])
        except Exception:
            days = None
        if days is None:
            days = cfg["advance_days"]
        _cache["adv_days"] = days
        _cache["adv_ts"] = time.time()
        _cache["rooms"] = None  # 规则变化时强制重读琴房
    if _cache["rooms"] is None or time.time() - _cache["rooms_ts"] > ROOM_TTL:
        cmap = pb.campus_map(s, cfg["http_timeout"])
        _cache["rooms"] = pb.load_rooms(s, cfg, cmap, cfg["http_timeout"])
        _cache["cmap"] = cmap
        _cache["rooms_ts"] = time.time()
    return _cache["adv_days"], _cache["rooms"], _cache["cmap"]


def watch_round(cfg):
    """一轮扫描: 返回(可能已更换的)会话。"""
    s = get_session(cfg)
    items = pb.load_watch()
    if not items:
        return s
    now = pb.now_in(cfg)
    naive_now = now.replace(tzinfo=None)
    lead = int(cfg.get("watch_stop_lead_min", 120))
    start_lead = int(cfg.get("watch_start_lead_min", 0) or 0)
    changed = False

    # 1) 超时放弃: 距开始不足 lead 分钟后不可能再有人取消
    for it in list(items):
        try:
            start_dt = pb.datetime.fromisoformat(
                f"{it['date']}T{it['start']}")
        except ValueError:
            items.remove(it)
            changed = True
            continue
        if naive_now >= start_dt - pb.timedelta(minutes=lead):
            items.remove(it)
            changed = True
            pb.log(f"候补超时停止: {it['date']} {it['start']}-{it['end']} "
                   f"(已临近开始)")
            pb.feishu_notify(
                cfg, f"⏰ 琴房候补停止：{it['date']} {it['start']}-"
                     f"{it['end']}\n已临近开始仍未捡到空位，自动放弃。")

    # 2) 该时段已有订单(常规预约已覆盖) -> 静默移除
    try:
        booked = pb.active_booked_keys(
            pb.list_my_orders(s, cfg["http_timeout"]))
    except Exception as e:
        pb.log(f"候补: 订单读取失败({e.__class__.__name__}), 本轮跳过")
        return s
    for it in list(items):
        if (it["date"], it["start"], it["end"]) in booked:
            items.remove(it)
            changed = True
            pb.log(f"候补移除(已有订单): {it['date']} {it['start']}-"
                   f"{it['end']}")
    if changed:
        pb.save_watch(items)
    if not items:
        return s

    adv_days, rooms, cmap = get_rule_rooms(s, cfg)
    if not rooms:
        pb.log("候补: 无候选琴房, 本轮跳过")
        return s

    # 心跳: 让"确实在扫"可观测, 又不至于刷日志
    global _last_heartbeat
    if time.time() - _last_heartbeat > HEARTBEAT:
        n_win = sum(1 for x in items if _in_window(start_lead, x, naive_now))
        extra = f"({n_win} 条在窗口内)" if start_lead else ""
        pb.log(f"候补扫描心跳: {len(items)} 条在队{extra}, "
               f"间隔约 {max(30, int(cfg.get('watch_interval', 60)))}s")
        _last_heartbeat = time.time()

    # 3) 逐条尝试提交(原子测试即预约)
    for it in list(items):
        if (it["date"], it["start"], it["end"]) in booked:
            continue
        try:
            use_d = pb.datetime.fromisoformat(it["date"]).date()
        except ValueError:
            continue
        if (use_d - now.date()).days > adv_days:
            continue  # 未放票
        if not _in_window(start_lead, it, naive_now):
            continue  # 未到扫描窗口(开始前 start_lead 分钟才开始扫)
        i, retried = 0, False
        occ = pb.get_availability(s, it["date"], rooms, cfg["http_timeout"])
        while i < len(rooms):
            room = rooms[i]
            i += 1
            if pb.occupation_block(occ, room, it["start"], it["end"]):
                continue  # 管理方占用段: 服务端不拦, 必须自查, 换下一间
            try:
                code, msg, order = pb.submit_booking(
                    s, room, cmap.get(room.get("campus_id")) or cfg["campus"],
                    it["date"], it["start"], it["end"], cfg["http_timeout"])
            except Exception as e:
                pb.log(f"候补 {it['date']} {it['start']} "
                       f"{room.get('room_name')} 网络异常"
                       f"({e.__class__.__name__}), 换会话下轮再试")
                time.sleep(3)
                s = get_session(cfg, force=True)
                break
            if code == 200 and order:
                room_name = order.get("room_name") or room.get("room_name")
                wd = pb.WEEKDAY_NAMES[use_d.isoweekday()]
                pb.log(f"候补成功! {it['date']}({wd}) {it['start']}-"
                       f"{it['end']} {room_name} 订单 {order.get('order_no')}")
                items.remove(it)
                changed = True
                pb.feishu_notify(
                    cfg, f"🎹 琴房候补成功！\n{it['date']}({wd}) "
                         f"{it['start']}-{it['end']}\n{room_name}\n"
                         f"订单号 {order.get('order_no')}\n"
                         f"门禁密码见网页控制台（开场前 15 分钟内显示）")
                break
            if any(k in msg for k in pb.MSG_NOT_OPEN):
                break  # 未放票, 下轮再看
            if any(k in msg for k in pb.MSG_CONFLICT):
                continue  # 该间满, 试下一间
            pb.log(f"候补提交异常(code={code}): {msg}")
            if code == 401 or "登录" in msg:
                pb.log("候补: 会话失效, 重新登录")
                s = get_session(cfg, force=True)
                break
            # 偶发 403/网关抖动(网关常拦每轮首个请求): 稍候重试同一间
            if not retried:
                retried = True
                time.sleep(3)
                i -= 1
    if changed:
        pb.save_watch(items)
    return s


def main():
    pb.log("琴房候补守护启动")
    while True:
        try:
            cfg = pb.load_config(pb.DEFAULT_CONFIG)
            interval = max(30, int(cfg.get("watch_interval", 60)))
            if cfg.get("watch_enabled", True):
                watch_round(cfg)
        except KeyboardInterrupt:
            raise
        except Exception:
            pb.log("候补轮异常:\n" + traceback.format_exc())
            interval = 20
        time.sleep(interval + random.uniform(0, max(1.0, interval * 0.2)))


if __name__ == "__main__":
    main()
