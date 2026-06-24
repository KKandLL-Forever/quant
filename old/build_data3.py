import duckdb, bisect, datetime, json
con=duckdb.connect('stock_data_tushare.duckdb', read_only=True)

# (code,name,list_date,fin亿,industry,board,regime)
ipos=[
 ('601138.SH','工业富联','2018-06-08',271,'电子制造','主板','熊市低位'),
 ('601319.SH','中国人保','2018-11-16',60,'保险','主板','熊市低位'),
 ('601658.SH','邮储银行','2019-12-10',327,'银行','主板','震荡市'),
 ('601816.SH','京沪高铁','2020-01-16',307,'铁路运输','主板','牛市高位'),
 ('688981.SH','中芯国际','2020-07-16',532,'半导体','科创板','牛市高位'),
 ('601728.SH','中国电信','2021-08-20',471,'通信运营','主板','震荡市'),
 ('600941.SH','中国移动','2022-01-05',487,'通信运营','主板','牛市高位'),
 ('600938.SH','中国海油','2022-04-21',323,'石油开采','主板','熊市低位'),
 ('688775.SH','影石创新','2025-06-11',19,'消费电子','科创板','牛市高位'),
 ('600930.SH','华电新能','2025-07-16',180,'电力','主板','牛市高位'),
 ('688795.SH','摩尔线程','2025-12-05',88,'半导体(GPU)','科创板','牛市高位'),
 ('688802.SH','沐曦股份','2025-12-17',40,'半导体(GPU)','科创板','牛市高位'),
]
INDICES={'sse':('000001.SH','上证综指'),'cyb':('399006.SZ','创业板指'),'star':('000680.SH','科创综指')}
idx={}
for k,(code,nm) in INDICES.items():
    rs=con.execute("select trade_date,open,close,low,high,pct_chg from index_daily where ts_code=? order by trade_date",[code]).fetchall()
    idx[k]={'dates':[r[0] for r in rs],'open':[r[1] for r in rs],'close':[r[2] for r in rs],
            'low':[r[3] for r in rs],'high':[r[4] for r in rs],'pct':[r[5] for r in rs],'name':nm}

offsets=[-60,-20,-5,0,5,20,60,120]
def win(k,d):
    D=idx[k]['dates']; CL=idx[k]['close']; PC=idx[k]['pct']
    if not D or d<D[0] or d>D[-1]: return None
    pos=bisect.bisect_left(D,d)
    if pos>=len(D): return None
    base=CL[pos]
    o={'cum':{},'series':[],'daily':{}}
    for off in offsets:
        p=pos+off
        o['cum'][off]=round(CL[p]/base*100-100,2) if 0<=p<len(CL) else None
    for off in range(-60,121):
        p=pos+off
        if 0<=p<len(CL): o['series'].append([off,round(CL[p]/base*100,2)])
    # listing-day own move + neighbours (daily pct_chg of the INDEX)
    for off in range(-2,4):
        p=pos+off
        o['daily'][off]=round(PC[p],2) if 0<=p<len(PC) and PC[p] is not None else None
    w=CL[max(0,pos-250):pos+1]
    o['pctile']=round(sum(1 for x in w if x<base)/len(w)*100)
    # OHLC candlestick around listing day: -60..+60  -> [offset, date, open, close, low, high]
    OP=idx[k]['open']; LO=idx[k]['low']; HI=idx[k]['high']
    o['ohlc']=[]
    for off in range(-60,61):
        p=pos+off
        if 0<=p<len(CL):
            o['ohlc'].append([off,str(D[p]),round(OP[p],2),round(CL[p],2),round(LO[p],2),round(HI[p],2)])
    o['list_idx_date']=str(D[pos])
    return o

res=[]
for code,name,ld,fin,ind,board,reg in ipos:
    d=datetime.date.fromisoformat(ld)
    row={'code':code,'name':name,'list_date':ld,'fin':fin,'industry':ind,'board':board,'regime':reg,'idx':{}}
    for k in INDICES:
        row['idx'][k]=win(k,d)
    rows=con.execute("select trade_date,close,pct_chg from daily where ts_code=? order by trade_date limit 130",[code]).fetchall()
    fc=rows[0][1]
    row['stk_series']=[[i,round(cl/fc*100,2)] for i,(td,cl,pc) in enumerate(rows)]
    mx=max(r[1] for r in rows)
    def gv(i): return round(rows[i][1]/fc*100-100,2) if len(rows)>i else None
    row['stk']={'first_close':round(fc,2),'first_day_pct':round(rows[0][2],2),
        'd20':gv(20),'d60':gv(60),'d120':gv(120),'max_gain':round(mx/fc*100-100,2),'n':len(rows)}
    res.append(row)
json.dump(res,open('ipo_v3.json','w',encoding='utf-8'),ensure_ascii=False)
print('OK',len(res))
