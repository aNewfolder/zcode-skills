# -*- coding: utf-8 -*-
"""服务器部署参考：脚本+配置(带proxy)+VPN健康检查+端到端验证。

凭据与服务器信息一律从环境变量读取，指向你自己的服务器：
  PIANO_DEPLOY_HOST  目标服务器 IP/域名（必填）
  PIANO_DEPLOY_USER  SSH 用户（默认 root）
  PIANO_DEPLOY_KEY   SSH 私钥路径（默认 ~/.ssh/id_ed25519；存在则优先密钥登录）
  PIANO_DEPLOY_PWD   SSH 密码（无密钥时使用）

VPN 说明(2026-10-04 起)：隧道由 zju-connect(aTrust 协议) systemd 服务提供
（部署方法见本仓库 zju-atrust-vpn skill，HTTP 代理 127.0.0.1:8888），
旧 easyconnect 容器方案已被浙大下线，勿再使用。
vpn-health.timer 每分钟端到端探测隧道，连续失败自动重启自愈。

计划 targets 以服务器为准（网页控制台在线编辑），默认不上传本地 targets；
确需用本地 config.json 整体覆盖时加参数 --config。
"""
import argparse
import json
import os
from pathlib import Path

import paramiko

ap = argparse.ArgumentParser()
ap.add_argument("--config", action="store_true",
                help="用本地 config.json 整体覆盖服务器配置(含 targets)")
args = ap.parse_args()

HOST = os.environ.get("PIANO_DEPLOY_HOST", "")
USER = os.environ.get("PIANO_DEPLOY_USER", "root")
KEY = os.environ.get("PIANO_DEPLOY_KEY",
                     str(Path.home() / ".ssh" / "id_ed25519"))
PWD = os.environ.get("PIANO_DEPLOY_PWD", "")
if not HOST or not (os.path.isfile(KEY) or PWD):
    raise SystemExit("请先设置环境变量 PIANO_DEPLOY_HOST / PIANO_DEPLOY_USER(默认 root) "
                     "/ PIANO_DEPLOY_KEY(默认 ~/.ssh/id_ed25519) 或 PIANO_DEPLOY_PWD "
                     "指向你自己的服务器")
kwargs = {"key_filename": KEY} if os.path.isfile(KEY) else {"password": PWD}
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, timeout=20, **kwargs)


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


run("mkdir -p /opt/piano-booking")

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

# 3. 健康检查脚本+timer: 每分钟端到端深度探测, 连续 2 次失败才重启,
#    重启后验证 + 退避冷却(2026-10-25 升级, 详见 vpn_health.sh 头注释)
sftp.put(str(HERE / "vpn_health.sh"), "/opt/piano-booking/vpn_health.sh")
sftp.close()
run("chmod +x /opt/piano-booking/vpn_health.sh")

svc = """[Unit]
Description=Deep health check & auto-heal for zju-connect tunnel and piano-web

[Service]
Type=oneshot
ExecStart=/opt/piano-booking/vpn_health.sh
"""
tmr = """[Unit]
Description=Probe VPN tunnel end-to-end every minute and self-heal

[Timer]
OnBootSec=2min
OnUnitActiveSec=60s
AccuracySec=5s
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
