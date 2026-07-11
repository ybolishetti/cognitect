# Contributing to Cognitect

Cognitect is in early beta. External contributions are welcome for:
- Bug reports (open a GitHub issue)
- Documentation improvements
- Small, focused PRs

For larger changes, please open an issue first to discuss.

## Development Setup

### Backend
- Python 3.10+ (production runs 3.12, the dev container runs 3.11)
- Install: `pip install -e ".[dev]"`
- Run tests: `pytest`
- Run server: `uvicorn api.main:app --reload`

### Frontend
- Node 18.17+ (Next.js 14 requirement)
- Install: `cd frontend && npm install`
- Run dev: `npm run dev`
- Run tests: `npm run test`
- Build: `npm run build`

## PR Conventions

- Branch from `main`, name branches descriptively
- Keep PRs focused — one feature/fix per PR
- Include tests for new behavior
- All PRs run through code review before merge

## Code Style

- Backend: `ruff` (configured in `pyproject.toml`)
- Frontend: no linter/formatter is configured yet — match the existing code
  style in the file you're editing
