import { describe, it, expect } from 'vitest';
import { buildLadder, calcSuccessRates } from './limitUp';
import type { LimitStock } from './limitUp';

// ── helper ────────────────────────────────────────────────────────────────────

function makeLimitStock(overrides: Partial<LimitStock> & { tsCode: string }): LimitStock {
  return {
    name: 'TestStock',
    industry: 'Tech',
    close: 10,
    pctChg: 10,
    amount: 1000,
    turnoverRatio: 5,
    openTimes: 0,
    limitTimes: 1,
    fdAmount: 5000,
    upStat: '1/1',
    firstTime: '093000',
    lastTime: '150000',
    limitType: 'U',
    luDesc: '',
    ...overrides,
  };
}

// ── buildLadder ───────────────────────────────────────────────────────────────

describe('buildLadder', () => {
  it('case 1: single first-board stock produces one group with days=1', () => {
    const stocks = [makeLimitStock({ tsCode: 'A.SZ', limitTimes: 1 })];
    const ladder = buildLadder('20240101', stocks);

    expect(ladder.tradeDate).toBe('20240101');
    expect(ladder.total).toBe(1);
    expect(ladder.groups).toHaveLength(1);
    expect(ladder.groups[0].days).toBe(1);
    expect(ladder.groups[0].stocks).toHaveLength(1);
    expect(ladder.groups[0].stocks[0].tsCode).toBe('A.SZ');
  });

  it('case 2: stocks at different limitTimes (1, 2, 4) produce 3 groups sorted high-to-low', () => {
    const stocks = [
      makeLimitStock({ tsCode: 'A.SZ', limitTimes: 1 }),
      makeLimitStock({ tsCode: 'B.SZ', limitTimes: 4 }),
      makeLimitStock({ tsCode: 'C.SZ', limitTimes: 2 }),
    ];
    const ladder = buildLadder('20240102', stocks);

    expect(ladder.total).toBe(3);
    expect(ladder.groups).toHaveLength(3);
    // groups sorted descending by days
    expect(ladder.groups.map((g) => g.days)).toEqual([4, 2, 1]);
  });

  it('case 3: stocks within a group are ordered by fdAmount descending', () => {
    const stocks = [
      makeLimitStock({ tsCode: 'Low.SZ',  limitTimes: 2, fdAmount: 1000 }),
      makeLimitStock({ tsCode: 'High.SZ', limitTimes: 2, fdAmount: 9000 }),
      makeLimitStock({ tsCode: 'Mid.SZ',  limitTimes: 2, fdAmount: 5000 }),
    ];
    const ladder = buildLadder('20240103', stocks);

    expect(ladder.groups).toHaveLength(1);
    const ordered = ladder.groups[0].stocks.map((s) => s.tsCode);
    expect(ordered).toEqual(['High.SZ', 'Mid.SZ', 'Low.SZ']);
  });

  it('case 4: empty stocks array returns empty groups with total=0', () => {
    const ladder = buildLadder('20240104', []);

    expect(ladder.tradeDate).toBe('20240104');
    expect(ladder.total).toBe(0);
    expect(ladder.groups).toHaveLength(0);
  });
});

// ── calcSuccessRates ──────────────────────────────────────────────────────────

describe('calcSuccessRates', () => {
  it('case 5: all first-board stocks reappear next day → rate 1.0', () => {
    const d1 = '20240101';
    const d2 = '20240102';
    const stocks = [
      makeLimitStock({ tsCode: 'A.SZ', limitTimes: 1 }),
      makeLimitStock({ tsCode: 'B.SZ', limitTimes: 1 }),
    ];
    // next day same codes still in limit list (now limitTimes=2, but tsCode match is what counts)
    const nextDayStocks = stocks.map((s) => ({ ...s, limitTimes: 2 }));

    const data = new Map([
      [d1, stocks],
      [d2, nextDayStocks],
    ]);

    const rows = calcSuccessRates([d1, d2], data);
    const row = rows.find((r) => r.days === 1);

    expect(row).toBeDefined();
    expect(row!.attempts).toBe(2);
    expect(row!.successes).toBe(2);
    expect(row!.rate).toBe(1.0);
  });

  it('case 6: no first-board stocks appear next day → rate 0', () => {
    const d1 = '20240101';
    const d2 = '20240102';
    const stocks = [makeLimitStock({ tsCode: 'A.SZ', limitTimes: 1 })];
    // next day has different stocks
    const nextDayStocks = [makeLimitStock({ tsCode: 'Z.SZ', limitTimes: 1 })];

    const data = new Map([
      [d1, stocks],
      [d2, nextDayStocks],
    ]);

    const rows = calcSuccessRates([d1, d2], data);
    const row = rows.find((r) => r.days === 1);

    expect(row).toBeDefined();
    expect(row!.successes).toBe(0);
    expect(row!.rate).toBe(0);
  });

  it('case 7: only one date in list → no pairs, returns empty array', () => {
    // With a single date there is no "next day", so no counters are built.
    const d1 = '20240101';
    const stocks = [makeLimitStock({ tsCode: 'A.SZ', limitTimes: 1 })];
    const data = new Map([[d1, stocks]]);

    const rows = calcSuccessRates([d1], data);
    expect(rows).toHaveLength(0);
  });

  it('case 8: aggregation across multiple days accumulates attempts and successes', () => {
    const d1 = '20240101';
    const d2 = '20240102';
    const d3 = '20240103';

    const stocksD1 = [
      makeLimitStock({ tsCode: 'A.SZ', limitTimes: 1 }),
      makeLimitStock({ tsCode: 'B.SZ', limitTimes: 1 }),
    ];
    // d2: A succeeded (stays), B did not
    const stocksD2 = [makeLimitStock({ tsCode: 'A.SZ', limitTimes: 2 })];
    // d3: A succeeded again
    const stocksD3 = [makeLimitStock({ tsCode: 'A.SZ', limitTimes: 3 })];

    const data = new Map([
      [d1, stocksD1],
      [d2, stocksD2],
      [d3, stocksD3],
    ]);

    const rows = calcSuccessRates([d1, d2, d3], data);

    // days=1: 2 attempts (d1), 1 success (A on d2) → rate 0.5
    const row1 = rows.find((r) => r.days === 1);
    expect(row1).toBeDefined();
    expect(row1!.attempts).toBe(2);
    expect(row1!.successes).toBe(1);
    expect(row1!.rate).toBeCloseTo(0.5);

    // days=2: 1 attempt (A on d2), 1 success (A on d3) → rate 1.0
    const row2 = rows.find((r) => r.days === 2);
    expect(row2).toBeDefined();
    expect(row2!.attempts).toBe(1);
    expect(row2!.successes).toBe(1);
    expect(row2!.rate).toBe(1.0);
  });
});
