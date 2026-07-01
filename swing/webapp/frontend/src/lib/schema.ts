// /api/train 返回 payload 的 zod 校验(运行时守护后端契约,顺带出 TS 类型)。
import { z } from 'zod'

export const SignalSchema = z.object({
  ts: z.string(),
  date: z.string(),
  name: z.string().optional(),
  score: z.number(),
  board: z.string().optional(),
  tier: z.union([z.number(), z.string()]).optional(),
  price: z.number().nullable().optional(),
  donret: z.number().nullable().optional(),
  czret: z.number().nullable().optional(),
}).passthrough()   // 其余字段(donexit/czexit/maxfwd…)放行,不强约束

export const TrainPayloadSchema = z.object({
  ok: z.boolean().optional(),
  cached: z.boolean().optional(),
  signals: z.array(SignalSchema),
  latest: z.string().optional(),
  ntrade: z.number().optional(),
  cal: z.array(z.string()).optional(),
  tier: z.number().optional(),
  mode: z.string().optional(),
  start: z.string().optional(),
  pivot: z.string().optional(),
  banner: z.any().optional(),
}).passthrough()

export type TrainPayload = z.infer<typeof TrainPayloadSchema>
export type Signal = z.infer<typeof SignalSchema>
