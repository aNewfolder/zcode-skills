---
name: piano-room-booking
description: 浙大「文艺生活」平台（myarts.zju.edu.cn）琴房自动预约与订单管理（多人共享版）。当用户提到琴房预约/抢琴房/候补捡漏、查看琴房订单与放票情况、添加/修改/取消预约计划（每周或指定日期）、签到状态，或要部署/检查自动抢票服务（piano-web 统一调度、服务器隧道）时使用。凭据在 config.json（学号密码），只在本机/用户自己的服务器上使用。
---

# piano-room-booking（浙大琴房自动预约）

核心脚本 `piano_booking.py` 负责登录与抢票（也可当库被 `piano_web.py` / `piano_watch.py` 复用），`piano_web.py` 是多人共享版网页控制台兼统一调度中心（内部 scheduler/watch/keepalive 三线程，接管了原 systemd timer 与独立候补守护），`config.json` 存账号与计划（首次使用从 `config.example.json` 复制并填写）。**完整的部署说明（本机模式 / 云服务器 + 校园网隧道模式）、配置字段表、多人共享机制、注意事项都在 `README.md`，AI 部署前先通读它。**

常用命令：

```bash
python piano_booking.py --status             # 查看账号现有预约和可约目标日期
python piano_booking.py --run-now --dry-run  # 演练全流程不真提交
python piano_booking.py --run-now            # 立即尝试预约
```

要点：

- 计划分两种写进 `config.json` 的 `targets`：每周计划 `{"weekday":1,"start":"08:00","end":"10:00"}`、单次计划 `{"date":"2026-10-12","start":"14:00","end":"16:00"}`；**只允许整点起止、时长 1/2 小时**。服务器部署后计划以服务器配置为准（网页控制台在线维护）。
- `--run-now` 会真实提交预约；拿不准就先 `--dry-run`。
- 约满会自动进候补队列（`watch_*` 配置），有人取消即自动补约；网页控制台也可手动点「候补」。
- 服务器部署模式（公网服务器 + zju-connect aTrust 隧道 + 网页控制台）见 `deploy_vpn.py` / `deploy_web.py`、`vpn_health.sh` 与 README，SSH 凭据通过 `PIANO_DEPLOY_HOST/USER/KEY/PWD` 环境变量传入。隧道本身部署排障见本仓库 `zju-atrust-vpn` skill。
- 多人共享：成员经注册入口验证后获得个人链接，密码绝不落盘；优先级只决定抢票/捡漏顺序，绝不取消他人已成功订单。
