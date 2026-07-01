import type { Trade, TradeAction } from './tradeTypes';
import { toTsCode } from './codeMap';
import { z } from 'zod';
import { AppError } from './error';
import { TradeSchema } from './schemas/trade';

const COL = {
  date: '成交日期',
  code: '证券代码',
  name: '证券名称',
  action: '操作',
  qty: '成交数量',
  price: '成交均价',
  amount: '成交金额',
  occur: '发生金额',
  fee: '手续费',
  stamp: '印花税',
  other: '其他杂费',
  balance: '本次金额',
  contract: '合同编号',
  tradeNo: '成交编号',
  market: '交易市场',
} as const;

function mapAction(raw: string): TradeAction {
  const s = raw.trim();
  // 普通买入 / 逆回购拆出（资金借出）→ 现金减
  if (s === '买入' || s === '证券买入' || s === '拆出') return 'BUY';
  // 普通卖出 / 逆回购到期购回 → 现金增
  if (s === '卖出' || s === '证券卖出' || s === '购回') return 'SELL';
  // 入金
  if (
    s === '开户' ||
    s === '解冻' ||
    s.includes('银证转入') ||
    s.includes('存入')
  )
    return 'DEPOSIT';
  // 出金
  if (s === '冻结' || s.includes('银证转出') || s.includes('取出'))
    return 'WITHDRAW';
  return 'OTHER';
}

function formatDate(raw: string): string {
  // 20260409 → 2026-04-09
  const s = raw.trim();
  if (/^\d{8}$/.test(s)) return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
  return s;
}

function num(v: string | undefined): number {
  if (v == null) return 0;
  const t = v.trim();
  if (!t) return 0;
  const n = Number(t);
  return Number.isFinite(n) ? n : 0;
}

/**
 * 解析券商导出的 GBK TSV（伪 .xls）
 * 接受 ArrayBuffer / Uint8Array / 已解码字符串
 */
export function parseTradeFile(input: ArrayBuffer | Uint8Array | string): Trade[] {
  let text: string;
  if (typeof input === 'string') {
    text = input;
  } else {
    const buf = input instanceof Uint8Array ? input : new Uint8Array(input);
    text = new TextDecoder('gbk').decode(buf);
  }

  // 去 BOM、统一换行
  text = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
  const lines = text.split('\n').filter((l) => l.trim().length > 0);
  if (lines.length < 2) return [];

  const header = lines[0].split('\t').map((h) => h.trim());
  const idx = (key: string) => header.indexOf(key);

  const iDate = idx(COL.date);
  const iCode = idx(COL.code);
  const iName = idx(COL.name);
  const iAction = idx(COL.action);
  const iQty = idx(COL.qty);
  const iPrice = idx(COL.price);
  const iAmount = idx(COL.amount);
  const iOccur = idx(COL.occur);
  const iFee = idx(COL.fee);
  const iStamp = idx(COL.stamp);
  const iOther = idx(COL.other);
  const iBalance = idx(COL.balance);
  const iContract = idx(COL.contract);
  const iTradeNo = idx(COL.tradeNo);
  const iMarket = idx(COL.market);

  if (iDate < 0 || iAction < 0) {
    throw AppError.parse('表头无法识别，请确认是券商导出的对账单', `检测到表头: ${header.join(' | ')}`);
  }

  const trades: Trade[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split('\t');
    const rawCode = (cells[iCode] || '').trim();
    const market = (cells[iMarket] || '').trim();
    const rawAction = (cells[iAction] || '').trim();
    trades.push({
      date: formatDate(cells[iDate] || ''),
      code: rawCode,
      tsCode: toTsCode(rawCode, market),
      name: (cells[iName] || '').trim(),
      rawAction,
      action: mapAction(rawAction),
      qty: num(cells[iQty]),
      price: num(cells[iPrice]),
      amount: num(cells[iAmount]),
      occurAmount: num(cells[iOccur]),
      fee: num(cells[iFee]),
      stampTax: num(cells[iStamp]),
      otherFee: num(cells[iOther]),
      balanceAfter: num(cells[iBalance]),
      market,
      contractNo: (cells[iContract] || '').trim(),
      tradeNo: (cells[iTradeNo] || '').trim(),
    });
  }
  const result = z.array(TradeSchema).safeParse(trades);
  if (!result.success) {
    throw AppError.validation('对账单字段不符合预期', result.error);
  }
  return result.data;
}
