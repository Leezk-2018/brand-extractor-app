# Repository Guidelines

## Project Structure & Module Organization
This repository is intentionally small and flat:

- `app.py`: the full Streamlit application, including UI, YouTube API access, parsing, and export logic.
- `requirements.txt`: runtime dependencies.
- `README.md`: setup and product overview.
- `venv/`: local virtual environment; do not commit changes from this directory.

If the app grows, extract reusable logic from `app.py` into focused modules (for example `youtube_client.py` or `brand_matching.py`) and place tests under a top-level `tests/` directory.

## Build, Test, and Development Commands
- `python -m venv venv`: create the local virtual environment.
- `.\venv\Scripts\activate`: activate the environment on Windows.
- `pip install -r requirements.txt`: install Streamlit, Pandas, and Google API client dependencies.
- `streamlit run app.py`: start the local app at `http://localhost:8501`.
- `python -m py_compile app.py`: quick syntax validation before opening a PR.

## Coding Style & Naming Conventions
Use 4-space indentation and keep functions small enough to separate UI code from API/data-processing code. Follow existing Python naming:

- `snake_case` for variables and functions
- `UPPER_SNAKE_CASE` for module-level constants
- descriptive helper names such as `_log_detail` and `_mask_api_key`

No formatter or linter is configured in this repository today, so keep changes consistent with the current file style and avoid unrelated reformatting.

## Testing Guidelines
There is no automated test suite yet. For now, validate changes by:

- running `python -m py_compile app.py`
- launching `streamlit run app.py`
- manually checking API input, date filtering, brand extraction, and CSV export flows

When adding tests, prefer `pytest`, name files `test_*.py`, and keep fixtures small and deterministic.

## Commit & Pull Request Guidelines
Recent commits are short and imperative (`优化`, `修改介绍`, `Initial commit: ...`). Keep commit messages concise, focused, and scoped to one change.

Pull requests should include:

- a short description of user-visible behavior changes
- linked issue or context when applicable
- screenshots or short recordings for Streamlit UI changes
- notes about manual test coverage and any API-related limitations

## Security & Configuration Tips
Do not hardcode YouTube API keys in `app.py`, `README.md`, or screenshots. Use local input fields or environment-specific secrets, and keep exported data free of sensitive credentials.
