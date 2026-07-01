import { z } from 'zod';

export const TradeActionSchema = z.enum(['BUY', 'SELL', 'DEPOSIT', 'WITHDRAW', 'OTHER']);

export const TradeSchema = z.object({
  date: z.string(),
  code: z.string(),
  tsCode: z.string(),
  name: z.string(),
  rawAction: z.string(),
  action: TradeActionSchema,
  qty: z.number(),
  price: z.number(),
  amount: z.number(),
  occurAmount: z.number(),
  fee: z.number(),
  stampTax: z.number(),
  otherFee: z.number(),
  balanceAfter: z.number(),
  market: z.string(),
  contractNo: z.string(),
  tradeNo: z.string(),
}).strict();

export type TradeAction = z.infer<typeof TradeActionSchema>;
export type Trade = z.infer<typeof TradeSchema>;
