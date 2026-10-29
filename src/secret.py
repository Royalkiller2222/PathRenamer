"""Passwords and tokens in config.json are encrypted for the current Windows
user (DPAPI, the same protection browsers use for saved passwords), so a
copied config file doesn't give them away. On other systems (and in tests)
they are kept as they are.

seal()/unseal() are the strict version used for the Pro license state: they
raise OSError instead of falling back to plain text.
"""
import base64
import sys

PREFIX = "dpapi:"
_UI_FORBIDDEN = 0x01


def protect(text: str) -> str:
    if not text or sys.platform != "win32":
        return text or ""
    try:
        return PREFIX + base64.b64encode(_crypt(text.encode("utf-8"), True)).decode("ascii")
    except OSError:
        return text


def unprotect(value: str) -> str:
    """The original text; '' when it can't be decrypted (another user or PC)."""
    if not value or not value.startswith(PREFIX):
        return value or ""
    if sys.platform != "win32":
        return ""
    try:
        return _crypt(base64.b64decode(value[len(PREFIX):]), False).decode("utf-8")
    except (OSError, ValueError):
        return ""


def seal(data: bytes, entropy: bytes = b"") -> bytes:
    """Encrypt and sign data for this Windows user. Raises OSError elsewhere
    or when Windows can't do it."""
    if sys.platform != "win32":
        raise OSError("DPAPI needs Windows")
    return _crypt(data, True, entropy)


def unseal(blob: bytes, entropy: bytes = b"") -> bytes:
    """The data seal() was given. Raises OSError when it was changed, made by
    another Windows user or computer, or sealed with other entropy."""
    if sys.platform != "win32":
        raise OSError("DPAPI needs Windows")
    return _crypt(blob, False, entropy)


def _crypt(data: bytes, encrypt: bool, entropy: bytes = b"") -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    extra = None
    if entropy:                          # must be the same to decrypt
        extra_buffer = ctypes.create_string_buffer(entropy, len(entropy))
        extra = ctypes.byref(Blob(len(entropy), ctypes.cast(extra_buffer, ctypes.POINTER(ctypes.c_char))))
    blob_out = Blob()
    if not fn(ctypes.byref(blob_in), None, extra, None, None, _UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
