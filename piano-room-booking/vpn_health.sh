#!/bin/bash
# zju-connect 深度健康自愈 (2026-10-25 升级, 取代 07:40 单次浅检)
#
# 背景: 2026-10-25 隧道会话在凌晨死亡但 zju-connect 进程存活、自带 UDP
# 心跳仍报 OK, 旧 vpn-health(6h 一次) 无法及时发现, 导致 CAS/琴房接口
# 从 07:57 坏到 08:39 人工介入。本脚本改为每分钟真实请求探测端到端连通。
#
# 逻辑:
#   1. 经代理真实请求 myarts(主探测) —— 通则一切正常
#   2. 主探测不通时再探 zjuam —— zjuam 通说明隧道正常、是学校侧
#      myarts 单点问题, 不重启(防学校维护期间反复重启)
#   3. 两个探测连续 MAXFAIL 次都不通才重启 zju-connect(单次抖动不折腾,
#      重启会掐断所有在途隧道连接, 包括 08:00 抢票)
#   4. 重启后轮询验证最多 60s; 仍不通则进入 RESTART_COOLDOWN 退避,
#      防止学校侧全面故障时每分钟重启
#   5. 顺带检查 piano-web healthz(独立于隧道), 连续 2 次不通重启 piano-web
#      (同 MAXFAIL/冷却约束, 防止误杀 08:00 调度所在进程)
#
# 状态存 /run/vpn-health/state(重启后清零, 开机后 zju-connect 本就会
# 重新建隧道)。日志进 journald: journalctl -u vpn-health -f
# 测试钩子: HEALTH_PROXY/HEALTH_URL/HEALTH_URL2/HEALTH_STATE_DIR 可覆盖。
set -u

PROXY="${HEALTH_PROXY:-http://127.0.0.1:8888}"
URL1="${HEALTH_URL:-https://myarts.zju.edu.cn/service/api/sso/login-url}"
URL2="${HEALTH_URL2:-https://zjuam.zju.edu.cn/}"
STATE_DIR="${HEALTH_STATE_DIR:-/run/vpn-health}"
STATE="$STATE_DIR/state"
LOCK="/run/vpn-health.lock"
MAXFAIL=2
RESTART_COOLDOWN=600
BOOK_URL="http://127.0.0.1:8039/healthz"

ts() { date '+%F %T'; }

mkdir -p "$STATE_DIR"
exec 9>"$LOCK"
flock -n 9 || exit 0    # 上一轮(重启+验证最长 ~80s)尚未结束, 跳过本轮

fails=0; last_restart=0; pwfails=0; last_pw_restart=0
if [ -f "$STATE" ]; then
    read -r fails last_restart pwfails last_pw_restart < "$STATE" || true
    fails=${fails:-0}; last_restart=${last_restart:-0}
    pwfails=${pwfails:-0}; last_pw_restart=${last_pw_restart:-0}
fi
now=$(date +%s)

probe() { curl -sk -x "$PROXY" -o /dev/null --max-time 12 "$1"; }

# ---- 隧道端到端探测 ----
if probe "$URL1"; then
    if [ "$fails" -gt 0 ]; then
        echo "[$(ts)] 隧道恢复(此前连败 $fails 次)"
    fi
    fails=0
else
    if probe "$URL2"; then
        if [ "$fails" -gt 0 ]; then
            echo "[$(ts)] myarts 不通但 zjuam 经隧道可达, 判定学校侧单点问题, 不重启"
        fi
        fails=0
    else
        fails=$((fails + 1))
        echo "[$(ts)] 隧道端到端探测失败 $fails/$MAXFAIL"
        if [ "$fails" -ge "$MAXFAIL" ]; then
            if [ $((now - last_restart)) -lt "$RESTART_COOLDOWN" ]; then
                echo "[$(ts)] 距上次重启不足 ${RESTART_COOLDOWN}s, 退避中(隧道仍未恢复)"
            else
                if systemctl is-active --quiet zju-connect; then
                    echo "[$(ts)] 连败达阈值, systemctl restart zju-connect"
                    systemctl restart zju-connect
                else
                    echo "[$(ts)] zju-connect 未运行, systemctl start zju-connect"
                    systemctl start zju-connect
                fi
                last_restart=$now
                recovered=0
                for _ in $(seq 1 12); do
                    sleep 5
                    if probe "$URL1"; then recovered=1; break; fi
                done
                if [ "$recovered" = 1 ]; then
                    echo "[$(ts)] 自愈成功, 隧道已恢复"
                    fails=0
                else
                    echo "[$(ts)] 重启后 $((12 * 5))s 内仍不通, ${RESTART_COOLDOWN}s 后再试(注意: 若 client_data.json 失效需人工跑 /opt/zju-connect/atrust_login.sh)"
                fi
            fi
        fi
    fi
fi

# ---- piano-web 存活(独立于隧道) ----
if curl -s -o /dev/null --max-time 8 "$BOOK_URL"; then
    pwfails=0
else
    pwfails=$((pwfails + 1))
    if [ "$pwfails" -ge 2 ]; then
        if [ $((now - last_pw_restart)) -lt "$RESTART_COOLDOWN" ]; then
            echo "[$(ts)] piano-web healthz 仍不通, 退避中"
        else
            echo "[$(ts)] piano-web healthz 连续 $pwfails 次不通, systemctl restart piano-web"
            systemctl restart piano-web
            last_pw_restart=$now
            pwfails=0
        fi
    fi
fi

echo "$fails $last_restart $pwfails $last_pw_restart" > "$STATE"
