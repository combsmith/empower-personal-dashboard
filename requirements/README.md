# Hash-locked workflow dependencies

GitHub Actions workflows install Python packages only from these lock files,
using `pip install --require-hashes`. This satisfies the OpenSSF Scorecard
Pinned-Dependencies check ([#58](https://github.com/combsmith/empower-personal-dashboard/issues/58)).
The project itself is then installed with
`pip install --no-deps --no-build-isolation -e .`, so nothing outside the locks
gets fetched.

| Lock | Used by | Interpreter | Covers |
| --- | --- | --- | --- |
| `ci.txt` | `ci.yml` (build-and-test, coverage), `sonarcloud.yml` | universal, 3.9–3.13 | runtime + `mcp` + `test` + build backend |
| `mutation.txt` | `mutation.yml` | 3.12 | runtime + `mcp` + `test` + `mutation` + build backend |
| `copilot.txt` | `copilot-setup-steps.yml` | 3.12 | runtime + `all` + build backend |
| `build.txt` | `publish.yml` (build job) | 3.12 | `build` extra (`build`, `twine`) + build backend |

`build-backend.in` mirrors `[build-system].requires` in `pyproject.toml`.
`publish.yml` runs `python -m build --no-isolation`, so its backend comes from `build.txt`.

## Regenerating

Each lock's header records the exact
[uv](https://docs.astral.sh/uv/) command that produced it. Run that command
from the repository root, for example:

```bash
uv pip compile pyproject.toml requirements/build-backend.in --extra mcp --extra test \
  --universal --python-version 3.9 --generate-hashes --no-annotate -o requirements/ci.txt
```

Regenerate the affected locks whenever you change dependencies or extras in
`pyproject.toml`. `tests/test_pinned_dependencies.py` fails if a lock is
missing a declared dependency, if an entry isn't exact and hashed, or if a
workflow adds an unpinned `pip install`.

## Provenance

Regenerate locks only against the default PyPI index (`https://pypi.org/simple`);
do not pass `--extra-index-url` or `--index-url` pointing at a mirror. Hashes
prove the installed bytes match what was locked, not who published them, so
review the diff of each regenerated lock (new packages, version jumps) before
committing. After regenerating, run `python3 -m unittest tests/test_pinned_dependencies.py`
to confirm every lock still satisfies the version specifiers in `pyproject.toml`.
