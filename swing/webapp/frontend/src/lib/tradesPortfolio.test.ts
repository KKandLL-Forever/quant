import { describe, it, expect } from 'vitest';
import type { Trade } from './tradeTypes';
import { buildPortfolio } from './tradesPortfolio';

const makeTrade = (overrides: Partial<Trade> = {}): Trade => ({
  date: '2026-01-01',
  code: '600110',
  tsCode: '600110.SH',
  name: '宏和科技',
  rawAction: '证券买入',
  action: 'BUY',
  qty: 100,
  price: 10,
  amount: 1000,
  occurAmount: 1005,
  fee: 5,
  stampTax: 0,
  otherFee: 0,
  balanceAfter: 100000,
  market: '上海A股',
  contractNo: 'T0001',
  tradeNo: 'N0001',
  ...overrides,
});

describe('buildPortfolio', () => {
  it('1. single BUY creates Holding with correct qty, avgCost, costBasis', () => {
    const trade = makeTrade();
    const result = buildPortfolio([trade]);

    expect(result.holdings).toHaveLength(1);
    const h = result.holdings[0];
    expect(h.qty).toBe(100);
    // costBasis = amount + fee + stampTax + otherFee = 1000 + 5 = 1005
    expect(h.costBasis).toBe(1005);
    // avgCost = costBasis / qty
    expect(h.avgCost).toBeCloseTo(1005 / 100, 6);
  });

  it('2. two BUYs of same stock produce weighted-average avgCost', () => {
    const buy1 = makeTrade({ qty: 100, amount: 1000, fee: 5, occurAmount: 1005, tradeNo: 'N0001' });
    const buy2 = makeTrade({ qty: 200, amount: 2200, fee: 10, occurAmount: 2210, tradeNo: 'N0002' });
    const result = buildPortfolio([buy1, buy2]);

    expect(result.holdings).toHaveLength(1);
    const h = result.holdings[0];
    expect(h.qty).toBe(300);
    // costBasis = (1000+5) + (2200+10) = 3215
    expect(h.costBasis).toBeCloseTo(3215, 6);
    expect(h.avgCost).toBeCloseTo(3215 / 300, 6);
  });

  it('3. partial SELL reduces qty, creates RealizedRecord, avgCost unchanged', () => {
    const buy = makeTrade({ qty: 200, amount: 2000, fee: 10, occurAmount: 2010, tradeNo: 'N0001' });
    // costBasis after buy = 2010, avgCost = 2010/200 = 10.05
    const sell = makeTrade({
      action: 'SELL',
      rawAction: '证券卖出',
      qty: 100,
      price: 12,
      amount: 1200,
      occurAmount: 1195,
      fee: 5,
      stampTax: 0,
      tradeNo: 'N0002',
      date: '2026-01-02',
    });
    const result = buildPortfolio([buy, sell]);

    expect(result.holdings).toHaveLength(1);
    const h = result.holdings[0];
    expect(h.qty).toBe(100);
    // avgCost should remain same after partial sell
    expect(h.avgCost).toBeCloseTo(2010 / 200, 6);

    expect(result.realized).toHaveLength(1);
    const r = result.realized[0];
    expect(r.qtySold).toBe(100);
    expect(r.proceeds).toBe(1195);
    // costPortion = avgBefore * sellQty = 10.05 * 100 = 1005
    expect(r.costPortion).toBeCloseTo(1005, 6);
    expect(r.pnl).toBeCloseTo(1195 - 1005, 6);
  });

  it('4. full SELL removes holding and creates RealizedRecord', () => {
    const buy = makeTrade({ qty: 100, amount: 1000, fee: 5, occurAmount: 1005, tradeNo: 'N0001' });
    const sell = makeTrade({
      action: 'SELL',
      rawAction: '证券卖出',
      qty: 100,
      price: 12,
      amount: 1200,
      occurAmount: 1194,
      fee: 6,
      tradeNo: 'N0002',
      date: '2026-01-02',
    });
    const result = buildPortfolio([buy, sell]);

    expect(result.holdings).toHaveLength(0);
    expect(result.realized).toHaveLength(1);
  });

  it('5. SELL then BUY again creates fresh Holding with new avgCost', () => {
    const buy1 = makeTrade({ qty: 100, amount: 1000, fee: 5, occurAmount: 1005, tradeNo: 'N0001' });
    const sell = makeTrade({
      action: 'SELL',
      rawAction: '证券卖出',
      qty: 100,
      price: 12,
      amount: 1200,
      occurAmount: 1194,
      fee: 6,
      tradeNo: 'N0002',
      date: '2026-01-02',
    });
    // Buy again at different price
    const buy2 = makeTrade({
      qty: 100,
      price: 15,
      amount: 1500,
      fee: 7,
      occurAmount: 1507,
      tradeNo: 'N0003',
      date: '2026-01-03',
    });
    const result = buildPortfolio([buy1, sell, buy2]);

    expect(result.holdings).toHaveLength(1);
    const h = result.holdings[0];
    expect(h.qty).toBe(100);
    // New costBasis = amount + fee = 1500 + 7 = 1507
    expect(h.costBasis).toBeCloseTo(1507, 6);
    expect(h.avgCost).toBeCloseTo(1507 / 100, 6);
  });

  it('6. DEPOSIT accumulates in totalDeposit and increases cash', () => {
    const deposit = makeTrade({
      action: 'DEPOSIT',
      rawAction: '银行转入',
      code: '',
      tsCode: '',
      qty: 0,
      price: 0,
      amount: 0,
      occurAmount: 50000,
      fee: 0,
      tradeNo: 'N0001',
    });
    const result = buildPortfolio([deposit]);

    expect(result.totalDeposit).toBe(50000);
    expect(result.cash).toBe(50000);
  });

  it('7. WITHDRAW accumulates in totalWithdraw and decreases cash', () => {
    const deposit = makeTrade({
      action: 'DEPOSIT',
      rawAction: '银行转入',
      code: '',
      tsCode: '',
      qty: 0,
      price: 0,
      amount: 0,
      occurAmount: 100000,
      fee: 0,
      tradeNo: 'N0001',
    });
    const withdraw = makeTrade({
      action: 'WITHDRAW',
      rawAction: '银行转出',
      code: '',
      tsCode: '',
      qty: 0,
      price: 0,
      amount: 0,
      occurAmount: 30000,
      fee: 0,
      tradeNo: 'N0002',
      date: '2026-01-02',
    });
    const result = buildPortfolio([deposit, withdraw]);

    expect(result.totalWithdraw).toBe(30000);
    expect(result.cash).toBe(70000);
  });

  it('8. reverse repo (204xxx) earnings go to totalInterestIncome, not totalRealizedPnl', () => {
    // 拆出 (BUY): buy 1 unit of 204001 at 100 (lending 100)
    const buy = makeTrade({
      code: '204001',
      tsCode: '204001.SH',
      name: '1天国债逆回购',
      qty: 1,
      price: 100,
      amount: 100,
      occurAmount: 100,
      fee: 0,
      tradeNo: 'N0001',
    });
    // 购回 (SELL): get back principal + interest
    const sell = makeTrade({
      action: 'SELL',
      rawAction: '逆回购到期',
      code: '204001',
      tsCode: '204001.SH',
      name: '1天国债逆回购',
      qty: 1,
      price: 100.1,
      amount: 100.1,
      occurAmount: 100.1, // proceeds > cost = interest
      fee: 0,
      tradeNo: 'N0002',
      date: '2026-01-02',
    });
    const result = buildPortfolio([buy, sell]);

    expect(result.realized).toHaveLength(1);
    const r = result.realized[0];
    expect(r.isInterest).toBe(true);
    // pnl = proceeds - costPortion = 100.1 - 100 = 0.1
    expect(r.pnl).toBeCloseTo(0.1, 6);
    expect(result.totalInterestIncome).toBeCloseTo(0.1, 6);
    expect(result.totalRealizedPnl).toBe(0);
    // Reverse repo holdings are filtered out of holdingList
    expect(result.holdings).toHaveLength(0);
  });

  it('9. reportedFinalBalance equals last trade balanceAfter (sorted order)', () => {
    const t1 = makeTrade({ date: '2026-01-01', tradeNo: 'N0001', balanceAfter: 90000 });
    const t2 = makeTrade({ date: '2026-01-02', tradeNo: 'N0002', balanceAfter: 95000 });
    const result = buildPortfolio([t1, t2]);

    expect(result.reportedFinalBalance).toBe(95000);
  });

  it('10. firstDate and lastDate reflect min/max dates from input', () => {
    const t1 = makeTrade({ date: '2026-01-05', tradeNo: 'N0001' });
    const t2 = makeTrade({ date: '2026-01-01', tradeNo: 'N0002' });
    const t3 = makeTrade({ date: '2026-01-10', tradeNo: 'N0003' });
    const result = buildPortfolio([t1, t2, t3]);

    expect(result.firstDate).toBe('2026-01-01');
    expect(result.lastDate).toBe('2026-01-10');
  });

  it('11. empty trades returns PortfolioState with zero values and empty arrays', () => {
    const result = buildPortfolio([]);

    expect(result.holdings).toHaveLength(0);
    expect(result.realized).toHaveLength(0);
    expect(result.cash).toBe(0);
    expect(result.totalDeposit).toBe(0);
    expect(result.totalWithdraw).toBe(0);
    expect(result.totalRealizedPnl).toBe(0);
    expect(result.totalInterestIncome).toBe(0);
    expect(result.reportedFinalBalance).toBe(0);
    expect(result.firstDate).toBe('');
    expect(result.lastDate).toBe('');
  });

  it('12. out-of-order trades produce same result as sorted input', () => {
    const tradeA = makeTrade({ date: '2026-01-01', tradeNo: 'N0001', qty: 100, amount: 1000, fee: 5, occurAmount: 1005 });
    const tradeB = makeTrade({ date: '2026-01-02', tradeNo: 'N0002', qty: 50, amount: 550, fee: 3, occurAmount: 553 });

    const sorted = buildPortfolio([tradeA, tradeB]);
    const reversed = buildPortfolio([tradeB, tradeA]);

    expect(reversed.holdings).toHaveLength(sorted.holdings.length);
    expect(reversed.holdings[0].qty).toBe(sorted.holdings[0].qty);
    expect(reversed.holdings[0].costBasis).toBeCloseTo(sorted.holdings[0].costBasis, 6);
    expect(reversed.cash).toBeCloseTo(sorted.cash, 6);
    expect(reversed.realized).toHaveLength(sorted.realized.length);
  });
});
