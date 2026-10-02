import asyncio
import struct
from urllib.parse import parse_qs


async def topic(host, port, query, timeout=5):
    payload = b"\x00\x00\x00\x00\x00" + query.encode() + b"\x00"
    packet = b"\x00\x83" + struct.pack(">H", len(payload)) + payload
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        writer.write(packet)
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(4), timeout)
        size = struct.unpack(">H", header[2:4])[0]
        body = await asyncio.wait_for(reader.readexactly(size), timeout)
    finally:
        writer.close()
    if body[:1] != b"\x06":
        return {}
    return {k: v[0] for k, v in parse_qs(body[1:-1].decode(errors="replace")).items()}
