# piano-room-booking（浙大琴房自动预约）

浙江大学「文艺生活」平台（myarts.zju.edu.cn）琴房自动预约：每天早上放票时刻（默认 08:00，提前 `advance_days` 天放票）自动登录 CAS、按你配置的计划提交预约，并带一个手机网页控制台，在手机上就能增删每周计划、单次计划、取消订单。

> 本项目最初是给作者自己用的完整工具（不是从零为开源写的），随本仓库开源。项目里未注册为 skill 的部分（服务器部署脚本）一并收录，供参考改造。

## 功能与适用场景

- **自动抢票**：systemd timer 每天 07:59 前登录，08:00:00 按计划提交；未成功按 `retry_seconds` 自动重试（放出的票经常秒空）。
- **计划系统**：「每周重复」计划（如每周一 08:00-10:00）+「指定日期」单次计划（自动替代当天的每周计划，也可标记某天"这天不约"）。
- **手机网页控制台**（`piano_web.py`）：查看放票倒计时与已有订单、增删计划、对已放票计划点「立即预约」、对未使用订单一键取消；链接含随机 token（`web_token.txt`），仅凭链接可操作；页面每 60 秒自动刷新。
- **平台限定**：浙江大学 myarts「文艺生活」平台的琴房模块（金桥门禁），其他学校接口不同不可直接用；预约需要校园网环境，公网服务器部署需自建隧道（见下）。

## 文件说明

| 文件 | 作用 |
|---|---|
| `piano_booking.py` | 核心脚本：CAS 登录、查琴房与可约时段、提交预约、重试、计划计算（含 `--status` / `--run-now` / `--dry-run`） |
| `piano_web.py` | 手机网页控制台（纯标准库，部署为 systemd 服务 `piano-web`） |
| `config.example.json` | 配置模板，复制为 `config.json` 使用 |
| `deploy_vpn.py` | 参考部署脚本：上传脚本+配置到服务器（加 `proxy` 走隧道）、装 VPN 健康检查 timer、端到端验证 |
| `deploy_web.py` | 参考部署脚本：部署/更新网页控制台的 systemd 服务 |

## 依赖

- `piano_booking.py`：Python 3.10+，`pip install requests`
- `piano_web.py`：纯 Python 标准库，无额外依赖
- `deploy_vpn.py` / `deploy_web.py`（可选，仅参考）：`pip install paramiko`
- 服务器部署还需要：一台你自己的云服务器、Docker（跑 EasyConnect 容器建校园网隧道）、systemd

## 安装部署

### 方式一：本机直接跑（最简单）

有校园网环境的机器（自己的电脑/校内设备）上：

1. 把整个目录复制到 AI 助手技能目录（ZCode：`~/.zcode/skills/`；兼容任何 SKILL.md 约定框架，目录名不变），或当作普通脚本项目放任意位置。
2. `pip install requests`，把 `config.example.json` 复制为同目录 `config.json` 并填入你的账号密码与计划。
3. 手动验证：`python piano_booking.py --status`（查账号状态）、`--run-now --dry-run`（演练不真提交）。
4. 挂定时：systemd timer / cron / Windows 任务计划，每天 `open_time` 前几分钟跑 `python piano_booking.py`（无参数 = 睡到放票时刻自动预约）。

### 方式二：云服务器全自动（作者的实际用法）

公网服务器到校内业务系统不通，需要先解决校园网隧道，作者用的是 docker 里的 EasyConnect 容器（`-e CLI_OPTS` 传入浙大上网账号），在宿主机上暴露一个 SOCKS/HTTP 代理（如 `http://127.0.0.1:8888`），服务器版 `config.json` 比本机版多一个 `"proxy": "http://127.0.0.1:8888"` 字段即可让脚本经隧道访问。

1. 服务器上建工作目录 `/opt/piano-booking`，放 `piano_booking.py` 和填好的 `config.json`。
2. 运行 EasyConnect 容器，确认 `curl -sk -x http://127.0.0.1:8888 https://myarts.zju.edu.cn/...` 通。
3. 设置环境变量 `PIANO_DEPLOY_HOST` / `PIANO_DEPLOY_USER` / `PIANO_DEPLOY_PWD`（指向你自己的服务器）后运行 `python deploy_vpn.py`：自动上传脚本与配置、写 VPN 健康检查脚本（放票前 07:40 + 每 6 小时自愈重启隧道容器）、装 systemd timer、做 `--status` 与 dry-run 端到端验证。
4. 运行 `python deploy_web.py` 部署网页控制台（systemd 服务 `piano-web`，端口默认 8039），首次启动自动生成 `web_token.txt`，用 `http://<服务器IP>:8039/t/<token>/` 访问（记得放行安全组/防火墙端口）。

两个 deploy 脚本只是作者实际部署流程的记录，路径（`/opt/piano-booking`、端口 8039 等）可直接改，或照着 systemd 单元内容自己手写。

## 需要配置的信息（config.json）

| 字段 | 说明 |
|---|---|
| `username` / `password` | 浙大统一身份认证账号（学号）与密码；**只存本机/服务器，不要提交到任何仓库** |
| `timezone` | 时区，默认 `Asia/Shanghai` |
| `open_time` | 放票时刻，默认 `08:00` |
| `login_lead_seconds` | 提前多少秒开始登录（默认 25） |
| `advance_days` | 提前几天放票（默认 3，即周一早上放周四的票，以平台实际为准） |
| `campus` | 校区名，如 `紫金港校区` |
| `room_preference` | 琴房优先级列表（从左到右依次尝试），房间名以平台展示为准 |
| `targets` | 预约计划数组：每周计划 `{"weekday":1,"start":"08:00","end":"10:00"}`（weekday 1=周一）；单次计划把 `weekday` 换成 `"date":"2026-10-12"` |
| `retry_seconds` | 提交失败后的重试间隔（默认 120 秒） |
| `http_timeout` | 请求超时秒数 |
| `proxy` | （可选）服务器部署时走校园网隧道的代理地址 |

## 基本用法

```bash
python piano_booking.py --status             # 查看账号现有预约和接下来可约的目标日期
python piano_booking.py --run-now            # 立即尝试预约(手动触发)
python piano_booking.py --run-now --dry-run  # 演练: 走全流程但不真正提交
python piano_booking.py                      # 睡到放票时刻自动预约(给 systemd timer 用)
```

网页控制台（部署后）：查看 3 天放票窗口与倒计时、已有订单（未使用的可一键取消）、增删每周/单次计划、对已放票计划「立即预约」。

## 注意事项

- **凭据安全**：CAS 学号密码只存在你自己的 `config.json` 里（已被本仓库 `.gitignore` 排除），网页控制台链接里的 token 等于钥匙，不要外传。
- **取消订单走官方后台服务接口**：App 内取消琴房可能报「删除失败」（门禁服务商侧问题），本项目已改走官方 `booking_admin_action_service.save_booking_order_status` 路径，门禁撤销失败时自动继续。
- **合规使用**：这只是把"每天早上手动抢琴房"自动化，请遵守平台规则，预约后不用记得取消，把资源留给同学。
- 开源版未包含作者服务器上的抓包记录（`explore/` 目录）与运行日志；接口地址与报文格式核心结论都写在 `piano_booking.py` 的注释里。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
