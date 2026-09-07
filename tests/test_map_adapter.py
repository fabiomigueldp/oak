"""Synthetic NBT/MCA regression tests; no private world or game assets required."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

spec = importlib.util.spec_from_file_location('adapter', Path(__file__).resolve().parents[1] / 'map/adapter.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

def chunk():
    root = {'DataVersion': (3, 5018), 'unrelated': (8, 'preserved'), 'sections': (9, (10, [
        {'Y': (1, 0), 'block_states': (10, {'palette': (9, (8, ['example:log', 'example:log[axis=x]'])),
                                          'data': (12, (1, b'\x01' * 8))})},
        {'Y': (1, 1), 'block_states': (10, {'palette': (9, (10, [
            {'': (8, 'example:log')},
            {'id': (8, 'example:leaves'), 'properties': (10, {'persistent': (8, 'true')})},
            {'Name': (8, 'example:log'), 'Properties': (10, {'axis': (8, 'z')})}
        ]))})}
    ]))}
    out = io.BytesIO(b'\x0a\x00\x00'); out.seek(3); a.write_tag(out, 10, root)
    return root, out.getvalue()

def mca(raw, compression=2):
    payload = {1: gzip.compress, 2: zlib.compress, 3: lambda b: b}[compression](raw)
    record = struct.pack('>I', len(payload) + 1) + bytes([compression]) + payload
    sectors = (len(record) + 4095) // 4096
    header = bytearray(8192); header[:4] = b'\x00\x00\x02' + bytes([sectors])
    header[4096:4100] = struct.pack('>I', 123456)
    return bytes(header) + record + bytes(sectors * 4096 - len(record))

class AdapterTests(unittest.TestCase):
    def setUp(self):
        a.DEFAULTS.clear()
        a.DEFAULTS.update({'example:log': {'axis': 'y'},
                          'example:leaves': {'persistent': 'false', 'waterlogged': 'false'}})

    def test_shapes_defaults_and_preservation(self):
        original, raw = chunk(); converted = a.adapt(raw)
        stream = io.BytesIO(converted); stream.read(3); result = a.read_tag(stream, 10)
        self.assertEqual(result['DataVersion'], original['DataVersion'])
        self.assertEqual(result['unrelated'], original['unrelated'])
        sections = result['sections'][1][1]
        self.assertEqual(sections[0]['block_states'][1]['data'], original['sections'][1][1][0]['block_states'][1]['data'])
        palette = sections[0]['block_states'][1]['palette'][1][1]
        self.assertEqual(palette[0]['Properties'][1]['axis'], (8, 'y'))
        self.assertEqual(palette[1]['Properties'][1]['axis'], (8, 'x'))
        palette = sections[1]['block_states'][1]['palette'][1][1]
        self.assertEqual(palette[0]['Name'], (8, 'example:log'))
        self.assertEqual(palette[1]['Properties'][1], {'persistent': (8, 'true'), 'waterlogged': (8, 'false')})
        self.assertEqual(palette[2]['Properties'][1]['axis'], (8, 'z'))
        self.assertEqual(a.adapt(converted), converted)

    def test_region_compression_and_source_integrity(self):
        for compression in (1, 2, 3):
            with self.subTest(compression=compression), tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / 'source.mca'; dst = Path(tmp) / 'result.mca'
                before = mca(chunk()[1], compression); src.write_bytes(before)
                a.region(src, dst); result = dst.read_bytes()
                self.assertEqual(src.read_bytes(), before)
                self.assertEqual(result[4096:8192], before[4096:8192])
                size = int.from_bytes(result[8192:8196], 'big')
                self.assertEqual(zlib.decompress(result[8197:8196 + size]), a.adapt(chunk()[1]))

    def test_invalid_input_does_not_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'source.mca'; dst = Path(tmp) / 'result.mca'
            dst.write_bytes(b'previous output')
            invalid = bytearray(mca(chunk()[1])); invalid[8196] = 130
            for payload in (b'bad header', bytes(invalid)):
                src.write_bytes(payload)
                with self.assertRaises(ValueError): a.region(src, dst)
                self.assertEqual(dst.read_bytes(), b'previous output')
            with self.assertRaises(ValueError): a.region(src, src)

    def test_overlapping_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'source'; src.mkdir()
            for dst in (src, src / 'child', src.parent):
                with self.assertRaises(ValueError): a.validate_paths(src, dst)

    def test_incremental_conversion_and_report_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); src = root / 'source'; src.mkdir(); dst = root / 'output'
            (src / 'r.0.0.mca').write_bytes(mca(chunk()[1]))
            report = root / 'blocks.json'
            report.write_text(json.dumps({key: {'states': [{'default': True, 'properties': value}]} for key, value in a.DEFAULTS.items()}))
            args = ['--source', str(src), '--destination', str(dst), '--blocks-report', str(report), '--min-free-gib', '0']
            a.main(args)
            with patch.object(a, 'region', wraps=a.region) as convert:
                a.main(args); convert.assert_not_called()
                report.write_text(report.read_text() + '\n')
                a.main(args); self.assertEqual(convert.call_count, 1)
                (dst / 'r.0.0.mca').unlink()
                a.main(args); self.assertEqual(convert.call_count, 2)

if __name__ == '__main__':
    unittest.main()
