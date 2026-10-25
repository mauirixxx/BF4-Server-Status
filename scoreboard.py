"""BF4 PC player scoreboards for Discord's Mobile and Wide layouts."""
from __future__ import annotations


def _safe_name(value):
    return str(value or "Unknown").replace("\n", " ").replace("\r", " ").replace("`", "'")[:48]


def _render(teams, server_name, *, wide):
    heading = f"👥 **BF4 Players — {_safe_name(server_name)}**\n"
    result = []
    for team in teams or []:
        rows = team.get("rows") or []
        title = f"TEAM {team.get('team_id', '?')}"
        if team.get("faction"):
            title += f" - {_safe_name(team['faction'])}"
        title += f" ({len(rows)})"
        header = " #  Player                         Score    K    D    K/D" if wide else " #  Player                  K/D"
        width = 62 if wide else 37
        formatted = [title, header, "-" * width]
        for i, row in enumerate(rows, 1):
            name = _safe_name(row.get("name"))
            kills = int(row.get("kills") or 0)
            deaths = int(row.get("deaths") or 0)
            kdr = kills / deaths if deaths else float(kills)
            if wide:
                formatted.append(f"{i:2d}  {name[:29]:29} {int(row.get('score') or 0):>6} {kills:>4} {deaths:>4} {kdr:>6.2f}")
            else:
                formatted.append(f"{i:2d}  {name[:22]:22} {kdr:>6.2f}")
        prefix = heading + "```text\n"
        current = []
        for line in formatted:
            candidate = prefix + "\n".join(current + [line]) + "\n```"
            if len(candidate) > 1900 and current:
                result.append(prefix + "\n".join(current) + "\n```")
                current = [title, header, "-" * width, line] if line not in formatted[:3] else [line]
            else:
                current.append(line)
        if current:
            result.append(prefix + "\n".join(current) + "\n```")
    return result or [heading + "No team player data is available."]


def mobile_scoreboard_messages(teams, server_name):
    return _render(teams, server_name, wide=False)


def wide_scoreboard_messages(teams, server_name):
    return _render(teams, server_name, wide=True)
