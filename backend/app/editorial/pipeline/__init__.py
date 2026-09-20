"""The daily DPDPA briefing pipeline (was run_pipeline.sh + tools/*.py on GitHub Actions).

roadmap → research → content → image → publish. Each step is a plain function taking and
returning dicts, so the job in `app/jobs/editorial.py` wires them and tests drive each one
with the externals (Google Sheets, SerpAPI, Anthropic, KIE.ai, storage) mocked.
"""
