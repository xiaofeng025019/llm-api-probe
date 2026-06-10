# Project Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the current rename/default-timeout cleanup internally consistent without adding legacy DB compatibility logic.

**Architecture:** This is a repository hygiene pass, not a feature. Keep runtime behavior unchanged except for already-present defaults (`llm_api_probe.db`, 60s timeout, Docker port/name updates), clean documentation and metadata to match, and verify that no schema migration is needed for Python-side default changes.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, React/Vite/TypeScript, Docker Compose, uv, pytest, ruff, mypy, pnpm.

---

## File Structure

- Modify docs/metadata only where names are stale:
  - `.env.example`
  - `AGENTS.md`
  - `CHANGELOG.md`
  - `CLAUDE.md`
  - `CONTRIBUTING.md`
  - `README.md`
  - `LICENSE`
  - `docs/api.md`
  - `docs/architecture.md`
  - `docs/superpowers/db-migrations.md`
  - `docs/superpowers/specs/2026-06-03-llm-api-probe-design.md`
- Preserve current runtime cleanup changes:
  - `backend/app/core/config.py`
  - `backend/app/db/models.py`
  - `backend/app/schemas/api.py`
  - `backend/app/main.py`
  - `backend/app/probers/_streaming.py`
  - `Dockerfile.backend`
  - `docker-compose.yml`
- Preserve current frontend rename/status changes:
  - `frontend/index.html`
  - `frontend/package.json`
  - `frontend/public/icon.svg`
  - `frontend/src/api/types.ts`
  - `frontend/src/hooks/useLocale.ts`
  - `frontend/src/hooks/useTheme.ts`
  - `frontend/src/locales/en.ts`
  - `frontend/src/locales/zh.ts`
  - `frontend/src/pages/SettingsPage.tsx`
  - `frontend/src/styles.css`
- Do not create Alembic migrations unless verification reveals a real schema change.

---

### Task 1: Clean stale project-name references

**Files:**
- Modify: all files returned by old-name searches.

- [ ] **Step 1: Search for old names**

Run:

```bash
rg -n "LLM Usability|llm-usability|llm_usability|llm_usability\.db|llm-usability-design" .
```

Expected: A finite list of stale references or only historical references that should stay.

- [ ] **Step 2: Replace clearly current-project references**

Use targeted edits only. Replace current product/package/default-resource names as follows:

```text
LLM Usability -> LLM API Probe
llm-usability -> llm-api-probe
llm_usability -> llm_api_probe
llm_usability.db -> llm_api_probe.db
2026-06-03-llm-usability-design.md -> 2026-06-03-llm-api-probe-design.md
```

Do not rewrite references that explicitly discuss prior historical names unless they would mislead current setup instructions.

- [ ] **Step 3: Re-run old-name search**

Run:

```bash
rg -n "LLM Usability|llm-usability|llm_usability|llm_usability\.db|llm-usability-design" .
```

Expected: no matches, or matches are explicitly historical and acceptable. If matches remain, review them one-by-one and either fix them or record why they are intentionally historical.

---

### Task 2: Normalize spec rename

**Files:**
- Delete/rename source: `docs/superpowers/specs/2026-06-03-llm-usability-design.md`
- Keep target: `docs/superpowers/specs/2026-06-03-llm-api-probe-design.md`

- [ ] **Step 1: Ensure target spec exists**

Run:

```bash
test -f docs/superpowers/specs/2026-06-03-llm-api-probe-design.md
```

Expected: exit code 0.

- [ ] **Step 2: Ensure old spec path is removed from final tree**

Run:

```bash
test ! -e docs/superpowers/specs/2026-06-03-llm-usability-design.md
```

Expected: exit code 0 after the rename/delete state is finalized.

- [ ] **Step 3: Check git rename detection**

Run:

```bash
git diff --find-renames --name-status -- docs/superpowers/specs
```

Expected: ideally an `R...` rename entry from the old spec path to the new spec path. If git still reports `D` plus untracked new file before staging, that is acceptable at working-tree time; the important final state is that the new spec is tracked and the old path is gone.

---

### Task 3: Confirm timeout default requires no Alembic migration

**Files:**
- Inspect: `backend/app/db/models.py`
- Inspect: `backend/alembic/versions/`
- Modify only if stale 30s defaults remain in tests/docs/frontend.

- [ ] **Step 1: Inspect timeout column definition**

Confirm `backend/app/db/models.py` uses a Python-side SQLAlchemy default only:

```py
timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
```

Expected: no `server_default` and no column type/nullability change.

- [ ] **Step 2: Search for stale 30-second defaults**

Run:

```bash
rg -n "timeout_seconds.*30|default timeout.*30|30s|30 seconds|30 秒" backend frontend docs README.md CHANGELOG.md .env.example
```

Expected: no stale current-default references. Historical changelog notes that explicitly describe old behavior may remain only if they clearly state it was old behavior.

- [ ] **Step 3: Do not add migration for Python-side default**

No Alembic migration is needed when the only change is SQLAlchemy/Pydantic object creation default and not database schema shape. If a `server_default`, type, nullability, or existing-row data migration is discovered, stop and reassess before creating a migration.

---

### Task 4: Verify current working tree consistency

**Files:**
- All changed files.

- [ ] **Step 1: Check concise status**

Run:

```bash
git status --short
```

Expected: changed files reflect intended cleanup only; no accidental temp/build/cache files.

- [ ] **Step 2: Review summary diff**

Run:

```bash
git diff --stat
```

Expected: changes are limited to project rename/default timeout/Docker fixes/docs/icon/stale status/main shutdown cleanup.

- [ ] **Step 3: Review changed-file list**

Run:

```bash
git diff --name-status
```

Expected: old spec removed, new spec present, no `data/` SQLite files or secrets.

---

### Task 5: Run backend verification

**Files:**
- Backend source and tests.

- [ ] **Step 1: Run pytest**

Run:

```bash
cd backend && uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run ruff check**

Run:

```bash
cd backend && uv run ruff check
```

Expected: no lint violations.

- [ ] **Step 3: Run ruff format check**

Run:

```bash
cd backend && uv run ruff format --check
```

Expected: all files already formatted.

- [ ] **Step 4: Run mypy**

Run:

```bash
cd backend && uv run mypy app
```

Expected: no type errors.

---

### Task 6: Run frontend verification

**Files:**
- Frontend source/package metadata.

- [ ] **Step 1: Build frontend**

Run:

```bash
cd frontend && pnpm build
```

Expected: TypeScript compilation and Vite production build succeed.

---

### Task 7: Final report

**Files:**
- No code changes.

- [ ] **Step 1: Summarize final state**

Report:

```text
- Old-name references cleaned: yes/no, with any intentional historical leftovers.
- DB compatibility: not added by user choice.
- Alembic migration: not added because timeout default is Python-side only, unless verification found otherwise.
- Spec rename: final old/new paths.
- Verification: exact pass/fail status for pytest, ruff, format, mypy, frontend build.
```

- [ ] **Step 2: Do not commit unless explicitly requested**

The user asked to process the working tree, not commit. Leave changes unstaged unless the user asks for staging/commit.
