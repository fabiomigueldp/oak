"""Bounded read-only NBT fingerprint, excluding empty-server clock churn."""
import gzip
import hashlib
import io
import struct


VOLATILE = {'LastPlayed', 'Time', 'DayTime', 'rainTime', 'thunderTime', 'clearWeatherTime'}


def fingerprint(path):
    raw = path.read_bytes()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as archive:
            data = archive.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('Metadata exceeds fingerprint workspace.')
        stream = io.BytesIO(data)

        def read(size):
            if size < 0 or size > len(data):
                raise ValueError('Invalid NBT size.')
            result = stream.read(size)
            if len(result) != size:
                raise ValueError('Truncated NBT.')
            return result

        def number(fmt):
            return struct.unpack('>' + fmt, read(struct.calcsize('>' + fmt)))[0]

        def name():
            return read(number('H')).decode('utf-8')

        def value(tag, location=()):
            if len(location) > 64:
                raise ValueError('NBT nesting exceeds fingerprint workspace.')
            if tag in (1, 2, 3, 4, 5, 6):
                return read({1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}[tag])
            if tag == 8:
                return read(number('H'))
            if tag in (7, 11, 12):
                return read(number('i') * {7: 1, 11: 4, 12: 8}[tag])
            if tag == 9:
                subtype, count = number('B'), number('i')
                if not 0 <= count <= 1000000:
                    raise ValueError('Invalid NBT list.')
                return subtype, tuple(value(subtype, location + ('[]',)) for _ in range(count))
            if tag == 10:
                entries = []
                while child := number('B'):
                    key = name()
                    item = value(child, location + (key,))
                    if location != ('Data',) or key not in VOLATILE:
                        entries.append((key, child, item))
                return tuple(sorted(entries))
            raise ValueError('Unsupported NBT tag.')

        tag = number('B')
        name()
        normalized = value(tag)
        if stream.read(1):
            raise ValueError('Trailing NBT data.')
        return hashlib.sha256(repr(normalized).encode()).hexdigest()
    except (OSError, ValueError, EOFError, struct.error, RecursionError):
        # Unknown metadata remains backup-worthy instead of being silently ignored.
        return hashlib.sha256(raw).hexdigest()
