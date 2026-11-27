import numpy as np

from roamengine.inference import infer_locations

H = 3600.0
NAN = np.nan


def run(t, lat, utc=None, eligible=None):
    t, lat = np.array(t, float), np.array(lat, float)
    lon = np.where(np.isfinite(lat), 0.0, NAN)
    utc = np.ones(len(t), bool) if utc is None else np.array(utc)
    located = np.isfinite(lat)
    el = ~located if eligible is None else np.array(eligible)
    return infer_locations(t, utc, lat, lon, el, located)


def test_nearest_within_30_min():
    src, dts = run([0, 20 * 60], [10.0, NAN])
    assert src.tolist() == [-1, 0] and dts[1] == -20 * 60


def test_one_side_too_far():
    src, _ = run([0, 45 * 60], [10.0, NAN])
    assert src[1] == -1


def test_two_sides_agree_within_2h():
    # 1.4 h after a point and 1.6 h before another 1 km away: both sides agree, the nearer one wins
    src, dts = run([0, 1.4 * H, 3 * H], [10.0, NAN, 10.009])
    assert src[1] == 0 and dts[1] == -1.4 * H


def test_two_sides_disagree():
    src, _ = run([0, 1.5 * H, 3 * H], [10.0, NAN, 11.0])      # ~111 km apart: travelling, no guess
    assert src[1] == -1


def test_zone_kinds_never_mix():
    src, _ = run([0, 10 * 60], [10.0, NAN], utc=[True, False])
    assert src[1] == -1


def test_not_eligible_untouched_and_no_chaining():
    # item 1 inferred from 0; item 2 is 25 min from item 1 but 50 min from the only real point: no chain
    src, _ = run([0, 25 * 60, 50 * 60], [10.0, NAN, NAN])
    assert src.tolist() == [-1, 0, -1]
    src, _ = run([0, 10 * 60], [10.0, NAN], eligible=[False, False])
    assert src[1] == -1
