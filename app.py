import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re

st.set_page_config(page_title="3-Minute Bullish BUY Scanner", page_icon="📈", layout="wide")

UNIVERSE_FILE = Path(".boom_stock_universe.csv")
UNIVERSE_META = Path(".boom_stock_universe_meta.json")

def normalize_symbol(value):
    if pd.isna(value):
        return ""
    s = str(value).strip().upper()
    s = re.sub(r"\s+", "", s).replace("$", "")
    if s.endswith(".NS"):
        return s
    if re.fullmatch(r"[A-Z0-9&._-]+", s):
        return s + ".NS"
    return ""

def detect_symbol_column(df):
    preferred = ["SYMBOL", "TICKER", "STOCK", "STOCKNAME", "NAME", "CODE"]
    mapping = {str(c).strip().upper().replace(" ", "").replace("_", ""): c for c in df.columns}
    for p in preferred:
        if p in mapping:
            return mapping[p]
    best, score = None, 0
    for c in df.columns:
        vals = df[c].dropna().astype(str).head(100)
        sc = sum(bool(re.fullmatch(r"[A-Za-z0-9&._-]+(?:\.NS)?", v.strip())) for v in vals)
        if sc > score:
            best, score = c, sc
    return best

def is_green_value(v):
    s = str(v).strip().lower()
    if not s or s in {"nan", "none", "null"}:
        return False
    green_words = ["green", "light green", "dark green", "bright green", "lime", "mint", "teal", "olive", "chartreuse", "green 1", "green 2", "green 3", "green 4", "lightgreen", "darkgreen", "brightgreen"]
    if any(w in s for w in green_words):
        return True
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 6:
            try:
                r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)
                return g > r * 1.15 and g > b * 1.05
            except Exception:
                pass
    nums = re.findall(r"\d+", s)
    if len(nums) >= 3:
        try:
            r, g, b = map(int, nums[:3])
            return g > r * 1.15 and g > b * 1.05
        except Exception:
            pass
    return False

def extract_green_stocks(df):
    symbol_col = detect_symbol_column(df)
    if symbol_col is None:
        raise ValueError("Could not identify a stock-symbol column.")
    green_columns = [c for c in df.columns if any(x in str(c).strip().lower() for x in ["color", "colour", "shade", "status", "signal", "highlight"])]
    green_rows = []
    for _, row in df.iterrows():
        symbol = normalize_symbol(row[symbol_col])
        if not symbol:
            continue
        marked_green = any(is_green_value(row[c]) for c in green_columns)
        if not marked_green:
            marked_green = any(is_green_value(value) for value in row.tolist())
        if marked_green:
            green_rows.append(symbol)
    return list(dict.fromkeys(green_rows))

def color_to_label(color):
    if color is None:
        return ""
    try:
        if color.type == "rgb" and color.rgb:
            return "#" + color.rgb[-6:].upper()
        if color.type == "indexed" and color.indexed is not None:
            return f"Indexed:{color.indexed}"
        if color.type == "theme" and color.theme is not None:
            tint = getattr(color, "tint", 0) or 0
            return f"Theme:{color.theme}" + (f" Tint:{tint:g}" if tint else "")
    except Exception:
        pass
    return ""

def fill_green_info(cell):
    fill = cell.fill
    if fill and fill.fill_type:
        fg = fill.fgColor
        label = color_to_label(fg)
        if fg.type == "rgb" and fg.rgb and is_green_value("#" + fg.rgb[-6:]):
            return True, label, "#" + fg.rgb[-6:].upper()
        if fg.type == "indexed" and fg.indexed is not None and fg.indexed in {3, 4, 35, 36, 43, 44, 50, 51}:
            indexed_hex = {3: "#00FF00", 4: "#00FFFF", 35: "#99CC00", 36: "#CCFF00", 43: "#00FF00", 44: "#33CCCC", 50: "#00CC99", 51: "#99FF99"}.get(fg.indexed, "#00FF00")
            return True, label, indexed_hex
        if fg.type == "theme" and getattr(fg, "rgb", None) and is_green_value("#" + fg.rgb[-6:]):
            return True, label, "#" + fg.rgb[-6:].upper()
    if is_green_value(cell.value):
        return True, str(cell.value).strip(), "#00B050"
    return False, "", ""

def extract_green_stocks_from_excel(uploaded_bytes, filename):
    try:
        from openpyxl import load_workbook
        import io
        wb = load_workbook(io.BytesIO(uploaded_bytes), data_only=True)
        ws = wb.active
        headers = [cell.value for cell in ws[1]]
        symbol_col = detect_symbol_column(pd.DataFrame(columns=headers))
        symbol_idx = headers.index(symbol_col) + 1 if symbol_col is not None else 1
        records = []
        for r in range(2, ws.max_row + 1):
            symbol = normalize_symbol(ws.cell(r, symbol_idx).value)
            if not symbol:
                continue
            for c in range(1, ws.max_column + 1):
                green, shade, color_hex = fill_green_info(ws.cell(r, c))
                if green:
                    records.append({"Symbol": symbol, "Green Shade": shade or "Green", "Green Color": color_hex or "#00B050"})
                    break
        return records
    except Exception:
        return []

def save_universe(records, original_filename):
    pd.DataFrame(records).drop_duplicates(subset=["Symbol"]).to_csv(UNIVERSE_FILE, index=False)
    symbols = [r["Symbol"] for r in records]
    meta = {"filename": original_filename, "count": len(symbols), "updated": pd.Timestamp.now().isoformat(), "hash": hashlib.sha256("|".join(symbols).encode()).hexdigest()}
    UNIVERSE_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")

def load_saved_universe():
    if not UNIVERSE_FILE.exists():
        return []
    try:
        df = pd.read_csv(UNIVERSE_FILE)
        if "Symbol" not in df.columns:
            return []
        if "Green Shade" not in df.columns:
            df["Green Shade"] = "Green"
        if "Green Color" not in df.columns:
            df["Green Color"] = "#00B050"
        return df[["Symbol", "Green Shade", "Green Color"]].fillna("").to_dict("records")
    except Exception:
        return []

def load_meta():
    if not UNIVERSE_META.exists():
        return {}
    try:
        return json.loads(UNIVERSE_META.read_text(encoding="utf-8"))
    except Exception:
        return {}

st.title("📈 3-Minute Bullish BUY Scanner")
st.subheader("🟢 Green-Stock Universe")

with st.expander("Upload / Replace Stock File", expanded=not bool(load_saved_universe())):
    uploaded = st.file_uploader("Upload your Excel/CSV stock file. Only green-shade stocks will be scanned.", type=["xlsx", "xls", "csv"], key="stock_universe_upload")
    if uploaded is not None:
        try:
            raw_bytes = uploaded.getvalue()
            if uploaded.name.lower().endswith((".xlsx", ".xls")):
                records = extract_green_stocks_from_excel(raw_bytes, uploaded.name)
                if not records:
                    import io
                    df_upload = pd.read_excel(io.BytesIO(raw_bytes))
                    records = [{"Symbol": s, "Green Shade": "Green", "Green Color": "#00B050"} for s in extract_green_stocks(df_upload)]
            else:
                import io
                df_upload = pd.read_csv(io.BytesIO(raw_bytes))
                records = [{"Symbol": s, "Green Shade": "Green", "Green Color": "#00B050"} for s in extract_green_stocks(df_upload)]
            if records:
                save_universe(records, uploaded.name)
                st.success(f"Saved {len(records)} green-shade stocks from {uploaded.name}. This list will remain active until the next upload.")
            else:
                st.error("No green-shade stocks were detected. For Excel, make sure the stock rows/cells are actually filled with green.")
        except Exception as e:
            st.error(f"Could not process the file: {e}")

saved_universe = load_saved_universe()
saved_symbols = [r["Symbol"] for r in saved_universe]
meta = load_meta()

if saved_universe:
    st.success(f"ACTIVE UNIVERSE: {len(saved_universe)} green stocks | File: {meta.get('filename','saved file')} | Next upload will replace this list.")
    universe_df = pd.DataFrame({"Green Stocks": [r["Symbol"].replace(".NS", "") for r in saved_universe]})
    color_map = {r["Symbol"].replace(".NS", ""): r.get("Green Color", "#00B050") or "#00B050" for r in saved_universe}
    def color_universe_rows(row):
        symbol = str(row["Green Stocks"])
        color = color_map.get(symbol, "#00B050")
        return [f"background-color: {color}; font-weight: 700"]
    st.dataframe(universe_df.style.apply(color_universe_rows, axis=1), use_container_width=True, hide_index=True)
else:
    st.warning("No stock file is saved yet. Upload your file to create the green-stock scanning universe.")

with st.sidebar:
    st.header("Scanner Settings")
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
    st.subheader("ADX Confirmation")
    adx_period = st.number_input("ADX / DI period", 5, 50, 14)
    adx_min = st.slider("Minimum ADX", 10.0, 40.0, 20.0, 1.0)
    require_adx_rising = st.checkbox("Require ADX rising", value=True)
    require_plus_di = st.checkbox("Require +DI > -DI", value=True)
    st.subheader("Backtest")
    bt_target = st.number_input("Backtest target %", 0.2, 10.0, 1.0, 0.1)
    bt_stop = st.number_input("Backtest stop %", 0.2, 5.0, 0.5, 0.1)
    bt_bars = st.number_input("Bars after signal", 1, 50, 10)

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
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    return df.resample("3min", origin="start_day", offset="15min", label="left", closed="left").agg(agg).dropna(subset=["Open", "High", "Low", "Close"])

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

def calculate_adx(df, period=None):
    period = int(period or adx_period)
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_smoothed = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    minus_smoothed = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    df["+DI"] = 100 * plus_smoothed / atr.replace(0, np.nan)
    df["-DI"] = 100 * minus_smoothed / atr.replace(0, np.nan)
    dx = 100 * (df["+DI"] - df["-DI"]).abs() / (df["+DI"] + df["-DI"]).replace(0, np.nan)
    df["ADX"] = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return df

def calculate_o2l(daily):
    daily = daily.copy()
    daily["O2L%"] = ((daily["Low"] - daily["Open"]) / daily["Open"]) * 100
    return daily

def get_daily_data(symbol):
    try:
        return clean_columns(yf.download(symbol, period="15d", interval="1d", auto_adjust=False, progress=False, threads=False))
    except Exception:
        return pd.DataFrame()

def get_intraday_data(symbol):
    try:
        x = clean_columns(yf.download(symbol, period="7d", interval="1m", auto_adjust=False, progress=False, threads=False))
        return resample_to_3min(x) if not x.empty else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def identify_long_green_candle(row):
    candle_range = float(row["High"] - row["Low"])
    body = float(row["Close"] - row["Open"])
    return candle_range > 0 and body > 0 and (body / candle_range) * 100 >= min_body_percent

def kijun_crosses_through_candle(row):
    k = row.get("Kijun", np.nan)
    if pd.isna(k):
        return False
    tol = abs(float(k)) * kijun_tolerance / 100.0
    return float(row["Low"]) - tol <= float(k) <= float(row["High"]) + tol

def find_signal(intraday):
    if intraday.empty:
        return None
    df = calculate_adx(calculate_ichimoku(calculate_alligator(intraday)))
    for pos in range(len(df) - 1, -1, -1):
        row = df.iloc[pos]
        ts = df.index[pos]
        if any(pd.isna(row[c]) for c in ["Kijun", "Jaw", "Teeth", "Lips", "ADX", "+DI", "-DI"]):
            continue
        adx_ok = float(row["ADX"]) >= float(adx_min)
        if require_adx_rising:
            if pos == 0 or pd.isna(df["ADX"].iloc[pos - 1]) or float(row["ADX"]) <= float(df["ADX"].iloc[pos - 1]):
                adx_ok = False
        if require_plus_di and float(row["+DI"]) <= float(row["-DI"]):
            adx_ok = False
        if adx_ok and bool(row["Alligator_Bullish"]) and identify_long_green_candle(row) and kijun_crosses_through_candle(row) and float(row["Close"]) > float(row["Kijun"]):
            rng = float(row["High"] - row["Low"])
            return {
                "Signal Time": ts, "Signal Price": float(row["Close"]),
                "Open": float(row["Open"]), "High": float(row["High"]), "Low": float(row["Low"]), "Close": float(row["Close"]),
                "Kijun": float(row["Kijun"]), "Jaw": float(row["Jaw"]), "Teeth": float(row["Teeth"]), "Lips": float(row["Lips"]),
                "ADX": float(row["ADX"]), "Plus_DI": float(row["+DI"]), "Minus_DI": float(row["-DI"]),
                "Green Body %": ((float(row["Close"] - row["Open"]) / rng) * 100 if rng else 0),
                "_signal_pos": pos
            }
    return None

def evaluate_signal_outcome(intraday, signal):
    try:
        pos = int(signal["_signal_pos"])
        entry = float(signal["Signal Price"])
        future = intraday.iloc[pos + 1:pos + 1 + int(bt_bars)]
        if future.empty:
            return "No future data", np.nan
        target = entry * (1 + float(bt_target) / 100)
        stop = entry * (1 - float(bt_stop) / 100)
        for _, bar in future.iterrows():
            hit_target = float(bar["High"]) >= target
            hit_stop = float(bar["Low"]) <= stop
            if hit_target and hit_stop:
                return "Ambiguous", np.nan
            if hit_target:
                return "Target", float(bt_target)
            if hit_stop:
                return "Stop", -float(bt_stop)
        return "Neither", 0.0
    except Exception:
        return "Unknown", np.nan

def scan_stock(symbol, green_shade="Green", green_color="#00B050"):
    try:
        daily = calculate_o2l(get_daily_data(symbol))
        if daily.empty or len(daily) < 5:
            return None
        previous_three = daily.iloc[-4:-1]
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
            "Symbol": symbol.replace(".NS", ""), "_Green Color": green_color,
            "Current Price": current_price, "Previous Close": previous_close,
            "Day-1 O2L%": float(previous_three["O2L%"].iloc[-1]), "Day-2 O2L%": float(previous_three["O2L%"].iloc[-2]), "Day-3 O2L%": float(previous_three["O2L%"].iloc[-3]),
            "Signal Time": signal["Signal Time"], "Signal Price": signal["Signal Price"], "Kijun": signal["Kijun"],
            "ADX": signal["ADX"], "+DI": signal["Plus_DI"], "-DI": signal["Minus_DI"],
            "Green Body %": signal["Green Body %"], "Alligator": "Bullish"
        }, signal
    except Exception:
        return None

st.info("Only the saved green-shade stock universe is scanned. The saved file/list is retained until a new upload replaces it.")

if st.button("🚀 RUN BUY SCAN", type="primary", use_container_width=True):
    if not saved_symbols:
        st.error("Upload a stock file containing green-shade stocks first.")
    else:
        results, details = [], []
        progress, status = st.progress(0), st.empty()
        total = len(saved_symbols)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(scan_stock, r["Symbol"], r.get("Green Shade", "Green"), r.get("Green Color", "#00B050")): r for r in saved_universe}
            for i, future in enumerate(as_completed(futures), 1):
                record = futures[future]
                status.write(f"Scanning green stock {record['Symbol'].replace('.NS', '')} — {i}/{total}")
                try:
                    result = future.result()
                    if result:
                        row, signal = result
                        outcome, outcome_pct = evaluate_signal_outcome(get_intraday_data(record["Symbol"]), signal)
                        row["Backtest Outcome"] = outcome
                        row["Backtest Return %"] = outcome_pct
                        results.append(row)
                        details.append((row["Symbol"], signal, outcome, outcome_pct))
                except Exception:
                    pass
                progress.progress(i / total)
        status.success(f"Scan complete. Scanned only {total} saved green-shade stocks. {len(results)} BUY candidate(s) found.")
        if results:
            result_df = pd.DataFrame(results).sort_values(["Signal Time", "Green Body %"], ascending=[False, False])
            st.subheader(f"BUY Candidates ({len(result_df)})")
            display_df = result_df.drop(columns=["_Green Color"], errors="ignore").copy()
            for col in ["Current Price", "Previous Close", "Signal Price", "Kijun", "ADX", "+DI", "-DI", "Backtest Return %"]:
                if col in display_df.columns:
                    display_df[col] = pd.to_numeric(display_df[col], errors="coerce").map(lambda x: f"{x:.2f}" if pd.notna(x) else "")
            result_color_map = {str(row["Symbol"]): str(row.get("_Green Color", "#00B050") or "#00B050") for _, row in result_df.iterrows()}
            def color_result_rows(row):
                symbol = str(row["Symbol"])
                color = result_color_map.get(symbol, "#00B050")
                return [f"background-color: {color}; font-weight: 700" if col == "Symbol" else "" for col in display_df.columns]
            st.dataframe(display_df.style.apply(color_result_rows, axis=1), use_container_width=True, hide_index=True)
            st.download_button("⬇️ Download CSV", display_df.to_csv(index=False).encode("utf-8"), "buy_scan_results.csv", "text/csv")
            bt = result_df[result_df["Backtest Outcome"].isin(["Target", "Stop", "Neither", "Ambiguous"])].copy()
            if not bt.empty:
                st.subheader("📊 Current Scan Backtest Check")
                target_count = int((bt["Backtest Outcome"] == "Target").sum())
                stop_count = int((bt["Backtest Outcome"] == "Stop").sum())
                evaluated = target_count + stop_count
                win_rate = (target_count / evaluated * 100) if evaluated else np.nan
                x1, x2, x3 = st.columns(3)
                x1.metric("Target Hits", target_count)
                x2.metric("Stop Hits", stop_count)
                x3.metric("Win Rate", f"{win_rate:.2f}%" if pd.notna(win_rate) else "N/A")
                st.caption(f"Outcome test uses target {bt_target:.2f}%, stop {bt_stop:.2f}%, and the next {int(bt_bars)} completed 3-minute candles. This is a forward check of the signals found in the current scan, not a full historical backtest.")
            for symbol, signal, outcome, outcome_pct in details:
                with st.expander(f"🟢 {symbol} — {signal['Signal Time']}"):
                    a, b, c, d = st.columns(4)
                    a.metric("Signal Price", f"{signal['Signal Price']:.2f}")
                    b.metric("Kijun", f"{signal['Kijun']:.2f}")
                    c.metric("ADX", f"{signal['ADX']:.2f}")
                    d.metric("Backtest", outcome)
                    st.write({k: round(v, 2) if isinstance(v, (int, float, np.floating)) else v for k, v in signal.items() if not k.startswith("_")})
        else:
            st.warning("No saved green stocks matched all BUY conditions.")

with st.expander("📐 Formulas / Definitions"):
    st.code("""O2L% = ((Low - Open) / Open) * 100
Alligator bullish = Lips > Teeth > Jaw
Kijun = (Highest High over 26 + Lowest Low over 26) / 2
Long green candle = Close > Open and body/range >= selected threshold
Kijun crossing candle = Low <= Kijun <= High
Signal Close > Kijun
Current Price > Previous Daily Close
ADX confirmation = ADX >= selected minimum, optionally rising, with +DI > -DI
Backtest = target/stop outcome over selected number of subsequent 3-minute candles""")

st.caption("Yahoo Finance/yfinance data. Screening signals are not guarantees of future price movement.")
