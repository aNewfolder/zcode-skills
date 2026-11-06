# zhiyun-lecture-notes（智云课堂讲义）

浙大智云课堂（classroom.zju.edu.cn）课次素材抓取与教材级讲义整理：定位课程与课次，下载每讲 PPT 板书（自动去重动画帧）与官方语音转写，配合回放视频抽帧重建纯板书，按一套严格的撰写规范把素材整理成"读者看这一篇就能完全取代听课"的讲义。

**给 AI 看的完整操作手册（触发条件、红线、撰写规范、旁听与批量串讲流程）在同目录 [`SKILL.md`](SKILL.md)；本 README 写给人看：怎么装、要配什么、怎么用。**

## 能做什么

- **素材抓取**：一门课的全部课次列表（第 N 次课/日期/可回放）、每讲 PPT 板书截图（动画去重后的最终版）、带时间戳的官方语音转写、回放 mp4 直链。
- **纯板书课**：老师全程黑板手写、无 PPT 的课（智云时间轴只有一张教室桌面截图），用 `zyframes.py` 从回放视频抽帧，按"擦板"自动切分板书时代，重建板书序列。
- **旁听未选的课**：智云素材接口不校验选课关系，登录后可全站搜索并直接读任何课程的课次素材（2026-11 实测口径）。
- **整门课批量串讲**：每讲派一个子智能体串行处理，主对话验收，产出带承上启下的整套讲义。

## 依赖

- Python 3.10+，`pip install requests`（`zhiyun.py` / `zjuauth.py`）
- `zyframes.py` 额外需要：`pip install opencv-python numpy Pillow imageio-ffmpeg`（imageio-ffmpeg 自带 ffmpeg 二进制，系统无需装 ffmpeg）
- 一个浙大统一认证账号（学号 + 密码）
- 讲义配图上传图床需要本机 PicGo（配合本仓库 [picgo-image-upload](../picgo-image-upload/) skill）；不用图床也可以本地引用，但笔记仓库里会残留图片文件

## 安装

把整个目录复制到 AI 助手技能目录（ZCode：`~/.zcode/skills/`；其他框架按各自约定），或当作普通脚本项目放任意位置。目录名保持 `zhiyun-lecture-notes` 不变。

## 需要配置的信息

在 `scripts/` 下手动创建 `config.json`（**不要提交到任何仓库**，本仓库 `.gitignore` 已排除）：

```json
{
  "username": "你的学号",
  "password": "统一认证密码",
  "password_verified": true
}
```

- `password_verified` 是安全闸门：脚本发现密码错误后会把该标志自动置 false 并拒绝自动登录（统一认证连续错约 5 次锁号），更正密码后手动改回 true。
- 首次使用可先 `python zhiyun.py login` 验证凭据；会话持久化在 `scripts/sessions/zhiyun.json`，日常业务命令会自动登录，无需先 login。

## 用法

```bash
cd scripts

# 找课
python zhiyun.py courses --keyword 信号 --scope all      # mine=我的课程, all=全站搜索
python zhiyun.py lessons --course <课名或course_id>       # 第N次课/日期/可回放状态

# 拉一讲素材（产出 transcript.txt + slides_final/ + manifest.json）
python zhiyun.py fetch --course <课> --lesson 5 --out /tmp/zy_L5

# 纯板书课：从回放抽帧重建板书
python zyframes.py <回放mp4路径或URL> <out_dir> --interval 8
```

`fetch` 结束时打印的 `manifest.json` 里有转写质量粗筛（`quality: ok|poor|none`）——据此决定讲义走"转写+PPT 双源"还是"纯 PPT"模式，判断细则见 SKILL.md。

## 注意事项

- **限速与单实例是硬约束**：脚本内置全局锁 + 最小请求间隔（智云 API 1.2s），不要并行跑、不要循环轮询。
- **锁号纪律**：统一认证凭据类登录失败 1 小时内最多 2 次（脚本自动记账）；要求验证码时脚本安全中止，先在浏览器登录一次统一认证再试。
- **当天的课没有字幕**（下课才生成）；无字幕/质量差时讲义应完全基于 PPT 撰写。
- **回放直链会过期**（auth_key 约 3-4 天有效），过期 403 重跑 fetch 即可；45 分钟 1080p 回放约 0.5-1.5GB，校园网约 11MB/s。
- **合规使用**：素材只用于个人学习笔记；讲义成稿后删除全部过程文件（转写、本地图片、回放视频），只留成品。
- 接口改版排查见 [`references/api-notes.md`](references/api-notes.md)。

> 本 skill 由 AI（ZCode + GLM）辅助编写并经作者日常使用验证，按"现状"提供，不保证更新与通用性。
