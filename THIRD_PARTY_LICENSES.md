# Third-party licenses

Vinga builds on and derives from the following projects. Their license notices
are reproduced here as required by the MIT license; they also apply to any
substantial portions of their code included in this repository.

## 78/xiaozhi-esp32

<https://github.com/78/xiaozhi-esp32>

```
MIT License

Copyright (c) 2025 Shenzhen Xinzhi Future Technology Co., Ltd.
Copyright (c) 2025 Project Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## xinnan-tech/xiaozhi-esp32-server

<https://github.com/xinnan-tech/xiaozhi-esp32-server>

```
MIT License

Copyright (c) 2025 xinnan-tech

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Notes on other components

- Wake-word models (ESP-SR) are licensed by Espressif for use on Espressif
  chips.
- ASR/VAD model weights (e.g. SenseVoiceSmall, Silero VAD) are downloaded at
  deploy time and carry their own licenses; they are not redistributed here.
- The `edge-tts` Python package is GPL-3.0; Vinga treats TTS engines as
  optional pluggable providers so that the core server does not depend on it.

## The published container image

`ghcr.io/rafacm/vinga-server` is published in two variants. The
**default** one (unsuffixed tags) bundles vinga-server with both of its
optional local engines, so that one `docker run` serves a conversation
without a cloud account. Everything in this section describes that
variant. The **slim** one (`-slim` tags) installs no optional extra, and
so contains neither of the copyleft components below except PyAV, which
is a core dependency.

Two of the default variant's contents carry copyleft terms:

- **piper-tts** (piper1-gpl) is **GPL-3.0**. It is installed in the
  default image as an independent, unmodified package that
  vinga-server calls through
  its ordinary Python API; the two are aggregated on one filesystem, not
  combined into a derived work. vinga-server itself remains MIT and does
  not depend on piper outside the optional `piper` extra. Corresponding
  source for piper-tts is available from
  <https://github.com/OHF-voice/piper1-gpl>.
- **PyAV** ships a bundled FFmpeg build, most of which is **LGPL-2.1 or
  later**. It is likewise installed unmodified and dynamically linked, and
  its source is available from <https://github.com/PyAV-Org/PyAV> and
  <https://ffmpeg.org>.

Model weights are never included in the image. Whisper models and Piper
voices download at first start into the mounted `/data` volume, under
their own licenses.

An image without the GPL engine is a reasonable thing to want, and the
`slim` variant is it: no optional extras, so no piper-tts and no
GPL-3.0 component. PyAV and its bundled FFmpeg remain, being a core
dependency of the server itself.
