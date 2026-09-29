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

# ============================================================
# PERSISTENT UPLOADED STOCK UNIVERSE
# ============================================================
# The uploaded file is saved in Streamlit's persistent app folder.
# It remains the active universe until the user uploads another file.
UNIVERSE_FILE = Path(".boom_stock_universe.csv")
UNIVERSE_META = Path(".boom_stock_universe_meta.json")

def normalize_symbol(value):
    if pd.isna(value):
        return ""
    s = str(value).strip().upper()
    s = re.sub(r"\s+", "", s)
    s = s.replace("$", "")
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
    # Prefer a column containing many NSE-like ticker strings.
    best, score = None, 0
    for c in df.columns:
        vals = df[c].dropna().astype(str).head(100)
        sc = sum(bool(re.fullmatch(r"[A-Za-z0-9&._-]+(?:\\.NS)?", v.strip())) for v in vals)
        if sc > score:
            best, score = c, sc
    return best

def is_green_value(v):
    """Recognize common Excel/CSV green shade names and RGB/hex values."""
    s = str(v).strip().lower()
    if not s or s in {"nan", "none", "null"}:
        return False
    green_words = [
        "green", "light green", "dark green", "bright green",
        "lime", "mint", "teal", "olive", "chartreuse",
        "green 1", "green 2", "green 3", "green 4",
        "lightgreen", "darkgreen", "brightgreen"
    ]
    if any(w in s for w in green_words):
        return True
    # Common Excel/theme green color names / hex values.
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 6:
            try:
                r, g, b = int(h[:2],16), int(h[2:4],16), int(h[4:],16)
                return g > r * 1.15 and g > b * 1.05
            except Exception:
                pass
    # rgb(r,g,b), (r,g,b), or comma-separated RGB.
    nums = re.findall(r"\d+", s)
    if len(nums) >= 3:
        try:
            r, g, b = map(int, nums[:3])
            return g > r * 1.15 and g > b * 1.05
        except Exception:
            pass
    return False

def read_uploaded_file(uploaded):
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded)
    raise ValueError("Upload CSV or Excel file.")

def extract_green_stocks(df):
    """Extract symbols whose row or symbol cell is marked green."""
    symbol_col = detect_symbol_column(df)
    if symbol_col is None:
        raise ValueError("Could not identify a stock-symbol column.")

    green_rows = []
    green_columns = []

    # If an explicit color/status column exists, use it.
    for c in df.columns:
        cname = str(c).strip().lower()
        if any(x in cname for x in ["color", "colour", "shade", "status", "signal", "highlight"]):
            green_columns.append(c)

    for idx, row in df.iterrows():
        symbol = normalize_symbol(row[symbol_col])
        if not symbol:
            continue

        marked_green = False

        # Green in any explicit text/color/status cell.
        for c in green_columns:
            if is_green_value(row[c]):
                marked_green = True
                break

        # Also accept a green word in any row cell.
        if not marked_green:
            for value in row.tolist():
                if is_green_value(value):
                    marked_green = True
                    break

        if marked_green:
            green_rows.append(symbol)

    # CSV files cannot retain Excel fill colors. For Excel, inspect actual cell
    # fills when openpyxl is available.
    if name_is_excel := str(getattr(df, "_source_filename", "")).lower().endswith((".xlsx", ".xls")):
        pass

    return list(dict.fromkeys(green_rows))

def extract_green_stocks_from_excel(uploaded_bytes, filename):
    """Read actual Excel cell fill colors, including green shades."""
    try:
        from openpyxl import load_workbook
        import io
        wb = load_workbook(io.BytesIO(uploaded_bytes), data_only=True)
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        symbol_col = detect_symbol_column(pd.DataFrame(columns=headers))
        if symbol_col is None:
            # Common fallback: first column.
            symbol_idx = 1
        else:
            symbol_idx = headers.index(symbol_col) + 1

        symbols = []
        for r in range(2, ws.max_row + 1):
            raw = ws.cell(r, symbol_idx).value
            symbol = normalize_symbol(raw)
            if not symbol:
                continue

            row_green = False
            for c in range(1, ws.max_column + 1):
                cell = ws.cell(r, c)
                fill = cell.fill
                if fill and fill.fill_type:
                    fg = fill.fgColor
                    candidates = []
                    if fg.type == "rgb" and fg.rgb:
                        candidates.append(fg.rgb[-6:])
                    elif fg.type == "indexed" and fg.indexed is not None:
                        # Common Excel indexed greens.
                        if fg.indexed in {3, 4, 35, 36, 43, 44, 50, 51}:
                            row_green = True
                    elif fg.type == "theme":
                        # Theme colors are not reliably named; inspect tint plus
                        # the cell's displayed RGB when possible.
                        try:
                            rgb = fg.rgb
                            if rgb:
                                candidates.append(rgb[-6:])
                        except Exception:
                            pass
                    for h in candidates:
                        if is_green_value("#" + h):
                            row_green = True
                            break
                if row_green:
                    break

                if is_green_value(cell.value):
                    row_green = True
                    break

            if row_green:
                symbols.append(symbol)

        return list(dict.fromkeys(symbols))
    except Exception:
        return []

def save_universe(symbols, original_filename):
    pd.DataFrame({"Symbol": symbols}).to_csv(UNIVERSE_FILE, index=False)
    meta = {
        "filename": original_filename,
        "count": len(symbols),
        "updated": pd.Timestamp.now().isoformat(),
        "hash": hashlib.sha256("|".join(symbols).encode()).hexdigest()
    }
    UNIVERSE_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")

def load_saved_universe():
    if not UNIVERSE_FILE.exists():
        return []
    try:
        df = pd.read_csv(UNIVERSE_FILE)
        return [x for x in df["Symbol"].dropna().astype(str).tolist() if x]
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
    uploaded = st.file_uploader(
        "Upload your Excel/CSV stock file. Only green-shade stocks will be scanned.",
        type=["xlsx", "xls", "csv"],
        key="stock_universe_upload"
    )
    if uploaded is not None:
        try:
            raw_bytes = uploaded.getvalue()
            if uploaded.name.lower().endswith((".xlsx", ".xls")):
                symbols = extract_green_stocks_from_excel(raw_bytes, uploaded.name)
                if not symbols:
                    # Fallback to textual/status interpretation.
                    import io
                    df_upload = pd.read_excel(io.BytesIO(raw_bytes))
                    symbols = extract_green_stocks(df_upload)
            else:
                import io
                df_upload = pd.read_csv(io.BytesIO(raw_bytes))
                symbols = extract_green_stocks(df_upload)

            if symbols:
                save_universe(symbols, uploaded.name)
                st.success(f"Saved {len(symbols)} green-shade stocks from {uploaded.name}. This list will remain active until the next upload.")
            else:
                st.error("No green-shade stocks were detected. For Excel, make sure the stock rows/cells are actually filled with green.")
        except Exception as e:
            st.error(f"Could not process the file: {e}")

saved_symbols = load_saved_universe()
meta = load_meta()

if saved_symbols:
    st.success(f"ACTIVE UNIVERSE: {len(saved_symbols)} green stocks | File: {meta.get('filename','saved file')} | Next upload will replace this list.")
    st.dataframe(pd.DataFrame({"Green Stocks": [s.replace(".NS","") for s in saved_symbols]}), use_container_width=True, hide_index=True)
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
    df = calculate_ichimoku(calculate_alligator(intraday))
    for ts, row in df.iloc[::-1].iterrows():
        if pd.isna(row["Kijun"]) or pd.isna(row["Jaw"]) or pd.isna(row["Teeth"]) or pd.isna(row["Lips"]):
            continue
        if bool(row["Alligator_Bullish"]) and identify_long_green_candle(row) and kijun_crosses_through_candle(row) and float(row["Close"]) > float(row["Kijun"]):
            rng = float(row["High"] - row["Low"])
            return {
                "Signal Time": ts, "Signal Price": float(row["Close"]),
                "Open": float(row["Open"]), "High": float(row["High"]),
                "Low": float(row["Low"]), "Close": float(row["Close"]),
                "Kijun": float(row["Kijun"]), "Jaw": float(row["Jaw"]),
                "Teeth": float(row["Teeth"]), "Lips": float(row["Lips"]),
                "Green Body %": ((float(row["Close"] - row["Open"]) / rng) * 100 if rng else 0)
            }
    return None

def scan_stock(symbol):
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
            "Symbol": symbol.replace(".NS", ""),
            "Current Price": current_price,
            "Previous Close": previous_close,
            "Day-1 O2L%": float(previous_three["O2L%"].iloc[-1]),
            "Day-2 O2L%": float(previous_three["O2L%"].iloc[-2]),
            "Day-3 O2L%": float(previous_three["O2L%"].iloc[-3]),
            "Signal Time": signal["Signal Time"],
            "Signal Price": signal["Signal Price"],
            "Kijun": signal["Kijun"],
            "Green Body %": signal["Green Body %"],
            "Alligator": "Bullish"
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
            futures = {executor.submit(scan_stock, s): s for s in saved_symbols}
            for i, future in enumerate(as_completed(futures), 1):
                symbol = futures[future]
                status.write(f"Scanning green stock {symbol.replace('.NS','')} — {i}/{total}")
                try:
                    result = future.result()
                    if result:
                        row, signal = result
                        results.append(row)
                        details.append((row["Symbol"], signal))
                except Exception:
                    pass
                progress.progress(i / total)

        status.success(f"Scan complete. Scanned only {total} saved green-shade stocks. {len(results)} BUY candidate(s) found.")

        if results:
            result_df = pd.DataFrame(results).sort_values(["Signal Time", "Green Body %"], ascending=[False, False])
            st.subheader(f"BUY Candidates ({len(result_df)})")
            st.dataframe(result_df, use_container_width=True, hide_index=True)
            st.download_button("⬇️ Download CSV", result_df.to_csv(index=False).encode("utf-8"), "buy_scan_results.csv", "text/csv")
            for symbol, signal in details:
                with st.expander(f"🟢 {symbol} — {signal['Signal Time']}"):
                    a, b, c = st.columns(3)
                    a.metric("Signal Price", f"{signal['Signal Price']:.2f}")
                    b.metric("Kijun", f"{signal['Kijun']:.2f}")
                    c.metric("Green Body", f"{signal['Green Body %']:.2f}%")
                    st.write({k: round(v, 2) if isinstance(v, (int, float, np.floating)) else v for k, v in signal.items()})
        else:
            st.warning("No saved green stocks matched all BUY conditions.")

with st.expander("📐 Formulas / Definitions"):
    st.code("""O2L% = ((Low - Open) / Open) * 100
Alligator bullish = Lips > Teeth > Jaw
Kijun = (Highest High over 26 + Lowest Low over 26) / 2
Long green candle = Close > Open and body/range >= selected threshold
Kijun crossing candle = Low <= Kijun <= High
Signal Close > Kijun
Current Price > Previous Daily Close""")

st.caption("Yahoo Finance/yfinance data. Screening signals are not guarantees of future price movement.")
