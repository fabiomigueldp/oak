"""Explicit service configuration; no credentials are stored in source code."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    state: Path
    origin: str = 'https://oak.fabiomigueldp.me'
    socket: str = '/run/oak-control/agent.sock'
    demo: bool = False
    session_seconds: int = 12 * 3600
    poll_seconds: int = 5

    def __post_init__(self):
        url = urlsplit(self.origin)
        if url.path or url.query or url.fragment or url.username or url.password or not url.hostname:
            raise ValueError('OAK_ADMIN_ORIGIN must be a bare origin.')
        if self.demo and url.hostname not in ('localhost', '127.0.0.1'):
            raise ValueError('Demonstration mode is restricted to localhost.')
        if url.scheme != 'https' and not (self.demo and url.hostname in ('localhost', '127.0.0.1')):
            raise ValueError('HTTPS is required outside local demonstration mode.')

    @property
    def rp_id(self):
        return urlsplit(self.origin).hostname

    @property
    def secure(self):
        return self.origin.startswith('https://')

    @property
    def cookie(self):
        return '__Secure-oak_session' if self.secure else 'oak_demo_session'

    @classmethod
    def from_env(cls):
        demo = os.environ.get('OAK_ADMIN_DEMO') == '1'
        state = Path(os.environ.get('OAK_ADMIN_STATE', '/var/lib/oak-control'))
        origin = os.environ.get('OAK_ADMIN_ORIGIN', 'http://localhost:8092' if demo else 'https://oak.fabiomigueldp.me')
        return cls(state=state, origin=origin, socket=os.environ.get('OAK_ADMIN_SOCKET', '/run/oak-control/agent.sock'), demo=demo)
