"""Copy / hard-link instead of move, and the rename history kept on disk."""
import errno
import os

import pytest

from conftest import make_files
import history


@pytest.fixture
def renamer(main, tmp_path):
    return main.Renamer(history.RenameHistory(tmp_path / "history.json"))


def test_copy_keeps_the_original_and_undo_removes_the_copy(main, renamer, tmp_path):
    src, = make_files(tmp_path / "dl", "Movie.2019.mkv")
    src.write_bytes(b"video")
    dst = tmp_path / "lib" / "Movie (2019)" / "Movie (2019).mkv"
    res = renamer.execute([(src, dst)], dest_root=str(tmp_path / "lib"), action="copy")
    assert res.succeeded and src.exists() and dst.read_bytes() == b"video"
    undo = renamer.undo_last()
    assert undo.succeeded and src.exists() and not dst.exists()
    assert not (tmp_path / "lib" / "Movie (2019)").exists()


def test_hardlink_shares_the_file(main, renamer, tmp_path):
    src, = make_files(tmp_path / "dl", "Movie.2019.mkv")
    dst = tmp_path / "lib" / "Movie (2019).mkv"
    res = renamer.execute([(src, dst)], action="hardlink")
    assert res.succeeded[0].method == "hardlink"
    assert os.path.samefile(src, dst)
    renamer.undo_last()
    assert src.exists() and not dst.exists()


def test_hardlink_to_another_drive_copies(main, renamer, tmp_path, monkeypatch):
    def no_link(a, b):
        raise OSError(errno.EXDEV, "Invalid cross-device link")
    monkeypatch.setattr(main.os, "link", no_link)
    src, = make_files(tmp_path / "dl", "Movie.2019.mkv")
    dst = tmp_path / "lib" / "Movie (2019).mkv"
    op = renamer.execute([(src, dst)], action="hardlink").succeeded[0]
    assert op.method == "copy" and "same drive" in op.note
    assert src.exists() and dst.exists() and not os.path.samefile(src, dst)


def test_copy_is_kept_when_the_original_is_gone(main, renamer, tmp_path):
    src, = make_files(tmp_path / "dl", "Movie.2019.mkv")
    dst = tmp_path / "lib" / "Movie (2019).mkv"
    renamer.execute([(src, dst)], action="copy")
    src.unlink()                                     # e.g. the torrent was deleted with its data
    undo = renamer.undo_last()
    assert undo.failed and "kept" in undo.failed[0].error
    assert dst.exists()


def test_history_survives_a_restart(main, tmp_path):
    src, = make_files(tmp_path / "dl", "Show.S01E01.mkv")
    dst = tmp_path / "lib" / "Show" / "Show S01E01.mkv"
    main.Renamer(history.RenameHistory(tmp_path / "h.json")).execute([(src, dst)])
    later = main.Renamer(history.RenameHistory(tmp_path / "h.json"))   # app opened again
    assert later.can_undo()
    assert later.undo_last().succeeded and src.exists() and not dst.exists()
    assert not main.Renamer(history.RenameHistory(tmp_path / "h.json")).can_undo()


def test_an_older_rename_can_be_undone(main, renamer, tmp_path):
    a, b = make_files(tmp_path / "dl", "A.2001.mkv", "B.2002.mkv")
    renamer.execute([(a, tmp_path / "lib" / "A.mkv")])
    renamer.execute([(b, tmp_path / "lib" / "B.mkv")])
    older = renamer.history.batches()[1]
    assert history.describe(older).startswith("1 file moved")
    renamer.undo_batch(older["id"])
    assert a.exists() and not b.exists()             # only the older batch went back
    assert renamer.history.get(older["id"])["undone"]
    assert renamer.can_undo()                        # the newer one still can be


def test_unreadable_history_starts_fresh(tmp_path):
    (tmp_path / "history.json").write_text("{not json")
    h = history.RenameHistory(tmp_path / "history.json")
    assert h.batches() == []
    h.add([("a", "b", "move")])
    assert len(history.RenameHistory(tmp_path / "history.json").batches()) == 1


def test_history_is_capped(tmp_path):
    h = history.RenameHistory(tmp_path / "h.json", limit=3)
    for i in range(5):
        h.add([(f"s{i}", f"d{i}", "move")])
    assert [b["ops"][0]["src"] for b in h.batches()] == ["s4", "s3", "s2"]
