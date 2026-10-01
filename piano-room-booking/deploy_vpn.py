# -*- coding: utf-8 -*-
"""服务器最终部署:脚本+配置(带proxy)+VPN健康检查+端到端验证。

计划 targets 以服务器为准(网页控制台在线编辑), 默认不上传本地 targets;
确需用本地 config.json 整体覆盖时加参数 --config。
"""
import argparse
import json
import os
import time
from pathlib import Path
import paramiko

ap = argparse.ArgumentParser()
ap.add_argument("--config", action="store_true",
                help="用本地 config.json 整体覆盖服务器配置(含 targets)")
args = ap.parse_args()

HOST, USER, PWD = (os.environ.get("PIANO_DEPLOY_HOST", ""),
                   os.environ.get("PIANO_DEPLOY_USER", "root"),
                   os.environ.get("PIANO_DEPLOY_PWD", ""))
if not HOST or not PWD:
    raise SystemExit("请先设置环境变量 PIANO_DEPLOY_HOST / PIANO_DEPLOY_USER(默认 root)"
                     " / PIANO_DEPLOY_PWD 指向你自己的服务器")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PWD, timeout=20)


def run(cmd, timeout=180, show=True):
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    code = out.channel.recv_exit_status()
    if show:
        print("$ " + cmd[:120] + f"  (exit={code})")
        o = out.read().decode().strip()
        e = err.read().decode().strip()
        if o:
            print(o[:2000])
        if e:
            print("[stderr]", e[:400])
        print()
    return code


# 1. 上传脚本(与本脚本同目录)
HERE = Path(__file__).resolve().parent
sftp = ssh.open_sftp()
sftp.put(str(HERE / "piano_booking.py"),
         "/opt/piano-booking/piano_booking.py")
# 2. 服务器配置: 增加 proxy; targets 默认保留服务器版本(网页控制台在线维护)
cfg = json.load(open(HERE / "config.json", encoding="utf-8"))
cfg["proxy"] = "http://127.0.0.1:8888"
try:
    with sftp.open("/opt/piano-booking/config.json") as f:
        server_targets = json.load(f).get("targets")
except IOError:
    server_targets = None
if server_targets is not None and not args.config:
    kept = len(server_targets)
    cfg["targets"] = server_targets
    print(f"=== 保留服务器上的 {kept} 条计划(网页控制台维护); "
          f"如需用本地配置覆盖请加 --config ===")
sftp.remove("/opt/piano-booking/config.json")
with sftp.open("/opt/piano-booking/config.json", "w") as f:
    f.write(json.dumps(cfg, ensure_ascii=False, indent=2))
sftp.close()
print("=== 文件已上传 ===\n")

# 3. 健康检查脚本+timer: 放票前 07:40 自愈
run("cat > /opt/piano-booking/vpn_health.sh <<'EOF'\n"
    "#!/bin/bash\n"
    "# EasyConnect 代理健康检查: 不通则重启容器(重建隧道), 日志写 journald\n"
    "if ! curl -sk -x http://127.0.0.1:8888 -o /dev/null --max-time 15 \\\n"
    "     https://myarts.zju.edu.cn/service/api/sso/login-url; then\n"
    "    echo \"[$(date '+%F %T')] VPN proxy down, restarting easyconnect\"\n"
    "    docker restart easyconnect\n"
    "    sleep 45\n"
    "    if curl -sk -x http://127.0.0.1:8888 -o /dev/null --max-time 15 \\\n"
    "       https://myarts.zju.edu.cn/service/api/sso/login-url; then\n"
    "        echo \"[$(date '+%F %T')] VPN proxy recovered\"\n"
    "    else\n"
    "        echo \"[$(date '+%F %T')] VPN proxy STILL down after restart\"\n"
    "    fi\n"
    "else\n"
    "    echo \"[$(date '+%F %T')] VPN proxy OK\"\n"
    "fi\n"
    "EOF\n"
    "chmod +x /opt/piano-booking/vpn_health.sh")

svc = """[Unit]
Description=EasyConnect VPN proxy health check for piano booking

[Service]
Type=oneshot
ExecStart=/opt/piano-booking/vpn_health.sh
"""
tmr = """[Unit]
Description=Check VPN proxy before booking time

[Timer]
OnCalendar=*-*-* 07:40:00
OnBootSec=10min
OnUnitActiveSec=6h
Persistent=false
Unit=vpn-health.service

[Install]
WantedBy=timers.target
"""
run("cat > /etc/systemd/system/vpn-health.service <<'EOF'\n" + svc + "EOF\n"
    "cat > /etc/systemd/system/vpn-health.timer <<'EOF'\n" + tmr + "EOF\n"
    "systemctl daemon-reload && systemctl enable --now vpn-health.timer")

# 4. 端到端验证
print("=== 服务器 --status (经 VPN 代理) ===")
run("cd /opt/piano-booking && python3 piano_booking.py --status", timeout=120)
print("=== 服务器 dry-run ===")
run("cd /opt/piano-booking && python3 piano_booking.py --run-now --dry-run",
    timeout=120)
print("=== timers 总览 ===")
run("systemctl list-timers --no-pager | grep -E 'piano|vpn'")
ssh.close()
print("=== 部署完成 ===")
