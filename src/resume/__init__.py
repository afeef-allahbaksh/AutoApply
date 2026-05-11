"""Resume processing package — parse PDFs, optimize for a JD, render to PDF, diff.

Submodules:
  - parser: PDF → structured resume.json via Claude
  - optimizer: tailor a base resume against a job description (with caching)
  - renderer: resume.json → HTML/CSS → PDF via WeasyPrint
  - diff: per-section before/after comparison for the optimize page

External callers import from this package directly:

    from src.resume.optimizer import optimize_resume, find_cached_resume, _slugify
    from src.resume.parser import parse_pdf_to_resume
    from src.resume.diff import diff_resumes
    from src.resume.renderer import render_resume_pdf
"""
