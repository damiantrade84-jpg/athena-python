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


def compact_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").replace(" ", "").upper()


UNIVERSE: tuple[Instrument, ...] = (
    Instrument("EURUSD", "EUR/USD", "forex", "major", "EURUSD.FOREX", "USDX", False),
    Instrument("GBPUSD", "GBP/USD", "forex", "major", "GBPUSD.FOREX", "USDX", False),
    Instrument("USDJPY", "USD/JPY", "forex", "major", "USDJPY.FOREX", "USDX", False),
    Instrument("AUDUSD", "AUD/USD", "forex", "major", "AUDUSD.FOREX", "USDX", False),
    Instrument("AUDCHF", "AUD/CHF", "forex", "cross_em", "AUDCHF.FOREX", "USDX", False),
    Instrument("AUDNZD", "AUD/NZD", "forex", "cross_em", "AUDNZD.FOREX", "USDX", False),
    Instrument("NZDUSD", "NZD/USD", "forex", "major", "NZDUSD.FOREX", "USDX", False),
    Instrument("EURGBP", "EUR/GBP", "forex", "cross_em", "EURGBP.FOREX", "USDX", False),
    Instrument("USDCAD", "USD/CAD", "forex", "major", "USDCAD.FOREX", "USDX", False),
    Instrument("USDCHF", "USD/CHF", "forex", "major", "USDCHF.FOREX", "USDX", False),
    Instrument("EURJPY", "EUR/JPY", "forex", "cross_em", "EURJPY.FOREX", "USDX", False),
    Instrument("GBPJPY", "GBP/JPY", "forex", "cross_em", "GBPJPY.FOREX", "USDX", False),
    Instrument("AUDJPY", "AUD/JPY", "forex", "cross_em", "AUDJPY.FOREX", "USDX", False),
    Instrument("EURAUD", "EUR/AUD", "forex", "cross_em", "EURAUD.FOREX", "USDX", False),
    Instrument("GBPAUD", "GBP/AUD", "forex", "cross_em", "GBPAUD.FOREX", "USDX", False),
    Instrument("USDZAR", "USD/ZAR", "forex", "cross_em", "USDZAR.FOREX", "USDX", False),
    Instrument("EURCHF", "EUR/CHF", "forex", "cross_em", "EURCHF.FOREX", "USDX", False),
    Instrument("USDMXN", "USD/MXN", "forex", "cross_em", "USDMXN.FOREX", "USDX", False),
    Instrument("USDSGD", "USD/SGD", "forex", "cross_em", "USDSGD.FOREX", "USDX", False),
    Instrument("USDBRL", "USD/BRL", "forex", "cross_em", "USDBRL.FOREX", "USDX", False),
    Instrument("USDINR", "USD/INR", "forex", "cross_em", "USDINR.FOREX", "USDX", False),
    Instrument("GC=F", "XAU/USD", "commodity", "cfd", "XAUUSD.FOREX", "XAUUSD", False),
    Instrument("SI=F", "XAG/USD", "commodity", "cfd", "XAGUSD.FOREX", "XAUUSD", False),
    Instrument("CL=F", "WTI Oil", "commodity", "cfd", "CL", "XAUUSD", False),
    Instrument("BZ=F", "Brent Oil", "commodity", "cfd", "BZ", "XAUUSD", False),
    Instrument("NATGAS", "Nat Gas", "commodity", "cfd", "NG", "XAUUSD", False),
    Instrument("COPPER", "Copper", "commodity", "cfd", "COPPER", "XAUUSD", False),
    Instrument("ALUMINIUM", "Aluminium", "commodity", "cfd", "ALUMINIUM", "XAUUSD", False),
    Instrument("LEAD", "Lead", "commodity", "cfd", "LEAD", "XAUUSD", False),
    Instrument("NICKEL", "Nickel", "commodity", "cfd", "NICKEL", "XAUUSD", False),
    Instrument("ZINC", "Zinc", "commodity", "cfd", "ZINC", "XAUUSD", False),
    Instrument("PL=F", "XPT/USD", "commodity", "cfd", "XPTUSD.FOREX", "XAUUSD", False),
    Instrument("PA=F", "XPD/USD", "commodity", "cfd", "XPDUSD.FOREX", "XAUUSD", False),
    Instrument("GASOLINE", "Gasoline", "commodity", "cfd", "GASOLINE", "XAUUSD", False),
    Instrument("CATTLE", "Cattle", "commodity", "cfd", "CATTLE", "XAUUSD", False),
    Instrument("COCOA", "Cocoa", "commodity", "cfd", "COCOA", "XAUUSD", False),
    Instrument("COFFEE", "Coffee", "commodity", "cfd", "COFFEE", "XAUUSD", False),
    Instrument("CORN", "Corn", "commodity", "cfd", "CORN", "XAUUSD", False),
    Instrument("COTTON", "Cotton", "commodity", "cfd", "COTTON", "XAUUSD", False),
    Instrument("SOYBEANS", "Soybeans", "commodity", "cfd", "SOYBEANS", "XAUUSD", False),
    Instrument("SUGAR", "Sugar", "commodity", "cfd", "SUGAR", "XAUUSD", False),
    Instrument("WHEAT", "Wheat", "commodity", "cfd", "WHEAT", "XAUUSD", False),
    Instrument("NAS100", "NASDAQ-100", "index_etf", "default", "IXIC.INDX", "SPY", False),
    Instrument("^GSPC", "S&P 500", "index_etf", "default", "GSPC.INDX", "SPY", False),
    Instrument("US2000", "US2000", "index_etf", "default", "US2000", "SPY", False),
    Instrument("^DJI", "Dow Jones", "index_etf", "default", "DJI.INDX", "SPY", False),
    Instrument("^GDAXI", "DAX 40", "index_etf", "default", "GDAXI.INDX", "SPY", False),
    Instrument("^FTSE", "UK100", "index_etf", "default", "FTSE.INDX", "SPY", False),
    Instrument("^AXJO", "ASX 200", "index_etf", "default", "AXJO.INDX", "SPY", False),
    Instrument("^N225", "Nikkei 225", "index_etf", "default", "N225.INDX", "SPY", False),
    Instrument("^HSI", "Hang Seng", "index_etf", "default", "HSI.INDX", "SPY", False),
    Instrument("EURX", "EURX", "index_etf", "default", "EURX", "SPY", False),
    Instrument("JPYX", "JPYX", "index_etf", "default", "JPYX", "SPY", False),
    Instrument("USDX", "USDX", "index_etf", "default", "USDX", "SPY", False),
    Instrument("AAPL", "AAPL", "equity", "us", "AAPL.US", "SPY", False),
    Instrument("TSLA", "TSLA", "equity", "us", "TSLA.US", "SPY", False),
    Instrument("NVDA", "NVDA", "equity", "us", "NVDA.US", "SPY", False),
    Instrument("MSFT", "MSFT", "equity", "us", "MSFT.US", "SPY", False),
    Instrument("AMZN", "AMZN", "equity", "us", "AMZN.US", "SPY", False),
    Instrument("META", "META", "equity", "us", "META.US", "SPY", False),
    Instrument("GOOGL", "GOOGL", "equity", "us", "GOOGL.US", "SPY", False),
    Instrument("AVGO", "AVGO", "equity", "us", "AVGO.US", "SPY", False),
    Instrument("JPM", "JPM", "equity", "us", "JPM.US", "SPY", False),
    Instrument("V", "V", "equity", "us", "V.US", "SPY", False),
    Instrument("XOM", "XOM", "equity", "us", "XOM.US", "SPY", False),
    Instrument("NFLX", "NFLX", "equity", "us", "NFLX.US", "SPY", False),
    Instrument("AMD", "AMD", "equity", "us", "AMD.US", "SPY", False),
    Instrument("CRM", "CRM", "equity", "us", "CRM.US", "SPY", False),
    Instrument("DIS", "DIS", "equity", "us", "DIS.US", "SPY", False),
    Instrument("BA", "BA", "equity", "us", "BA.US", "SPY", False),
    Instrument("COIN", "COIN", "equity", "us", "COIN.US", "SPY", False),
    Instrument("PYPL", "PYPL", "equity", "us", "PYPL.US", "SPY", False),
    Instrument("INTC", "INTC", "equity", "us", "INTC.US", "SPY", False),
    Instrument("UBER", "UBER", "equity", "us", "UBER.US", "SPY", False),
    Instrument("PLTR", "PLTR", "equity", "us", "PLTR.US", "SPY", False),
    Instrument("SPY", "SPY", "index_etf", "default", "SPY.US", "SPY", False),
    Instrument("QQQ", "QQQ", "index_etf", "default", "QQQ.US", "SPY", False),
    Instrument("TQQQ", "TQQQ", "index_etf", "default", "TQQQ.US", "SPY", False),
    Instrument("SQQQ", "SQQQ", "index_etf", "default", "SQQQ.US", "SPY", False),
    Instrument("GLD", "GLD", "index_etf", "default", "GLD.US", "SPY", False),
    Instrument("TLT", "TLT", "index_etf", "default", "TLT.US", "SPY", False),
    Instrument("IWM", "IWM", "index_etf", "default", "IWM.US", "SPY", False),
    Instrument("EEM", "EEM", "index_etf", "default", "EEM.US", "SPY", False),
    Instrument("DIA", "DIA", "index_etf", "default", "DIA.US", "SPY", False),
    Instrument("GDX", "GDX", "index_etf", "default", "GDX.US", "SPY", False),
    Instrument("SOXX", "SOXX", "index_etf", "default", "SOXX.US", "SPY", False),
    Instrument("XLE", "XLE", "index_etf", "default", "XLE.US", "SPY", False),
    Instrument("SLV", "SLV", "index_etf", "default", "SLV.US", "SPY", False),
    Instrument("USO", "USO", "index_etf", "default", "USO.US", "SPY", False),
    Instrument("NPN.JO", "Naspers", "equity", "jse", "NPN.JO.US", "SPY", True),
    Instrument("SOL.JO", "Sasol", "equity", "jse", "SOL.JO.US", "SPY", True),
    Instrument("SBK.JO", "Std Bank", "equity", "jse", "SBK.JO.US", "SPY", True),
    Instrument("AGL.JO", "Anglo Am", "equity", "jse", "AGL.JO.US", "SPY", True),
    Instrument("MTN.JO", "MTN Group", "equity", "jse", "MTN.JO.US", "SPY", True),
    Instrument("SHP.JO", "Shoprite", "equity", "jse", "SHP.JO.US", "SPY", True),
    Instrument("CFR.JO", "Richemont", "equity", "jse", "CFR.JO.US", "SPY", True),
    Instrument("FSR.JO", "FirstRand", "equity", "jse", "FSR.JO.US", "SPY", True),
    Instrument("ABG.JO", "Absa", "equity", "jse", "ABG.JO.US", "SPY", True),
    Instrument("CPI.JO", "Capitec", "equity", "jse", "CPI.JO.US", "SPY", True),
    Instrument("PRX.JO", "Prosus", "equity", "jse", "PRX.JO.US", "SPY", True),
    Instrument("GFI.JO", "Gold Fields", "equity", "jse", "GFI.JO.US", "SPY", True),
    Instrument("ANG.JO", "AngloGold", "equity", "jse", "ANG.JO.US", "SPY", True),
    Instrument("SSW.JO", "Sibanye", "equity", "jse", "SSW.JO.US", "SPY", True),
    Instrument("BTCUSDT", "BTC/USDT", "crypto", "major", "BTCUSDT", "BTCUSDT", False),
    Instrument("ETHUSDT", "ETH/USDT", "crypto", "major", "ETHUSDT", "BTCUSDT", False),
    Instrument("XRPUSDT", "XRP/USDT", "crypto", "major", "XRPUSDT", "BTCUSDT", False),
    Instrument("SOLUSDT", "SOL/USDT", "crypto", "major", "SOLUSDT", "BTCUSDT", False),
    Instrument("ADAUSDT", "ADA/USDT", "crypto", "alt", "ADAUSDT", "BTCUSDT", False),
    Instrument("DOGEUSDT", "DOGE/USDT", "crypto", "alt", "DOGEUSDT", "BTCUSDT", False),
    Instrument("AVAXUSDT", "AVAX/USDT", "crypto", "alt", "AVAXUSDT", "BTCUSDT", False),
    Instrument("LINKUSDT", "LINK/USDT", "crypto", "alt", "LINKUSDT", "BTCUSDT", False),
    Instrument("POLUSDT", "POL/USDT", "crypto", "alt", "POLUSDT", "BTCUSDT", False),
    Instrument("BNBUSDT", "BNB/USDT", "crypto", "major", "BNBUSDT", "BTCUSDT", False),
    Instrument("DOTUSDT", "DOT/USDT", "crypto", "alt", "DOTUSDT", "BTCUSDT", False),
    Instrument("LTCUSDT", "LTC/USDT", "crypto", "alt", "LTCUSDT", "BTCUSDT", False),
    Instrument("SUIUSDT", "SUI/USDT", "crypto", "alt", "SUIUSDT", "BTCUSDT", False),
    Instrument("NEARUSDT", "NEAR/USDT", "crypto", "alt", "NEARUSDT", "BTCUSDT", False),
    Instrument("APTUSDT", "APT/USDT", "crypto", "alt", "APTUSDT", "BTCUSDT", False),
    Instrument("INJUSDT", "INJ/USDT", "crypto", "alt", "INJUSDT", "BTCUSDT", False),
    Instrument("RENDERUSDT", "RENDER/USDT", "crypto", "alt", "RENDERUSDT", "BTCUSDT", False),
    Instrument("AAVEUSDT", "AAVE/USDT", "crypto", "alt", "AAVEUSDT", "BTCUSDT", False),
    Instrument("ALGOUSDT", "ALGO/USDT", "crypto", "alt", "ALGOUSDT", "BTCUSDT", False),
    Instrument("ATOMUSDT", "ATOM/USDT", "crypto", "alt", "ATOMUSDT", "BTCUSDT", False),
    Instrument("BCHUSDT", "BCH/USDT", "crypto", "alt", "BCHUSDT", "BTCUSDT", False),
    Instrument("ETCUSDT", "ETC/USDT", "crypto", "alt", "ETCUSDT", "BTCUSDT", False),
    Instrument("TRXUSDT", "TRX/USDT", "crypto", "alt", "TRXUSDT", "BTCUSDT", False),
    Instrument("XLMUSDT", "XLM/USDT", "crypto", "alt", "XLMUSDT", "BTCUSDT", False),
    Instrument("UNIUSDT", "UNI/USDT", "crypto", "alt", "UNIUSDT", "BTCUSDT", False),
    Instrument("FILUSDT", "FIL/USDT", "crypto", "alt", "FILUSDT", "BTCUSDT", False),
    Instrument("ICPUSDT", "ICP/USDT", "crypto", "alt", "ICPUSDT", "BTCUSDT", False),
    Instrument("HBARUSDT", "HBAR/USDT", "crypto", "alt", "HBARUSDT", "BTCUSDT", False),
    Instrument("ARBUSDT", "ARB/USDT", "crypto", "alt", "ARBUSDT", "BTCUSDT", False),
    Instrument("OPUSDT", "OP/USDT", "crypto", "alt", "OPUSDT", "BTCUSDT", False),
    Instrument("SEIUSDT", "SEI/USDT", "crypto", "alt", "SEIUSDT", "BTCUSDT", False),
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
