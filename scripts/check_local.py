"""Run the same disposable stack locally and in CI, without reading local config."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent


def main():
    subprocess.run([sys.executable, str(ROOT / 'scripts/privacy_check.py')], cwd=ROOT, check=True)
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                    '-p', 'test_*.py', '-v'], cwd=ROOT, check=True)
    # Start from an allowlist: do not forward DATABASE_URL, Oura credentials,
    # COMPOSE_FILE, COMPOSE_PROJECT_NAME or production environment settings.
    allowed = ('PATH', 'HOME', 'USER', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG',
               'DOCKER_CERT_PATH', 'DOCKER_TLS_VERIFY', 'SSH_AUTH_SOCK', 'TMPDIR',
               'SYSTEMROOT', 'COMSPEC', 'APPDATA', 'LOCALAPPDATA')
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    run_id = uuid.uuid4().hex[:12]
    project = 'personal-coach-test-' + run_id
    env.update(COACH_TEST_RUN_ID=run_id, COMPOSE_DISABLE_ENV_FILE='1')
    compose = ['docker', 'compose', '--env-file', os.devnull,
               '--project-name', project, '--file', str(ROOT / 'compose.test.yaml')]

    def run(*arguments, timeout=600, check=True):
        return subprocess.run([*compose, *arguments], cwd=ROOT, env=env,
                              check=check, timeout=timeout)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        run('config', '--quiet', timeout=30)
        run('build', 'api', 'web', 'checks', 'mcp-checks', timeout=900)
        run('run', '--rm', '--no-deps', 'checks', 'python', '/checks/integration/run_unit.py')
        run('run', '--rm', '--no-deps', 'mcp-checks')
        run('up', '--detach', '--wait', '--wait-timeout', '150',
            'db', 'api', 'fake-worker', 'web', 'browser', timeout=240)
        run('run', '--rm', '--no-deps', 'checks')
        run('run', '--rm', '--no-deps', 'checks', 'python', '-m', 'unittest',
            'discover', '-s', '/checks/e2e', '-p', 'test_*.py', '-v')
        print('Application unit, MCP contract, PostgreSQL integration and Chromium checks passed.', flush=True)
        return 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f'Test command failed ({type(error).__name__}). Only synthetic test data was used.', file=sys.stderr)
        # Service logs contain synthetic data only. No screenshots or artifacts.
        run('logs', '--no-color', '--tail', '60', 'api', 'fake-worker', 'web', timeout=30, check=False)
        return 1
    except KeyboardInterrupt:
        print('Test run interrupted; cleaning up its isolated stack.', file=sys.stderr)
        return 130
    finally:
        # Random, fixed-prefix project; no production compose files or volumes.
        # PostgreSQL storage is tmpfs and disappears with the test container.
        run('down', '--remove-orphans', '--timeout', '10', timeout=45, check=False)


if __name__ == '__main__':
    raise SystemExit(main())
