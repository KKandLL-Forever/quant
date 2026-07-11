// 共享外壳:导航项 / antd 主题 / Header / PageTitle / 骨架屏积木。供各页面复用,避免循环依赖。
import React from 'react'
import { Skeleton } from 'antd'

export const NAV_ITEMS = [
  { key: '/', label: 'ML 主升浪信号' },
  { key: '/advise', label: '缠论卖点提示' },
  { key: '/holdings', label: '持仓总览' },
  { key: '/limitup', label: '涨停统计' },
  { key: '/etfshare', label: 'ETF份额' },
  { key: '/bulltop', label: '牛市逃顶' },
  { key: '/xiaoxifu', label: '小西西弗' },
  { key: '/boll', label: 'BOLL突破信号' },
  { key: '/concept', label: '概念轮动' },
  { key: '/oversold', label: '超跌反弹' },
  { key: '/lianban', label: '连板信号' },
]

export const QUANT_THEME = {
  token: {
    colorPrimary: '#0b6e4f',
    colorInfo: '#0b6e4f',
    colorBgBase: '#f7f4ed',
    colorBgContainer: '#fffdf8',
    colorBgLayout: '#f7f4ed',
    colorText: '#17140f',
    colorTextSecondary: '#5b554a',
    colorBorder: '#e6e0d3',
    colorBorderSecondary: '#ece7db',
    borderRadius: 7,
    fontFamily: '"IBM Plex Sans", -apple-system, "PingFang SC", sans-serif',
    fontSize: 13,
    controlHeight: 34,
    boxShadow: '0 4px 20px -10px rgba(23,20,15,.18)',
  },
  components: {
    Table: { headerBg: '#f1ede3', headerColor: '#5b554a', headerSplitColor: '#e6e0d3',
      borderColor: '#ece7db', rowHoverBg: '#f3efe5', cellPaddingBlockSM: 7, fontWeightStrong: 600 },
    Card: { colorBorderSecondary: '#ece7db' },
    Button: { fontWeight: 500, primaryShadow: 'none' },
    Tabs: { inkBarColor: '#0b6e4f', itemSelectedColor: '#0b6e4f', itemColor: '#5b554a', titleFontSize: 14 },
    Modal: { titleFontSize: 16 },
    Statistic: { contentFontSize: 22 },
  },
}

export const PageTitle = ({ kicker, children }: { kicker?: string; children: React.ReactNode }) => (
  <div style={{ margin: '4px 0 18px' }}>
    {kicker && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: 2.5,
      color: 'var(--accent)', fontWeight: 500, textTransform: 'uppercase' }}>{kicker}</div>}
    <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 30, margin: '2px 0 0',
      letterSpacing: .2, color: 'var(--ink)' }}>{children}</h1>
  </div>
)

// ── 骨架屏积木:各页首帧加载态用,尺寸对齐真实块以降 CLS(布局位移) ──
const skelBlk: React.CSSProperties = { background: '#fffdf8', border: '1px solid #ece7db', borderRadius: 8, marginBottom: 14 }
const skelLine = (w: number | string, h = 14) => <Skeleton.Input active size="small" style={{ width: w, height: h, minWidth: 0 }} />
export const SkelStatus = ({ h = 60 }: { h?: number }) => (
  <div style={{ ...skelBlk, padding: 14, minHeight: h }}><Skeleton active paragraph={{ rows: 1, width: ['82%'] }} title={{ width: '28%' }} /></div>
)
export const SkelChart = ({ h = 240, title = true }: { h?: number; title?: boolean }) => (
  <div style={{ ...skelBlk, padding: 14 }}>{title && <div style={{ marginBottom: 10 }}>{skelLine(280)}</div>}
    <Skeleton.Node active style={{ width: '100%', height: h }}><span /></Skeleton.Node></div>
)
export const SkelTable = ({ rows = 8, title = true }: { rows?: number; title?: boolean }) => (
  <div style={{ ...skelBlk, padding: 14 }}>{title && <div style={{ marginBottom: 12 }}>{skelLine(320)}</div>}
    <Skeleton active title={false} paragraph={{ rows, width: '100%' }} /></div>
)
export const SkelStatRow = ({ n = 3 }: { n?: number }) => (
  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
    {Array.from({ length: n }).map((_, i) => <div key={i} style={{ ...skelBlk, marginBottom: 0, padding: 14, flex: '1 1 200px', minHeight: 120 }}><Skeleton active paragraph={{ rows: 2 }} title={{ width: '50%' }} /></div>)}
  </div>
)

export function Header() {
  const cur = window.location.hash.replace('#', '') || '/'
  const go = (k: string) => { window.location.hash = k }
  return (
    <div style={{ display: 'flex', alignItems: 'stretch', height: 64, background: 'var(--ink)',
      padding: '0 26px', marginBottom: 22, borderRadius: 10, boxShadow: '0 8px 30px -12px rgba(11,110,79,.45)',
      position: 'relative', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(120% 180% at 0% 0%, #0b6e4f33, transparent 55%)', pointerEvents: 'none' }} />
      <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', marginRight: 44, zIndex: 1 }}>
        <div style={{ fontFamily: 'var(--font-display)', color: '#fbf8f0', fontSize: 22, fontWeight: 600, lineHeight: 1, letterSpacing: .3 }}>
          量化策略台
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', color: '#0b6e4f', fontSize: 10, letterSpacing: 3, marginTop: 4, fontWeight: 500 }}>
          QUANT&nbsp;TERMINAL
        </div>
      </div>
      <nav style={{ display: 'flex', alignItems: 'stretch', gap: 4, zIndex: 1 }}>
        {NAV_ITEMS.map(it => {
          const on = cur === it.key
          return (
            <a key={it.key} onClick={() => go(it.key)} style={{
              display: 'flex', alignItems: 'center', padding: '0 18px', cursor: 'pointer',
              fontSize: 14, fontWeight: on ? 600 : 400, letterSpacing: .5,
              color: on ? '#fbf8f0' : '#9b958a',
              borderBottom: on ? '2px solid var(--accent)' : '2px solid transparent',
              transition: 'color .15s' }}
              onMouseEnter={e => { if (!on) e.currentTarget.style.color = '#d8d2c6' }}
              onMouseLeave={e => { if (!on) e.currentTarget.style.color = '#9b958a' }}>
              {it.label}
            </a>
          )
        })}
      </nav>
    </div>
  )
}
