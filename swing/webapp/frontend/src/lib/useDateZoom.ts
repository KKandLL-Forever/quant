import { useState, useCallback, useRef } from 'react';

/** 缩放窗口的最小数据点跨度 */
const MIN_SPAN = 15;

export interface DateRange {
  start: string; // YYYYMMDD
  end: string;   // YYYYMMDD
}

/**
 * 共享日期缩放：滚轮以光标位置为锚点缩放、按住拖动平移。
 * masterDates 必须升序。返回的 range + handlers 给所有图表共用即可实现联动。
 *
 * 性能：手势用 ref 同步累积，渲染通过 requestAnimationFrame 合并，
 * 一帧内的多次滚轮只触发一次重绘。
 */
export function useDateZoom(masterDates: string[]) {
  const n = masterDates.length;
  // n 用 ref 同步保存，保证手势回调即使被 useCallback([]) 冻结也能读到最新长度。
  // （masterDates 异步加载：首帧 n=0，若回调闭包冻结了 n=0，拖动时 span 恒为 0、无法平移。）
  const nRef = useRef(n);
  nRef.current = n;
  // masterDates 也存 ref，供 focusDate 按日期反查索引（点击事件触发，需读最新值）。
  const datesRef = useRef(masterDates);
  datesRef.current = masterDates;
  // 当前窗口（索引）。null = 全量。用 ref 同步保存以便手势连续累积。
  const viewRef = useRef<{ s: number; e: number } | null>(null);
  const [, forceRender] = useState(0);
  const rafId = useRef<number | null>(null);
  const drag = useRef<{ x: number; s: number; e: number } | null>(null);

  const scheduleRender = useCallback(() => {
    if (rafId.current != null) return;
    rafId.current = requestAnimationFrame(() => {
      rafId.current = null;
      forceRender((x) => x + 1);
    });
  }, []);

  const clampWin = useCallback(
    (ns: number, span: number): { s: number; e: number } => {
      const nn = nRef.current;
      const sp = Math.max(MIN_SPAN, Math.min(nn - 1, span));
      let cs = Math.round(ns);
      if (cs < 0) cs = 0;
      if (cs + sp > nn - 1) cs = nn - 1 - sp;
      if (cs < 0) cs = 0;
      return { s: cs, e: Math.min(nn - 1, cs + sp) };
    },
    [],
  );

  // 当前有效窗口（夹取到现有数据范围）。读 ref，保证冻结的闭包也拿到最新值。
  const cur = useCallback((): { s: number; e: number } => {
    const nn = nRef.current;
    if (nn === 0) return { s: 0, e: 0 };
    const v = viewRef.current;
    if (!v) return { s: 0, e: nn - 1 };
    return clampWin(v.s, v.e - v.s);
  }, [clampWin]);

  const setView = useCallback(
    (w: { s: number; e: number } | null) => {
      const nn = nRef.current;
      viewRef.current = w && w.s <= 0 && w.e >= nn - 1 ? null : w;
      scheduleRender();
    },
    [scheduleRender],
  );

  // 原生 wheel 处理器：必须以非被动监听器绑定，否则 preventDefault 无效、页面会跟着滚动。
  const onWheel = useCallback(
    (ev: WheelEvent) => {
      const nn = nRef.current;
      if (nn < 2) return;
      ev.preventDefault();
      const el = ev.currentTarget as HTMLElement;
      const rect = el.getBoundingClientRect();
      const f = rect.width > 0 ? (ev.clientX - rect.left) / rect.width : 0.5;
      const { s: curS, e: curE } = cur();
      const span = curE - curS;
      const factor = ev.deltaY < 0 ? 0.8 : 1.25; // 上滚放大、下滚缩小
      const newSpan = Math.max(MIN_SPAN, Math.min(nn - 1, Math.round(span * factor)));
      const anchor = curS + f * span;
      setView(clampWin(anchor - f * newSpan, newSpan));
    },
    [clampWin, setView, cur],
  );

  const onPointerDown = useCallback((ev: React.PointerEvent) => {
    const { s, e } = cur();
    drag.current = { x: ev.clientX, s, e };
    ev.currentTarget.setPointerCapture?.(ev.pointerId);
  }, [cur]);

  const onPointerMove = useCallback(
    (ev: React.PointerEvent) => {
      if (!drag.current || nRef.current < 2) return;
      const rect = ev.currentTarget.getBoundingClientRect();
      const span = drag.current.e - drag.current.s;
      const pxPer = rect.width / Math.max(1, span);
      const deltaIdx = Math.round((ev.clientX - drag.current.x) / pxPer);
      if (deltaIdx === 0) return;
      setView(clampWin(drag.current.s - deltaIdx, span));
    },
    [clampWin, setView],
  );

  const onPointerUp = useCallback((ev: React.PointerEvent) => {
    drag.current = null;
    ev.currentTarget.releasePointerCapture?.(ev.pointerId);
  }, []);

  const reset = useCallback(() => setView(null), [setView]);

  // 定位到某日期并拉到最大缩放（MIN_SPAN 窗口，居中）。
  const focusDate = useCallback((date: string) => {
    const idx = datesRef.current.indexOf(date);
    if (idx < 0) return;
    const half = Math.floor(MIN_SPAN / 2);
    setView(clampWin(idx - half, MIN_SPAN));
  }, [clampWin, setView]);

  const { s, e } = cur();
  const range: DateRange | null = n === 0 ? null : { start: masterDates[s], end: masterDates[e] };

  return {
    range,
    onWheel, // 原生 wheel handler，需用非被动监听器绑定（见 ZoomBox）
    pointerHandlers: { onPointerDown, onPointerMove, onPointerUp },
    reset,
    focusDate,
    isZoomed: viewRef.current !== null,
  };
}

/** 过滤升序日期序列到 [range.start, range.end]，range 为 null 则原样返回 */
export function clipByRange<T>(rows: T[], dateOf: (t: T) => string, range: DateRange | null): T[] {
  if (!range) return rows;
  return rows.filter((r) => {
    const d = dateOf(r);
    return d >= range.start && d <= range.end;
  });
}

/**
 * 抽稀：点数超过 cap 时按步长降采样，始终保留首尾点。
 * 用于缩小时减少 SVG 节点数，提升渲染流畅度。
 */
export function decimate<T>(rows: T[], cap = 1500): T[] {
  if (rows.length <= cap) return rows;
  const step = Math.ceil(rows.length / cap);
  const out: T[] = [];
  for (let i = 0; i < rows.length; i += step) out.push(rows[i]);
  const last = rows[rows.length - 1];
  if (out[out.length - 1] !== last) out.push(last);
  return out;
}
