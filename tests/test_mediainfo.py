"""Reading resolution and codecs from the video file itself (mediainfo.py),
and using them in new names when the file name doesn't say."""
import shutil
import struct
from pathlib import Path

import pytest

from mediainfo import read_media_info, resolution_label

SAMPLES = Path(__file__).parent / "data" / "media"      # made by make_samples.sh


@pytest.mark.parametrize("name, expected", [
    ("h264_ac3_51.mkv", (1920, 800, "1080p", "H.264", "Dolby Digital", "5.1")),
    ("hevc_eac3_51.mkv", (3840, 1600, "2160p", "H.265", "Dolby Digital Plus", "5.1")),
    ("h264_dts_51.mkv", (1280, 720, "720p", "H.264", "DTS", "5.1")),
    ("av1_opus_20.mkv", (1920, 1080, "1080p", "AV1", "Opus", "2.0")),
    ("vp9_vorbis_20.webm", (1280, 536, "720p", "VP9", "Vorbis", "2.0")),
    ("h264_truehd_51.mkv", (1920, 1080, "1080p", "H.264", "Dolby TrueHD", "5.1")),
    ("h264_aac_71.mkv", (1920, 1080, "1080p", "H.264", "AAC", "7.1")),
    ("mpeg2_mp2_20.mkv", (720, 576, "576p", "MPEG-2", "MP2", "2.0")),
    ("h264_flac_10.mkv", (1440, 1080, "1080p", "H.264", "FLAC", "1.0")),
    ("h264_aac_51.mp4", (1920, 1080, "1080p", "H.264", "AAC", "5.1")),       # index first
    ("hevc_ac3_51.mp4", (3840, 2160, "2160p", "H.265", "Dolby Digital", "5.1")),  # index last
    ("h264_eac3_51.m4v", (1280, 720, "720p", "H.264", "Dolby Digital Plus", "5.1")),
    ("h264_mp3_20.mov", (852, 480, "480p", "H.264", "MP3", "2.0")),
    ("xvid_mp3_20.avi", (720, 480, "480p", "Xvid", "MP3", "2.0")),
    ("divx_ac3_51.avi", (640, 360, "360p", "DivX", "Dolby Digital", "5.1")),
])
def test_reads_real_files(name, expected):
    info = read_media_info(SAMPLES / name)
    assert (info.width, info.height, info.resolution, info.video_codec,
            info.audio_codec, info.audio_channels) == expected


@pytest.mark.parametrize("size, label", [
    ((3840, 2160), "2160p"), ((3840, 1600), "2160p"), ((4096, 1716), "2160p"),
    ((2560, 1440), "1440p"), ((1920, 1080), "1080p"), ((1920, 800), "1080p"),
    ((1440, 1080), "1080p"), ((1920, 1200), "1080p"), ((2048, 858), "1080p"),
    ((1280, 720), "720p"), ((1280, 536), "720p"), ((720, 576), "576p"),
    ((720, 480), "480p"), ((720, 404), "480p"), ((640, 480), "480p"),
    ((640, 360), "360p"), ((320, 240), "240p"), ((0, 0), ""), ((1920, 0), ""),
])
def test_resolution_labels(size, label):
    assert resolution_label(*size) == label


def test_files_that_are_not_videos(tmp_path):
    empty = tmp_path / "empty.mkv"
    empty.write_bytes(b"")
    text = tmp_path / "notes.mkv"
    text.write_text("not a video at all " * 50)
    assert read_media_info(empty) is None
    assert read_media_info(text) is None
    assert read_media_info(tmp_path / "missing.mkv") is None


@pytest.mark.parametrize("name", ["h264_ac3_51.mkv", "h264_aac_51.mp4", "hevc_ac3_51.mp4", "xvid_mp3_20.avi"])
def test_cut_off_files_never_raise(tmp_path, name):
    data = (SAMPLES / name).read_bytes()
    for cut in (4, 16, 40, 100, 300, len(data) // 2, len(data) - 1):
        part = tmp_path / f"part{cut}{Path(name).suffix}"
        part.write_bytes(data[:cut])
        read_media_info(part)                     # None or partial, but no error


# ── hand-built files for the less common layouts ────────────────────────────
def _ebml(eid, payload):
    return eid.to_bytes((eid.bit_length() + 7) // 8, "big") + (1 << 56 | len(payload)).to_bytes(8, "big") + payload


def _uint(eid, value, width=None):
    return _ebml(eid, value.to_bytes(width or max(1, (value.bit_length() + 7) // 8), "big"))


def _track(kind, codec, default=None, width=0, height=0, channels=None):
    parts = [_uint(0x83, kind), _ebml(0x86, codec.encode())]
    if default is not None:
        parts.append(_uint(0x88, default))
    if width:
        parts.append(_ebml(0xE0, _uint(0xB0, width) + _uint(0xBA, height)))
    if channels:
        parts.append(_ebml(0xE1, _uint(0x9F, channels)))
    return _ebml(0xAE, b"".join(parts))


def test_mkv_with_tracks_after_the_video_data(tmp_path):
    """Some muxers write the track list last; the SeekHead says where it is.
    The default audio track wins over the first one."""
    tracks = _ebml(0x1654AE6B, _track(1, "V_MPEGH/ISO/HEVC", width=3840, height=2160)
                   + _track(2, "A_AAC", default=0, channels=2)
                   + _track(2, "A_TRUEHD", default=1, channels=8))
    cluster = _ebml(0x1F43B675, b"\x00" * 5000)

    def seekhead(pos):
        return _ebml(0x114D9B74, _ebml(0x4DBB, _ebml(0x53AB, b"\x16\x54\xae\x6b") + _uint(0x53AC, pos, 8)))
    head = seekhead(0)
    segment = _ebml(0x18538067, seekhead(len(head) + len(cluster)) + cluster + tracks)
    f = tmp_path / "late.mkv"
    f.write_bytes(_ebml(0x1A45DFA3, _ebml(0x4282, b"matroska")) + segment)
    info = read_media_info(f)
    assert (info.resolution, info.video_codec, info.audio_codec, info.audio_channels) == \
        ("2160p", "H.265", "Dolby TrueHD", "7.1")


def test_mp4_with_64_bit_box_sizes(tmp_path):
    """Files over 4 GB use 64-bit box sizes for the video data."""
    data = (SAMPLES / "h264_aac_51.mp4").read_bytes()
    pos = 0
    boxes = {}
    while pos < len(data):
        size, kind = struct.unpack_from(">I4s", data, pos)
        boxes[kind] = data[pos:pos + size]
        pos += size
    payload = b"\x00" * 100_000
    big = boxes[b"ftyp"] + struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload)) + payload + boxes[b"moov"]
    f = tmp_path / "big.mp4"
    f.write_bytes(big)
    assert read_media_info(f).resolution == "1080p"


# ── in new names ────────────────────────────────────────────────────────────
@pytest.fixture
def engine(main):
    def make(**cfg):
        return main.MatchEngine(cfg=dict(main.DEFAULTS, use_cinemeta=False, **cfg))
    return make


def _movie(tmp_path, name, sample="h264_ac3_51.mkv"):
    video = tmp_path / name
    shutil.copy(SAMPLES / sample, video)
    return video


def test_name_gets_quality_from_the_file(engine, tmp_path):
    cand = engine().match(_movie(tmp_path, "Some Movie (2019).mkv"), "movie")
    assert cand.dest_rel == "Some Movie (2019)/Some Movie (2019) 1080p H.264 5.1 Dolby Digital.mkv"


def test_the_name_wins_over_the_file(engine, tmp_path):
    cand = engine().match(_movie(tmp_path, "Some.Movie.2019.720p.x265.mkv"), "movie")
    assert cand.dest_rel == "Some Movie (2019)/Some Movie (2019) 720p H.265 5.1 Dolby Digital.mkv"
    # channels of another audio track are not mixed into the name's codec
    cand = engine().match(_movie(tmp_path, "Some.Movie.2019.DTS.mkv"), "movie")
    assert cand.dest_rel == "Some Movie (2019)/Some Movie (2019) 1080p H.264 DTS.mkv"


def test_reading_the_file_can_be_turned_off(engine, tmp_path):
    cand = engine(read_media_info=False).match(_movie(tmp_path, "Some Movie (2019).mkv"), "movie")
    assert cand.dest_rel == "Some Movie (2019)/Some Movie (2019).mkv"
