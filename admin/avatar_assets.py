"""Private, bounded skin cache. Remote requests have a single fixed HTTPS host."""
from collections import OrderedDict
import re
import struct
import threading
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SkinCache:
    def __init__(self):
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        if not re.fullmatch('[a-f0-9]{32,64}', key):
            raise ValueError('Invalid texture identifier.')
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            request = urllib.request.Request('https://textures.minecraft.net/texture/' + key, headers={'User-Agent': 'Oak-Control/1.0'})
            with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
                data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024 or data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) < 24:
                raise ValueError('Invalid skin texture.')
            width, height = struct.unpack('>II', data[16:24])
            if width < 64 or width > 512 or height not in (width, width // 2):
                raise ValueError('Unsupported skin dimensions.')
            self.cache[key] = data
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return data
