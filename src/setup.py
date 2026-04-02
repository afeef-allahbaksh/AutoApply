import json
from pathlib import Path

from src.profile_loader import PROFILES_DIR
from src.resume_parser import parse_pdf_to_resume
from src.role_expander import expand_roles
from src.schemas import validate_profile, validate_responses


def _ask(prompt: str, default: str = "") -> str:
    """Prompt the user for input with an optional default."""
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result if result else default
    while True:
        result = input(f"  {prompt}: ").strip()
        if result:
            return result
        print("    (required)")


def _ask_list(prompt: str, examples: str = "") -> list[str]:
    """Prompt for a comma-separated list."""
    hint = f" (e.g. {examples})" if examples else ""
    raw = input(f"  {prompt}{hint}: ").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    """Prompt for yes/no."""
    suffix = "[Y/n]" if default else "[y/N]"
    result = input(f"  {prompt} {suffix}: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


def run_setup(profile_name: str | None = None) -> str:
    """Interactive profile setup. Returns the profile name."""
    print("\n=== AutoApply Setup ===\n")

    # Profile name
    if profile_name:
        print(f"  Profile name: {profile_name}")
    else:
        profile_name = _ask("Profile name (used as directory name)", "").replace(" ", "_").lower()
    profile_dir = PROFILES_DIR / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Check if profile already exists
    profile_path = profile_dir / "profile.json"
    if profile_path.exists():
        if not _ask_yes_no(f"Profile '{profile_name}' already exists. Overwrite?"):
            print("Setup cancelled.")
            return profile_name

    # Personal info
    print("\n--- Personal Info ---")
    name = _ask("Full name")
    email = _ask("Email")
    phone = _ask("Phone number")
    location = _ask("Location", "Philadelphia, PA")
    linkedin = _ask("LinkedIn handle (just the slug, not full URL)", "")
    github = _ask("GitHub username", "")

    # Job preferences
    print("\n--- Job Preferences ---")
    roles = _ask_list("Target roles (comma-separated)", "Software Engineer, New Grad SWE")
    if not roles:
        roles = ["Software Engineer"]

    levels = _ask_list("Experience levels (comma-separated)", "entry-level, new grad")
    if not levels:
        levels = ["entry-level"]

    # Expand roles using LLM
    print("\n  Expanding role keywords (one-time, ~$0.005)...")
    expanded_roles = expand_roles(roles, levels)
    print(f"  Generated {len(expanded_roles)} search keywords from your {len(roles)} role(s)")
    print(f"  Examples: {', '.join(expanded_roles[:8])}...")

    locations = _ask_list("Preferred locations (comma-separated, or 'Any' for no preference)", "Remote, New York, San Francisco")
    if not locations:
        locations = ["Remote"]

    salary_input = _ask("Minimum salary (or press Enter to skip)", "")
    salary_min = None
    if salary_input:
        try:
            salary_min = int(salary_input.replace(",", "").split(".")[0])
        except ValueError:
            print(f"    Could not parse '{salary_input}' as a number, skipping salary.")

    industries = _ask_list("Preferred industries (comma-separated, or Enter to skip)", "AI, Tech, SaaS")

    # Settings
    print("\n--- Settings ---")
    auto_submit = _ask_yes_no("Auto-submit applications without review?", default=False)
    rate_limit = _ask("Seconds between applications", "30")

    # Build profile
    profile_data = {
        "name": name,
        "email": email,
        "phone": phone,
        "location": location,
        "job_preferences": {
            "roles": expanded_roles,
            "experience_levels": levels,
            "locations": locations,
        },
        "settings": {
            "auto_submit": auto_submit,
            "rate_limit_seconds": int(rate_limit),
        },
    }
    if linkedin:
        profile_data["linkedin"] = linkedin
    if github:
        profile_data["github"] = github
    if salary_min:
        profile_data["job_preferences"]["salary_min"] = salary_min
    if industries:
        profile_data["job_preferences"]["industries"] = industries

    validate_profile(profile_data)
    with open(profile_path, "w") as f:
        json.dump(profile_data, f, indent=2)
        f.write("\n")
    print(f"\nSaved profile to: {profile_path}")

    # Canned responses
    print("\n--- Common Application Responses ---")
    print("  (These get auto-filled on forms — press Enter to use defaults)\n")
    responses = {}
    responses["work_authorization"] = _ask(
        "Work authorization status",
        "Yes, I am authorized to work in the United States",
    )
    responses["visa_sponsorship"] = _ask("Require visa sponsorship?", "No")
    responses["gender"] = _ask("Gender (for EEO forms)", "Prefer not to say")
    responses["ethnicity"] = _ask("Ethnicity (for EEO forms)", "Prefer not to say")
    responses["veteran_status"] = _ask("Veteran status", "I am not a veteran")
    responses["disability"] = _ask("Disability status", "Prefer not to say")

    validate_responses(responses)
    responses_path = profile_dir / "responses.json"
    with open(responses_path, "w") as f:
        json.dump(responses, f, indent=2)
        f.write("\n")
    print(f"Saved responses to: {responses_path}")

    # Resume import
    print("\n--- Resume ---")
    pdf_path = _ask("Path to your resume PDF (drag and drop works)", "")
    pdf_path = pdf_path.strip("'\"")  # strip quotes from drag-and-drop

    if pdf_path and Path(pdf_path).exists():
        print("\nParsing resume (this calls Claude API — one-time cost ~$0.01)...")
        resume_data = parse_pdf_to_resume(pdf_path)

        # Ensure education entries have start/end dates (needed for ATS form filling)
        if resume_data.get("education"):
            print("\n--- Education Dates ---")
            print("  (ATS forms need structured dates — let's confirm yours)\n")
            for edu in resume_data["education"]:
                school = edu.get("institution", "Unknown")
                if not edu.get("start_date"):
                    edu["start_date"] = _ask(f"Start date for {school} (e.g. September 2022)")
                if not edu.get("end_date") or "expected" in edu.get("end_date", "").lower():
                    edu["end_date"] = _ask(f"End/graduation date for {school} (e.g. June 2026)", edu.get("end_date", ""))
                # Normalize degree to match dropdown options (e.g. "Bachelor's Degree")
                degree = edu.get("degree", "")
                if degree and "bachelor" in degree.lower() and "degree" not in degree.lower():
                    edu["degree"] = "Bachelor's Degree"
                elif degree and "master" in degree.lower() and "degree" not in degree.lower():
                    edu["degree"] = "Master's Degree"

        # Project pool — let user add projects from other resume versions
        base_projects = resume_data.get("projects", [])
        if base_projects:
            print(f"\n--- Project Pool ---")
            print(f"  Your resume has {len(base_projects)} project(s): {', '.join(p['name'] for p in base_projects)}")
            print(f"  You can add projects from other resume versions so the optimizer")
            print(f"  picks the best ones per job (final resume still uses {len(base_projects)}).\n")

            all_projects = list(base_projects)
            seen_names = {p["name"].lower() for p in all_projects}

            while _ask_yes_no("Add projects from another resume PDF?"):
                extra_pdf = _ask("Path to resume PDF").strip("'\"")
                if not Path(extra_pdf).exists():
                    print(f"    File not found: {extra_pdf}")
                    continue
                print("    Parsing...")
                try:
                    extra_resume = parse_pdf_to_resume(extra_pdf)
                    extra_projects = extra_resume.get("projects", [])
                    added = 0
                    for p in extra_projects:
                        if p["name"].lower() not in seen_names:
                            all_projects.append(p)
                            seen_names.add(p["name"].lower())
                            added += 1
                            print(f"    + {p['name']}")
                    if added == 0:
                        print(f"    No new projects found (all duplicates)")
                    else:
                        print(f"    Added {added} new project(s)")
                except Exception as e:
                    print(f"    Error parsing: {e}")

            if len(all_projects) > len(base_projects):
                resume_data["project_pool"] = all_projects
                print(f"\n  Project pool: {len(all_projects)} total projects")
                for p in all_projects:
                    marker = "*" if p in base_projects else "+"
                    print(f"    {marker} {p['name']}")
                print(f"  (* = in base resume, + = from other versions)")

        resume_path = profile_dir / "resume.json"
        with open(resume_path, "w") as f:
            json.dump(resume_data, f, indent=2)
            f.write("\n")
        print(f"\nSaved resume to: {resume_path}")
        print(f"Sections found: {', '.join(resume_data.get('section_order', []))}")
    elif pdf_path:
        print(f"File not found: {pdf_path}")
        print("You can import later with: python main.py --profile {profile_name} import-resume --pdf <path>")
    else:
        print(f"Skipped. Import later with: python main.py --profile {profile_name} import-resume --pdf <path>")

    # Done
    print(f"\n{'=' * 50}")
    print(f"Setup complete! Profile: {profile_name}")
    print(f"\nNext steps:")
    print(f"  python main.py --profile {profile_name} run")
    print(f"{'=' * 50}")

    return profile_name
