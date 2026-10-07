#!/usr/bin/env python3
"""Prepare the approved public files and Issues without touching the root .git."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "Sphag/ratatouille"
BASE_URL = "https://github.com/{}/blob/main/".format(REPOSITORY)
FORBIDDEN = {".git", ".agents", ".codex", ".aws", "private", ".cache", "node_modules"}
PRIVATE_FILES = {"docs/QUESTIONNAIRE.md", "docs/FOLLOW_UP.md"}
LINKS = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b[0-9]{6,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"/(?:home/[^/\s]+|mnt/[a-z]/(?:dev|Users))/"),
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def public_path(path):
    candidate = PurePosixPath(path)
    require(not candidate.is_absolute() and ".." not in candidate.parts, "Unsafe path: " + path)
    require(not FORBIDDEN.intersection(candidate.parts), "Excluded path: " + path)
    require(path not in PRIVATE_FILES and not candidate.name.startswith(".env"), "Private path: " + path)
    target = ROOT / path
    require(target.is_file(), "Missing file: " + path)
    require(target.resolve() == target.absolute(), "Symlink path: " + path)
    return target


def manifest_paths():
    manifest = (ROOT / "docs/PUBLICATION_MANIFEST.md").read_text(encoding="utf-8")
    table = manifest.split("## Файлы\n", 1)[1].split("\n## ", 1)[0]
    paths = []
    for line in table.splitlines():
        if not line.startswith("| ["):
            continue
        link = LINKS.search(line).group(1)
        path = os.path.normpath("docs/" + unquote(link)).replace(os.sep, "/")
        public_path(path)
        require(path not in paths, "Duplicate manifest path: " + path)
        paths.append(path)
    require(bool(paths), "Empty publication manifest")
    return paths


def check_text(path, data, approved):
    content = data.decode("utf-8")
    for pattern in SECRET_PATTERNS:
        require(not pattern.search(content), "Sensitive content marker in " + path)
    if not path.endswith(".md"):
        return
    targets = LINKS.findall(content) + re.findall(r'<img[^>]+src="([^"]+)"', content)
    for target in targets:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "//")):
            continue
        relative = unquote(target.split("#", 1)[0].split("?", 1)[0])
        linked = os.path.normpath(str(PurePosixPath(path).parent / relative)).replace(os.sep, "/")
        require(linked in approved, "Link outside publication: {} -> {}".format(path, target))


def issue_specs():
    backlog = (ROOT / "docs/BACKLOG.md").read_text(encoding="utf-8")
    sections = re.split(r"(?m)^### (T\d{2}) — ([^\n]+)\n", backlog)
    issues = []
    for index in range(1, len(sections), 3):
        task, title, body = sections[index:index + 3]
        body = re.split(r"(?m)^## ", body)[0].strip()
        dependency_line = re.search(r"\*\*Зависимости:\*\* ([^\n]+)", body)
        if not dependency_line:
            body = "**Зависимости:** нет.\n\n" + body
        dependencies = []
        if dependency_line:
            for match in re.finditer(r"T(\d{2})(?:[–-]T(\d{2}))?", dependency_line.group(1)):
                start, end = int(match.group(1)), int(match.group(2) or match.group(1))
                dependencies.extend("T{:02d}".format(number) for number in range(start, end + 1))
        body = LINKS.sub(lambda match: "[{}]({})".format(
            match.group(0).split("](", 1)[0][1:],
            match.group(1) if "://" in match.group(1) else BASE_URL + "docs/" + match.group(1)
        ), body)
        agreed = task in {"T00", "T01", "T02", "T03"}
        body += "\n\n## Согласование начала\n\n- [{}] Владелец отдельно согласовал эту задачу.\n".format("x" if agreed else " ")
        body += "\nСоздание Issue не разрешает выполнение следующих задач. Отложенные решения требуют ответа владельца.\n"
        body += "\n## Проверка\n\n"
        if task in {"T00", "T01", "T02"}:
            body += "Фактические результаты приведены в статусе выше и в документации соответствующей задачи.\n"
        else:
            body += "Проверить каждый критерий готовности; записать фактический результат и невыполненные проверки перед закрытием Issue. Проверки приложения пока не выполнены.\n"
        body += "\n[Источник: бэклог]({}docs/BACKLOG.md) · [Требования]({}docs/PROJECT_BRIEF.md)\n".format(BASE_URL, BASE_URL)
        issues.append({"task": task, "title": task + " — " + title, "body": body,
                       "dependencies": list(dict.fromkeys(dependencies)),
                       "state": "closed" if task in {"T00", "T01", "T02"} else "open"})
    require([issue["task"] for issue in issues] == ["T{:02d}".format(n) for n in range(16)], "Expected T00–T15")
    return issues


def git(directory, *args):
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("GIT_"):
            environment.pop(key)
    environment.update({"GIT_AUTHOR_NAME": "Sphag", "GIT_COMMITTER_NAME": "Sphag",
                        "GIT_AUTHOR_EMAIL": "10113961+Sphag@users.noreply.github.com",
                        "GIT_COMMITTER_EMAIL": "10113961+Sphag@users.noreply.github.com"})
    return subprocess.check_output(["git", "-C", str(directory), *args], env=environment,
                                   stderr=subprocess.PIPE).decode("utf-8").strip()


def prepare():
    paths = manifest_paths()
    contents = {path: public_path(path).read_bytes() for path in paths}
    for path, data in contents.items():
        if not path.endswith(".png"):
            check_text(path, data, set(paths))
    issues = issue_specs()
    cache = ROOT / "tools/publication/.cache"
    cache.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=str(cache)))
    snapshot = output / "repository"
    snapshot.mkdir()
    for path, data in contents.items():
        target = snapshot / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(snapshot, "-c", "init.templateDir=", "init")
    git(snapshot, "symbolic-ref", "HEAD", "refs/heads/main")
    excluded = ["docs/QUESTIONNAIRE.md", "docs/FOLLOW_UP.md", ".env", ".aws/test", ".agents/test",
                ".codex/test", "private/test", "local.sqlite", "tools/mcp/.cache/test",
                "tools/publication/.cache/test", "tools/mcp/node_modules/test"]
    for path in excluded:
        require(git(snapshot, "check-ignore", "--", path) == path, "Not ignored: " + path)
    git(snapshot, "add", "--", *paths)
    staged = git(snapshot, "ls-files", "-z").split("\0")
    require(set(staged) - {""} == set(paths), "Index differs from manifest")
    git(snapshot, "-c", "core.hooksPath=/dev/null", "commit", "-m", "Prepare Ratatouille specifications and development tooling")
    git(snapshot, "remote", "add", "origin", "https://github.com/" + REPOSITORY + ".git")
    git(snapshot, "bundle", "create", str(output / "ratatouille.bundle"), "main")
    git(snapshot, "bundle", "verify", str(output / "ratatouille.bundle"))
    issue_directory = output / "issues"
    issue_directory.mkdir()
    for issue in issues:
        (issue_directory / (issue["task"] + ".md")).write_text(issue["body"], encoding="utf-8")
    (output / "issues.json").write_text(json.dumps(issues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"repository": REPOSITORY, "visibility": "public", "license": "MIT",
              "commit": git(snapshot, "rev-parse", "HEAD"), "files": len(paths), "issues": len(issues),
              "checks": ["manifest", "local links", "sensitive markers", "Git exclusions", "exact index", "bundle"],
              "sha256": {path: hashlib.sha256(contents[path]).hexdigest() for path in paths},
              "published": False}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "commit": report["commit"], "files": len(paths), "issues": len(issues)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        prepare()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit("Publication preparation failed: " + str(error))
