import os
import shutil
import sqlite3

import numpy as np
import pytest

from roamengine.catalog import Catalog
from roamengine.filedates import date_from_name


@pytest.mark.parametrize("name,day", [
    ("IMG-20190101-WA0001.jpg", "2019-01-01"),
    ("VID-20211231-WA0042.mp4", "2021-12-31"),
    ("sub/dir/img-20200229-wa0003.jpeg", "2020-02-29"),
    ("IMG_20180704_101010.jpg", "2018-07-04"),
    ("PXL_20230115_093012345.jpg", "2023-01-15"),
    ("Screenshot_20190101-101010_App.jpg", "2019-01-01"),
    ("WhatsApp Image 2020-05-06 at 10.11.12.jpeg", "2020-05-06"),
    ("IMG-20190230-WA0001.jpg", None),            # no 30 Feb
    ("IMG-18990101-WA0001.jpg", None),            # before 1990
    ("DSC_20190101.jpg", None),                   # bare 8 digits: too easy to misread
    ("12345678_123456.jpg", None),
    ("photo.jpg", None),
])
def test_date_from_name(name, day):
    assert date_from_name(name, max_year=2026) == (day and day + "T12:00:00")


def test_catalog_dates_only_undated_items(library, tmp_path):
    """blue.jpg (undated) renamed to a WhatsApp name gets a day; items with real times keep theirs."""
    lib = str(tmp_path / "lib")
    os.makedirs(lib)
    shutil.copy(os.path.join(library[1], "manifest.sqlite"), lib)
    before = Catalog(lib)
    con = sqlite3.connect(os.path.join(lib, "manifest.sqlite"))
    with con:
        con.execute("UPDATE asset_files SET rel_path = REPLACE(rel_path, 'blue.jpg', 'IMG-20200506-WA0001.jpg') "
                    "WHERE rel_path LIKE '%blue.jpg'")
        con.execute("UPDATE asset_files SET rel_path = REPLACE(rel_path, 'red.jpg', 'IMG-19990101-WA0001.jpg') "
                    "WHERE rel_path LIKE '%a_red.jpg'")             # red has EXIF time: the name must not win
    con.close()
    c = Catalog(lib)
    src = dict(zip(c.asset_ids, c.time_source))
    named = [a for a, s in src.items() if s == "filename"]
    assert len(named) == 1
    i = c.index[named[0]]
    assert c.time_local[i] == "2020-05-06T12:00:00" and c.time_utc[i] is None and np.isfinite(c.t[i])
    assert int(np.isfinite(c.t).sum()) == int(np.isfinite(before.t).sum()) + 1
    for a in c.asset_ids:                                            # every other time is unchanged
        if a != named[0]:
            j, k = c.index[a], before.index[a]
            assert (c.time_utc[j], c.time_local[j], c.time_source[j]) == \
                (before.time_utc[k], before.time_local[k], before.time_source[k])
