"""Start the local EKT site and backend with an isolated synthetic catalog."""
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if not PYTHON.exists():
    raise SystemExit('Create .venv and install backend dependencies as described in README.md.')


def main():
    data = ROOT / 'backend/data'
    data.mkdir(exist_ok=True)
    fixture = data / 'demo-catalog.json'
    fixture.write_text((ROOT / 'backend/examples/demo_catalog.json').read_text(encoding='utf-8'), encoding='utf-8')
    env = os.environ.copy()
    env.update({
        'DEMO_CART_ENABLED': 'true', 'EKT_LIVE_ENABLED': 'false',
        'OPENAI_API_KEY': '', 'CATALOG_DB': str(data / 'demo.sqlite3'),
        'CATALOG_STALE_SECONDS': '3600', 'PYTHONIOENCODING': 'utf-8',
    })
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    subprocess.run([str(PYTHON), '-m', 'app.import_catalog', '--files', str(fixture), '--source', 'synthetic'], cwd=ROOT / 'backend', env=env, check=True, creationflags=flags)
    children = []
    try:
        children.append(subprocess.Popen([str(PYTHON), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'], cwd=ROOT / 'backend', env=env, creationflags=flags))
        children.append(subprocess.Popen([str(PYTHON), '-m', 'http.server', '5173', '--bind', '127.0.0.1', '--directory', str(ROOT / 'frontend')], cwd=ROOT, env=env, creationflags=flags))
        print('EKT Assistant: http://127.0.0.1:5173 | Synthetic demo, no external API calls. Ctrl+C to stop.', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            child.wait(timeout=10)


if __name__ == '__main__':
    main()
