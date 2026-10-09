"""Exercise release orchestration without a VPS or Docker daemon."""

import os
import subprocess
from pathlib import Path


def fake_host(tmp_path: Path) -> tuple[dict[str, str], Path]:
    executable = tmp_path / "bin"
    executable.mkdir()
    journal = tmp_path / "commands"
    script = executable / "docker"
    script.write_text("""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['COMMANDS'],'a') as f:f.write(json.dumps(args)+'\\n')
if 'ratatouille.backup' in args:
    if os.environ.get('FAIL_BACKUP'):sys.exit(1)
    marker=Path(os.environ['BACKUP_DIR'])/Path(args[-1]).name
    if marker.exists():sys.exit(1)
    marker.write_text('backup')
""")
    script.chmod(0o755)
    curl = executable / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    backups = tmp_path / "backups"
    backups.mkdir()
    environment = os.environ | {
        "PATH": str(executable) + os.pathsep + os.environ["PATH"],
        "RATATOUILLE_DEPLOY_ROOT": str(tmp_path),
        "COMMANDS": str(journal),
        "BACKUP_DIR": str(backups),
    }
    return environment, journal


def test_same_commit_can_be_released_twice_without_overwriting_backup(tmp_path: Path) -> None:
    environment, journal = fake_host(tmp_path)
    script = Path("tools/deploy/release.sh").resolve()
    for _ in range(2):
        subprocess.run(
            ["bash", str(script), "a" * 40], env=environment, check=True, capture_output=True
        )
    assert len(list((tmp_path / "backups").iterdir())) == 2
    assert (tmp_path / "deploy/.release/current").read_text().strip() == "a" * 40


def test_failed_backup_restarts_existing_services_and_does_not_migrate(tmp_path: Path) -> None:
    environment, journal = fake_host(tmp_path)
    result = subprocess.run(
        ["bash", "tools/deploy/release.sh", "a" * 40],
        env=environment | {"FAIL_BACKUP": "1"},
        capture_output=True,
    )
    assert result.returncode == 1
    commands = journal.read_text()
    assert '"start", "web", "bot"' in commands
    assert '"up"' not in commands
    assert '"run", "--rm", "--no-deps", "migrate"]' not in commands
