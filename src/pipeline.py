import json
from pathlib import Path

from src.discovery import discover_companies
from src.job_discovery import discover_jobs
from src.profile_loader import Profile
from src.resume_diff import diff_resumes
from src.resume_optimizer import optimize_resume, save_tailored_resume
from src.schemas import validate_resume


def run_pipeline(profile: Profile, headless: bool = False) -> None:
    """Run the full AutoApply pipeline interactively."""
    profile_name = profile.profile_name

    # Step 1: Company discovery
    companies_path = profile.profile_dir / "companies.json"
    if not companies_path.exists():
        print("\n--- Step 1: Discovering companies ---")
        result = discover_companies(profile_name)
        print(f"Done: {result['added']} companies added")
    else:
        with open(companies_path) as f:
            companies = json.load(f)
        print(f"\n--- Step 1: {len(companies)} companies already discovered ---")
        refresh = input("  Refresh company list? [y/N]: ").strip().lower()
        if refresh in ("y", "yes"):
            result = discover_companies(profile_name)
            print(f"Done: {result['added']} added, {result['skipped']} skipped")

    # Step 2: Job discovery
    print("\n--- Step 2: Finding matching jobs ---")
    jobs = discover_jobs(profile_name)
    if not jobs:
        print("No matching jobs found. Try adjusting your role/location preferences.")
        return

    # Step 3: Show matches and let user pick
    print(f"\n--- Step 3: {len(jobs)} matching jobs found ---\n")
    for i, j in enumerate(jobs):
        print(f"  [{i:3d}] [{j['relevance_score']:5.1f}] {j['company']:15s} | {j['title']}")
        print(f"        {j['location']}")

    print(f"\nOptions:")
    print(f"  Enter job numbers to apply (e.g. 0,1,3)")
    print(f"  'all'  — apply to all {len(jobs)} jobs")
    print(f"  'quit' — exit")

    selection = input("\nSelect jobs: ").strip().lower()
    if selection == "quit" or selection == "q":
        return

    if selection == "all":
        selected_indices = list(range(len(jobs)))
    else:
        try:
            selected_indices = [int(x.strip()) for x in selection.split(",") if x.strip()]
            selected_indices = [i for i in selected_indices if 0 <= i < len(jobs)]
        except ValueError:
            print("Invalid selection.")
            return

    if not selected_indices:
        print("No jobs selected.")
        return

    selected_jobs = [jobs[i] for i in selected_indices]
    print(f"\nSelected {len(selected_jobs)} job(s)")

    # Step 4: Resume optimization
    resume_path = profile.profile_dir / "resume.json"
    if not resume_path.exists():
        print("\nNo resume.json found. Run setup or import-resume first.")
        return

    with open(resume_path) as f:
        base_resume = json.load(f)
    validate_resume(base_resume)

    print(f"\n--- Step 4: Optimizing resume for each job ---")
    optimize = input("  Tailor resume per job? (costs ~$0.02/job) [Y/n]: ").strip().lower()
    should_optimize = optimize not in ("n", "no")

    for i, job in enumerate(selected_jobs):
        company = job["company"]
        role = job["title"]
        print(f"\n  [{i + 1}/{len(selected_jobs)}] {company} — {role}")

        if should_optimize:
            print(f"    Tailoring resume...")
            optimized = optimize_resume(base_resume, job.get("content", role))
            diff = diff_resumes(base_resume, optimized)
            if diff != "No changes.":
                print(f"\n{diff}")
            paths = save_tailored_resume(profile_name, optimized, company, role)
            print(f"    Saved: {paths['pdf']}")
        else:
            print(f"    Using base resume (no optimization)")

    # Step 5: Apply
    print(f"\n--- Step 5: Applying to {len(selected_jobs)} job(s) ---")
    if not profile.auto_submit:
        print("  (auto_submit is off — you'll review each application before submitting)")

    proceed = input("  Ready to start applying? [Y/n]: ").strip().lower()
    if proceed in ("n", "no"):
        print("  Stopped. Your optimized resumes are saved — run 'apply' when ready.")
        return

    from src.applicant import apply_to_jobs
    results = apply_to_jobs(
        profile=profile,
        jobs=selected_jobs,
        resume_data=base_resume,
        headless=headless,
    )

    # Summary
    applied = sum(1 for r in results if r["status"] == "applied")
    failed = sum(1 for r in results if r["status"] == "failed")
    skipped = sum(1 for r in results if r["status"] == "skipped")

    print(f"\n{'=' * 50}")
    print(f"Pipeline complete!")
    print(f"  Applied: {applied}")
    print(f"  Failed:  {failed}")
    print(f"  Skipped: {skipped}")
    print(f"\nView history: python main.py --profile {profile_name} history")
    print(f"{'=' * 50}")
