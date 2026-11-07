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

## Checklist

- [ ] Tests are written before the code, and fail without it.
- [ ] `python -m pytest`, `ruff check .` and `ruff format --check .` pass; `newskit test` ends with « témoin vert ».
- [ ] No key, private data or name specific to one media in the diff or the commit messages.
- [ ] The documentation says what changed (README, skill, CHANGELOG).
