#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""check_env.py — 环境自检（跨平台，Windows / macOS / Linux 都能跑）。

用途：换一台电脑、或把 skill 给别人时，先跑这一条命令，它会告诉你还缺什么、怎么补。
不修改任何文件，只做检测与建议。
"""
import importlib
import os
import platform
import shutil
import socket
import subprocess
import sys

OK = '[OK]  '
BAD = '[缺少]'
WARN = '[注意]'
rows = []


def add(level, item, detail):
    rows.append((level, item, detail))


print('=' * 72)
print('ZJU 文献下载 skill — 环境自检')
print('=' * 72)
print('系统: %s %s | Python %s' % (platform.system(), platform.release(), platform.python_version()))

# ---------- Python ----------
v = sys.version_info
if v >= (3, 8):
    add(OK, 'Python >= 3.8', '%d.%d.%d' % v[:3])
else:
    add(BAD, 'Python >= 3.8', '当前 %d.%d，请升级' % v[:2])

# ---------- 依赖 ----------
REQUIRED = {
    'pdfplumber': 'PDF 文本提取（解析、校验、身份核对都靠它）',
    'pypdf': 'PDF 解析备选引擎',
}
OPTIONAL = {
    'openpyxl': '读写 .xlsx（人工批注表）',
    'pymupdf': '给 PDF 插入解析卡（annotate_papers.py --annotate）',
}
for mod, why in REQUIRED.items():
    try:
        m = importlib.import_module(mod)
        add(OK, 'Python 包 %s' % mod, '%s  (%s)' % (getattr(m, '__version__', '?'), why))
    except ImportError:
        add(BAD, 'Python 包 %s' % mod, 'pip install -r requirements.txt   （%s）' % why)
for mod, why in OPTIONAL.items():
    try:
        m = importlib.import_module(mod)
        add(OK, '可选包 %s' % mod, '%s  (%s)' % (getattr(m, '__version__', '?'), why))
    except ImportError:
        add(WARN, '可选包 %s' % mod, '需要时装: pip install %s   （%s）' % (mod, why))

# ---------- Node ----------
node = shutil.which('node')
if node:
    try:
        out = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        out = '?'
    add(OK, 'Node.js', '%s (%s)  CDP 脚本需要 22+' % (out, node))
else:
    add(BAD, 'Node.js', '下载环节需要 Node 22+：https://nodejs.org （或让 agent 用自带 node 运行时）')

# ---------- 浏览器 ----------
BROWSERS = []
if platform.system() == 'Windows':
    BROWSERS = [
        os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
    ]
elif platform.system() == 'Darwin':
    BROWSERS = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
                '/Applications/Chromium.app/Contents/MacOS/Chromium']
else:
    BROWSERS = [shutil.which(x) for x in ('google-chrome', 'google-chrome-stable', 'chromium',
                                          'chromium-browser', 'microsoft-edge')]
found = [b for b in BROWSERS if b and os.path.exists(b)]
if found:
    add(OK, 'Chromium 系浏览器', found[0])
else:
    add(BAD, 'Chrome / Edge / Chromium', '下载环节需要其一（skill 不绑定具体哪一个）')

# ---------- CDP 端口 ----------
def port_open(port):
    s = socket.socket()
    s.settimeout(0.8)
    try:
        s.connect(('127.0.0.1', port))
        return True
    except Exception:
        return False
    finally:
        s.close()

if port_open(9222):
    add(OK, 'CDP 调试端口 9222', '已就绪（浏览器已用独立 profile 启动）')
else:
    add(WARN, 'CDP 调试端口 9222', '未启动。下载前运行 start-debug-browser.ps1 -Port 9222，'
                                   '并在该窗口人工登录浙大图书馆/WebVPN')
if port_open(3456):
    add(OK, 'web-access 代理 3456', '存在（本 skill 也可直连 9222，不强制需要它）')
else:
    add(WARN, 'web-access 代理 3456', '不存在也没关系：脚本会自动回落直连 9222')

# ---------- 文献库根目录 ----------
lib = os.environ.get('ZJU_LIBRARY')
if lib and os.path.isdir(lib):
    add(OK, '文献库根目录 (ZJU_LIBRARY)', lib)
elif lib:
    add(BAD, '文献库根目录 (ZJU_LIBRARY)', '环境变量指向的目录不存在: %s' % lib)
else:
    add(WARN, '文献库根目录', '未设置。归位时用 --library "<你的库根目录>" 指定，'
                              '或设环境变量 ZJU_LIBRARY。每个人的目录树不同，所以没有默认值')

# ---------- 中文字体（插解析卡用） ----------
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cjk_font import find_cjk_font
    f = find_cjk_font()
    if f:
        add(OK, '系统中文字体', f)
    else:
        add(WARN, '系统中文字体', '未找到；annotate_papers.py 会退回 PyMuPDF 内置 china-s 字体，仍可用')
except Exception as e:
    add(WARN, '中文字体检测', 'cjk_font.py 不可用: %s' % e)

print('')
for level, item, detail in rows:
    print('%-8s %-28s %s' % (level, item, detail))

bad = sum(1 for l, _i, _d in rows if l == BAD)
warn = sum(1 for l, _i, _d in rows if l == WARN)
print('')
print('-' * 72)
print('结论: %d 项缺少（必须补齐）、%d 项注意（可用但建议处理）' % (bad, warn))
if bad == 0:
    print('可以开始：先跑 start-debug-browser.ps1 登录，然后三步流程 下载 → 解析 → 归位。')
else:
    print('请先按上面的提示补齐缺少项；细节见 README-使用说明.md')
print('=' * 72)
sys.exit(1 if bad else 0)
