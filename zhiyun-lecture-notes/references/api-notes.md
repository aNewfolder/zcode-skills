# API 要点（排查异常时读；口径来自 Anchorite-no/zju-skill-public 与 2026-11 实测）

## 智云课堂

- 域名：classroom.zju.edu.cn（门户/PPT 接口）、yjapi.cmc.zju.edu.cn（业务 API）、
  education.cmc.zju.edu.cn（我的课程）、tgmedia.cmc.zju.edu.cn（登录入口）。
- **未选课程免申请直读（2026-11-05 实测）**：searchlist 全站搜索 → catalogue 课次目录 →
  search-ppt → search-trans-result → get-sub-info 全链路**不校验选课关系**，登录用户可直接读
  任何课程的课次列表、转写、板书、回放地址。旁听/跨班讲义无需走听课申请。若失效（401/403）再找官方申请通道。
- 登录链：GET `https://tgmedia.cmc.zju.edu.cn/index.php?r=auth%2Flogin&forward=https%3A%2F%2Fclassroom.zju.edu.cn%2F`
  → 30x 到 CAS → 回跳后 `.zju.edu.cn` 域出现 `JWTUser` cookie（JWT，可解出 id/account/tenant_id）。
- API 鉴权：`_token` cookie（URL 解码）取 JWT 三段式 → `Authorization: Bearer <token>`；带 `Accept: application/json`
  与 `Referer: https://classroom.zju.edu.cn/`（转写接口 Referer 用 livingroom 页）。
- 失效判据：HTTP 401 / body `code==401` / 302 到 cas 或 auth/login / body 以 `<!doctype|<html` 开头。
  注意：字幕接口的 Content-Type 是 text/html 但 body 是 JSON，判失效要看 body 不是看头。
  **隐性失效（2026-11-06 实测）**：education 的 account-profile 接口报 `HTTP 403`（非 401），JWTUser cookie
  仍在、yjapi/classroom 接口部分正常——这是 `_token` Bearer 过期而自动重登判据兜不住的形态，
  `zhiyun.py login --fresh` 一次即好，不要反复重试业务请求。
- 我的课程：`education.cmc.zju.edu.cn/personal/courseapi/vlabpassportapi/v1/account-profile/course?nowpage=&per-page=&force_mycourse=1`
  → params.result.{total, data:[{Id, Title, Teacher|realname, TermName}]}；TermName 如 "2026-20271"（1=秋冬 2=春夏）。
- 全站搜索：`classroom.zju.edu.cn/pptnote/v1/searchlist?tenant_id&user_id&user_name&title&realname&page&per_page&source=1`
  → total.{total, list:[{course_id, sub_id, subject_title, ...}]}。
- 课次目录：`yjapi.cmc.zju.edu.cn/courseapi/v2/course/catalogue?course_id=` → result.data[]，
  type ∈ course_live/live/ilive；content 是 JSON 字符串（playback/audio_url/file_list/download）；
  按 start_at 升序编号即"第 N 次课"；status=="6" 且有音视频=可回放；status=="2"=未开课/未录。
  content.download / file_list 里可能有老师上传的 courseware pptx（yjapi sourceapi 链接）。
  "第 N 次课"≠"第 N 教学周"：一周两次课的课，同一教学周内有 2 个 lesson；补课/调休也会加 lesson。
  周次映射：第 1 周周一 + 7×(N-1) 得各周日期区间，再按 start_at 落位。
- PPT 时间轴：`classroom.zju.edu.cn/pptnote/v1/schedule/search-ppt?course_id&sub_id&page&per_page(≤100)`
  → list[].content(JSON).pptimgurl + created_sec（板书时刻）；分页 total 去重判断。
  **纯板书课识别特征**：search-ppt 只返回 1 帧（created_sec≈1 的教室电脑桌面截图）且 catalogue 无
  courseware 附件 → 老师全程黑板手写，板书素材只能从回放视频抽帧重建（见下"回放抽帧"）。
- 语音转写：`yjapi.cmc.zju.edu.cn/courseapi/v3/web-socket/search-trans-result?sub_id=&format=json`
  → list[].all_content[]{BeginSec, EndSec, Text}；当天课次要等下课才有字幕；
  无数据时报"未查询到语音数据"。45 分钟课典型 700-800 句 / 1.5-2 万字；
  2 节连堂 90-105 分钟课（含课间静默）典型 610-1050 句 / 1.4-2.9 万字（16 讲整门课实测）；
  快语速+口语课转写错字率高（专业术语、人名几乎必错），按教材知识修正后使用。
  数学课实测错字样本（利于快速对义）：Bessel→"白色/杯子/(best)芳草"、Legendre→"双圆/狼的王子"、
  Fourier 变换→"福利/分裂/复利变换"、Green 函数→"隔离函数/格林公司"、达朗贝尔→"大唐贝尔公司"、
  Duhamel→"读航母"、特征值→"单子"、齐次→"其次"、特征线法→"行莫法/刑部法"、幂级数→"密集做"。
- 回放：`yjapi.cmc.zju.edu.cn/courseapi/v3/portal-home-setting/get-sub-info?course_id&sub_id` → data.playurl
  （字符串或 {清晰度:url}，mp4 直链带 `auth_key` 参数）。auth_key 有效期约 3-4 天，过期 403，重跑 fetch 取新链。
  catalogue 的 content.playback.url 同样可用。
  CDN（vod/resource.cmc.zju.edu.cn）支持 Range，校园网实测 ~11MB/s；45min 1080p ≈ 0.5-1.5GB，curl 直接下。
- PPT 图片是 CDN 静态资源，可少量并发下载；API 请求保持 1.2s+ 间隔。

### 回放抽帧重建板书（zyframes.py，2026-11-05 实测口径）

- ffmpeg 不必系统安装：`imageio-ffmpeg` 包自带完整 ffmpeg 二进制
  （`imageio_ffmpeg.get_ffmpeg_exe()`），支持 `-ss` 快速 seek 与 https 远程输入；
  帧分析用 opencv-python（cv2）。
- 黑板区域自动检测：暗绿色主导像素（g>r+20 & g>b+15 & 60<g<200）取 1%/99% 分位 bbox；
  1080p 黑板机位实测样例 crop=(180,224,1604,359)，不同教室需按实际画面微调或 --crop 覆盖。
- 时代切分：**粉笔掩码**（灰度>150）占比骤降 >0.6% = 擦板 → 切"板书时代"。
  **勿用相邻帧差分分段**：增量写字每 8s 只增 0.1-0.5%，远低于阈值则几十分钟并成一个状态，
  高于阈值则被人走动打碎（两种坑都踩过）。空板时代（粉笔占比<0.35%）丢弃。
- 每时代取粉笔量最丰富 top-4 帧（相互 xor>1% 去重）+ 时代首帧；代表帧可能被师生身体遮挡，
  同一时刻 ±8s 换一帧常可避开。
- 读数字：联络表只够看布局；对关键图表区域用 cv2.resize(INTER_CUBIC) 2-3x 放大裁剪。
  例题每个数字都要在特写帧或转写中找到出处；板书与转写冲突时取逻辑自洽者并注明。

## 统一认证（zjuam.zju.edu.cn/cas）

- getKaptchaStatus=true 时会要求图形验证码 → 必须中止（别硬试）。
- getPubKey 返回 512-bit RSA（hex modulus 128 位）；密码 UTF-16 码元反转后按 ohdave RSA 分块加密
  （每 2 码元一个 16-bit 小端数字 pow(e,n)，hex 定宽 4 位空格连接；仅 ASCII 密码）。
- POST 表单 {username, password:<enc>, authcode:"", execution, _eventId:submit, rememberMe, ys}。
- 凭据被拒约 5 次锁号：1 小时内最多 2 次凭据类失败（state.json 记账），超限脚本拒绝登录。
