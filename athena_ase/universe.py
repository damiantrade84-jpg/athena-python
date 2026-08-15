"""ASE instrument universe — Engine A active ATFX book (162 instruments)."""

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


# ATFX quotes Alphabet Class C as GOOG. Keep GOOGL as a lookup alias so old
# journal / PTIS rows still resolve.
_SYMBOL_ALIASES: dict[str, str] = {
    "GOOGL": "GOOG",
}


def _lookup_keys(symbol: str) -> set[str]:
    key = compact_symbol(symbol)
    keys = {key}
    alias = _SYMBOL_ALIASES.get(key)
    if alias:
        keys.add(alias)
    if key.endswith("=X") and len(key) > 2:
        keys.add(key[:-2])
    if key.endswith(".FOREX") and len(key) > 6:
        keys.add(key[:-6])
    return {k for k in keys if k}


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
    # ATFX Market Watch additions (athena.py FOREX_PAIRS.extend, 2026-07-28).
    Instrument("AUDCAD", "AUD/CAD", "forex", "cross_em", "AUDCAD.FOREX", "USDX"),
    Instrument("CADCHF", "CAD/CHF", "forex", "cross_em", "CADCHF.FOREX", "USDX"),
    Instrument("CADJPY", "CAD/JPY", "forex", "cross_em", "CADJPY.FOREX", "USDX"),
    Instrument("CHFJPY", "CHF/JPY", "forex", "cross_em", "CHFJPY.FOREX", "USDX"),
    Instrument("EURCAD", "EUR/CAD", "forex", "cross_em", "EURCAD.FOREX", "USDX"),
    Instrument("EURNZD", "EUR/NZD", "forex", "cross_em", "EURNZD.FOREX", "USDX"),
    Instrument("GBPCAD", "GBP/CAD", "forex", "cross_em", "GBPCAD.FOREX", "USDX"),
    Instrument("GBPCHF", "GBP/CHF", "forex", "cross_em", "GBPCHF.FOREX", "USDX"),
    Instrument("GBPNZD", "GBP/NZD", "forex", "cross_em", "GBPNZD.FOREX", "USDX"),
    Instrument("NZDCAD", "NZD/CAD", "forex", "cross_em", "NZDCAD.FOREX", "USDX"),
    Instrument("NZDCHF", "NZD/CHF", "forex", "cross_em", "NZDCHF.FOREX", "USDX"),
    Instrument("NZDJPY", "NZD/JPY", "forex", "cross_em", "NZDJPY.FOREX", "USDX"),
    Instrument("EURHUF", "EUR/HUF", "forex", "cross_em", "EURHUF.FOREX", "USDX"),
    Instrument("EURPLN", "EUR/PLN", "forex", "cross_em", "EURPLN.FOREX", "USDX"),
    Instrument("EURZAR", "EUR/ZAR", "forex", "cross_em", "EURZAR.FOREX", "USDX"),
    Instrument("GBPZAR", "GBP/ZAR", "forex", "cross_em", "GBPZAR.FOREX", "USDX"),
    Instrument("USDCNH", "USD/CNH", "forex", "cross_em", "USDCNH.FOREX", "USDX"),
    Instrument("USDCZK", "USD/CZK", "forex", "cross_em", "USDCZK.FOREX", "USDX"),
    Instrument("USDDKK", "USD/DKK", "forex", "cross_em", "USDDKK.FOREX", "USDX"),
    Instrument("USDNOK", "USD/NOK", "forex", "cross_em", "USDNOK.FOREX", "USDX"),
    Instrument("USDHUF", "USD/HUF", "forex", "cross_em", "USDHUF.FOREX", "USDX"),
    Instrument("USDPLN", "USD/PLN", "forex", "cross_em", "USDPLN.FOREX", "USDX"),
    Instrument("USDSEK", "USD/SEK", "forex", "cross_em", "USDSEK.FOREX", "USDX"),
    Instrument("GC=F", "XAU/USD", "commodity", "cfd", "XAUUSD.FOREX", "XAUUSD"),
    Instrument("XAUZAR", "XAU/ZAR", "commodity", "cfd", "XAUZAR.FOREX", "XAUUSD"),
    Instrument("SI=F", "XAG/USD", "commodity", "cfd", "XAGUSD.FOREX", "XAUUSD"),
    Instrument("CL=F", "WTI Oil", "commodity", "cfd", "CL", "XAUUSD"),
    Instrument("BZ=F", "Brent Oil", "commodity", "cfd", "BZ", "XAUUSD"),
    Instrument("NATGAS", "Nat Gas", "commodity", "cfd", "NG", "XAUUSD"),
    Instrument("NAS100", "NASDAQ-100", "index_etf", "default", "IXIC.INDX", "SPY"),
    Instrument("^GSPC", "S&P 500", "index_etf", "default", "GSPC.INDX", "SPY"),
    Instrument("US2000", "US2000", "index_etf", "default", "US2000", "SPY"),
    Instrument("^DJI", "Dow Jones", "index_etf", "default", "DJI.INDX", "SPY"),
    Instrument("^GDAXI", "DAX 40", "index_etf", "default", "GDAXI.INDX", "SPY"),
    Instrument("^FTSE", "UK100", "index_etf", "default", "FTSE.INDX", "SPY"),
    Instrument("^AXJO", "ASX 200", "index_etf", "default", "AXJO.INDX", "SPY"),
    Instrument("^N225", "Nikkei 225", "index_etf", "default", "N225.INDX", "SPY"),
    Instrument("^HSI", "Hang Seng", "index_etf", "default", "HSI.INDX", "SPY"),
    Instrument("CHI50", "China A50", "index_etf", "default", "CHI50", "SPY"),
    Instrument("ESP35", "Spain 35", "index_etf", "default", "ESP35", "SPY"),
    Instrument("FRA40", "France 40", "index_etf", "default", "FRA40", "SPY"),
    Instrument("IT40", "Italy 40", "index_etf", "default", "IT40", "SPY"),
    Instrument("AAPL", "AAPL", "equity", "us", "AAPL.US", "SPY"),
    Instrument("TSLA", "TSLA", "equity", "us", "TSLA.US", "SPY"),
    Instrument("NVDA", "NVDA", "equity", "us", "NVDA.US", "SPY"),
    Instrument("MSFT", "MSFT", "equity", "us", "MSFT.US", "SPY"),
    Instrument("AMZN", "AMZN", "equity", "us", "AMZN.US", "SPY"),
    Instrument("META", "META", "equity", "us", "META.US", "SPY"),
    Instrument("GOOG", "GOOG", "equity", "us", "GOOG.US", "SPY"),
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
    # ATFX US additions so ASE matches the 50-name EODHD_WS_US_SYMBOLS book.
    Instrument("ORCL", "ORCL", "equity", "us", "ORCL.US", "SPY"),
    Instrument("QCOM", "QCOM", "equity", "us", "QCOM.US", "SPY"),
    Instrument("TSM", "TSM", "equity", "us", "TSM.US", "SPY"),
    Instrument("ADBE", "ADBE", "equity", "us", "ADBE.US", "SPY"),
    Instrument("SNOW", "SNOW", "equity", "us", "SNOW.US", "SPY"),
    Instrument("RBLX", "RBLX", "equity", "us", "RBLX.US", "SPY"),
    Instrument("SHOP", "SHOP", "equity", "us", "SHOP.US", "SPY"),
    Instrument("DASH", "DASH", "equity", "us", "DASH.US", "SPY"),
    Instrument("TWLO", "TWLO", "equity", "us", "TWLO.US", "SPY"),
    Instrument("DOCU", "DOCU", "equity", "us", "DOCU.US", "SPY"),
    Instrument("ZM", "ZM", "equity", "us", "ZM.US", "SPY"),
    Instrument("AI", "AI", "equity", "us", "AI.US", "SPY"),
    Instrument("GME", "GME", "equity", "us", "GME.US", "SPY"),
    Instrument("AMC", "AMC", "equity", "us", "AMC.US", "SPY"),
    Instrument("MRNA", "MRNA", "equity", "us", "MRNA.US", "SPY"),
    Instrument("IBM", "IBM", "equity", "us", "IBM.US", "SPY"),
    Instrument("GS", "GS", "equity", "us", "GS.US", "SPY"),
    Instrument("BAC", "BAC", "equity", "us", "BAC.US", "SPY"),
    Instrument("WFC", "WFC", "equity", "us", "WFC.US", "SPY"),
    Instrument("CVX", "CVX", "equity", "us", "CVX.US", "SPY"),
    Instrument("GE", "GE", "equity", "us", "GE.US", "SPY"),
    Instrument("CAT", "CAT", "equity", "us", "CAT.US", "SPY"),
    Instrument("NRG", "NRG", "equity", "us", "NRG.US", "SPY"),
    Instrument("BABA", "BABA", "equity", "us", "BABA.US", "SPY"),
    Instrument("NKE", "NKE", "equity", "us", "NKE.US", "SPY"),
    Instrument("GM", "GM", "equity", "us", "GM.US", "SPY"),
    Instrument("C", "C", "equity", "us", "C.US", "SPY"),
    Instrument("LLY", "LLY", "equity", "us", "LLY.US", "SPY"),
    Instrument("UAL", "UAL", "equity", "us", "UAL.US", "SPY"),
    # ATFX non-US cash equities (Xetra, Euronext Paris, HKEX).
    Instrument("SAP", "SAP", "equity", "us", "SAP.XETRA", "SPY"),
    Instrument("SIE", "SIE", "equity", "us", "SIE.XETRA", "SPY"),
    Instrument("IFX", "IFX", "equity", "us", "IFX.XETRA", "SPY"),
    Instrument("DB1", "DB1", "equity", "us", "DB1.XETRA", "SPY"),
    Instrument("BMW", "BMW", "equity", "us", "BMW.XETRA", "SPY"),
    Instrument("AIR", "AIR", "equity", "us", "AIR.PA", "SPY"),
    Instrument("LVMH", "LVMH", "equity", "us", "MC.PA", "SPY"),
    Instrument("TTEF", "TTEF", "equity", "us", "TTE.PA", "SPY"),
    Instrument("AXAF", "AXAF", "equity", "us", "CS.PA", "SPY"),
    Instrument("BNPP", "BNPP", "equity", "us", "BNP.PA", "SPY"),
    Instrument("HK0700", "HK0700", "equity", "us", "0700.HK", "SPY"),
    Instrument("HK9988", "HK9988", "equity", "us", "9988.HK", "SPY"),
    Instrument("HK1810", "HK1810", "equity", "us", "1810.HK", "SPY"),
    Instrument("HK0005", "HK0005", "equity", "us", "0005.HK", "SPY"),
    Instrument("HK0388", "HK0388", "equity", "us", "0388.HK", "SPY"),
    Instrument("GLD", "GLD", "index_etf", "default", "GLD.US", "SPY"),
    Instrument("EEM", "EEM", "index_etf", "default", "EEM.US", "SPY"),
    Instrument("GDX", "GDX", "index_etf", "default", "GDX.US", "SPY"),
    Instrument("XLE", "XLE", "index_etf", "default", "XLE.US", "SPY"),
    Instrument("USO", "USO", "index_etf", "default", "USO.US", "SPY"),
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
    keys = _lookup_keys(symbol)
    for inst in UNIVERSE:
        inst_keys = (
            _lookup_keys(inst.symbol)
            | _lookup_keys(inst.display)
            | _lookup_keys(inst.eodhd_symbol)
        )
        if keys & inst_keys:
            return inst
    return None
