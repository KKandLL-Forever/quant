import re, json
line = open(r'D:\code\quart\first10Result\batch_20260501_000546.html', encoding='utf-8').readlines()[56]
m = re.search(r'const signals=(\[.*?\]);', line)
data = json.loads(m.group(1))

rows = []
for s in data:
    rb = s['ret_base1']
    rows.append({
        'name': s['name'], 'code': s['code'], 'date': s['date'], 'year': s['year'],
        'pct': s['pct_chg'], 'vol': s['vol_ratio'], 'amp': s['amplitude'],
        'sl': s['sideways_low'], 'sh': s['sideways_high'],
        'close': s['close'], 'op': s['open_price'], 'exit': s.get('exit_reason'),
        'r5': rb.get('5'), 'r21': rb.get('21'), 'r42': rb.get('42'), 'r63': rb.get('63'),
    })

def fx(v):
    return f"{v:.1f}" if v is not None else "NA"

rows.sort(key=lambda x: (x['r63'] is None, -(x['r63'] if x['r63'] is not None else 0)))
print(f"{'name':<10}{'date':<12}{'yr':<6}{'pct':<7}{'vol':<6}{'amp':<6}{'r5':<7}{'r21':<7}{'r42':<7}{'r63':<7}{'exit':<8}")
print("--- TOP 15 by 63d ---")
for r in rows[:15]:
    print(f"{r['name']:<10}{r['date']:<12}{r['year']:<6}{r['pct']:<7.2f}{r['vol']:<6.2f}{r['amp']:<6.2f}{fx(r['r5']):<7}{fx(r['r21']):<7}{fx(r['r42']):<7}{fx(r['r63']):<7}{str(r['exit']):<8}")

print("\n--- BOTTOM 8 by 63d ---")
for r in rows[-8:]:
    print(f"{r['name']:<10}{r['date']:<12}{r['year']:<6}{r['pct']:<7.2f}{r['vol']:<6.2f}{r['amp']:<6.2f}{fx(r['r5']):<7}{fx(r['r21']):<7}{fx(r['r42']):<7}{fx(r['r63']):<7}{str(r['exit']):<8}")

# Aggregate stats: top quartile vs bottom quartile
valid = [r for r in rows if r['r63'] is not None]
n = len(valid)
top = valid[:n//4]
bot = valid[-n//4:]
def avg(lst, k):
    vals = [x[k] for x in lst if x[k] is not None]
    return sum(vals)/len(vals) if vals else None

print("\n--- 分位对比 (top25% vs bottom25%) ---")
print(f"count: top={len(top)} bot={len(bot)} total_valid={n} total={len(rows)}")
for k in ('pct','vol','amp','r5','r21','r42','r63'):
    print(f"  {k}: top={avg(top,k):.2f}  bot={avg(bot,k):.2f}")

# Year distribution of winners
from collections import Counter
print("\nWinners (r63>20%) year dist:", Counter(r['year'] for r in valid if r['r63']>20))
print("Losers (r63<-10%) year dist:", Counter(r['year'] for r in valid if r['r63']<-10))

# Exit reason for winners
print("\n大幅盈利(r63>30%):")
for r in [x for x in valid if x['r63']>30]:
    box_amp = (r['sh']/r['sl']-1)*100
    print(f"  {r['name']} {r['date']} pct={r['pct']:.1f} vol={r['vol']:.1f}x amp={r['amp']:.1f}% boxRange={box_amp:.1f}% exit={r['exit']} r63={r['r63']:.1f}")
