# piano-room-booking（浙大琴房自动预约 · 多人共享版）

浙江大学「文艺生活」平台（myarts.zju.edu.cn）琴房自动预约：每天早上放票时刻（默认 08:00，提前 `advance_days` 天放票）自动登录 CAS、按优先级逐人提交预约；约满自动进入**候补队列捡漏**；带手机网页控制台，支持**多人注册共享**，在手机上就能增删每周计划、单次计划、取消订单、查看候补与签到状态。

> 本项目最初是给作者自己用的完整工具（不是从零为开源写的），随本仓库开源，按"现状"提供。项目里未注册为 skill 的部分（服务器部署脚本、健康自愈脚本）一并收录，供参考改造。

## 功能与适用场景

- **自动抢票**：放票时刻（默认 08:00:00）按优先级升序逐人提交计划；未成功按 `retry_seconds` 自动重试（放出的票经常秒空）。
- **候补捡漏（watch）**：抢票约满的目标自动进入候补队列，短间隔持续扫描，有人取消即自动补约；也可在网页控制台对任意计划手动点「候补」。
- **多人共享（2026-10 起）**：同学通过注册入口用统一认证学号密码验证后获得个人专属链接，功能与站长相同（仅限本人数据）；站长有总控制台看所有人数据、调优先级、代约代取消。
  - **成员密码绝不落盘**：密码只在注册/续期请求线程内存中完成一次 CAS 验证，服务器仅保存登录后的会话 cookie（600 权限）。
  - **优先级只决定顺序**（先约者先挑房间/先捡空位），绝不取消任何用户已成功的订单。
  - 注册接口熔断：学号 1h/2 次、IP 1h/10 次凭据失败；CAS 登录全局串行。
- **会话保活**：对有近期计划/候补的用户定期轻量请求，防学校网关把闲置约 3 小时的会话踢掉——成员无需常驻密码也能在放票时刻可用。
- **入场签到监控**：开始后 `checkin_late_min` 分钟内未签到会被学校端判爽约，后台线程轮询签到状态，检测到签到/爽约记录动态（推送给站长）。
- **飞书通知**：抢票结果、候补成功/放弃、会话失效等经飞书群自定义机器人推送（只推站长配置的 webhook，成员端永远没有推送）。
- **平台限定**：浙江大学 myarts「文艺生活」平台的琴房模块（金桥门禁），其他学校接口不同不可直接用；预约需要校园网环境，公网服务器部署需自建隧道（见下）。

## 文件说明

| 文件 | 作用 |
|---|---|
| `piano_booking.py` | 核心模块：CAS 登录、查琴房与可约时段、提交预约/取消、计划校验、飞书推送；也可直接 `--status` / `--run-now` 单机使用 |
| `piano_web.py` | 多人共享版网页控制台 + 统一调度中心（纯标准库 + requests）。单进程内三个守护线程：`scheduler`（放票调度）、`watch`（候补扫描）、`keepalive`（会话保活）——**接管了**原来的 systemd timer 和独立候补守护进程 |
| `piano_watch.py` | 独立候补守护进程（旧方案，已被 `piano_web.py` 内的 watch 线程取代；单机模式不想跑网页时可用） |
| `vpn_health.sh` | 隧道深度健康自愈脚本：每分钟经代理真实探测端到端连通，连续失败才重启 zju-connect / piano-web（防误杀 08:00 调度） |
| `config.example.json` | 配置模板，复制为 `config.json` 使用 |
| `deploy_vpn.py` | 参考部署脚本：上传脚本+配置（加 `proxy` 走隧道）、装 VPN 健康检查 timer、端到端验证 |
| `deploy_web.py` | 参考部署脚本：部署/更新网页控制台 systemd 服务，自动停用旧调度单元防双跑 |

## 依赖

- `piano_booking.py` / `piano_web.py` / `piano_watch.py`：Python 3.10+，`pip install requests`（`piano_web.py` 其余为纯标准库）
- `deploy_vpn.py` / `deploy_web.py`（可选，仅参考）：`pip install paramiko`
- 服务器部署还需要：一台你自己的云服务器、systemd、以及通往校园网的隧道（作者用 [zju-connect](https://github.com/mythkiven/zju-connect) 跑 aTrust 协议，部署见本仓库 [zju-atrust-vpn](../zju-atrust-vpn/) skill）

## 安装部署

### 方式一：本机/单机直接跑（最简单）

有校园网环境的机器（自己的电脑/校内设备）上：

1. 把整个目录复制到 AI 助手技能目录（ZCode：`~/.zcode/skills/`；兼容任何 SKILL.md 约定框架，目录名不变），或当作普通脚本项目放任意位置。
2. `pip install requests`，把 `config.example.json` 复制为同目录 `config.json` 并填入你的账号密码与计划。
3. 手动验证：`python piano_booking.py --status`（查账号状态）、`--run-now --dry-run`（演练不真提交）。
4. 挂定时：systemd timer / cron / Windows 任务计划，每天 `open_time` 前几分钟跑 `python piano_booking.py`（无参数 = 睡到放票时刻自动预约）。

### 方式二：云服务器全自动（作者的实际用法）

公网服务器到校内业务系统不通，需要先解决校园网隧道。**浙大旧 EasyConnect 通道已被下线，现行方案是 zju-connect 跑 aTrust 协议**（HTTP 代理 `http://127.0.0.1:8888` / SOCKS5 `127.0.0.1:1080`），部署与排障见本仓库 [zju-atrust-vpn](../zju-atrust-vpn/) skill。服务器版 `config.json` 比本机版多一个 `"proxy": "http://127.0.0.1:8888"` 字段即可让脚本经隧道访问。

1. 服务器上建工作目录 `/opt/piano-booking`，放 `piano_booking.py` 和填好的 `config.json`。
2. 起好 zju-connect 隧道，确认 `curl -sk -x http://127.0.0.1:8888 https://myarts.zju.edu.cn/` 通。
3. 运行 `python deploy_vpn.py`（环境变量 `PIANO_DEPLOY_HOST` / `PIANO_DEPLOY_USER` / `PIANO_DEPLOY_KEY` 或 `PIANO_DEPLOY_PWD` 指向你自己的服务器）：自动上传脚本与配置、装每分钟端到端探测的 vpn-health timer、做 `--status` 与 dry-run 端到端验证。
4. 运行 `python deploy_web.py` 部署网页控制台（systemd 服务 `piano-web`，端口默认 8039）：脚本会自动 **stop+disable** 旧的 `piano-booking.timer` 与 `piano-watch.service`（调度已由 piano-web 内部线程接管，双跑会打架），然后做回环验证。
5. 访问 `http://<服务器IP>:8039/t/<token>/`（首次启动自动生成 `web_token.txt`，URL 即钥匙；记得放行安全组/防火墙端口，对外建议再挂一层 Nginx HTTPS 反代）。注册入口是 `/reg/<token>/`（`reg_token.txt`，可在总控制台重置）。

两个 deploy 脚本只是作者实际部署流程的记录，路径（`/opt/piano-booking`、端口 8039 等）可直接改，或照着 systemd 单元内容自己手写。

## 需要配置的信息（config.json）

| 字段 | 说明 |
|---|---|
| `username` / `password` | 浙大统一身份认证账号（学号）与密码；**只存本机/服务器，不要提交到任何仓库**。多人共享版允许省略 `password`（成员密码不落盘，会话失效由面板续期） |
| `timezone` | 时区，默认 `Asia/Shanghai` |
| `open_time` | 放票时刻，默认 `08:00` |
| `login_lead_seconds` | 提前多少秒开始登录（默认 25） |
| `advance_days` | 提前几天放票（默认 3，即周一早上放周四的票，以平台实际为准） |
| `campus` | 校区名，如 `紫金港校区` |
| `room_preference` | 琴房优先级列表（从左到右依次尝试），房间名以平台展示为准；多人琴房已被全局过滤（2026-10-27 起禁约） |
| `targets` | 预约计划数组：每周计划 `{"weekday":1,"start":"08:00","end":"10:00"}`（weekday 1=周一）；单次计划把 `weekday` 换成 `"date":"2026-10-12"`。**只允许整点起止、时长 1/2 小时**（学校端限制） |
| `retry_seconds` | 提交失败后的重试间隔（默认 120 秒） |
| `http_timeout` | 请求超时秒数 |
| `proxy` | （可选）服务器部署时走校园网隧道的代理地址 |
| `watch_enabled` | 抢票约满自动进入候补（默认开） |
| `watch_interval` | 候补扫描间隔秒（下限 30，防刷接口） |
| `watch_stop_lead_min` | 距开始不足 N 分钟停止扫描（默认 120；学校取消需提前 30 分钟，之后捡漏价值低） |
| `watch_start_lead_min` | 距开始 N 分钟内才开始扫（0=入队即扫） |
| `feishu_webhook` | 飞书群自定义机器人 webhook（空=仅记日志不推送） |
| `checkin_watch_enabled` | 入场签到状态监控（默认开，推送仅站长） |
| `checkin_interval` / `checkin_late_min` / `checkin_remind_min` | 签到轮询间隔 / 签到窗口 / 开场 N 分钟未签到即提醒 |

## 基本用法

```bash
python piano_booking.py --status             # 查看账号现有预约和接下来可约的目标日期
python piano_booking.py --run-now            # 立即尝试预约(手动触发)
python piano_booking.py --run-now --dry-run  # 演练: 走全流程但不真正提交
python piano_booking.py                      # 睡到放票时刻自动预约(给 systemd timer 用)
python piano_watch.py                        # 独立候补守护(单机模式; 服务器模式由 piano_web 接管)
```

网页控制台（部署后）：

- 站长 `/t/<token>/`：放票倒计时、已有订单（未使用可一键取消）、每周/单次计划、立即预约、候补、会话状态；`/t/<token>/admin` 总控制台：所有用户计划/订单/门禁密码/候补/动态、优先级调整、启停/删除/代约代取消、注册链接管理。
- 成员 `/u/<token>/`：与站长相同的功能，仅限本人数据；会话失效时在「会话与安全」重新输密码续期。
- 页面自动刷新；所有链接 token 为 24 位 hex，**URL 即凭据，不要外传**。

## 注意事项

- **凭据安全**：站长 CAS 学号密码只存在你自己的 `config.json` 里（已被本仓库 `.gitignore` 排除）；成员密码绝不落盘；`web_token.txt` / `reg_token.txt` / `users/` 目录都是敏感运行时文件，别提交、别外传。
- **取消订单走官方后台服务接口**：App 内取消琴房可能报「删除失败」（门禁服务商侧问题），本项目已改走官方 `booking_admin_action_service.save_booking_order_status` 路径，门禁撤销失败时自动继续。
- **单日时长上限**：学校端按人按使用日累计 2 小时封顶（与订单数无关），触顶后换房间/重试都无用。
- **合规使用**：这只是把"每天早上手动抢琴房"自动化，请遵守平台规则，预约后不用记得取消，把资源留给同学。
- 开源版未包含作者服务器上的抓包记录（`explore/` 目录）、运行日志与任何真实用户数据；接口地址与报文格式核心结论都写在 `piano_booking.py` 的注释里。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
