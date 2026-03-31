import argparse
import json
import sys
from pathlib import Path

from src.discovery import discover_companies
from src.job_discovery import discover_jobs
from src.profile_loader import Profile, ProfileLoadError
from src.resume_diff import diff_resumes
from src.resume_optimizer import optimize_resume, save_tailored_resume
from src.resume_parser import parse_pdf_to_resume
from src.schemas import validate_resume


def main():
    parser = argparse.ArgumentParser(
        prog="autoapply",
        description="AutoApply — fully automated job application pipeline",
    )
    parser.add_argument(
        "--profile", required=False, help="Profile name (directory under profiles/)"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["setup", "status", "discover", "discover-jobs", "import-resume", "optimize", "apply", "run", "history"],
        help="Command to run (default: status)",
    )
    parser.add_argument(
        "--job", type=int, help="Job index from jobs.json (for optimize/apply, 0-based)",
    )
    parser.add_argument(
        "--pdf", type=str, help="Path to PDF resume (for import-resume command)",
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run browser in headless mode (for apply)",
    )

    args = parser.parse_args()

    # Setup doesn't require --profile (but accepts it)
    if args.command == "setup":
        from src.setup import run_setup
        run_setup(profile_name=args.profile)
        return

    # All other commands require --profile
    if not args.profile:
        print("Error: --profile is required for this command.", file=sys.stderr)
        print("Run 'python main.py setup' to create a profile first.")
        sys.exit(1)

    try:
        profile = Profile(args.profile)
    except ProfileLoadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded profile: {profile.data['name']}")
    print(f"Roles: {', '.join(profile.job_preferences['roles'])}")
    print(f"Locations: {', '.join(profile.job_preferences['locations'])}")
    print(f"Auto-submit: {profile.auto_submit}")
    print(f"Applications on file: {len(profile.applications)}")

    if args.command == "status":
        pass  # info above is the status output
    elif args.command == "discover":
        print("\nDiscovering companies from seed list...")
        result = discover_companies(args.profile)
        print(f"\nDone: {result['added']} added, {result['skipped']} skipped, "
              f"{result['failed']} failed, {result['total']} total")
    elif args.command == "discover-jobs":
        print("\nSearching for matching jobs...")
        jobs = discover_jobs(args.profile)
        if jobs:
            print(f"\nTop matches:")
            for j in jobs[:10]:
                print(f"  [{j['relevance_score']:5.1f}] {j['company']:15s} | {j['title']}")
                print(f"         {j['location']}")
                print(f"         {j['posting_url']}")
            if len(jobs) > 10:
                print(f"\n  ... and {len(jobs) - 10} more (see jobs.json)")
        else:
            print("\nNo matching jobs found.")
    elif args.command == "import-resume":
        if not args.pdf:
            print("\nError: --pdf <path> is required for import-resume.", file=sys.stderr)
            sys.exit(1)
        pdf_path = Path(args.pdf)
        if not pdf_path.exists():
            print(f"\nError: File not found: {pdf_path}", file=sys.stderr)
            sys.exit(1)

        print(f"\nParsing resume from: {pdf_path}")
        resume_data = parse_pdf_to_resume(str(pdf_path))
        output_path = profile.profile_dir / "resume.json"
        with open(output_path, "w") as f:
            json.dump(resume_data, f, indent=2)
            f.write("\n")
        print(f"Saved to: {output_path}")
        print(f"Sections: {', '.join(resume_data.get('section_order', []))}")

    elif args.command == "optimize":
        if args.job is None:
            print("\nError: --job <index> is required for optimize.", file=sys.stderr)
            print("Run 'discover-jobs' first, then use the job index from the output.")
            sys.exit(1)

        # Load jobs.json
        jobs_path = profile.profile_dir / "jobs.json"
        if not jobs_path.exists():
            print("\nError: No jobs.json found. Run 'discover-jobs' first.", file=sys.stderr)
            sys.exit(1)
        with open(jobs_path) as f:
            jobs = json.load(f)

        if args.job < 0 or args.job >= len(jobs):
            print(f"\nError: Job index {args.job} out of range (0-{len(jobs) - 1}).", file=sys.stderr)
            sys.exit(1)

        job = jobs[args.job]
        print(f"\nOptimizing resume for:")
        print(f"  Company: {job['company']}")
        print(f"  Role:    {job['title']}")
        print(f"  URL:     {job['posting_url']}")

        # Load base resume
        resume_path = profile.profile_dir / "resume.json"
        if not resume_path.exists():
            print("\nError: No resume.json found in profile.", file=sys.stderr)
            print("Import your resume first: place a PDF in your profile directory")
            print("and run the parser to generate resume.json.")
            sys.exit(1)
        with open(resume_path) as f:
            base_resume = json.load(f)
        validate_resume(base_resume)

        # Optimize
        print("\nTailoring resume...")
        optimized = optimize_resume(base_resume, job.get("content", job["title"]))

        # Show diff
        print(f"\n{diff_resumes(base_resume, optimized)}")

        # Save
        paths = save_tailored_resume(args.profile, optimized, job["company"], job["title"])
        print(f"\nSaved tailored resume:")
        print(f"  JSON: {paths['json']}")
        print(f"  PDF:  {paths['pdf']}")
    elif args.command == "apply":
        from src.applicant import apply_to_jobs

        # Load jobs
        jobs_path = profile.profile_dir / "jobs.json"
        if not jobs_path.exists():
            print("\nError: No jobs.json found. Run 'discover-jobs' first.", file=sys.stderr)
            sys.exit(1)
        with open(jobs_path) as f:
            jobs = json.load(f)

        # If --job specified, apply to just that one
        if args.job is not None:
            if args.job < 0 or args.job >= len(jobs):
                print(f"\nError: Job index {args.job} out of range (0-{len(jobs) - 1}).", file=sys.stderr)
                sys.exit(1)
            jobs = [jobs[args.job]]

        # Load resume data if available
        resume_data = None
        resume_path = profile.profile_dir / "resume.json"
        if resume_path.exists():
            with open(resume_path) as f:
                resume_data = json.load(f)

        print(f"\nApplying to {len(jobs)} job(s)...")
        if not profile.auto_submit:
            print("(auto_submit is off — you'll be prompted to review each application)")

        results = apply_to_jobs(
            profile=profile,
            jobs=jobs,
            resume_data=resume_data,
            headless=args.headless,
        )

        # Summary
        applied = sum(1 for r in results if r["status"] == "applied")
        failed = sum(1 for r in results if r["status"] == "failed")
        skipped = sum(1 for r in results if r["status"] == "skipped")
        print(f"\nDone: {applied} applied, {failed} failed, {skipped} skipped")

    elif args.command == "run":
        from src.pipeline import run_pipeline
        run_pipeline(profile, headless=args.headless)

    elif args.command == "history":
        if not profile.applications:
            print("\nNo applications on file.")
        else:
            print(f"\n{'Status':<17} {'Company':<20} {'Role':<35} {'Date'}")
            print("-" * 85)
            for app in profile.applications:
                print(f"  {app['status']:<15} {app['company']:<20} {app['role']:<35} {app['date']}")

    else:
        print(f"\nCommand '{args.command}' is not yet implemented.")


if __name__ == "__main__":
    main()
