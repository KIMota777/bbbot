# -*- coding: utf-8 -*-
"""Замер времени: сколько стоит один прогон холдоута. Нужен, чтобы спланировать
бюджет на развёртку точки раскола, а не гадать."""
import time
t0=time.time()
import bots_honest as bh, config, evolution as ev, evolution2 as e2, evolution7 as e7, evolution8 as e8, ext_data as xd
print("import", round(time.time()-t0,2))
t0=time.time(); pct5=xd.fetch_daily_pct5(); print("pct5", round(time.time()-t0,2))
e2.BARS_PER_DAY=96
t0=time.time(); c=ev.fetch("BTCUSDT","15",bh.DAYS); print("fetch", round(time.time()-t0,2), len(c))
t0=time.time(); aux=e8.make_aux_builder(pct5,96)("BTCUSDT",c); print("aux", round(time.time()-t0,2))
n=len(c); h=int(n*0.72); ho=c[h:]
t0=time.time(); pre=e2.prep(ho); print("prep", round(time.time()-t0,2))
p=config.SYMBOL_PARAMS["BTCUSDT"]["normal"]
g=e7.cfg_to_genome(p,"normal")
for k,v in e8.OFF8.items(): g.setdefault(k,v)
t0=time.time(); filt=e8.make_filter8(g,{k:bh.slice_aux(v,h,n) for k,v in aux.items()}); print("filt", round(time.time()-t0,2))
t0=time.time(); evs=[]; r=bh.run_at(ho,pre,g,filt,5,events=evs); print("run", round(time.time()-t0,2))
m=bh.summarize(r,evs,10.5)
print({k:(round(v,2) if isinstance(v,float) else v) for k,v in m.items() if k!="pnls"})
