"""ASE instrument universe — derived from ALL_PAIRS (134 instruments)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from athena_ase.data.costs import ModelFamily

Horizon = Literal['intraday', 'swing']


@dataclass(frozen=True)
class Instrument:
    symbol: str
    display: str
    family: ModelFamily
    subclass: str
    eodhd_symbol: str
    benchmark: str
    swing_only: bool = False
    mt5_live: bool = True


def compact_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").replace(" ", "").upper()


UNIVERSE: tuple[Instrument, ...] = (
    Instrument("EURUSD", "EUR/USD", "forex", "major", "EURUSD.FOREX", "USDX"),
    Instrument("GBPUSD", "GBP/USD", "forex", "major", "GBPUSD.FOREX", "USDX"),
    Instrument("USDJPY", "USD/JPY", "forex", "major", "USDJPY.FOREX", "USDX"),
    Instrument("AUDUSD", "AUD/USD", "forex", "major", "AUDUSD.FOREX", "USDX"),
    Instrument("AUDCHF", "AUD/CHF", "forex", "cross_em", "AUDCHF.FOREX", "USDX"),
    Instrument("AUDNZD", "AUD/NZD", "forex", "cross_em", "AUDNZD.FOREX", "USDX"),
    Instrument("NZDUSD", "NZD/USD", "forex", "major", "NZDUSD.FOREX", "USDX"),
    Instrument("EURGBP", "EUR/GBP", "forex", "cross_em", "EURGBP.FOREX", "USDX"),
    Instrument("USDCAD", "USD/CAD", "forex", "major", "USDCAD.FOREX", "USDX"),
    Instrument("USDCHF", "USD/CHF", "forex", "major", "USDCHF.FOREX", "USDX"),
    Instrument("EURJPY", "EUR/JPY", "forex", "cross_em", "EURJPY.FOREX", "USDX"),
    Instrument("GBPJPY", "GBP/JPY", "forex", "cross_em", "GBPJPY.FOREX", "USDX"),
    Instrument("AUDJPY", "AUD/JPY", "forex", "cross_em", "AUDJPY.FOREX", "USDX"),
    Instrument("EURAUD", "EUR/AUD", "forex", "cross_em", "EURAUD.FOREX", "USDX"),
    Instrument("GBPAUD", "GBP/AUD", "forex", "cross_em", "GBPAUD.FOREX", "USDX"),
    Instrument("USDZAR", "USD/ZAR", "forex", "cross_em", "USDZAR.FOREX", "USDX"),
    Instrument("EURCHF", "EUR/CHF", "forex", "cross_em", "EURCHF.FOREX", "USDX"),
    Instrument("USDMXN", "USD/MXN", "forex", "cross_em", "USDMXN.FOREX", "USDX"),
    Instrument("USDSGD", "USD/SGD", "forex", "cross_em", "USDSGD.FOREX", "USDX"),
    Instrument("USDBRL", "USD/BRL", "forex", "cross_em", "USDBRL.FOREX", "USDX"),
    Instrument("USDINR", "USD/INR", "forex", "cross_em", "USDINR.FOREX", "USDX"),
    Instrument("GC=F", "XAU/USD", "commodity", "cfd", "XAUUSD.FOREX", "XAUUSD"),
    Instrument("SI=F", "XAG/USD", "commodity", "cfd", "XAGUSD.FOREX", "XAUUSD"),
    Instrument("CL=F", "WTI Oil", "commodity", "cfd", "CL", "XAUUSD"),
    Instrument("BZ=F", "Brent Oil", "commodity", "cfd", "BZ", "XAUUSD"),
    Instrument("NATGAS", "Nat Gas", "commodity", "cfd", "NG", "XAUUSD"),
    Instrument("COPPER", "Copper", "commodity", "cfd", "COPPER", "XAUUSD"),
    Instrument("ALUMINIUM", "Aluminium", "commodity", "cfd", "ALUMINIUM", "XAUUSD"),
    Instrument("LEAD", "Lead", "commodity", "cfd", "LEAD", "XAUUSD"),
    Instrument("NICKEL", "Nickel", "commodity", "cfd", "NICKEL", "XAUUSD"),
    Instrument("ZINC", "Zinc", "commodity", "cfd", "ZINC", "XAUUSD"),
    Instrument("PL=F", "XPT/USD", "commodity", "cfd", "XPTUSD.FOREX", "XAUUSD"),
    Instrument("PA=F", "XPD/USD", "commodity", "cfd", "XPDUSD.FOREX", "XAUUSD"),
    Instrument("GASOLINE", "Gasoline", "commodity", "cfd", "GASOLINE", "XAUUSD"),
    Instrument("CATTLE", "Cattle", "commodity", "cfd", "CATTLE", "XAUUSD"),
    Instrument("COCOA", "Cocoa", "commodity", "cfd", "COCOA", "XAUUSD"),
    Instrument("COFFEE", "Coffee", "commodity", "cfd", "COFFEE", "XAUUSD"),
    Instrument("CORN", "Corn", "commodity", "cfd", "CORN", "XAUUSD"),
    Instrument("COTTON", "Cotton", "commodity", "cfd", "COTTON", "XAUUSD"),
    Instrument("SOYBEANS", "Soybeans", "commodity", "cfd", "SOYBEANS", "XAUUSD"),
    Instrument("SUGAR", "Sugar", "commodity", "cfd", "SUGAR", "XAUUSD"),
    Instrument("WHEAT", "Wheat", "commodity", "cfd", "WHEAT", "XAUUSD"),
    Instrument("NAS100", "NASDAQ-100", "index_etf", "default", "IXIC.INDX", "SPY"),
    Instrument("^GSPC", "S&P 500", "index_etf", "default", "GSPC.INDX", "SPY"),
    Instrument("US2000", "US2000", "index_etf", "default", "US2000", "SPY"),
    Instrument("^DJI", "Dow Jones", "index_etf", "default", "DJI.INDX", "SPY"),
    Instrument("^GDAXI", "DAX 40", "index_etf", "default", "GDAXI.INDX", "SPY"),
    Instrument("^FTSE", "UK100", "index_etf", "default", "FTSE.INDX", "SPY"),
    Instrument("^AXJO", "ASX 200", "index_etf", "default", "AXJO.INDX", "SPY"),
    Instrument("^N225", "Nikkei 225", "index_etf", "default", "N225.INDX", "SPY"),
    Instrument("^HSI", "Hang Seng", "index_etf", "default", "HSI.INDX", "SPY"),
    Instrument("EURX", "EURX", "index_etf", "default", "EURX", "SPY"),
    Instrument("JPYX", "JPYX", "index_etf", "default", "JPYX", "SPY"),
    Instrument("USDX", "USDX", "index_etf", "default", "USDX", "SPY"),
    Instrument("AAPL", "AAPL", "equity", "us", "AAPL.US", "SPY"),
    Instrument("TSLA", "TSLA", "equity", "us", "TSLA.US", "SPY"),
    Instrument("NVDA", "NVDA", "equity", "us", "NVDA.US", "SPY"),
    Instrument("MSFT", "MSFT", "equity", "us", "MSFT.US", "SPY"),
    Instrument("AMZN", "AMZN", "equity", "us", "AMZN.US", "SPY"),
    Instrument("META", "META", "equity", "us", "META.US", "SPY"),
    Instrument("GOOGL", "GOOGL", "equity", "us", "GOOGL.US", "SPY"),
    Instrument("AVGO", "AVGO", "equity", "us", "AVGO.US", "SPY"),
    Instrument("JPM", "JPM", "equity", "us", "JPM.US", "SPY"),
    Instrument("V", "V", "equity", "us", "V.US", "SPY"),
    Instrument("XOM", "XOM", "equity", "us", "XOM.US", "SPY"),
    Instrument("NFLX", "NFLX", "equity", "us", "NFLX.US", "SPY"),
    Instrument("AMD", "AMD", "equity", "us", "AMD.US", "SPY"),
    Instrument("CRM", "CRM", "equity", "us", "CRM.US", "SPY"),
    Instrument("DIS", "DIS", "equity", "us", "DIS.US", "SPY"),
    Instrument("BA", "BA", "equity", "us", "BA.US", "SPY"),
    Instrument("COIN", "COIN", "equity", "us", "COIN.US", "SPY"),
    Instrument("PYPL", "PYPL", "equity", "us", "PYPL.US", "SPY"),
    Instrument("INTC", "INTC", "equity", "us", "INTC.US", "SPY"),
    Instrument("UBER", "UBER", "equity", "us", "UBER.US", "SPY"),
    Instrument("PLTR", "PLTR", "equity", "us", "PLTR.US", "SPY"),
    Instrument("SPY", "SPY", "index_etf", "default", "SPY.US", "SPY"),
    Instrument("QQQ", "QQQ", "index_etf", "default", "QQQ.US", "SPY"),
    Instrument("TQQQ", "TQQQ", "index_etf", "default", "TQQQ.US", "SPY", False, False),
    Instrument("SQQQ", "SQQQ", "index_etf", "default", "SQQQ.US", "SPY", False, False),
    Instrument("GLD", "GLD", "index_etf", "default", "GLD.US", "SPY"),
    Instrument("TLT", "TLT", "index_etf", "default", "TLT.US", "SPY"),
    Instrument("IWM", "IWM", "index_etf", "default", "IWM.US", "SPY"),
    Instrument("EEM", "EEM", "index_etf", "default", "EEM.US", "SPY"),
    Instrument("DIA", "DIA", "index_etf", "default", "DIA.US", "SPY"),
    Instrument("GDX", "GDX", "index_etf", "default", "GDX.US", "SPY"),
    Instrument("SOXX", "SOXX", "index_etf", "default", "SOXX.US", "SPY"),
    Instrument("XLE", "XLE", "index_etf", "default", "XLE.US", "SPY"),
    Instrument("SLV", "SLV", "index_etf", "default", "SLV.US", "SPY"),
    Instrument("USO", "USO", "index_etf", "default", "USO.US", "SPY"),
    Instrument("NPN.JO", "Naspers", "equity", "jse", "NPN.JO.US", "SPY", True, False),
    Instrument("SOL.JO", "Sasol", "equity", "jse", "SOL.JO.US", "SPY", True, False),
    Instrument("SBK.JO", "Std Bank", "equity", "jse", "SBK.JO.US", "SPY", True, False),
    Instrument("AGL.JO", "Anglo Am", "equity", "jse", "AGL.JO.US", "SPY", True, False),
    Instrument("MTN.JO", "MTN Group", "equity", "jse", "MTN.JO.US", "SPY", True, False),
    Instrument("SHP.JO", "Shoprite", "equity", "jse", "SHP.JO.US", "SPY", True, False),
    Instrument("CFR.JO", "Richemont", "equity", "jse", "CFR.JO.US", "SPY", True, False),
    Instrument("FSR.JO", "FirstRand", "equity", "jse", "FSR.JO.US", "SPY", True, False),
    Instrument("ABG.JO", "Absa", "equity", "jse", "ABG.JO.US", "SPY", True, False),
    Instrument("CPI.JO", "Capitec", "equity", "jse", "CPI.JO.US", "SPY", True, False),
    Instrument("PRX.JO", "Prosus", "equity", "jse", "PRX.JO.US", "SPY", True, False),
    Instrument("GFI.JO", "Gold Fields", "equity", "jse", "GFI.JO.US", "SPY", True, False),
    Instrument("ANG.JO", "AngloGold", "equity", "jse", "ANG.JO.US", "SPY", True, False),
    Instrument("SSW.JO", "Sibanye", "equity", "jse", "SSW.JO.US", "SPY", True, False),
    Instrument("BTCUSDT", "BTC/USDT", "crypto", "major", "BTCUSDT", "BTCUSDT", False, False),
    Instrument("ETHUSDT", "ETH/USDT", "crypto", "major", "ETHUSDT", "BTCUSDT", False, False),
    Instrument("XRPUSDT", "XRP/USDT", "crypto", "major", "XRPUSDT", "BTCUSDT", False, False),
    Instrument("SOLUSDT", "SOL/USDT", "crypto", "major", "SOLUSDT", "BTCUSDT", False, False),
    Instrument("ADAUSDT", "ADA/USDT", "crypto", "alt", "ADAUSDT", "BTCUSDT", False, False),
    Instrument("DOGEUSDT", "DOGE/USDT", "crypto", "alt", "DOGEUSDT", "BTCUSDT", False, False),
    Instrument("AVAXUSDT", "AVAX/USDT", "crypto", "alt", "AVAXUSDT", "BTCUSDT", False, False),
    Instrument("LINKUSDT", "LINK/USDT", "crypto", "alt", "LINKUSDT", "BTCUSDT", False, False),
    Instrument("POLUSDT", "POL/USDT", "crypto", "alt", "POLUSDT", "BTCUSDT", False, False),
    Instrument("BNBUSDT", "BNB/USDT", "crypto", "major", "BNBUSDT", "BTCUSDT", False, False),
    Instrument("DOTUSDT", "DOT/USDT", "crypto", "alt", "DOTUSDT", "BTCUSDT", False, False),
    Instrument("LTCUSDT", "LTC/USDT", "crypto", "alt", "LTCUSDT", "BTCUSDT", False, False),
    Instrument("SUIUSDT", "SUI/USDT", "crypto", "alt", "SUIUSDT", "BTCUSDT", False, False),
    Instrument("NEARUSDT", "NEAR/USDT", "crypto", "alt", "NEARUSDT", "BTCUSDT", False, False),
    Instrument("APTUSDT", "APT/USDT", "crypto", "alt", "APTUSDT", "BTCUSDT", False, False),
    Instrument("INJUSDT", "INJ/USDT", "crypto", "alt", "INJUSDT", "BTCUSDT", False, False),
    Instrument("RENDERUSDT", "RENDER/USDT", "crypto", "alt", "RENDERUSDT", "BTCUSDT", False, False),
    Instrument("AAVEUSDT", "AAVE/USDT", "crypto", "alt", "AAVEUSDT", "BTCUSDT", False, False),
    Instrument("ALGOUSDT", "ALGO/USDT", "crypto", "alt", "ALGOUSDT", "BTCUSDT", False, False),
    Instrument("ATOMUSDT", "ATOM/USDT", "crypto", "alt", "ATOMUSDT", "BTCUSDT", False, False),
    Instrument("BCHUSDT", "BCH/USDT", "crypto", "alt", "BCHUSDT", "BTCUSDT", False, False),
    Instrument("ETCUSDT", "ETC/USDT", "crypto", "alt", "ETCUSDT", "BTCUSDT", False, False),
    Instrument("TRXUSDT", "TRX/USDT", "crypto", "alt", "TRXUSDT", "BTCUSDT", False, False),
    Instrument("XLMUSDT", "XLM/USDT", "crypto", "alt", "XLMUSDT", "BTCUSDT", False, False),
    Instrument("UNIUSDT", "UNI/USDT", "crypto", "alt", "UNIUSDT", "BTCUSDT", False, False),
    Instrument("FILUSDT", "FIL/USDT", "crypto", "alt", "FILUSDT", "BTCUSDT", False, False),
    Instrument("ICPUSDT", "ICP/USDT", "crypto", "alt", "ICPUSDT", "BTCUSDT", False, False),
    Instrument("HBARUSDT", "HBAR/USDT", "crypto", "alt", "HBARUSDT", "BTCUSDT", False, False),
    Instrument("ARBUSDT", "ARB/USDT", "crypto", "alt", "ARBUSDT", "BTCUSDT", False, False),
    Instrument("OPUSDT", "OP/USDT", "crypto", "alt", "OPUSDT", "BTCUSDT", False, False),
    Instrument("SEIUSDT", "SEI/USDT", "crypto", "alt", "SEIUSDT", "BTCUSDT", False, False),
)

DEFAULT_INSTRUMENTS = UNIVERSE


def instruments_for_family(family: ModelFamily) -> list[Instrument]:
    return [i for i in UNIVERSE if i.family == family]


def instrument_by_symbol(symbol: str) -> Instrument | None:
    key = compact_symbol(symbol)
    for inst in UNIVERSE:
        if compact_symbol(inst.symbol) == key:
            return inst
    return None
