# tiny-segmented fixture

A minimal DASH-fragmented MP4 asset set (init + 2 media fragments,
video-only, ~20KB total) used by
`tests/test_builder_sign_fragmented.py` to exercise the
`Builder.sign_fragmented` FFI wrapper.

## Regenerate

From a host with a full ffmpeg build (needs `lavfi` + `libx264` +
`dash` muxer; the bundled StardustProof ffmpeg omits `lavfi` and
cannot produce this fixture):

```bash
ffmpeg -y -f lavfi -i "testsrc=size=320x240:rate=24:duration=2" \
  -an -c:v libx264 -preset veryfast -crf 28 -g 24 -keyint_min 24 \
  -sc_threshold 0 \
  -f dash -seg_duration 1 -use_template 1 -use_timeline 0 \
  -init_seg_name 'init.m4s' -media_seg_name 'seg-$Number%04d$.m4s' \
  -adaptation_sets 'id=0,streams=v' \
  tests/fixtures/tiny-segmented/manifest.mpd
```
