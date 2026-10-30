# zju-atrust-vpn（浙大 aTrust 校网隧道）

从校外服务器触达浙大校内网络的隧道方案与运维手册。核心是用 [zju-connect](https://github.com/mythkiven/zju-connect) 跑浙大新版 RVPN 的 **aTrust 协议**，在服务器上起一个本地 HTTP/SOCKS 代理，之后任何程序（curl、requests、其他 skill）走代理即可访问 myarts.zju.edu.cn 等校内系统。

**给 AI 看的完整手册（架构、手机共享隧道、故障速查、历史坑）在同目录 [`SKILL.md`](SKILL.md)；本 README 写给人看：怎么部署、要准备什么。**

## 背景：为什么是 aTrust

- 浙大 RVPN 已从 EasyConnect 迁到 aTrust，旧 EasyConnect 通道 2026-10 前后被半下线：认证能过，但配置下发失败（`GetConfig 10015`）、隧道约 2 分钟被杀且拒绝续期。**任何 EasyConnect 容器/客户端方案都不要再修**。
- aTrust 方案实测稳定（12 分钟+零掉线），且首次登录后的设备信任文件（`client_data.json`）可让后续**免验证码、免短信静默登录**——这是能全自动的关键。

## 前置准备（需要你提供的信息）

| 项 | 说明 |
|---|---|
| 学号 | 浙大统一身份认证账号 |
| RVPN 独立密码 | 不是统一认证密码。在浙大 IT 服务门户（its.zju.edu.cn）的 RVPN/aTrust 服务里单独设置/重置 |
| 一台云服务器/常驻主机 | 有公网 IP 更佳；Linux + systemd |
| 绑定手机 | 首次交互登录要收一次短信验证码（之后信任文件长期有效） |

## 安装步骤

1. **下载 zju-connect** release 二进制，放到部署目录（下文以 `/opt/zju-connect/` 为例）。
2. **首次交互登录**，生成设备信任文件 `client_data.json`：
   - 用一个 stdin 永不 EOF 的方式起 zju-connect（如 `tail -f /tmp/zcin.txt | ./zju-connect ...`），按日志提示：
   - 点选验证码：日志会落一张 `/tmp/captcha.png`，取回本地看图，把点击坐标以 JSON 追加进 `/tmp/zcin.txt`；
   - 短信验证码：以 `$<验证码>` 追加（`$` 前缀 = 信任此设备，跳过后续二次验证）；
   - 日志出现 `Client data saved` + `VPN client started` 即成功，此后回归静默。
3. **写 systemd unit**（完整模板见 SKILL.md），关键参数：

   ```
   -protocol atrust -server vpn.zju.edu.cn -port 443
   -username <学号> -password <RVPN独立密码>
   -client-data-file /opt/zju-connect/client_data.json
   -custom-dns myarts.zju.edu.cn:10.204.20.131 -custom-proxy-domain myarts.zju.edu.cn
   -http-bind 127.0.0.1:8888 -socks-bind 127.0.0.1:1080
   ```

   注意 `chmod 600` unit 文件（里面有密码）。`-custom-dns`/`-custom-proxy-domain` 必须带：myarts 校内真实 IP 是 10.204.20.131，走公网入口业务接口会 410。其他校内域名解析不到时照葫芦画瓢加。
4. **启用并验证**：

   ```bash
   systemctl enable --now zju-connect
   curl -sk -x http://127.0.0.1:8888 -o /dev/null -w '%{http_code}\n' \
     https://myarts.zju.edu.cn/service/api/sso/login-url    # 200 = 通
   ```

5. **（推荐）健康自愈**：把本仓库 piano-room-booking 里的 `vpn_health.sh` 挂成每分钟 systemd timer——经代理真实请求 myarts，连续失败才重启 zju-connect（防学校侧维护期间反复重启误杀在途连接），顺带检查业务服务健康。

## 使用

- 服务器上任何程序：HTTP 代理 `http://127.0.0.1:8888`，SOCKS5 `127.0.0.1:1080`。
- 本机（Windows/Mac）临时用：`ssh -N -L 8888:127.0.0.1:8888 <你的服务器>` 保持窗口，浏览器代理指向 `127.0.0.1:8888`。
- 手机进阶玩法（IKEv2 共享隧道、DNS 劫持分流）：见 SKILL.md「手机共享隧道」一节。

## 注意事项

- **同账号互踢**：aTrust 同一账号不要两台机器同时在线（含手机装 aTrust App 登录——手机一登会把服务器会话顶掉）。换机先停旧机。
- 凭据只在服务器 systemd unit 里，别写进任何仓库；`client_data.json` 等同登录态，同样只留服务器。
- 状态健康判据：`systemctl status zju-connect` active 且日志有 `KeepAlive using UDP: OK`；常见故障对照 SKILL.md 的速查表。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
