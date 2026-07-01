import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';
import { parseTradeFile } from './parser';
import { AppError } from './error';

const fixturePath = (name: string) => join(process.cwd(), 'tests', 'fixtures', name);
const readFixture = (name: string) => readFileSync(fixturePath(name));

describe('parseTradeFile', () => {
  it('sample-200 parses 22 trades', () => {
    const buf = readFixture('sample-200.xls');
    const trades = parseTradeFile(buf);
    expect(trades).toHaveLength(22);
  });

  it('sample-200 first trade is DEPOSIT with balanceAfter 100000', () => {
    const buf = readFixture('sample-200.xls');
    const trades = parseTradeFile(buf);
    expect(trades[0].action).toBe('DEPOSIT');
    expect(trades[0].balanceAfter).toBe(100000);
  });

  it('sample-200 contains at least one BUY action', () => {
    const buf = readFixture('sample-200.xls');
    const trades = parseTradeFile(buf);
    expect(trades.some((t) => t.action === 'BUY')).toBe(true);
  });

  it('sample-200 contains at least one SELL action', () => {
    const buf = readFixture('sample-200.xls');
    const trades = parseTradeFile(buf);
    expect(trades.some((t) => t.action === 'SELL')).toBe(true);
  });

  it('sample-200 tsCode is properly attached for BUY/SELL trades', () => {
    const buf = readFixture('sample-200.xls');
    const trades = parseTradeFile(buf);
    const stockTrade = trades.find((t) => t.action === 'BUY' || t.action === 'SELL');
    expect(stockTrade).toBeDefined();
    expect(stockTrade!.tsCode).toMatch(/^\d{6}\.(SH|SZ|BJ)$/);
  });

  it('sample-200 dates are normalized to YYYY-MM-DD', () => {
    const buf = readFixture('sample-200.xls');
    const trades = parseTradeFile(buf);
    for (const trade of trades) {
      expect(trade.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
  });

  it('sample-empty returns []', () => {
    const buf = readFixture('sample-empty.xls');
    expect(() => parseTradeFile(buf)).not.toThrow();
    expect(parseTradeFile(buf)).toEqual([]);
  });

  it('sample-malformed throws AppError with kind=parse', () => {
    const buf = readFixture('sample-malformed.xls');
    let caught: unknown;
    try { parseTradeFile(buf); } catch (e) { caught = e; }
    expect(caught).toBeInstanceOf(AppError);
    expect((caught as AppError).kind).toBe('parse');
  });
});
