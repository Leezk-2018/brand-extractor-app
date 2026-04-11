# YouTube KOL Brand Extractor - Project Context

This document provides essential context for AI assistants working on the YouTube KOL Brand Extractor project.

## Project Overview
The **YouTube KOL Brand Extractor** is a specialized tool designed for market research and competitive analysis. It automates the process of identifying brand mentions in YouTube videos from specific Influencers (KOLs).

- **Purpose:** Batch scan YouTube channels for videos containing specific keywords and identify mentioned brands within titles, descriptions, and tags.
- **Key Technologies:** 
  - **Frontend:** [Streamlit](https://streamlit.io/) for a rapid, interactive Web UI.
  - **Data Source:** [YouTube Data API v3](https://developers.google.com/youtube/v3) for channel and video metadata.
  - **Data Handling:** [Pandas](https://pandas.pydata.org/) for structured data manipulation and CSV export.
  - **Logic:** Python 3.8+ with advanced Regular Expressions for precise brand matching.

## Architecture & Core Components
- **`app.py`**: The main entry point and "orchestrator." It manages the Streamlit application lifecycle, session state, and the primary execution loop for processing KOLs.
- **`extractor_core.py`**: Encapsulates all YouTube API interaction logic, including channel ID resolution (from handles or search), video discovery, and detailed metadata fetching.
- **`brand_rules.py`**: The "brain" of the extraction logic. It defines how brands are matched using regex with word boundaries (`\b`), supporting aliases, exclusions (negative lookahead/lookbehind logic), and case sensitivity.
- **`history_store.py`**: Manages local persistence of run data. It saves results to `history/` as CSVs, JSON metadata, and debug logs.
- **`app_ui.py`**: Contains reusable Streamlit UI components, custom CSS injections for a polished look, and rendering logic for stats and results.
- **`brands.json`**: The default configuration file for brand matching rules.

## Development Conventions
- **State Management:** Heavy reliance on `st.session_state` to maintain the "Current Run" progress, allowing for task pausing and resumption (especially when hitting API quotas).
- **Logging:** Implements a custom "Lee Debug" logging system that captures detailed API request/response payloads into a session-based list, viewable via an in-app console.
- **Testing:** Standard Python `unittest` framework is used. Critical paths like brand matching and result row construction are covered in `test_brand_rules.py`.
- **API Quota Awareness:** The tool explicitly tracks and displays YouTube API quota usage (e.g., 100 units for a search, 1 unit for video details).

## Building and Running
- **Installation:** `pip install -r requirements.txt`
- **Execution:** `streamlit run app.py`
- **Testing:** `python -m unittest discover` or `python -m unittest test_brand_rules.py`

## Working with this Codebase
- **Adding Features:** New extraction logic should generally go into `extractor_core.py`, while UI enhancements belong in `app_ui.py`.
- **Modifying Brand Logic:** Always verify changes against `test_brand_rules.py` to ensure no regressions in brand detection precision.
- **Styling:** Custom styles are injected via `st.markdown(..., unsafe_allow_html=True)` in `app.py` and `app_ui.py`. Follow the existing "modern/clean" aesthetic.
- **API Keys:** Never hardcode API keys. The app prompts for the `YouTube API Key` in the sidebar and stores it in the session.
