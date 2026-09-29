import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="3-Minute Bullish BUY Scanner", page_icon="📈", layout="wide")

NIFTY_50 = [
"ADANIENT.NS","ADANIPORTS.NS","APOLLOHOSP.NS","ASIANPAINT.NS","AXISBANK.NS",
"BAJAJ-AUTO.NS","BAJFINANCE.NS","BAJAJFINSV.NS","BEL.NS","BHARTIARTL.NS",
"CIPLA.NS","COALINDIA.NS","DRREDDY.NS","EICHERMOT.NS","ETERNAL.NS",
"GRASIM.NS","HCLTECH.NS","HDFCBANK.NS","HDFCLIFE.NS","HEROMOTOCO.NS",
"HINDALCO.NS","HINDUNILVR.NS","ICICIBANK.NS","INDUSINDBK.NS","INFY.NS",
"ITC.NS","JIOFIN.NS","JSWSTEEL.NS","KOTAKBANK.NS","LT.NS","M&M.NS",
"MARUTI.NS","MAXHEALTH.NS","NESTLEIND.NS","NTPC.NS","ONGC.NS","POWERGRID.NS",
"RELIANCE.NS","SBILIFE.NS","SBIN.NS","SHRIRAMFIN.NS","SUNPHARMA.NS",
"TATACONSUM.NS","TATAMOTORS.NS","TATASTEEL.NS","TCS.NS","TECHM.NS",
"TITAN.NS","TRENT.NS","ULTRACEMCO.NS","WIPRO.NS"
]

NIFTY_100_EXTRA = [
"ABB.NS","ADANIENSOL.NS","ADANIGREEN.NS","ADANIPOWER.NS","ALKEM.NS","AMBER.NS",
"AMBUJACEM.NS","AUROPHARMA.NS","BAJAJHLDNG.NS","BANKBARODA.NS","BANKINDIA.NS",
"BDL.NS","BOSCHLTD.NS","BPCL.NS","BRITANNIA.NS","BSE.NS","CANBK.NS","CGPOWER.NS",
"CHOLAFIN.NS","COLPAL.NS","CONCOR.NS","CUMMINSIND.NS","DABUR.NS","DIVISLAB.NS",
"DIXON.NS","DLF.NS","DMART.NS","EXIDEIND.NS","FEDERALBNK.NS","GAIL.NS","GLENMARK.NS",
"GODREJCP.NS","GODREJPROP.NS","HAL.NS","HAVELLS.NS","HINDPETRO.NS","ICICIGI.NS",
"ICICIPRULI.NS","IDFCFIRSTB.NS","INDHOTEL.NS","INDIGO.NS","INDUSTOWER.NS",
"IOC.NS","IRCTC.NS","IREDA.NS","IRFC.NS","JINDALSTEL.NS","JUBLFOOD.NS",
"LICI.NS","LODHA.NS","LUPIN.NS","MARICO.NS","MCX.NS","MFSL.NS","MOTHERSON.NS",
"MPHASIS.NS","MUTHOOTFIN.NS","NHPC.NS","NMDC.NS","OFSS.NS","OIL.NS","PAGEIND.NS",
"PATANJALI.NS","PAYTM.NS","PERSISTENT.NS","PETRONET.NS","PFC.NS","PIDILITIND.NS",
"PIIND.NS","PNB.NS","POLYCAB.NS","RECLTD.NS","SAIL.NS","SBICARD.NS","SHREECEM.NS",
"SIEMENS.NS","SRF.NS","SUPREMEIND.NS","SUZLON.NS","TATAPOWER.NS","TATATECH.NS",
"TORNTPHARM.NS","TORNTPOWER.NS","TVSMOTOR.NS","UNIONBANK.NS","UNITEDSPIRITS.NS",
"VBL.NS","VEDL.NS","VOLTAS.NS","YESBANK.NS","ZYDUSLIFE.NS"
]

NIFTY_200_EXTRA = [
"AARTIIND.NS","ABCAPITAL.NS","ABFRL.NS","ACC.NS","ACE.NS","AFFLE.NS","AJANTAPHARM.NS",
"APARINDS.NS","APLAPOLLO.NS","ASHOKLEY.NS","ASTRAL.NS","ATGL.NS","AUBANK.NS",
"AWL.NS","BALKRISIND.NS","BANDHANBNK.NS","BANKNIFTY.NS","BATAINDIA.NS","BHARATFORG.NS",
"BIOCON.NS","BLUESTARCO.NS","BHEL.NS","BSOFT.NS","CAMS.NS","CARBORUNIV.NS","CDSL.NS",
"CEATLTD.NS","CENTRALBK.NS","CENTURYPLY.NS","CESC.NS","CROMPTON.NS","CYIENT.NS",
"DALBHARAT.NS","DEEPAKNTR.NS","DELHIVERY.NS","DEVYANI.NS","EMAMILTD.NS","ENDURANCE.NS",
"ESCORTS.NS","FACT.NS","FINEORG.NS","FLUOROCHEM.NS","FORTIS.NS","GMRINFRA.NS",
"GNFC.NS","GODREJIND.NS","GRANULES.NS","GRAPHITE.NS","GUJGASLTD.NS","HDFCAMC.NS",
"HINDCOPPER.NS","HINDZINC.NS","HOMEFIRST.NS","HUDCO.NS","IDBI.NS","IEX.NS","IGL.NS",
"INDIAMART.NS","INDIANB.NS","INDIANENERGY.NS","INDUSTOWER.NS","INOXWIND.NS","INTELLECT.NS",
"IPCALAB.NS","JBCHEPHARM.NS","JSL.NS","JSWENERGY.NS","JUBLINGREA.NS","KALYANKJIL.NS",
"KEI.NS","KPITTECH.NS","LAURUSLABS.NS","LICHSGFIN.NS","LTIM.NS","LTF.NS","MANAPPURAM.NS",
"MAZDOCK.NS","MEDANTA.NS","METROPOLIS.NS","MGL.NS","MINDACORP.NS","MOTILALOFS.NS",
"MRF.NS","NATIONALUM.NS","NAVINFLUOR.NS","NBCC.NS","NCC.NS","NEWGEN.NS","NH.NS",
"NIACL.NS","NUVAMA.NS","OBEROIRLTY.NS","OLECTRA.NS","PAYTM.NS","PCBL.NS","PEL.NS",
"PHOENIXLTD.NS","PPLPHARMA.NS","PRESTIGE.NS","RADICO.NS","RAILTEL.NS","RAMCOCEM.NS",
"RBLBANK.NS","RITES.NS","ROUTE.NS","SAREGAMA.NS","SCHAEFFLER.NS","SHRIRAMFIN.NS",
"SJVN.NS","SONACOMS.NS","SOBHA.NS","SOLARINDS.NS","SONATSOFTW.NS","STARHEALTH.NS",
"SUMICHEM.NS","SUNDARMFIN.NS","SUNTV.NS","SUPRAJIT.NS","TATAELXSI.NS","TATACHEMICALS.NS",
"THERMAX.NS","TIMKEN.NS","TITAGARH.NS","TRIDENT.NS","TRIVENI.NS","UCOBANK.NS",
"UJJIVANSFB.NS","UNOMINDA.NS","UPL.NS","USHAMART.NS","VGUARD.NS","WELCORP.NS",
"WHIRLPOOL.NS","WOCKPHARMA.NS","ZEEL.NS","ZENSARTECH.NS"
]

with st.sidebar:
    st.header("Scanner Settings")
    index_name = st.selectbox("Universe", ["NIFTY 200", "NIFTY 100", "NIFTY 50"])
    workers = st.slider("Parallel workers", 1, 10, 5)
    st.subheader("Alligator")
    jaw_period = st.number_input("Jaw period", 2, 50, 13)
    jaw_shift = st.number_input("Jaw shift", 0, 20, 8)
    teeth_period = st.number_input("Teeth period", 2, 50, 8)
    teeth_shift = st.number_input("Teeth shift", 0, 20, 5)
    lips_period = st.number_input("Lips period", 2, 50, 5)
    lips_shift = st.number_input("Lips shift", 0, 20, 3)
    st.subheader("Ichimoku")
    conversion_period = st.number_input("Conversion / Tenkan", 2, 100, 9)
    base_period = st.number_input("Base / Kijun", 2, 100, 26)
    span_b_period = st.number_input("Span B", 2, 150, 52)
    min_body_percent = st.slider("Minimum long green candle body %", 20, 95, 60)
    kijun_tolerance = st.slider("Kijun candle tolerance %", 0.0, 2.0, 0.0, 0.1)

NIFTY_100 = list(dict.fromkeys(NIFTY_50 + NIFTY_100_EXTRA))
NIFTY_200 = list(dict.fromkeys(NIFTY_100 + NIFTY_200_EXTRA))
UNIVERSE = {"NIFTY 50": NIFTY_50, "NIFTY 100": NIFTY_100, "NIFTY 200": NIFTY_200}[index_name]

def clean_columns(df):
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] if isinstance(c, tuple) else c for c in out.columns]
    out.columns = [str(c).strip().title() for c in out.columns]
    return out

def resample_to_3min(df):
    df = clean_columns(df)
    if df.empty:
        return df
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    agg = {"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}
    return df.resample("3min", origin="start_day", offset="15min", label="left", closed="left").agg(agg).dropna(subset=["Open","High","Low","Close"])

def smma(series, period):
    return series.ewm(alpha=1 / float(period), adjust=False, min_periods=period).mean()

def calculate_alligator(df):
    median = (df["High"] + df["Low"]) / 2.0
    df = df.copy()
    df["Jaw"] = smma(median, jaw_period)
    df["Teeth"] = smma(median, teeth_period)
    df["Lips"] = smma(median, lips_period)
    df["Alligator_Bullish"] = (df["Lips"] > df["Teeth"]) & (df["Teeth"] > df["Jaw"])
    return df

def calculate_ichimoku(df):
    df = df.copy()
    high, low = df["High"], df["Low"]
    df["Tenkan"] = (high.rolling(conversion_period).max() + low.rolling(conversion_period).min()) / 2
    df["Kijun"] = (high.rolling(base_period).max() + low.rolling(base_period).min()) / 2
    df["Span_B"] = (high.rolling(span_b_period).max() + low.rolling(span_b_period).min()) / 2
    return df

def calculate_o2l(daily):
    daily = daily.copy()
    daily["O2L%"] = ((daily["Low"] - daily["Open"]) / daily["Open"]) * 100
    return daily

def get_daily_data(symbol):
    try:
        x = yf.download(symbol, period="15d", interval="1d", auto_adjust=False, progress=False, threads=False)
        return clean_columns(x)
    except Exception:
        return pd.DataFrame()

def get_intraday_data(symbol):
    try:
        x = yf.download(symbol, period="7d", interval="1m", auto_adjust=False, progress=False, threads=False)
        x = clean_columns(x)
        if x.empty:
            return pd.DataFrame()
        return resample_to_3min(x)
    except Exception:
        return pd.DataFrame()

def identify_long_green_candle(row):
    candle_range = float(row["High"] - row["Low"])
    body = float(row["Close"] - row["Open"])
    if candle_range <= 0:
        return False
    return body > 0 and (body / candle_range) * 100 >= min_body_percent

def kijun_crosses_through_candle(row):
    k = row.get("Kijun", np.nan)
    if pd.isna(k):
        return False
    tol = abs(float(k)) * kijun_tolerance / 100.0
    return float(row["Low"]) - tol <= float(k) <= float(row["High"]) + tol

def find_signal(intraday):
    if intraday.empty:
        return None
    df = calculate_alligator(intraday)
    df = calculate_ichimoku(df)
    for ts, row in df.iloc[::-1].iterrows():
        if pd.isna(row["Kijun"]) or pd.isna(row["Jaw"]) or pd.isna(row["Teeth"]) or pd.isna(row["Lips"]):
            continue
        if (
            bool(row["Alligator_Bullish"])
            and identify_long_green_candle(row)
            and kijun_crosses_through_candle(row)
            and float(row["Close"]) > float(row["Kijun"])
        ):
            candle_range = float(row["High"] - row["Low"])
            body_pct = (float(row["Close"] - row["Open"]) / candle_range) * 100 if candle_range else 0
            return {
                "Signal Time": ts, "Signal Price": float(row["Close"]),
                "Open": float(row["Open"]), "High": float(row["High"]),
                "Low": float(row["Low"]), "Close": float(row["Close"]),
                "Kijun": float(row["Kijun"]), "Jaw": float(row["Jaw"]),
                "Teeth": float(row["Teeth"]), "Lips": float(row["Lips"]),
                "Green Body %": body_pct
            }
    return None

def scan_stock(symbol):
    try:
        daily = calculate_o2l(get_daily_data(symbol))
        if daily.empty or len(daily) < 5:
            return None
        previous_three = daily.iloc[-4:-1].copy()
        if len(previous_three) != 3 or not (previous_three["O2L%"] < -1.0).all():
            return None
        previous_close = float(daily["Close"].iloc[-2])
        intraday = get_intraday_data(symbol)
        if intraday.empty:
            return None
        current_price = float(intraday["Close"].dropna().iloc[-1])
        if current_price <= previous_close:
            return None
        signal = find_signal(intraday)
        if not signal:
            return None
        return {
            "Symbol": symbol.replace(".NS", ""),
            "Current Price": current_price,
            "Previous Close": previous_close,
            "Current > Prev Close": True,
            "Day-1 O2L%": float(previous_three["O2L%"].iloc[-1]),
            "Day-2 O2L%": float(previous_three["O2L%"].iloc[-2]),
            "Day-3 O2L%": float(previous_three["O2L%"].iloc[-3]),
            "O2L All < -1%": True,
            "Signal Time": signal["Signal Time"],
            "Signal Price": signal["Signal Price"],
            "Kijun": signal["Kijun"],
            "Green Body %": signal["Green Body %"],
            "Alligator": "Bullish"
        }, signal
    except Exception:
        return None

st.title("📈 3-Minute Bullish BUY Scanner")
st.caption("Alligator bullish + Kijun/Base crossing a long green candle + price above Kijun + previous 3 sessions O2L% < -1% + current price above previous close.")

st.info("3-minute candles are constructed from Yahoo Finance 1-minute OHLCV data. Kijun crossing is defined as Kijun lying inside the candle High-Low range. The previous three completed daily sessions are used for O2L.")

st.markdown("### BUY Conditions")
st.markdown("""
1. 3-minute timeframe
2. Alligator bullish: Lips > Teeth > Jaw
3. Long green candle: Close > Open and body/range >= selected threshold
4. Ichimoku Base/Kijun crosses the candle: Low <= Kijun <= High
5. Signal candle closes above Kijun
6. Previous 3 completed trading days: ((Low - Open) / Open) × 100 < -1% for all 3 days
7. Current price > previous daily close
""")

if st.button("🚀 RUN BUY SCAN", type="primary", use_container_width=True):
    results, details = [], []
    progress, status = st.progress(0), st.empty()
    total = len(UNIVERSE)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(scan_stock, s): s for s in UNIVERSE}
        for i, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            status.write(f"Scanning {symbol} — {i}/{total}")
            try:
                result = future.result()
                if result:
                    row, signal = result
                    results.append(row)
                    details.append((row["Symbol"], signal))
            except Exception:
                pass
            progress.progress(i / total)

    status.success(f"Scan complete. {len(results)} BUY candidate(s) found.")

    if results:
        result_df = pd.DataFrame(results).sort_values(["Signal Time", "Green Body %"], ascending=[False, False])
        st.subheader(f"BUY Candidates ({len(result_df)})")
        st.dataframe(result_df, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download CSV", result_df.to_csv(index=False).encode("utf-8"), "buy_scan_results.csv", "text/csv")
        st.subheader("Signal Details")
        for symbol, signal in details:
            with st.expander(f"🟢 {symbol} — {signal['Signal Time']}"):
                a, b, c = st.columns(3)
                a.metric("Signal Price", f"{signal['Signal Price']:.2f}")
                b.metric("Kijun", f"{signal['Kijun']:.2f}")
                c.metric("Green Body", f"{signal['Green Body %']:.2f}%")
                st.write({k: round(v, 2) if isinstance(v, (int, float, np.floating)) else v for k, v in signal.items()})
    else:
        st.warning("No stocks matched all conditions.")

with st.expander("📐 Formulas / Definitions"):
    st.code("""O2L% = ((Low - Open) / Open) * 100

Alligator:
Median Price = (High + Low) / 2
Jaw   = SMMA(Median Price, 13)
Teeth = SMMA(Median Price, 8)
Lips  = SMMA(Median Price, 5)
Bullish = Lips > Teeth > Jaw

Ichimoku Base / Kijun:
Kijun = (Highest High over 26 + Lowest Low over 26) / 2

Long green candle:
Close > Open
Body % = (Close - Open) / (High - Low) * 100
Body % >= selected threshold

Kijun crossing candle:
Low <= Kijun <= High

Price confirmation:
Signal Close > Kijun
Current Price > Previous Daily Close""")

st.caption("Data source: Yahoo Finance via yfinance. Screening signals are not guarantees of future price movement. Verify live quotes, candle completion, liquidity, slippage, and broker execution before using real-money orders.")
