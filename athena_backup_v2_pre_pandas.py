#!/usr/bin/env python3
"""ATHENA PRO v2.0 - Trading Intelligence Engine (Python Edition)"""
# Windows CMD: force unbuffered output so all prints show immediately
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)
import os, sys, json, math, time, threading, webbrowser
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory
import requests as http_requests

CONFIG = {
    # "TWELVE_DATA_KEY": os.environ.get("TWELVE_DATA_KEY", ""),  # kept for revert reference
    "ANTHROPIC_KEY": os.environ.get("ANTHROPIC_KEY", "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"),
    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
    "CRYPTOPANIC_KEY": os.environ.get("CRYPTOPANIC_KEY", "fc270ad07f32f46661c162cd2887dca81b1d18eb"),
    "FINNHUB_KEY": os.environ.get("FINNHUB_KEY", "d6jmcn9r01qkvh5qgu00d6jmcn9r01qkvh5qgu0g"),
    "RISK_PCT": 0.01, "SL_ATR_MULT": 1.5, "TP1_ATR_MULT": 2.0, "TP2_ATR_MULT": 3.5,
    "VOLUME_THRESHOLD": 1.5, "ADX_TREND_MIN": 25,
    "D1_CANDLES": 250, "H4_CANDLES": 120, "H1_CANDLES": 120, "MIN_CONFLUENCE": 8,
    "ATR_CLASS": {
        "forex":     {"sl":1.2, "tp1":2.0, "tp2":3.0},
        "commodity": {"sl":1.5, "tp1":2.5, "tp2":4.0},
        "index":     {"sl":1.5, "tp1":2.5, "tp2":4.0},
        "stock":     {"sl":1.5, "tp1":2.5, "tp2":4.0},
        "crypto":    {"sl":2.0, "tp1":3.5, "tp2":5.0}
    }
}

# enabled=False = negative backtest SQN, excluded from live scan (still available in backtest)
FOREX_PAIRS = [
    {"symbol":"EURUSD=X","type":"forex","display":"EUR/USD","source":"yfinance","enabled":False},  # SQN -0.26
    {"symbol":"GBPUSD=X","type":"forex","display":"GBP/USD","source":"yfinance","enabled":True},   # SQN +0.20
    {"symbol":"USDJPY=X","type":"forex","display":"USD/JPY","source":"yfinance","enabled":False},  # SQN -0.76
    {"symbol":"AUDUSD=X","type":"forex","display":"AUD/USD","source":"yfinance","enabled":False},  # SQN -1.54
    {"symbol":"NZDUSD=X","type":"forex","display":"NZD/USD","source":"yfinance","enabled":False},  # SQN -1.15
    {"symbol":"EURGBP=X","type":"forex","display":"EUR/GBP","source":"yfinance","enabled":False},  # SQN -0.19
    {"symbol":"USDCAD=X","type":"forex","display":"USD/CAD","source":"yfinance","enabled":False},  # SQN -1.22
    {"symbol":"USDCHF=X","type":"forex","display":"USD/CHF","source":"yfinance","enabled":False},  # SQN -1.22
]
COMMODITY_PAIRS = [
    {"symbol":"GC=F","type":"commodity","display":"XAU/USD","source":"yfinance","enabled":True},   # SQN +2.31
    {"symbol":"SI=F","type":"commodity","display":"XAG/USD","source":"yfinance","enabled":True},   # SQN +2.45
    {"symbol":"CL=F","type":"commodity","display":"WTI Oil","source":"yfinance","enabled":True},   # SQN +0.64
]
INDEX_PAIRS = [
    {"symbol":"^GSPC","type":"index","display":"S&P 500","source":"yfinance","enabled":False},    # SQN -2.38
    {"symbol":"^IXIC","type":"index","display":"Nasdaq","source":"yfinance","enabled":False},      # SQN -0.99
    {"symbol":"^DJI","type":"index","display":"Dow Jones","source":"yfinance","enabled":True},    # SQN +0.87
]
JSE_PAIRS = [
    {"symbol":"NPN.JO","type":"stock","display":"Naspers","source":"yfinance","enabled":True},    # SQN +1.45
    {"symbol":"SOL.JO","type":"stock","display":"Sasol","source":"yfinance","enabled":False},     # SQN -0.48
    {"symbol":"SBK.JO","type":"stock","display":"Std Bank","source":"yfinance","enabled":True},   # SQN +0.56
    {"symbol":"AGL.JO","type":"stock","display":"Anglo Am","source":"yfinance","enabled":False},  # SQN -0.49 (2nd run)
]
CRYPTO_PAIRS = [
    {"symbol":"BTCUSDT","type":"crypto","display":"BTC/USDT","source":"binance","enabled":True},   # SQN +0.18
    {"symbol":"ETHUSDT","type":"crypto","display":"ETH/USDT","source":"binance","enabled":False},  # SQN -0.25
    {"symbol":"XRPUSDT","type":"crypto","display":"XRP/USDT","source":"binance","enabled":False},  # SQN -0.17
    {"symbol":"SOLUSDT","type":"crypto","display":"SOL/USDT","source":"binance","enabled":True},   # SQN +1.75
    {"symbol":"ADAUSDT","type":"crypto","display":"ADA/USDT","source":"binance","enabled":True},   # SQN +0.41
    {"symbol":"DOGEUSDT","type":"crypto","display":"DOGE/USDT","source":"binance","enabled":True},  # SQN +0.17
    {"symbol":"AVAXUSDT","type":"crypto","display":"AVAX/USDT","source":"binance","enabled":True},  # SQN +1.50
    {"symbol":"LINKUSDT","type":"crypto","display":"LINK/USDT","source":"binance","enabled":True},  # SQN +1.00
    {"symbol":"MATICUSDT","type":"crypto","display":"MATIC/USDT","source":"binance","enabled":True}, # SQN +0.41
    {"symbol":"BNBUSDT","type":"crypto","display":"BNB/USDT","source":"binance","enabled":True},   # SQN +1.40
    {"symbol":"DOTUSDT","type":"crypto","display":"DOT/USDT","source":"binance","enabled":False},  # SQN -0.19
    {"symbol":"LTCUSDT","type":"crypto","display":"LTC/USDT","source":"binance","enabled":False},  # SQN -0.25
]
ALL_PAIRS = FOREX_PAIRS + COMMODITY_PAIRS + INDEX_PAIRS + JSE_PAIRS + CRYPTO_PAIRS
ACTIVE_PAIRS = [p for p in ALL_PAIRS if p.get("enabled", True)]
TF_B = {"D1":"1d","H4":"4h","H1":"1h"}

def fetch_yfinance(sym, tf, limit):
    try:
        import yfinance as yf
        import pandas as pd
        period = "2y" if tf == "D1" else "60d"
        interval = "1d" if tf == "D1" else "1h"
        df = yf.download(sym, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty: print(f"  [YF] {sym}: no data", flush=True); return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        if tf == "H4":
            df = df.resample("4h").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
        df = df.tail(limit)
        return [{"time":str(idx.date() if tf=="D1" else idx),"open":float(r["Open"]),"high":float(r["High"]),"low":float(r["Low"]),"close":float(r["Close"]),"vol":float(r.get("Volume",0))} for idx,r in df.iterrows()]
    except Exception as e: print(f"  [YF] {sym}: {e}", flush=True); return None

def fetch_binance(sym, interval, limit):
    try:
        for base in ["https://api.binance.com","https://api1.binance.com"]:
            r = http_requests.get(f"{base}/api/v3/klines", params={"symbol":sym,"interval":interval,"limit":limit}, timeout=15)
            if r.status_code == 200:
                return [{"time":datetime.fromtimestamp(k[0]/1000,tz=timezone.utc).isoformat(),"open":float(k[1]),"high":float(k[2]),"low":float(k[3]),"close":float(k[4]),"vol":float(k[5])} for k in r.json()]
            print(f"  [BN] {sym} HTTP {r.status_code}: {r.text[:120]}", flush=True)
        return None
    except Exception as e: print(f"  [BN] {sym}: {e}"); return None

def fetch_candles(pair, tf, limit):
    if pair["source"]=="binance": return fetch_binance(pair["symbol"], TF_B[tf], limit)
    if pair["source"]=="yfinance": return fetch_yfinance(pair["symbol"], tf, limit)
    return None

def calc_ema(c, p):
    k=2/(p+1); e=[None]*len(c)
    if len(c)<p: return e
    e[p-1]=sum(c[:p])/p
    for i in range(p,len(c)): e[i]=c[i]*k+e[i-1]*(1-k)
    return e

def calc_sma(a, p):
    r=[None]*len(a)
    for i in range(p-1,len(a)): r[i]=sum(a[i-p+1:i+1])/p
    return r

def calc_rsi(c, p):
    r=[None]*len(c)
    if len(c)<p+1: return r
    g=l=0
    for i in range(1,p+1):
        d=c[i]-c[i-1]
        if d>0: g+=d
        else: l-=d
    ag,al=g/p,l/p
    r[p]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(p+1,len(c)):
        d=c[i]-c[i-1]; ag=(ag*(p-1)+max(d,0))/p; al=(al*(p-1)+max(-d,0))/p
        r[i]=100 if al==0 else 100-100/(1+ag/al)
    return r

def calc_macd(c, f=12, s=26, sig=9):
    ef,es=calc_ema(c,f),calc_ema(c,s)
    ml=[ef[i]-es[i] if ef[i] is not None and es[i] is not None else None for i in range(len(c))]
    valid=[v for v in ml if v is not None]; se=calc_ema(valid,sig)
    sl2=[None]*len(c); vf=next((i for i,v in enumerate(ml) if v is not None),len(c)); si=0
    for i in range(vf,len(c)): sl2[i]=se[si] if si<len(se) else None; si+=1
    hist=[ml[i]-sl2[i] if ml[i] is not None and sl2[i] is not None else None for i in range(len(c))]
    return {"macd":ml,"signal":sl2,"hist":hist}

def calc_atr(h, l, c, p):
    tr=[0]+[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    a=[None]*len(c)
    if len(tr)<=p: return a
    a[p]=sum(tr[1:p+1])/p
    for i in range(p+1,len(tr)): a[i]=(a[i-1]*(p-1)+tr[i])/p
    return a

def calc_adx(hi, lo, c, p):
    n=len(c); adx=[None]*n; pDI=[None]*n; mDI=[None]*n
    if n<p*2: return {"adx":adx,"plusDI":pDI,"minusDI":mDI}
    tr,dp,dm=[],[],[]
    for i in range(1,n):
        u,d=hi[i]-hi[i-1],lo[i-1]-lo[i]
        dp.append(u if u>d and u>0 else 0); dm.append(d if d>u and d>0 else 0)
        tr.append(max(hi[i]-lo[i],abs(hi[i]-c[i-1]),abs(lo[i]-c[i-1])))
    at,ps,ms=sum(tr[:p]),sum(dp[:p]),sum(dm[:p]); dx=[]
    for i in range(p,len(tr)):
        at=at-at/p+tr[i]; ps=ps-ps/p+dp[i]; ms=ms-ms/p+dm[i]
        pd=(ps/at)*100 if at else 0; md=(ms/at)*100 if at else 0; sm=pd+md
        pDI[i+1]=pd; mDI[i+1]=md; dx.append(abs(pd-md)/sm*100 if sm else 0)
    if len(dx)>=p:
        av=sum(dx[:p])/p
        if p*2<n: adx[p*2]=av
        for i in range(p,len(dx)):
            av=(av*(p-1)+dx[i])/p; idx=i+p+1
            if idx<n: adx[idx]=av
    return {"adx":adx,"plusDI":pDI,"minusDI":mDI}

def calc_bb(c, p, m):
    u,mid,l=[],[],[]
    for i in range(len(c)):
        if i<p-1: u.append(None); mid.append(None); l.append(None); continue
        sl=c[i-p+1:i+1]; mn=sum(sl)/p; sd=math.sqrt(sum((x-mn)**2 for x in sl)/p)
        mid.append(mn); u.append(mn+m*sd); l.append(mn-m*sd)
    return {"upper":u,"mid":mid,"lower":l}

def calc_rsi_divergence(candles, lookback=30):
    """Wilder: RSI divergence is the strongest reversal signal.
    Returns: 'bullish', 'bearish', or None"""
    try:
        c=candles[-lookback:]; cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo=[x["low"] for x in c]
        rsi=calc_rsi(cl,14); n=len(cl)
        if n<20 or rsi[-1] is None: return None
        # Find two recent swing highs/lows in last half
        mid=n//2
        # Bearish: price makes higher high, RSI makes lower high
        ph1=max(hi[mid//2:mid]); ph2=max(hi[mid:])
        rh1=max((r for r in rsi[mid//2:mid] if r is not None),default=None)
        rh2=max((r for r in rsi[mid:] if r is not None),default=None)
        if ph2>ph1*1.001 and rh1 and rh2 and rh2<rh1*0.99: return "bearish"
        # Bullish: price makes lower low, RSI makes higher low
        pl1=min(lo[mid//2:mid]); pl2=min(lo[mid:])
        rl1=min((r for r in rsi[mid//2:mid] if r is not None),default=None)
        rl2=min((r for r in rsi[mid:] if r is not None),default=None)
        if pl2<pl1*0.999 and rl1 and rl2 and rl2>rl1*1.01: return "bullish"
    except: pass
    return None

def calc_weinstein_stage(candles):
    """Stan Weinstein stage analysis using 30-week MA (approx 150 D1 bars).
    Stage 1=Basing, Stage 2=Advancing, Stage 3=Topping, Stage 4=Declining"""
    try:
        cl=[c["close"] for c in candles]
        if len(cl)<150: return None,None
        ma30w=calc_sma(cl,150); L=len(cl)-1
        if ma30w[L] is None or ma30w[L-5] is None: return None,None
        price=cl[L]; ma=ma30w[L]; ma_prev=ma30w[L-5]
        ma_rising=ma>ma_prev
        if price>ma and ma_rising: return 2,"Stage 2 — Advancing"
        if price>ma and not ma_rising: return 3,"Stage 3 — Topping"
        if price<ma and not ma_rising: return 4,"Stage 4 — Declining"
        if price<ma and ma_rising: return 1,"Stage 1 — Basing"
    except: pass
    return None,None

def calc_fib_proximity(price, fib, direction):
    """Check if price is near a key Fibonacci level (within 0.5% ATR-relative band).
    Returns vote: 1=near support fib for LONG, -1=near resistance fib for SHORT, 0=no confluence"""
    try:
        levels=[fib["fib382"],fib["fib500"],fib["fib618"]]
        rng=fib["highest"]-fib["lowest"]
        if rng<=0: return 0
        band=rng*0.015
        for lvl in levels:
            if abs(price-lvl)<=band:
                if direction=="LONG" and price<=lvl: return 1
                if direction=="SHORT" and price>=lvl: return -1
                if abs(price-lvl)<=band*0.5: return 1 if direction=="LONG" else -1
    except: pass
    return 0

def calc_stochastic(candles, kp, ks, ds):
    hi=[c["high"] for c in candles]; lo=[c["low"] for c in candles]; cl=[c["close"] for c in candles]
    n=len(cl); rawK=[None]*n
    for i in range(kp-1,n):
        hh,ll=max(hi[i-kp+1:i+1]),min(lo[i-kp+1:i+1])
        rawK[i]=((cl[i]-ll)/(hh-ll))*100 if hh!=ll else 50
    mapped=[v if v is not None else 0 for v in rawK]; kL=calc_sma(mapped,ks)
    for i in range(kp-1+ks-1):
        if i<len(kL): kL[i]=None
    mapped2=[v if v is not None else 0 for v in kL]; dL=calc_sma(mapped2,ds)
    for i in range(kp-1+ks-1+ds-1):
        if i<len(dL): dL[i]=None
    return {"k":kL,"d":dL}

def calc_indicators(candles):
    cl=[c["close"] for c in candles]; hi=[c["high"] for c in candles]; lo=[c["low"] for c in candles]
    e21,e50,e200=calc_ema(cl,21),calc_ema(cl,50),calc_ema(cl,200)
    rsi=calc_rsi(cl,14); macd=calc_macd(cl); atr=calc_atr(hi,lo,cl,14)
    adx=calc_adx(hi,lo,cl,14); bb=calc_bb(cl,20,2); L=len(cl)-1
    adx_now=adx["adx"][L]; adx_prev=next((adx["adx"][j] for j in range(L-1,-1,-1) if adx["adx"][j] is not None),None)
    rsi_prev=next((rsi[j] for j in range(L-1,-1,-1) if rsi[j] is not None),None)
    e200_slope=round((e200[L]-e200[L-10])/e200[L-10]*100,3) if e200[L] and L>=10 and e200[L-10] else 0
    return {"snap":{"ema21":e21[L],"ema50":e50[L],"ema200":e200[L],"close":cl[L],"rsi":rsi[L],"rsiPrev":rsi_prev,
        "macdLine":macd["macd"][L],"macdSignal":macd["signal"][L],"macdHist":macd["hist"][L],
        "macdHistPrev":macd["hist"][L-1] if L>0 else None,
        "atr":atr[L],"adx":adx_now,"adxPrev":adx_prev,"plusDI":adx["plusDI"][L],"minusDI":adx["minusDI"][L],
        "bbUpper":bb["upper"][L],"bbMid":bb["mid"][L],"bbLower":bb["lower"][L],"ema200Slope10":e200_slope}}

def calc_fib(candles):
    r=candles[-50:]; high=max(c["high"] for c in r); low=min(c["low"] for c in r); rng=high-low
    return {"highest":round(high,6),"lowest":round(low,6),"fib236":round(high-rng*0.236,6),
            "fib382":round(high-rng*0.382,6),"fib500":round(high-rng*0.5,6),"fib618":round(high-rng*0.618,6),
            "fib786":round(high-rng*0.786,6),"ext1618":round(high+rng*0.618,6)}

def get_session():
    h=datetime.now(timezone.utc).hour
    if 7<=h<9: return {"name":"London Open","quality":"high","color":"#22c55e"}
    if 13<=h<16: return {"name":"London/NY Overlap","quality":"high","color":"#22c55e"}
    if 9<=h<13: return {"name":"London","quality":"medium","color":"#3b82f6"}
    if 16<=h<22: return {"name":"New York","quality":"medium","color":"#3b82f6"}
    return {"name":"Asian / Off-Hours","quality":"low","color":"#f59e0b"}

def calc_confluence(d1, h4, h1, vr, stoch, e200s, pair, btc_bias, d1_candles=None, h4_candles=None, h1_candles=None):
    v={}; w=[]; bull=bear=0; s=d1["snap"]

    # --- D1 TREND LAYER (Elder Screen 1) ---
    d1_trend=0
    if s["ema21"] and s["ema50"] and s["ema200"]:
        if s["ema21"]>s["ema50"]>s["ema200"]: v["D1 EMA Stack"]=1; bull+=1; d1_trend=1
        elif s["ema21"]<s["ema50"]<s["ema200"]: v["D1 EMA Stack"]=-1; bear+=1; d1_trend=-1
        else: v["D1 EMA Stack"]=0
    else: v["D1 EMA Stack"]=0
    if e200s>0.001: v["D1 EMA200 Slope"]=1; bull+=1
    elif e200s<-0.001: v["D1 EMA200 Slope"]=-1; bear+=1
    else: v["D1 EMA200 Slope"]=0; w.append("D1 EMA200 flat")
    # Elder Impulse on D1: EMA slope + MACD hist must agree (Come Into My Trading Room)
    d1_ema_bull = s["ema21"] and s["ema50"] and s["ema21"]>s["ema50"]
    d1_macd_bull = s["macdHist"] is not None and s["macdHist"]>0
    d1_ema_bear = s["ema21"] and s["ema50"] and s["ema21"]<s["ema50"]
    d1_macd_bear = s["macdHist"] is not None and s["macdHist"]<0
    if d1_ema_bull and d1_macd_bull: v["D1 Impulse"]=1; bull+=1
    elif d1_ema_bear and d1_macd_bear: v["D1 Impulse"]=-1; bear+=1
    else: v["D1 Impulse"]=0; w.append("D1 Impulse neutral (EMA/MACD disagree)")

    # Weinstein Stage Analysis (30-week MA on D1)
    weinstein_stage=None; weinstein_label=None
    if d1_candles:
        weinstein_stage, weinstein_label = calc_weinstein_stage(d1_candles)
        if weinstein_stage==2: v["D1 Weinstein Stage"]=1; bull+=1
        elif weinstein_stage==4: v["D1 Weinstein Stage"]=-1; bear+=1
        elif weinstein_stage==3: v["D1 Weinstein Stage"]=0; w.append(f"Weinstein {weinstein_label} — potential distribution")
        elif weinstein_stage==1: v["D1 Weinstein Stage"]=0; w.append(f"Weinstein {weinstein_label} — wait for Stage 2 breakout")
        else: v["D1 Weinstein Stage"]=0
    else: v["D1 Weinstein Stage"]=0

    # --- HARD D1 GATE (Elder Triple Screen rule) ---
    hard_long_block = (v["D1 EMA Stack"]==-1 and v["D1 Impulse"]==-1)
    hard_short_block = (v["D1 EMA Stack"]==1 and v["D1 Impulse"]==1)

    # --- H4 MOMENTUM LAYER (Elder Screen 2) ---
    s4=h4["snap"]
    if s4["ema21"] and s4["ema50"]:
        if s4["ema21"]>s4["ema50"]: v["H4 EMA Trend"]=1; bull+=1
        else: v["H4 EMA Trend"]=-1; bear+=1
    else: v["H4 EMA Trend"]=0
    # H4 Price vs EMA200 — Minervini intermediate trend confirmation
    cl4=s4.get("close"); e200_4=s4.get("ema200"); e200s4=s4.get("ema200Slope10",0)
    if cl4 and e200_4 and e200_4>0:
        if cl4>e200_4 and e200s4>=0: v["H4 Price>EMA200"]=1; bull+=1
        elif cl4<e200_4 and e200s4<=0: v["H4 Price>EMA200"]=-1; bear+=1
        else: v["H4 Price>EMA200"]=0
    else: v["H4 Price>EMA200"]=0
    r4=s4["rsi"]
    if r4 is not None:
        # Cardwell RSI regime: uptrend trades 40-80, downtrend trades 20-60
        if 50<r4<75: v["H4 RSI Zone"]=1; bull+=1
        elif 25<r4<50: v["H4 RSI Zone"]=-1; bear+=1
        elif r4>=75: v["H4 RSI Zone"]=0; w.append(f"H4 RSI overbought ({r4:.0f}) — wait for pullback")
        elif r4<=25: v["H4 RSI Zone"]=0; w.append(f"H4 RSI oversold ({r4:.0f}) — wait for bounce")
        else: v["H4 RSI Zone"]=0
    else: v["H4 RSI Zone"]=0
    if s4["macdLine"] is not None and s4["macdSignal"] is not None and s4["macdHist"] is not None:
        hist_now=s4["macdHist"]; hist_prev=s4.get("macdHistPrev")
        # MACD cross + histogram acceleration (hist growing = momentum building)
        if s4["macdLine"]>s4["macdSignal"] and hist_now>0:
            v["H4 MACD Cross"]=1; bull+=1
            if hist_prev is not None and hist_now<hist_prev: w.append("H4 MACD histogram decelerating")
        elif s4["macdLine"]<s4["macdSignal"] and hist_now<0:
            v["H4 MACD Cross"]=-1; bear+=1
            if hist_prev is not None and hist_now>hist_prev: w.append("H4 MACD histogram decelerating")
        else: v["H4 MACD Cross"]=0
    else: v["H4 MACD Cross"]=0
    # ADX strength + slope check (Wilder: falling ADX from high = weakening trend)
    adx_val=s4["adx"]; adx_prev=s4.get("adxPrev")
    adx_rising = adx_prev is None or adx_val is None or adx_val >= adx_prev
    if adx_val is not None and adx_val>CONFIG["ADX_TREND_MIN"]:
        if s4["plusDI"] is not None and s4["minusDI"] is not None:
            if s4["plusDI"]>s4["minusDI"]: v["H4 ADX Strength"]=1; bull+=1
            else: v["H4 ADX Strength"]=-1; bear+=1
        else: v["H4 ADX Strength"]=0
        if not adx_rising and adx_val>35: w.append(f"H4 ADX weakening ({adx_val:.1f}↓ from {adx_prev:.1f}) — trend fading")
    else:
        v["H4 ADX Strength"]=0
        adx_str=f"{adx_val:.1f}" if adx_val is not None else "n/a"
        w.append(f"H4 ADX weak ({adx_str}) — range/choppy market")
    lK=stoch["k"][-1] if stoch["k"] and stoch["k"][-1] is not None else None
    lD=stoch["d"][-1] if stoch["d"] and stoch["d"][-1] is not None else None
    if lK is not None and lD is not None:
        if lK>lD and lK<30: v["H4 Stochastic"]=1; bull+=1
        elif lK<lD and lK>70: v["H4 Stochastic"]=-1; bear+=1
        elif lK>lD and 30<=lK<=50: v["H4 Stochastic"]=1; bull+=1
        elif lK<lD and 50<=lK<=70: v["H4 Stochastic"]=-1; bear+=1
        else: v["H4 Stochastic"]=0
    else: v["H4 Stochastic"]=0

    # --- H1 ENTRY LAYER (Elder Screen 3) ---
    s1=h1["snap"]
    if s1["ema21"] and s1["ema50"]:
        if s1["ema21"]>s1["ema50"]: v["H1 EMA Entry"]=1; bull+=1
        else: v["H1 EMA Entry"]=-1; bear+=1
    else: v["H1 EMA Entry"]=0
    if s1["bbUpper"] is not None and s1["bbLower"] is not None:
        bbr=s1["bbUpper"]-s1["bbLower"]; bbp=(s1["ema21"]-s1["bbLower"])/bbr if bbr>0 and s1["ema21"] else 0.5
        if bbp<0.3: v["H1 BB Zone"]=1; bull+=1
        elif bbp>0.7: v["H1 BB Zone"]=-1; bear+=1
        else: v["H1 BB Zone"]=0
    else: v["H1 BB Zone"]=0
    # Price vs EMA200 on H1 (Minervini Trend Template)
    cl1=s1.get("close"); e200_1=s1.get("ema200")
    if cl1 is not None and e200_1 is not None and e200_1>0:
        if cl1>e200_1: v["H1 Price>EMA200"]=1; bull+=1
        else: v["H1 Price>EMA200"]=-1; bear+=1
    else: v["H1 Price>EMA200"]=0
    # H1 RSI divergence (Wilder: strongest reversal signal)
    if h1_candles:
        h1_div=calc_rsi_divergence(h1_candles)
        if h1_div=="bullish": v["H1 RSI Divergence"]=1; bull+=1; w.append("H1 RSI Bullish Divergence — reversal signal (Wilder)")
        elif h1_div=="bearish": v["H1 RSI Divergence"]=-1; bear+=1; w.append("H1 RSI Bearish Divergence — reversal signal (Wilder)")
        else: v["H1 RSI Divergence"]=0
    else: v["H1 RSI Divergence"]=0
    if vr>=CONFIG["VOLUME_THRESHOLD"]:
        if s1["ema21"] and s1["ema50"]:
            if s1["ema21"]>s1["ema50"]: v["H1 Volume"]=1; bull+=1
            else: v["H1 Volume"]=-1; bear+=1
        else: v["H1 Volume"]=0
    else:
        v["H1 Volume"]=0
        if max(bull,bear)>=6: w.append(f"Low volume ({vr:.1f}x avg) — confirm before entry")

    # --- INTERMARKET CONTEXT ---
    if pair["type"]=="crypto" and pair["symbol"]!="BTCUSDT":
        dom="LONG" if bull>=bear else "SHORT"
        if dom=="LONG" and btc_bias=="bearish": w.append("BTC bearish — alt LONG is counter-trend risk")
        elif dom=="SHORT" and btc_bias=="bullish": w.append("BTC bullish — alt SHORT is counter-trend risk")

    direction="LONG" if bull>=bear else "SHORT"

    # Fib proximity vote (Murphy: price at key fib = high-probability entry zone)
    if h4_candles:
        h4_fib=calc_fib(h4_candles)
        fib_vote=calc_fib_proximity(s4.get("close",0) or 0, h4_fib, direction)
        v["H4 Fib Confluence"]=fib_vote
        if fib_vote==1: bull+=1
        elif fib_vote==-1: bear+=1
    else: v["H4 Fib Confluence"]=0

    score=max(bull,bear)

    # Apply hard D1 gate — flag counter-trend trades (Elder's strict rule)
    if direction=="LONG" and hard_long_block:
        w.append("COUNTER-TREND: D1 bearish — Elder Triple Screen violation, -2 score")
        score=max(0, score-2)
    if direction=="SHORT" and hard_short_block:
        w.append("COUNTER-TREND: D1 bullish — Elder Triple Screen violation, -2 score")
        score=max(0, score-2)

    # Determine trend state for Marcus Reid context
    if adx_val is not None:
        if adx_val>=35: trend_state="TRENDING"
        elif adx_val>=25: trend_state="DEVELOPING"
        else: trend_state="RANGING"
    else: trend_state="UNKNOWN"

    atr_mults=CONFIG["ATR_CLASS"].get(pair["type"],{"sl":CONFIG["SL_ATR_MULT"]})
    if s1["atr"] and s1["ema21"] and s1["atr"]*atr_mults["sl"]>s1["ema21"]*0.03:
        w.append("Wide SL > 3% of price — size down")
    return {"score":score,"votes":v,"direction":direction,"bull":bull,"bear":bear,"warnings":w,
            "trendState":trend_state,"weinsteinStage":weinstein_stage,"weinsteinLabel":weinstein_label}

def detect_div(d1c, h4c, h1c):
    w=[]
    try:
        h4=h4c[-20:]; cl=[c["close"] for c in h4]; rsi=calc_rsi(cl,14); pr=[c["high"] for c in h4]; n=len(pr)
        if n>=10 and rsi[-1] is not None:
            t=n//3; pm=max(pr[t:2*t]); rm=[x for x in rsi[t:2*t] if x is not None]
            if rm:
                if pr[-1]>pm and rsi[-1]<max(rm): w.append("H4 RSI Bearish Divergence")
                if pr[-1]<pm and rsi[-1]>max(rm): w.append("H4 RSI Bullish Divergence")
    except: pass
    try:
        h1=h1c[-20:]; vols=[c["vol"] for c in h1]; pr=[c["close"] for c in h1]; n=len(pr)
        if n>=10:
            m=n//2
            if pr[-1]>pr[0] and vols[-1]<vols[m] and vols[-1]>0: w.append("H1 Vol Div - rising price, falling vol")
            if pr[-1]<pr[0] and vols[-1]<vols[m] and vols[-1]>0: w.append("H1 Vol Div - falling price, falling vol")
    except: pass
    return w

def analyze_pair(pair, btc_bias):
    d1=fetch_candles(pair,"D1",CONFIG["D1_CANDLES"])
    h4=fetch_candles(pair,"H4",CONFIG["H4_CANDLES"])
    h1=fetch_candles(pair,"H1",CONFIG["H1_CANDLES"])
    if not d1 or not h4 or not h1: return None
    if len(d1)<200 or len(h4)<50 or len(h1)<50: return None
    d1i,h4i,h1i=calc_indicators(d1),calc_indicators(h4),calc_indicators(h1)
    vols=[c["vol"] for c in h1]; vsma=calc_sma(vols,20)
    vr=vols[-1]/vsma[-1] if vsma[-1] and vsma[-1]>0 else 0
    stoch=calc_stochastic(h4,14,3,3)
    e200=calc_ema([c["close"] for c in d1],200)
    e200s=(e200[-1]-e200[-21])/e200[-21] if e200[-1] and len(e200)>=21 and e200[-21] else 0
    res=calc_confluence(d1i,h4i,h1i,vr,stoch,e200s,pair,btc_bias,d1_candles=d1,h4_candles=h4,h1_candles=h1)
    allw=res["warnings"]+detect_div(d1,h4,h1)
    price=h1[-1]["close"]; atr=h1i["snap"]["atr"]
    if not atr or atr==0: return None
    d=res["direction"]
    m=CONFIG["ATR_CLASS"].get(pair["type"],{"sl":CONFIG["SL_ATR_MULT"],"tp1":CONFIG["TP1_ATR_MULT"],"tp2":CONFIG["TP2_ATR_MULT"]})
    sl=price-atr*m["sl"] if d=="LONG" else price+atr*m["sl"]
    tp1=price+atr*m["tp1"] if d=="LONG" else price-atr*m["tp1"]
    tp2=price+atr*m["tp2"] if d=="LONG" else price-atr*m["tp2"]
    rr1=abs(tp1-price)/abs(sl-price) if abs(sl-price)>0 else 0
    rr2=abs(tp2-price)/abs(sl-price) if abs(sl-price)>0 else 0
    sk=stoch["k"][-1] if stoch["k"] and stoch["k"][-1] is not None else None
    sd=stoch["d"][-1] if stoch["d"] and stoch["d"][-1] is not None else None
    risk_dollar=round(abs(price-sl)/price*100,2)
    max_score=16
    return {"pair":pair["display"],"display":pair["display"],"symbol":pair["symbol"],"type":pair["type"],
        "direction":d,"confluenceScore":res["score"],"confluencePct":round(res["score"]/max_score*100),
        "votes":res["votes"],"maxScore":max_score,"price":price,"sl":round(sl,6),"tp1":round(tp1,6),
        "tp2":round(tp2,6),"rr1":round(rr1,2),"rr2":round(rr2,2),"atr":round(atr,6),
        "slPips":round(abs(price-sl),6),"slPct":risk_dollar,"fib":calc_fib(h4),"d1":d1i,"h4":h4i,"h1":h1i,
        "volRatio":round(vr,2),"ema200Slope":round(e200s*100,3),
        "stochK":round(sk,1) if sk else None,"stochD":round(sd,1) if sd else None,
        "btcBias":btc_bias if pair["type"]=="crypto" else "n/a",
        "trendState":res["trendState"],"weinsteinStage":res["weinsteinStage"],"weinsteinLabel":res["weinsteinLabel"],
        "warnings":allw,"session":get_session(),
        "timestamp":datetime.now(timezone.utc).isoformat(),"aiAnalysis":None}

def run_full_scan():
    results,errors,skipped=[],[],[]
    btc_bias="neutral"
    print("  [NEWS] Fetching market context...", flush=True)
    news_ctx = fetch_news_context()
    try:
        btc=fetch_candles({"symbol":"BTCUSDT","source":"binance"},"D1",CONFIG["D1_CANDLES"])
        if btc and len(btc)>=200:
            s=calc_indicators(btc)["snap"]
            if s["ema21"] and s["ema50"] and s["ema200"]:
                if s["ema21"]>s["ema50"]>s["ema200"]: btc_bias="bullish"
                elif s["ema21"]<s["ema50"]<s["ema200"]: btc_bias="bearish"
        print(f"  BTC bias: {btc_bias}", flush=True)
    except Exception as e: print(f"  BTC err: {e}")
    for pair in ACTIVE_PAIRS:
        try:
            sig=analyze_pair(pair,btc_bias)
            if sig: sig["newsCtx"]=news_ctx; results.append(sig); print(f"  {pair['display']:12s} {sig['direction']:5s} {sig['confluenceScore']}/16 [{sig.get('trendState','?')}]", flush=True)
            else: skipped.append({"pair":pair["display"],"reason":"No data"}); print(f"  {pair['display']:12s} SKIP", flush=True)
        except Exception as e: errors.append({"pair":pair["display"],"error":str(e)}); print(f"  {pair['display']:12s} ERR: {e}", flush=True)
        time.sleep(0.5)
    results.sort(key=lambda x:x["confluenceScore"],reverse=True)
    return {"success":True,"signals":results,"errors":errors,"skipped":skipped,
            "btcBias":btc_bias,"totalPairs":len(ACTIVE_PAIRS),"scannedAt":datetime.now(timezone.utc).isoformat()}

EXPERT_PROMPT="""You are Marcus Reid — 18-year professional trader and prop desk veteran. Your framework: Elder Triple Screen (tide/wave/ripple), Murphy intermarket analysis, Wilder's original indicator rules, Minervini trend templates, Weinstein stage analysis, Van Tharp position sizing (R-multiples), and Mark Douglas probability mindset. You are direct, occasionally blunt, always precise. You never sugarcoat weak setups and you never fake certainty — you give EDGE PROBABILITY, not guarantees.

═══ GRADING SCALE (16-point confluence system) ═══
A+ (14-16): Elite setup — maximum conviction, full position size
A  (11-13): Strong setup — act with normal size
B  (8-10):  Valid setup — half size, confirm entry trigger
C  (5-7):   Developing — watchlist only, do not enter yet
F  (0-4):   Avoid — setup is broken or counter-trend

═══ MARKET REGIME RULES (read TrendState field) ═══
TRENDING (ADX≥35): Use trend-following entries. Pullbacks to EMA21/EMA50 are buys/sells. Extend TP targets.
DEVELOPING (ADX 25-34): Standard setup rules apply. Confirm with volume.
RANGING (ADX<25): NEVER trade trend-following signals in ranging markets. Only fade extremes (BB outer bands + stoch reversal). Downgrade any B setup to C. Downgrade C to F.

═══ ELDER TRIPLE SCREEN RULES (mandatory) ═══
Screen 1 (D1 tide): D1 EMA Stack + D1 Impulse must agree. If D1 Impulse is neutral — reduce confidence.
Screen 2 (H4 wave): Trade only in direction of D1 tide. H4 Stochastic oversold in uptrend = buy zone. H4 Stochastic overbought in downtrend = sell zone.
Screen 3 (H1 ripple): Entry trigger only. H1 EMA cross or BB touch. If H1 conflicts with H4 — WAIT.
COUNTER-TREND warning = automatic grade downgrade of 1 level. State this in verdict.

═══ WILDER INDICATOR RULES ═══
RSI: In uptrend, RSI trades 40-80 (Cardwell range). RSI below 40 in uptrend = weakness. RSI Divergence warnings are HIGH PRIORITY — address them directly.
ADX: ADX<25 = no trend, do not use trend signals. ADX weakening from above 40 = trend exhaustion, tighten TP or move SL to breakeven.
ATR: SL must be placed beyond 1 ATR minimum. Tight SL inside ATR = premature stop.

═══ MINERVINI / WEINSTEIN STAGE RULES ═══
Weinstein Stage 2 (Advancing): Ideal LONG environment. 30-week MA rising, price above it.
Weinstein Stage 4 (Declining): Ideal SHORT environment. 30-week MA falling, price below it.
Stage 1 (Basing) or Stage 3 (Topping): DO NOT enter trend trades. Wait for stage transition.
Minervini: Price must be above EMA200 on H4 AND H1 for LONG. Below for SHORT.

═══ MURPHY INTERMARKET RULES ═══
DXY rising = headwind for EUR/USD, GBP/USD, AUD/USD, NZD/USD, XAU/USD, XAG/USD, WTI Oil LONGS.
DXY falling = tailwind for above pairs LONG.
Rising bond yields = headwind for Nasdaq, growth stocks.
BTC bearish = higher risk for all crypto altcoin LONGS.

═══ FIBONACCI RULES (Murphy) ═══
38.2%, 50%, 61.8% retracements are the highest-probability entry zones.
If H4 Fib Confluence vote is active, mention the specific fib level in entryZone.
Price AT a fib + stochastic reversal + EMA support = A-grade entry cluster.

═══ VAN THARP POSITION SIZING ═══
1R = risk per trade (1% of account). Express ALL targets in R-multiples.
Minimum trade: 2R reward. Optimal: 3-5R. Never risk more than 2% (2R) on one trade.
If SL% > 2% of price — recommend reduced position size explicitly.
SQN interpretation: >1.6=average system, >2.5=good, >3=excellent — reference this if backtest data available.

═══ MARK DOUGLAS PROBABILITY MINDSET ═══
Never use the word "will" — use "edge suggests", "probability favors", "setup indicates".
A losing trade on a valid setup is NOT a failure — it is a statistically expected outcome.
Never recommend chasing a missed entry. If price has moved >0.5 ATR from ideal entry — WAIT for re-test.

═══ SCHWAGER / WIZARD RULES ═══
Cut losses immediately when the setup invalidation level is hit — no hoping.
A+ setups only: score≥14, TrendState=TRENDING, D1+H4+H1 all aligned, no COUNTER-TREND warnings.
Score<8 = watchlist. Do not encourage entries on watchlist items.

═══ NEWS & EVENTS ═══
High-impact events (FOMC, NFP, CPI, earnings) within 24 hours = reduce size by 50% or WAIT.
Event during active trade = move SL to breakeven before event.
Conflicting news (bullish news on SHORT setup) = downgrade grade by 1 level.

═══ USER STYLE PREFERENCE ═══
SCALP: ADX>30 mandatory. H1 exhaustion signal required. TP at 1.5-2R only. Flag if D1 trend too strong for scalp reversal.
INTRADAY: H4+H1 alignment critical. Same session only. 2-3R target. Session quality must be medium or high.
SWING: D1 EMA stack dominant. EMA200 slope positive (LONG) or negative (SHORT). 4-6R. Warn if H1 is extended — wait for pullback.
If setup is fundamentally incompatible with requested style, say so and recommend correct style.

═══ OUTPUT FORMAT — JSON ONLY, no prose outside block ═══
{"grade":"A","verdict":"One sharp sentence verdict.","narrative":"2-3 sentence technical story — what the charts are saying across D1/H4/H1. Reference specific indicators.","entryZone":"Price level or range with fib reference if applicable","invalidation":"Exact price level that kills the trade idea","keyLevels":"Support and resistance levels to watch","positionSizing":"e.g. Full size (score≥12, trending) / Half size (score 8-11) / Quarter size (counter-trend or ranging)","tradeStyle":"INTRADAY","tradeStyleReason":"Why this style fits the current setup","warnings":["Specific warning 1","Specific warning 2"],"edgeProbability":68}"""

def fetch_dxy_context():
    try:
        d=fetch_yfinance("DX-Y.NYB","D1",10)
        if not d or len(d)<5: return None
        cl=[c["close"] for c in d]
        chg=round((cl[-1]-cl[-5])/cl[-5]*100,2)
        trend="rising" if chg>0.3 else "falling" if chg<-0.3 else "flat"
        return f"trend={trend} 5d_chg={chg}% price={round(cl[-1],2)}"
    except: return None

def fetch_news_context():
    ctx = {"forexEvents":[], "cryptoNews":[], "marketNews":[]}
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r = http_requests.get(f"https://finnhub.io/api/v1/calendar/economic?from={today}&to={today}&token={CONFIG.get('FINNHUB_KEY','')}", timeout=8)
        if r.status_code == 200:
            events = [e for e in r.json().get("economicCalendar",[]) if e.get("impact","").lower() in ["high","3"]]
            ctx["forexEvents"] = [{"time":e.get("time",""),"currency":e.get("country",""),"event":e.get("event","")} for e in events[:5]]
            print(f"  [NEWS] Economic calendar: {len(ctx['forexEvents'])} high-impact events today", flush=True)
    except Exception as e: print(f"  [NEWS] Economic calendar failed: {e}", flush=True)
    if CONFIG.get("CRYPTOPANIC_KEY"):
        try:
            r = http_requests.get(f"https://cryptopanic.com/api/v1/posts/?auth_token={CONFIG['CRYPTOPANIC_KEY']}&public=true&filter=hot", timeout=8)
            if r.status_code == 200:
                posts = r.json().get("results", [])
                ctx["cryptoNews"] = [{"title":p.get("title",""),"sentiment":"bullish" if p.get("votes",{}).get("positive",0)>p.get("votes",{}).get("negative",0) else "bearish" if p.get("votes",{}).get("negative",0)>0 else "neutral","currencies":[c["code"] for c in p.get("currencies",[])]} for p in posts[:8]]
                print(f"  [NEWS] CryptoPanic: {len(ctx['cryptoNews'])} headlines", flush=True)
        except Exception as e: print(f"  [NEWS] CryptoPanic failed: {e}", flush=True)
    if CONFIG.get("FINNHUB_KEY"):
        try:
            r = http_requests.get(f"https://finnhub.io/api/v1/news?category=general&token={CONFIG['FINNHUB_KEY']}", timeout=8)
            if r.status_code == 200:
                ctx["marketNews"] = [{"headline":a.get("headline",""),"summary":a.get("summary","")[:100]} for a in r.json()[:5]]
                print(f"  [NEWS] Finnhub: {len(ctx['marketNews'])} market headlines", flush=True)
        except Exception as e: print(f"  [NEWS] Finnhub failed: {e}", flush=True)
    return ctx

def run_ai(signal, news_ctx=None, style_pref="auto"):
    if not CONFIG.get("ANTHROPIC_KEY") or CONFIG["ANTHROPIC_KEY"]=="YOUR_ANTHROPIC_API_KEY":
        print("[AI] ERROR: Anthropic API key is None or not configured!", flush=True)
        return {"error":"Anthropic API key not configured"}
    try:
        print(f"  [AI] Analyzing {signal['pair']}...", flush=True)
        import anthropic
        c=anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_KEY"])
        style_labels = {"scalp":"SCALP — focus on H1 exhaustion, tight 1.5R, quick execution","intraday":"INTRADAY — H4+H1 alignment, same-session execution, 2-3R","swing":"SWING — D1 trend dominance, EMA200 slope, 4-6R multi-day hold"}
        dxy_ctx=fetch_dxy_context()
        max_score=signal.get("maxScore",16)
        msg=(f"Pair:{signal['pair']} Dir:{signal['direction']} Score:{signal['confluenceScore']}/{max_score} "
             f"TrendState:{signal.get('trendState','?')} "
             f"Weinstein:{signal.get('weinsteinLabel','n/a')} "
             f"Price:{signal['price']} SL:{signal['sl']}(SL%:{signal.get('slPct','?')}%) "
             f"TP1:{signal['tp1']}(R:{signal['rr1']}) TP2:{signal['tp2']}(R:{signal['rr2']}) "
             f"ATR:{signal['atr']} "
             f"Votes:{json.dumps(signal['votes'])} "
             f"Vol:{signal['volRatio']}x Stoch:{signal.get('stochK')}/{signal.get('stochD')} "
             f"EMA200slope:{signal['ema200Slope']}% "
             f"BTC:{signal.get('btcBias','n/a')} "
             f"Session:{signal['session']['name']}({signal['session']['quality']}) "
             f"Warnings:{json.dumps(signal['warnings'])} "
             f"Fib:{json.dumps(signal['fib'])}")
        if dxy_ctx: msg += f" DXY:{dxy_ctx}"
        if style_pref and style_pref.lower() != "auto":
            msg += f" UserPreference:{style_labels.get(style_pref.lower(), style_pref.upper())}"
        if news_ctx:
            if news_ctx.get("forexEvents"): msg += f" HighImpactEvents:{json.dumps(news_ctx['forexEvents'])}"
            if news_ctx.get("marketNews"): msg += f" MarketNews:{json.dumps(news_ctx['marketNews'])}"
            if news_ctx.get("cryptoNews") and signal.get("type")=="crypto":
                pair_coins = [signal["symbol"].replace("USDT","").replace("USDC","")]
                relevant = [n for n in news_ctx["cryptoNews"] if not n["currencies"] or any(c in pair_coins for c in n["currencies"])]
                if relevant: msg += f" CryptoNews:{json.dumps(relevant[:3])}"
        r=c.messages.create(model=CONFIG["ANTHROPIC_MODEL"],max_tokens=1500,system=EXPERT_PROMPT,messages=[{"role":"user","content":msg}])
        t=r.content[0].text.strip()
        if "```" in t:
            parts = t.split("```")
            for p in parts:
                p = p.strip()
                if p.startswith("json"): p = p[4:].strip()
                if p.startswith("{"): t = p; break
        start = t.find("{"); end = t.rfind("}") + 1
        if start >= 0 and end > start: t = t[start:end]
        result = json.loads(t)
        print(f"  [AI] {signal['pair']} => Grade:{result.get('grade','?')} | {str(result.get('verdict',''))[:60]}", flush=True)
        return result
    except Exception as e:
        print(f"  [AI] ERROR for {signal.get('pair','?')}: {e}", flush=True)
        return {"error":str(e)}

def backtest_pair(pair):
    print(f"  [BT] {pair['display']} fetching data...", flush=True)
    try:
        import yfinance as yf, pandas as pd
        sym = pair["symbol"]
        def df_to_candles(df):
            return [{"time":str(idx)[:10],"open":float(r["Open"]),"high":float(r["High"]),"low":float(r["Low"]),"close":float(r["Close"]),"vol":float(r.get("Volume",0))} for idx,r in df.iterrows()]
        if pair["source"] == "binance":
            d1_raw = fetch_binance(sym, "1d", 1000)
            h4_raw = fetch_binance(sym, "4h", 1000)
            h1_raw = fetch_binance(sym, "1h", 1000)
        else:
            d1_df = yf.download(sym, period="2y", interval="1d", progress=False, auto_adjust=True)
            if d1_df is None or d1_df.empty: return {"error": f"No D1 data for {pair['display']}"}
            if isinstance(d1_df.columns, pd.MultiIndex): d1_df.columns = [col[0] for col in d1_df.columns]
            h1_df = yf.download(sym, period="60d", interval="1h", progress=False, auto_adjust=True)
            if h1_df is None or h1_df.empty: h1_df = None
            elif isinstance(h1_df.columns, pd.MultiIndex): h1_df.columns = [col[0] for col in h1_df.columns]
            d1_raw = df_to_candles(d1_df)
            h4_raw = df_to_candles(h1_df.resample("4h").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()) if h1_df is not None else None
            h1_raw = df_to_candles(h1_df) if h1_df is not None else None
        if not d1_raw: return {"error": f"No D1 data for {pair['display']}"}
        if len(d1_raw) < 230: return {"error": f"Insufficient D1 history for {pair['display']} ({len(d1_raw)} bars)"}
    except Exception as e:
        return {"error": f"Data fetch failed: {e}"}

    # Walk forward on D1 bars — reliable 2yr history for all pair types
    trades = []; equity = 1.0; equity_curve = [1.0]
    MIN_BARS = 220; COOLDOWN = 5  # bars to wait after a trade exits
    total_bars = len(d1_raw)
    i = MIN_BARS; last_exit_bar = 0
    while i < total_bars - 1:
        # Cooldown: skip if too close to last trade exit
        if i - last_exit_bar < COOLDOWN:
            i += 1; continue
        d1_window = d1_raw[i - MIN_BARS : i]
        # Use H4/H1 windows if available (yfinance recent), else approximate from D1
        if h4_raw and len(h4_raw) >= 50:
            h4_offset = max(0, len(h4_raw) - max(1, (total_bars - i) * 6))
            h4_window = h4_raw[h4_offset : h4_offset + 80] if h4_offset + 80 <= len(h4_raw) else h4_raw[-80:]
        else:
            h4_window = d1_window[-80:]
        if h1_raw and len(h1_raw) >= 60:
            h1_offset = max(0, len(h1_raw) - max(1, (total_bars - i) * 24))
            h1_window = h1_raw[h1_offset : h1_offset + 120] if h1_offset + 120 <= len(h1_raw) else h1_raw[-120:]
        else:
            h1_window = d1_window[-60:]
        if len(h4_window) < 50 or len(h1_window) < 50:
            i += 1; continue
        try:
            d1i = calc_indicators(d1_window)
            h4i = calc_indicators(h4_window)
            h1i = calc_indicators(h1_window)
            vols = [c["vol"] for c in h1_window]; vsma = calc_sma(vols, 20)
            vr = vols[-1] / vsma[-1] if vsma[-1] and vsma[-1] > 0 else 1.0
            stoch = calc_stochastic(h4_window, 14, 3, 3)
            e200 = calc_ema([c["close"] for c in d1_window], 200)
            e200s = (e200[-1] - e200[-21]) / e200[-21] if e200[-1] and len(e200) >= 21 and e200[-21] else 0
            res = calc_confluence(d1i, h4i, h1i, vr, stoch, e200s, pair, "neutral",
                                   d1_candles=d1_window, h4_candles=h4_window, h1_candles=h1_window)
        except Exception:
            i += 1; continue
        # Per-asset-class threshold — crypto has more data so needs stricter filter
        bt_min = {"crypto":8,"commodity":7}.get(pair["type"], 6)
        if res["score"] < bt_min:
            i += 1; continue
        # 50-bar trend bias filter (Weinstein/Minervini): only trade WITH the D1 macro trend
        d1_closes = [c["close"] for c in d1_window[-50:]]
        d1_macro_mean = sum(d1_closes) / len(d1_closes)
        d1_current = d1_closes[-1]
        macro_long  = d1_current >= d1_macro_mean   # price above 50-bar avg = macro uptrend
        macro_short = d1_current <= d1_macro_mean   # price below 50-bar avg = macro downtrend
        direction = res["direction"]
        if direction == "LONG"  and not macro_long:  i += 1; continue
        if direction == "SHORT" and not macro_short: i += 1; continue
        # Entry at next day's open
        entry_bar = d1_raw[i]
        entry = entry_bar["close"]
        atr = d1i["snap"]["atr"]
        if not atr or atr == 0: i += 1; continue
        m=CONFIG["ATR_CLASS"].get(pair["type"],{"sl":CONFIG["SL_ATR_MULT"],"tp1":CONFIG["TP1_ATR_MULT"],"tp2":CONFIG["TP2_ATR_MULT"]})
        sl_mult=m["sl"]; tp1_mult=m["tp1"]; tp2_mult=m["tp2"]
        sl  = entry - atr * sl_mult  if direction == "LONG" else entry + atr * sl_mult
        tp1 = entry + atr * tp1_mult if direction == "LONG" else entry - atr * tp1_mult
        tp2 = entry + atr * tp2_mult if direction == "LONG" else entry - atr * tp2_mult
        rr1 = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
        # Simulate forward on D1 — up to 20 bars
        outcome = "OPEN"; result_r = 0.0; exit_bar = i
        for j in range(i + 1, min(i + 21, total_bars)):
            bar = d1_raw[j]
            if direction == "LONG":
                if bar["low"] <= sl:   outcome = "SL";  result_r = -1.0; exit_bar = j; break
                if bar["high"] >= tp2: outcome = "TP2"; result_r = tp2_mult / sl_mult; exit_bar = j; break
                if bar["high"] >= tp1: outcome = "TP1"; result_r = rr1; exit_bar = j; break
            else:
                if bar["high"] >= sl:  outcome = "SL";  result_r = -1.0; exit_bar = j; break
                if bar["low"] <= tp2:  outcome = "TP2"; result_r = tp2_mult / sl_mult; exit_bar = j; break
                if bar["low"] <= tp1:  outcome = "TP1"; result_r = rr1; exit_bar = j; break
        if outcome == "OPEN": result_r = 0.0
        equity_change = result_r * CONFIG["RISK_PCT"]
        equity = round(equity * (1 + equity_change), 6)
        equity_curve.append(round(equity, 4))
        trades.append({
            "date": entry_bar["time"][:10],
            "pair": pair["display"], "direction": direction,
            "score": res["score"], "entry": round(entry, 6),
            "sl": round(sl, 6), "tp1": round(tp1, 6), "tp2": round(tp2, 6),
            "outcome": outcome, "resultR": round(result_r, 2)
        })
        if outcome != "OPEN": last_exit_bar = exit_bar
        i = exit_bar + 1 if outcome != "OPEN" else i + 1

    if not trades: return {"error": f"No signals generated for {pair['display']} (threshold too high or insufficient data)"}
    wins = [t for t in trades if t["outcome"] in ("TP1","TP2")]
    losses = [t for t in trades if t["outcome"] == "SL"]
    gross_profit = sum(t["resultR"] for t in wins)
    gross_loss   = abs(sum(t["resultR"] for t in losses))
    win_rate     = round(len(wins) / len(trades) * 100, 1) if trades else 0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
    total_r      = round(sum(t["resultR"] for t in trades), 2)
    r_values     = [t["resultR"] for t in trades]
    avg_r        = round(total_r / len(trades), 3) if trades else 0
    _var = sum((r - avg_r)**2 for r in r_values) / len(r_values) if r_values else 0
    sqn  = round((avg_r / _var**0.5) * (len(trades)**0.5), 2) if len(trades) > 1 and avg_r != 0 and _var > 0 else 0
    peak = 1.0; max_dd = 0.0
    for e in equity_curve:
        if e > peak: peak = e
        dd = (peak - e) / peak
        if dd > max_dd: max_dd = dd
    max_dd_pct = round(max_dd * 100, 1)
    print(f"  [BT] {pair['display']} done: {len(trades)} trades, WR {win_rate}%, PF {profit_factor}, Expect {avg_r}R, SQN {sqn}", flush=True)
    return {
        "pair": pair["display"], "symbol": pair["symbol"], "type": pair["type"],
        "totalTrades": len(trades), "wins": len(wins), "losses": len(losses),
        "winRate": win_rate, "profitFactor": profit_factor,
        "totalR": total_r, "expectancy": avg_r, "sqn": sqn,
        "maxDrawdownPct": max_dd_pct,
        "equityCurve": equity_curve, "trades": trades[-50:]
    }

def run_full_backtest():
    results = []; errors = []
    for pair in ALL_PAIRS:
        r = backtest_pair(pair)
        if "error" in r: errors.append({"pair": pair["display"], "error": r["error"]})
        else: results.append(r)
    results.sort(key=lambda x: x["profitFactor"] if x["profitFactor"] is not None else 999, reverse=True)
    return {"success": True, "results": results, "errors": errors, "totalPairs": len(ALL_PAIRS)}

app=Flask(__name__,static_folder="static")

@app.route("/")
def index(): return send_from_directory("static","index.html")

@app.route("/api/scan",methods=["POST"])
def api_scan(): return jsonify(run_full_scan())

@app.route("/api/analyze",methods=["POST"])
def api_analyze():
    d=request.json
    if not d or "signal" not in d: return jsonify({"error":"No signal"})
    news_ctx = d["signal"].get("newsCtx") or fetch_news_context()
    style_pref = d.get("stylePreference", "auto")
    return jsonify(run_ai(d["signal"], news_ctx, style_pref))

@app.route("/api/backtest",methods=["POST"])
def api_backtest():
    d=request.json or {}
    sym=d.get("symbol")
    if sym:
        pair=next((p for p in ALL_PAIRS if p["symbol"]==sym),None)
        if not pair: return jsonify({"error":f"Unknown symbol: {sym}"})
        return jsonify(backtest_pair(pair))
    return jsonify(run_full_backtest())

@app.route("/api/health")
def health():
    return jsonify({"status":"ok","pairs":len(ALL_PAIRS),
        "dataSource":"yfinance+binance",
        "anthropicKey":CONFIG["ANTHROPIC_KEY"]!="YOUR_ANTHROPIC_API_KEY"})

if __name__=="__main__":
    print("\n"+"="*60)
    print("  ATHENA PRO v2.0 - Python Edition")
    print("="*60)
    active_fx=sum(1 for p in FOREX_PAIRS if p.get("enabled",True))
    active_cr=sum(1 for p in CRYPTO_PAIRS if p.get("enabled",True))
    print(f"  Pairs: {len(ACTIVE_PAIRS)} active / {len(ALL_PAIRS)} total ({active_fx}fx {len(COMMODITY_PAIRS)}cmd {sum(1 for p in INDEX_PAIRS if p.get('enabled',True))}idx {sum(1 for p in JSE_PAIRS if p.get('enabled',True))}jse {active_cr}crypto)")
    print(f"  Data: yfinance (free) + Binance (free)")
    print(f"  Anthropic: {'SET' if CONFIG['ANTHROPIC_KEY']!='YOUR_ANTHROPIC_API_KEY' else 'NOT SET'}")
    print(f"  Est. scan time: ~30s")
    if "--scan" in sys.argv:
        print("\n  [SCAN MODE] Running full scan...")
        print("="*60)
        scan_result = run_full_scan()
        print("\n"+"="*60)
        print(f"  Scan complete: {scan_result['totalPairs']} pairs, {len(scan_result['signals'])} signals")
        if scan_result['errors']: print(f"  Errors ({len(scan_result['errors'])}): {[e['pair']+': '+e['error'] for e in scan_result['errors']]}")
        if scan_result['skipped']: print(f"  Skipped ({len(scan_result['skipped'])}): {[s['pair'] for s in scan_result['skipped']]}")
        if scan_result['signals']:
            print("\n  [AI TEST] Testing AI on top signal...")
            top = scan_result['signals'][0]
            ai_result = run_ai(top)
            if "error" in ai_result:
                print(f"  [AI TEST] FAILED: {ai_result['error']}", flush=True)
            else:
                print(f"  [AI TEST] OK => Grade:{ai_result.get('grade','?')} Confidence:{ai_result.get('confidence','?')}%", flush=True)
        else:
            print("  [AI TEST] Skipped — no signals to test", flush=True)
        print("="*60)
        sys.exit(0)
    print(f"\n  http://localhost:5000")
    print("="*60)
    threading.Timer(1.5,lambda:webbrowser.open("http://localhost:5000")).start()
    app.run(host="0.0.0.0",port=5000,debug=False)