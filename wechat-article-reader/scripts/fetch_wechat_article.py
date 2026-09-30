# -*- coding: utf-8 -*-
"""微信公众号推文完整抓取：正文转 Markdown，图片/视频/音频下载到本地。

用法：
    python fetch_wechat_article.py <文章链接> [更多链接或 urls.txt] [-o 输出根目录]
    python fetch_wechat_article.py --html-file 已保存的页面.html [-o 输出根目录]
        # 被风控后的兜底：浏览器打开文章另存为 html，再用本模式转换

单篇文章输出一个文件夹：
    2026-08-27_文章标题/
    ├── 文章标题.md      正文 Markdown（YAML frontmatter 含标题/日期/公众号等）
    ├── media/           图片 img_0001.jpg…、视频 video_001.mp4…、音频 audio_001.mp3…
    └── source.html      原始网页快照（备查，--no-source-html 关闭）

退出码：0 成功；1 失败（链接失效/无正文等）；2 被风控（请改走 --html-file 兜底）。
纯标准库实现，无需 pip 安装依赖。仅供个人学习记录使用，注意文章版权。
"""
import argparse
import datetime
import html
import json
import os
import random
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    'User-Agent': UA,
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
}
ZERO_WIDTH = '\u200b\u200c\u200d\u2060\ufeff\u00ad'
BQ_ON, BQ_OFF = '\x03', '\x04'          # blockquote 区间哨兵
TOKEN_IMG = '\x05'                       # 内容片段标记（真实内容用 \x05 包裹）
# 每篇文章重置：KIND -> [信息字典]；'_hidden_mpvideo' 为 JSON 兜底直链
MEDIA = {}
# 最近一次 export() 的结果（供 fetch_latest 读取标题/目录）
LAST_RESULT = {}


class Captcha(Exception):
    """被风控/需要验证。"""


# ================================================================ 抓取

def fetch_page(url, retries=4, timeout=40):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read().decode('utf-8', errors='ignore')
            if ('环境异常' in data or 'wappoc_appmsgcaptcha' in data or 'antispider' in data):
                raise Captcha('微信返回了验证页')
            if len(data) < 60000:
                raise RuntimeError('页面过短（可能被风控或链接失效）')
            return data
        except Captcha:
            raise
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt * 2 + random.random() * 2)
    raise Captcha('网络重试多次仍失败：%r' % last_err)


def download_file(url, path, timeout=120):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': '*/*',
        'Referer': 'https://mp.weixin.qq.com/',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        with open(path, 'wb') as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)


# ================================================================ 元数据

_ATTR_CACHE = {}


def _attr(tag_text, name):
    pat = _ATTR_CACHE.get(name)
    if pat is None:
        pat = re.compile(name + r'\s*=\s*"([^"]*)"', re.I)
        _ATTR_CACHE[name] = pat
    m = pat.search(tag_text)
    return m.group(1).strip() if m else ''


def normalize_time(s):
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})?', s.strip())
    if m:
        return '%04d-%02d-%02d %s' % (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                      m.group(4) or '')
    return s.strip()


def get_meta(page):
    title = (_first_meta_og_title(page)
             or _first(r"var\s+msg_title\s*=\s*htmlDecode\([\"']([^\"']*)[\"']\)", page)
             or _first(r'var\s+msg_title\s*=\s*[\'"]([^\'"]*)', page)
             or _first(r'<title>(.*?)</title>', page))
    meta = {'title': title.strip()}
    meta['digest'] = (_first(r'<meta[^>]*name="description"[^>]*content="([^"]*)"', page)
                      or _first(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', page))

    nick = (_first(r'var\s+nickname\s*=\s*htmlDecode\("([^"]*)"\)', page)
            or _first(r'id="js_name"[^>]*>\s*(?:<a[^>]*>)?(?:<span[^>]*>)?([^<]+?)\s*<', page)
            or _first(r'[\'"]nickname[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]', page))
    meta['account'] = nick

    author = (_first(r'<meta[^>]*property="og:article:author"[^>]*content="([^"]*)"', page)
              or _first(r"var\s+author\s*=\s*htmlDecode\(['\"]([^'\"]*)['\"]\)", page))
    meta['author'] = author.lstrip('@').strip()

    pub = (_first(r'id="publish_time"[^>]*>\s*(?:<em[^>]*>)?\s*([^<]+?)\s*<', page)
           or _first(r'var\s+createTime\s*=\s*[\'"]([^\'"]+)[\'"]', page))
    if not pub:
        ts = (_first(r"createTime\s*[:=]\s*['\"](\d{10})['\"]", page)
              or _first(r'oriCreateTime\s*[:=]\s*["\'](\d{10})["\']', page)
              or _first(r'var\s+ct\s*=\s*"(\d{10})"', page))
        if ts:
            try:
                pub = datetime.datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M')
            except Exception:
                pub = ''
    meta['publish_time'] = normalize_time(pub)
    return meta


def _first(pattern, page=''):
    m = re.search(pattern, page)
    if not m:
        return ''
    val = next((g for g in m.groups() if g), m.group(1))
    return html.unescape(val).strip()


def _first_meta_og_title(page):
    m1 = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', page)
    if m1:
        return html.unescape(m1.group(1)).strip()
    m2 = re.search(r'<meta[^>]*content="([^"]*)"[^>]*property="og:title"', page)
    if m2:
        return html.unescape(m2.group(1)).strip()
    return ''


def get_body_segment(page):
    """截取 #js_content 开始到推荐阅读/打赏等区域之前的正文 HTML。"""
    idx = page.find('id="js_content"')
    if idx < 0:
        idx = page.find("id='js_content'")
    if idx < 0:
        return ''
    seg = page[idx:]
    gt = seg.find('>')
    if gt >= 0:
        seg = seg[gt + 1:]
    for marker in ['id="js_tags_preview_toast"', 'id="js_pc_qr_code"', 'class="reward_area"',
                   'id="content_bottom_area"', 'id="js_cpc_area"']:
        cut = seg.find(marker)
        if cut > 0:
            tag_start = seg.rfind('<', 0, cut)
            seg = seg[:tag_start if tag_start > 0 else cut]
            break
    return seg


# ================================================================ 媒体下载

IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.I)
FMT_MAP = {'jpeg': 'jpg', 'jpg': 'jpg', 'png': 'png', 'gif': 'gif', 'webp': 'webp',
           'svg': 'svg', 'bmp': 'bmp'}
SIGS = [(b'\xff\xd8\xff', '.jpg'), (b'\x89png', '.png'),
        (b'gif8', '.gif'), (b'<?xml', '.svg')]


def sniff_media_ext(head):
    if b'ftyp' in head[:12]:
        return '.mp4'
    if head.startswith(b'OggS'):
        return '.ogg'
    if head.startswith(b'ID3') or (len(head) > 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return '.mp3'
    low = head.lower()
    for sig, ext in SIGS:
        if low.startswith(sig):
            return ext
    return ''


def download_and_save(url, path_stem, timeout=120):
    """按真实类型给文件定扩展名；成功返回文件名，失败返回 ''。"""
    tmp = path_stem + '.part'
    try:
        download_file(url, tmp, timeout=timeout)
        size = os.path.getsize(tmp)
        with open(tmp, 'rb') as f:
            head = f.read(32)
        ext = sniff_media_ext(head)
        if not ext:
            low = head.lower()
            if size < 400 and (b'<html' in low or b'<!doctype' in low or b'{\"' in head[:4]):
                raise ValueError('返回的不是媒体文件')
            raise ValueError('无法识别的媒体类型')
        final = os.path.basename(path_stem) + ext
        os.replace(tmp, os.path.join(os.path.dirname(path_stem), final))
        return final
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        return ''


def collect_images(body_seg, media_dir):
    urls, seen = [], set()
    for tag in IMG_TAG_RE.findall(body_seg):
        u = (_attr(tag, 'data-src') or _attr(tag, 'src')).replace('\\/', '/')
        if u.startswith('http') and '.qpic.cn' in u and u not in seen:
            seen.add(u)
            urls.append(u)
    mapping = {}
    for i, u in enumerate(urls, 1):
        stem = os.path.join(media_dir, 'img_%04d' % i)
        name = download_and_save(u, stem)
        if name:
            mapping[u] = 'media/' + name
        if i % 20 == 0:
            print('    图片进度 %d/%d ...' % (i, len(urls)))
    print('    图片：%d 张已保存' % len(mapping))
    return mapping


# ================================================================ 音视频占位

def cut_blocks(seg, tag_pattern, class_needle=None):
    """找出标签完整块（处理嵌套/自闭合），返回 [(start, block)] 升序互不重叠。"""
    pos = []
    for m in re.finditer('<(%s)(?=[\\s/>])' % tag_pattern, seg, re.I):
        if class_needle and class_needle.lower() not in \
                seg[m.start():m.start() + 3000].lower():
            continue
        pos.append((m.start(), m.group(1)))
    blocks, boundary = [], -1
    for s, name in pos:
        if s < boundary:
            continue
        tag_re = re.compile('</?' + re.escape(name) + r'\b[^>]*?>', re.I | re.S)
        depth, i, endpos = 0, s, -1
        while True:
            t = tag_re.search(seg, i)
            if not t:
                break
            if t.group(0)[1] == '/':
                depth -= 1
            elif t.group(0).endswith('/>'):
                pass
            else:
                depth += 1
            i = t.end()
            if depth <= 0:
                endpos = i
                break
        if endpos < 0:
            endpos = len(seg)
        blocks.append((s, seg[s:endpos]))
        boundary = endpos
    return blocks


def extract_media_placeholders(body_seg):
    """音视频元素替换为 @@KIND@@n@@ 占位符（自后向前替换保证位移正确）。"""
    global MEDIA
    MEDIA = {k: [] for k in ('VIDEO', 'SNAP', 'VOICE', 'MUSIC')}
    MEDIA['_hidden_mpvideo'] = []
    seg = body_seg

    def snap_info(b):
        cover = (_attr(b, 'data-headimgurl') or _attr(b, 'data-cover')
                 or _attr(b, 'data-head-img')).replace('\\/', '/')
        return {'title': _attr(b, 'data-title') or _attr(b, 'data-describe') or '',
                'nick': _attr(b, 'data-nickname'),
                'cover': cover}

    def video_info(b):
        src = (_attr(b, 'data-src') or _attr(b, 'src')).replace('\\/', '/')
        kv = (re.search(r'[?&]vid=([0-9A-Za-z_-]{6,})', src)
              or re.search(r'\bvid=["\']([0-9A-Za-z_-]{6,})', b))
        return {'url': src if src.startswith('http') else '',
                'vid': kv.group(1) if kv else '',
                'cover': _attr(b, 'data-cover').replace('\\/', '/')}

    steps = [
        ('SNAP', r'mp-common-videosnap|mpvideosnap|txvlog', None, snap_info),
        ('SNAP', r'span', 'js_video_channel_card', snap_info),
        ('VIDEO', r'video|txvvideo', None, video_info),
        ('VIDEO', r'iframe', None, video_info),
        ('VOICE', r'mpvoice|mpaudio', None,
         lambda b: {'fid': _attr(b, 'voice_encode_fileid'), 'name': _attr(b, 'name')}),
        ('MUSIC', r'qqmusic', None,
         lambda b: {'name': _attr(b, 'album') or _attr(b, 'title') or ''}),
    ]
    for kind, pat, needle, fn in steps:
        for sp, block in reversed(cut_blocks(seg, pat, needle)):
            token = '@@%s@@%d@@' % (kind, len(MEDIA[kind]))
            MEDIA[kind].append(fn(block))
            seg = seg[:sp] + token + seg[sp + len(block):]

    seen, uniq = set(), []
    for u in re.findall(
            r'https?://mpvideo\.qpic\.cn/[\w./%-]+\.(?:mp4|m4v)\?[^\s"\'<>\\]{20,}',
            body_seg.replace('\\/', '/')):
        key = u.split('?')[0]
        if key not in seen:
            seen.add(key)
            uniq.append(u)
    MEDIA['_hidden_mpvideo'] = uniq
    return seg


def download_videos_audios(media_dir, text_only=False):
    results = {}
    if text_only:
        return results
    counters = {}

    def stem(prefix):
        counters[prefix] = counters.get(prefix, 0) + 1
        return os.path.join(media_dir, '%s_%03d' % (prefix, counters[prefix]))

    pool = list(MEDIA.get('_hidden_mpvideo', []))
    for i, v in enumerate(MEDIA['VIDEO']):
        url = v.get('url')
        if not url and pool:
            url = pool.pop(0)
        if url:
            name = download_and_save(url, stem('video'))
            if name:
                results[('VIDEO', i)] = 'media/' + name

    for i, a in enumerate(MEDIA['VOICE']):
        fid = a.get('fid')
        if not fid:
            continue
        base = 'https://res.wx.qq.com/voice/voiceaudio/' + fid
        for cand in (base + '/s.mp3', base + '?download=1'):
            name = download_and_save(cand, stem('audio'))
            if not name:
                continue
            p = os.path.join(media_dir, name)
            if os.path.getsize(p) > 4000 and sniff_media_ext(open(p, 'rb').read(16)):
                results[('VOICE', i)] = 'media/' + name
                break
            os.remove(p)
    return results


def download_snap_covers(media_dir, text_only=False):
    covers = []
    if text_only:
        return covers
    for j, v in enumerate(MEDIA['SNAP']):
        c = v.get('cover') or ''
        local = ''
        if c.startswith('http'):
            name = download_and_save(c, os.path.join(media_dir, 'snap_cover_%04d' % (1000 + j)))
            local = 'media/' + name if name else ''
        covers.append(local)
    return covers


# ================================================================ HTML -> Markdown

class MdParser(HTMLParser):
    BREAK_TAGS = {'p', 'div', 'section', 'figure', 'tr', 'figcaption'}

    def __init__(self, img_map):
        super().__init__(convert_charrefs=True)
        self.chunks = []             # 字符串流，内容以 \x05 标记
        self.img_map = img_map
        self.skip_depth = 0
        self.pre_open = False        # pre 内正在输出内容
        self.pre_pending = False     # pre 已开始但围栏首行尚未输出
        self.pre_lang = ''
        self._fence_open = False
        self.link_stack = []         # [{'href': str, 'mark': int}]
        self.quote_depth = 0

    def put(self, s):
        self.chunks.append(s)

    def nl(self):
        self.chunks.append('\n')

    def tok(self, s):
        """被 \x05 包裹的行内标记，结果阶段再剥壳，避免纯空白干扰。"""
        self.chunks.append(TOKEN_IMG + s + TOKEN_IMG)

    # ---- 开始标签
    def handle_starttag(self, tag, attrs):
        a = {}
        for k, v in attrs:
            if k not in a:
                a[k] = v or ''
        if tag in ('script', 'style'):
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == 'br':
            self.nl()
        elif tag == 'img':
            src = (a.get('data-src') or a.get('src') or '').strip()
            local = self.img_map.get(src)
            if local:
                self.nl()
                self.tok('![](%s)' % local)
                self.nl()
        elif tag == 'pre':
            self.nl()
            self.pre_open = False
            self.pre_pending = True          # 等到第一个内容再输出围栏开头，以便拿到内层 code 的语言
            self.pre_lang = ''
            self._fence_open = False
        elif tag == 'blockquote':
            self.nl()
            self.quote_depth += 1
            self.put(BQ_ON)
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.nl()
            self.tok('#' * int(tag[1]) + ' ')
        elif tag == 'a':
            href = (a.get('href') or '').strip()
            self.link_stack.append({'href': href if href.startswith(('http', 'ftp')) else '',
                                    'mark': len(self.chunks)})
        elif tag in ('strong', 'b'):
            self.tok('**')
        elif tag in ('em', 'i'):
            self.tok('*')
        elif tag == 'li':
            self.nl()
            self.tok('- ')
        elif tag == 'code' and self.pre_pending:
            # WeChat 代码块：语言标记常在内层 <code> 上
            m = re.search(r'(?:language|lang)-([\w+#.-]+)', a.get('class', '') or '')
            self.pre_lang = (a.get('lang') or a.get('data-lang')
                             or (m.group(1) if m else '') or '')
        elif tag in ('td', 'th'):
            self.tok('| ')
        elif tag == 'ol':
            self._ol_counter = 0
        elif tag == 'ul':
            self._ol_counter = None

    # ---- 结束标签
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag == 'pre':
            if self.pre_pending:
                self.pre_pending = False          # 空代码块，直接跳过
                self._fence_open = False
            elif self._fence_open:
                self.tok('\n```')
                self._fence_open = False
            self.nl()
        elif tag == 'blockquote':
            self.quote_depth = max(0, self.quote_depth - 1)
            self.put(BQ_OFF)
            self.nl()
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6') or tag in self.BREAK_TAGS:
            self.nl()
        elif tag == 'a':
            if self.link_stack:
                self._close_link(self.link_stack.pop())
        elif tag in ('strong', 'b', 'em', 'i'):
            self.tok('**' if tag in ('strong', 'b') else '*')

    def _close_link(self, ctx):
        pieces = [p for p in self.chunks[ctx['mark']:]]
        self.chunks[ctx['mark']:] = []
        flat = ''.join(pieces).replace(TOKEN_IMG, '')
        imgs = re.findall(r'!\[\]\(media/[^\s)]+\)', flat)
        rest = re.sub(r'!\[\]\(media/[^\s)]+\)', '', flat)
        label = re.sub(r'\s+', '', re.sub(r'^[\s>|·]+|[\s>|·]+$', '', rest))[:80]
        href = ctx['href']
        if href and imgs:
            # 图上包链接：各自独立保留图片 + 文字链接
            self.chunks.extend(pieces_without_tokens(pieces))
            if label:
                self.tok('\n[%s](%s)' % (label, href))
        elif href and label:
            self.tok('[%s](%s)' % (label, href))
        else:
            self.chunks.extend(pieces_without_tokens(pieces))

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.pre_pending or self.pre_open:
            if self.pre_pending and data.strip():
                self.tok('```%s\n' % self.pre_lang)
                self.pre_pending = False
                self._fence_open = True
            self.put(data.replace(ZERO_WIDTH, ''))
            return
        txt = data.replace(ZERO_WIDTH, '')
        if txt.strip():
            compact = re.sub(r'[ \t\r\n]+', ' ', txt).strip()
            self.tok(compact)
        elif txt:
            self.put(' ')

    # ---- 结果整理
    def result(self):
        raw = ''.join(self.chunks).replace(ZERO_WIDTH, '')

        # 1) 行级整理：引用区加 "> "；代码块内部原样保留（不剥缩进）
        out_lines, quoting, in_fence = [], False, False
        for ln in raw.split('\n'):
            enter, leave = BQ_ON in ln, BQ_OFF in ln
            ln = ln.replace(BQ_ON, '').replace(BQ_OFF, '').replace(TOKEN_IMG, '')
            core = ln.strip()
            if not core:
                out_lines.append(ln.rstrip() if in_fence else '')
            elif in_fence:
                out_lines.append(ln.rstrip())
                if core.startswith('```'):
                    in_fence = False
            elif core.startswith('```'):
                out_lines.append(core)
                in_fence = True
            else:
                prefix = '> ' if (quoting or enter) else ''
                out_lines.append(prefix + core)
                if enter:
                    quoting = True
            if leave:
                quoting = False

        # 2) 按结构拼装：围栏块原样保留；列表/引用/表格相邻行合组；其余单行成段
        def cls(s):
            return ('fence' if s.startswith('```')
                    else 'list' if re.match(r'^\s*(- |\*\s|\d+[.)]\s)', s)
                    else 'quote' if s.startswith('>')
                    else 'table' if s.startswith('|')
                    else 'head' if re.match(r'^#{1,6}\s', s)
                    else 'para')

        md_lines = [l for l in out_lines]
        final_parts = []
        i, n = 0, len(md_lines)
        while i < n:
            ln = md_lines[i]
            stripped = ln.strip()
            if not stripped:
                i += 1
                continue
            t = cls(stripped)
            if t == 'fence':
                j = i + 1
                while j < n and not md_lines[j].strip().startswith('```'):
                    j += 1
                seg = md_lines[min(i, n):min(j + 1, n)]
                final_parts.append('\n'.join(x.rstrip() for x in seg))
                i = j + 1
            elif t in ('list', 'quote', 'table'):
                j = i
                grp = []
                while j < n and cls(md_lines[j].strip()) == t:
                    grp.append(md_lines[j])
                    j += 1
                final_parts.append('\n'.join(grp))
                i = j
            else:
                final_parts.append(ln)
                i += 1

        md = '\n\n'.join(p for p in final_parts if p.strip())
        # 表格行美化：管道符两侧留一个空格
        md = '\n'.join(
            re.sub(r'\s*\|\s*', ' | ', l).strip() if l.strip().startswith('|') else l
            for l in md.split('\n'))
        md = md.replace('****', '')
        if md.count('**') % 2 == 1:
            md = md.replace('**', '')
        md = re.sub(r'\n{3,}', '\n\n', md)
        return md.strip()


def pieces_without_tokens(pieces):
    return [re.sub(TOKEN_IMG, '', p) if isinstance(p, str) else p for p in pieces]


# ================================================================ 占位符落地

def render_media_note(kind, info, local, cover):
    if kind == 'VIDEO':
        if local:
            return '![](%s)\n\n🎬 视频（已离线保存在 media 目录）' % local
        if info.get('vid'):
            return '[🎬 在线视频（腾讯视频 vid:%s）](https://v.qq.com/x/page/%s.html)' % (
                info['vid'], info['vid'])
        return '🎬 嵌入视频（未能离线下载，请打开原文观看）'
    if kind == 'SNAP':
        head = '![](%s)\n\n' % cover if cover else ''
        nick = ' · @' + info['nick'] if info.get('nick') else ''
        return head + '🎬 视频号视频《%s》%s —— 需在微信客户端观看' % (
            info.get('title') or '未命名', nick)
    if kind == 'VOICE':
        if local:
            return '![](%s)\n\n🎧 公众号语音「%s」（已离线保存）' % (local, info.get('name') or '')
        return '🎧 公众号语音「%s」（需点原文收听）' % (info.get('name') or '未识别')
    if kind == 'MUSIC':
        return '🎵 音乐《%s》（请点原文试听）' % (info.get('name') or '未识别')
    return ''


def finalize_markdown(md_body, media_results, snap_covers):
    for kind in ('VIDEO', 'SNAP', 'VOICE', 'MUSIC'):
        entries = MEDIA[kind]

        def sub(m, kind=kind):
            n = int(m.group(1))
            info = entries[n] if n < len(entries) else {}
            cov = snap_covers[n] if kind == 'SNAP' and n < len(snap_covers) else ''
            return '\n\n%s\n\n' % render_media_note(kind, info,
                                                    media_results.get((kind, n)), cov)
        md_body = re.sub(r'@@%s@@(\d+)@@' % kind, sub, md_body)
    return re.sub(r'\n{3,}', '\n\n', md_body)


# ================================================================ 单篇导出

def safe_name(s, maxlen=48):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', s).strip(' .')
    s = re.sub(r'\s+', ' ', s)
    return s[:maxlen].rstrip('. ') or 'untitled'


def yq(s):
    return json.dumps(str(s), ensure_ascii=False)


def export(source, out_root, opts, page=None):
    if page is not None:                      # 调用方直接给页面内容（如专栏镜像源）
        pass
    elif source.startswith('http'):
        page = fetch_page(source)
    else:
        with open(source, encoding='utf-8', errors='ignore') as f:
            page = f.read()

    meta = get_meta(page)
    body_seg = get_body_segment(page)
    if not body_seg:
        raise RuntimeError('未找到正文 #js_content（可能需登录、已删除或需微信客户端打开）')

    d10 = meta['publish_time'][:10]
    art_date = d10 if re.match(r'^\d{4}-\d{2}-\d{2}$', d10) else datetime.date.today().isoformat()
    title = meta['title'] or '(未获取到标题)'
    art_dir = os.path.join(out_root, '%s_%s' % (art_date, safe_name(title)))
    media_dir = os.path.join(art_dir, 'media')
    os.makedirs(media_dir, exist_ok=True)

    seg = extract_media_placeholders(body_seg)
    text_only = bool(opts.text_only)
    img_map = {} if text_only else collect_images(body_seg, media_dir)
    media_results = download_videos_audios(media_dir, text_only=text_only)
    snap_covers = download_snap_covers(media_dir, text_only=text_only)

    parser = MdParser(img_map)
    parser.feed(seg)
    parser.close()
    md_body = finalize_markdown(parser.result(), media_results, snap_covers)

    n_img = len(img_map)
    n_vid = sum(1 for k in media_results if k[0] == 'VIDEO')
    n_voice = sum(1 for k in media_results if k[0] == 'VOICE')
    n_unsaved = (len(MEDIA['VIDEO']) - n_vid) + len(MEDIA['SNAP'])

    fm = [
        '---',
        'title: %s' % yq(title),
        'account: %s' % yq(meta['account']),
        'author: %s' % yq(meta['author']),
        'publish_time: %s' % yq(meta['publish_time']),
        'source_url: %s' % yq(source if source.startswith('http') else ''),
        'fetched_at: %s' % yq(datetime.datetime.now().strftime('%Y-%m-%d %H:%M')),
        'digest: %s' % yq(meta['digest'][:150]),
        'counts: {images: %d, videos_saved: %d, voice_saved: %d}' % (n_img, n_vid, n_voice),
        '---',
    ]
    doc_parts = [
        '\n'.join(fm),
        '',
        '# ' + title,
        '',
        '> 📰 ' + (meta['account'] or '?')
        + (('　｜　作者：' + meta['author']) if meta['author'] else '')
        + '　🗓 ' + (meta['publish_time'] or '时间未知')
        + (('　🔗 [原文链接](%s)' % source) if source.startswith('http') else ''),
        '',
        md_body or '*（未能提取到正文文本，请打开原文查看）*',
        '',
    ]
    if n_unsaved > 0:
        doc_parts.append('> 注：文中 %d 个在线嵌入媒体（如视频号卡片）无法直接离线，'
                         '已在对应位置标注观看方式。' % n_unsaved)

    md_path = os.path.join(art_dir, safe_name(title) + '.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(doc_parts))

    if source.startswith('http') and not opts.no_source_html:
        with open(os.path.join(art_dir, 'source.html'), 'w', encoding='utf-8',
                  errors='ignore') as f:
            f.write(page)

    total_files = sum(len(fs) for _, _, fs in os.walk(art_dir))
    LAST_RESULT.clear()
    LAST_RESULT.update(title=title, dir=art_dir)
    print('[OK] %s' % os.path.abspath(art_dir))
    print('     标题: %s' % title)
    print('     公众号: %s ｜ 日期: %s ｜ 文件数%d（图%d 视频%d 语音%d 已离线）'
          % (meta['account'] or '?', meta['publish_time'] or '?',
             total_files, n_img, n_vid, n_voice))
    return 0


# ================================================================ 入口

def expand_sources(raw_args):
    out = []
    for a in raw_args:
        if a.startswith('http'):
            out.append(a)
        elif os.path.isfile(a):
            with open(a, encoding='utf-8-sig') as f:
                out += [ln.strip() for ln in f if ln.strip().startswith('http')]
    return out


def main():
    ap = argparse.ArgumentParser(
        prog='fetch_wechat_article.py',
        description='微信公众号推文完整抓取：Markdown 正文 + 图片/视频/音频本地化')
    ap.add_argument('urls', nargs='*', help='文章链接（可多个），或每行一条链接的 txt 文件')
    ap.add_argument('-o', '--output', default='.', help='输出根目录（默认当前目录）')
    ap.add_argument('--text-only', action='store_true', help='只提取文字，不下载任何媒体')
    ap.add_argument('--no-source-html', action='store_true', help='不保存 source.html 快照')
    ap.add_argument('--html-file', help='转换本地保存的文章页面 html（风控兜底模式）')
    opts = ap.parse_args()

    sources = ([opts.html_file] if opts.html_file else []) + expand_sources(opts.urls)
    sources = list(dict.fromkeys(sources))
    if not sources:
        ap.print_help()
        return 1

    os.makedirs(opts.output, exist_ok=True)
    rc = 0
    for i, s in enumerate(sources, 1):
        print('[%d/%d] %s' % (i, len(sources),
                              s if s.startswith('http') else '本地:' + s))
        try:
            rc |= export(s, opts.output, opts)
        except Captcha as e:
            print('[RISK] 被风控：%s' % e)
            print('       兜底方案见 SKILL.md：浏览器打开→另存为网页→--html-file 转换')
            rc = max(rc, 2)
        except Exception as e:
            print('[FAIL] %r' % e)
            rc = max(rc, 1)
        if i < len(sources):
            time.sleep(random.uniform(1.5, 3.0))
    return rc


if __name__ == '__main__':
    sys.exit(main())
