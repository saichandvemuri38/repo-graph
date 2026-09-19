"""Tests for day-1.py. The file name has a hyphen, so it is loaded by path instead of imported."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("day1", Path(__file__).with_name("day-1.py"))
day1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(day1)

SAMPLE = [10, 15, 12, 18, 20, 100, 16, 14, 19, 17]


def test_two_sum():
    assert day1.Solution().twoSum([2, 7, 11, 15], 9) == [0, 1]
    assert day1.Solution().twoSum([1, 2], 10) == []


def test_validate_input_and_bounds():
    assert day1.validate_input([1, 2.5, 3])
    assert not day1.validate_input("not a list")
    assert not day1.validate_input([1, "x"])
    assert day1.check_bounds([0, 10])
    assert not day1.check_bounds([5000])


def test_clean_outliers_drops_the_extreme_value():
    assert 100 not in day1.clean_outliers(SAMPLE)
    with pytest.raises(ValueError):
        day1.clean_outliers("bad")


def test_calculate_percentiles_default_and_explicit():
    data = list(range(1, 11))
    assert day1.calculate_percentiles(data) == {"p25": 3, "p50": 6, "p75": 8}
    assert day1.calculate_percentiles(data, [10, 90]) == {"p10": 2, "p90": 10}
    assert day1.calculate_percentiles([]) == {}


def test_percentiles_has_no_mutable_default():
    # a list default would be created once and shared by every call
    assert day1.calculate_percentiles.__defaults__ == (None,)
    first = day1.calculate_percentiles([1, 2, 3])
    second = day1.calculate_percentiles([1, 2, 3])
    assert first == second and first is not second


def test_extract_features_on_the_sample():
    features = day1.extract_features(SAMPLE)
    assert features["count"] == 9 and features["min"] == 10 and features["max"] == 20
    assert features["mean"] == pytest.approx(15.6667, abs=1e-3)
    assert {"p25", "p50", "p75"} <= set(features)


def test_transformations():
    assert day1.normalize_data([1, 2, 3]) == [0.0, 0.5, 1.0]
    assert day1.transform_data([1, 2, 3], "scale") == [0.0, 50.0, 100.0]
    assert day1.transform_data([1, 2, 3], "other") == [1, 2, 3]


def test_generate_report():
    report = day1.generate_report(SAMPLE)
    assert report["status"] == "success"
    assert report["input_size"] == 10 and report["preprocessed_size"] == 9
