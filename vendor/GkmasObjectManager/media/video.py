"""
media/video.py
USM video conversion plugin for GkmasResource.
"""

import subprocess

from .dummy import GkmasDummyMedia


class GkmasUSMVideo(GkmasDummyMedia):
    """Conversion plugin for USM videos."""

    def _init_mimetype(self):
        self.mimetype = "video"
        self.default_converted_format = "mp4"

    def _convert(self, raw: bytes) -> bytes:

        probe = subprocess.run(
            [
                "ffprobe",
                "pipe:0",
                "-v",
                "error",  # hide verbose info
                "-select_streams",
                "v:0",  # first video stream
                "-show_entries",
                "stream=codec_name",  # query
                "-of",
                "default=noprint_wrappers=1:nokey=1",  # hide formatting
            ],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        cmd = [
            "ffmpeg",
            "-i",
            "pipe:0",  # input bytestream
            "-f",
            self.converted_format,
        ]
        if probe.stdout.strip() == b"mpeg1video":  # should re-encode
            cmd += ["-preset", "ultrafast"]  # -c:v inferred from -f
        else:
            cmd += ["-c:v", "copy"]
        cmd += [
            "-c:a",
            "aac",  # was adpcm_adx in source
            "-b:a",
            "1024k",  # auto fallback to highest possible bitrate
            "-movflags",
            "faststart+frag_keyframe",
            # otherwise libx264 reports 'muxer does not support non seekable output'
            "pipe:1",  # output bytestream
        ]

        return subprocess.run(
            cmd,
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # disposed
            check=True,
        ).stdout
