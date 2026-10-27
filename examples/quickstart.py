# -*- coding: utf-8 -*-
"""tdxdata 最小可运行示例。

跑法（两种都行）：
    python examples/quickstart.py          # 直接跑：未安装时自动退回仓库内源码
    pip install -e . && python examples/quickstart.py

需要能连外网（库直连通达信手机版服务器的 7615 端口）。
注意：服务端有速率限制，本脚本故意在每步之间留出间隔，不要删掉 sleep。
"""
import os
import sys
import time

try:
    import tdxdata
except ModuleNotFoundError:                   # 没 pip install 时，直接用仓库里的源码
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import tdxdata


def main() -> None:
    with tdxdata.Client() as c:
        # ① 日线：不复权 / 前复权 / 后复权（服务端算，不是本地乘系数）
        print("=== 贵州茅台 日线 ===")
        for name in ("NONE", "QFQ", "HFQ"):
            adj = getattr(tdxdata.Adjust, name)
            bars = c.get_bars("600519", period="day", count=3, adjust=adj)
            print("  %-4s 共 %d 根  末根 %s 收 %.2f"
                  % (name, len(bars), bars[-1].date, bars[-1].close))
            time.sleep(2.2)                      # 服务端限流：别删

        # ② 分时（当日 240 点）
        print("\n=== 上证指数 当日分时 ===")
        mins = c.get_minutes("000001")
        print("  %d 点  首 %s 收 %.3f  末 %s 收 %.3f"
              % (len(mins), mins[0].time, mins[0].price,
                 mins[-1].time, mins[-1].price))
        time.sleep(2.2)

        # ③ 不同市场的品种都能直接写代码，不用自己判市场
        print("\n=== 跨品种 ===")
        for code, label in (("900901", "沪 B"), ("113054", "沪转债"),
                            ("920207", "北交所"), ("123283", "深转债")):
            try:
                bars = c.get_bars(code, period="day", count=1)
                print("  %-8s %-6s 收 %.3f" % (code, label, bars[-1].close))
            except tdxdata.EmptyResult:
                print("  %-8s %-6s 无数据（该代码可能已摘牌/停牌）" % (code, label))
            time.sleep(2.2)

        # ④ 数据中心产品（421 个之一）：返回 Dataset，表格在 .table
        print("\n=== 数据中心：节假日 ===")
        d = c.get("holiday")
        print("  shape=%s  cols[:3]=%s" % (d.table.shape, d.table.cols[:3]))
        print("  首行: %s" % (str(d.rows[0])[:100],))
        time.sleep(2.2)

        # ⑤ 底层命令：ctl 一定要对，错了服务端只回空包
        print("\n=== 底层调用：节假日 ===")
        t = c.call("CWServ.ph_datacenter_hg_holiday", 0x21, ["001", "1"])
        print("  shape=%s  cols=%s" % (t.shape, t.cols[:3]))
        print("  首行: %s" % (str(t.rows[0])[:90],))

        print("\n限流统计:", c.rate_stats())


if __name__ == "__main__":
    main()
