"""Real-world release names -> what PathRenamer should name them.

Names are typical of what torrent/usenet clients download: scene and P2P
releases, anime fansubs, foreign releases, site-prefixed names, obfuscated
files inside release folders. Each case passes with guessit 3.8 (the version
bundled in the v1.2.0 build) and guessit 4.x.

Bare file names are placed in a "/lib/Incoming" folder, the kind of folder
name a download client uses, to check that it is never taken for a title.
"""
import datetime

import pytest

from parser import analyse_path

CASES = [
    # TV: scene/P2P names, multi-episode files, daily shows, foreign season words
    ('tv', 'The.Office.US.S02E03.720p.WEB-DL.x264.mkv',
     'The Office (US)', None, 2, [3], None),
    ('tv', 'Obi-Wan.Kenobi.S01E01.1080p.WEB.H264-GLHF.mkv',
     'Obi-Wan Kenobi', None, 1, [1], None),
    ('tv', 'She-Hulk.Attorney.at.Law.S01E01.1080p.DSNP.WEB-DL.mkv',
     'She-Hulk Attorney at Law', None, 1, [1], None),
    ('tv', '1923.S02E01.1080p.WEB.h264-ETHEL.mkv',
     '1923', None, 2, [1], None),
    ('tv', '1883.S01E01.1883.2160p.WEB-DL.DDP5.1.H.265-NTb.mkv',
     '1883', None, 1, [1], None),
    ('tv', 'The.Last.of.Us.S01E01.1080p.HMAX.WEB-DL.mkv',
     'The Last of Us', None, 1, [1], None),
    ('tv', 'Doctor.Who.2005.S06E13.The.Wedding.of.River.Song.720p.mkv',
     'Doctor Who', 2005, 6, [13], None),
    ('tv', 'The.Simpsons.S01E01E02E03.1080p.BluRay.x265.mkv',
     'The Simpsons', None, 1, [1, 2, 3], None),
    ('tv', 'Show.Name.S04E44-E45.1080p.WEB.mkv',
     'Show Name', None, 4, [44, 45], None),
    ('tv', 'Series.S03E01.S03E02.720p.HDTV.X264-DIMENSION.mkv',
     'Series', None, 3, [1, 2], None),
    ('tv', 'Phineas and Ferb S01E00 & S01E01 & S01E02.mkv',
     'Phineas and Ferb', None, 1, [0, 1, 2], None),
    ('tv', 'The.Daily.Show.2024.05.17.Guest.Name.720p.WEB.h264.mkv',
     'The Daily Show', None, None, [], '2024-05-17'),
    ('tv', 'Tree_Series_2018_06_22_Seth_Meyers_720p_HEVC_x265-MeGusta.mkv',
     'Tree Series', None, None, [], '2018-06-22'),
    ('tv', 'Vikings.s02.09.AVC.tahiy.mkv',
     'Vikings', None, 2, [9], None),
    ('tv', 'The.Witcher.S01.07.mp4',
     'The Witcher', None, 1, [7], None),
    ('tv', 'Die.Simpsons.Staffel.2.Folge.5.German.1080p.mkv',
     'Die Simpsons', None, 2, [5], None),
    ('tv', 'Kaamelott Saison 2 Épisode 5 FRENCH 1080p.mkv',
     'Kaamelott', None, 2, [5], None),
    ('tv', 'Serie Säsong 2 Avsnitt 5 SWEDISH 1080p.mkv',
     'Serie', None, 2, [5], None),
    ('tv', 'La casa del dragón 2×7.mkv',
     'La casa del dragón', None, 2, [7], None),
    ('tv', 'Por Trece Razones - Temporada 2 [HDTV 720p][Cap.201][AC3 5.1 Castellano].mkv',
     'Por Trece Razones', None, 2, [1], None),
    ('tv', 'Show.Name.Aflevering.5.van.12.1080p.mkv',
     'Show Name', None, None, [5], None),
    ('tv', 'Series Title Season 01 Episode 05-06 720p.mkv',
     'Series Title', None, 1, [5, 6], None),
    ('tv', 'Road.to.the.NHL.Winter.Classic.S07E01.1080p.WEB.mkv',
     'Road to the NHL Winter Classic', None, 7, [1], None),
    ('tv', 'www.Torrenting.org - Sons.of.Anarchy.S07E07.720p.HDTV.X264-DIMENSION.mkv',
     'Sons of Anarchy', None, 7, [7], None),
    ('tv', '[ www.Speed.cd ] -Sons.of.Anarchy.S07E07.720p.HDTV.X264-DIMENSION.mkv',
     'Sons of Anarchy', None, 7, [7], None),
    ('tv', 'Lgds.of.Tmrow-02_17.F.avi',
     'Lgds of Tmrow', None, 2, [17], None),
    ('tv', 'Доктор Хаус 03-20.mkv',
     'Доктор Хаус', None, 3, [20], None),
    ('tv', 'Chernobyl E02 1 23 45.mp4',
     'Chernobyl', None, None, [2], None),
    ('tv', 'Mobile_Suit_Gundam_00_Season_2_Ep07_A_Reunion_and_a_Parting_[1080p,BluRay,x264]_-_THORA.mkv',
     'Mobile Suit Gundam 00', None, 2, [7], None),
    ('tv', "Looney Tunes 1940x01 Porky's Last Stand.mkv",
     'Looney Tunes', None, 1940, [1], None),
    ('tv', '庆余年 第二季 第3集.mp4',
     '庆余年', None, 2, [3], None),
    ('tv', '超能警探.Memorist.S01E01.2160p.WEB-DL.H265.AAC-FLTTH.mkv',
     'Memorist', None, 1, [1], None),
    ('tv', 'Extras.2005.S01E01.mkv',
     'Extras', 2005, 1, [1], None),
    ('tv', 'grown-ish.s03e01.web.x264-tbs[eztv].mkv',
     'grown-ish', None, 3, [1], None),
    ('tv', 'Show Name - S01E02 - S01E03 - S01E04 - Ep Name.mkv',
     'Show Name', None, 1, [2, 3, 4], None),
    ('tv', 'The Office - S06xE01.avi',
     'The Office', None, 6, [1], None),
    ('tv', 'Series Title - [02x01-x02] - Episode 1.mkv',
     'Series Title', None, 2, [1, 2], None),
    ('tv', 'Iron-Fist-2017-01_13-F.avi',
     'Iron-Fist', 2017, 1, [13], None),
    ('tv', 'Interny. Сезон №9. Серия №180.avi',
     'Interny', None, 9, [180], None),

    # Anime: fansub names, absolute numbers, season markers, specials
    ('anime', '[SubsPlease] Jujutsu Kaisen - 01 (1080p) [ABCD1234].mkv',
     'Jujutsu Kaisen', None, None, [1], None),
    ('anime', '[SubsPlease] Series Title - 100 Years Quest - 01 (1080p) [1107F3A9].mkv',
     'Series Title - 100 Years Quest', None, None, [1], None),
    ('anime', '[SubsPlease] Title de Series S2 - 03 (540p) [63501322].mkv',
     'Title de Series', None, 2, [3], None),
    ('anime', '[Erai-raws] Honzuki no Gekokujou - Shisho ni Naru Tame ni wa Shudan wo Erandeiraremasen 2nd Season - 12 END [1080p][Multiple Subtitle].mkv',
     'Honzuki no Gekokujou - Shisho ni Naru Tame ni wa Shudan wo Erandeiraremasen', None, 2, [12], None),
    ('anime', '[HR] Boku no Hero Academia 87 (S4-24) [1080p HEVC Multi-Subs] HR-GZ.mkv',
     'Boku no Hero Academia', None, 4, [24], None),
    ('anime', '[Frostii]_Nodame_Cantabile_Finale_-_00_[73AD0735].mkv',
     'Nodame Cantabile Finale', None, None, [0], None),
    ('anime', '[HorribleSubs] Momokuri - 01+02 [720p].mkv',
     'Momokuri', None, None, [1, 2], None),
    ('anime', 'Hayate no Gotoku 2nd Season 24 (Blu-Ray 1080p) [Chihiro].mkv',
     'Hayate no Gotoku', None, 2, [24], None),
    ('anime', '[Group] Anime Super Episode 161 [VOSTFR][720p].mp4',
     'Anime Super', None, None, [161], None),
    ('anime', '[FFF] Seirei Tsukai no Blade Dance - SP01 [BD][720p-AAC][F1FF8588].mkv',
     'Seirei Tsukai no Blade Dance', None, 0, [1], None),
    ('anime', '[GM-Team][国漫][诛仙][Jade Dynasty][2022][11][HEVC][GB][4K].mp4',
     'Jade Dynasty', 2022, None, [11], None),
    ('anime', 'Naruto Shippuden - 107 - Strange Bedfellows.mkv',
     'Naruto Shippuden', None, None, [107], None),
    ('anime', '[DameDesuYo] Another Anime With Special Naming (Season 2) - 33 (1280x720 10bit EAC3) [42A12A76].mkv',
     'Another Anime With Special Naming', None, 2, [33], None),
    ('anime', '[Hatsuyuki] Series Title (2014) - 018 (116) [1280x720][C4A3B16E].mkv',
     'Series Title', 2014, None, [18], None),

    # Movies: hyphenated/numeric titles, Part/Vol, title words guessit takes for tags
    ('movie', 'Spider-Man.No.Way.Home.2021.1080p.WEB-DL.x264.mkv',
     'Spider-Man No Way Home', 2021, None, [], None),
    ('movie', 'Ant-Man.and.the.Wasp.Quantumania.2023.1080p.WEB-DL.mkv',
     'Ant-Man and the Wasp Quantumania', 2023, None, [], None),
    ('movie', 'Dune.Part.Two.2024.2160p.WEB-DL.mkv',
     'Dune Part Two', 2024, None, [], None),
    ('movie', 'Mission.Impossible.Dead.Reckoning.Part.One.2023.1080p.mkv',
     'Mission Impossible Dead Reckoning Part One', 2023, None, [], None),
    ('movie', 'Kill.Bill.Vol.1.2003.1080p.BluRay.mkv',
     'Kill Bill Vol 1', 2003, None, [], None),
    ('movie', 'The.Godfather.Part.II.1974.1080p.BluRay.mkv',
     'The Godfather Part II', 1974, None, [], None),
    ('movie', '1917 (2019) [BluRay Rip 1080p ITA-ENG AC3 SUBS].mkv',
     '1917', 2019, None, [], None),
    ('movie', '9.2009.Blu-ray.DTS.720p.x264.HDBRiSe.mkv',
     '9', 2009, None, [], None),
    ('movie', 'The.Immigrant.1917.1080p.BluRay.x264-GRP.mkv',
     'The Immigrant', 1917, None, [], None),
    ('movie', 'Johnny.English.2003.1080p.BluRay.mkv',
     'Johnny English', 2003, None, [], None),
    ('movie', 'It.Ends.With.Us.2024.1080p.WEB-DL.mkv',
     'It Ends With Us', 2024, None, [], None),
    ('movie', 'The.Collector.2009.1080p.BluRay.mkv',
     'The Collector', 2009, None, [], None),
    ('movie', 'Us.2019.1080p.BluRay.mkv',
     'Us', 2019, None, [], None),
    ('movie', 'xXx.2002.15th.Anniversary.Edition.1080p.BluRay.Remux.mkv',
     'xXx', 2002, None, [], None),
    ('movie', 'www.5MovieRulz.show - Khel Khel Mein (2024) 1080p x264.mkv',
     'Khel Khel Mein', 2024, None, [], None),
    ('movie', 'Movie Name (1897) [DVD].mp4',
     'Movie Name', 1897, None, [], None),
    ('movie', 'Blade.Runner.2049.2017.1080p.BluRay.mkv',
     'Blade Runner 2049', 2017, None, [], None),
    ('movie', '2001.A.Space.Odyssey.1968.1080p.BluRay.mkv',
     '2001 A Space Odyssey', 1968, None, [], None),
    ('movie', 'Wonder.Woman.1984.2020.1080p.mkv',
     'Wonder Woman 1984', 2020, None, [], None),
    ('movie', 'The.Matrix.1999.1080p.BluRay.x264-GRP.mkv',
     'The Matrix', 1999, None, [], None),

    # TV inside folders: show/season folders, release folders, obfuscated files
    ('tv', '/media/Breaking Bad/Season 1/01 - Pilot.mkv',
     'Breaking Bad', None, 1, [1], None),
    ('tv', '/media/The Office (US)/Season 2/The.Office.S02E03.mkv',
     'The Office (US)', None, 2, [3], None),
    ('tv', '/dl/Incoming/The.Office.US.S02E03.720p.mkv',
     'The Office (US)', None, 2, [3], None),
    ('tv', '/dl/Show.Name.S01E05.720p.WEB-DL-GRP/abc123.mkv',
     'Show Name', None, 1, [5], None),
    ('tv', '/dl/Zoo.S02E05.1080p.WEB-DL.DD5.1.H.264.HKD/160725_02.mkv',
     'Zoo', None, 2, [5], None),
    ('tv', '/dl/Mind.Field.S02E06.1080p.WEBDL/gNWDXow11s7E0X7GTDrZ.mkv',
     'Mind Field', None, 2, [6], None),
    ('tv', '/media/Friends S03/Friends - 07 - The One.mkv',
     'Friends', None, 3, [7], None),
    ('tv', '/media/Doctor Who (2005)/Saison 6/Doctor Who (2005) - S06E13 - The Wedding of River Song.mkv',
     'Doctor Who', 2005, 6, [13], None),
    ('tv', '/media/Babylon 5/Season 01/Ep. 02 - Soul Hunter.mkv',
     'Babylon 5', None, 1, [2], None),
    ('tv', '/media/The 4400/Season 1/The.4400.S01E01.mkv',
     'The 4400', None, 1, [1], None),
    ('tv', '/media/The Big Bang Theory/tbbt.s01e01.mkv',
     'The Big Bang Theory', None, 1, [1], None),
    ('tv', '/media/庆余年第二季/01.mp4',
     '庆余年', None, 2, [1], None),
    ('tv', '/dl/The.Office.US.S02.720p.WEB-DL.x264-GRP/The.Office.US.S02E03.720p.WEB-DL.x264-GRP.mkv',
     'The Office (US)', None, 2, [3], None),

    # Movies inside folders
    ('movie', '/movies/Fear and Loathing in Las Vegas (1998)/Fear.and.Loathing.in.Las.Vegas.720p.HDDVD.DTS.x264-ESiR.mkv',
     'Fear and Loathing in Las Vegas', 1998, None, [], None),
    ('movie', '/movies/Charlie.And.Boots.DVDRip.XviD-TheWretched/wthd-cab.avi',
     'Charlie And Boots', None, None, [], None),
    ('movie', '/movies/Cosmopolis.2012.LiMiTED.720p.BluRay.x264-AN0NYM0US[bb]/ano-cosmo.720p.mkv',
     'Cosmopolis', 2012, None, [], None),
    ('movie', '/dl/Downloads/Inception.2010.1080p.BluRay.mkv',
     'Inception', 2010, None, [], None),
    ('movie', '/movies/9 (2009)/9.mkv',
     '9', 2009, None, [], None),
]


@pytest.mark.parametrize("media_type,path,title,year,season,episodes,date", CASES,
                         ids=[c[1].rsplit("/", 1)[-1][:60] for c in CASES])
def test_release_name(media_type, path, title, year, season, episodes, date):
    full = path if path.startswith("/") else "/lib/Incoming/" + path
    g = analyse_path(full, media_type)
    got = (g.title, g.year, g.season, g.episodes, g.date.isoformat() if g.date else None)
    assert got == (title, year, season, episodes, date)


def test_generic_folder_is_never_the_title():
    for folder in ("Downloads", "Incoming", "Complete", "TV Shows", "qBittorrent"):
        g = analyse_path(f"/x/{folder}/S02E03.mkv", "tv")
        assert g.title == ""          # nothing to go on: the app reports an error instead


def test_season_folder_gives_the_season():
    g = analyse_path("/media/Show Name/Season 3/07 - Some Title.mkv", "tv")
    assert (g.title, g.season, g.episodes) == ("Show Name", 3, [7])
    g = analyse_path("/media/Show Name/Specials/Show Name - S00E02.mkv", "tv")
    assert (g.season, g.episodes) == (0, [2])


def test_date_object_type():
    g = analyse_path("/x/The.Daily.Show.2024.05.17.Guest.Name.720p.WEB.h264.mkv", "tv")
    assert g.date == datetime.date(2024, 5, 17)
