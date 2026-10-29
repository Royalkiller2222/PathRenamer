#!/bin/sh
# Rebuilds the tiny sample videos used by tests/test_mediainfo.py (one black
# frame and a little silence each, so they stay a few KB). Needs ffmpeg.
set -e
cd "$(dirname "$0")"
V="-f lavfi -i color=black:r=25"
A="-f lavfi -i anullsrc=sample_rate=48000"
mk() {  # mk <name> <size> <channel layout> <ffmpeg options...>
    name=$1; size=$2; layout=$3; shift 3
    ffmpeg -nostdin -loglevel error -y -f lavfi -i "color=black:s=$size:r=25" \
        -f lavfi -i "anullsrc=channel_layout=$layout:sample_rate=48000" \
        -frames:v 1 -t 0.04 -map_metadata -1 -fflags +bitexact "$@" "$name"
}
X264="-c:v libx264 -preset ultrafast -crf 51"
X265="-c:v libx265 -preset ultrafast -crf 51 -x265-params log-level=none"
mk h264_ac3_51.mkv        1920x800  5.1     $X264 -c:a ac3
mk hevc_eac3_51.mkv       3840x1600 5.1     $X265 -c:a eac3
mk h264_aac_71.mkv        1920x1080 7.1     $X264 -c:a aac
mk h264_dts_51.mkv        1280x720  5.1     $X264 -c:a dca -strict -2
mk av1_opus_20.mkv        1920x1080 stereo  -c:v libsvtav1 -preset 12 -svtav1-params log-level=0 -c:a libopus
mk vp9_vorbis_20.webm     1280x536  stereo  -c:v libvpx-vp9 -deadline realtime -c:a libvorbis
mk h264_truehd_51.mkv     1920x1080 5.1     $X264 -c:a truehd -strict -2
mk mpeg2_mp2_20.mkv       720x576   stereo  -c:v mpeg2video -c:a mp2
mk h264_flac_10.mkv       1440x1080 mono    $X264 -c:a flac
mk h264_aac_51.mp4        1920x1080 5.1     $X264 -c:a aac -movflags +faststart
mk hevc_ac3_51.mp4        3840x2160 5.1     $X265 -tag:v hvc1 -c:a ac3
mk h264_eac3_51.m4v       1280x720  5.1     $X264 -c:a eac3 -f mp4
mk h264_mp3_20.mov        852x480   stereo  $X264 -c:a libmp3lame
mk xvid_mp3_20.avi        720x480   stereo  -c:v libxvid -c:a libmp3lame
mk divx_ac3_51.avi        640x360   5.1     -c:v mpeg4 -vtag DX50 -c:a ac3
