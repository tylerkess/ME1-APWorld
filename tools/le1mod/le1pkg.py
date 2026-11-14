"""Read and write Mass Effect LE1 packages (.pcc) without Legendary Explorer.

A package is read into its uncompressed image (the bytes the engine works on), edited in place by size-preserving
patches, and written back compressed with the same chunk layout. Oodle blocks go through oodle.ps1, which calls the
game's own oo2core DLL (Windows PowerShell; from WSL through powershell.exe).
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field

TAG = 0x9E2A83C1
OODLE = 0x400
HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass
class Header:
    name_count: int
    name_off: int
    export_count: int
    export_off: int
    import_count: int
    import_off: int
    compression: int
    chunk_table_off: int  # file offset of the first chunk entry; the chunk count is the int before it
    chunks: list[tuple[int, int, int, int]]  # (uncompressed offset, uncompressed size, file offset, file size)


def read_header(data: bytes) -> Header:
    if struct.unpack_from("<I", data, 0)[0] != TAG:
        raise ValueError("not a package")
    group_len = struct.unpack_from("<i", data, 12)[0]
    o = 16 + group_len
    flags, names, name_off, exports, export_off, imports, import_off = struct.unpack_from("<7I", data, o)
    o += 48 + 16  # 12 ints (flags .. thumbnail table) + package GUID
    generations = struct.unpack_from("<i", data, o)[0]
    o += 4 + 12 * generations
    o += 16  # engine version, cooker version, two LE1 ints
    compression, count = struct.unpack_from("<II", data, o)
    o += 8
    chunks = [struct.unpack_from("<4I", data, o + 16 * i) for i in range(count)]
    return Header(names, name_off, exports, export_off, imports, import_off, compression, o, chunks)


@dataclass
class Export:
    index: int  # 1-based
    class_index: int
    outer: int
    name: str
    size: int
    offset: int  # in the uncompressed image
    entry_offset: int  # this entry's position in the export table


@dataclass
class Package:
    path: str
    raw: bytes  # the file as read
    header: Header
    image: bytearray  # uncompressed bytes
    names: list[str] = field(default_factory=list)
    imports: list[tuple[str, str, int]] = field(default_factory=list)  # (class, name, outer)
    exports: list[Export] = field(default_factory=list)

    def object_name(self, index: int) -> str:
        if index > 0:
            return self.exports[index - 1].name
        if index < 0:
            return self.imports[-index - 1][1]
        return ""

    def full_name(self, index: int) -> str:
        parts = []
        while index:
            parts.append(self.object_name(index))
            index = self.exports[index - 1].outer if index > 0 else self.imports[-index - 1][2]
        return ".".join(reversed(parts))

    def class_name(self, export: Export) -> str:
        return self.object_name(export.class_index) if export.class_index else "Class"

    def find(self, class_name: str, full_name: str) -> Export:
        for export in self.exports:
            if self.class_name(export) == class_name and self.full_name(export.index) == full_name:
                return export
        raise KeyError(f"{class_name} {full_name} not in {os.path.basename(self.path)}")

    def name_index(self, name: str) -> int:
        return self.names.index(name)


def _name(names: list[str], index: int, number: int) -> str:
    return names[index] + (f"_{number - 1}" if number > 0 else "")


def _parse_tables(pkg: Package) -> None:
    d, h = pkg.image, pkg.header
    o = h.name_off
    for _ in range(h.name_count):
        n = struct.unpack_from("<i", d, o)[0]
        o += 4
        if n < 0:
            pkg.names.append(bytes(d[o:o - 2 * n]).decode("utf-16-le").rstrip("\0"))
            o += -2 * n
        else:
            pkg.names.append(bytes(d[o:o + n]).decode("latin-1").rstrip("\0"))
            o += n
    o = h.import_off
    for _ in range(h.import_count):
        _pkg, _, cls, _, outer, name, number = struct.unpack_from("<7i", d, o)
        pkg.imports.append((pkg.names[cls], _name(pkg.names, name, number), outer))
        o += 28
    o = h.export_off
    for i in range(1, h.export_count + 1):
        start = o
        cls, _super, outer, name, number, _arch = struct.unpack_from("<6i", d, o)
        size, offset = struct.unpack_from("<ii", d, o + 32)
        generations = struct.unpack_from("<i", d, o + 44)[0]
        o += 48 + 4 * generations + 16 + 4  # entry, generation net object counts, package GUID, package flags
        pkg.exports.append(Export(i, cls, outer, _name(pkg.names, name, number), size, offset, start))


def _run_oodle(mode: str, blocks: list[tuple[bytes, int]], dll: str) -> list[bytes]:
    tmp = tempfile.mkdtemp(prefix="le1oodle")
    try:
        jobs, out = os.path.join(tmp, "jobs.bin"), os.path.join(tmp, "out.bin")
        with open(jobs, "wb") as w:
            w.write(struct.pack("<i", len(blocks)))
            for data, out_size in blocks:
                w.write(struct.pack("<ii", len(data), out_size))
                w.write(data)
        script = _host_path(os.path.join(HERE, "oodle.ps1"))
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
                        "-Dll", _host_path(dll), "-Mode", mode, "-Jobs", _host_path(jobs), "-Out", _host_path(out)],
                       check=True, stdout=subprocess.DEVNULL)
        result, data, o = [], open(out, "rb").read(), 0
        for _ in blocks:
            n = struct.unpack_from("<i", data, o)[0]
            result.append(data[o + 4:o + 4 + n])
            o += 4 + n
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _host_path(path: str) -> str:
    """A path Windows PowerShell can open (WSL paths go through wslpath)."""
    if os.name == "nt":
        return path
    return subprocess.check_output(["wslpath", "-w", path], text=True).strip()


def _chunk_blocks(data: bytes, offset: int) -> tuple[int, list[tuple[bytes, int]]]:
    tag, block_size, _csize, usize = struct.unpack_from("<4I", data, offset)
    if tag != TAG:
        raise ValueError(f"bad chunk tag at {offset}")
    count = (usize + block_size - 1) // block_size
    sizes = [struct.unpack_from("<II", data, offset + 16 + 8 * i) for i in range(count)]
    o, blocks = offset + 16 + 8 * count, []
    for csize, usize_block in sizes:
        blocks.append((data[o:o + csize], usize_block))
        o += csize
    return block_size, blocks


def read(path: str, dll: str) -> Package:
    raw = open(path, "rb").read()
    header = read_header(raw)
    if header.compression == 0:
        image = bytearray(raw)
    else:
        if header.compression != OODLE:
            raise ValueError(f"{path}: compression {header.compression:#x} is not Oodle")
        per_chunk = [_chunk_blocks(raw, c[2])[1] for c in header.chunks]
        decoded = iter(_run_oodle("decompress", [b for blocks in per_chunk for b in blocks], dll))
        image = bytearray(max(u + s for u, s, _, _ in header.chunks))
        first = header.chunks[0][0]
        tail_len = first - header.chunk_table_off  # source/additional-packages fields after the chunk table
        image[:header.chunk_table_off] = raw[:header.chunk_table_off]
        struct.pack_into("<I", image, header.chunk_table_off - 4, 0)
        image[header.chunk_table_off:first] = raw[header.chunk_table_off + 16 * len(header.chunks):
                                                 header.chunk_table_off + 16 * len(header.chunks) + tail_len]
        for (uoff, usize, _, _), blocks in zip(header.chunks, per_chunk):
            image[uoff:uoff + usize] = b"".join(next(decoded) for _ in blocks)
    pkg = Package(path, raw, header, image)
    _parse_tables(pkg)
    return pkg


def write(pkg: Package, path: str, dll: str) -> None:
    """Compress the image back into the original chunk layout (same chunk ranges and block size)."""
    h, raw = pkg.header, pkg.raw
    if h.compression == 0:
        open(path, "wb").write(bytes(pkg.image))
        return
    plan = []  # per chunk: block size and the uncompressed blocks
    for uoff, usize, coff, _ in h.chunks:
        block_size, _ = _chunk_blocks(raw, coff)
        plan.append((block_size, [bytes(pkg.image[s:min(s + block_size, uoff + usize)])
                                  for s in range(uoff, uoff + usize, block_size)]))
    encoded = iter(_run_oodle("compress", [(b, 0) for _, blocks in plan for b in blocks], dll))
    table_end = h.chunk_table_off + 16 * len(h.chunks)
    tail = raw[table_end:h.chunks[0][2]]
    out = bytearray(raw[:h.chunk_table_off]) + bytearray(16 * len(h.chunks)) + bytearray(tail)
    for i, ((uoff, usize, _, _), (block_size, blocks)) in enumerate(zip(h.chunks, plan)):
        packed = [next(encoded) for _ in blocks]
        body = struct.pack("<4I", TAG, block_size, sum(map(len, packed)), usize)
        body += b"".join(struct.pack("<II", len(c), len(u)) for c, u in zip(packed, blocks)) + b"".join(packed)
        struct.pack_into("<4I", out, h.chunk_table_off + 16 * i, uoff, usize, len(out), len(body))
        out += body
    end = max(c + s for _, _, c, s in h.chunks)
    out += raw[end:]  # anything stored after the last chunk
    open(path, "wb").write(bytes(out))


def _header_fields(data: bytes | bytearray) -> int:
    """Offset of the header's package flags (the summary fields follow it)."""
    return 16 + struct.unpack_from("<i", data, 12)[0]


def relayout(pkg: Package, added_names: list[str], replaced: dict[int, bytes]) -> bytes:
    """The package image with names appended to the name table and export data replaced (export index -> bytes).
    Everything after the name table moves, and every offset into it is updated. Write it with write_image."""
    d, h = bytes(pkg.image), pkg.header
    o = _header_fields(d)
    export_off = struct.unpack_from("<i", d, o + 16)[0]
    import_off, depends_off, guids_off = struct.unpack_from("<3i", d, o + 24)
    thumbnail_off = struct.unpack_from("<i", d, o + 44)[0]
    full_header = struct.unpack_from("<i", d, 8)[0]
    names_end = h.name_off
    for _ in range(h.name_count):
        n = struct.unpack_from("<i", d, names_end)[0]
        names_end += 4 + (n if n >= 0 else -2 * n)
    if names_end != import_off:
        raise ValueError(f"{pkg.path}: the import table does not follow the name table")
    new_names = b"".join(struct.pack("<i", len(n) + 1) + n.encode("latin-1") + b"\0" for n in added_names)
    delta = len(new_names)

    head = bytearray(d[:names_end]) + new_names + bytearray(d[names_end:full_header])
    struct.pack_into("<i", head, 8, full_header + delta)
    struct.pack_into("<i", head, o + 4, h.name_count + len(added_names))
    struct.pack_into("<i", head, o + 16, export_off + delta)
    struct.pack_into("<3i", head, o + 24, import_off + delta, depends_off + delta,
                     guids_off + delta if guids_off else 0)
    if thumbnail_off:
        struct.pack_into("<i", head, o + 44, thumbnail_off + delta)
    generations = struct.unpack_from("<i", head, o + 64)[0]
    for g in range(generations):  # each generation records the name count
        struct.pack_into("<i", head, o + 68 + 12 * g + 4, h.name_count + len(added_names))

    ordered = sorted(pkg.exports, key=lambda e: e.offset)
    if ordered and ordered[0].offset < full_header:
        raise ValueError(f"{pkg.path}: export data starts inside the header")
    body, cursor = bytearray(), full_header
    for export in ordered:
        if export.offset != cursor:
            raise ValueError(f"{pkg.path}: export data is not contiguous at {export.offset}")
        data = replaced.get(export.index, d[export.offset:export.offset + export.size])
        struct.pack_into("<ii", head, export.entry_offset + delta + 32, len(data), full_header + delta + len(body))
        body += data
        cursor = export.offset + export.size
    return bytes(head + body + d[cursor:])


def append_exports(pkg: Package, replaced: dict[int, bytes]) -> bytes:
    """The package image with export data replaced (export index -> bytes) by appending it at the end of the image;
    no other byte moves, so offsets stored inside export data (bulk data) stay right. Write it with write_image."""
    image = bytearray(pkg.image)
    by_index = {e.index: e for e in pkg.exports}
    for index, data in replaced.items():
        struct.pack_into("<ii", image, by_index[index].entry_offset + 32, len(data), len(image))
        image += data
    return bytes(image)


def add_names(pkg: Package, names: list[str]) -> list[str]:
    """The names not yet in the package, in order (pass them to relayout)."""
    return [n for n in dict.fromkeys(names) if n not in pkg.names]


CHUNK_SIZE = 0x100000  # uncompressed bytes per chunk when chunks are laid out anew


def _only_appended(pkg: Package, image: bytes) -> bool:
    """Whether `image` is pkg's image with bytes appended (only the export table may differ before its end)."""
    if len(image) < len(pkg.image) or not pkg.exports:
        return False
    last = pkg.exports[-1].entry_offset
    table_end = last + 48 + 4 * struct.unpack_from("<i", pkg.image, last + 44)[0] + 16 + 4  # as _parse_tables
    start = pkg.header.export_off
    return image[:start] == pkg.image[:start] and image[table_end:len(pkg.image)] == pkg.image[table_end:]


def write_image(pkg: Package, image: bytes, path: str, dll: str) -> None:
    """Write an image whose size may differ from pkg's: uncompressed as is, or compressed. An image that only appends
    to pkg's (append_exports) keeps pkg's chunks and adds chunks for the appended bytes; any other is chunked anew."""
    h, raw = pkg.header, pkg.raw
    if h.compression == 0:
        open(path, "wb").write(image)
        return
    header_end = h.chunks[0][0]  # the image header (no chunk table) ends where the first chunk's data starts
    tail = image[h.chunk_table_off:header_end]
    block_size, _ = _chunk_blocks(raw, h.chunks[0][2])
    old_end = max(u + s for u, s, _, _ in h.chunks)
    if old_end == len(pkg.image) and _only_appended(pkg, image):
        # Keep the game's chunk ranges (they end on export boundaries; the game hangs loading a large package
        # whose exports straddle chunks) and put the appended bytes in chunks of their own.
        ranges = [(u, u + s) for u, s, _, _ in h.chunks]
        sizes = [_chunk_blocks(raw, c[2])[0] for c in h.chunks]
        first_new = old_end
    else:
        ranges, sizes, first_new = [], [], header_end
    ranges += [(start, min(start + CHUNK_SIZE, len(image))) for start in range(first_new, len(image), CHUNK_SIZE)]
    sizes += [block_size] * (len(ranges) - len(sizes))
    blocks = [[image[b:min(b + bs, end)] for b in range(start, end, bs)] for (start, end), bs in zip(ranges, sizes)]
    encoded = iter(_run_oodle("compress", [(b, 0) for chunk in blocks for b in chunk], dll))
    out = bytearray(image[:h.chunk_table_off - 4]) + struct.pack("<I", len(ranges)) + bytearray(16 * len(ranges)) + tail
    for i, ((start, end), chunk, bs) in enumerate(zip(ranges, blocks, sizes)):
        packed = [next(encoded) for _ in chunk]
        body = struct.pack("<4I", TAG, bs, sum(map(len, packed)), end - start)
        body += b"".join(struct.pack("<II", len(c), len(u)) for c, u in zip(packed, chunk)) + b"".join(packed)
        struct.pack_into("<4I", out, h.chunk_table_off + 16 * i, start, end - start, len(out), len(body))
        out += body
    open(path, "wb").write(bytes(out))
