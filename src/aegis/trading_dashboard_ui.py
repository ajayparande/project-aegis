"""Presentation only: escaped terminal components and charts of verified read models."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from html import escape
from typing import Any

import altair as alt
import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from aegis.trading_dashboard_data import IST, UNAVAILABLE, safe_text, timestamp

PALETTE = {
    "background": "#0B1120",
    "sidebar": "#111C30",
    "card": "#17243A",
    "border": "#293B55",
    "text": "#F1F5F9",
    "secondary": "#94A3B8",
    "blue": "#3B82F6",
    "purple": "#8B5CF6",
    "positive": "#22C55E",
    "negative": "#F87171",
    "warning": "#F59E0B",
}
THEME = {
    "base": "dark",
    "backgroundColor": PALETTE["background"],
    "secondaryBackgroundColor": PALETTE["card"],
    "textColor": PALETTE["text"],
    "primaryColor": PALETTE["blue"],
    "borderColor": PALETTE["border"],
    "sidebar.backgroundColor": PALETTE["sidebar"],
    "sidebar.secondaryBackgroundColor": PALETTE["card"],
    "dataframeHeaderBackgroundColor": PALETTE["sidebar"],
    "dataframeHeaderTextColor": PALETTE["secondary"],
    "dataframeBorderColor": PALETTE["border"],
    "font": "sans serif",
    "baseFontSize": "14",
    "baseRadius": "10px",
    "buttonRadius": "8px",
    "paddingTop": "1rem",
    "paddingBottom": "2rem",
    "sidebar.paddingTop": "0.5rem",
}

# Static CSS only. Keys are Streamlit's supported st.container(key=...) CSS hooks;
# tab selectors use semantic ARIA roles, never generated Emotion/BaseWeb classes.
STYLES = """<style>
.aegis-brand {display:flex;align-items:center;gap:12px;margin-bottom:4px}
.aegis-monogram {display:grid;place-items:center;width:35px;height:35px;border-radius:10px;
 background:#17243A;border:1px solid #293B55;color:#60A5FA;font-size:21px;font-weight:700}
.aegis-brand h1 {font-size:19px;letter-spacing:2px;line-height:1.1;margin:0;color:#F1F5F9}
.aegis-brand h1 span {font-size:12px;font-weight:500;letter-spacing:1.6px;color:#94A3B8}
.aegis-brand p {margin:4px 0 0;font-size:11px;color:#94A3B8;letter-spacing:.45px}
.aegis-header {display:flex;justify-content:space-between;gap:12px;align-items:center;
 border-bottom:1px solid #293B55;padding:0 0 12px;margin:0 0 2px}
.aegis-header .aegis-brand {margin:0}
.aegis-badges {display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.aegis-badge {display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:650;
 letter-spacing:.45px;border:1px solid #293B55;border-radius:5px;padding:5px 8px;
 color:#94A3B8;background:#111C30;line-height:1.2}
.aegis-badge.blue {color:#93C5FD;border-color:#25477B;background:#132744}
.aegis-badge.purple {color:#C4B5FD;border-color:#4B3E78;background:#24203C}
.aegis-context {display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:11px 0 4px;
 color:#94A3B8;font-size:11px;line-height:1.5}
.aegis-context strong {color:#F1F5F9;font-weight:550}
.aegis-context > span {min-width:0;overflow-wrap:anywhere}
.st-key-aegis-terminal [role="alert"] {overflow-wrap:anywhere}
.aegis-context .aegis-session {max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.aegis-kpis {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:5px 0}
.aegis-kpi {background:#17243A;border:1px solid #293B55;border-radius:11px;padding:12px 16px;
 min-width:0;box-shadow:0 3px 10px #0000000D;position:relative}
.aegis-kpi .aegis-label {display:flex;align-items:center;justify-content:space-between;gap:5px;
 font-size:11px;font-weight:500;color:#94A3B8;margin:0 0 7px;line-height:1.3}
.aegis-kpi .aegis-label span {font-size:12px;color:#6885AB;font-weight:400}
.aegis-value {font-size:clamp(18px,1.65vw,29px);line-height:1.15;font-weight:650;
 font-variant-numeric:tabular-nums;letter-spacing:-.55px;color:#F1F5F9;overflow-wrap:anywhere}
.aegis-kpi .aegis-note {font-size:10px;color:#94A3B8;line-height:1.5;margin-top:6px;
 overflow-wrap:anywhere}
.aegis-kpi.positive .aegis-value {color:#22C55E}
.aegis-kpi.negative .aegis-value {color:#F87171}
.aegis-kpi.accent .aegis-value {color:#93C5FD}
.aegis-kpi.unavailable .aegis-value {font-size:17px;color:#94A3B8;letter-spacing:0}
.aegis-section {display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin:2px 0 8px}
.aegis-section h2 {font-size:14px;line-height:1.4;font-weight:600;margin:0;color:#F1F5F9}
.aegis-section span {font-size:10px;color:#94A3B8}
.aegis-health {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:10px 0}
.aegis-health-item {min-width:0;border:1px solid #293B55;border-radius:8px;padding:10px 12px;background:#111C30}
.aegis-health-item small {font-size:10px;color:#94A3B8;display:block;margin-bottom:5px}
.aegis-health-item strong {font-size:11px;font-weight:550;display:flex;align-items:center;gap:6px;
 overflow-wrap:anywhere;color:#94A3B8}
.aegis-dot {width:6px;height:6px;border-radius:50%;background:#94A3B8;flex-shrink:0}
.aegis-health-item.positive .aegis-dot {background:#22C55E}
.aegis-health-item.positive strong {color:#86EFAC}
.aegis-health-item.warning .aegis-dot {background:#F59E0B}
.aegis-health-item.warning strong {color:#FBBF24}
.aegis-health-item.negative .aegis-dot {background:#F87171}
.aegis-health-item.negative strong {color:#F87171}
.aegis-footnote {font-size:10px;color:#94A3B8;line-height:1.6;margin:6px 0}
.st-key-aegis-workspace .aegis-health {grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.st-key-aegis-workspace .aegis-health-item {padding:8px}
.st-key-aegis-workspace .aegis-health-item strong {font-size:10px}
.st-key-aegis-workspace .aegis-brand h1 {font-size:16px}
.st-key-aegis-terminal [role="tablist"] {gap:0;border-bottom:1px solid #293B55;margin-bottom:10px}
.st-key-aegis-terminal [role="tab"] {flex:1 1 0;min-width:0;padding:9px 7px;height:54px;
 border-radius:7px 7px 0 0;color:#94A3B8;gap:5px}
.st-key-aegis-terminal [role="tab"] p {font-size:12px;line-height:1.25;white-space:normal}
.st-key-aegis-terminal [role="tab"][aria-selected="true"] {color:#93C5FD;background:#17243A}
.st-key-aegis-terminal [role="tab"]::before {font-size:14px;opacity:.8}
.st-key-aegis-terminal [role="tab"]:nth-child(1)::before {content:"▦"}
.st-key-aegis-terminal [role="tab"]:nth-child(2)::before {content:"≋"}
.st-key-aegis-terminal [role="tab"]:nth-child(3)::before {content:"▣"}
.st-key-aegis-terminal [role="tab"]:nth-child(4)::before {content:"⇄"}
.st-key-aegis-terminal [role="tab"]:nth-child(5)::before {content:"◷"}
.st-key-aegis-terminal [role="tab"]:nth-child(6)::before {content:"◇"}
.st-key-aegis-terminal [role="tab"]:nth-child(7)::before {content:"⚑"}
.st-key-aegis-terminal [role="tab"]:nth-child(8)::before {content:"≡"}
@media(max-width:1100px) {.aegis-header {align-items:flex-start;flex-direction:column}
 .aegis-kpis {grid-template-columns:repeat(2,minmax(0,1fr))}
 .aegis-value {font-size:24px}.aegis-health {grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:800px) {.st-key-aegis-terminal [role="tab"] {flex:none;min-width:115px}
 .aegis-context {gap:8px}.aegis-context .aegis-session {max-width:240px}}
@media(max-width:480px) {.aegis-kpis {gap:8px}.aegis-kpi {padding:12px}.aegis-value {font-size:19px}
 .aegis-header .aegis-brand h1 {font-size:16px}.aegis-badge {font-size:9px;padding:5px}
 .aegis-context .aegis-session {max-width:200px}}
@media(max-width:350px) {.aegis-kpis {grid-template-columns:1fr}}
</style>"""


def theme_arguments() -> list[str]:
    """Official per-launch theme flags; never change another app's global config."""
    return [f"--theme.{key}={value}" for key, value in THEME.items()]


def text(value: object) -> str:
    return escape(safe_text(value), quote=True)


def decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def formatted(value: object, kind: str = "money") -> str:
    parsed = decimal(value)
    if parsed is None:
        return UNAVAILABLE
    places = 0 if kind == "count" else 4 if kind == "percent" else 3 if kind == "ratio" else 2
    if Decimal(f"{parsed:.{places}f}") == 0:
        parsed = Decimal(0)
    if kind == "count":
        return f"{parsed:,.0f}"
    if kind == "percent":
        return f"{parsed:,.4f}%"
    if kind == "ratio":
        return f"{parsed:,.3f}"
    return f"₹{parsed:,.2f}"


def value_tone(value: object, *, directional: bool = False) -> str:
    parsed = decimal(value)
    if parsed is None:
        return "unavailable"
    return (
        ("positive" if parsed > 0 else "negative" if parsed < 0 else "neutral")
        if directional
        else "neutral"
    )


def kpi_html(
    label: str,
    value: object,
    *,
    kind: str = "money",
    directional: bool = False,
    note: str = "",
    accent: bool = False,
) -> str:
    tone = value_tone(value, directional=directional)
    if accent and tone == "neutral":
        tone = "accent"
    return (
        f'<article class="aegis-kpi {tone}" aria-label="{text(label)}">'
        f'<div class="aegis-label">{text(label)}<span aria-hidden="true">◈</span></div>'
        f'<div class="aegis-value">{text(formatted(value, kind))}</div>'
        f'<div class="aegis-note">{text(note)}</div></article>'
    )


def kpis(items: list[dict[str, Any]]) -> None:
    st.html('<div class="aegis-kpis">' + "".join(kpi_html(**item) for item in items) + "</div>")


def section(title: str, detail: str = "") -> None:
    st.html(f'<div class="aegis-section"><h2>{text(title)}</h2><span>{text(detail)}</span></div>')


def local_time(value: object) -> str:
    if not isinstance(value, str):
        return UNAVAILABLE
    try:
        return timestamp(value).astimezone(IST).strftime("%d %b %Y · %H:%M:%S IST")
    except (ValueError, TypeError):
        return UNAVAILABLE


def latest_evidence(data: dict[str, Any]) -> str:
    candidates = [r.get("quote_timestamp") for r in data.get("market", [])]
    candidates += [r.get("execution_timestamp") for r in data.get("trades", [])]
    candidates += [data.get("analytics", {}).get("closing_at")]
    valid = [v for v in candidates if isinstance(v, str) and local_time(v) != UNAVAILABLE]
    return local_time(max(valid, key=timestamp)) if valid else UNAVAILABLE


def header(
    data: dict[str, Any] | None = None,
    *,
    period: str = "All Time",
    refresh: str = "15 seconds",
    session: str | None = None,
) -> None:
    st.html(
        '<header class="aegis-header"><div class="aegis-brand">'
        '<div class="aegis-monogram" aria-hidden="true">A</div><div>'
        "<h1>AEGIS <span>| PAPER TERMINAL</span></h1><p>LOCAL RESEARCH &amp; PORTFOLIO INTELLIGENCE</p></div></div>"
        '<div class="aegis-badges"><span class="aegis-badge purple">RESEARCH_ONLY</span>'
        '<span class="aegis-badge blue">SIMULATED PAPER</span>'
        '<span class="aegis-badge">LIVE BROKER ORDERS DISABLED</span></div></header>'
    )
    if data:
        account = data["account"]
        session_label = session or f"Account {account['id']}"
        st.html(
            '<div class="aegis-context">'
            f"<span><strong>Portfolio {text(account['id'])}</strong> · {text(account['name'])}</span>"
            f'<span class="aegis-session" title="{text(session_label)}">Session · {text(session_label)}</span>'
            f'<span class="aegis-badge blue">{text(period)} · IST</span>'
            f"<span>Last evidence · <strong>{text(latest_evidence(data))}</strong></span>"
            f"<span>Refresh · {text(refresh)}</span></div>"
        )


def system_states(data: dict[str, Any]) -> list[tuple[str, str, str]]:
    finance = data.get("finance", {})
    reconciliation = finance.get("reconciliation", UNAVAILABLE)
    market = data.get("market", [])
    freshness = (
        "STALE"
        if any(r.get("freshness") == "STALE" for r in market)
        else "PERSISTED FRESH"
        if market and all(r.get("freshness") == "FRESH" for r in market)
        else UNAVAILABLE
    )
    sources = data.get("source_imports", [])
    source = (
        "VERIFIED_PERMITTED"
        if sources and all(r.get("source_use_status") == "VERIFIED_PERMITTED" for r in sources)
        else "UNVERIFIED"
        if any(r.get("source_use_status") == "UNVERIFIED" for r in sources)
        else UNAVAILABLE
    )
    halted = bool(data.get("account", {}).get("halted"))
    return [
        (
            "Paper accounting",
            data.get("analytics", {}).get("status", UNAVAILABLE),
            "positive" if data.get("analytics", {}).get("status") == "VERIFIED" else "neutral",
        ),
        (
            "Reconciliation",
            reconciliation,
            "positive"
            if reconciliation == "RECONCILED"
            else "negative"
            if reconciliation == "FAILED"
            else "neutral",
        ),
        ("Runtime · persisted", data.get("runtime", {}).get("status") or UNAVAILABLE, "neutral"),
        ("Market observations", freshness, "warning" if freshness == "STALE" else "neutral"),
        (
            "Calendar",
            data.get("calendar_confidence", UNAVAILABLE),
            "warning" if data.get("calendar_confidence") == "CONFIGURED" else "neutral",
        ),
        ("Source-use declaration", source, "warning" if source == "UNVERIFIED" else "neutral"),
        (
            "Risk controls",
            "HALTED" if halted else "LIMITS RECORDED" if data.get("risk_limits") else UNAVAILABLE,
            "negative" if halted else "neutral",
        ),
        ("Live broker orders", "DISABLED", "neutral"),
    ]


def health_strip(data: dict[str, Any]) -> None:
    st.html(
        '<div class="aegis-health">'
        + "".join(
            f'<div class="aegis-health-item {tone}"><small>{text(label)}</small>'
            f'<strong title="{text(status)}"><i class="aegis-dot" aria-hidden="true"></i>{text(status).replace("_", "_<wbr>")}</strong></div>'
            for label, status, tone in system_states(data)
        )
        + "</div>"
    )


def _themed(chart: Any, height: int = 245) -> Any:
    return (
        chart.properties(height=height, background=PALETTE["card"])
        .configure_view(strokeOpacity=0)
        .configure_axis(
            labelColor=PALETTE["secondary"],
            titleColor=PALETTE["secondary"],
            gridColor=PALETTE["border"],
            gridOpacity=0.55,
            domain=False,
            labelFontSize=10,
            titleFontSize=10,
            titlePadding=12,
            labelPadding=8,
            tickSize=0,
        )
        .configure_legend(labelColor=PALETTE["secondary"], titleColor=PALETTE["secondary"])
        .configure(font="Segoe UI")
    )


def pnl_chart(
    rows: list[dict[str, Any]],
    *,
    bucket: str = "bucket",
    value: str = "realized_pnl",
    height: int = 230,
) -> Any | None:
    if not rows:
        return None
    values = [
        {
            "bucket": str(r[bucket]),
            "amount": float(Decimal(r[value])),
            "exact": formatted(r[value]),
            "direction": value_tone(r[value], directional=True),
        }
        for r in rows
    ]
    base = alt.Chart(alt.InlineData(values=values))
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, size=28).encode(
        x=alt.X("bucket:N", title=None, axis=alt.Axis(labelAngle=0, labelLimit=130)),
        y=alt.Y(
            "amount:Q",
            title="Simulated P&L · INR",
            stack=None,
            axis=alt.Axis(labelExpr="'₹' + format(datum.value, ',.2f')"),
        ),
        color=alt.Color(
            "direction:N",
            scale=alt.Scale(
                domain=["positive", "negative", "neutral"],
                range=[PALETTE["positive"], PALETTE["negative"], PALETTE["secondary"]],
            ),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("bucket:N", title="Period / attribution"),
            alt.Tooltip(
                "exact:N", title="Daily net P&L" if value == "net_pnl" else "Net realized P&L"
            ),
        ],
    )
    zero = (
        alt.Chart(alt.InlineData(values=[{"zero": 0}]))
        .mark_rule(color=PALETTE["secondary"], opacity=0.6)
        .encode(y="zero:Q")
    )
    return _themed(bars + zero, height)


def equity_chart(analytics: dict[str, Any], *, height: int = 260) -> Any | None:
    curve = analytics.get("curve", [])
    values = [
        {
            "time": row["timestamp"],
            "sequence": row["sequence"],
            "ist": local_time(row["timestamp"]),
            "equity": float(Decimal(row["equity"])),
            "exact": formatted(row["equity"]),
            "snapshot": row["snapshot_id"],
        }
        for row in curve
        if row["equity"] is not None
    ]
    if not values:
        return None
    base = alt.Chart(alt.InlineData(values=values)).encode(
        x=alt.X(
            "time:T",
            title="Observed time · UTC",
            scale=alt.Scale(type="utc"),
            axis=alt.Axis(format="%d %b %H:%M", tickCount=4, labelOverlap=True),
        ),
        y=alt.Y(
            "equity:Q",
            title="Portfolio equity · INR",
            stack=None,
            scale=alt.Scale(zero=False),
            axis=alt.Axis(labelExpr="'₹' + format(datum.value, ',.2f')"),
        ),
        order="sequence:Q",
        tooltip=[
            alt.Tooltip("ist:N", title="Observed at"),
            alt.Tooltip("exact:N", title="Equity"),
            alt.Tooltip("sequence:Q", title="Sequence"),
            alt.Tooltip("snapshot:N", title="Snapshot"),
        ],
    )
    if any(row["equity"] is None for row in curve):
        return _themed(base.mark_circle(color=PALETTE["blue"], size=50), height)
    area = base.mark_area(color=PALETTE["blue"], opacity=0.09, clip=True)
    line = base.mark_line(
        color=PALETTE["blue"], strokeWidth=2.5, point=alt.OverlayMarkDef(color="#38BDF8", size=28)
    )
    return _themed(area + line, height)


def drawdown_rows(analytics: dict[str, Any]) -> list[dict[str, Any]]:
    """Display projection only, gated by the existing verified drawdown metric."""
    if analytics.get("status") != "VERIFIED" or analytics.get("max_drawdown_pct") is None:
        return []
    with localcontext() as context:
        context.prec = 50
        peak = decimal(analytics.get("opening_equity"))
        if peak is None or peak <= 0:
            return []
        result = []
        for row in analytics.get("curve", []):
            equity = decimal(row["equity"])
            if equity is None:
                return []
            peak = max(peak, equity)
            drawdown = (equity - peak) / peak * 100
            result.append(
                {
                    "time": row["timestamp"],
                    "sequence": row["sequence"],
                    "drawdown": float(drawdown),
                    "exact": formatted(drawdown, "percent"),
                    "ist": local_time(row["timestamp"]),
                }
            )
        return result


def drawdown_chart(analytics: dict[str, Any]) -> Any | None:
    values = drawdown_rows(analytics)
    if not values:
        return None
    base = alt.Chart(alt.InlineData(values=values)).encode(
        x=alt.X(
            "time:T",
            title="Observed time · UTC",
            scale=alt.Scale(type="utc"),
            axis=alt.Axis(format="%d %b %H:%M", tickCount=4),
        ),
        y=alt.Y(
            "drawdown:Q",
            title="Drawdown from observed peak · %",
            stack=None,
            axis=alt.Axis(format=".4f"),
        ),
        order="sequence:Q",
        tooltip=[
            alt.Tooltip("ist:N", title="Observed at"),
            alt.Tooltip("exact:N", title="Drawdown"),
        ],
    )
    return _themed(
        base.mark_area(color=PALETTE["negative"], opacity=0.12)
        + base.mark_line(color=PALETTE["negative"], strokeWidth=2)
    )


MONEY_FIELDS = frozenset(
    {
        "reference_price",
        "fill_price",
        "modeled_fees",
        "mark_price",
        "eligible_mark_price",
        "average_entry_price",
        "market_value",
        "unrealized_gross_pnl",
        "realized_net_pnl",
        "remaining_entry_fees",
        "latest_persisted_price",
        "candle_close",
        "stop_loss",
        "target_price",
        "opening_equity",
        "closing_equity",
        "equity_change",
        "net_pnl",
        "price",
        "cash",
        "equity",
    }
)
LABELS = {
    "trading_symbol": "Symbol",
    "instrument_id": "Instrument",
    "execution_timestamp": "Executed · IST",
    "side": "Action",
    "quantity": "Qty",
    "reference_price": "Submitted / reference",
    "fill_price": "Simulated fill",
    "modeled_fees": "Modeled fees",
    "status": "Status",
    "strategy": "Strategy",
    "runtime_id": "Session",
    "state": "Decision",
    "timestamp": "Time · IST",
    "order_id": "Order ID",
    "event_id": "Event ID",
    "record_id": "Record ID",
    "kind": "Event kind",
    "reference": "Evidence reference",
}


def cell_style(value: object, column: str) -> str:
    raw = str(value)
    if raw == "BUY":
        return f"color: {PALETTE['positive']}; background-color: #14352D; font-weight: 600"
    if raw == "SELL":
        return f"color: {PALETTE['negative']}; background-color: #362333; font-weight: 600"
    if "pnl" in column or column in ("equity_change", "unrealized_return_pct", "return_pct"):
        tone = value_tone(value, directional=True)
        return f"color: {PALETTE.get(tone, PALETTE['secondary'])}"
    if raw in ("REJECTED", "BLOCKED", "FAILED", "HALTED"):
        return f"color: {PALETTE['negative']}"
    if raw in ("STALE", "UNVERIFIED", "CONFIGURED", "INCOMPLETE"):
        return f"color: {PALETTE['warning']}"
    if raw in ("VERIFIED", "RECONCILED", "FILLED", "COMPLETE"):
        return f"color: {PALETTE['positive']}"
    return ""


def styled_table(
    rows: list[dict[str, Any]],
    empty: str = "No persisted records in this scope.",
    *,
    max_height: int = 360,
) -> None:
    if not rows:
        st.info(empty)
        return
    # The presentation copy leaves authenticated projections and CSV exports untouched.
    frame = pd.DataFrame(rows).copy()
    # Numeric grid cells sort numerically and align right. This display conversion
    # performs no accounting; exports keep the untouched canonical Decimal text.
    for column in frame.columns:
        if column in MONEY_FIELDS or column.endswith("_pct"):
            frame[column] = frame[column].map(
                lambda value: float(parsed) if (parsed := decimal(value)) is not None else None
            )
    style = frame.style.format(na_rep=UNAVAILABLE)
    for column in frame.columns:
        style = style.map(lambda value, name=column: cell_style(value, name), subset=[column])
        if column in MONEY_FIELDS:
            style = style.format(lambda value: formatted(value), subset=[column])
            style = style.set_properties(subset=[column], **{"text-align": "right"})
        elif column.endswith("_pct"):
            style = style.format(lambda value: formatted(value, "percent"), subset=[column])
        elif column in (
            "timestamp",
            "execution_timestamp",
            "decision_timestamp",
            "quote_timestamp",
            "received_at",
            "observed_at",
        ):
            style = style.format(local_time, subset=[column])
    preferred = [
        key
        for key in (
            "execution_timestamp",
            "timestamp",
            "trading_symbol",
            "symbol",
            "side",
            "state",
            "quantity",
            "reference_price",
            "fill_price",
            "modeled_fees",
            "status",
            "strategy",
        )
        if key in frame
    ]
    widths = {
        "execution_timestamp": 190,
        "timestamp": 190,
        "quote_timestamp": 190,
        "received_at": 190,
        "trading_symbol": 80,
        "symbol": 80,
        "side": 70,
        "quantity": 55,
        "reference_price": 145,
        "fill_price": 115,
        "modeled_fees": 110,
        "status": 100,
        "strategy": 180,
        "source_use_status": 190,
    }
    config = {
        key: st.column_config.Column(
            LABELS.get(key, key.replace("_", " ").title()), width=widths.get(key, 140)
        )
        for key in frame.columns
    }
    for key in frame.columns:
        if key in MONEY_FIELDS or key.endswith("_pct"):
            config[key] = st.column_config.NumberColumn(
                LABELS.get(key, key.replace("_", " ").title()),
                width=widths.get(key, 140),
                format="₹%.2f" if key in MONEY_FIELDS else "%.4f%%",
            )
    st.dataframe(
        style,
        hide_index=True,
        width="stretch",
        height=min(max_height, 37 + 32 * len(frame)),
        row_height=32,
        column_order=preferred + [key for key in frame if key not in preferred],
        column_config=config,
    )


def plot(chart: Any | None, message: str) -> None:
    if chart is None:
        st.info(message)
    else:
        st.altair_chart(chart, width="stretch", theme=None)
