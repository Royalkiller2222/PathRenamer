"""Scanning, subtitle pickup, renaming/undo and duplicate detection on real files."""
from pathlib import Path

from conftest import make_files


def test_scan_skips_samples_and_extras(main, tmp_path):
    keep = make_files(tmp_path,
                      "Movie.2019.1080p.BluRay.x264-GRP/Movie.2019.1080p.BluRay.x264-GRP.mkv",
                      "Show.S01.1080p/Show.S01E01.1080p.mkv")
    make_files(tmp_path,
               "Movie.2019.1080p.BluRay.x264-GRP/movie.2019.1080p-sample.mkv",
               "Movie.2019.1080p.BluRay.x264-GRP/Sample/movie-sample.mkv",
               "Movie.2019.1080p.BluRay.x264-GRP/Featurettes/Making Of.mkv",
               "Show.S01.1080p/Extras/Gag Reel.mkv")
    assert sorted(main.scan_folder(str(tmp_path))) == sorted(keep)


def test_scan_still_takes_an_explicitly_chosen_extras_folder(main, tmp_path):
    files = make_files(tmp_path, "Extras/Gag Reel.mkv")
    assert main.scan_folder(str(tmp_path / "Extras")) == files


def test_subtitle_names_must_match_the_whole_video_name(main, tmp_path):
    video, _ = make_files(tmp_path, "Show - 1.mkv", "Show - 10.mkv")
    subs = make_files(tmp_path, "Show - 1.srt", "Show - 1.en.forced.srt", "Show - 10.srt")
    assert sorted(main.find_subtitle_siblings(video)) == sorted(subs[:2])


def test_single_movie_takes_everything_in_its_subs_folder(main, tmp_path):
    rel = "Movie.2019.1080p.BluRay.x264-GRP"
    video, _sample = make_files(tmp_path, f"{rel}/{rel}.mkv", f"{rel}/{rel}-sample.mkv")
    subs = make_files(tmp_path, f"{rel}/Subs/2_English.srt", f"{rel}/Subs/3_Spanish.srt")
    assert sorted(main.find_subtitle_siblings(video)) == sorted(subs)


def test_season_pack_subs_in_per_episode_folders(main, tmp_path):
    e1, e2 = make_files(tmp_path, "Show.S01/Show.S01E01.1080p.mkv", "Show.S01/Show.S01E02.1080p.mkv")
    s1, s2 = make_files(tmp_path, "Show.S01/Subs/Show.S01E01.1080p/2_English.srt",
                        "Show.S01/Subs/Show.S01E02.1080p/2_English.srt")
    assert main.find_subtitle_siblings(e1) == [s1]
    assert main.find_subtitle_siblings(e2) == [s2]


def test_subtitles_follow_the_video_name(main, tmp_path):
    rel = "Movie.2019.1080p.BluRay.x264-GRP"
    video, = make_files(tmp_path, f"{rel}/{rel}.mkv")
    make_files(tmp_path, f"{rel}/Subs/2_English.srt", f"{rel}/Subs/3_Spanish.srt")
    cand = main.MatchEngine(cfg=dict(main.DEFAULTS)).match(video, "movie")
    names = sorted(Path(dest).name for _, dest in cand.subtitles)
    assert names == ["Movie (2019) 1080p H.264.eng.srt", "Movie (2019) 1080p H.264.spa.srt"]


def test_rename_and_undo(main, tmp_path):
    src, = make_files(tmp_path / "in", "Show.S01E01.mkv")
    dst = tmp_path / "lib" / "Show" / "Season 01" / "Show S01E01 - Pilot.mkv"
    r = main.Renamer()
    res = r.execute([(src, dst)], dest_root=str(tmp_path / "lib"))
    assert len(res.succeeded) == 1 and dst.exists() and not src.exists()
    undo = r.undo_last()
    assert len(undo.succeeded) == 1 and src.exists()
    assert not (tmp_path / "lib" / "Show").exists()     # emptied folders are cleaned up


def test_rename_never_overwrites(main, tmp_path):
    src, dst = make_files(tmp_path, "in/a.mkv", "lib/b.mkv")
    res = main.Renamer().execute([(src, dst)])
    assert res.failed and src.exists()


def test_two_files_with_the_same_destination_are_flagged(main, tmp_path):
    files = make_files(tmp_path / "in", "Show.S01E01.720p.HDTV.x264.mkv", "Show.S01E01.1080p.WEB.x264.mkv")
    engine = main.MatchEngine(cfg=dict(main.DEFAULTS))
    worker = main.ScanWorker(files, engine, "tv", str(tmp_path / "lib"))
    got = []
    worker.candidate_ready.connect(got.append)
    worker.run()
    assert len(got) == 2
    assert got[0].dest_rel == got[1].dest_rel
    assert not got[0].error
    assert got[1].error.startswith("Same new name as")
