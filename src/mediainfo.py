"""Video details read from the file itself: resolution, video codec, audio
codec and channels. They fill in names like "Movie (2010) 1080p H.264 5.1
Dolby Digital" when the file name doesn't say.

Only headers are read (a few KB, or one seek to the end of an MP4 that keeps
its index last), so this is quick even for large files on network drives.
Reads MKV / WebM, MP4 / M4V / MOV and AVI. The names match guessit's, so a
name built from the file reads the same as one built from a release name.
"""
import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_MAX_HEADER = 32 * 1024 * 1024        # never load more than this (an MP4 index)


@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    audio_channels: str = ""

    @property
    def resolution(self) -> str:
        return resolution_label(self.width, self.height)


# ── names ───────────────────────────────────────────────────────────────────
# (min width, min height, label): wide films are labelled by width
# (1920x800 is 1080p), 4:3 ones by height (1440x1080 is 1080p).
_RESOLUTIONS = (
    (6900, 3888, "4320p"), (3456, 1944, "2160p"), (2304, 1296, "1440p"),
    (1728, 972, "1080p"), (1152, 648, "720p"), (922, 547, "576p"),
    (704, 432, "480p"), (576, 324, "360p"),
)


def resolution_label(width, height) -> str:
    """'1080p' for 1920x1080, 1920x800 or 1440x1080; '' when unknown."""
    if not width or not height or width < 0 or height < 0:
        return ""
    for min_w, min_h, label in _RESOLUTIONS:
        if width >= min_w or height >= min_h:
            return label
    return "240p"


_CHANNELS = {1: "1.0", 2: "2.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}


def channels_label(count) -> str:
    return _CHANNELS.get(count, "")


# AC-3 audio coding mode -> full-range channels (1+1 dual mono counts as 2)
_AC3_ACMOD = (2, 1, 2, 3, 3, 4, 4, 5)

_FOURCC_VIDEO = {
    "XVID": "Xvid", "DIVX": "DivX", "DX50": "DivX", "DIV3": "DivX", "DIV4": "DivX",
    "DIV5": "DivX", "DIV6": "DivX", "H264": "H.264", "X264": "H.264", "AVC1": "H.264",
    "DAVC": "H.264", "HEVC": "H.265", "H265": "H.265", "X265": "H.265", "HVC1": "H.265",
    "HEV1": "H.265", "MPG2": "MPEG-2", "WVC1": "VC-1", "WMV3": "VC-1", "VP90": "VP9",
    "VP80": "VP8", "AV01": "AV1",
}

_WAVE_FORMATS = {
    0x0001: "PCM", 0x0003: "PCM", 0x0050: "MP2", 0x0055: "MP3", 0x2000: "Dolby Digital",
    0x2001: "DTS", 0x00FF: "AAC", 0x1600: "AAC", 0x1601: "AAC", 0x706D: "AAC",
    0xF1AC: "FLAC", 0x674F: "Vorbis", 0x6750: "Vorbis", 0x6751: "Vorbis",
    0x676F: "Vorbis", 0x6770: "Vorbis", 0x6771: "Vorbis", 0x704F: "Opus",
}

_MKV_VIDEO = {
    "V_MPEG4/ISO/AVC": "H.264", "V_MPEGH/ISO/HEVC": "H.265", "V_AV1": "AV1",
    "V_VP9": "VP9", "V_VP8": "VP8", "V_MPEG2": "MPEG-2",
}

_MKV_AUDIO = (     # CodecID prefixes, longest first where they overlap
    ("A_EAC3", "Dolby Digital Plus"), ("A_AC3", "Dolby Digital"), ("A_TRUEHD", "Dolby TrueHD"),
    ("A_DTS/LOSSLESS", "DTS-HD"), ("A_DTS", "DTS"), ("A_AAC", "AAC"), ("A_FLAC", "FLAC"),
    ("A_OPUS", "Opus"), ("A_VORBIS", "Vorbis"), ("A_MPEG/L3", "MP3"), ("A_MPEG/L2", "MP2"),
    ("A_PCM", "PCM"),
)

_MP4_VIDEO = {
    b"avc1": "H.264", b"avc2": "H.264", b"avc3": "H.264", b"avc4": "H.264",
    b"hvc1": "H.265", b"hev1": "H.265", b"dvh1": "H.265", b"dvhe": "H.265",
    b"av01": "AV1", b"vp09": "VP9", b"vp08": "VP8", b"vc-1": "VC-1", b"mp2v": "MPEG-2",
}

_MP4_AUDIO = {
    b"ac-3": "Dolby Digital", b"ec-3": "Dolby Digital Plus", b"Opus": "Opus",
    b"fLaC": "FLAC", b"dtsc": "DTS", b"dtse": "DTS", b"dtsh": "DTS-HD", b"dtsl": "DTS-HD",
    b"mlpa": "Dolby TrueHD", b".mp3": "MP3", b"sowt": "PCM", b"twos": "PCM",
    b"lpcm": "PCM", b"ipcm": "PCM", b"in24": "PCM", b"in32": "PCM", b"fl32": "PCM",
}


def _fourcc_video(code: bytes) -> str:
    return _FOURCC_VIDEO.get(code.decode("latin-1").upper(), "")


def _wave_format(data: bytes):
    """(codec, channels label) from a WAVEFORMATEX(TENSIBLE)."""
    if len(data) < 4:
        return "", ""
    tag, channels = struct.unpack_from("<HH", data, 0)
    if tag == 0xFFFE and len(data) >= 26:          # WAVE_FORMAT_EXTENSIBLE: tag is in the GUID
        tag = struct.unpack_from("<H", data, 24)[0]
    return _WAVE_FORMATS.get(tag, ""), channels_label(channels)


# ── entry point ─────────────────────────────────────────────────────────────
def read_media_info(path) -> Optional[MediaInfo]:
    """The file's video details, or None for files that can't be read or
    aren't MKV, MP4 or AVI."""
    try:
        with open(Path(path), "rb") as f:
            f.seek(0, io.SEEK_END)
            size = f.tell()
            f.seek(0)
            magic = f.read(12)
            f.seek(0)
            if magic[:4] == b"\x1a\x45\xdf\xa3":
                info = _read_mkv(f, size)
            elif magic[:4] == b"RIFF" and magic[8:12] == b"AVI ":
                info = _read_avi(f, size)
            elif magic[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip", b"pnot"):
                info = _read_mp4(f, size)
            else:
                return None
    except (OSError, ValueError, EOFError, struct.error, IndexError, UnicodeDecodeError):
        return None
    if info and (info.width or info.video_codec or info.audio_codec):
        return info
    return None


# ── Matroska / WebM ─────────────────────────────────────────────────────────
_SEGMENT, _SEEKHEAD, _TRACKS, _CLUSTER = 0x18538067, 0x114D9B74, 0x1654AE6B, 0x1F43B675


def _vint(read, keep_marker):
    first = read(1)
    if not first:
        raise EOFError
    b = first[0]
    length = 1
    mask = 0x80
    while length <= 8 and not b & mask:
        mask >>= 1
        length += 1
    if length > 8:
        raise ValueError("bad EBML number")
    value = b if keep_marker else b & (mask - 1)
    rest = read(length - 1)
    if len(rest) != length - 1:
        raise EOFError
    for c in rest:
        value = (value << 8) | c
    unknown = not keep_marker and value == (1 << (7 * length)) - 1
    return value, unknown


def _element(read):
    """(id, size or None for unknown size) of the next EBML element."""
    eid, _ = _vint(read, True)
    size, unknown = _vint(read, False)
    return eid, (None if unknown else size)


def _children(data: bytes):
    """(id, payload) of each element inside a master element."""
    stream = io.BytesIO(data)
    while stream.tell() < len(data):
        try:
            eid, size = _element(stream.read)
        except EOFError:
            return
        if size is None:
            return
        payload = stream.read(size)
        yield eid, payload


def _uint(data: bytes) -> int:
    return int.from_bytes(data, "big") if data else 0


def _read_mkv(f, file_size):
    eid, size = _element(f.read)                     # EBML header
    if size is None:
        raise ValueError("bad header")
    f.seek(size, io.SEEK_CUR)
    while True:                                      # find the Segment
        eid, size = _element(f.read)
        if eid == _SEGMENT:
            break
        if size is None:
            raise ValueError("no segment")
        f.seek(size, io.SEEK_CUR)
    seg_start = f.tell()
    seg_end = file_size if size is None else min(file_size, seg_start + size)
    tracks_at = None
    pos = seg_start
    while pos < seg_end:
        f.seek(pos)
        eid, size = _element(f.read)
        data_start = f.tell()
        if eid == _TRACKS and size is not None:
            return _mkv_tracks(f.read(min(size, _MAX_HEADER)))
        if eid == _SEEKHEAD and size is not None:
            for cid, seek in _children(f.read(min(size, 1 << 20))):
                if cid != 0x4DBB:
                    continue
                fields = dict(_children(seek))
                if _uint(fields.get(0x53AB, b"")) == _TRACKS and 0x53AC in fields:
                    tracks_at = seg_start + _uint(fields[0x53AC])
        if eid == _CLUSTER or size is None:          # the video data starts here
            break
        pos = data_start + size
    if tracks_at is not None and tracks_at < file_size:
        f.seek(tracks_at)
        eid, size = _element(f.read)
        if eid == _TRACKS and size is not None:
            return _mkv_tracks(f.read(min(size, _MAX_HEADER)))
    return None


def _mkv_tracks(data: bytes):
    info = MediaInfo()
    video = audio = None
    for eid, entry in _children(data):
        if eid != 0xAE:                              # TrackEntry
            continue
        t = {"type": 0, "codec": "", "default": 1, "enabled": 1, "private": b"",
             "width": 0, "height": 0, "channels": 1}
        for cid, value in _children(entry):
            if cid == 0x83:
                t["type"] = _uint(value)
            elif cid == 0x86:
                t["codec"] = value.decode("ascii", "replace").rstrip("\x00")
            elif cid == 0x88:
                t["default"] = _uint(value)
            elif cid == 0xB9:
                t["enabled"] = _uint(value)
            elif cid == 0x63A2:
                t["private"] = value
            elif cid == 0xE0:                        # Video
                for vid, v in _children(value):
                    if vid == 0xB0:
                        t["width"] = _uint(v)
                    elif vid == 0xBA:
                        t["height"] = _uint(v)
            elif cid == 0xE1:                        # Audio
                for aid, v in _children(value):
                    if aid == 0x9F:
                        t["channels"] = _uint(v)
        if not t["enabled"]:
            continue
        if t["type"] == 1 and video is None:
            video = t
        elif t["type"] == 2 and (audio is None or (t["default"] and not audio["default"])):
            audio = t
    if video:
        info.width, info.height = video["width"], video["height"]
        codec = video["codec"]
        if codec == "V_MS/VFW/FOURCC" and len(video["private"]) >= 20:
            info.video_codec = _fourcc_video(video["private"][16:20])
        else:
            info.video_codec = _MKV_VIDEO.get(codec, "")
    if audio:
        codec = audio["codec"]
        if codec == "A_MS/ACM":
            info.audio_codec, info.audio_channels = _wave_format(audio["private"])
        else:
            info.audio_codec = next((name for prefix, name in _MKV_AUDIO if codec.startswith(prefix)), "")
            info.audio_channels = channels_label(audio["channels"])
    return info


# ── MP4 / MOV ───────────────────────────────────────────────────────────────
def _boxes(data: bytes, start=0, end=None):
    """(type, payload start, payload end) of each box in data[start:end]."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _find(data, start, end, *path):
    """Payload bounds of the first box at path (e.g. b"mdia", b"minf")."""
    for kind, s, e in _boxes(data, start, end):
        if kind == path[0]:
            if len(path) == 1:
                return s, e
            found = _find(data, s, e, *path[1:])
            if found:
                return found
    return None


def _read_mp4(f, file_size):
    pos = 0
    while pos + 8 <= file_size:                      # top-level boxes; moov may be last
        f.seek(pos)
        header = f.read(16)
        if len(header) < 8:
            break
        size, kind = struct.unpack_from(">I4s", header, 0)
        head = 8
        if size == 1 and len(header) >= 16:
            size, head = struct.unpack_from(">Q", header, 8)[0], 16
        elif size == 0:
            size = file_size - pos
        if size < head:
            break
        if kind == b"moov":
            if size > _MAX_HEADER:
                return None
            f.seek(pos + head)
            moov = f.read(size - head)
            return _mp4_moov(moov)
        pos += size
    return None


def _descriptor(data, pos):
    """(tag, payload start, payload end) of an MPEG-4 descriptor."""
    tag = data[pos]
    pos += 1
    length = 0
    for _ in range(4):
        b = data[pos]
        pos += 1
        length = (length << 7) | (b & 0x7F)
        if not b & 0x80:
            break
    return tag, pos, min(pos + length, len(data))


def _esds(data, start, end):
    """(objectTypeIndication, AAC channel configuration) from an esds box."""
    pos = start + 4                                  # version + flags
    tag, s, e = _descriptor(data, pos)
    if tag != 0x03:
        return None, None
    flags = data[s + 2]
    s += 3
    if flags & 0x80:
        s += 2
    if flags & 0x40:
        s += 1 + data[s]
    if flags & 0x20:
        s += 2
    tag, s, e = _descriptor(data, s)
    if tag != 0x04:
        return None, None
    object_type = data[s]
    s += 13
    channels = None
    if s < e:
        tag, ds, de = _descriptor(data, s)
        if tag == 0x05 and de - ds >= 2:             # AudioSpecificConfig
            bits = int.from_bytes(data[ds:ds + 8].ljust(8, b"\x00"), "big")
            left = 64

            def take(n):
                nonlocal left
                left -= n
                return (bits >> left) & ((1 << n) - 1)
            if take(5) == 31:                        # object type escape
                take(6)
            if take(4) == 0xF:                       # explicit sampling rate
                take(24)
            channels = take(4)
    return object_type, channels


def _ac3_channels(acmod, lfe, extra=0):
    return f"{_AC3_ACMOD[acmod] + extra}.{1 if lfe else 0}"


def _mp4_moov(moov: bytes):
    info = MediaInfo()
    have_video = have_audio = False
    for kind, s, e in _boxes(moov):
        if kind != b"trak":
            continue
        tkhd = _find(moov, s, e, b"tkhd")
        if tkhd and not moov[tkhd[0] + 3] & 1:       # track disabled
            continue
        hdlr = _find(moov, s, e, b"mdia", b"hdlr")
        handler = moov[hdlr[0] + 8:hdlr[0] + 12] if hdlr else b""
        stsd = _find(moov, s, e, b"mdia", b"minf", b"stbl", b"stsd")
        if not stsd or stsd[1] - stsd[0] < 16:
            continue
        entry = next(_boxes(moov, stsd[0] + 8, stsd[1]), None)
        if entry is None:
            continue
        fmt, es, ee = entry
        if handler == b"vide" and not have_video:
            have_video = True
            info.width, info.height = struct.unpack_from(">HH", moov, es + 24)
            codec = _MP4_VIDEO.get(fmt, "")
            if fmt == b"mp4v":
                esds = _find(moov, es + 78, ee, b"esds")
                if esds:
                    ot, _ = _esds(moov, *esds)
                    codec = "MPEG-2" if ot is not None and 0x60 <= ot <= 0x65 else ""
            info.video_codec = codec
        elif handler == b"soun" and not have_audio:
            have_audio = True
            version = struct.unpack_from(">H", moov, es + 8)[0]
            channels = struct.unpack_from(">H", moov, es + 16)[0]
            children = es + 28 + {1: 16, 2: 36}.get(version, 0)
            if version == 2:
                channels = struct.unpack_from(">I", moov, es + 40)[0]
            codec = _MP4_AUDIO.get(fmt, "")
            label = channels_label(channels)
            if fmt == b"mp4a":
                esds = _find(moov, children, ee, b"esds") or _find(moov, children, ee, b"wave", b"esds")
                ot, config = _esds(moov, *esds) if esds else (0x40, None)
                codec = {0x69: "MP3", 0x6B: "MP3"}.get(ot, "AAC")
                if config:
                    label = {1: "1.0", 2: "2.0", 3: "3.0", 4: "4.0", 5: "5.0", 6: "5.1",
                             7: "7.1"}.get(config, label)
            elif fmt == b"ac-3":
                box = _find(moov, children, ee, b"dac3")
                if box and box[1] - box[0] >= 3:
                    bits = int.from_bytes(moov[box[0]:box[0] + 3], "big")
                    label = _ac3_channels((bits >> 11) & 0x7, (bits >> 10) & 0x1)
            elif fmt == b"ec-3":
                box = _find(moov, children, ee, b"dec3")
                if box and box[1] - box[0] >= 5:
                    bits = int.from_bytes(moov[box[0] + 2:box[0] + 5], "big")
                    acmod, lfe = (bits >> 9) & 0x7, (bits >> 8) & 0x1
                    dependent = (bits >> 1) & 0xF
                    label = _ac3_channels(acmod, lfe, 2 if dependent else 0)
            elif fmt == b"Opus":
                box = _find(moov, children, ee, b"dOps")
                if box and box[1] - box[0] >= 2:
                    label = channels_label(moov[box[0] + 1]) or label
            info.audio_codec, info.audio_channels = codec, label
    return info


# ── AVI ─────────────────────────────────────────────────────────────────────
def _read_avi(f, file_size):
    pos = 12
    while pos + 12 <= file_size:
        f.seek(pos)
        kind, size, list_type = struct.unpack("<4sI4s", f.read(12))
        if kind == b"LIST" and list_type == b"hdrl":
            return _avi_header(f.read(min(size - 4, _MAX_HEADER)))
        pos += 8 + size + (size & 1)
    return None


def _avi_chunks(data, start=0, end=None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        kind, size = struct.unpack_from("<4sI", data, pos)
        yield kind, pos + 8, min(pos + 8 + size, end)
        pos += 8 + size + (size & 1)


def _avi_header(hdrl: bytes):
    info = MediaInfo()
    have_video = have_audio = False
    for kind, s, e in _avi_chunks(hdrl):
        if kind != b"LIST" or hdrl[s:s + 4] != b"strl":
            continue
        stream_type, fmt = b"", None
        for ck, cs, ce in _avi_chunks(hdrl, s + 4, e):
            if ck == b"strh":
                stream_type = hdrl[cs:cs + 4]
            elif ck == b"strf":
                fmt = hdrl[cs:ce]
        if fmt is None:
            continue
        if stream_type == b"vids" and not have_video and len(fmt) >= 20:
            have_video = True
            width, height = struct.unpack_from("<ii", fmt, 4)
            info.width, info.height = width, abs(height)
            info.video_codec = _fourcc_video(fmt[16:20])
        elif stream_type == b"auds" and not have_audio:
            have_audio = True
            info.audio_codec, info.audio_channels = _wave_format(fmt)
    return info
