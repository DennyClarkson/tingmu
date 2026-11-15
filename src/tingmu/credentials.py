"""Windows current-user DPAPI protection; encrypted bytes alone are persisted."""
from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


class CredentialError(RuntimeError):
    pass


def _crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise CredentialError("手工密钥保存需要 Windows DPAPI；请改用环境变量。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise CredentialError("密钥保护失败，或此密钥属于另一台电脑/Windows 用户；请重新输入。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)
        ctypes.memset(buffer, 0, len(data))


def protect(secret: str) -> str:
    return base64.b64encode(_crypt(secret.encode("utf-8"))).decode("ascii")


def unprotect(ciphertext: str) -> str:
    try:
        return _crypt(base64.b64decode(ciphertext, validate=True), decrypt=True).decode("utf-8")
    except (ValueError, UnicodeError):
        raise CredentialError("保存的密钥无法读取；请重新输入。") from None
