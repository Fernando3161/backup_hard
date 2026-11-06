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


def encode_windows_name(name: str, maximum_length: int = 180) -> EncodedName:
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
