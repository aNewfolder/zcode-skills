# -*- coding: utf-8 -*-
"""cjk_font.py — 跨平台中文字体查找。

annotate_papers.py 需要在解析卡里渲染中文，但不同系统的字体路径不同：
  Windows: C:\\Windows\\Fonts\\msyh.ttc / simsun.ttc / simhei.ttf
  macOS  : /System/Library/Fonts/PingFang.ttc、STHeiti Light.ttc、Hiragino Sans GB.ttc
  Linux  : /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc 等
若都没有：返回 None，调用方应改成纯英文卡片（不要报错中断）。

另外 PyMuPDF 自带中文内置字体 `china-s`（宋体）可直接用，不依赖系统字体；
本模块只在需要"自定义字体文件"时使用。
"""
import os

CANDIDATES = [
    # Windows
    r'C:\\Windows\\Fonts\\msyh.ttc', r'C:\\Windows\\Fonts\\simsun.ttc', r'C:\\Windows\\Fonts\\simhei.ttf',
    # macOS
    '/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/STHeiti Light.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc', '/Library/Fonts/Arial Unicode.ttf',
    # Linux
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    '/usr/share/fonts/truetype/arphic/uming.ttc',
]


def find_cjk_font():
    """返回第一个存在的字体文件路径；都没有则返回 None。"""
    for p in CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def has_cjk_font():
    return find_cjk_font() is not None


if __name__ == '__main__':
    f = find_cjk_font()
    print(f if f else '(未找到系统中文字体；仍可使用 PyMuPDF 内置 china-s 字体)')
