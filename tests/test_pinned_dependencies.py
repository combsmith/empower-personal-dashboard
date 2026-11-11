"""Guards for the OpenSSF Scorecard Pinned-Dependencies check (issue #58).

Every ``pip install`` in a GitHub Actions workflow must be pinned by hash,
which means one of the two forms Scorecard accepts:

* ``pip install --require-hashes -r requirements/<lock>.txt``
* ``pip install --no-deps -e .``, a local editable install that pulls in no
  dependencies; those come from the hash-locked file installed before it.

The lock files under ``requirements/`` must hash every entry and cover every
dependency the matching ``pyproject.toml`` extras declare, so a new
dependency can't skip the lock and leave a step failing or unpinned.
"""
from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path
from typing import Iterator, List

import yaml

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
REQUIREMENTS_DIR = ROOT / "requirements"
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

# Lock file -> pyproject extras it must cover (runtime deps are always included).
LOCK_EXTRAS = {
    "ci.txt": ["mcp", "test"],
    "mutation.txt": ["mcp", "test", "mutation"],
    "build.txt": ["build"],
    "copilot.txt": ["all"],
}
# Lock file -> the one interpreter it's resolved for. None means a universal
# lock (the 3.9-3.13 CI matrix) that has to cover every marker branch.
LOCK_PYTHON = {
    "ci.txt": None,
    "mutation.txt": (3, 12),
    "build.txt": (3, 12),
    "copilot.txt": (3, 12),
}

_REMOTE_EDITABLE = re.compile(r"^(git|svn|hg|bzr).+$")
_PINNED_GIT_EDITABLE = re.compile(
    r"^git(\+(https?|ssh|git))?://.*(\.git)?@[a-fA-F0-9]{40}(#egg=.*)?$"
)
_FLAG = re.compile(r"^(--?\w+)+$")
_PYTHON = re.compile(r"^python(\d+(\.\d+)?)?$")
_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize(name: str) -> str:
    """Normalize a package name for comparison (lowercase, replace separators with dash)."""
    return re.sub(r"[-_.]+", "-", name).lower()


_PY_MARKER = re.compile(r"python_version\s*(<=|>=|==|!=|<|>)\s*['\"](\d+)\.(\d+)['\"]")
_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _applies(spec: str, python) -> bool:
    """Does a dependency spec apply on ``python``? (None = universal lock)

    Only ``python_version`` comparisons are evaluated; those are the only
    markers pyproject.toml uses.
    """
    if python is None or ";" not in spec:
        return True
    marker = spec.split(";", 1)[1]
    return all(
        _OPS[op](python, (int(major), int(minor)))
        for op, major, minor in _PY_MARKER.findall(marker)
    )


def _tokens(command: str) -> List[str]:
    """Tokenize a shell command, falling back to whitespace split for complex quoting."""
    # Segments split out of a multi-line quoted string may not tokenize; a
    # plain whitespace split is enough there because pip install arguments
    # in these workflows never contain quoted spaces.
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _pip_args(tokens: List[str]) -> List[str]:
    """Return the arguments after ``pip install``, or [] if this isn't one."""
    if not tokens:
        return []
    exe = tokens[0].rsplit("/", 1)[-1]
    if exe in ("pip", "pip3"):
        rest = tokens[1:]
    elif _PYTHON.match(exe):
        for i in range(1, len(tokens) - 1):
            if tokens[i] == "-m" and tokens[i + 1] == "pip":
                rest = tokens[i + 2:]
                break
        else:
            return []
    else:
        return []
    # Skip pip global options (e.g. --disable-pip-version-check) before the subcommand.
    lowered = [r.lower() for r in rest]
    if "install" not in lowered:
        return []
    rest = rest[lowered.index("install"):]
    return rest[1:] or ["<bare>"]


def is_unpinned_pip_install(command: str) -> bool:
    """Mirror Scorecard's ``isUnpinnedPipInstall`` for one shell command."""
    args = _pip_args(_tokens(command))
    if not args:
        return False
    if args == ["<bare>"]:
        return True
    no_deps = editable = False
    editable_pinned = True
    require_hashes = additional = wheel = False
    for arg in args:
        if arg.lower() == "--no-deps":
            no_deps = True
            continue
        if arg in ("-e", "--editable"):
            editable = True
            continue
        if arg.lower() == "--require-hashes":
            require_hashes = True
            break
        if _FLAG.match(arg):
            continue
        if arg.endswith(".whl"):
            wheel = True
            continue
        if editable:
            if _REMOTE_EDITABLE.match(arg) and not _PINNED_GIT_EDITABLE.match(arg):
                editable_pinned = False
            continue
        additional = True
    if editable:
        return not no_deps or not editable_pinned
    if require_hashes:
        return False
    if additional:
        return True
    return not wheel


def _split_commands(script: str) -> Iterator[str]:
    """Split a shell script into individual commands, handling continuations and separators."""
    joined = re.sub(r"\\\n", " ", script)
    for line in joined.splitlines():
        for part in re.split(r"&&|\|\||;|\|", line):
            part = part.strip()
            if part and not part.startswith("#"):
                yield part


def _run_blocks(node) -> Iterator[str]:
    """Recursively extract 'run' script blocks from a YAML node structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                yield value
            else:
                yield from _run_blocks(value)
    elif isinstance(node, list):
        for item in node:
            yield from _run_blocks(item)


def _workflow_pip_commands() -> Iterator[tuple]:
    """Yield (workflow_name, pip_command) pairs found in all workflow files."""
    for path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for block in _run_blocks(data):
            for cmd in _split_commands(block):
                if _pip_args(_tokens(cmd)):
                    yield path.name, cmd


def _lock_entries(path: Path) -> List[str]:
    """Return each requirement entry (continuation lines joined)."""
    text = re.sub(r"\\\n", " ", path.read_text(encoding="utf-8"))
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    ]


class TestScorecardPipRules(unittest.TestCase):
    """The rule mirror must agree with Scorecard on known samples."""

    def test_unpinned_forms(self):
        """Verify that known unpinned forms are detected."""
        for cmd in (
            "pip install --upgrade pip",
            "python -m pip install --upgrade pip",
            "pip install -r requirements.txt",
            'pip install -e ".[mcp,test]"',
            "pip install --upgrade build twine",
            "pip install",
            "pip install -e git+https://example.invalid/pkg.git",
            "pip3 install requests==2.0",
            "pip --disable-pip-version-check install requests",
        ):
            with self.subTest(cmd=cmd):
                self.assertTrue(is_unpinned_pip_install(cmd))

    def test_pinned_forms(self):
        """Verify that hash-pinned forms are correctly recognized."""
        for cmd in (
            "pip install --require-hashes -r requirements/ci.txt",
            "python -m pip install --require-hashes -r requirements/ci.txt",
            "python -m pip install --no-deps --no-build-isolation -e .",
            "pip install --no-deps dist/pkg-1.0-py3-none-any.whl",
            "echo not pip",
        ):
            with self.subTest(cmd=cmd):
                self.assertFalse(is_unpinned_pip_install(cmd))


class TestWorkflowPipInstallsPinned(unittest.TestCase):
    def test_workflows_contain_pip_installs(self):
        """Verify workflows have pip install commands to check."""
        # Guards against the scan silently matching nothing.
        self.assertTrue(list(_workflow_pip_commands()))

    def test_every_pip_install_is_pinned_by_hash(self):
        """Verify all workflow pip installs use hash pinning (Scorecard requirement)."""
        unpinned = [
            f"{name}: {cmd}"
            for name, cmd in _workflow_pip_commands()
            if is_unpinned_pip_install(cmd)
        ]
        self.assertEqual(unpinned, [], "unpinned pip installs:\n" + "\n".join(unpinned))

    def test_referenced_lock_files_exist(self):
        """Each workflow must install from its designated lock file."""
        expected_locks = {
            "ci.yml": REQUIREMENTS_DIR / "ci.txt",
            "sonarcloud.yml": REQUIREMENTS_DIR / "ci.txt",
            "mutation.yml": REQUIREMENTS_DIR / "mutation.txt",
            "copilot-setup-steps.yml": REQUIREMENTS_DIR / "copilot.txt",
            "publish.yml": REQUIREMENTS_DIR / "build.txt",
        }
        locked_workflows = set()
        for name, cmd in _workflow_pip_commands():
            tokens = _tokens(cmd)
            for i, tok in enumerate(tokens[:-1]):
                if tok in ("-r", "--requirement"):
                    locked_workflows.add(name)
                    with self.subTest(workflow=name, lock=tokens[i + 1]):
                        path = ROOT / tokens[i + 1]
                        self.assertTrue(path.is_file())
                        self.assertEqual(
                            path.resolve(),
                            expected_locks.get(name),
                            "workflow must use its designated lock file",
                        )
        # Removing a workflow's lock install must fail, not silently pass.
        for name in expected_locks:
            with self.subTest(workflow=name):
                self.assertIn(
                    name,
                    locked_workflows,
                    "workflow must install from its lock file via -r",
                )


class TestLockFiles(unittest.TestCase):
    def test_marker_evaluation(self):
        """Verify python_version marker evaluation for dependency filtering."""
        tomli = "tomli>=1.1.0; python_version < '3.11'"
        mcp = "mcp>=1.23.0; python_version >= '3.10'"
        self.assertFalse(_applies(tomli, (3, 12)))
        self.assertTrue(_applies(tomli, (3, 9)))
        self.assertTrue(_applies(tomli, None))
        self.assertTrue(_applies(mcp, (3, 12)))
        self.assertFalse(_applies(mcp, (3, 9)))
        self.assertTrue(_applies("requests>=2.28.0", (3, 12)))

    def test_expected_lock_files_exist(self):
        """Verify all expected lock files exist."""
        for lock in LOCK_EXTRAS:
            with self.subTest(lock=lock):
                self.assertTrue((REQUIREMENTS_DIR / lock).is_file())

    def test_every_entry_is_exact_and_hashed(self):
        """Verify all lock file entries are exact versions with SHA256 hashes."""
        for lock in LOCK_EXTRAS:
            entries = _lock_entries(REQUIREMENTS_DIR / lock)
            self.assertTrue(entries, f"{lock} has no entries")
            for entry in entries:
                with self.subTest(lock=lock, entry=entry.split()[0]):
                    self.assertRegex(entry, r"^[A-Za-z0-9._-]+(\[[^\]]*\])?==\S+")
                    self.assertIn("--hash=sha256:", entry)

    def test_locks_cover_declared_dependencies(self):
        """Verify each lock has an entry for every declared dependency name.

        Only package-name presence is checked; pinned versions are not compared
        against the pyproject specifiers.
        """
        optional = PYPROJECT["project"]["optional-dependencies"]
        for lock, extras in LOCK_EXTRAS.items():
            locked = {
                _normalize(_NAME.match(e).group(1))
                for e in _lock_entries(REQUIREMENTS_DIR / lock)
            }
            declared = list(PYPROJECT["project"]["dependencies"])
            for extra in extras:
                declared.extend(optional[extra])
            for spec in declared:
                if not _applies(spec, LOCK_PYTHON[lock]):
                    continue
                name = _normalize(_NAME.match(spec).group(1))
                with self.subTest(lock=lock, dependency=name):
                    self.assertIn(name, locked)

    def test_editable_installs_have_build_backend_locked(self):
        """Verify build backend tools are available in all hash-locked environments."""
        # `--no-build-isolation` editable installs need the build backend
        # preinstalled from the same hash-locked file. build.txt is included
        # because publish.yml runs `python -m build --no-isolation`, requiring
        # setuptools and wheel to be pre-installed with pinned hashes.
        requires = PYPROJECT["build-system"]["requires"]
        for lock in LOCK_EXTRAS:
            locked = {
                _normalize(_NAME.match(e).group(1))
                for e in _lock_entries(REQUIREMENTS_DIR / lock)
            }
            for spec in requires:
                name = _normalize(_NAME.match(spec).group(1))
                with self.subTest(lock=lock, requirement=name):
                    self.assertIn(name, locked)


if __name__ == "__main__":
    unittest.main()
