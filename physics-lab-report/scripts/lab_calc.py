# -*- coding: utf-8 -*-
"""
大学物理实验 数据处理计算器（严格按《大物实验绪论》PPT 口径）
仅用标准库。所有报告中的数值先用本脚本算，再以手算过程誊进报告。

子命令：
  direct    直接测量五步法：剔除/零点修正 → x̄ → uA → uB=Δ仪/√3 → u → 结果表达式
  align     按课程位数规则给出最终结果表达式（不确定度首位<3留2位/≥3留1位，
            最大误差限原则；平均值末位对齐，四舍六入五凑偶）
  lsq       最小二乘法 y=a+bx：b、a、r 及中间量
  zhucha    逐差法：前后分组对应相减
  prop-add  和差形式传递 uy=√Σ(ci·ui)²
  prop-rel  积商形式传递 uy/y=√Σ(k·ui/xi)²

示例：
  python lab_calc.py direct --data "2.567,2.565,2.569,2.570,2.571,2.568" --dinst 0.004 --unit mm
  python lab_calc.py align --value 2.3450 --unc 0.0320 --unit cm2
  python lab_calc.py lsq --x "0,1,2,3,4,5,6,7" --y "0.00,0.99,1.85,2.75,3.66,4.55,5.44,6.32"
  python lab_calc.py zhucha --data "L1,L2,...,L8" --m 4
  python lab_calc.py prop-rel --y 297.6 --term "2 0.005 9.800" --term "2 0.005 4.500" --term "1 0.005 5.000"
"""
import argparse
import math
import sys
from decimal import Decimal, ROUND_HALF_EVEN, getcontext

getcontext().prec = 34

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def parse_floats(s):
    return [float(t) for t in s.replace("，", ",").split(",") if t.strip()]


def first_sig_digit(u):
    """首位非零有效数字（u>0）。"""
    d = Decimal(repr(u)).normalize()
    return int(str(d).replace(".", "").replace("-", "").lstrip("0")[0])


def round_unc(u):
    """不确定度修约：首位<3留2位，≥3留1位；最大误差限原则——只看欲保留最低位的
    后一位数字，非 0 则进位、为 0 则舍去（PPT 90：0.12134→0.13，0.1201→0.12，
    0.3201→0.4，0.3021→0.3；0.50592 后一位为 0 → 0.5）。"""
    if u <= 0:
        raise SystemExit("不确定度必须为正")
    d = Decimal(repr(u))
    keep = 2 if first_sig_digit(u) < 3 else 1
    exp = d.adjusted()  # 10^exp ≤ |d| < 10^(exp+1)
    q = Decimal(1).scaleb(exp - keep + 1)  # 保留位的位置权
    truncated = (d / q).to_integral_value(rounding="ROUND_DOWN") * q
    first_discarded = ((d - truncated) / q * 10).to_integral_value(rounding="ROUND_DOWN")
    if first_discarded != 0:
        truncated += q  # 后一位非 0 则进位
    return truncated, keep


def round_up_1sig(u):
    """进位保留 1 位有效数字（PPT 75 课程示范对 uB 的处理：0.00231→0.003）。
    同样只看后一位：0.00205 后一位为 0 → 0.002。"""
    d = Decimal(repr(u))
    q = Decimal(1).scaleb(d.adjusted())
    t = (d / q).to_integral_value(rounding="ROUND_DOWN") * q
    first_discarded = ((d - t) / q * 10).to_integral_value(rounding="ROUND_DOWN")
    return t + q if first_discarded != 0 else t


def decimals_of(d):
    """Decimal 的小数位数。"""
    e = -d.as_tuple().exponent
    return max(e, 0)


def round_mean_align(v, u_rounded):
    """平均值按不确定度末位对齐，四舍六入五凑偶。"""
    dec = decimals_of(u_rounded)
    q = Decimal(1).scaleb(-dec)
    return Decimal(repr(v)).quantize(q, rounding=ROUND_HALF_EVEN), dec


def fmt(d):
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def cmd_direct(a):
    data = parse_floats(a.data)
    n = len(data)
    if n < 2:
        raise SystemExit("至少 2 个数据（异常值先由人工剔除后再传入）")
    if not (6 <= n <= 10):
        print(f"提示：n={n} 不在 6~10，uA≈s(x̄) 的简化适用条件是 6≤n≤10")
    mean = sum(data) / n
    s2 = sum((x - mean) ** 2 for x in data) / (n * (n - 1))
    s_mean = math.sqrt(s2)
    ua = s_mean
    ub_raw = a.dinst / math.sqrt(3)
    # 课程示范（PPT 75）：uA 按通用规则修约；uB 进位保留 1 位；合成用进位后的值
    ua_r, _ = round_unc(ua)
    ub_r = round_up_1sig(ub_raw)
    u = math.sqrt(float(ua_r) ** 2 + float(ub_r) ** 2)
    u_r, _ = round_unc(u)
    best = mean - a.zero  # 最佳估计值 = 平均值 - 零点误差（零点误差本身可正可负）
    m_r, dec = round_mean_align(best, u_r)
    print(f"n = {n}")
    print(f"平均值 x̄ = {mean:.6g} {a.unit}")
    if a.zero:
        print(f"最佳估计值 = x̄ − ({a.zero}) = {best:.6g} {a.unit}")
    print(f"s(x̄) = √[Σ(xi−x̄)²/(n(n−1))] = {s_mean:.6g} {a.unit}")
    print(f"uA ≈ s(x̄) = {ua:.6g} → 记 {fmt(ua_r)} {a.unit}")
    print(f"uB = Δ仪/√3 = {a.dinst}/1.732 = {ub_raw:.6g} → 进位记 {fmt(ub_r)} {a.unit}（PPT 75 示范）")
    print(f"u = √(uA²+uB²) = √({fmt(ua_r)}²+{fmt(ub_r)}²) = {u:.4g} → {fmt(u_r)} {a.unit}")
    e = float(u_r) / float(m_r) * 100 if m_r != 0 else float("nan")
    print(f"修约后：x̄ = {fmt(m_r)} {a.unit}（保留 {dec} 位小数），u = {fmt(u_r)} {a.unit}")
    print(f"结果表达式：N = ({fmt(m_r)} ± {fmt(u_r)}) {a.unit}，E = {e:.2g}%")
    print(f"[参考] 若用未进位中间值：uA={ua:.4g}, uB={ub_raw:.4g}, u={math.sqrt(ua*ua+ub_raw*ub_raw):.4g}")


def cmd_align(a):
    u_r, keep = round_unc(a.unc)
    m_r, dec = round_mean_align(a.value, u_r)
    print(f"不确定度首位 {first_sig_digit(a.unc)}（{'<3 留 2 位' if keep == 2 else '≥3 留 1 位'}）→ u = {fmt(u_r)}")
    print(f"平均值末位对齐（四舍六入五凑偶）→ {fmt(m_r)}")
    print(f"结果表达式：N = ({fmt(m_r)} ± {fmt(u_r)}) {a.unit}")


def cmd_lsq(a):
    x = parse_floats(a.x)
    y = parse_floats(a.y)
    if len(x) != len(y) or len(x) < 2:
        raise SystemExit("x/y 长度相等且至少 2 个点")
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum(xi * yi for xi, yi in zip(x, y)) / n
    sx2 = sum(xi * xi for xi in x) / n
    b = (sxy - mx * my) / (sx2 - mx * mx)
    aa = my - b * mx
    r_num = sxy - mx * my
    r_den = math.sqrt((sx2 - mx * mx) * (sum(yi * yi for yi in y) / n - my * my))
    r = r_num / r_den if r_den else float("nan")
    print(f"n = {n}，x̄ = {mx:.6g}，ȳ = {my:.6g}")
    print(f"xȳ(均值) = {sxy:.6g}，x²̄(均值) = {sx2:.6g}")
    print(f"b = (x̄y - x̄·ȳ)/(x²̄ - x̄²) = {b:.6g}")
    print(f"a = ȳ - b·x̄ = {aa:.6g}")
    print(f"r = {r:.6f}")


def cmd_zhucha(a):
    d = parse_floats(a.data)
    m = a.m
    if len(d) != 2 * m:
        raise SystemExit(f"数据个数应为 2×m = {2*m}")
    diffs = [d[m + i] - d[i] for i in range(m)]
    mean_diff = sum(diffs) / m
    print("逐差（组间距 = %d × 原间距）：" % m)
    for i, df in enumerate(diffs, 1):
        print(f"  Δ{i} = x{m+i} - x{i} = {df:.6g}")
    print(f"平均 Δ = {mean_diff:.6g}；每个原间距均值 = Δ/{m} = {mean_diff/m:.6g}")


def cmd_prop_add(a):
    terms = []
    for t in a.term:
        parts = [float(v) for v in t.replace(",", " ").split()]
        if len(parts) == 2:
            terms.append(parts[0] * parts[1])
        elif len(parts) == 1:
            terms.append(parts[0])  # 已算好的 c·u 项
        else:
            raise SystemExit("每项格式：'系数 不确定度' 或直接给一个 c·u 值")
    uy = math.sqrt(sum(t * t for t in terms))
    print("uy = √(" + "² + ".join(f"({t:.6g})" for t in terms) + "²)")
    print(f"uy = {uy:.6g}")
    u_r, _ = round_unc(uy)
    print(f"修约后：uy = {fmt(u_r)}")
    if a.y is not None:
        m_r, dec = round_mean_align(a.y, u_r)
        print(f"结果表达式：N = ({fmt(m_r)} ± {fmt(u_r)}) {a.unit}".rstrip())


def cmd_prop_rel(a):
    terms = []
    for t in a.term:
        parts = [float(v) for v in t.replace(",", " ").split()]
        if len(parts) == 3:
            k, u, x = parts
            if x == 0:
                raise SystemExit("xi 不能为 0")
            terms.append(k * u / x)
        elif len(parts) == 1:
            terms.append(parts[0])  # 已按偏导算好的 |∂lnf/∂xi|·ui 项
        else:
            raise SystemExit("每项格式：'k ux x' 或直接给一个偏导项值")
    urel = math.sqrt(sum(t * t for t in terms))
    print("uy/y = √(" + "² + ".join(f"({t:.6g})" for t in terms) + "²)")
    print(f"uy/y = {urel:.6g}（即 {urel*100:.2g}%）")
    # 课程示范（PPT 82）：相对不确定度按普通规则留 2 位有效数字，再用它乘 y
    d = Decimal(repr(urel))
    q = Decimal(1).scaleb(d.adjusted() - 1)
    urel_disp = float(d.quantize(q, rounding=ROUND_HALF_EVEN))
    print(f"uy/y 保留 2 位有效数字 → {urel_disp:.6g}")
    uy = abs(a.y) * urel_disp
    u_r, _ = round_unc(uy)
    print(f"uy = {a.y:.6g}×{urel_disp:.6g} = {uy:.6g} → 修约 {fmt(u_r)}")
    m_r, _ = round_mean_align(a.y, u_r)
    print(f"结果表达式：N = ({fmt(m_r)} ± {fmt(u_r)}) {a.unit}".rstrip())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)

    d = sp.add_parser("direct", help="直接测量五步法")
    d.add_argument("--data", required=True, help="逗号分隔的测量列（可含零点修正前数据）")
    d.add_argument("--dinst", type=float, required=True, help="仪器允差 Δ仪")
    d.add_argument("--zero", type=float, default=0.0, help="零点误差（已定系统误差，最佳估计值 = 平均值 − 该值；如零点误差 −0.005 就传 -0.005）")
    d.add_argument("--unit", default="", help="单位")
    d.set_defaults(fn=cmd_direct)

    g = sp.add_parser("align", help="最终结果修约与对齐")
    g.add_argument("--value", type=float, required=True)
    g.add_argument("--unc", type=float, required=True)
    g.add_argument("--unit", default="")
    g.set_defaults(fn=cmd_align)

    l = sp.add_parser("lsq", help="最小二乘 y=a+bx")
    l.add_argument("--x", required=True)
    l.add_argument("--y", required=True)
    l.set_defaults(fn=cmd_lsq)

    z = sp.add_parser("zhucha", help="逐差法")
    z.add_argument("--data", required=True)
    z.add_argument("--m", type=int, default=4, help="前组数据个数（默认 4，共 8 个数据）")
    z.set_defaults(fn=cmd_zhucha)

    pa = sp.add_parser("prop-add", help="和差形式传递")
    pa.add_argument("--term", action="append", required=True, help="每项 '系数 不确定度' 或单项值，可多次")
    pa.add_argument("--y", type=float, default=None, help="间接量数值（给出则输出完整结果表达式）")
    pa.add_argument("--unit", default="")
    pa.set_defaults(fn=cmd_prop_add)

    pr = sp.add_parser("prop-rel", help="积商形式传递")
    pr.add_argument("--y", type=float, required=True, help="间接量数值")
    pr.add_argument("--term", action="append", required=True, help="每项 'k ux x'，复合函数可先手算偏导项后直接单项给出")
    pr.add_argument("--unit", default="")
    pr.set_defaults(fn=cmd_prop_rel)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
