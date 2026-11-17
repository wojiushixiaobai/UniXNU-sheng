"""Diagnose the compressed firmware volume that the macOS boot image build consumes.

Prints the environment, then replays boot_template's stages one by one so a failure can be
attributed to the built firmware volume, the pinned template, or the LZMA round trip.

usage: python3 analyze-firmware-volume.py --fd <SILICIUM_UEFI.fd> \
           [--template-dir out/downloads] [--loader-guid GUID]
"""
import argparse
import ctypes
import hashlib
import lzma
import pathlib
import platform
import struct
import sys
import traceback
import uuid

LZMA_GUID = 'ee4e5898-3914-4259-9d6e-dc7bd79403cf'
FILTERS = [{'id': lzma.FILTER_LZMA1, 'dict_size': 1 << 24, 'lc': 3, 'lp': 0, 'pb': 2}]


def size24(data, offset):
    return int.from_bytes(data[offset:offset + 3], 'little')


def align(value, boundary):
    return (value + boundary - 1) & -boundary


def sections(data, start, end):
    while start < end:
        length = size24(data, start)
        if length < 4 or start + length > end:
            raise ValueError(f'section boundary {start} len {length} end {end}')
        yield start, data[start + 3], bytes(data[start:start + length])
        start = align(start + length, 4)


def fv_files(data, offset=0):
    if data[offset + 40:offset + 44] != b'_FVH':
        raise ValueError('no FV header')
    length = struct.unpack_from('<Q', data, offset + 32)[0]
    header = struct.unpack_from('<H', data, offset + 48)[0]
    at, end, files = offset + header, offset + length, []
    while at + 24 <= end and data[at:at + 24] != b'\xff' * 24:
        size = size24(data, at + 20)
        if size < 24 or size == 0xffffff or at + size > end:
            raise ValueError(f'file boundary {at} size {size}')
        files.append((at, str(uuid.UUID(bytes_le=data[at:at + 16])), data[at + 18], size))
        at = align(at + size, 8)
    return header, length, files


def find_stream(firmware):
    """Return (guid, data_at, section) of the first guided section that names the LZMA GUID."""
    _, _, files = fv_files(firmware)
    for at, guid, kind, size in files:
        if kind != 0x0b:
            continue
        for _, sec_kind, section in sections(firmware, at + 24, at + size):
            if sec_kind != 2 or len(section) < 24:
                continue
            guided = str(uuid.UUID(bytes_le=section[4:20]))
            data_at = struct.unpack_from('<H', section, 20)[0]
            if guided == LZMA_GUID:
                return guided, data_at, section
    raise ValueError('no LZMA guided section')


def decode_lzma(stream, label):
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=(64 << 20) * 2)
        out = decoder.decompress(stream, max_length=64 << 20)
        print(f'    {label}: ALONE ok out {len(out)} eof {decoder.eof} unused {len(decoder.unused_data)}')
        return out
    except Exception as error:  # noqa: BLE001
        print(f'    {label}: ALONE FAILED {error!r}')
        return None


def probe_roundtrip():
    """Replay replace_loader's recompress + size patch + re-decode in this environment."""
    payload = bytes(range(256)) * 4000
    packed = bytearray(lzma.compress(payload, format=lzma.FORMAT_ALONE, filters=FILTERS))
    declared = struct.unpack_from('<Q', packed, 5)[0]
    print(f'  probe: encoder declared_size {declared:#x} head {bytes(packed[:13]).hex()}')
    packed[5:13] = len(payload).to_bytes(8, 'little')
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
        out = decoder.decompress(bytes(packed), max_length=len(payload) + 1)
        print(f'  probe: patched-size decode ok out {len(out)} eof {decoder.eof} '
              f'unused {len(decoder.unused_data)}')
    except Exception as error:  # noqa: BLE001
        print(f'  probe: patched-size decode FAILED {error!r}')
    packed2 = bytearray(lzma.compress(payload, format=lzma.FORMAT_ALONE, filters=FILTERS))
    try:
        out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(bytes(packed2),
                                                                         max_length=len(payload) + 1)
        print(f'  probe: unpatched decode ok out {len(out)}')
    except Exception as error:  # noqa: BLE001
        print(f'  probe: unpatched decode FAILED {error!r}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fd', required=True)
    parser.add_argument('--template-dir')
    parser.add_argument('--loader-guid')
    args = parser.parse_args()

    import _lzma
    print(f'python {platform.python_version()} {sys.executable}')
    print(f'_lzma {_lzma.__file__}')
    try:
        library = ctypes.CDLL(_lzma.__file__)
        library.lzma_version_string.restype = ctypes.c_char_p
        print(f'liblzma {library.lzma_version_string().decode()}')
    except Exception as error:  # noqa: BLE001
        print(f'liblzma probe failed {error!r}')
    probe_roundtrip()

    fd_path = pathlib.Path(args.fd)
    print(f'firmware volume: {fd_path} exists {fd_path.is_file()}')
    firmware = fd_path.read_bytes() if fd_path.is_file() else b''
    if not firmware:
        return
    print(f'  size {len(firmware)} sha256 {hashlib.sha256(firmware).hexdigest()}')
    header, length, files = fv_files(firmware)
    print(f'  root FV length {length} header {header} files {len(files)}')
    for at, guid, kind, size in files:
        print(f'  file {guid} type {kind:#04x} size {size}')
    guid, data_at, section = find_stream(firmware)
    stream = section[data_at:]
    print(f'  section guid {guid} data_at {data_at} section {len(section)} stream {len(stream)}')
    print(f'  stream head {stream[:13].hex()}')
    expanded = decode_lzma(stream, 'source FD')
    if expanded:
        try:
            nested = fv_files(expanded, 8)
            print(f'  nested FV header {nested[0]} files {len(nested[2])}')
        except Exception as error:  # noqa: BLE001
            print(f'  nested FV FAILED {error!r}')

    if args.loader_guid:
        sys.path.insert(0, 'scripts')
        try:
            from unixnu import boot_template
            print('  imported unixnu.boot_template')
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return
        try:
            pe = boot_template.loader_pe(firmware, str(uuid.UUID(args.loader_guid)))
            print(f'  loader_pe(source FD) ok bytes {len(pe)}')
        except Exception as error:  # noqa: BLE001
            print(f'  loader_pe(source FD) FAILED {error!r}')
            pe = None

    if not args.template_dir:
        return
    for path in sorted(pathlib.Path(args.template_dir).glob('boot-template-*.img.xz')):
        print(f'template {path.name} {path.stat().st_size} sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}')
        try:
            data = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=(64 << 20) * 8).decompress(
                path.read_bytes(), max_length=256 << 20)
            print(f'  xz ok image {len(data)}')
        except Exception as error:  # noqa: BLE001
            print(f'  xz FAILED {error!r}')
            continue
        try:
            shim, template_fw, tree, image = boot_template.parse_boot_image(data)
            print(f'  parse ok shim {len(shim)} firmware {len(template_fw)} '
                  f'sha256 {hashlib.sha256(template_fw).hexdigest()} tree {len(tree)} image {image}')
        except Exception as error:  # noqa: BLE001
            print(f'  parse_boot_image FAILED {error!r}')
            continue
        try:
            t_guid, t_data_at, t_section = find_stream(template_fw)
            t_stream = t_section[t_data_at:]
            print(f'  section guid {t_guid} data_at {t_data_at} stream {len(t_stream)} '
                  f'head {t_stream[:13].hex()}')
            decode_lzma(t_stream, 'template FD')
        except Exception as error:  # noqa: BLE001
            print(f'  template section FAILED {error!r}')
        if args.loader_guid and pe:
            try:
                replaced, report = boot_template.replace_loader(
                    template_fw, pe, str(uuid.UUID(args.loader_guid)))
                print(f'  replace_loader ok {report}')
            except Exception as error:  # noqa: BLE001
                print(f'  replace_loader FAILED {error!r}')
                traceback.print_exc()


if __name__ == '__main__':
    main()
