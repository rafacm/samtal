"""The Piper provider, as far as a network-free unit test can go.
Actual synthesis needs a downloaded voice; that is the local lane's and
the device checkpoint's job. What runs here depends on whether the GPL
extra is installed: option and download-skip logic when it is, the
helpful registry error when it is not.
"""

import importlib.util
from pathlib import Path

import pytest

from vinga_server.config.models import ProviderConfig
from vinga_server.providers import ProviderError, build_entry

HAS_PIPER = importlib.util.find_spec("piper") is not None


@pytest.mark.skipif(not HAS_PIPER, reason="piper extra not installed")
async def test_a_missing_voice_option_fails_before_any_download() -> None:
    with pytest.raises(ProviderError, match='"voice" is required'):
        await build_entry("tts", "voice", ProviderConfig.model_validate({"type": "piper"}))


@pytest.mark.skipif(not HAS_PIPER, reason="piper extra not installed")
def test_a_present_voice_is_not_downloaded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vinga_server.providers import piper_tts

    downloads: list[str] = []
    monkeypatch.setattr(piper_tts, "download_voice", lambda voice, _dir: downloads.append(voice))

    (tmp_path / "sv_SE-nst-medium.onnx").write_bytes(b"onnx")
    (tmp_path / "sv_SE-nst-medium.onnx.json").write_text("{}")
    onnx = piper_tts.ensure_voice("sv_SE-nst-medium", tmp_path)
    assert onnx == tmp_path / "sv_SE-nst-medium.onnx"
    assert downloads == []

    piper_tts.ensure_voice("en_US-lessac-medium", tmp_path)
    assert downloads == ["en_US-lessac-medium"]


@pytest.mark.skipif(HAS_PIPER, reason="piper extra is installed")
async def test_without_the_extra_the_error_names_it() -> None:
    with pytest.raises(ProviderError) as excinfo:
        await build_entry(
            "tts", "voice", ProviderConfig.model_validate({"type": "piper", "voice": "x"})
        )
    assert "uv sync --extra piper" in str(excinfo.value)
