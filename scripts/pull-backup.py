"""Download and verify the current restic recovery export. Does not execute game commands."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import re
import subprocess
import tarfile
import uuid

FREE_RESERVE_BYTES = 256 * 1024 ** 2


def preflight(command, destination, hidden, reserve_bytes=FREE_RESERVE_BYTES):
    if type(reserve_bytes) is not int or reserve_bytes < 0:
        raise ValueError('reserve_bytes must be a nonnegative integer.')
    response = subprocess.run(command + ['--estimate'], capture_output=True, check=True, timeout=60, **hidden)
    if len(response.stdout) > 65536:
        raise RuntimeError('Invalid recovery export size estimate.')
    try:
        estimate = json.loads(response.stdout)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('Invalid recovery export size estimate.') from exc
    if (not isinstance(estimate, dict) or type(estimate.get('format')) is not int or estimate['format'] != 1 or
            type(estimate.get('expected_tar_bytes')) is not int or not 0 < estimate['expected_tar_bytes'] <= 2 ** 63 - 1 or
            type(estimate.get('file_count')) is not int or not 0 < estimate['file_count'] <= 2 ** 63 - 1):
        raise RuntimeError('Invalid recovery export size estimate.')
    required = estimate['expected_tar_bytes'] + reserve_bytes
    available = shutil.disk_usage(destination).free
    if available < required:
        raise RuntimeError(f'Recovery export requires {required} free bytes ({estimate["expected_tar_bytes"]} estimated archive bytes plus {reserve_bytes} reserve); {available} bytes are available.')
    return {**estimate, 'reserve_bytes': reserve_bytes, 'required_free_bytes': required, 'available_free_bytes': available}


def sync_directory(path):
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def durable_replace(source, destination):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        win = ctypes.WinDLL('kernel32', use_last_error=True)
        win.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        win.MoveFileExW.restype = wintypes.BOOL
        # Flush the rename before acknowledging the external copy.
        if not win.MoveFileExW(str(source), str(destination), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, destination)
        sync_directory(Path(destination).parent)


def write_json(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        durable_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def secure_windows_path(path, sid, directory):
    """Replace the DACL, including old explicit grants, with owner/System access."""
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    present, defaulted = wintypes.BOOL(), wintypes.BOOL()
    inheritance = 'OICI' if directory else ''
    sddl = f'D:P(A;{inheritance};FA;;;{sid})(A;{inheritance};FA;;;SY)'
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    advapi.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        error = advapi.SetNamedSecurityInfoW(str(path), 1, 0x4 | 0x80000000, None, None, dacl, None)
        if error:
            raise ctypes.WinError(error)
    finally:
        kernel.LocalFree(descriptor)


def secure_destination(destination):
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or getattr(destination, 'is_junction', lambda: False)():
        raise RuntimeError('The backup destination cannot be a symlink or junction.')
    if os.name != 'nt':
        destination.chmod(0o700)
        return
    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
    sid = next(csv.reader(identity.splitlines()))[-1]
    if not re.fullmatch(r'S-\d-(?:\d+-)+\d+', sid):
        raise RuntimeError('The current Windows security identifier is unavailable.')
    secure_windows_path(destination, sid, True)
    for root, directories, files in os.walk(destination, followlinks=False):
        for name in directories + files:
            path = Path(root) / name
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or path.lstat().st_file_attributes & 0x400:
                raise RuntimeError('A backup entry cannot be a Windows reparse point.')
            secure_windows_path(path, sid, name in directories)


def verify(path):
    observed = {}
    manifest = None
    with tarfile.open(path, 'r:') as archive:
        for entry in archive:
            name = PurePosixPath(entry.name)
            if (not entry.isfile() or name.is_absolute() or '..' in name.parts or
                    '\\' in entry.name or ':' in entry.name or str(name) != entry.name or entry.name in observed):
                raise RuntimeError('Invalid archive inventory.')
            stream = archive.extractfile(entry)
            if entry.name == 'manifest.json':
                if manifest is not None or entry.size > 16 * 1024 * 1024:
                    raise RuntimeError('Invalid manifest.')
                manifest = json.load(stream)
            else:
                observed[entry.name] = {'sha256': hashlib.file_digest(stream, 'sha256').hexdigest(), 'bytes': entry.size}
    if not isinstance(manifest, dict) or manifest.get('files') != observed or 'control/repository.key' not in observed or 'control/repository/config' not in observed:
        raise RuntimeError('Recovery export integrity check failed.')
    return manifest


def pull(destination, host='oracle', keep=3, reserve_bytes=FREE_RESERVE_BYTES):
    if not isinstance(host, str) or not host or host.startswith('-') or any(value.isspace() for value in host):
        raise ValueError('SSH host must be a hostname or configured alias.')
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError('keep must be a positive integer.')
    destination = Path(destination).absolute()
    secure_destination(destination)
    hidden = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    lock = (destination / '.pull.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '--', host, 'sudo', '-n', '/opt/oak-operator/current/.venv/bin/python', '/opt/oak-operator/current/scripts/export-backup.py']
        estimate = preflight(command, destination, hidden, reserve_bytes)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = destination / ('oak-recovery-' + stamp + '.tar')
        partial = target.with_suffix('.partial')
        try:
            with partial.open('xb') as output:
                subprocess.run(command, stdout=output, stderr=subprocess.PIPE, check=True, timeout=3600, **hidden)
                output.flush()
                os.fsync(output.fileno())
            manifest = verify(partial)
            with partial.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            durable_replace(partial, target)
            status = {'path': str(target), 'verified_at': datetime.now(timezone.utc).isoformat(), 'sha256': digest, 'files': len(manifest['files']), 'server_acknowledged': False, 'estimate': estimate}
            write_json(destination / 'latest.json', status)
            try:
                ack = subprocess.run(command + ['--ack-sha256', digest, '--bytes', str(target.stat().st_size)], capture_output=True, timeout=30, **hidden)
                if ack.returncode:
                    raise RuntimeError('Server acknowledgement exited with status ' + str(ack.returncode) + '.')
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
                status['ack_error'] = str(exc)[:1000]
                write_json(destination / 'latest.json', status)
                raise RuntimeError('Recovery export is verified locally, but server acknowledgement failed. The archive was retained.') from exc
            status['server_acknowledged'] = True
            write_json(destination / 'latest.json', status)
            for old in sorted(destination.glob('oak-recovery-*.tar'))[:-keep]:
                old.unlink()
            (destination / 'last-error.json').unlink(missing_ok=True)
            sync_directory(destination)
            return status
        finally:
            partial.unlink(missing_ok=True)
    finally:
        lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path.home() / 'Backups' / 'OakRecovery')
    parser.add_argument('--host', default='oracle')
    parser.add_argument('--reserve-mib', type=int, default=256, help='Free-space reserve beyond the estimated archive size (default: 256 MiB).')
    args = parser.parse_args()
    try:
        result = pull(args.destination, args.host, reserve_bytes=args.reserve_mib * 1024 ** 2)
        if __import__('sys').stdout is not None:
            print(json.dumps(result))
    except Exception as exc:
        args.destination.mkdir(parents=True, exist_ok=True)
        write_json(args.destination / 'last-error.json', {'at': datetime.now(timezone.utc).isoformat(), 'error': type(exc).__name__ + ': ' + str(exc)})
        raise
