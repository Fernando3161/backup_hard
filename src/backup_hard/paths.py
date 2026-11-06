"""Deterministic Windows-safe destination name encoding."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass


INVALID = frozenset('<>:"/\\|?*%')
RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})
ESCAPE = re.compile(r"%([0-9A-F]{2})")


@dataclass(frozen=True)
class EncodedName:
    original: str
    encoded: str
    shortened: bool = False


def _escape_character(character: str) -> str:
    return "".join(f"%{byte:02X}" for byte in character.encode("utf-8"))


def needs_encoding(name: str) -> bool:
    """True when Windows would reject or alter the name as written."""
    stripped = name.rstrip(" .")
    return (
        not name or stripped != name
        or any(character in INVALID - {"%"} or ord(character) < 32 for character in name)
        or name.split(".", 1)[0].casefold() in RESERVED
    )


def encode_windows_name(name: str, maximum_length: int = 180) -> EncodedName:
    """Return a Windows-safe file name.

    Names Windows already accepts are kept as they are (only shortened when too
    long). Otherwise every unsafe character, and `%` itself, is percent-escaped
    so the result decodes back to the original. The plan always records the
    original name, so decoding is a convenience, not a requirement.
    """
    if not needs_encoding(name):
        if len(name) <= maximum_length:
            return EncodedName(name, name)
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        suffix = "~" + digest
        stem, dot, extension = name.rpartition(".")
        keep = f"{dot}{extension}" if dot and len(extension) <= 10 else ""
        return EncodedName(name, name[: maximum_length - len(suffix) - len(keep)] + suffix + keep, True)
    if not name:
        name = "[empty]"
    trailing_start = len(name.rstrip(" ."))
    encoded = "".join(
        _escape_character(character)
        if character in INVALID or ord(character) < 32 or index >= trailing_start
        else character
        for index, character in enumerate(name)
    )
    stem = encoded.split(".", 1)[0].casefold()
    if stem in RESERVED:
        encoded = "%5F" + encoded
    if len(encoded) <= maximum_length:
        return EncodedName(name, encoded)
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    suffix = "~" + digest
    return EncodedName(name, encoded[: maximum_length - len(suffix)] + suffix, True)


def decode_windows_name(encoded: str) -> str:
    """Decode non-shortened names; long-name originals come from the manifest."""
    if encoded.startswith("%5F") and encoded[3:].split(".", 1)[0].casefold() in RESERVED:
        encoded = encoded[3:]
    data = bytearray()
    output: list[str] = []
    index = 0
    while index < len(encoded):
        match = ESCAPE.match(encoded, index)
        if match:
            data.append(int(match.group(1), 16))
            index = match.end()
            continue
        if data:
            output.append(data.decode("utf-8"))
            data.clear()
        output.append(encoded[index])
        index += 1
    if data:
        output.append(data.decode("utf-8"))
    return "".join(output)


def destination_collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/")).casefold()
