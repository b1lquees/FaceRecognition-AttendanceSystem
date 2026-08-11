import zipfile

import numpy as np
import pytest

from attendance.recognition import (
    ENCODING_LENGTH,
    load_known_encodings,
    save_known_encodings,
)


def fake_encoding():
    return np.random.rand(ENCODING_LENGTH)


def test_round_trip(tmp_path):
    original = {"Alice": [fake_encoding(), fake_encoding()], "Bob": [fake_encoding()]}
    path = tmp_path / "encodings.npz"

    save_known_encodings(original, path)
    restored = load_known_encodings(path)

    assert sorted(restored) == sorted(original)
    assert {k: len(v) for k, v in restored.items()} == {"Alice": 2, "Bob": 1}
    for name in original:
        for before, after in zip(original[name], restored[name]):
            assert np.allclose(before, after)


# names are stored in a parallel array rather than as npz keys, which is what lets them
# contain characters that would be illegal or ambiguous as entry names inside a zip
@pytest.mark.parametrize(
    "name", ["Alice Chen", "O'Brien", "Jean-Luc", "a/b", "a\\b", "..", "encodings"]
)
def test_awkward_names_survive(tmp_path, name):
    path = tmp_path / "encodings.npz"

    save_known_encodings({name: [fake_encoding()]}, path)

    assert list(load_known_encodings(path)) == [name]


def test_a_missing_file_is_an_empty_cache(tmp_path):
    assert load_known_encodings(tmp_path / "nothing-here.npz") == {}


# np.array([]) has the wrong shape to stack back into 128-d vectors, so an empty cache
# needs its dimensions stated explicitly rather than inferred
def test_an_empty_cache_round_trips(tmp_path):
    path = tmp_path / "encodings.npz"

    save_known_encodings({}, path)

    assert load_known_encodings(path) == {}


# THE reason this file is not a pickle. Unpickling executes whatever it is given, and the
# web application writes this file now, so a format that runs code on load is the wrong
# shape entirely. numpy will still unpickle object arrays out of a .npz unless told not
# to -- load_known_encodings passes allow_pickle=False, and this proves it.
def test_a_cache_containing_pickled_objects_is_refused(tmp_path):
    path = tmp_path / "encodings.npz"

    # np.savez writes object arrays quite happily; it is the reader that has to refuse
    np.savez(
        path,
        names=np.array([{"anything": "at all"}], dtype=object),
        encodings=np.zeros((1, ENCODING_LENGTH)),
    )

    with pytest.raises(ValueError, match="allow_pickle"):
        load_known_encodings(path)


def test_the_written_file_is_a_real_npz(tmp_path):
    path = tmp_path / "encodings.npz"

    save_known_encodings({"Alice": [fake_encoding()]}, path)

    assert zipfile.is_zipfile(path)
    with zipfile.ZipFile(path) as archive:
        assert sorted(n.removesuffix(".npy") for n in archive.namelist()) == [
            "encodings",
            "names",
        ]


# the file is written to a temp name and moved into place, so a reader never sees a
# half-written cache -- which matters now that a web request can rewrite it while the
# camera is reading it
def test_writing_leaves_no_temporary_files(tmp_path):
    path = tmp_path / "encodings.npz"

    save_known_encodings({"Alice": [fake_encoding()]}, path)

    assert [p.name for p in tmp_path.iterdir()] == ["encodings.npz"]


def test_saving_over_an_existing_cache_replaces_it(tmp_path):
    path = tmp_path / "encodings.npz"
    save_known_encodings({"Alice": [fake_encoding()]}, path)

    save_known_encodings({"Bob": [fake_encoding()]}, path)

    assert list(load_known_encodings(path)) == ["Bob"]
