"""Install the wheel and run the tests outside the source checkout."""
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import venv
import zipfile

repository = Path(__file__).resolve().parents[1]
wheels = list((repository / 'dist').glob('grokbot_exporter-*.whl'))
if len(wheels) != 1:
    raise SystemExit('Build one wheel with python -m build first')
wheel = wheels[0]
source = wheel.parent / 'grokbot_exporter-0.1.0.tar.gz'
with tarfile.open(source) as package:
    names = {'/'.join(name.split('/')[1:]) for name in package.getnames()}
    required = {'tests/twins.py', 'tests/twin_playwright.js', 'tests/fixtures/roster.json',
                'grokbot_exporter/adapters/openmaus/README.md', 'config.example.json', 'scripts/check-package.py'}
    if not required <= names:
        raise SystemExit('Source package is missing test or documentation files')
with zipfile.ZipFile(wheel) as package:
    names = set(package.namelist())
    required = {'grokbot_exporter/capture.js', 'grokbot_exporter/attach_reader.js',
                'grokbot_exporter/cloud_export.py', 'grokbot_exporter/adapters/openmaus/apply.py',
                'grokbot_exporter/adapters/openmaus/README.md'}
    if not required <= names or any(name.startswith(('tests/', 'private/', 'exports/', 'grokbot_port/')) for name in names):
        raise SystemExit('Wheel contents do not match the package contract')

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    environment = root / 'venv'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / 'bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', str(wheel)], check=True, cwd=root)
    subprocess.run([str(environment / 'bin/grokbot-exporter'), '--help'], check=True, cwd=root)
    env = dict(os.environ, GROKBOT_TEST_INSTALLED='1')
    env.pop('PYTHONPATH', None)
    subprocess.run([str(python), '-m', 'unittest', 'discover', '-s', str(repository / 'tests'), '-v'],
                   check=True, cwd=root, env=env)
