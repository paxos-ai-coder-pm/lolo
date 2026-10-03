"""Lossless compressor: order-0 canonical Huffman coding over bytes.

Format:
    4 bytes   original length (big-endian)
    256 bytes code length for each byte value (0 = unused)    [omitted if length is 0]
    rest      Huffman bitstream, MSB first, zero-padded to a whole byte
"""

import heapq
from collections import Counter


def _code_lengths(freqs):
    """Return a list of 256 Huffman code lengths for the given byte frequencies."""
    lengths = [0] * 256
    symbols = [s for s in range(256) if freqs[s]]
    if len(symbols) == 1:
        lengths[symbols[0]] = 1
        return lengths
    # Heap entries: (weight, tiebreak, list of symbols in this subtree)
    heap = [(freqs[s], s, [s]) for s in symbols]
    heapq.heapify(heap)
    tiebreak = 256
    while len(heap) > 1:
        w1, _, s1 = heapq.heappop(heap)
        w2, _, s2 = heapq.heappop(heap)
        for s in s1:
            lengths[s] += 1
        for s in s2:
            lengths[s] += 1
        heapq.heappush(heap, (w1 + w2, tiebreak, s1 + s2))
        tiebreak += 1
    return lengths


def _canonical_codes(lengths):
    """Assign canonical codes: dict symbol -> (code, length)."""
    order = sorted((l, s) for s, l in enumerate(lengths) if l)
    codes = {}
    code = 0
    prev_len = order[0][0] if order else 0
    for l, s in order:
        code <<= l - prev_len
        codes[s] = (code, l)
        code += 1
        prev_len = l
    return codes


def compress(data: bytes) -> bytes:
    n = len(data)
    header = n.to_bytes(4, "big")
    if n == 0:
        return header
    counts = Counter(data)
    freqs = [counts.get(s, 0) for s in range(256)]
    lengths = _code_lengths(freqs)
    codes = _canonical_codes(lengths)
    table = [""] * 256
    for s, (c, l) in codes.items():
        table[s] = format(c, "0%db" % l)
    bits = "".join([table[b] for b in data])
    pad = (-len(bits)) % 8
    bits += "0" * pad
    payload = int(bits, 2).to_bytes(len(bits) // 8, "big")
    return header + bytes(lengths) + payload


def decompress(blob: bytes) -> bytes:
    n = int.from_bytes(blob[:4], "big")
    if n == 0:
        return b""
    lengths = list(blob[4:260])
    payload = blob[260:]
    codes = _canonical_codes(lengths)
    lookup = {(l, c): s for s, (c, l) in codes.items()}
    bits = format(int.from_bytes(payload, "big"), "0%db" % (len(payload) * 8))
    out = bytearray()
    append = out.append
    code = 0
    length = 0
    for ch in bits:
        code = (code << 1) | (ch == "1")
        length += 1
        s = lookup.get((length, code))
        if s is not None:
            append(s)
            if len(out) == n:
                break
            code = 0
            length = 0
    return bytes(out)
