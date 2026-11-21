# Faster sweep, same findings: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scripts/sweep.py --pre-push` (and `--history`) finishes in seconds on the HAOS box instead of minutes, and
reports exactly the same findings as today for every input.

**Architecture:** Three independent speed-ups, each provably result-preserving: (1) one `git cat-file --batch`
process instead of two `git` processes per object; (2) a whole-blob prefilter that skips the per-line loop for
blobs and values that cannot match, with the per-line logic itself unchanged; (3) a local cache of blobs already
proven clean, keyed by blob sha, path and a hash of every input that can change a verdict. A frozen copy of
today's `sweep.py` is kept in the tests as the reference, and a differential test proves new == old.

**Tech Stack:** Python 3.13 stdlib, git plumbing, pytest.

**Spec:** none written; owner's request of 21/11: "alteração no secret sweep visando performance mantendo
obrigatoriamente o mesmo nível de segurança do script atual, em outro PR, mergeado antes da release v0.4.1".
This plan's Global Constraints are that spec.

## Why it is slow today (measured 21/11 on the HAOS box, repo at 1b79c9f)
- `rev-list --objects --all`: 4429 objects, 1790 blobs, 21 MB.
- `sweep_history` spawns `git cat-file -t` for every object with a path (blobs and trees) and `git cat-file -p` for
  every blob: ~6 200 spawns × ~6 ms ≈ 37 s.
- `_scan_text` runs `_allowed` and every private-value regex on every line: ~5 ms per KB of text
  (`maestro.py`, 38 KB, 0.20 s) → well over a minute for the history.
- Total > 120 s, which is why a push wrapped in `timeout 120` died silently on 20/11.

## Global Constraints
- **Same findings, same output.** For any repo, revs, private list and `.sweep-allow`, the new `sweep_history`,
  `sweep_staged` and `sweep` return the same list of `Finding` (category, path incl. `@<sha8>`, line, masked
  excerpt) in the same order as the reference, and `main` prints the same text and exit code.
- **Scope unchanged:** `--pre-push` still scans the FULL history reachable from the pushed revs (not only new
  commits): a value added to the private list later must still catch an old commit. `--history` still scans
  `--all`. Deletions are still dropped. Blob dedupe stays "by sha, first path seen in `rev-list` order".
- **Binary rule unchanged:** a blob with `\x00` in its first 8192 bytes is skipped; text decodes `utf-8`,
  `errors="replace"`.
- **The cache can only skip work, never change a verdict:** only clean `(blob sha, path)` pairs are stored; any
  change to `scripts/sweep.py`, `scripts/inventory_scan.py`, the private list file or `.sweep-allow` changes the
  key and everything is rescanned. The cache lives under the git dir (`git rev-parse --git-path sweep-cache`),
  never in the tree, never in CI (CI runs `--history` without `--pre-push`, and the cache is used only by
  `--pre-push`). `FRIDAY_SWEEP_NO_CACHE=1` disables it. A cache file that cannot be read or parsed is ignored
  (full scan), never trusted partially.
- The cache file stores only shas and repo paths (no values, no excerpts); written atomically (tmp + rename), 0600.
- No new dependency; no change to `inventory_scan.py`'s patterns, to `.sweep-allow` syntax or to the hook.
- Git messages and code in English; never `--no-verify`.

## Review Focus
1. A pattern whose match spans a newline (`\s` in the phone pattern, `\s*` in the `"token"` pattern) — the
   prefilter on the whole blob must only be used as "no match in the blob ⇒ no match in any line", never to report.
2. Lines split by `str.splitlines` separators other than `\n` (`\r`, `\x0b`, `\x0c`, `\x1c`–`\x1e`, `\x85`,
   ` `, ` `): line numbers must stay identical.
3. A value from the private list that appears only in the path (folder/file name) of a blob whose content is clean.
4. A blob first cached clean, then the private list gains a value it contains: the next push must fail.
5. `cat-file --batch` framing: blobs whose content contains `\n` at the end, empty blobs, a missing object
   (`<sha> missing`) — must not desync the reader.

---

### Task 1: Reference copy and differential harness

**Files:**
- Create: `tests/tools/sweep_reference.py` — byte-for-byte copy of `scripts/sweep.py` at `1b79c9f`, with only the
  module docstring's first line changed to `"""Frozen reference of scripts/sweep.py at 1b79c9f (differential tests only)."""`
  and its `HERE` pointing at `<repo>/scripts` so it loads the same `inventory_scan.py`.
- Create: `tests/tools/test_sweep_equivalence.py`
- Modify: `tests/tools/test_sweep.py` (nothing yet; Task 2+ add cases)

**Interfaces:**
- Produces: fixture `ref` (the reference module) and `new` (the current `scripts/sweep.py`), and helper
  `make_repo(tmp_path, commits: list[dict[str, str | bytes]]) -> str` creating a git repo with one commit per dict
  (path → content; `None` deletes), plus a second branch helper `branch(repo, name, files)`.

- [ ] **Step 1:** Write `test_reference_matches_current_on_fixture_repo`: a repo whose history holds every finding
  kind — private value in content, private value only in a folder name (`Zelda/notes.txt`, clean content), each
  GENERIC category, an allow-rule line, a `private:` allow rule, a binary blob with a secret after a NUL, a blob
  that later gets deleted, the same blob at two paths, CRLF content, a line split by ` `, a phone number broken
  across a newline (`+55 11\n91234-5678`), an empty file. Private list `["Zelda", "Karlinha"]`, allow rules
  `["docs/*:Bearer example", "private:LICENSE:Zelda"]`. Assert
  `new.sweep_history(repo, P, A) == ref.sweep_history(repo, P, A)` and the same with `revs=[<branch sha>]`, and
  `new.sweep_staged`/`new.sweep` equal to the reference on the same tree.
- [ ] **Step 2:** Write `test_reference_matches_current_on_this_repo` (marked `@pytest.mark.slow`, skipped unless
  `FRIDAY_SWEEP_EQUIV=1`): runs both on the real repo's `--all` with the real private list when present, plus a
  synthetic private list of 30 frequent words found in the repo (`def`, `self`, `friday`, `satellite`, …) so it
  yields thousands of findings; asserts list equality.
- [ ] **Step 3:** Run `python3 -m pytest -q tests/tools/test_sweep_equivalence.py tests/tools/test_sweep.py`.
  Expected: PASS (both are still the same code).
- [ ] **Step 4:** Commit `test(sweep): freeze the current sweep as a differential reference`.

### Task 2: One `git cat-file --batch` for the history

**Files:** Modify `scripts/sweep.py` (`sweep_history`); Test `tests/tools/test_sweep.py`.

**Interfaces:** Produces `_iter_blobs(repo: str, revs: list[str] | None) -> Iterator[tuple[str, str, bytes]]`
yielding `(sha, path, data)` for each unique blob with a path, in `rev-list` order.

- [ ] **Step 1:** Tests: `test_history_spawns_three_git_processes` (patch `subprocess.run`/`Popen` and count:
  exactly `rev-list`, `cat-file --batch-check`, `cat-file --batch`, independent of blob count);
  `test_batch_reader_handles_trailing_newline_empty_and_missing` (blob ending in `\n\n`, empty blob, and a
  `missing` line fed to the parser directly).
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement: `rev-list --objects` once; `cat-file --batch-check='%(objectname) %(objecttype)'` over
  the unique shas with a path to keep only blobs; `cat-file --batch` streaming the blob shas, reading the header
  `<sha> blob <size>\n`, then exactly `size` bytes plus the trailing `\n`. `<sha> missing` → skip (today: `-t`
  returns empty → skipped). `sweep_history` keeps its body otherwise (binary check, decode, `_scan_text`, path
  suffix `@sha8`).
- [ ] **Step 4:** Run `test_sweep.py` + `test_sweep_equivalence.py`. Expected: PASS.
- [ ] **Step 5:** Commit `perf(sweep): read the history through one cat-file --batch`.

### Task 3: Whole-blob prefilter in `_scan_text`

**Files:** Modify `scripts/sweep.py` (`_scan_text`, `_private`); Test `tests/tools/test_sweep.py`.

**Interfaces:** `_private` returns the same pairs plus a precompiled word regex per value (internal type
`_Value(cat, val, rx)`; `_scan_text` keeps its signature and accepts the old pair list too, for the reference test
harness).

- [ ] **Step 1:** Tests: `test_prefilter_never_drops_a_line_hit` — property test over 2 000 random texts built from
  fragments (values, IPs, MACs incl. escaped, emails, phones split and unsplit, tokens, every `splitlines`
  separator, letters glued to values, allow-rule text): `new._scan_text(...) == ref._scan_text(...)` for each, with
  fixed seed; `test_clean_blob_runs_no_line_regex` (a 1 MB clean text: `_allowed` is never called — patch and count).
- [ ] **Step 2:** Run. Expected: the second FAILS.
- [ ] **Step 3:** Implement: per blob, keep only the private values whose `rx.search(content)` hits and the GENERIC
  patterns whose `pat.search(content)` hits (contrapositive of "a line match is also a match in the whole text":
  no pattern uses `^`, `$`, `\A`, `\Z`, and every `splitlines` separator is a non-letter, non-word character, so
  `\b` and the letter lookarounds behave the same at a line edge). If none survive, skip the line loop (path-name
  checks still run). In the line loop, keep the same order of checks but only for the surviving values/patterns,
  and evaluate `_allowed(...)` only when the line has a candidate hit. Add a guard test
  `test_no_anchor_in_patterns` asserting no GENERIC/SECRET pattern source contains `^` outside a character class,
  `$`, `\A` or `\Z`, so a future pattern cannot silently break the prefilter.
- [ ] **Step 4:** Run `test_sweep.py` + `test_sweep_equivalence.py`. Expected: PASS.
- [ ] **Step 5:** Commit `perf(sweep): skip the line loop for values a blob cannot contain`.

### Task 4: Clean-blob cache for `--pre-push`

**Files:** Modify `scripts/sweep.py` (`sweep_history`, `main`); Test `tests/tools/test_sweep.py`.

**Interfaces:** `sweep_history(repo, private, allow, revs=None, cache: str | None = None)`; `main` passes
`cache=<git-path sweep-cache>/<rules_key>` only with `--pre-push` and without `FRIDAY_SWEEP_NO_CACHE=1`.
`rules_key(repo: str, list_path: str | None) -> str` = sha256 over the bytes of `scripts/sweep.py`,
`scripts/inventory_scan.py`, the private list (or `b"<none>"`) and `.sweep-allow` (or `b"<none>"`).

- [ ] **Step 1:** Tests: `test_cache_skips_clean_blobs_on_second_run` (second run reads no blob content: count
  `--batch` requests == 0 for an unchanged repo); `test_cache_never_stores_a_blob_with_findings` (dirty blob is
  rescanned and reported every run); `test_new_private_value_invalidates_cache` (Review Focus 4);
  `test_allow_change_invalidates_cache`; `test_corrupt_cache_means_full_scan`; `test_cache_is_keyed_by_path`
  (same sha at a new path named after a resident is still caught); `test_ci_history_mode_never_uses_cache`;
  `test_cache_file_mode_0600_and_has_no_values`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement: cache file = one `<sha> <path>` per line for clean pairs; loaded into a set at start
  (any read/parse error → empty set); a pair in the set skips the `--batch` read; after the run, write the union
  of the old set and the new clean pairs to `<file>.tmp` (0600) then `os.replace`. Older cache files under
  `sweep-cache/` with another key are deleted on write (keeps one generation).
- [ ] **Step 4:** Run the whole tools suite: `python3 -m pytest -q tests/tools`. Expected: PASS.
- [ ] **Step 5:** Commit `perf(sweep): remember blobs already proven clean for the pre-push hook`.

### Task 5: Measure, document, PR

- [ ] **Step 1:** On the HAOS box, in this worktree: `time python3 tests/tools/sweep_reference.py --history .`
  (reference) and `time python3 scripts/sweep.py --history .` (new, cold); then
  `FRIDAY_SWEEP_EQUIV=1 python3 -m pytest -q -m slow tests/tools/test_sweep_equivalence.py`; then two
  `time git push --dry-run origin HEAD` (first fills the cache, second is warm). Record the timings in the PR body.
  Expected: new cold < 30 s, warm < 5 s; equivalence PASS.
- [ ] **Step 2:** Update the module docstring of `sweep.py` (cache, `FRIDAY_SWEEP_NO_CACHE`), and
  `docs/releasing.md` if it mentions the sweep's duration.
- [ ] **Step 3:** `scripts/test-all.sh` green; push (no timeout wrapper, check `git ls-remote` after); PR to
  master titled `Faster sweep with identical findings`, ready (no live gate: CI + differential test decide).
  Merge before tagging v0.4.1 (owner).
