def diff_resumes(base: dict, optimized: dict) -> str:
    """Generate a human-readable diff between base and optimized resumes.

    Returns a formatted string showing what changed.
    """
    lines = []

    # Skills diff
    base_skills = {s["category"]: s["items"] for s in base.get("skills", [])}
    opt_skills = {s["category"]: s["items"] for s in optimized.get("skills", [])}

    skills_changed = False
    for category in base_skills:
        old = base_skills[category]
        new = opt_skills.get(category, [])
        if old != new:
            if not skills_changed:
                lines.append("SKILLS")
                lines.append("-" * 60)
                skills_changed = True
            added = [s for s in new if s not in old]
            removed = [s for s in old if s not in new]
            reordered = old != new and not added and not removed
            lines.append(f"  {category}:")
            if added:
                lines.append(f"    + Added: {', '.join(added)}")
            if removed:
                lines.append(f"    - Removed: {', '.join(removed)}")
            if reordered:
                lines.append(f"    ~ Reordered: {', '.join(new)}")
            lines.append("")

    # Experience diff
    for old_exp, new_exp in zip(base.get("experience", []), optimized.get("experience", [])):
        bullets_changed = False
        for i, (ob, nb) in enumerate(zip(old_exp["bullets"], new_exp["bullets"])):
            if ob != nb:
                if not bullets_changed:
                    lines.append(f"EXPERIENCE: {old_exp['company']} — {old_exp['title']}")
                    lines.append("-" * 60)
                    bullets_changed = True
                lines.append(f"  Bullet {i + 1}:")
                lines.append(f"    - {ob}")
                lines.append(f"    + {nb}")
                lines.append("")

    # Projects diff
    for old_proj, new_proj in zip(base.get("projects", []), optimized.get("projects", [])):
        bullets_changed = False
        for i, (ob, nb) in enumerate(zip(old_proj["bullets"], new_proj["bullets"])):
            if ob != nb:
                if not bullets_changed:
                    lines.append(f"PROJECT: {old_proj['name']}")
                    lines.append("-" * 60)
                    bullets_changed = True
                lines.append(f"  Bullet {i + 1}:")
                lines.append(f"    - {ob}")
                lines.append(f"    + {nb}")
                lines.append("")

    if not lines:
        return "No changes."

    header = f"Resume Diff: {len(lines)} lines of changes\n{'=' * 60}\n"
    return header + "\n".join(lines)
