import io, os, re, sys
SK = os.path.dirname(os.path.abspath(__file__))
# 自查清单: 把你自己的用户名/本机私有路径加进来, 用来扫出没清干净的个人信息
BAD = [('本机用户名', r'your-username'),
       ('本机私有路径', r'C:\\Users\\你的用户名')]
n = 0
for f in sorted(os.listdir(SK)):
    if f in ('check_portable.py',) or not f.endswith(('.py', '.mjs', '.ps1')): continue
    s = io.open(os.path.join(SK, f), encoding='utf-8').read()
    for label, pat in BAD:
        for m in re.finditer(pat, s):
            ln = s[:m.start()].count(chr(10)) + 1
            print('  %s:%d  %s  <- %s' % (f, ln, label, s.split(chr(10))[ln-1].strip()[:74]))
            n += 1
print('可移植性问题: %d 处' % n)
sys.exit(1 if n else 0)
