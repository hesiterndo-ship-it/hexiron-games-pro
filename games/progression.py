"""
Progression layer on top of XP: a Level and a Rank title, both derived purely
from the existing `users.xp` column (no schema/migration needed). Leveling
uses an increasing curve (each level needs a bit more XP than the last) so
early levels come quickly and higher ones feel earned.
"""

# XP required to REACH level N (index 0 unused, level 1 starts at 0 XP).
# level(n) needs roughly 60 * n^1.6 XP more than level(n-1).
_LEVEL_STEP = 60
_LEVEL_EXP = 1.6

RANKS = [
    (1, "🌱 نوآموز"),
    (5, "🥉 برنزی"),
    (10, "🥈 نقره‌ای"),
    (16, "🥇 طلایی"),
    (23, "💎 الماس"),
    (30, "👑 اسطوره"),
]


def _xp_for_level(level: int) -> int:
    if level <= 1:
        return 0
    return int(_LEVEL_STEP * (level - 1) ** _LEVEL_EXP)


def level_info(xp: int) -> dict:
    """Returns level, rank title, xp already earned within the current level,
    and xp still needed to reach the next one (None at the effective cap)."""
    xp = max(0, xp or 0)
    level = 1
    while _xp_for_level(level + 1) <= xp:
        level += 1
        if level > 999:  # safety valve, never realistically reached
            break
    rank = RANKS[0][1]
    for min_level, title in RANKS:
        if level >= min_level:
            rank = title
    current_floor = _xp_for_level(level)
    next_floor = _xp_for_level(level + 1)
    return {
        "level": level,
        "rank": rank,
        "xp_into_level": xp - current_floor,
        "xp_for_next_level": next_floor - current_floor,
        "xp_to_next": next_floor - xp,
    }


def progress_bar(info: dict, width: int = 10) -> str:
    span = info["xp_for_next_level"] or 1
    filled = min(width, int(width * info["xp_into_level"] / span))
    return "▰" * filled + "▱" * (width - filled)
