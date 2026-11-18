"""Synthetic tests for local NSE and Upstox source conversion."""

import gzip
import json
from datetime import date

import pytest

from aegis.providers.universe_sources import convert_offline_sources


def _files(tmp_path):
    csv_path = tmp_path / "nifty50.csv"
    gz_path = tmp_path / "nse.json.gz"
    csv_path.write_text("Company Name,Symbol\nMahindra,M&M\nReliance,RELIANCE\n", encoding="utf-8")
    records = [
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "trading_symbol": "M&M",
            "instrument_key": "NSE_EQ|INE101A01026",
        },
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "trading_symbol": "RELIANCE",
            "instrument_key": "NSE_EQ|INE002A01018",
        },
    ]
    gz_path.write_bytes(gzip.compress(json.dumps(records).encode()))
    return csv_path, gz_path


def _convert(tmp_path):
    csv_path, gz_path = _files(tmp_path)
    return convert_offline_sources(
        nse_csv=csv_path,
        upstox_gzip=gz_path,
        nse_observed_on=date(2026, 11, 18),
        upstox_observed_on=date(2026, 11, 18),
    )


def test_valid_sources_include_ampersand_symbol(tmp_path):
    master, membership = _convert(tmp_path)
    assert len(master["members"]) == 2
    assert membership["members"][0]["symbol"] == "M&M"
    assert master["source_reference"].startswith("upstox-nse-json-gz:sha256:")


def test_missing_stock_fails_closed(tmp_path):
    csv_path, gz_path = _files(tmp_path)
    csv_path.write_text("Symbol\nUNKNOWN\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unresolved"):
        convert_offline_sources(
            nse_csv=csv_path,
            upstox_gzip=gz_path,
            nse_observed_on=date(2026, 11, 18),
            upstox_observed_on=date(2026, 11, 18),
        )


def test_duplicate_nse_stock_rejected(tmp_path):
    csv_path, gz_path = _files(tmp_path)
    csv_path.write_text("Symbol\nRELIANCE\nRELIANCE\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        convert_offline_sources(
            nse_csv=csv_path,
            upstox_gzip=gz_path,
            nse_observed_on=date(2026, 11, 18),
            upstox_observed_on=date(2026, 11, 18),
        )


def test_invalid_gzip_rejected(tmp_path):
    csv_path, gz_path = _files(tmp_path)
    gz_path.write_bytes(b"invalid-gzip")
    with pytest.raises(ValueError, match="compressed"):
        convert_offline_sources(
            nse_csv=csv_path,
            upstox_gzip=gz_path,
            nse_observed_on=date(2026, 11, 18),
            upstox_observed_on=date(2026, 11, 18),
        )
