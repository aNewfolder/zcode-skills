# -*- coding: utf-8 -*-
"""部署 piano_web 服务到服务器并做回环测试。"""
import json
import os
import time
from pathlib import Path
import paramiko

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
        print("$ " + cmd[:110] + f"  (exit={code})")
        o = out.read().decode().strip()
        e = err.read().decode().strip()
        if o:
            print(o[:2200])
        if e:
            print("[stderr]", e[:400])
        print()
    return o


# 1. 上传(与本脚本同目录)
HERE = Path(__file__).resolve().parent
sftp = ssh.open_sftp()
sftp.put(str(HERE / "piano_booking.py"),
         "/opt/piano-booking/piano_booking.py")
sftp.put(str(HERE / "piano_web.py"),
         "/opt/piano-booking/piano_web.py")
sftp.close()
print("=== 文件已上传 ===\n")

# 2. systemd 服务
svc = """[Unit]
Description=Piano booking web console
After=network-online.target docker.service
Wants=network-online.target

[Service]
WorkingDirectory=/opt/piano-booking
ExecStart=/usr/bin/python3 /opt/piano-booking/piano_web.py
Restart=always
RestartSec=5
Environment=PIANO_WEB_PORT=8039
"""
run("cat > /etc/systemd/system/piano-web.service <<'EOF'\n" + svc + "EOF\n"
    "systemctl daemon-reload && systemctl enable --now piano-web.service")
run("sleep 2; systemctl is-active piano-web.service; cat /opt/piano-booking/web_token.txt")

# 3. 本机回环测试 state
run("curl -s http://127.0.0.1:8039/t/$(cat /opt/piano-booking/web_token.txt)/api/state | head -c 900")
ssh.close()
print("=== 部署完成, 进入回环测试 ===")
