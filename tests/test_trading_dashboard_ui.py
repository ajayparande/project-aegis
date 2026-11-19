"""Presentation contracts; existing accounting models remain the only authority."""

import hashlib
import json
import socket
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit import config
from streamlit.testing.v1 import AppTest

from aegis import trading_dashboard
from aegis import trading_dashboard_ui as ui
from aegis.trading_dashboard_data import PERIODS, csv_bytes
from tests.test_trading_dashboard import session


@pytest.mark.parametrize(
    "value,kind,expected",
    [
        ("9999.3", "money", "₹9,999.30"),
        ("-0.7", "money", "₹-0.70"),
        ("0", "money", "₹0.00"),
        ("-0.000", "money", "₹0.00"),
        ("-0.007", "percent", "-0.0070%"),
        ("12", "count", "12"),
        ("1.25", "ratio", "1.250"),
        (None, "money", "UNAVAILABLE"),
        ("NaN", "money", "UNAVAILABLE"),
        ("Infinity", "money", "UNAVAILABLE"),
        ("missing", "money", "UNAVAILABLE"),
    ],
)
def test_kpi_formatting_is_decimal_safe_and_missing_is_not_zero(value, kind, expected):
    assert ui.formatted(value, kind) == expected


@pytest.mark.parametrize(
    "value,tone", [("10", "positive"), ("-10", "negative"), ("0", "neutral"), (None, "unavailable")]
)
def test_directional_cards_have_positive_negative_zero_and_unavailable_states(value, tone):
    html = ui.kpi_html("Net P&L", value, directional=True)
    assert f'class="aegis-kpi {tone}"' in html
    assert ui.formatted(value) in html
    assert "↑" not in html and "↓" not in html
    assert ui.value_tone(value, directional=True) == tone


def test_cash_and_costs_do_not_imply_positive_performance():
    assert ui.value_tone("10000") == "neutral"
    assert 'class="aegis-kpi accent"' in ui.kpi_html("Equity", "10000", accent=True)
    assert 'class="aegis-kpi unavailable"' in ui.kpi_html("Equity", None, accent=True)


def test_rounded_negative_zero_is_display_only_across_financial_formats():
    for kind, value, expected in (
        ("money", "-0.001", "₹0.00"),
        ("percent", "-0.00001", "0.0000%"),
        ("ratio", "-0.0001", "0.000"),
    ):
        assert ui.formatted(value, kind) == expected
        assert ui.decimal(value) < 0  # Source sign and accounting value are unchanged.


def test_missing_source_status_is_unavailable_not_an_unverified_declaration():
    for sources in (
        [{}],
        [{"source_use_status": None}],
        [{"source_use_status": "VERIFIED_PERMITTED"}, {}],
    ):
        states = {
            label: status for label, status, _ in ui.system_states({"source_imports": sources})
        }
        assert states["Source-use declaration"] == "UNAVAILABLE"


def test_long_escaped_header_text_has_scoped_wrapping():
    assert ".aegis-context > span {min-width:0;overflow-wrap:anywhere}" in ui.STYLES
    assert '.st-key-aegis-terminal [role="alert"] {overflow-wrap:anywhere}' in ui.STYLES
    assert "<script>" not in ui.text("SYMBOL" * 200 + "<script>bad()</script>")


def test_numeric_table_presentation_sorts_numerically_without_changing_export(monkeypatch):
    rows = [
        {"side": "BUY", "fill_price": "99.80", "modeled_fees": None, "realized_net_pnl": "-0.7"}
    ]
    exported = csv_bytes(rows)
    render = Mock()
    monkeypatch.setattr(ui.st, "dataframe", render)
    ui.styled_table(rows)
    presentation = render.call_args.args[0].data
    assert presentation["fill_price"].dtype.kind == "f"
    assert presentation["fill_price"].iloc[0] == 99.8
    assert render.call_args.kwargs["column_config"]["fill_price"]["type_config"]["type"] == "number"
    assert rows[0]["fill_price"] == "99.80" and rows[0]["modeled_fees"] is None
    assert csv_bytes(rows) == exported


def test_theme_uses_requested_palette_and_supported_launch_options():
    assert set(ui.PALETTE.values()) == {
        "#0B1120",
        "#111C30",
        "#17243A",
        "#293B55",
        "#F1F5F9",
        "#94A3B8",
        "#3B82F6",
        "#8B5CF6",
        "#22C55E",
        "#F87171",
        "#F59E0B",
    }
    options = config.get_config_options()
    assert all("theme." + key in options for key in ui.THEME)
    assert "--theme.base=dark" in ui.theme_arguments()
    assert "--theme.backgroundColor=#0B1120" in ui.theme_arguments()
    assert "--theme.sidebar.backgroundColor=#111C30" in ui.theme_arguments()
    assert "st-key-aegis-terminal" in ui.STYLES and 'aria-selected="true"' in ui.STYLES
    assert "@media(max-width:480px)" in ui.STYLES


def test_dynamic_html_is_escaped_and_cannot_inject_css_or_javascript():
    attack = '"><img src=x onerror="alert(1)"></style><script>bad()</script>'
    result = ui.kpi_html(attack, "1", note=attack)
    assert "<script>" not in result and "<img" not in result and "</style>" not in result
    assert "&lt;script&gt;" in result and "&quot;" in result
    assert "<style" not in result
    assert ui.STYLES.count("<style>") == ui.STYLES.count("</style>") == 1
    assert "st-emotion" not in ui.STYLES and ".css-" not in ui.STYLES
    assert "url(" not in ui.STYLES


def test_system_status_never_promotes_missing_or_stale_evidence_to_healthy():
    empty = ui.system_states({})
    assert not any(tone == "positive" for _, _, tone in empty)
    assert all(state != "HEALTHY" for _, state, _ in empty)
    assert next(s for label, s, _ in empty if label == "Market observations") == "UNAVAILABLE"
    unknown = ui.system_states({"market": [{}]})
    assert next(s for label, s, _ in unknown if label == "Market observations") == "UNAVAILABLE"
    data = {
        "market": [{"freshness": "STALE"}],
        "calendar_confidence": "CONFIGURED",
        "source_imports": [{"source_use_status": "UNVERIFIED"}],
        "runtime": {"status": "RUNNING"},
        "finance": {"reconciliation": "FAILED"},
        "account": {"halted": True},
    }
    states = {label: (state, tone) for label, state, tone in ui.system_states(data)}
    assert states["Market observations"] == ("STALE", "warning")
    assert states["Calendar"] == ("CONFIGURED", "warning")
    assert states["Runtime · persisted"] == ("RUNNING", "neutral")
    assert states["Reconciliation"] == ("FAILED", "negative")
    assert states["Risk controls"] == ("HALTED", "negative")


def test_charts_handle_empty_or_insufficient_evidence_without_fake_points():
    assert ui.pnl_chart([]) is None and ui.equity_chart({}) is None
    assert ui.drawdown_chart({"status": "UNAVAILABLE", "max_drawdown_pct": None}) is None
    assert ui.drawdown_rows({"status": "VERIFIED", "max_drawdown_pct": None}) == []


def test_pnl_chart_uses_sign_colors_zero_baseline_and_valid_currency_format():
    rows = [{"bucket": str(i), "realized_pnl": value} for i, value in enumerate(("1", "-2", "0"))]
    original = json.dumps(rows)
    chart = ui.pnl_chart(rows).to_dict()
    bars, zero = chart["layer"]
    color = bars["encoding"]["color"]["scale"]
    assert color["domain"] == ["positive", "negative", "neutral"]
    assert color["range"] == ["#22C55E", "#F87171", "#94A3B8"]
    assert zero["mark"]["type"] == "rule"
    assert "labelExpr" in bars["encoding"]["y"]["axis"]
    assert bars["encoding"]["y"]["stack"] is None
    assert "₹,.2f" not in json.dumps(chart, ensure_ascii=False)
    assert json.dumps(rows) == original


def test_observed_drawdown_visual_uses_verified_scope_and_negative_labels():
    analytics = {
        "status": "VERIFIED",
        "max_drawdown_pct": "10",
        "opening_equity": "100",
        "curve": [
            {"timestamp": "2026-01-05T04:00:00Z", "equity": "100", "sequence": 1},
            {"timestamp": "2026-01-05T04:01:00Z", "equity": "90", "sequence": 2},
        ],
    }
    original = json.dumps(analytics)
    points = ui.drawdown_rows(analytics)
    assert [p["drawdown"] for p in points] == [0, -10]
    chart = ui.drawdown_chart(analytics).to_dict()
    assert chart["layer"][0]["encoding"]["y"]["stack"] is None
    assert chart["layer"][1]["mark"]["color"] == "#F87171"
    assert json.dumps(analytics) == original
    analytics["curve"][1]["equity"] = None
    assert ui.drawdown_chart(analytics) is None


def test_equity_gaps_use_unconnected_points_and_actual_timestamps():
    curve = [
        {"timestamp": "2026-01-05T04:00:00Z", "equity": "100", "sequence": 1, "snapshot_id": "one"},
        {
            "timestamp": "2026-01-05T04:01:00Z",
            "equity": None,
            "sequence": 2,
            "snapshot_id": "missing",
        },
    ]
    spec = ui.equity_chart({"curve": curve}).to_dict()
    assert spec["mark"]["type"] == "circle"
    assert spec["encoding"]["x"]["scale"]["type"] == "utc"
    assert ui.local_time(curve[0]["timestamp"]).endswith("09:30:00 IST")


def test_launcher_theme_is_local_and_does_not_initialize_a_database(tmp_path, monkeypatch):
    from streamlit.web import cli

    path = tmp_path / "missing.db"
    monkeypatch.setattr(
        sys, "argv", ["aegis.trading_dashboard", "--database", str(path), "--port", "8519"]
    )
    monkeypatch.setattr(cli, "main", Mock(return_value=0))
    assert trading_dashboard.main() == 0
    assert "--theme.base=dark" in sys.argv and "--server.address=127.0.0.1" in sys.argv
    assert "--client.toolbarMode=minimal" in sys.argv
    assert not path.exists()


def test_redesigned_pages_filters_html_and_refresh_are_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "unused.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "unused.log"))
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        raise AssertionError("No external networking")

    monkeypatch.setattr(socket.socket, "connect", local_only)
    monkeypatch.setattr(
        "requests.Session.request", Mock(side_effect=AssertionError("No broker HTTP"))
    )
    db, _, _ = session(tmp_path)
    monkeypatch.setenv("AEGIS_DASHBOARD_DATABASE_PATH", str(db))
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src/aegis/trading_dashboard.py")).run(
        timeout=20
    )
    assert not app.exception
    assert [tab.label for tab in app.tabs] == list(trading_dashboard.PAGES)
    options = next(s for s in app.selectbox if s.label == "Date range · Asia/Kolkata")
    assert options.options == list(PERIODS)
    markup = "\n".join(element.proto.body for element in app.get("html"))
    assert "aegis-kpis" in markup and "₹9,999.30" in markup and "₹-0.70" in markup
    assert "LIVE BROKER ORDERS DISABLED" in markup and "All Time · IST" in markup
    assert all(not element.proto.unsafe_allow_javascript for element in app.get("html"))
    options.set_value("Custom Date Range").run()
    assert {field.label for field in app.date_input} == {"Start date", "End date"}
    assert not app.exception
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    # Export projections remain independent of display styling.
    rows = [{"side": "BUY", "fill_price": "100.2"}]
    export = csv_bytes(rows)
    ui.cell_style(rows[0]["side"], "side")
    assert csv_bytes(rows) == export and rows[0]["fill_price"] == "100.2"
