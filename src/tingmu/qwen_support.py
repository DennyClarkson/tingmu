"""Nagisa tokenizer assets for Qwen alignment, installed with explicit model downloads."""

from .wheel_support import WheelSupport

SUPPORT = WheelSupport(
    name="nagisa-0.2.11",
    cache_group="qwen-support",
    wheel_url=(
        "https://files.pythonhosted.org/packages/6b/f5/6c6206fad84dca34e4383994fab176bd99f9293d731fc3e72bd7da0ab74c/"
        "nagisa-0.2.11-cp312-cp312-win_amd64.whl"
    ),
    wheel_bytes=21_352_973,
    wheel_sha256="6d68ee11ee6e82a37e54ad10885c120ac5e69446fe635d65327c4931b281d996",
    members={
        "nagisa/data/nagisa_v001.dict": (
            6_152_363, "968ac9e6c7a53051ef24d8561673dd31de81b2feb9b5bff01b1d3b6b2473113c"),
        "nagisa/data/nagisa_v001.hp": (
            519, "6737f76b588315fe2fe05d05c99939d3142a0f1c6468f49f4c23e7003192f204"),
        "nagisa/data/nagisa_v001.model": (
            40_274_433, "99e4b64a2f20ca66ae3e0664ebe5f6d087c22c17a7b77f2b7b44a868e1635c06"),
    },
)

inventory = SUPPORT.inventory
install = SUPPORT.install
