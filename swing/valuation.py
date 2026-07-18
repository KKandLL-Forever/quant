"""valuation.py — 确定性「相对估值锚」决策树(把"选哪个估值方法"从 LLM 自由发挑改成代码判定,求稳)。

决策树(接力,绝大多数公司都能接住):
  有正且相对稳定利润? → PE(动态扣非)[高成长叠 PEG]
     否 → 有净资产(PB>0)? → PB
          否 → 有收入? → PS
               否 → 无利润+无收入+无有效净资产 → 无法估值(纯预期驱动)
  行业 override(优先于上面):
    强周期(有色/煤炭/钢铁/化工/石油石化/存储) → 周期中枢PE(正常化利润)+ PB底
    银行 → PB-ROE;非银金融 → PB(保险可用内含价值EV)
    高股息公用(电力/水务/燃气/高速铁路) → 股息率
    高负债高折旧(航空/机场/港口/基建) → EV/EBITDA

用法:from valuation import pick_anchor; pick_anchor("601869.SH")
     或 CLI:python swing/valuation.py 601869
返回 dict:{archetype, anchor, reason, computable}。碰到无法处理的,anchor="无法估值",computable=False。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import duckdb
import cache_tushare as ct

_CYCLE_L1 = ("有色金属", "煤炭", "钢铁", "基础化工", "石油石化")
_TRANS_UTIL_KW = ("高速", "铁路", "公路")            # 交通运输里走股息率的子行业(区别于航空港口)
_EVEBITDA_KW = ("航空", "机场", "港口", "航运")       # 交通运输里走EV/EBITDA的子行业


def _norm(code: str) -> str:
    code = str(code).strip().upper()
    if "." in code:
        return code
    if code.startswith(("60", "68", "51", "58", "11", "5")):
        return code + ".SH"
    if code.startswith(("8", "4", "92")):
        return code + ".BJ"
    return code + ".SZ"


def pick_anchor(code: str) -> dict:
    """按确定性决策树给该股选相对估值主锚,返回 {archetype, anchor, reason, computable}。"""
    ts = _norm(code)
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    try:
        sw = con.execute("SELECT l1_name,l2_name,l3_name FROM sw_member WHERE ts_code=? ORDER BY is_new DESC LIMIT 1", [ts]).fetchone() or ("", "", "")
        prof = [r[0] for r in con.execute("""SELECT n_income_attr_p FROM income
            WHERE ts_code=? AND end_date LIKE '%1231' AND n_income_attr_p IS NOT NULL ORDER BY end_date DESC LIMIT 4""", [ts]).fetchall()]
        rev = con.execute("""SELECT total_revenue FROM income WHERE ts_code=? AND total_revenue IS NOT NULL
            ORDER BY end_date DESC LIMIT 1""", [ts]).fetchone()
        db = con.execute("SELECT pb,dv_ttm,pe_ttm FROM daily_basic WHERE ts_code=? AND trade_date<=CURRENT_DATE ORDER BY trade_date DESC LIMIT 1", [ts]).fetchone()
        fi = con.execute("SELECT netprofit_yoy,roe FROM fina_indicator WHERE ts_code=? AND netprofit_yoy IS NOT NULL ORDER BY end_date DESC LIMIT 1", [ts]).fetchone()
    finally:
        con.close()

    l1, l2, l3 = (sw or ("", "", ""))
    ind = " ".join(x or "" for x in (l1, l2, l3))
    pb = db[0] if db else None
    dv = db[1] if db else None
    npyoy = fi[0] if fi else None

    has_profit_stable = bool(prof) and prof[0] is not None and prof[0] > 0 and sum(1 for p in prof[:3] if p and p > 0) >= 2
    has_networth = pb is not None and pb > 0
    has_revenue = bool(rev and rev[0] and rev[0] > 0)
    high_growth = npyoy is not None and npyoy > 25

    if l1 == "银行":
        return {"archetype": "资产型(金融)", "anchor": "PB-ROE", "reason": "银行:用净资产收益率匹配市净率,PE参考价值低", "computable": True}
    if l1 == "非银金融":
        anc = "内含价值EV(保险)" if "保险" in ind else "PB"
        return {"archetype": "资产型(金融)", "anchor": anc, "reason": "非银金融:" + ("保险用内含价值" if "保险" in ind else "券商/多元金融用PB为主"), "computable": True}
    if l1 in _CYCLE_L1 or "存储" in ind:
        return {"archetype": "周期(成长)型", "anchor": "周期中枢PE(正常化利润)+ PB底", "reason": f"强周期({l1 or l3}):忌用当期PE(顶部PE最低=陷阱),用一轮周期均值利润算PE + PB判底", "computable": True}
    _sub = (l2 or "") + (l3 or "")
    if l1 == "公用事业" or (l1 == "交通运输" and any(k in _sub for k in _TRANS_UTIL_KW)):
        return {"archetype": "现金流型", "anchor": "股息率", "reason": f"高股息公用({l2 or l1}):看分红可持续性" + (f",当前股息率{dv:.1f}%" if dv else ""), "computable": True}
    if l1 == "建筑装饰" or (l1 == "交通运输" and any(k in _sub for k in _EVEBITDA_KW)):
        return {"archetype": "资产型(重资产)", "anchor": "EV/EBITDA", "reason": f"高负债高折旧({l2 or l1}):剔除资本结构/折旧差异更公平", "computable": True}

    if has_profit_stable:
        anc = "PE(动态扣非)" + ("叠PEG" if high_growth else "")
        why = "有正且相对稳定利润" + (f",净利同比{npyoy:.0f}%属高成长叠PEG" if high_growth else "")
        return {"archetype": "成长型" if high_growth else "成熟盈利型", "anchor": anc, "reason": why, "computable": True}
    if has_networth:
        return {"archetype": "资产型", "anchor": "PB(净资产/家底)", "reason": f"利润不稳/为负但有净资产(PB {pb:.2f}),用市净率;跌破净资产常是底部信号", "computable": True}
    if has_revenue:
        return {"archetype": "未盈利成长型", "anchor": "PS(市销率)", "reason": "无稳定利润但有收入,用市销率看收入规模对应估值", "computable": True}
    return {"archetype": "纯预期", "anchor": "无法估值", "reason": "无利润+无收入+无有效净资产(纯管线/pre-revenue/濒退),相对法失效,暂标纯预期驱动", "computable": False}


def anchor_text(code: str) -> str:
    """一行文本,供塞进基本面 info。"""
    a = pick_anchor(code)
    return f"{a['anchor']}(原型:{a['archetype']};依据:{a['reason']})"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("用法: python swing/valuation.py <代码>"); return
    a = pick_anchor(sys.argv[1])
    print(f"原型: {a['archetype']}\n主锚: {a['anchor']}\n依据: {a['reason']}\n可算: {a['computable']}")


if __name__ == "__main__":
    main()
