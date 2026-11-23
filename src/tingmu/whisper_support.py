"""Silero VAD from the faster-whisper wheel pinned in uv.lock."""

from .wheel_support import WheelSupport

FILENAME = "silero_vad_v6.onnx"
SUPPORT = WheelSupport(
    name="faster-whisper-1.2.1-silero-v6",
    cache_group="whisper-support",
    wheel_url=(
        "https://files.pythonhosted.org/packages/05/99/49ee85903dee060d9f08297b4a342e5e0bcfca2f027a07b4ee0a38ab13f9/"
        "faster_whisper-1.2.1-py3-none-any.whl"
    ),
    wheel_bytes=1_118_909,
    wheel_sha256="79a66ad50688c0b794dd501dc340a736992a6342f7f95e5811be60b5224a26a7",
    members={
        "faster_whisper/assets/" + FILENAME: (
            1_245_151, "4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2"
        )
    },
)
install = SUPPORT.install
inventory = SUPPORT.inventory
