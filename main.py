import argparse
import json
import sys
from pathlib import Path

from src.discovery import discover_companies
from src.job_discovery import discover_jobs
from src.profile_loader import Profile, ProfileLoadError
from src.resume_diff import diff_resumes
from src.resume_optimizer import optimize_resume, save_tailored_resume, select_projects
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
        choices=["setup", "status", "discover", "discover-jobs", "import-resume", "add-projects", "add-company", "update-settings", "update-responses", "update-preferences", "optimize", "apply", "run", "history"],
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
        pdf = args.pdf
        if not pdf:
            pdf = input("  Path to resume PDF (drag and drop works): ").strip().strip("'\"")
        if not pdf:
            print("No path provided.")
            sys.exit(1)
        pdf_path = Path(pdf)
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

        # Offer to build project pool
        base_projects = resume_data.get("projects", [])
        if base_projects:
            print(f"\nYour resume has {len(base_projects)} project(s): {', '.join(p['name'] for p in base_projects)}")
            add_more = input("  Add projects from another resume PDF? [y/N]: ").strip().lower()
            all_projects = list(base_projects)
            seen_names = {p["name"].lower() for p in all_projects}

            while add_more in ("y", "yes"):
                extra_pdf = input("  Path to resume PDF: ").strip().strip("'\"")
                if not extra_pdf or not Path(extra_pdf).exists():
                    print(f"    File not found: {extra_pdf}")
                else:
                    print("    Parsing...")
                    try:
                        extra_resume = parse_pdf_to_resume(extra_pdf)
                        for p in extra_resume.get("projects", []):
                            if p["name"].lower() not in seen_names:
                                all_projects.append(p)
                                seen_names.add(p["name"].lower())
                                print(f"    + {p['name']}")
                    except Exception as e:
                        print(f"    Error parsing: {e}")

                add_more = input("  Add projects from another resume PDF? [y/N]: ").strip().lower()

            if len(all_projects) > len(base_projects):
                resume_data["project_pool"] = all_projects
                with open(output_path, "w") as f:
                    json.dump(resume_data, f, indent=2)
                    f.write("\n")
                print(f"\n  Project pool: {len(all_projects)} total projects")

    elif args.command == "add-projects":
        resume_path = profile.profile_dir / "resume.json"
        if not resume_path.exists():
            print("\nError: No resume.json found. Run import-resume first.", file=sys.stderr)
            sys.exit(1)
        with open(resume_path) as f:
            resume_data = json.load(f)

        base_projects = resume_data.get("projects", [])
        pool = resume_data.get("project_pool", list(base_projects))
        seen_names = {p["name"].lower() for p in pool}

        print(f"\nCurrent project pool ({len(pool)} projects):")
        for p in pool:
            print(f"  - {p['name']}")

        pdf = args.pdf
        if not pdf:
            pdf = input("\n  Path to resume PDF with additional projects: ").strip().strip("'\"")
        if not pdf:
            print("No path provided.")
            sys.exit(1)

        pdf_path = Path(pdf)
        if not pdf_path.exists():
            print(f"\nError: File not found: {pdf_path}", file=sys.stderr)
            sys.exit(1)

        print(f"\nParsing projects from: {pdf_path}")
        extra_resume = parse_pdf_to_resume(str(pdf_path))
        extra_projects = extra_resume.get("projects", [])
        added = 0
        for p in extra_projects:
            if p["name"].lower() not in seen_names:
                pool.append(p)
                seen_names.add(p["name"].lower())
                added += 1
                print(f"  + {p['name']}")

        if added == 0:
            print("No new projects found (all duplicates).")
        else:
            resume_data["project_pool"] = pool
            with open(resume_path, "w") as f:
                json.dump(resume_data, f, indent=2)
                f.write("\n")
            print(f"\nAdded {added} project(s). Pool now has {len(pool)} total.")
            print(f"The optimizer will pick the best {len(base_projects)} per job.")

    elif args.command == "add-company":
        from src.discovery import validate_slug, _load_companies, _save_companies

        slug = input("  Company slug (e.g. 'anthropic'): ").strip().lower()
        if not slug:
            print("No slug provided.")
            sys.exit(1)

        ats = input("  ATS type (greenhouse/lever): ").strip().lower()
        if ats not in ("greenhouse", "lever"):
            print("  Auto-detecting ATS...")
            result = validate_slug(slug, "greenhouse")
            if result:
                ats = "greenhouse"
            else:
                result = validate_slug(slug, "lever")
                if result:
                    ats = "lever"
            if not result:
                print(f"  Could not find '{slug}' on Greenhouse or Lever.")
                sys.exit(1)
        else:
            result = validate_slug(slug, ats)
            if not result:
                print(f"  Slug '{slug}' not found on {ats.title()}.")
                sys.exit(1)

        companies = _load_companies(args.profile)
        existing_slugs = {c["slug"] for c in companies}
        if slug in existing_slugs:
            print(f"  '{slug}' already in companies list.")
        else:
            companies.append(result)
            _save_companies(args.profile, companies)
            print(f"  Added: {result['name']} ({ats}) — {result['careers_url']}")
            print(f"  Total companies: {len(companies)}")

    elif args.command == "update-settings":
        profile_path = profile.profile_dir / "profile.json"
        data = profile.data

        print(f"\n--- Current Settings ---")
        print(f"  auto_submit: {data['settings']['auto_submit']}")
        print(f"  rate_limit_seconds: {data['settings']['rate_limit_seconds']}")
        print()

        auto_input = input(f"  auto_submit [{data['settings']['auto_submit']}]: ").strip().lower()
        if auto_input in ("true", "yes", "y"):
            data["settings"]["auto_submit"] = True
        elif auto_input in ("false", "no", "n"):
            data["settings"]["auto_submit"] = False

        rate_input = input(f"  rate_limit_seconds [{data['settings']['rate_limit_seconds']}]: ").strip()
        if rate_input:
            try:
                data["settings"]["rate_limit_seconds"] = int(rate_input)
            except ValueError:
                print(f"  Invalid number, keeping {data['settings']['rate_limit_seconds']}")

        with open(profile_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print(f"\n  Updated: auto_submit={data['settings']['auto_submit']}, rate_limit={data['settings']['rate_limit_seconds']}s")

    elif args.command == "update-responses":
        from src.schemas import validate_responses

        responses_path = profile.profile_dir / "responses.json"
        responses = dict(profile.responses) if profile.responses else {}

        print(f"\n--- Update Canned Responses ---")
        print(f"  (Press Enter to keep current value)\n")

        fields = [
            ("work_authorization", "Work authorization status", "Yes, I am authorized to work in the United States"),
            ("visa_sponsorship", "Require visa sponsorship?", "No"),
            ("gender", "Gender (for EEO forms)", "Prefer not to say"),
            ("ethnicity", "Ethnicity (for EEO forms)", "Prefer not to say"),
            ("veteran_status", "Veteran status", "I am not a veteran"),
            ("disability", "Disability status", "Prefer not to say"),
        ]

        for key, prompt, fallback in fields:
            current = responses.get(key, fallback)
            new_val = input(f"  {prompt} [{current}]: ").strip()
            responses[key] = new_val if new_val else current

        validate_responses(responses)
        with open(responses_path, "w") as f:
            json.dump(responses, f, indent=2)
            f.write("\n")
        print(f"\n  Responses updated.")

    elif args.command == "update-preferences":
        from src.role_expander import expand_roles
        from src.schemas import validate_profile

        profile_path = profile.profile_dir / "profile.json"
        data = profile.data
        prefs = data["job_preferences"]

        print(f"\n--- Update Job Preferences ---")
        print(f"  (Press Enter to keep current value)\n")

        # Roles
        print(f"  Current roles: {', '.join(prefs['roles'][:5])}{'...' if len(prefs['roles']) > 5 else ''}")
        roles_input = input("  New target roles (comma-separated, or Enter to keep): ").strip()
        if roles_input:
            new_roles = [r.strip() for r in roles_input.split(",") if r.strip()]
            levels = prefs.get("experience_levels", ["entry-level"])
            print("  Expanding role keywords...")
            prefs["roles"] = expand_roles(new_roles, levels)
            print(f"  Generated {len(prefs['roles'])} search keywords")

        # Locations
        print(f"  Current locations: {', '.join(prefs['locations'])}")
        locs_input = input("  New locations (comma-separated, or Enter to keep): ").strip()
        if locs_input:
            prefs["locations"] = [l.strip() for l in locs_input.split(",") if l.strip()]

        # Salary
        current_salary = prefs.get("salary_min", "not set")
        salary_input = input(f"  Minimum salary [{current_salary}]: ").strip()
        if salary_input:
            try:
                prefs["salary_min"] = int(salary_input.replace(",", "").split(".")[0])
            except ValueError:
                print(f"  Could not parse '{salary_input}', keeping current value.")

        # Industries
        current_industries = prefs.get("industries", [])
        if current_industries:
            print(f"  Current industries: {', '.join(current_industries)}")
        ind_input = input("  Industries (comma-separated, or Enter to keep): ").strip()
        if ind_input:
            prefs["industries"] = [i.strip() for i in ind_input.split(",") if i.strip()]

        data["job_preferences"] = prefs
        validate_profile(data)
        with open(profile_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print(f"\n  Preferences updated.")

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

        # Select projects from pool if available
        job_content = job.get("content", job["title"])
        selection = select_projects(base_resume, job_content)
        if selection["had_pool"]:
            print(f"\nSelected projects: {', '.join(p['name'] for p in selection['projects'])}")
            tailored_base = {**base_resume, "projects": selection["projects"]}
        else:
            tailored_base = base_resume
            selection = None

        # Optimize
        print("\nTailoring resume...")
        optimized = optimize_resume(tailored_base, job_content)

        # Show diff
        print(f"\n{diff_resumes(tailored_base, optimized, project_selection=selection)}")

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
