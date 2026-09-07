"""Lossless independent RGB frames; all bytes are XOR-masked by the exporter.

Bank array: uint16 little-endian frame offsets, then frame packets.
Packet 0 = 204 literal bytes; packet 1 = (count, R, G, B) runs totaling 68.
No previous-frame state is required, including at loop/intro/outro boundaries.
"""


def pack_frames(frames):
    packets = []
    for colors in frames:
        if len(colors) != 68:
            raise ValueError("Expected 68 RGB pixels")
        raw = bytes(channel for color in colors for channel in color)
        runs = bytearray()
        i = 0
        while i < 68:
            end = i + 1
            while end < 68 and tuple(colors[end]) == tuple(colors[i]):
                end += 1
            runs.extend((end - i, *colors[i]))
            i = end
        packets.append(bytes([1]) + runs if len(runs) < len(raw) else bytes([0]) + raw)
    offset = len(packets) * 2
    index = bytearray()
    for packet in packets:
        if offset > 65535:
            raise ValueError("Compressed effect exceeds 16-bit frame offset capacity")
        index.extend(offset.to_bytes(2, "little"))
        offset += len(packet)
    return bytes(index) + b"".join(packets)


def unpack_frames(data, count):
    data = bytes(data)
    if count < 1 or len(data) < count * 2:
        raise ValueError("Invalid frame index")
    offsets = [int.from_bytes(data[i * 2:i * 2 + 2], "little") for i in range(count)]
    if offsets[0] != count * 2 or any(a >= b for a, b in zip(offsets, offsets[1:])):
        raise ValueError("Invalid frame offsets")
    frames = []
    for start, end in zip(offsets, offsets[1:] + [len(data)]):
        packet = data[start:end]
        if not packet:
            raise ValueError("Missing frame packet")
        if packet[0] == 0:
            if len(packet) != 205:
                raise ValueError("Invalid raw frame")
            pixels = [tuple(packet[i:i + 3]) for i in range(1, 205, 3)]
        elif packet[0] == 1:
            if (len(packet) - 1) % 4:
                raise ValueError("Truncated RLE run")
            pixels = []
            for i in range(1, len(packet), 4):
                n = packet[i]
                if not 1 <= n <= 68 or len(pixels) + n > 68:
                    raise ValueError("Invalid RLE run length")
                pixels.extend([tuple(packet[i + 1:i + 4])] * n)
            if len(pixels) != 68:
                raise ValueError("Incomplete RLE frame")
        else:
            raise ValueError("Unknown frame codec")
        frames.append(pixels)
    return frames
