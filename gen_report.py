import json, statistics as st
res=json.load(open('ipo_full.json',encoding='utf-8'))
offs=['-60','-20','-5','5','20','60','120']
agg={}
for o in offs:
    vals=[r['idx'][o]['pct'] for r in res if r['idx'][o]['pct'] is not None]
    agg[o]={'avg':round(st.mean(vals),2),'neg':sum(1 for v in vals if v<0),'n':len(vals)}
stkavg={k:round(st.mean([r['stk'][k] for r in res if r['stk'].get(k) is not None]),2) for k in ['first_day_pct','d20','d60','d120','max_gain']}
DATA=json.dumps(res,ensure_ascii=False)
AGG=json.dumps(agg,ensure_ascii=False)
STK=json.dumps(stkavg,ensure_ascii=False)

TPL = open('template.html',encoding='utf-8').read()
out = TPL.replace('__DATA__',DATA).replace('__AGG__',AGG).replace('__STK__',STK)
open('ipo_index_study.html','w',encoding='utf-8').write(out)
print('OK')
