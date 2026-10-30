---
name: zju-atrust-vpn
description: "通过服务器上的 zju-connect(aTrust 协议) 隧道完成浙大校内网任务：访问 myarts.zju.edu.cn 等校内系统、跑校内 API、诊断/修复/重建校网 VPN。触发词：校网、校内网、校内系统、浙大 VPN、VPN 挂了/接口错误/连不上校内、myarts、aTrust、zju-connect、EasyConnect 报错、rvpn、在校外访问校园网。凡需要从校外触达浙大校内网络资源时使用。"
---

# 通过 aTrust(zju-connect) 完成浙大校内网任务

## 架构（2026-10-04 定稿）

浙大 2025 年起把 RVPN 从 EasyConnect 迁到 aTrust；2026-10 前后旧 EasyConnect 通道被半下线（认证能过、配置下发失败 GetConfig 10015、隧道 ~2 分钟被杀且拒绝续期 `server does not support update_session`）。**任何 EasyConnect 容器/客户端方案都已失效，不要再修**。现行方案：

```
[任务] --代理--> 服务器上的 zju-connect(-protocol atrust) --VPN(aTrust)--> 校内系统
[手机] --IKEv2(UDP500/4500)--> strongswan --> sing-box tun(嗅探域名) --> zju-connect socks:1080 --> 校内（手机不登 aTrust，见"手机共享隧道"）
```

- aTrust 服务地址 `vpn.zju.edu.cn:443`，Radius 域账号密码登录（学号 + RVPN 独立密码）
- 凭据位置：自选的部署目录（如 `/opt/zju-connect/`），密码写在 systemd unit `zju-connect.service` 里（`chmod 600`）
- 设备信任文件 `client_data.json`：首次登录（点选验证码+短信）后生成，此后**跨服务器、免验证码、免短信静默登录**——这是 aTrust 方案能全自动的关键
- 会话稳定：实测 12min+ 零掉线（easyconnect 协议同版本 ~2 分钟必掉）

**铁律：aTrust 同账号不要在两台机器同时在线**（会互踢）。换机时先停旧机的 zju-connect。**手机同理：手机不装/不登 aTrust App**，校内访问一律走下面的共享隧道——手机一登会把服务器会话顶掉。

## 在服务器上跑校网任务

```bash
# 探活（假设 HTTP 代理绑在 127.0.0.1:8888）
curl -sk -x http://127.0.0.1:8888 -o /dev/null -w '%{http_code}\n' \
  https://myarts.zju.edu.cn/service/api/sso/login-url     # 200 = 通

# 任意 curl / python requests 都走代理
curl -sk -x http://127.0.0.1:8888 https://myarts.zju.edu.cn/...
# requests: proxies={"http":"http://127.0.0.1:8888","https":"http://127.0.0.1:8888"}
```

访问 myarts **必须**让 zju-connect 带 `-custom-dns myarts.zju.edu.cn:10.204.20.131 -custom-proxy-domain myarts.zju.edu.cn`（service unit 内置）：myarts 校内真实 IP 是 10.204.20.131，公网入口 210.32.129.80 对业务接口返回 410，且该域名公网 NXDOMAIN。其他校内域名若解析不到，同样加 custom-dns/custom-proxy-domain。

## 在本机(Windows)临时访问校网

```bash
ssh -N -L 8888:127.0.0.1:8888 <你的服务器>    # 保持窗口开着
```
然后浏览器/程序把 HTTP 代理设为 `http://127.0.0.1:8888`（SOCKS5 则映射 1080）。用完关窗口即可。

## 手机共享隧道（系统自带 IKEv2 VPN，可选进阶）

链路：手机系统 VPN（IKEv2/IPSec PSK，**常开 + 客户端分流**）--UDP 500/4500--> strongswan --> sing-box tun（sbphone：DNS 劫持→校内容 DNS；10/8→zju-connect；其余→direct 出公网）--> 校内。aTrust 会话仍只有服务器一个；手机端零第三方 App。**推荐形态：手机 VPN 高级选项里填 `DNS server=<VPN 内 DNS>` + `Forwarding routes=10.0.0.0/8`**——手机端自己分流：浙大流量进隧道、其余流量手机直连满速，VPN 可永远开着不用管；服务器端术语策略保持 0.0.0.0/0 自动兼容（清空手机这两个字段则回到全局隧道模式）。

- **strongswan**：`/etc/ipsec.conf` 两个 conn：`phone-psk`（双向 PSK，鸿蒙首选——鸿蒙的 MSCHAPv2 UI 没有用户名/密码输入框；标识符必填但服务器不校验）+ `phone`（EAP-MSCHAPv2 备用，供有完整字段的客户端）。均分流 `10.0.0.0/8`、压 DNS=VPN 内 DNS；PSK/EAP 凭据都在 `/etc/ipsec.secrets`（600）。systemd `strongswan-starter`，drop-in 保证启动后 `ipsec update` 加载连接定义。`ipsec statusall` 看 SAs，`journalctl -u strongswan-starter` 看认证失败
- **VPN 内 DNS**：dnsmasq 绑一个 lo 地址（如 10.99.0.1）；`zju.edu.cn`/`cc98.org` 两个 zone 转发给 zju-connect 的校内容 DNS（`--dns-server-bind 127.0.0.1:5353`，会用浙大 DNS 解析——tyys 等系统校内解析=内网 IP(10.x)，这样才能进隧道走 VPN；公网 DNS 给的 210.32.x 手机直连必被拒）。其余域名走公共 DNS。lo 上的 10.99.0.1/32 用一个小 systemd service 挂载
- **手机全局解析（核心机制）**：鸿蒙不认 IKEv2 下发的 DNS、华为浏览器还会用自带 HTTPDNS——所以 sing-box 在 tun 里**劫持全部 DNS**（规则顺序必须是 `[sniff, protocol:dns→hijack-dns, port:853→reject, ip_cidr:10/8→zju]`），统一用 zju-connect 的校内容 DNS 回答：浙大/cc98 域名→内网 IP→走 VPN；其余→公网 IP→直连出公网。调手机解析问题：sing-box 日志临时调 info，能看到每笔 `dns: exchanged A 域名 → IP`
- **zju-connect 路由边界（实测）**：10.x 目的 → VPN ✓；210.32.x 等公网 IP → DIRECT（`--proxy-all` 强送 VPN 会 000 失败，aTrust 网关不受理按 IP 连公网，勿用）；`--skip-domain-resource` 不改变 IP 路由。所以浙大系统的手机可达性取决于 DNS 给的是不是内网 IP
- **分流**：sing-box tun 实例（tun 全局模式：DNS 劫持+10/8 走 zju-connect，其余 direct）。**两个血泪坑**：① route 规则里 `protocol: dns`/`port` 条件生效的前提是**第一条规则必须是 `{"action":"sniff"}`**（没有 sniff 就识别不出协议，DNS 劫持静默失效、查询走 direct）；② 策略路由表 100 的 `default dev <tun>` 挂在 tun 设备上，**sing-box 重启会随设备一起蒸发**——必须在 sing-box unit 的 ExecStartPost 里等设备起来后重挂，别放转发表里
- 备用通道：sing-box ss2022 入口可保留给 FlClash/ClashBox，安全组不开即休眠
- 已知限制：**人在校内 Wi-Fi 时把 VPN 关掉**（10/8 路由会绕路）；华为浏览器自带 HTTPDNS 绕过系统解析，修好后第一次访问仍可能拿旧解析——清后台重开浏览器或换浏览器即可
- **已实测可用**：CC98 网页+登录 ✓、yunfeng.zju.edu.cn ✓、myarts（服务端）✓、tyys 类走校内 DNS 解析的站点 ✓。新站点进不去先看 sing-box 日志有没有它内网 IP 的连接；CC98 的"非校园网"判定绑在登录会话上——走隧道后须**退出重登**才刷新

## 状态检查与运维

```bash
systemctl status zju-connect        # active 且日志见 "KeepAlive using UDP: OK" 为健康
systemctl restart zju-connect       # 重连（~10s 就绪）
journalctl -u zju-connect -n 50     # 排障
```
配合每分钟端到端探活 + 自愈的 health timer（参考本仓库 piano-room-booking 的 `vpn_health.sh`）即全自动，不需要人盯。

## 部署/重建

手动要点：下载 zju-connect release 到部署目录 → 首次交互登录生成 client_data.json（见下节）→ 写 systemd unit → enable --now。关键参数：

```
-protocol atrust -server vpn.zju.edu.cn -port 443
-username <学号> -password <RVPN独立密码>
-client-data-file /opt/zju-connect/client_data.json
-custom-dns myarts.zju.edu.cn:10.204.20.131 -custom-proxy-domain myarts.zju.edu.cn
-http-bind 127.0.0.1:8888 -socks-bind 127.0.0.1:1080
```

systemd unit 模板（注意 600 权限防凭据泄露）：

```ini
[Unit]
Description=ZJU aTrust VPN proxy via zju-connect
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/opt/zju-connect/zju-connect -protocol atrust -server vpn.zju.edu.cn -port 443 -username <学号> -password <RVPN独立密码> -client-data-file /opt/zju-connect/client_data.json -custom-dns myarts.zju.edu.cn:10.204.20.131 -custom-proxy-domain myarts.zju.edu.cn -http-bind 127.0.0.1:8888 -socks-bind 127.0.0.1:1080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## client_data 失效时：重新交互登录

现象：日志反复出现 graph check code / SMS 提示，起不来。在服务器上：

```bash
setsid nohup /opt/zju-connect/atrust_login.sh > /dev/null 2>&1 &
# stdin 接的是 `tail -f /tmp/zcin.txt`，永不 EOF；答案全部"追加"到该文件
```

1. **点选验证码**：等 `/tmp/captcha.png` 出现（JPEG，尺寸要从文件头解析，别信扩展名），取回本地用 PIL 放大 3 倍叠 20px 网格读坐标，把 `{"coordinates":[[x,y],...],"width":W,"height":H}`（按题目顺序点击）追加到 `/tmp/zcin.txt`。坐标错会报 `Login error: expected newline`（实际是验证失败）——重跑脚本换一张图再试
2. **短信验证码**：发到绑定手机，人工要来后以 `$<验证码>` 追加到 `/tmp/zcin.txt`（`$` 前缀=信任设备，跳过后续二次验证）
3. 成功标志：日志出现 `Client data saved` + `VPN client started`。之后一切回归静默

## 故障速查

| 症状 | 原因 | 处置 |
|---|---|---|
| 代理 curl 返回 000 | 服务挂/刚重启未就绪 | 等 10s 重试；`systemctl restart zju-connect` |
| 日志 `GetConfig 10015` / `svpn stop!` 循环 | EasyConnect 旧通道被下线 | 换 aTrust，别修旧通道 |
| 日志 `server does not support update_session` | 网关拒绝会话续期（旧通道） | 同上 |
| myarts 请求 410 | 走了公网入口 210.32.129.80 | 确认 unit 带 custom-dns → 10.204.20.131 |
| 反复要验证码/短信 | client_data.json 丢失或失效 | 重走"重新交互登录" |
| aTrust 同账号时断时续 | 两台机器同时在线互踢 | 停掉一台的 zju-connect |
| **VPN 已连上但全部不通** | **Docker 把 FORWARD 链设成 DROP，转发包被丢** | `iptables -S FORWARD | head -1` 看 policy；放行规则加在 DOCKER-USER + FORWARD |
| 手机 VPN 连上但打不开 myarts | 手机 DNS 未走劫持或浏览器 HTTPDNS 缓存 | sing-box 日志调 info 看有无该站内网 IP 连接；浏览器清后台重开 |
| Nginx 502（反代的服务） | 上游本机服务没起 | 查对应 systemd 服务，别动 Nginx |

## 历史坑（勿重蹈）

- 服务器迁移收尾时旧机容器被 stop、timer 被 disable，导致两天没抢到票且控制台全报错——**收尾必须核对所有 systemd 单元的 enabled/active 状态**
- 用 `pkill -f 'protocol atrust'` 时匹配串撞上自身命令行把自己杀了——用 `pkill -f 'protocol[ ]atrust'`
- 用 SSH 别名管理多台服务器时，对"旧服务器"操作务必用显式主机名/IP，曾因此停错机器的服务
