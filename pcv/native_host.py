"""Windows Chrome native messaging host for launching the local PCV desktop."""
import http.client
import json
import re
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

from .companion import PORT, desktop_ready_path


HOST = 'org.pcv.launcher'
PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / '.pcv'


def install(extension_id):
    import winreg

    if not re.fullmatch('[a-p]{32}', extension_id):
        raise ValueError('Expected the 32-character Chrome extension ID.')
    ROOT.mkdir(exist_ok=True)
    launcher = ROOT / 'chrome-native-host.cmd'
    # CMD expands percent signs even inside quotes.
    python = sys.executable.replace('%', '%%')
    launcher.write_text(
        '@echo off\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\n'
        'cd /d "%~dp0.." || exit /b 1\n'
        f'"{python}" -I -c "import sys; sys.path.insert(0, \'.\'); from pcv.native_host import main; main()"\n',
        encoding='utf-8',
    )
    manifest = ROOT / 'chrome-native-host.json'
    manifest.write_text(json.dumps({
        'name': HOST, 'description': 'PCV desktop launcher', 'path': str(launcher),
        'type': 'stdio', 'allowed_origins': [f'chrome-extension://{extension_id}/'],
    }, indent=2), encoding='utf-8')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'Software\Google\Chrome\NativeMessagingHosts\{HOST}') as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(manifest))


def receiver_ready():
    """True for this vault, False for an occupied port, None if available."""
    # Windows can time out on an unused loopback port instead of refusing promptly.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', PORT))
        except OSError:
            pass
        else:
            return None
    connection = http.client.HTTPConnection('127.0.0.1', PORT, timeout=0.3)
    try:
        connection.request('GET', desktop_ready_path(ROOT))
        return connection.getresponse().status == 204
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def start(minimized=False):
    status = receiver_ready()
    if status is True:
        return {'ok': True}
    if status is None:
        python = Path(sys.executable)
        windowed = python.with_name('pythonw.exe')
        command = [str(windowed if windowed.is_file() else python), '-m', 'pcv.desktop', '--quiet-start']
        if minimized:
            command.append('--minimized')
        subprocess.Popen(
            command,
            cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    deadline = time.monotonic() + 7
    while time.monotonic() < deadline:
        time.sleep(0.15)
        result = receiver_ready()
        if result is True:
            return {'ok': True}
    return {'ok': False, 'error': 'port_in_use' if status is False else 'start_failed'}


def handle(source):
    try:
        length, = struct.unpack('<I', source.read(4))
        if not 0 < length <= 1024:
            return {'ok': False, 'error': 'invalid_request'}
        payload = source.read(length)
        request = json.loads(payload)
        if (len(payload) != length or not isinstance(request, dict) or request.get('action') != 'start' or
                request.keys() - {'action', 'minimized'} or not isinstance(request.get('minimized', False), bool)):
            return {'ok': False, 'error': 'invalid_request'}
    except (struct.error, ValueError):
        return {'ok': False, 'error': 'invalid_request'}
    try:
        return start(request.get('minimized', False))
    except OSError:
        return {'ok': False, 'error': 'start_failed'}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--install':
        install(sys.argv[2])
        return
    response = json.dumps(handle(sys.stdin.buffer)).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('<I', len(response)) + response)
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
