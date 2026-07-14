// LLM 分析缓存侧栏:主页左侧可伸缩竖栏,列全部已缓存分析,按日期降序分组、每组可折叠,
// 顶部可筛选;点击条目经 useAnaOpen() 秒开该缓存(命中缓存即弹窗直显)。监听 llm-cache-updated 事件自动刷新。
import React, { useState, useEffect, useMemo, useCallback } from 'react'
import { Tag, Input, Skeleton, Button } from 'antd'
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons'
import { useAnaOpen } from '../../App'

type CacheItem = { ts_code: string; code: string; name: string; date: string; action: string | null }

// 按板块给代码上色:科创=紫、创业=橙、主板=蓝(与 App.tsx 同口径)
const boardColor = (c: string) => c.startsWith('688') || c.startsWith('689') ? '#7c3aed'
  : (c.slice(0, 3) === '300' || c.slice(0, 3) === '301') ? '#c2410c' : '#3b6ea5'

// verdict 动作小标签(配色与弹窗内 verdict Tag 一致:买入green/卖出red/持有blue)
const actionTag = (a: string | null) => {
  if (a === '买入') return <Tag color="green" style={{ margin: 0, fontSize: 11, lineHeight: '16px' }}>买</Tag>
  if (a === '卖出') return <Tag color="red" style={{ margin: 0, fontSize: 11, lineHeight: '16px' }}>卖</Tag>
  if (a === '持有') return <Tag color="blue" style={{ margin: 0, fontSize: 11, lineHeight: '16px' }}>持</Tag>
  return null
}

const LS_KEY = 'llm-cache-sidebar-open'

export default function CacheSidebar() {
  const openCached = useAnaOpen()
  const [items, setItems] = useState<CacheItem[] | null>(null)
  const [open, setOpen] = useState<boolean>(() => { try { return localStorage.getItem(LS_KEY) !== '0' } catch { return true } })
  const [filter, setFilter] = useState('')
  const [openDates, setOpenDates] = useState<Record<string, boolean>>({})

  const fetchIdx = useCallback(async () => {
    try {
      const j = await (await fetch('/api/analyze/cache_index')).json()
      if (!j.ok) return
      const list: CacheItem[] = j.items || []
      setItems(list)
      setOpenDates(prev => {                                   // 默认展开最新日期一组(其余折叠)
        const dates = [...new Set(list.map(it => it.date))].sort().reverse()
        if (!dates.length || dates[0] in prev) return prev
        return { ...prev, [dates[0]]: true }
      })
    } catch { /* 忽略 */ }
  }, [])

  useEffect(() => { fetchIdx() }, [fetchIdx])
  useEffect(() => {
    const h = () => fetchIdx()                                // 新分析落盘后自动刷新
    window.addEventListener('llm-cache-updated', h)
    return () => window.removeEventListener('llm-cache-updated', h)
  }, [fetchIdx])
  useEffect(() => { try { localStorage.setItem(LS_KEY, open ? '1' : '0') } catch { /* 忽略 */ } }, [open])

  const groups = useMemo(() => {
    const q = filter.trim()
    const list = (items || []).filter(it => !q || it.name.includes(q) || it.code.includes(q) || it.ts_code.includes(q))
    const m: Record<string, CacheItem[]> = {}
    for (const it of list) (m[it.date] = m[it.date] || []).push(it)
    return Object.entries(m).sort((a, b) => (a[0] < b[0] ? 1 : -1))
  }, [items, filter])

  const toggleDate = (d: string) => setOpenDates(s => ({ ...s, [d]: !s[d] }))

  if (!open) return (
    <div style={{ ...railBase, width: 48, padding: '10px 0', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
      <div role="button" onClick={() => setOpen(true)} title="展开 LLM 分析缓存" style={toggleBtn}
        onMouseEnter={e => { e.currentTarget.style.background = 'var(--panel)'; e.currentTarget.style.color = 'var(--accent)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg)'; e.currentTarget.style.color = 'var(--ink-soft)' }}>
        <MenuUnfoldOutlined />
      </div>
      <div onClick={() => setOpen(true)} style={{ writingMode: 'vertical-rl', textOrientation: 'mixed', fontFamily: 'var(--font-display)', fontSize: 14, fontWeight: 600, color: 'var(--ink-soft)', letterSpacing: 3, cursor: 'pointer', userSelect: 'none' }}>LLM 分析缓存</div>
    </div>
  )

  return (
    <div style={{ ...railBase, width: 248, padding: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <b style={{ fontSize: 16, fontFamily: 'var(--font-display)' }}>LLM 分析缓存</b>
        <span style={{ fontSize: 12, color: 'var(--ink-soft)', fontVariantNumeric: 'tabular-nums' }}>{items?.length ?? 0}</span>
        <span style={{ flex: 1 }} />
        <Button size="small" type="text" onClick={() => fetchIdx()} title="刷新" style={{ padding: '0 4px', fontSize: 16, color: 'var(--ink-soft)' }}>↻</Button>
        <div role="button" onClick={() => setOpen(false)} title="收起" style={toggleBtn}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--panel)'; e.currentTarget.style.color = 'var(--accent)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg)'; e.currentTarget.style.color = 'var(--ink-soft)' }}>
          <MenuFoldOutlined />
        </div>
      </div>
      <Input size="small" allowClear placeholder="筛选名称/代码" value={filter} onChange={e => setFilter(e.target.value)} style={{ marginBottom: 8 }} />
      {items === null ? <Skeleton active paragraph={{ rows: 6 }} />
        : !groups.length ? <div style={{ fontSize: 12, color: '#b7b0a2', padding: '14px 0', textAlign: 'center' }}>暂无缓存分析</div>
        : groups.map(([date, list]) => {
          const isOpen = !!openDates[date]
          return (
            <div key={date} style={{ marginBottom: 6, border: '1px solid var(--line)', borderRadius: 7, overflow: 'hidden', background: 'var(--panel)' }}>
              <div onClick={() => toggleDate(date)} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', cursor: 'pointer', background: '#f1ede3', userSelect: 'none' }}>
                <span style={{ fontSize: 10, color: 'var(--ink-soft)', width: 10 }}>{isOpen ? '▾' : '▸'}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600 }}>{date}</span>
                <span style={{ fontSize: 11, color: 'var(--ink-soft)', background: '#fff', border: '1px solid var(--line)', borderRadius: 999, padding: '0 6px', marginLeft: 'auto' }}>{list.length}</span>
              </div>
              {isOpen && <div style={{ padding: 4 }}>
                {list.map(it => (
                  <div key={it.ts_code + it.date} onClick={() => openCached(it.ts_code, it.date, it.name)}
                    style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px', borderRadius: 5, cursor: 'pointer' }}
                    onMouseEnter={e => { e.currentTarget.style.background = '#f3efe5' }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12 }}>{it.name}</span>
                    {actionTag(it.action)}
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: boardColor(it.code) }}>{it.code}</span>
                  </div>
                ))}
              </div>}
            </div>
          )
        })}
    </div>
  )
}

const railBase: React.CSSProperties = {
  position: 'fixed', left: 0, top: 0, height: '100vh', overflowY: 'auto',
  background: 'var(--panel)', borderRight: '1px solid var(--line)', borderRadius: '0 10px 10px 0',
  boxShadow: '4px 0 20px -10px rgba(23,20,15,.22)', transition: 'width .15s', zIndex: 20,
}

// 展开/收起共用同一颗按钮(同尺寸·同表面·同chevron),仅箭头旋转,让两态读作同一个控件
const toggleBtn: React.CSSProperties = {
  width: 34, height: 34, flex: '0 0 auto', display: 'grid', placeItems: 'center',
  borderRadius: 9, cursor: 'pointer', color: 'var(--ink-soft)', fontSize: 17,
  background: 'var(--bg)', border: '1px solid var(--line)',
  boxShadow: '0 1px 2px rgba(23,20,15,.06)', transition: 'background .15s, color .15s',
}
