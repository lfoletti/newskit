## What

<!-- What the change does, in a few short sentences. English or French. -->

## Why

<!-- The need, or the issue it closes (Closes #N). -->

## Tests

<!-- For each new rule: the test that covers it, and the mutation that makes it fail. -->

| Rule | Test | Mutation |
| :- | :- | :- |
|  |  |  |

## Failing first

<!-- The command, and the FAILED lines it prints without the change. -->

## Effect on install

<!-- What the first pass after this version rewrites in each brain: documents replaced, and why. Count it with
`puits_cortexdb.py --compter --empreintes-avant`. When the fingerprint of an unchanged deposit changes, add its former
shape to `puits_cortexdb.FORMES_PRECEDENTES`: the first pass then rewrites nothing. -->

## Checklist

- [ ] Tests are written before the code, and fail without it.
- [ ] `python -m pytest`, `ruff check .` and `ruff format --check .` pass; `newskit test` ends with « témoin vert ».
- [ ] No key, private data or name specific to one media in the diff or the commit messages.
- [ ] The documentation says what changed (README, skill, CHANGELOG).
- [ ] `tests/empreintes_de_reference.json` is unchanged, or the PR declares the former shape of the deposit.
