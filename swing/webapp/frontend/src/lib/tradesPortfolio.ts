import type {
  Holding,
  PortfolioState,
  RealizedRecord,
  Trade,
} from './tradeTypes';
import { isReverseRepo } from './codeMap';

interface MutableHolding {
  code: string;
  tsCode: string;
  name: string;
  qty: number;
  costBasis: number; // 含费总成本
}

const EPS = 1e-6;

function feeTotal(t: Trade): number {
  return t.fee + t.stampTax + t.otherFee;
}

/**
 * 按时间正序处理流水，得到当前持仓 / 已实现盈亏 / 现金余额。
 *
 * 加权平均成本规则：
 *   买入: cost += 成交金额 + 各项费用； qty += 成交数量； avg = cost / qty
 *   卖出: 已实现 = 卖出净收入 - 卖出数量 * 当前 avg
 *         qty -= 卖出数量； cost -= 卖出数量 * avg；avg 保持不变
 *         qty 清零后该持仓被移除（再买入会重新建仓）
 *
 * 现金规则：BUY -= 发生金额, SELL += 发生金额, DEPOSIT += 发生金额, WITHDRAW -= 发生金额
 */
export function buildPortfolio(rawTrades: Trade[]): PortfolioState {
  // 文件是倒序（最新在前），按 (date, tradeNo) 升序处理
  const trades = [...rawTrades].sort((a, b) => {
    if (a.date !== b.date) return a.date < b.date ? -1 : 1;
    return a.tradeNo.localeCompare(b.tradeNo);
  });

  const holdings = new Map<string, MutableHolding>();
  const realized: RealizedRecord[] = [];
  let cash = 0;
  let totalDeposit = 0;
  let totalWithdraw = 0;
  let totalRealizedPnl = 0;
  let totalInterestIncome = 0;

  for (const t of trades) {
    switch (t.action) {
      case 'DEPOSIT':
        cash += t.occurAmount;
        totalDeposit += t.occurAmount;
        break;

      case 'WITHDRAW':
        cash -= t.occurAmount;
        totalWithdraw += t.occurAmount;
        break;

      case 'BUY': {
        cash -= t.occurAmount;
        if (!t.code) break;
        const key = t.tsCode || t.code;
        const h =
          holdings.get(key) ??
          ({
            code: t.code,
            tsCode: t.tsCode,
            name: t.name,
            qty: 0,
            costBasis: 0,
          } as MutableHolding);
        h.name = t.name || h.name;
        h.qty += t.qty;
        h.costBasis += t.amount + feeTotal(t);
        holdings.set(key, h);
        break;
      }

      case 'SELL': {
        cash += t.occurAmount;
        if (!t.code) break;
        const key = t.tsCode || t.code;
        const h = holdings.get(key);
        if (!h || h.qty < EPS) {
          // 数据异常：卖出无对应持仓，跳过仓位计算（不影响现金）
          break;
        }
        const avgBefore = h.costBasis / h.qty;
        const sellQty = Math.min(t.qty, h.qty);
        const proceeds = t.occurAmount; // 已扣费
        const costPortion = avgBefore * sellQty;
        const pnl = proceeds - costPortion;
        const interest = isReverseRepo(h.code);

        realized.push({
          code: h.code,
          tsCode: h.tsCode,
          name: h.name,
          date: t.date,
          qtySold: sellQty,
          avgCostAtSale: avgBefore,
          sellPrice: t.price,
          proceeds,
          costPortion,
          pnl,
          pnlPct: costPortion > 0 ? pnl / costPortion : 0,
          isInterest: interest,
        });
        if (interest) {
          totalInterestIncome += pnl;
        } else {
          totalRealizedPnl += pnl;
        }

        h.qty -= sellQty;
        h.costBasis -= costPortion;
        if (h.qty < EPS) {
          holdings.delete(key);
        }
        break;
      }

      case 'OTHER':
      default:
        // 未识别操作不影响现金，避免误算
        break;
    }
  }

  const holdingList: Holding[] = Array.from(holdings.values())
    .filter((h) => !isReverseRepo(h.code))
    .map((h) => ({
      code: h.code,
      tsCode: h.tsCode,
      name: h.name,
      qty: h.qty,
      avgCost: h.qty > 0 ? h.costBasis / h.qty : 0,
      costBasis: h.costBasis,
    }))
    .sort((a, b) => b.costBasis - a.costBasis);

  // 找最后一笔记录的本次金额（按时间正序的最后一行）
  const last = trades[trades.length - 1];
  const first = trades[0];
  const reportedFinalBalance = last ? last.balanceAfter : 0;

  return {
    holdings: holdingList,
    realized,
    cash,
    totalDeposit,
    totalWithdraw,
    totalRealizedPnl,
    totalInterestIncome,
    reportedFinalBalance,
    firstDate: first?.date ?? '',
    lastDate: last?.date ?? '',
  };
}
