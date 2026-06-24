import duckdb, bisect, datetime, json
con=duckdb.connect('stock_data_tushare.duckdb', read_only=True)

ipos=[
 ('601138.SH','工业富联','2018-06-08',271,'电子制造','熊市低位'),
 ('601319.SH','中国人保','2018-11-16',60,'保险','熊市低位'),
 ('601658.SH','邮储银行','2019-12-10',327,'银行','震荡市'),
 ('601816.SH','京沪高铁','2020-01-16',307,'铁路运输','牛市高位'),
 ('688981.SH','中芯国际','2020-07-16',532,'半导体','牛市高位'),
 ('601728.SH','中国电信','2021-08-20',471,'通信运营','震荡市'),
 ('600941.SH','中国移动','2022-01-05',487,'通信运营','牛市高位'),
 ('600938.SH','中国海油','2022-04-21',323,'石油开采','熊市低位'),
]
INDICES={'sse':('000001.SH','上证综指'),'cyb':('399006.SZ','创业板指')}
idxdata={}
for k,(code,nm) in INDICES.items():
    rs=con.execute("select trade_date,close from index_daily where ts_code=? order by trade_date",[code]).fetchall()
    idxdata[k]={'dates':[r[0] for r in rs],'close':[r[1] for r in rs],'name':nm}

offsets=[-60,-20,-5,0,5,20,60,120]
def win(k,d):
    dts=idxdata[k]['dates']; cl=idxdata[k]['close']
    pos=bisect.bisect_left(dts,d); base=cl[pos]
    out={'pct':{}, 'series':[]}
    for o in offsets:
        p=pos+o
        out['pct'][o]=round(cl[p]/base*100-100,2) if 0<=p<len(cl) else None
    for o in range(-40,121):
        p=pos+o
        if 0<=p<len(cl): out['series'].append([o,round(cl[p]/base*100,2)])
    # trailing 1y percentile
    w=cl[max(0,pos-250):pos+1]
    out['pctile']=round(sum(1 for x in w if x<base)/len(w)*100)
    return out

res=[]
for code,name,ld,fin,ind,reg in ipos:
    d=datetime.date.fromisoformat(ld)
    row={'code':code,'name':name,'list_date':ld,'fin':fin,'industry':ind,'regime':reg,'idx':{}}
    for k in INDICES: row['idx'][k]=win(k,d)
    rows=con.execute("select trade_date,close,pct_chg from daily where ts_code=? order by trade_date limit 130",[code]).fetchall()
    fc=rows[0][1]
    row['stk_series']=[[i,round(cl/fc*100,2)] for i,(td,cl,pc) in enumerate(rows)]
    mx=max(r[1] for r in rows)
    row['stk']={'first_close':round(fc,2),'first_day_pct':round(rows[0][2],2),
        'd20':round(rows[20][1]/fc*100-100,2) if len(rows)>20 else None,
        'd60':round(rows[60][1]/fc*100-100,2) if len(rows)>60 else None,
        'd120':round(rows[120][1]/fc*100-100,2) if len(rows)>120 else None,
        'max_gain':round(mx/fc*100-100,2)}
    res.append(row)
json.dump(res,open('ipo_v2.json','w',encoding='utf-8'),ensure_ascii=False)
print('OK',len(res))
