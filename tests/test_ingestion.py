"""
Tests for the ingestion layer — FRED, ECB, and BIS connectors and the shared
schema validators. All HTTP calls are mocked so the suite runs offline.
"""

import io
import zipfile
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from ingestion import bis_loader, ecb_connector, fred_connector, validators


# Test helpers
class _FakeResponse:
    """Minimal requests.Response stand-in for mocking HTTP calls."""

    def __init__(
        self, status_code: int = 200, json_data=None, text: str = "", content: bytes = b""
    ):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def _valid_series_df(series_id: str = "TEST") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "value": [1.0, 2.0, 3.0],
            "series_id": series_id,
        }
    )


def _wide_bis_csv() -> str:
    return (
        "FREQ,Series,2023,2023-Q2,2023-S1\n"
        "H,US_FX_SWAP,100.0,110.0,120.0\n"
        "H,DE_FX_SWAP,200.0,210.0,220.0\n"
    )


# --- fred_connector ---
def test_fred_fetch_series_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(ValueError, match="FRED_API_KEY"):
        fred_connector.fetch_series("DGS10", api_key=None)


def test_fred_fetch_series_success(monkeypatch):
    payload = {
        "observations": [
            {"date": "2024-01-01", "value": "4.5"},
            {"date": "2024-01-02", "value": "."},  # FRED's missing-value marker
            {"date": "2024-01-03", "value": "4.6"},
        ]
    }
    monkeypatch.setattr(
        fred_connector.requests, "get", Mock(return_value=_FakeResponse(json_data=payload))
    )

    df = fred_connector.fetch_series("DGS10", api_key="test_key")

    assert list(df.columns) == ["date", "value", "series_id"]
    assert len(df) == 2  # the "." observation is dropped
    assert df["series_id"].unique().tolist() == ["DGS10"]
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_fred_request_with_retry_recovers_after_transient_failure(monkeypatch):
    monkeypatch.setattr(fred_connector.time, "sleep", lambda _seconds: None)
    good_response = _FakeResponse(json_data={"observations": []})
    mock_get = Mock(side_effect=[requests.exceptions.ConnectionError("boom"), good_response])
    monkeypatch.setattr(fred_connector.requests, "get", mock_get)

    response = fred_connector._request_with_retry(
        fred_connector.FRED_BASE_URL, {"series_id": "DGS10"}
    )

    assert response is good_response
    assert mock_get.call_count == 2


def test_fred_request_with_retry_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr(fred_connector.time, "sleep", lambda _seconds: None)
    mock_get = Mock(side_effect=requests.exceptions.ConnectionError("boom"))
    monkeypatch.setattr(fred_connector.requests, "get", mock_get)

    with pytest.raises(requests.exceptions.ConnectionError):
        fred_connector._request_with_retry(fred_connector.FRED_BASE_URL, {"series_id": "DGS10"})

    assert mock_get.call_count == fred_connector.MAX_RETRIES


def test_fred_fetch_all_series_skips_failures(monkeypatch):
    def fake_fetch(series_id, **_kwargs):
        if series_id == "BAD":
            raise ValueError("boom")
        return _valid_series_df(series_id)

    monkeypatch.setattr(fred_connector, "fetch_series", fake_fetch)

    results = fred_connector.fetch_all_series(["DGS10", "BAD"])

    assert list(results.keys()) == ["DGS10"]


def test_fred_save_series_writes_csv(tmp_path):
    output_path = fred_connector.save_series(
        _valid_series_df("DGS10"), "DGS10", output_dir=tmp_path
    )

    assert output_path.exists()
    assert pd.read_csv(output_path).shape[0] == 3


# --- ecb_connector ---
def test_ecb_fetch_series_unknown_series_id_raises():
    with pytest.raises(ValueError, match="Unknown ECB series_id"):
        ecb_connector.fetch_series("NOT_A_SERIES")


def test_ecb_fetch_series_success(monkeypatch):
    csv_text = "TIME_PERIOD,OBS_VALUE\n2024-01-01,3.9\n2024-01-02,4.0\n"
    monkeypatch.setattr(
        ecb_connector.requests, "get", Mock(return_value=_FakeResponse(text=csv_text))
    )

    df = ecb_connector.fetch_series("EURIBOR_3M")

    assert list(df.columns) == ["date", "value", "series_id"]
    assert len(df) == 2
    assert df["series_id"].unique().tolist() == ["EURIBOR_3M"]


def test_ecb_validate_response_raises_without_obs_value():
    with pytest.raises(ValueError, match="Unexpected ECB response"):
        ecb_connector._validate_response(_FakeResponse(text="not a csv payload"))


def test_ecb_fetch_all_series_skips_failures(monkeypatch):
    def fake_fetch(series_id, **_kwargs):
        if series_id == "BAD":
            raise ValueError("boom")
        return _valid_series_df(series_id)

    monkeypatch.setattr(ecb_connector, "fetch_series", fake_fetch)

    results = ecb_connector.fetch_all_series(["EURIBOR_3M", "BAD"])

    assert list(results.keys()) == ["EURIBOR_3M"]


def test_ecb_save_series_writes_csv(tmp_path):
    output_path = ecb_connector.save_series(
        _valid_series_df("EURUSD_SPOT"), "EURUSD_SPOT", output_dir=tmp_path
    )

    assert output_path.exists()
    assert pd.read_csv(output_path).shape[0] == 3


# --- bis_loader ---
def test_bis_reshape_to_long_converts_periods_to_dates():
    raw = pd.read_csv(io.StringIO(_wide_bis_csv()))

    long_df = bis_loader._reshape_to_long(raw, "TEST_DATASET")

    assert list(long_df.columns) == ["date", "value", "series_id"]
    assert len(long_df) == 6
    assert set(long_df["series_id"]) == {"US_FX_SWAP", "DE_FX_SWAP"}
    assert pd.Timestamp("2023-01-01") in long_df["date"].values


def test_bis_reshape_to_long_missing_series_column_raises():
    raw = pd.DataFrame({"FREQ": ["H"], "2023": [100.0]})

    with pytest.raises(ValueError, match="'Series' identifier column"):
        bis_loader._reshape_to_long(raw, "TEST_DATASET")


def test_bis_reshape_to_long_no_period_columns_raises():
    raw = pd.DataFrame({"FREQ": ["H"], "Series": ["US_FX_SWAP"]})

    with pytest.raises(ValueError, match="No period-like columns"):
        bis_loader._reshape_to_long(raw, "TEST_DATASET")


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("2023", "2023-01-01"),
        ("2023-Q2", "2023-04-01"),
        ("2023-S1", "2023-01-01"),
        ("2023-S2", "2023-07-01"),
    ],
)
def test_bis_period_to_date(period, expected):
    assert bis_loader._period_to_date(period) == pd.Timestamp(expected)


def test_bis_extract_csv_handles_plain_text():
    assert bis_loader._extract_csv(b"a,b\n1,2\n") == "a,b\n1,2\n"


def test_bis_extract_csv_handles_zip_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", "a,b\n1,2\n")

    assert bis_loader._extract_csv(buffer.getvalue()) == "a,b\n1,2\n"


def test_bis_fetch_dataset_unknown_id_raises():
    with pytest.raises(ValueError, match="Unknown BIS dataset_id"):
        bis_loader.fetch_dataset("NOT_A_DATASET")


def test_bis_fetch_dataset_success(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", _wide_bis_csv())
    monkeypatch.setattr(
        bis_loader.requests, "get", Mock(return_value=_FakeResponse(content=buffer.getvalue()))
    )

    df = bis_loader.fetch_dataset("BIS_OTC_DERIV")

    assert list(df.columns) == ["date", "value", "series_id"]
    assert len(df) == 6


def test_bis_save_dataset_writes_csv(tmp_path):
    output_path = bis_loader.save_dataset(
        _valid_series_df("US_FX_SWAP"), "BIS_OTC_DERIV", output_dir=tmp_path
    )

    assert output_path.exists()
    assert pd.read_csv(output_path).shape[0] == 3


# --- validators ---
def test_validate_schema_passes_for_clean_dataframe():
    assert validators.validate_schema(_valid_series_df(), "TEST") is True


def test_validate_schema_missing_column_raises():
    df = _valid_series_df().drop(columns=["value"])
    with pytest.raises(ValueError, match="missing required columns"):
        validators.validate_schema(df, "TEST")


def test_validate_schema_non_numeric_value_raises():
    df = _valid_series_df()
    df["value"] = df["value"].astype(str)
    with pytest.raises(ValueError, match="not numeric"):
        validators.validate_schema(df, "TEST")


def test_validate_schema_non_datetime_date_raises():
    df = _valid_series_df()
    df["date"] = df["date"].astype(str)
    with pytest.raises(ValueError, match="not datetime"):
        validators.validate_schema(df, "TEST")


def test_validate_schema_nulls_raise():
    df = _valid_series_df()
    df.loc[0, "value"] = None
    with pytest.raises(ValueError, match="null values"):
        validators.validate_schema(df, "TEST")


def test_validate_schema_unsorted_dates_raise():
    df = _valid_series_df().iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="not sorted"):
        validators.validate_schema(df, "TEST")


def test_validate_all_marks_failures_false():
    series = {
        "GOOD": _valid_series_df("GOOD"),
        "BAD": _valid_series_df("BAD").drop(columns=["date"]),
    }

    results = validators.validate_all(series)

    assert results == {"GOOD": True, "BAD": False}
