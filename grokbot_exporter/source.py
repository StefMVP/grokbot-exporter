"""Own a separate headless source process. Never open or change the desktop profile."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
from urllib.request import urlopen
from .archive import write_private

PROFILE_FILES = ['sand-secrets.json', 'Local State', 'Preferences', 'Cookies', 'Cookies-journal',
                 'Local Storage', 'Session Storage', 'sand-client-persistence', 'statsigbootstrap']


def launch(app: Path, profile: Path, output: Path, session: str) -> dict:
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.mkdir(mode=0o700, exist_ok=True)
    output.chmod(0o700)
    state_file = output / 'source-process.json'
    if state_file.exists():
        raise ValueError('This source process has a receipt; stop it before starting another')
    clone = output / 'profile'
    clone.mkdir(mode=0o700)
    for name in PROFILE_FILES:
        source = profile / name
        dest = clone / name
        if source.is_dir():
            shutil.copytree(source, dest)
        elif source.is_file():
            shutil.copy2(source, dest)
    for current, dirs, files in os.walk(clone):
        Path(current).chmod(0o700)
        for name in files:
            (Path(current) / name).chmod(0o600)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    log = output / 'source.log'
    fd = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    environment = dict(os.environ, SAND_USER_DATA_DIR=str(clone), SAND_DATA_ROOT=str(output / 'data'))
    with os.fdopen(fd, 'ab') as stream:
        process = subprocess.Popen([str(app), f'--user-data-dir={clone}', '--ozone-platform=headless', '--disable-gpu',
            '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}'], env=environment, stdout=stream, stderr=stream, start_new_session=True)
    state = dict(pid=process.pid, port=port, profile=str(clone), session=session, app=str(app))
    write_private(state_file, state)
    ready = False
    for _ in range(100):
        if process.poll() is not None:
            break
        try:
            with urlopen(f'http://127.0.0.1:{port}/json/version', timeout=0.2) as response:
                ready = response.status == 200
        except OSError:
            pass
        if ready:
            break
        time.sleep(0.2)
    if not ready:
        raise RuntimeError('Headless Grok did not start. Read the private source log and stop the owned process.')
    result = subprocess.run(['playwright-cli', f'-s={session}', 'attach', f'--cdp=http://127.0.0.1:{port}'], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Playwright could not attach. The private process receipt is available for cleanup.')
    return dict(session=session, port=port, pid=process.pid, privateDirectory=str(output))


def stop(output: Path) -> dict:
    state_file = output / 'source-process.json'
    state = json.loads(state_file.read_text())
    command = Path(f'/proc/{state["pid"]}/cmdline')
    if command.exists():
        command_text = command.read_bytes().replace(b'\0', b' ').decode(errors='replace')
        profile_matches = re.search(r'(?:^|\s)--user-data-dir=' + re.escape(state['profile']) + r'(?=\s|$)', command_text) is not None
        app_matches = 'app' not in state or Path(f'/proc/{state["pid"]}/exe').resolve() == Path(state['app']).resolve()
        if not profile_matches or not app_matches:
            raise ValueError('The PID now belongs to another process')
        os.kill(state['pid'], signal.SIGTERM)
    state_file.rename(output / 'source-process-stopped.json')
    return dict(stopped=True, pid=state['pid'])
