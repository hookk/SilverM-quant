"""
tests/evolution/test_phase1.py — Phase 1 Unit Tests
=====================================================

Tests for:
  • BaseSignal ABC enforcement and contract validation
  • IndicatorsLib output shape/dtype/NaN handling
  • DataPreparer registry loading and date split resolution
  • SignalRegistry registration, retrieval, listing, singleton

All tests use synthetic data (np.random.seed for reproducibility).
No DuckDB dependency for unit tests — DataPreparer DB calls are mocked.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Make evolution importable from tests dir ──────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def price_df():
    """Synthetic OHLCV DataFrame — 252 trading days, seeded for reproducibility."""
    np.random.seed(42)
    n = 252
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high  = close + np.abs(np.random.randn(n) * 0.3)
    low   = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.2
    vol   = np.random.randint(100_000, 5_000_000, size=n).astype(float)

    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": vol,
    }, index=dates)


@pytest.fixture(autouse=True)
def clear_registry():
    """Reset the singleton registry before each test to avoid pollution."""
    from evolution.signal_registry import registry
    registry.clear()
    yield
    registry.clear()


# ═════════════════════════════════════════════════════════════════════════════
# §1  BaseSignal tests
# ═════════════════════════════════════════════════════════════════════════════

class TestBaseSignal:

    def test_cannot_instantiate_abc_directly(self):
        """BaseSignal is abstract — direct instantiation must raise."""
        from evolution.base_signal import BaseSignal
        with pytest.raises(TypeError):
            BaseSignal()

    def test_subclass_missing_define_params_raises(self):
        """Subclass missing define_params must fail to instantiate."""
        from evolution.base_signal import BaseSignal
        with pytest.raises(TypeError):
            class Bad(BaseSignal):
                def calculate(self, df, params):
                    return pd.Series(0.0, index=df.index)
            Bad()

    def test_subclass_missing_calculate_raises(self):
        """Subclass missing calculate must fail to instantiate."""
        from evolution.base_signal import BaseSignal
        with pytest.raises(TypeError):
            class Bad(BaseSignal):
                def define_params(self, trial):
                    return {}
            Bad()

    def test_valid_subclass_instantiates(self):
        """A complete subclass instantiates without error."""
        from evolution.base_signal import BaseSignal
        class Good(BaseSignal):
            def define_params(self, trial):
                return {"window": trial.suggest_int("window", 5, 20)}
            def calculate(self, df, params):
                return df["close"].rolling(params["window"]).mean() - df["close"]
        sig = Good()
        assert isinstance(sig, BaseSignal)

    def test_default_name_is_snake_case(self):
        """Default name property converts CamelCase to snake_case."""
        from evolution.base_signal import BaseSignal
        class MyMomentumSignal(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p): return pd.Series(0.0, index=df.index)
        sig = MyMomentumSignal()
        assert sig.name == "my_momentum"

    def test_calculate_returns_correct_length(self, price_df):
        """calculate() output length must match input df length."""
        from evolution.base_signal import BaseSignal
        class Good(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p):
                return pd.Series(0.0, index=df.index)
        sig = Good()
        result = sig._safe_calculate(price_df, {})
        assert len(result) == len(price_df)

    def test_contract_fails_on_wrong_length(self, price_df):
        """_safe_calculate raises SignalContractError on wrong length."""
        from evolution.base_signal import BaseSignal, SignalContractError
        class Bad(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p):
                return pd.Series([1.0, 2.0])  # wrong length
        sig = Bad()
        with pytest.raises(SignalContractError, match="length"):
            sig._safe_calculate(price_df, {})

    def test_contract_fails_on_wrong_type(self, price_df):
        """_safe_calculate raises SignalContractError if not a Series."""
        from evolution.base_signal import BaseSignal, SignalContractError
        class Bad(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p):
                return np.zeros(len(df))   # np.array not pd.Series
        sig = Bad()
        with pytest.raises(SignalContractError, match="pd.Series"):
            sig._safe_calculate(price_df, {})

    def test_contract_fails_on_wrong_index(self, price_df):
        """_safe_calculate raises if index doesn't align."""
        from evolution.base_signal import BaseSignal, SignalContractError
        class Bad(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p):
                return pd.Series(0.0, index=pd.RangeIndex(len(df)))  # wrong index
        sig = Bad()
        with pytest.raises(SignalContractError, match="index"):
            sig._safe_calculate(price_df, {})

    def test_output_is_float64(self, price_df):
        """_safe_calculate coerces output to float64."""
        from evolution.base_signal import BaseSignal
        class IntSig(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p):
                return pd.Series(1, index=df.index, dtype=int)  # int → should coerce
        sig = IntSig()
        result = sig._safe_calculate(price_df, {})
        assert result.dtype == np.float64

    def test_default_params_uses_mock_trial(self):
        """default_params() returns a concrete dict without Optuna."""
        from evolution.base_signal import BaseSignal
        class MySig(BaseSignal):
            def define_params(self, t):
                return {
                    "w": t.suggest_int("w", 5, 15),
                    "k": t.suggest_float("k", 0.0, 1.0),
                    "m": t.suggest_categorical("m", ["a", "b"]),
                }
            def calculate(self, df, p): return pd.Series(0.0, index=df.index)
        sig = MySig()
        params = sig.default_params()
        assert isinstance(params, dict)
        assert params["w"] == 10       # midpoint of 5..15
        assert params["k"] == pytest.approx(0.5)
        assert params["m"] == "a"      # first choice


# ═════════════════════════════════════════════════════════════════════════════
# §2  IndicatorsLib tests
# ═════════════════════════════════════════════════════════════════════════════

class TestIndicatorsLib:
    """
    Test the pure-numpy fallback implementations.
    These tests pass regardless of TA-Lib installation.
    """

    @pytest.fixture(autouse=True)
    def arrays(self, price_df):
        self.close  = price_df["close"].values
        self.high   = price_df["high"].values
        self.low    = price_df["low"].values
        self.volume = price_df["volume"].values
        self.n      = len(self.close)

    def test_ma_correct_shape(self):
        from evolution.indicators_lib import ma
        result = ma(self.close, period=20)
        assert result.shape == (self.n,)
        assert result.dtype == np.float64

    def test_ma_first_bars_nan(self):
        from evolution.indicators_lib import ma
        result = ma(self.close, period=20)
        assert np.all(np.isnan(result[:19]))
        assert not np.isnan(result[19])

    def test_ma_known_value(self):
        """MA of a constant array should equal that constant."""
        from evolution.indicators_lib import ma
        arr = np.ones(50, dtype=np.float64) * 7.0
        result = ma(arr, period=10)
        assert result[9] == pytest.approx(7.0)
        assert result[49] == pytest.approx(7.0)

    def test_ema_correct_shape(self):
        from evolution.indicators_lib import ema
        result = ema(self.close, period=20)
        assert result.shape == (self.n,)
        assert result.dtype == np.float64

    def test_ema_responds_faster_than_ma(self):
        """EMA should react faster than MA to a sudden jump."""
        from evolution.indicators_lib import ma, ema
        arr = np.concatenate([np.ones(50) * 10, np.ones(50) * 20])
        ma_arr  = ma(arr, period=10)
        ema_arr = ema(arr, period=10)
        # At bar 55 (5 bars after jump), EMA should be closer to 20 than MA
        assert abs(ema_arr[55] - 20) < abs(ma_arr[55] - 20)

    def test_rsi_range(self):
        from evolution.indicators_lib import rsi
        result = rsi(self.close, period=14)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0), "RSI must be >= 0"
        assert np.all(valid <= 100), "RSI must be <= 100"

    def test_rsi_constant_input(self):
        """RSI on constant prices should not produce extreme values."""
        from evolution.indicators_lib import rsi
        arr = np.ones(50, dtype=np.float64) * 100.0
        result = rsi(arr, period=14)
        valid = result[~np.isnan(result)]
        # No gain and no loss → undefined; just check no values out of range
        assert np.all((valid >= 0) & (valid <= 100))

    def test_bollinger_returns_three_arrays(self):
        from evolution.indicators_lib import bollinger
        upper, mid, lower = bollinger(self.close, period=20, num_std=2.0)
        assert upper.shape == (self.n,)
        assert mid.shape   == (self.n,)
        assert lower.shape == (self.n,)

    def test_bollinger_ordering(self):
        """upper >= mid >= lower for all valid bars."""
        from evolution.indicators_lib import bollinger
        upper, mid, lower = bollinger(self.close, period=20)
        mask = ~(np.isnan(upper) | np.isnan(mid) | np.isnan(lower))
        assert np.all(upper[mask] >= mid[mask])
        assert np.all(mid[mask] >= lower[mask])

    def test_atr_non_negative(self):
        """ATR should always be >= 0."""
        from evolution.indicators_lib import atr
        result = atr(self.high, self.low, self.close, period=14)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0)

    def test_macd_returns_three_arrays(self):
        from evolution.indicators_lib import macd
        m, s, h = macd(self.close)
        assert m.shape == (self.n,)
        assert s.shape == (self.n,)
        assert h.shape == (self.n,)

    def test_macd_histogram_equals_diff(self):
        """Histogram = MACD line - Signal line."""
        from evolution.indicators_lib import macd
        m, s, h = macd(self.close)
        mask = ~(np.isnan(m) | np.isnan(s) | np.isnan(h))
        np.testing.assert_allclose(h[mask], (m - s)[mask], atol=1e-9)

    def test_kdj_returns_three_arrays(self):
        from evolution.indicators_lib import kdj
        k, d, j = kdj(self.high, self.low, self.close)
        assert k.shape == (self.n,)
        assert d.shape == (self.n,)
        assert j.shape == (self.n,)

    def test_obv_monotone_on_rising_prices(self):
        """OBV should be non-decreasing on strictly rising prices."""
        from evolution.indicators_lib import obv
        n = 50
        close  = np.arange(1, n + 1, dtype=float)
        volume = np.ones(n) * 1000.0
        result = obv(close, volume)
        diffs = np.diff(result)
        assert np.all(diffs >= 0)

    def test_pct_change_shape_and_nan(self):
        from evolution.indicators_lib import pct_change
        result = pct_change(self.close, period=1)
        assert result.shape == (self.n,)
        assert np.isnan(result[0])
        assert not np.isnan(result[1])

    def test_log_return_close_to_pct_change_for_small_moves(self):
        """log(1+r) ≈ r for small r."""
        from evolution.indicators_lib import pct_change, log_return
        pc  = pct_change(self.close)
        lr  = log_return(self.close)
        valid = ~(np.isnan(pc) | np.isnan(lr))
        # Relative error < 1% for daily-sized moves
        rel_err = np.abs(lr[valid] - pc[valid]) / (np.abs(pc[valid]) + 1e-9)
        assert np.mean(rel_err) < 0.01


# ═════════════════════════════════════════════════════════════════════════════
# §3  DataPreparer tests (mocked DB)
# ═════════════════════════════════════════════════════════════════════════════

class TestDataPreparer:

    def test_load_registry_parses_splits(self, tmp_path):
        """DataPreparer loads data_registry.yaml and exposes split dates."""
        from evolution.data_preparer import DataPreparer, Split

        # Use the real registry file
        real_registry = Path(__file__).parent.parent.parent / "evolution" / "data_registry.yaml"
        if not real_registry.exists():
            pytest.skip("data_registry.yaml not found")

        dp = DataPreparer(registry_path=real_registry, db_path=tmp_path / "fake.duckdb")
        reg = dp.registry()

        assert "splits" in reg
        assert "train" in reg["splits"]
        assert "valid" in reg["splits"]
        assert "test"  in reg["splits"]

    def test_split_dates_train(self, tmp_path):
        from evolution.data_preparer import DataPreparer, Split
        real_registry = Path(__file__).parent.parent.parent / "evolution" / "data_registry.yaml"
        if not real_registry.exists():
            pytest.skip("data_registry.yaml not found")

        dp = DataPreparer(registry_path=real_registry, db_path=tmp_path / "fake.duckdb")
        start, end = dp.split_dates(Split.TRAIN)
        assert start == "2018-01-01"
        assert end   == "2023-12-31"

    def test_split_dates_valid(self, tmp_path):
        from evolution.data_preparer import DataPreparer, Split
        real_registry = Path(__file__).parent.parent.parent / "evolution" / "data_registry.yaml"
        if not real_registry.exists():
            pytest.skip("data_registry.yaml not found")

        dp = DataPreparer(registry_path=real_registry, db_path=tmp_path / "fake.duckdb")
        start, end = dp.split_dates(Split.VALID)
        assert start == "2024-01-01"
        assert end   == "2025-09-30"

    def test_split_dates_test_end_is_today_or_configured(self, tmp_path):
        from evolution.data_preparer import DataPreparer, Split
        from datetime import date
        real_registry = Path(__file__).parent.parent.parent / "evolution" / "data_registry.yaml"
        if not real_registry.exists():
            pytest.skip("data_registry.yaml not found")

        dp = DataPreparer(registry_path=real_registry, db_path=tmp_path / "fake.duckdb")
        start, end = dp.split_dates(Split.TEST)
        assert start == "2025-10-01"
        assert end == date.today().isoformat()  # null in yaml → today

    def test_list_datasets_returns_all(self, tmp_path):
        from evolution.data_preparer import DataPreparer
        real_registry = Path(__file__).parent.parent.parent / "evolution" / "data_registry.yaml"
        if not real_registry.exists():
            pytest.skip("data_registry.yaml not found")

        dp = DataPreparer(registry_path=real_registry, db_path=tmp_path / "fake.duckdb")
        datasets = dp.list_datasets()
        names = [d["name"] for d in datasets]
        assert "price_daily" in names

    def test_prepare_raises_if_db_missing(self, tmp_path):
        from evolution.data_preparer import DataPreparer, DataPreparerError, Split
        real_registry = Path(__file__).parent.parent.parent / "evolution" / "data_registry.yaml"
        if not real_registry.exists():
            pytest.skip("data_registry.yaml not found")

        dp = DataPreparer(
            registry_path=real_registry,
            db_path=tmp_path / "nonexistent.duckdb"
        )
        with pytest.raises(DataPreparerError, match="not found"):
            dp.prepare(split=Split.TRAIN, max_stocks=1)


# ═════════════════════════════════════════════════════════════════════════════
# §4  SignalRegistry tests
# ═════════════════════════════════════════════════════════════════════════════

class TestSignalRegistry:

    def _make_signal(self, name="test_signal"):
        from evolution.base_signal import BaseSignal
        class _Sig(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p): return pd.Series(0.0, index=df.index)
        _Sig.__name__ = name.replace("_", " ").title().replace(" ", "") + "Signal"
        return _Sig

    def test_singleton_is_same_object(self):
        from evolution.signal_registry import SignalRegistry
        a = SignalRegistry()
        b = SignalRegistry()
        assert a is b

    def test_module_registry_is_singleton(self):
        from evolution.signal_registry import registry, SignalRegistry
        assert registry is SignalRegistry()

    def test_register_and_get(self):
        from evolution.signal_registry import registry
        cls = self._make_signal("my_sig")
        registry.register_cls("my_sig", cls, source="evolved")
        retrieved = registry.get("my_sig")
        assert retrieved is cls

    def test_register_duplicate_raises(self):
        from evolution.signal_registry import registry
        cls = self._make_signal("dup_sig")
        registry.register_cls("dup_sig", cls, source="evolved")
        with pytest.raises(ValueError, match="already registered"):
            registry.register_cls("dup_sig", cls, source="evolved")

    def test_register_duplicate_overwrite(self):
        from evolution.signal_registry import registry
        from evolution.base_signal import BaseSignal
        cls1 = self._make_signal("over_sig")
        class cls2(BaseSignal):
            def define_params(self, t): return {"v": 2}
            def calculate(self, df, p): return pd.Series(1.0, index=df.index)
        registry.register_cls("over_sig", cls1)
        registry.register_cls("over_sig", cls2, overwrite=True)
        assert registry.get("over_sig") is cls2

    def test_get_missing_raises_key_error(self):
        from evolution.signal_registry import registry
        with pytest.raises(KeyError, match="not found"):
            registry.get("does_not_exist")

    def test_register_non_basesignal_raises(self):
        from evolution.signal_registry import registry
        with pytest.raises(TypeError, match="BaseSignal subclass"):
            registry.register_cls("bad", dict)  # dict is not a BaseSignal

    def test_list_all_sorted(self):
        from evolution.signal_registry import registry
        cls = self._make_signal()
        registry.register_cls("z_sig", cls, source="evolved")
        registry.register_cls("a_sig", cls, source="adapted")
        names = [e["name"] for e in registry.list_all()]
        # sorted by (source, name): adapted < evolved alphabetically
        adapted_names = [e["name"] for e in registry.list_all() if e["source"] == "adapted"]
        evolved_names = [e["name"] for e in registry.list_all() if e["source"] == "evolved"]
        assert adapted_names == sorted(adapted_names)
        assert evolved_names == sorted(evolved_names)

    def test_list_by_source(self):
        from evolution.signal_registry import registry
        cls = self._make_signal()
        registry.register_cls("s_adapted", cls, source="adapted")
        registry.register_cls("s_evolved", cls, source="evolved")
        adapted = registry.list_by_source("adapted")
        evolved = registry.list_by_source("evolved")
        assert all(e["source"] == "adapted" for e in adapted)
        assert all(e["source"] == "evolved" for e in evolved)

    def test_contains(self):
        from evolution.signal_registry import registry
        cls = self._make_signal()
        registry.register_cls("exists_sig", cls)
        assert "exists_sig" in registry
        assert "not_exists" not in registry

    def test_decorator_registration(self):
        from evolution.signal_registry import registry
        from evolution.base_signal import BaseSignal

        @registry.register("deco_sig", source="evolved", version="1.0.0")
        class DecoSig(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p): return pd.Series(0.0, index=df.index)

        assert registry.get("deco_sig") is DecoSig
        entry = registry.get_entry("deco_sig")
        assert entry.version == "1.0.0"
        assert entry.source == "evolved"

    def test_instantiate_via_registry(self, price_df):
        from evolution.signal_registry import registry
        from evolution.base_signal import BaseSignal

        @registry.register("inst_sig")
        class InstSig(BaseSignal):
            def define_params(self, t): return {}
            def calculate(self, df, p): return pd.Series(1.0, index=df.index)

        sig = registry.instantiate("inst_sig")
        result = sig._safe_calculate(price_df, {})
        assert len(result) == len(price_df)

    def test_len(self):
        from evolution.signal_registry import registry
        assert len(registry) == 0
        cls = self._make_signal()
        registry.register_cls("len_sig", cls)
        assert len(registry) == 1

    def test_iter(self):
        from evolution.signal_registry import registry
        cls = self._make_signal()
        registry.register_cls("iter_b", cls)
        registry.register_cls("iter_a", cls)
        names = list(registry)
        assert names == sorted(names)
