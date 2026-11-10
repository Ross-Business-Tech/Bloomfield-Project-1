# Contributing

This repository is **public**. Anything committed, including commit messages and pull request text, can be read by anyone and stays in the history.

## How to make a change

The base branch is **`development`**, not `main`. `main` is only updated by staff at the final reveal.

1. Create every new branch from `development`: `git fetch origin development && git checkout -b yourname/short-topic origin/development`.
2. Make the change on your branch. Run `.venv/bin/pytest -q` before you commit.
3. Push your branch and open a pull request with **base `development`** (GitHub may suggest `main`; change it). Say what changed, what you tested, and anything a reviewer should check.
4. A teammate reviews it; the team's designated merger merges it. Never push to `main` or straight to `development`.
5. Before starting new work, update your branch from `development` again (`git fetch origin development && git merge origin/development`).

## Never commit

- `.env` or any API key, database password or token (use `.env.example` for setting names only).
- Data files: anything in `data/raw/`, `output/` (lead exports), `eval/private/`, or any file with `_PRIVATE` in its name. These are gitignored; don't force-add them.
- Bloomfield's deal details: past deals, addresses, terms, emails, or anything they marked confidential.
- Licensed data (paid terminals and subscriptions shared with us privately): no rows, quotes, screenshots or file samples, and no provider names in code, docs or commit messages. Licensed facts live only in local files under `eval/private/` and the database's `private` schema. Use made-up examples in code comments and tests.

## Leak guard

Every pull request runs a check that fails if the changes add API or database keys, email addresses, or `_PRIVATE` files. Run the same check on every commit on your machine (once per clone):

```
git config core.hooksPath .githooks
```

The hook needs nothing else: it runs the generic checks (keys, emails, `_PRIVATE` files) on everyone's machine. One maintainer additionally keeps a private list of confidential terms in `eval/private/forbidden_terms.txt`; that file is gitignored and is **not shared**, so don't ask for it or create one. The hook uses it automatically only where it exists. Check a whole branch with `python3 tools/leak_guard.py --range origin/development...HEAD`.

Use your GitHub noreply address as your commit email (GitHub → Settings → Emails), so your personal email doesn't end up in the public history: `git config user.email "<id>+<username>@users.noreply.github.com"`.

If you think something sensitive was committed, don't push. Tell the team first: removing it from history is easy before a push and hard after.
