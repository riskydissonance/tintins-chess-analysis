"""Ported arm64/Rosetta Stockfish detection: the dependency-free Mach-O arch reader, exercised on
crafted headers so no real binary or subprocess is needed. See server/config.macho_arch."""
import sys

import pytest

from server import config

# (magic bytes, 4-byte cputype in the magic's endianness, expected arch)
CASES = [
    (b"\xcf\xfa\xed\xfe", (0x0100000C).to_bytes(4, "little"), "arm64"),   # 64-bit LE, arm64
    (b"\xcf\xfa\xed\xfe", (0x01000007).to_bytes(4, "little"), "x86_64"),  # 64-bit LE, x86_64
    (b"\xce\xfa\xed\xfe", (0x00000007).to_bytes(4, "little"), "x86_64"),  # 32-bit LE, i386 (base 7)
    (b"\xfe\xed\xfa\xcf", (0x0100000C).to_bytes(4, "big"), "arm64"),      # 64-bit BE magic, arm64
    (b"\xca\xfe\xba\xbe", b"\x00\x00\x00\x02", "universal"),              # fat/universal
    (b"\x7fELF", b"\x00\x00\x00\x00", "unknown"),                          # not Mach-O (Linux ELF)
]


@pytest.mark.parametrize("magic,cputype,expected", CASES)
def test_macho_arch(tmp_path, magic, cputype, expected):
    f = tmp_path / "bin"
    f.write_bytes(magic + cputype)
    assert config.macho_arch(str(f)) == expected


def test_macho_arch_missing_or_short(tmp_path):
    assert config.macho_arch(str(tmp_path / "does-not-exist")) == "unknown"
    short = tmp_path / "short"
    short.write_bytes(b"\xcf\xfa")  # < 8 bytes
    assert config.macho_arch(str(short)) == "unknown"


@pytest.mark.skipif(sys.platform == "darwin", reason="hardware-dependent on macOS")
def test_is_apple_silicon_false_off_darwin():
    assert config.is_apple_silicon() is False
