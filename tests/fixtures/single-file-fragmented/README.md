# Single-file fragmented fixture

`single_file_fragments.mp4` is the c2pa-rs fixture of the same name
(`sdk/tests/fixtures/single_file_fragments.mp4`): one file holding the
initialization prefix (`ftyp`/`moov`) and three H.264 `moof`/`mdat`
fragments, generated from the synthetic `testsrc2` source with FFmpeg 6.1.1:

```sh
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=32x32:rate=2 -t 3 \
  -c:v libx264 -threads 1 -g 2 -bf 0 \
  -movflags +empty_moov+frag_keyframe+default_base_moof+global_sidx \
  -y single_file_fragments.mp4
```

`Builder.sign_ladder` requires this shape for every rendition. Tests use the
committed bytes and do not require FFmpeg.
