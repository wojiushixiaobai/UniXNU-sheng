"""Relax the firmware volume decoder in the UniXNU core checkout.

The qualified boot template path decodes the LZMA guided section of the freshly built firmware
volume, of the pinned template, and of the volume it just recompressed. The recompressed stream
carries the real uncompressed size in its header and an end marker from the encoder; liblzma
older than 5.4 refuses that pair ("Corrupt input data") although the firmware's own SDK decoder
accepts it. Fall back to the raw LZMA1 framing, which decodes the same bytes on every liblzma.

usage: python3 patch_boot_template_lzma.py [path/to/boot_template.py]
"""
import pathlib
import sys

DEFAULT = 'scripts/unixnu/boot_template.py'

ORIGINAL = """            try:
                decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=MAX_FV * 2)
                expanded = decoder.decompress(section[data_at:], max_length=MAX_FV + 1)
            except lzma.LZMAError as error:
                raise Error(f'invalid compressed firmware volume: {error}') from error
            if len(expanded) > MAX_FV or not decoder.eof or decoder.unused_data:
                raise Error('compressed firmware volume exceeds bounds or has trailing data')
"""

PATCHED = """            expanded = decode_volume(section[data_at:])
"""

HELPER = '''def decode_volume(stream):
    """Decode a guided LZMA section, falling back to the raw framing old liblzma insists on."""
    errors = []
    for limit in (MAX_FV * 2, 1 << 31):
        try:
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=limit)
            expanded = decoder.decompress(stream, max_length=MAX_FV + 1)
        except lzma.LZMAError as error:
            errors.append(str(error))
            continue
        if len(expanded) > MAX_FV or not decoder.eof or decoder.unused_data:
            errors.append('compressed firmware volume exceeds bounds or has trailing data')
            continue
        return bytes(expanded)
    if len(stream) > 13 and stream[0] < 225:
        properties = stream[0]
        filters = [{'id': lzma.FILTER_LZMA1,
                    'dict_size': max(struct.unpack_from('<I', stream, 1)[0], 1 << 16),
                    'lc': properties % 9, 'lp': (properties // 9) % 5,
                    'pb': properties // 45}]
        for data in (stream[13:], stream):
            try:
                decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
                expanded = decoder.decompress(data, max_length=MAX_FV + 1)
            except lzma.LZMAError as error:
                errors.append(str(error))
                continue
            if 0 < len(expanded) <= MAX_FV:
                return bytes(expanded)
            errors.append('compressed firmware volume exceeds bounds')
    raise Error('invalid compressed firmware volume: ' + errors[-1])


'''


def main():
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
    text = path.read_text()
    if 'def decode_volume(' in text:
        print(f'{path}: already patched')
        return
    if text.count(ORIGINAL) != 1:
        raise SystemExit(f'{path}: the compressed_volume decoder does not match this core revision')
    marker = "LZMA_GUID = uuid.UUID('ee4e5898-3914-4259-9d6e-dc7bd79403cf').bytes_le\nMAX_FV = 64 << 20\n"
    if text.count(marker) != 1:
        raise SystemExit(f'{path}: the firmware constants do not match this core revision')
    text = text.replace(marker, marker + '\n\n' + HELPER.rstrip('\n') + '\n')
    text = text.replace(ORIGINAL, PATCHED)
    path.write_text(text)
    print(f'{path}: decoder relaxed')


if __name__ == '__main__':
    main()
