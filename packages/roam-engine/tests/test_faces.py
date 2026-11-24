"""Recurring-people logic on synthetic face vectors (no models, no images)."""
import numpy as np

from roamengine import faces

rng = np.random.default_rng(7)
DAY = 86400.0
T0 = 1_600_000_000.0


def unit(x):
    return (x / np.linalg.norm(x, axis=-1, keepdims=True)).astype(np.float32)


def library():
    """3 people seen on 10 days over 5 months (one also as a small, unsure face), plus 25 one-off strangers."""
    base = unit(rng.normal(size=(3, 128)))
    vec, t, area, score, who = [], [], [], [], []
    for p in range(3):
        for d in range(10):
            vec.append(unit(base[p] + 0.55 * unit(rng.normal(size=128)))); t.append(T0 + d * 15 * DAY)
            area.append(0.03); score.append(0.95); who.append(p)
    vec.append(unit(base[0] + 0.55 * unit(rng.normal(size=128)))); t.append(T0); area.append(0.002); score.append(0.8); who.append(0)
    for s in range(25):
        vec.append(unit(rng.normal(size=128))); t.append(T0 + s * DAY); area.append(0.02); score.append(0.95); who.append(-1)
    return np.array(vec), np.array(t), np.array(area), np.array(score), np.array(who)


def test_three_recurring_people_and_strangers_stay_apart():
    vec, t, area, score, who = library()
    order = np.argsort(-area)
    lab = np.full(len(vec), -1)
    lab[order] = faces.cluster(vec[order], ((area >= faces.SEED_AREA) & (score >= faces.SEED_SCORE))[order])
    imp = faces.recurring(lab, t)
    assert len(imp) == 3
    for p in range(3):                                   # each person is exactly one recurring cluster
        assert len({lab[i] for i in np.flatnonzero(who == p)}) == 1 and lab[who == p][0] in imp
    assert not any(lab[i] in imp for i in np.flatnonzero(who == -1))
    assert lab[30] == lab[0]                             # the small, unsure face joins its person but seeded nothing


def test_features_reward_known_and_penalise_prominent_strangers():
    face_asset = np.array([0, 0, 1, 2, 2, 2, 2, 2, 2, 3])
    area = np.array([0.03, 0.002, 0.03, *[0.003] * 6, 0.05])
    labels = np.array([5, -1, -1, *[-1] * 6, 9])
    known, strangers, crowd, group = faces.features(5, face_asset, area, labels, {5: 2.0, 9: 1.0})
    assert list(group) == [1, 1, 0, 1, 0]                       # faces under 0.5% of the photo are not "in" it
    assert known[0] == 1.0 and strangers[0] == 0                 # person 5 large; the tiny face is ignored
    assert known[1] == 0 and strangers[1] > 1                    # one prominent stranger
    assert crowd[2] == 1 and strangers[2] == 0                   # six tiny unknown faces: a crowd, not a portrait
    assert 0 < known[3] < known[0]                               # a less important recurring person
    assert known[4] == strangers[4] == crowd[4] == 0             # no faces


def test_no_faces():
    assert faces.cluster(np.zeros((0, 128), np.float32), np.zeros(0, bool)).size == 0
    assert faces.recurring(np.array([], int), np.array([])) == {}
