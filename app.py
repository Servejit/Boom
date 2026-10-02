import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import io
import matplotlib.pyplot as plt

st.set_page_config(page_title="3-Minute Bullish BUY Scanner", page_icon="📈", layout="wide")

UNIVERSE_FILE = Path(".boom_stock_universe.csv")
UNIVERSE_META = Path(".boom_stock_universe_meta.json")
AUTO_BLUE_FILE = Path(".boom_auto_blue.json")
INDEX_FILE = Path(".boom_index_universe.csv")
INDEX_META = Path(".boom_index_universe_meta.json")

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

def is_blue_fill(cell):
    """Return True when an Excel cell has a blue background/fill."""
    try:
        fill = cell.fill
        if not fill or not fill.fill_type:
            return False
        fg = fill.fgColor
        if fg.type == "rgb" and fg.rgb:
            h = fg.rgb[-6:].upper()
            r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)
            return b > r * 1.15 and b > g * 1.10
        if fg.type == "indexed" and fg.indexed is not None:
            return fg.indexed in {5, 12, 23, 24, 27, 28, 30, 32, 41, 42, 49, 55}
        rgb = getattr(fg, "rgb", None)
        if rgb:
            h = rgb[-6:].upper()
            r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)
            return b > r * 1.15 and b > g * 1.10
    except Exception:
        pass
    return False

def get_blue_fill_hex(cell):
    """Return the source Excel blue fill as a hex colour when possible."""
    try:
        fill = cell.fill
        if not fill or not fill.fill_type:
            return "#0000FF"
        fg = fill.fgColor
        if fg.type == "rgb" and fg.rgb:
            h = fg.rgb[-6:].upper()
            r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)
            if b > r * 1.15 and b > g * 1.10:
                return "#" + h
        if fg.type == "indexed" and fg.indexed is not None:
            indexed_hex = {
                5: "#0000FF", 12: "#000080", 23: "#3366FF", 24: "#6699FF",
                27: "#0033CC", 28: "#336699", 30: "#0066CC", 32: "#0099FF",
                41: "#0000FF", 42: "#0066CC", 49: "#0000FF", 55: "#3366FF"
            }
            if fg.indexed in indexed_hex:
                return indexed_hex[fg.indexed]
        rgb = getattr(fg, "rgb", None)
        if rgb:
            h = rgb[-6:].upper()
            r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)
            if b > r * 1.15 and b > g * 1.10:
                return "#" + h
    except Exception:
        pass
    return "#0000FF"

def extract_auto_blue_stocks(wb):
    """Read blue stocks from AutoBlue column A only and retain source blue colour."""
    try:
        if "AutoBlue" not in wb.sheetnames:
            return {}
        ws = wb["AutoBlue"]
        blue_symbols = {}
        for r in range(1, ws.max_row + 1):
            cell = ws.cell(r, 1)
            symbol = normalize_symbol(cell.value)
            if symbol and is_blue_fill(cell):
                blue_symbols[symbol] = get_blue_fill_hex(cell)
        return blue_symbols
    except Exception:
        return {}

def save_auto_blue_stocks(blue_map, original_filename):
    payload = {
        "filename": original_filename,
        "symbols": blue_map,
        "updated": pd.Timestamp.now().isoformat(),
        "hash": hashlib.sha256(json.dumps(blue_map, sort_keys=True).encode()).hexdigest()
    }
    AUTO_BLUE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

def load_auto_blue_stocks():
    if not AUTO_BLUE_FILE.exists():
        return {}
    try:
        payload = json.loads(AUTO_BLUE_FILE.read_text(encoding="utf-8"))
        raw = payload.get("symbols", {})
        if isinstance(raw, list):
            return {normalize_symbol(x): "#0000FF" for x in raw if normalize_symbol(x)}
        return {
            normalize_symbol(symbol): str(color or "#0000FF")
            for symbol, color in raw.items()
            if normalize_symbol(symbol)
        }
    except Exception:
        return {}

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
        # AutoGreen is the ONLY source of the scanning universe.
        ws = wb["AutoGreen"] if "AutoGreen" in wb.sheetnames else wb.active
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

def parse_index_file(uploaded_bytes):
    """Parse an Excel index file: row 1 contains index names, stocks are below each column."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(uploaded_bytes), data_only=True)
    ws = wb.active
    index_map = {}
    for c in range(1, ws.max_column + 1):
        raw_index = ws.cell(1, c).value
        if raw_index is None or not str(raw_index).strip():
            continue
        index_name = str(raw_index).strip()
        symbols = []
        for r in range(2, ws.max_row + 1):
            symbol = normalize_symbol(ws.cell(r, c).value)
            if symbol:
                symbols.append(symbol)
        if symbols:
            index_map[index_name] = list(dict.fromkeys(symbols))
    return index_map

def save_index_universe(index_map, original_filename):
    rows = []
    for index_name, symbols in index_map.items():
        for symbol in symbols:
            rows.append({"Index": index_name, "Symbol": symbol})
    pd.DataFrame(rows, columns=["Index", "Symbol"]).to_csv(INDEX_FILE, index=False)
    meta = {
        "filename": original_filename,
        "index_count": len(index_map),
        "stock_membership_count": len(rows),
        "updated": pd.Timestamp.now().isoformat(),
        "hash": hashlib.sha256(json.dumps(index_map, sort_keys=True).encode()).hexdigest()
    }
    INDEX_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")

def load_saved_index_universe():
    if not INDEX_FILE.exists():
        return {}
    try:
        df = pd.read_csv(INDEX_FILE)
        if not {"Index", "Symbol"}.issubset(df.columns):
            return {}
        out = {}
        for index_name, group in df.groupby("Index", sort=False):
            symbols = [normalize_symbol(x) for x in group["Symbol"].dropna().tolist()]
            symbols = [x for x in symbols if x]
            if symbols:
                out[str(index_name)] = list(dict.fromkeys(symbols))
        return out
    except Exception:
        return {}

def load_index_meta():
    if not INDEX_META.exists():
        return {}
    try:
        return json.loads(INDEX_META.read_text(encoding="utf-8"))
    except Exception:
        return {}

def get_index_positions(index_map):
    """Return each symbol's rank by current-day % change within every supplied index."""
    if not index_map:
        return {}
    all_symbols = list(dict.fromkeys(s for members in index_map.values() for s in members))
    if not all_symbols:
        return {}
    try:
        raw = yf.download(
            all_symbols, period="5d", interval="1d", auto_adjust=False,
            progress=False, threads=False, group_by="column"
        )
        if raw.empty:
            return {}
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else pd.DataFrame()
        else:
            close = raw[["Close"]] if "Close" in raw.columns else pd.DataFrame()
            if len(all_symbols) == 1:
                close.columns = all_symbols
        if close.empty:
            return {}
        changes = {}
        for symbol in all_symbols:
            try:
                series = pd.to_numeric(close[symbol], errors="coerce").dropna()
                if len(series) < 2 or float(series.iloc[-2]) == 0:
                    continue
                changes[symbol] = (float(series.iloc[-1]) / float(series.iloc[-2]) - 1.0) * 100.0
            except Exception:
                continue
        positions = {}
        for index_name, members in index_map.items():
            ranked = [(symbol, changes[symbol]) for symbol in members if symbol in changes]
            ranked.sort(key=lambda x: x[1], reverse=True)
            for rank, (symbol, pct) in enumerate(ranked, 1):
                positions.setdefault(symbol, []).append(f"{index_name}: Top {rank} ({pct:+.2f}%)")
        return {symbol: "; ".join(values) if values else "No Index" for symbol, values in positions.items()}
    except Exception:
        return {}

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
            auto_blue_map = {}
            if uploaded.name.lower().endswith((".xlsx", ".xls")):
                from openpyxl import load_workbook
                wb_upload = load_workbook(io.BytesIO(raw_bytes), data_only=True)
                auto_blue_map = extract_auto_blue_stocks(wb_upload)
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
                save_auto_blue_stocks(auto_blue_map, uploaded.name)
                st.success(
                    f"Saved {len(records)} AutoGreen stocks from {uploaded.name}. "
                    f"{len(auto_blue_map)} blue AutoBlue stocks detected. "
                    "Matching stocks will show the AutoBlue source colour in Current Price."
                )
            else:
                st.error("No green-shade stocks were detected. For Excel, make sure the stock rows/cells are actually filled with green.")
        except Exception as e:
            st.error(f"Could not process the file: {e}")

saved_universe = load_saved_universe()
auto_blue_map = load_auto_blue_stocks()
st.subheader("📊 Index Membership File")
with st.expander("Upload / Replace Index File", expanded=not bool(load_saved_index_universe())):
    index_uploaded = st.file_uploader(
        "Upload an Excel file: first row = Index names, stocks below each index column.",
        type=["xlsx", "xls"], key="index_file_upload"
    )
    if index_uploaded is not None:
        try:
            index_map = parse_index_file(index_uploaded.getvalue())
            if index_map:
                save_index_universe(index_map, index_uploaded.name)
                st.success(f"Saved {len(index_map)} indexes from {index_uploaded.name}. This file will remain active until the next Index File upload.")
            else:
                st.error("No index memberships were detected. Make sure the first row contains index names and stocks are listed below each column.")
        except Exception as e:
            st.error(f"Could not process the Index File: {e}")

saved_index_map = load_saved_index_universe()
index_meta = load_index_meta()
if saved_index_map:
    total_memberships = sum(len(v) for v in saved_index_map.values())
    st.success(f"ACTIVE INDEX FILE: {len(saved_index_map)} indexes | {total_memberships} memberships | File: {index_meta.get('filename','saved file')} | Next Index File upload will replace it.")
else:
    st.caption("No Index File is active. Scanned stocks will show 'No Index'.")

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
    st.header("Scanner Controls")

    scan_on = st.checkbox("🟢 Scanner ON", value=True)
    show_backtest = st.checkbox("📊 Backtest ON", value=True)
    perspective_1h_on = st.checkbox("🕐 1H Perspective", value=False)
    perspective_45m_on = st.checkbox("🕐 45M Perspective", value=False)
    perspective_30m_on = st.checkbox("🕐 30M Perspective", value=False)

    with st.expander("⚙️ Scanner Details", expanded=False):
        workers = st.slider("Parallel workers", 1, 10, 5)

        st.markdown("**Alligator settings**")
        jaw_period = st.number_input("Jaw period", 2, 50, 13)
        jaw_shift = st.number_input("Jaw shift", 0, 20, 8)
        teeth_period = st.number_input("Teeth period", 2, 50, 8)
        teeth_shift = st.number_input("Teeth shift", 0, 20, 5)
        lips_period = st.number_input("Lips period", 2, 50, 5)
        lips_shift = st.number_input("Lips shift", 0, 20, 3)

        st.markdown("**Ichimoku settings**")
        conversion_period = st.number_input("Conversion / Tenkan", 2, 100, 9)
        base_period = st.number_input("Base / Kijun", 2, 100, 26)
        span_b_period = st.number_input("Span B", 2, 150, 52)
        min_body_percent = st.slider("Minimum long green candle body %", 20, 95, 60)
        kijun_tolerance = st.slider("Kijun candle tolerance %", 0.0, 2.0, 0.0, 0.1)

        st.markdown("**ADX settings**")
        adx_period = st.number_input("ADX / DI period", 5, 50, 14)
        adx_min = st.slider("Minimum ADX", 10.0, 40.0, 20.0, 1.0)
        require_adx_rising = st.checkbox("Require ADX rising", value=True)
        require_plus_di = st.checkbox("Require +DI > -DI", value=True)

        st.markdown("**Backtest settings**")
        bt_target = st.number_input("Backtest target %", 0.2, 10.0, 1.0, 0.1)
        bt_stop = st.number_input("Backtest stop %", 0.2, 5.0, 0.5, 0.1)
        bt_bars = st.number_input("Bars after signal", 1, 50, 10)


# Safe defaults when the details panel is left closed.
if "workers" not in locals():
    workers = 5
if "jaw_period" not in locals():
    jaw_period, jaw_shift, teeth_period, teeth_shift, lips_period, lips_shift = 13, 8, 8, 5, 5, 3
if "conversion_period" not in locals():
    conversion_period, base_period, span_b_period, min_body_percent, kijun_tolerance = 9, 26, 52, 60, 0.0
if "adx_period" not in locals():
    adx_period, adx_min, require_adx_rising, require_plus_di = 14, 20.0, True, True
if "bt_target" not in locals():
    bt_target, bt_stop, bt_bars = 1.0, 0.5, 10

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

def get_1h_data(symbol):
    """Download 15-minute data once and build 1H/45M/30M/15M perspectives."""
    try:
        x = clean_columns(yf.download(symbol, period="60d", interval="15m", auto_adjust=False, progress=False, threads=False))
        if x.empty:
            return {}
        if not isinstance(x.index, pd.DatetimeIndex):
            x.index = pd.to_datetime(x.index)
        if getattr(x.index, "tz", None) is not None:
            x.index = x.index.tz_convert("Asia/Kolkata").tz_localize(None)
        x = x.dropna(subset=["Open", "High", "Low", "Close"]).copy()

        def aggregate(frame, rule):
            # NSE intraday candles are anchored to the 09:15 session open,
            # not to midnight. This keeps 30M/45M/1H candles aligned with
            # the candles normally shown on trading charts.
            return frame.resample(
                rule,
                origin="start_day",
                offset="15min",
                label="left",
                closed="left"
            ).agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum"
            }).dropna(subset=["Open", "High", "Low", "Close"])

        return {
            "15M": x,
            "30M": aggregate(x, "30min"),
            "45M": aggregate(x, "45min"),
            "1H": aggregate(x, "1h")
        }
    except Exception:
        return {}

def analyze_multi_timeframe_perspective(data):
    result = {}
    for tf in ["1H", "45M", "30M", "15M"]:
        frame = data.get(tf, pd.DataFrame()) if isinstance(data, dict) else pd.DataFrame()
        result[f"{tf} Current"] = "N/A"
        result[f"{tf} Red Pattern"] = "N/A"
        if frame is None or frame.empty:
            continue
        # The final row is intentionally treated as the LIVE / FORMING
        # candle. Its OHLC values may change until that timeframe closes.
        cur = frame.iloc[-1]
        current_green = float(cur["Close"]) > float(cur["Open"])
        result[f"{tf} Current"] = "Green" if current_green else "Red"

        # Red-1 is the immediately preceding COMPLETED candle.
        # Continue backward through every consecutive completed red candle
        # until the first non-red candle. There is no artificial limit.
        red_count = 0
        for i in range(2, len(frame) + 1):
            candle = frame.iloc[-i]
            if float(candle["Close"]) < float(candle["Open"]):
                red_count += 1
            else:
                break

        if current_green:
            result[f"{tf} Red Pattern"] = f"{red_count} Red"
        else:
            result[f"{tf} Red Pattern"] = "Current Not Green"
    return result

def timeframe_perspective_passes(row, tf):
    # Perspective filter requires the current candle to be Green.
    current = str(row.get(f"{tf} Current", ""))
    pattern = str(row.get(f"{tf} Red Pattern", ""))
    if current != "Green":
        return False
    try:
        return int(pattern.split()[0]) >= 1
    except Exception:
        return False

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

def find_signal(intraday, max_pos=None):
    if intraday.empty:
        return None
    df = calculate_adx(calculate_ichimoku(calculate_alligator(intraday)))
    start_pos = len(df) - 1 if max_pos is None else min(int(max_pos), len(df) - 1)
    for pos in range(start_pos, -1, -1):
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
        # _signal_pos is based on the same 3-minute dataframe used by find_signal.
        future = intraday.iloc[pos + 1:pos + 1 + int(bt_bars)].copy()
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

def scan_stock(symbol, green_shade="Green", green_color="#00B050", auto_blue=False):
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
        backtest_max_pos = len(intraday) - int(bt_bars) - 1 if show_backtest else None
        signal = find_signal(intraday, backtest_max_pos)
        if not signal:
            return None
        timeframe_data = get_1h_data(symbol)
        mtf = analyze_multi_timeframe_perspective(timeframe_data)
        outcome, outcome_pct = evaluate_signal_outcome(intraday, signal) if show_backtest else ("Not Run", np.nan)
        if show_backtest and outcome == "Unknown":
            outcome = "Neither" if len(intraday) > int(signal["_signal_pos"]) + 1 else "No future data"
            outcome_pct = 0.0 if outcome == "Neither" else np.nan
        return {
            "Symbol": symbol.replace(".NS", ""), "_Green Color": green_color,
            "_AutoBlue": bool(auto_blue), "_AutoBlueColor": auto_blue_map.get(symbol, "#0000FF") if auto_blue else "",
            "Index Position": "No Index",
            "Current Price": current_price, "Previous Close": previous_close,
            "Day-1 O2L%": float(previous_three["O2L%"].iloc[-1]), "Day-2 O2L%": float(previous_three["O2L%"].iloc[-2]), "Day-3 O2L%": float(previous_three["O2L%"].iloc[-3]),
            "Signal Time": signal["Signal Time"], "Signal Price": signal["Signal Price"], "Kijun": signal["Kijun"],
            "ADX": signal["ADX"], "+DI": signal["Plus_DI"], "-DI": signal["Minus_DI"],
            "Backtest Outcome": outcome, "Backtest Return %": outcome_pct,
            "Green Body %": signal["Green Body %"], "Alligator": "Bullish", **mtf
        }, signal
    except Exception:
        return None

st.info("Only the saved green-shade stock universe is scanned. The saved file/list is retained until a new upload replaces it.")

def make_chart_thumbnail(intraday, signal):
    """Create a compact true 3-minute OHLC candlestick thumbnail with indicators."""
    try:
        df = calculate_adx(calculate_ichimoku(calculate_alligator(intraday.copy())))
        pos = int(signal.get("_signal_pos", len(df) - 1))
        chart = df.iloc[max(0, pos - 45):pos + 1].copy()
        if chart.empty:
            return None

        fig, ax = plt.subplots(figsize=(5.8, 3.1), dpi=120)
        x = np.arange(len(chart), dtype=float)
        price_span = max(float(chart["High"].max() - chart["Low"].min()), 1e-9)
        candle_width = 0.62
        for i, (_, bar) in enumerate(chart.iterrows()):
            o, h, l, cl = map(float, [bar["Open"], bar["High"], bar["Low"], bar["Close"]])
            is_green = cl >= o
            body_low = min(o, cl)
            body_height = max(abs(cl - o), price_span * 0.002)
            # Green/red candlestick bodies and wicks.
            candle_color = "green" if is_green else "red"
            ax.vlines(i, l, h, linewidth=0.75, color=candle_color, zorder=2)
            rect = plt.Rectangle((i - candle_width / 2, body_low), candle_width, body_height,
                                 facecolor=candle_color, edgecolor=candle_color, linewidth=0.5, zorder=3)
            ax.add_patch(rect)

        ax.plot(x, chart["Kijun"].to_numpy(dtype=float), linewidth=1.0, label="Kijun")
        ax.plot(x, chart["Jaw"].to_numpy(dtype=float), linewidth=0.75, label="Jaw")
        ax.plot(x, chart["Teeth"].to_numpy(dtype=float), linewidth=0.75, label="Teeth")
        ax.plot(x, chart["Lips"].to_numpy(dtype=float), linewidth=0.75, label="Lips")

        signal_x = len(chart) - 1
        ax.scatter([signal_x], [float(signal["Signal Price"])], s=38, marker="^", zorder=5, label="BUY")
        ax.set_title("3M Candlestick Signal", fontsize=9)
        ax.set_xlim(-1, len(chart))
        ax.grid(True, alpha=0.18, axis="y")
        ax.tick_params(axis="both", labelsize=7)
        tick_idx = np.linspace(0, len(chart) - 1, min(6, len(chart)), dtype=int)
        ax.set_xticks(tick_idx)
        ax.set_xticklabels([chart.index[i].strftime("%H:%M") for i in tick_idx], rotation=25, fontsize=6)
        ax.legend(loc="upper left", fontsize=5.5, ncol=5, frameon=False)
        fig.tight_layout(pad=0.6)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        return None

def render_sidebar_matching_thumbnails(result_df):
    with st.sidebar:
        st.markdown("---")
        st.subheader("🟢 Matching Stocks")
        if result_df is None or result_df.empty:
            st.caption("No matching stocks from the latest scan.")
            return
        for _, row in result_df.iterrows():
            symbol = str(row.get("Symbol", ""))
            color = str(row.get("_Green Color", "#00B050") or "#00B050")
            signal_price = pd.to_numeric(row.get("Signal Price", np.nan), errors="coerce")
            adx = pd.to_numeric(row.get("ADX", np.nan), errors="coerce")
            body = pd.to_numeric(row.get("Green Body %", np.nan), errors="coerce")
            chart_bytes = row.get("_Chart Thumbnail")
            with st.expander(f"🟢 {symbol}", expanded=False):
                price_text = f"₹{signal_price:.2f}" if pd.notna(signal_price) else "N/A"
                st.markdown(f"<div style='border-left:6px solid {color};padding:8px;border-radius:6px;background:rgba(0,176,80,.08)'><b>{symbol}</b><br>Signal: {price_text}</div>", unsafe_allow_html=True)
                if chart_bytes:
                    st.image(chart_bytes, use_container_width=True)
                    z1, z2 = st.columns(2)
                    with z1:
                        with st.popover("🔍 Zoom", use_container_width=True):
                            st.image(chart_bytes, caption=f"{symbol} — 3-minute candlestick chart", use_container_width=True)
                    with z2:
                        tradingview_symbol = symbol.replace(".NS", "").upper()
                        tradingview_url = f"https://www.tradingview.com/chart/?symbol=NSE%3A{tradingview_symbol}"
                        st.link_button("📊 TradingView", tradingview_url, use_container_width=True)
                st.caption("Mini chart: true 3-minute OHLC candlesticks with Kijun and Alligator lines. Use Zoom for a larger view or TradingView for the full interactive chart.")
                for label, value in [
                    ("Alligator", "✓ Bullish"),
                    ("Kijun", "✓ Cross + Close above"),
                    ("Green Candle", f"✓ {body:.2f}% body" if pd.notna(body) else "✓"),
                    ("ADX", f"✓ {adx:.2f}" if pd.notna(adx) else "✓"),
                    ("+DI > -DI", "✓"),
                    ("3-Day O2L", "✓ All < -1%"),
                    ("Price > Prev Close", "✓"),
                    ("1H Current", f"✓ {row.get('1H Current','N/A')}"),
                    ("1H Red Pattern", f"✓ {row.get('1H Red Pattern','N/A')}"),
                    ("45M Current", f"✓ {row.get('45M Current','N/A')}"),
                    ("45M Red Pattern", f"✓ {row.get('45M Red Pattern','N/A')}"),
                    ("30M Current", f"✓ {row.get('30M Current','N/A')}"),
                    ("30M Red Pattern", f"✓ {row.get('30M Red Pattern','N/A')}"),
                    ("15M Current", f"✓ {row.get('15M Current','N/A')}"),
                    ("15M Red Pattern", f"✓ {row.get('15M Red Pattern','N/A')}"),
                ]:
                    st.markdown(f"**{label}:** {value}")

def render_scan_results(result_df, details):
    if result_df.empty:
        st.warning("No saved green stocks matched the active filters.")
        return

    result_df = result_df.sort_values(
        ["Signal Time", "Green Body %"], ascending=[False, False]
    )
    st.subheader(f"BUY Candidates ({len(result_df)})")
    display_df = result_df.drop(
        columns=["_Green Color", "_AutoBlue", "_AutoBlueColor", "_Chart Thumbnail", "1H Perspective Ready", "1H Red-1", "1H Red-2", "1H Red-3", "1H Red-1 Above Low %", "1H Red-2 Above Low %", "1H Red-3 Above Low %"], errors="ignore"
    ).copy()

    for col in [
        "Current Price", "Previous Close", "Signal Price", "Kijun",
        "ADX", "+DI", "-DI", "Backtest Return %"
    ]:
        if col in display_df.columns:
            display_df[col] = pd.to_numeric(
                display_df[col], errors="coerce"
            ).map(lambda x: f"{x:.2f}" if pd.notna(x) else "")

    desired_order = [
        "Symbol", "Current Price", "Previous Close", "Index Position", "Day-1 O2L%", "Day-2 O2L%", "Day-3 O2L%",
        "Signal Time", "Signal Price", "Kijun", "ADX", "+DI", "-DI", "Backtest Outcome", "Backtest Return %",
        "Green Body %", "Alligator", "1H Current", "1H Red Pattern", "45M Current", "45M Red Pattern", "30M Current", "30M Red Pattern", "15M Current", "15M Red Pattern"
    ]
    ordered = [c for c in desired_order if c in display_df.columns]
    remaining = [c for c in display_df.columns if c not in ordered]
    display_df = display_df[ordered + remaining]

    result_color_map = {
        str(row["Symbol"]): str(
            row.get("_Green Color", "#00B050") or "#00B050"
        )
        for _, row in result_df.iterrows()
    }
    auto_blue_status_map = {
        str(row["Symbol"]): bool(row.get("_AutoBlue", False))
        for _, row in result_df.iterrows()
    }
    auto_blue_color_map = {
        str(row["Symbol"]): str(row.get("_AutoBlueColor", "#0000FF") or "#0000FF")
        for _, row in result_df.iterrows()
    }

    def color_result_rows(row):
        symbol = str(row["Symbol"])
        green_color = result_color_map.get(symbol, "#00B050")
        blue_price = auto_blue_status_map.get(symbol, False)
        styles = []
        for col in display_df.columns:
            if col == "Symbol":
                styles.append(f"background-color: {green_color}; font-weight: 700")
            elif col == "Current Price" and blue_price:
                blue_color = auto_blue_color_map.get(symbol, "#0000FF")
                styles.append(f"color: {blue_color}; font-weight: 700")
            else:
                styles.append("")
        return styles

    st.dataframe(
        display_df.style.apply(color_result_rows, axis=1),
        use_container_width=True,
        hide_index=True
    )
    st.download_button(
        "⬇️ Download CSV",
        display_df.to_csv(index=False).encode("utf-8"),
        "buy_scan_results.csv",
        "text/csv",
        key="download_scan_results"
    )

    bt = result_df[
        result_df["Backtest Outcome"].isin(
            ["Target", "Stop", "Neither", "Ambiguous"]
        )
    ].copy()
    if not bt.empty:
        st.subheader("📊 Current Scan Backtest Check")
        target_count = int((bt["Backtest Outcome"] == "Target").sum())
        stop_count = int((bt["Backtest Outcome"] == "Stop").sum())
        evaluated = target_count + stop_count
        win_rate = (target_count / evaluated * 100) if evaluated else np.nan
        x1, x2, x3 = st.columns(3)
        x1.metric("Target Hits", target_count)
        x2.metric("Stop Hits", stop_count)
        x3.metric(
            "Win Rate",
            f"{win_rate:.2f}%" if pd.notna(win_rate) else "N/A"
        )
        st.caption(
            f"Outcome test uses target {bt_target:.2f}%, stop {bt_stop:.2f}%, "
            f"and the next {int(bt_bars)} completed 3-minute candles. "
            "This is a forward check of the signals found in the current scan, "
            "not a full historical backtest."
        )

    for symbol, signal, outcome, outcome_pct in details:
        with st.expander(f"🟢 {symbol} — {signal['Signal Time']}"):
            a, b, c, d = st.columns(4)
            a.metric("Signal Price", f"{signal['Signal Price']:.2f}")
            b.metric("Kijun", f"{signal['Kijun']:.2f}")
            c.metric("ADX", f"{signal['ADX']:.2f}")
            d.metric("Backtest", outcome)
            st.write({
                k: round(v, 2)
                if isinstance(v, (int, float, np.floating)) else v
                for k, v in signal.items()
                if not k.startswith("_")
            })

if st.button("🚀 RUN BUY SCAN", type="primary", use_container_width=True):
    if not scan_on:
        st.info("Scanner is OFF. Turn Scanner ON from the sidebar to run it.")
        st.stop()
    if not saved_symbols:
        st.error("Upload a stock file containing green-shade stocks first.")
    else:
        results, details = [], []
        progress, status = st.progress(0), st.empty()
        total = len(saved_symbols)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    scan_stock,
                    r["Symbol"],
                    r.get("Green Shade", "Green"),
                    r.get("Green Color", "#00B050"),
                    r["Symbol"] in auto_blue_map
                ): r
                for r in saved_universe
            }
            for i, future in enumerate(as_completed(futures), 1):
                record = futures[future]
                status.write(
                    f"Scanning green stock "
                    f"{record['Symbol'].replace('.NS', '')} — {i}/{total}"
                )
                try:
                    result = future.result()
                    if result:
                        row, signal = result
                        row["_Chart Thumbnail"] = make_chart_thumbnail(get_intraday_data(record["Symbol"]), signal)
                        results.append(row)
                        details.append(
                            (row["Symbol"], signal, outcome, outcome_pct)
                        )
                except Exception:
                    pass
                progress.progress(i / total)

        index_positions = get_index_positions(saved_index_map)
        for row in results:
            row["Index Position"] = index_positions.get(str(row.get("Symbol", "")).upper() + ".NS", "No Index")
        st.session_state["boom_scan_results"] = pd.DataFrame(results)
        st.session_state["boom_scan_details"] = details
        st.session_state["boom_scan_completed"] = True
        status.success(
            f"Scan complete. Scanned only {total} saved green-shade stocks. "
            f"{len(results)} BUY candidate(s) found. All 1H details were "
            "collected during this scan."
        )

if st.session_state.get("boom_scan_completed", False):
    all_results = st.session_state.get(
        "boom_scan_results", pd.DataFrame()
    ).copy()
    all_details = st.session_state.get("boom_scan_details", [])

    if not all_results.empty:
        filtered = all_results.copy()
        active_filters = []
        if perspective_1h_on:
            filtered = filtered[filtered.apply(lambda row: timeframe_perspective_passes(row, "1H"), axis=1)].copy()
            active_filters.append("1H")
        if perspective_45m_on:
            filtered = filtered[filtered.apply(lambda row: timeframe_perspective_passes(row, "45M"), axis=1)].copy()
            active_filters.append("45M")
        if perspective_30m_on:
            filtered = filtered[filtered.apply(lambda row: timeframe_perspective_passes(row, "30M"), axis=1)].copy()
            active_filters.append("30M")
        if active_filters:
            st.info(f"Timeframe Perspective ON: {', '.join(active_filters)}. Showing {len(filtered)} of {len(all_results)} candidates. Change the switches without running the scanner again.")
        render_scan_results(filtered, [d for d in all_details if str(d[0]) in set(filtered["Symbol"].astype(str))])


if st.session_state.get("boom_scan_completed", False):
    _sidebar_results = st.session_state.get("boom_scan_results", pd.DataFrame()).copy()
    if not _sidebar_results.empty:
        if perspective_1h_on:
            _sidebar_results = _sidebar_results[_sidebar_results.apply(lambda row: timeframe_perspective_passes(row, "1H"), axis=1)].copy()
        render_sidebar_matching_thumbnails(_sidebar_results)

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
