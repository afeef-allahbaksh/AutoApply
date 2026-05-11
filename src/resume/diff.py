def diff_resumes(base: dict, optimized: dict, project_selection: dict | None = None) -> str:
    """Generate a human-readable diff between base and optimized resumes.

    If project_selection is provided (from select_projects()), shows which
    projects were selected/skipped and why.
    Returns a formatted string showing what changed.
    """
    lines = []

    # Project selection reasoning (shown first when pool was used)
    if project_selection and project_selection.get("had_pool") and project_selection.get("reasoning"):
        lines.append("PROJECT SELECTION")
        lines.append("-" * 60)
        for entry in project_selection["reasoning"]:
            marker = "+" if entry.get("selected") else "-"
            label = "SELECTED" if entry.get("selected") else "SKIPPED "
            lines.append(f"  {marker} [{label}] {entry['project']}")
            lines.append(f"    {entry['reason']}")
        lines.append("")

    # Skills diff
    base_skills = {s["category"]: s["items"] for s in base.get("skills", [])}
    opt_skills = {s["category"]: s["items"] for s in optimized.get("skills", [])}

    skills_changed = False
    all_categories = list(base_skills.keys()) + [c for c in opt_skills if c not in base_skills]
    for category in all_categories:
        old = base_skills.get(category, [])
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
            if not old:
                lines.append(f"    + New category: {', '.join(new)}")
            elif not new:
                lines.append(f"    - Removed category")
            else:
                if added:
                    lines.append(f"    + Added: {', '.join(added)}")
                if removed:
                    lines.append(f"    - Removed: {', '.join(removed)}")
                if reordered:
                    lines.append(f"    ~ Reordered: {', '.join(new)}")
            lines.append("")

    # Experience diff
    base_exp = base.get("experience", [])
    opt_exp = optimized.get("experience", [])
    for idx in range(max(len(base_exp), len(opt_exp))):
        old_exp = base_exp[idx] if idx < len(base_exp) else None
        new_exp = opt_exp[idx] if idx < len(opt_exp) else None

        if old_exp is None:
            lines.append(f"EXPERIENCE: + Added entry: {new_exp['company']} — {new_exp['title']}")
            lines.append("")
            continue
        if new_exp is None:
            lines.append(f"EXPERIENCE: - Removed entry: {old_exp['company']} — {old_exp['title']}")
            lines.append("")
            continue

        bullets_changed = False
        old_bullets = old_exp.get("bullets", [])
        new_bullets = new_exp.get("bullets", [])
        for i in range(max(len(old_bullets), len(new_bullets))):
            ob = old_bullets[i] if i < len(old_bullets) else None
            nb = new_bullets[i] if i < len(new_bullets) else None
            if ob != nb:
                if not bullets_changed:
                    lines.append(f"EXPERIENCE: {old_exp['company']} — {old_exp['title']}")
                    lines.append("-" * 60)
                    bullets_changed = True
                if ob is None:
                    lines.append(f"  Bullet {i + 1}:")
                    lines.append(f"    + {nb}")
                elif nb is None:
                    lines.append(f"  Bullet {i + 1}:")
                    lines.append(f"    - {ob}")
                else:
                    lines.append(f"  Bullet {i + 1}:")
                    lines.append(f"    - {ob}")
                    lines.append(f"    + {nb}")
                lines.append("")

    # Projects diff
    base_proj = base.get("projects", [])
    opt_proj = optimized.get("projects", [])
    for idx in range(max(len(base_proj), len(opt_proj))):
        old_proj = base_proj[idx] if idx < len(base_proj) else None
        new_proj = opt_proj[idx] if idx < len(opt_proj) else None

        if old_proj is None:
            lines.append(f"PROJECT: + Added: {new_proj['name']}")
            lines.append("")
            continue
        if new_proj is None:
            lines.append(f"PROJECT: - Removed: {old_proj['name']}")
            lines.append("")
            continue

        bullets_changed = False
        old_bullets = old_proj.get("bullets", [])
        new_bullets = new_proj.get("bullets", [])
        for i in range(max(len(old_bullets), len(new_bullets))):
            ob = old_bullets[i] if i < len(old_bullets) else None
            nb = new_bullets[i] if i < len(new_bullets) else None
            if ob != nb:
                if not bullets_changed:
                    lines.append(f"PROJECT: {old_proj['name']}")
                    lines.append("-" * 60)
                    bullets_changed = True
                if ob is None:
                    lines.append(f"  Bullet {i + 1}:")
                    lines.append(f"    + {nb}")
                elif nb is None:
                    lines.append(f"  Bullet {i + 1}:")
                    lines.append(f"    - {ob}")
                else:
                    lines.append(f"  Bullet {i + 1}:")
                    lines.append(f"    - {ob}")
                    lines.append(f"    + {nb}")
                lines.append("")

    if not lines:
        return "No changes."

    header = f"Resume Diff: {len(lines)} lines of changes\n{'=' * 60}\n"
    return header + "\n".join(lines)
