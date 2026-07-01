// 连板日历龙头列:每交易日一列,竖排 6板及以上龙头(高板在上)。迁自 trade_dashboard,Tailwind→内联。
import type { DayBoard } from './BoardLineChart'
import { PLOT_LEFT, CHART_MARGIN } from './BoardLineChart'

const fmtDate = (d: string) => (d.length === 8 ? `${d.slice(4, 6)}/${d.slice(6, 8)}` : d)
const boardColor = (b: number) => (b >= 9 ? '#a01d13' : b >= 8 ? '#c0392b' : b >= 7 ? '#d5543f' : '#e07b39')

export function BoardGrid({ data, colW = 34, onSelectDate }: { data: DayBoard[]; colW?: number; onSelectDate?: (d: string) => void }) {
  const showNames = colW >= 44
  return (
    <div style={{ display: 'flex', paddingLeft: PLOT_LEFT, paddingRight: CHART_MARGIN.right }}>
      {data.map(day => (
        <div key={day.date} onPointerDown={e => e.stopPropagation()} onClick={() => onSelectDate?.(day.date)}
          style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0, borderRight: '1px solid var(--line)', cursor: 'pointer' }}>
          {showNames && <div style={{ fontSize: 9, color: 'var(--ink-soft)', textAlign: 'center', marginBottom: 2 }}>{fmtDate(day.date)}</div>}
          {day.dragons.map(d => (
            <div key={d.tsCode} title={`${d.name} ${d.board}板 · 点击定位`}
              style={{ background: boardColor(d.board), color: '#fff', fontSize: showNames ? 10 : 9, lineHeight: 1.3,
                padding: showNames ? '1px 3px' : '1px 0', marginBottom: 1, borderRadius: 2, textAlign: 'center',
                overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>
              {showNames ? <>{d.name} <span style={{ opacity: .8 }}>{d.board}</span></> : d.board}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
