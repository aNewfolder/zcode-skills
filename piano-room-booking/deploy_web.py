# -*- coding: utf-8 -*-
"""部署多用户共享版琴房网页控制台到服务器并做回环验证。

2026-10-16 起为多人共享版(注册/个人链接/统一调度/优先级/总控制台):
优先级只决定抢票/捡漏顺序, 绝不取消他人已成功订单; 飞书通知只发站长
webhook。
  - 每天 08:00 抢票改由 piano_web 内调度线程执行(接管 piano-booking.timer)
  - 候补扫描改由 piano_web 内 watch 线程执行(接管 piano-watch 守护)
  - 部署时对两个旧单元 stop+disable, 避免双跑(教训 2026-10-02:
    收尾必须确认所有 systemd 单元状态, 故本脚本回环验证里含单元状态检查)

凭据与服务器信息一律从环境变量读取（同 deploy_vpn.py）：
  PIANO_DEPLOY_HOST / PIANO_DEPLOY_USER / PIANO_DEPLOY_KEY / PIANO_DEPLOY_PWD
"""
import os
from pathlib import Path

import paramiko

HOST = os.environ.get("PIANO_DEPLOY_HOST", "")
USER = os.environ.get("PIANO_DEPLOY_USER", "root")
KEY = os.environ.get("PIANO_DEPLOY_KEY",
                     str(Path.home() / ".ssh" / "id_ed25519"))
PWD = os.environ.get("PIANO_DEPLOY_PWD", "")
if not HOST or not (os.path.isfile(KEY) or PWD):
    raise SystemExit("请先设置环境变量 PIANO_DEPLOY_HOST / PIANO_DEPLOY_USER(默认 root) "
                     "/ PIANO_DEPLOY_KEY 或 PIANO_DEPLOY_PWD 指向你自己的服务器")
kwargs = {"key_filename": KEY} if os.path.isfile(KEY) else {"password": PWD}
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, timeout=20, **kwargs)


def run(cmd, timeout=180, show=True):
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    code = out.channel.recv_exit_status()
    o = out.read().decode().strip()
    e = err.read().decode().strip()
    if show:
        print("$ " + cmd[:110] + f"  (exit={code})")
        if o:
            print(o[:2200])
        if e:
            print("[stderr]", e[:400])
        print()
    return o


# 0. 备份旧版(可回滚)
run("cd /opt/piano-booking && for f in piano_booking.py piano_web.py; do "
    "[ -f $f ] && cp -a $f $f.bak-deploy; done; true")

# 1. 上传(与本脚本同目录)
HERE = Path(__file__).resolve().parent
sftp = ssh.open_sftp()
sftp.put(str(HERE / "piano_booking.py"),
         "/opt/piano-booking/piano_booking.py")
sftp.put(str(HERE / "piano_web.py"), "/opt/piano-booking/piano_web.py")
sftp.close()
print("=== 文件已上传 ===\n")

# 2. 停用旧调度/候补单元(由 piano_web 内线程接管, 防双跑)
run("systemctl stop piano-watch.service piano-booking.timer 2>/dev/null; "
    "systemctl disable piano-watch.service piano-booking.timer 2>/dev/null; "
    "echo -n 'piano-watch='; systemctl is-active piano-watch.service; "
    "echo -n 'piano-booking.timer='; systemctl is-active piano-booking.timer; true")

# 3. systemd 服务 + 重启
svc = """[Unit]
Description=Piano booking web console
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/piano-booking
ExecStart=/usr/bin/python3 /opt/piano-booking/piano_web.py
Restart=always
RestartSec=5
Environment=PIANO_WEB_PORT=8039
"""
run("cat > /etc/systemd/system/piano-web.service <<'EOF'\n" + svc + "EOF\n"
    "systemctl daemon-reload && systemctl enable --now piano-web.service"
    " && systemctl restart piano-web.service")
run("sleep 2 && echo -n 'piano-web=' && systemctl is-active piano-web.service")

# 4. 回环验证
run("curl -s http://127.0.0.1:8039/healthz; echo")
tok = run("cat /opt/piano-booking/web_token.txt", show=False)
run(f"T={tok}; "
    f"curl -s -o /dev/null -w 'owner-page  %{{http_code}}\\n' "
    f"http://127.0.0.1:8039/t/$T/; "
    f"curl -sL -o /dev/null -w 'admin-page(无斜杠301->200)  %{{http_code}}\\n' "
    f"http://127.0.0.1:8039/t/$T/admin; "
    f"curl -s -o /dev/null -w 'owner-state %{{http_code}}\\n' "
    f"http://127.0.0.1:8039/t/$T/api/state")
run("sleep 4; curl -s http://127.0.0.1:8039/healthz; echo; "
    "journalctl -u piano-web -n 6 --no-pager -o cat")
reg = run("cat /opt/piano-booking/reg_token.txt 2>/dev/null", show=False)
run(f"R={reg}; curl -s -o /dev/null -w 'reg-page    %{{http_code}}\\n' "
    f"http://127.0.0.1:8039/reg/$R/")

# 5. 单元状态总检(教训: 部署收尾必须核对全部相关单元)
run("systemctl is-active piano-web; "
    "systemctl is-enabled piano-booking.timer piano-watch.service 2>&1; "
    "systemctl list-timers --no-pager | grep -E 'vpn' || true")

ssh.close()
print("=== 部署完成 ===")
print(f"站长控制台 : http://{HOST}:8039/t/{tok}/  "
      f"(URL 即钥匙, 勿外传; 对外建议挂 Nginx HTTPS 反代)")
print(f"总控制台   : http://{HOST}:8039/t/{tok}/admin")
print(f"注册入口   : http://{HOST}:8039/reg/{reg}/  (分享给同学)")
