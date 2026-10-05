"""Start and stop an isolated dashboard for repeatable local/CI browser checks."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', help='Optional installed Chrome executable')
    args = parser.parse_args()
    output = ROOT / 'runs'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='network-browser-') as directory:
        with (output / 'ci-browser.log').open('w+', encoding='utf-8') as log:
            server = subprocess.Popen([sys.executable, '-u', 'dashboard.py', '--port', '0', '--data-dir', directory], cwd=ROOT, stdout=log, stderr=log)
            try:
                url = None
                for _ in range(100):
                    text = (output / 'ci-browser.log').read_text(encoding='utf-8')
                    if text.startswith('Dashboard: '):
                        url = text.split()[1]
                        break
                    if server.poll() is not None:
                        raise RuntimeError('Dashboard startup failed; inspect runs/ci-browser.log')
                    time.sleep(.1)
                if not url:
                    raise RuntimeError('Dashboard startup timed out')
                with urlopen(url + '/api/runs', timeout=5) as response:
                    assert response.status == 200
                command = [sys.executable, 'tests/browser_check.py', '--url', url,
                           '--screenshots', str(output / 'ci-browser-screenshots')]
                if args.chrome:
                    command += ['--chrome', args.chrome]
                subprocess.run(command, cwd=ROOT, check=True)
                subprocess.run([sys.executable, 'tests/browser_recovery_check.py', '--url', url,
                                '--data-dir', directory, *(['--chrome', args.chrome] if args.chrome else [])], cwd=ROOT, check=True)
                subprocess.run([sys.executable, 'tests/browser_governance_check.py', '--url', url,
                                *(['--chrome', args.chrome] if args.chrome else [])], cwd=ROOT, check=True)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == '__main__':
    main()
