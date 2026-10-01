"""
NIET Cloud Portal - Personal College Assistant Module
Processes text commands and formats clean, human-readable responses suitable
for terminal interaction and future WhatsApp integration.
"""

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import portal


BASE_DIR = Path(__file__).resolve().parent
ATTENDANCE_JSON_FILE = BASE_DIR / "attendance.json"
TODAYS_SCHEDULE_JSON_FILE = BASE_DIR / "today's_schedule.json"
WEEKLY_SCHEDULE_JSON_FILE = BASE_DIR / "weekly_schedule.json"

# Cache validity duration: 60 minutes
CACHE_MAX_AGE_SECONDS = 21600

# ==============================================================================
# DATA LOADERS & CACHE MANAGEMENT
# ==============================================================================

def is_cache_valid(file_path: Path, max_age: int = CACHE_MAX_AGE_SECONDS) -> bool:
    """Checks if a cached JSON file exists and was modified within max_age seconds."""
    if not file_path.exists():
        return False
    try:
        mtime = file_path.stat().st_mtime
        return (time.time() - mtime) < max_age
    except Exception:
        return False


def load_cached_json(file_path: Path) -> Optional[Dict[str, Any]]:
    """Loads JSON data from file if present and valid."""
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_json_file(file_path: Path, data: Dict[str, Any]) -> None:
    """Saves structured dictionary to JSON file."""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[!] Warning: Failed to save {file_path.name}: {e}")


def get_attendance_data(force_refresh: bool = False) -> Dict[str, Any]:
    """Retrieves attendance data from cache or directly from portal if needed."""
    if not force_refresh and is_cache_valid(ATTENDANCE_JSON_FILE):
        cached = load_cached_json(ATTENDANCE_JSON_FILE)
        if cached:
            return cached

    # Fetch live from portal
    print("[*] Fetching latest attendance from NIET Portal...")
    data = portal.get_student_attendance()
    save_json_file(ATTENDANCE_JSON_FILE, data)
    return data


def get_schedule_data(force_refresh: bool = False, fetch_weekly: bool = True) -> Dict[str, Any]:
    """Retrieves schedule data from cache or directly from portal if needed."""
    today_valid = is_cache_valid(TODAYS_SCHEDULE_JSON_FILE)
    weekly_valid = is_cache_valid(WEEKLY_SCHEDULE_JSON_FILE)

    if not force_refresh and today_valid and (not fetch_weekly or weekly_valid):
        today_data = load_cached_json(TODAYS_SCHEDULE_JSON_FILE)
        weekly_data = load_cached_json(WEEKLY_SCHEDULE_JSON_FILE) if fetch_weekly else None
        if today_data:
            return {"today": today_data, "weekly": weekly_data}

    # Fetch live from portal
    print("[*] Fetching latest timetable from NIET Portal...")
    data = portal.get_class_schedule(fetch_weekly=fetch_weekly)
    if "today" in data:
        save_json_file(TODAYS_SCHEDULE_JSON_FILE, data["today"])
    if "weekly" in data and data["weekly"]:
        save_json_file(WEEKLY_SCHEDULE_JSON_FILE, data["weekly"])
    return data


# ==============================================================================
# RESPONSE FORMATTERS (WhatsApp & Terminal friendly)
# ==============================================================================

def format_attendance_message(data: Dict[str, Any]) -> str:
    """Formats attendance data into the clean, requested format."""
    overall = data.get("overall_attendance", {})
    subjects = data.get("subject_wise_attendance", [])

    pct = overall.get("percentage", 0.0)
    attended = overall.get("classes_attended", 0)
    absent = overall.get("classes_absent", 0)
    total = overall.get("total_classes", 0)

    lines = [
        "📊 ATTENDANCE REPORT",
        "",
        f"Overall Attendance: {pct:.2f}%",
        "",
        f"Classes Attended: {attended} / {total}",
        f"Classes Absent: {absent}",
        "",
        "Subject-wise Attendance:",
        ""
    ]

    for s in subjects:
        name = s.get("course_name", "Unknown Subject")
        s_pct = s.get("percentage", 0.0)
        s_att = s.get("classes_attended", 0)
        s_tot = s.get("total_classes", 0)

        # 🟢 >= 75%, 🟡 65%-74.99%, 🔴 < 65%
        if s_pct >= 75.0:
            indicator = "🟢"
        elif s_pct >= 65.0:
            indicator = "🟡"
        else:
            indicator = "🔴"

        lines.append(f"{indicator} {name}")
        lines.append(f"   {s_pct:.1f}% ({s_att}/{s_tot})")
        lines.append("")

    return "\n".join(lines).strip()


def format_today_schedule_message(today_data: Dict[str, Any]) -> str:
    """Formats today's class schedule into the clean, requested format."""
    classes = today_data.get("classes", [])

    lines = [
        "📅 TODAY'S CLASSES",
        ""
    ]

    if not classes:
        lines.append("🎉 No classes scheduled for today.")
        return "\n".join(lines)

    for c in classes:
        time_slot = f"{c.get('start_time', '')} - {c.get('end_time', '')}" if c.get('start_time') else c.get('time', '')
        subj = c.get('course_name', 'Subject')
        faculty = c.get('faculty', 'Faculty')
        room = c.get('room') or "N/A"

        lines.extend([
            f"⏰ {time_slot}",
            f"📚 {subj}",
            f"👨‍🏫 {faculty}",
            f"🏫 {room}",
            ""
        ])

    return "\n".join(lines).strip()


def format_weekly_schedule_message(weekly_data: Dict[str, Any]) -> str:
    """Formats the weekly Monday-Friday timetable into the clean, requested format."""
    schedule_by_day = weekly_data.get("schedule_by_day", {})

    lines = [
        "🗓 WEEKLY TIMETABLE",
        ""
    ]

    if not schedule_by_day:
        lines.append("🎉 No classes scheduled for this week.")
        return "\n".join(lines)

    target_days = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]

    # Normalize day names (keys may be 'Monday (Sep 28, 2026)' or 'Monday')
    normalized_schedule = {}
    for raw_day, classes in schedule_by_day.items():
        clean_day = raw_day.split("(")[0].strip().upper()
        normalized_schedule[clean_day] = classes

    for day in target_days:
        lines.append(f"{day}")
        day_classes = normalized_schedule.get(day, [])
        if not day_classes:
            lines.append("🎉 No classes scheduled.")
            lines.append("")
            continue

        for c in day_classes:
            time_slot = f"{c.get('start_time', '')} - {c.get('end_time', '')}" if c.get('start_time') else c.get('time', '')
            subj = c.get('course_name', 'Subject')
            faculty = c.get('faculty', 'Faculty')
            room = c.get('room') or "N/A"

            lines.extend([
                f"⏰ {time_slot} — {subj}",
                f"👨‍🏫 {faculty}",
                f"🏫 {room}",
                ""
            ])

    return "\n".join(lines).strip()


def format_all_message(attendance_data: Dict[str, Any], today_data: Dict[str, Any]) -> str:
    """Combines concise attendance summary with today's classes."""
    overall = attendance_data.get("overall_attendance", {})
    pct = overall.get("percentage", 0.0)
    attended = overall.get("classes_attended", 0)
    absent = overall.get("classes_absent", 0)
    total = overall.get("total_classes", 0)

    classes = today_data.get("classes", [])

    lines = [
        "📊 ATTENDANCE",
        f"Overall: {pct:.2f}%",
        "",
        f"Classes Attended: {attended} / {total}",
        f"Classes Absent: {absent}",
        "",
        "📅 TODAY",
        ""
    ]

    if not classes:
        lines.append("🎉 No classes scheduled for today.")
    else:
        for c in classes:
            time_slot = f"{c.get('start_time', '')} - {c.get('end_time', '')}" if c.get('start_time') else c.get('time', '')
            subj = c.get('course_name', 'Subject')
            faculty = c.get('faculty', 'Faculty')
            room = c.get('room') or "N/A"

            lines.extend([
                f"⏰ {time_slot} — {subj}",
                f"👨‍🏫 {faculty} | 🏫 {room}",
                ""
            ])

    return "\n".join(lines).strip()


def format_help_message() -> str:
    """Returns the list of supported assistant commands."""
    return (
        "🤖 NIET College Assistant Help\n\n"
        "Available commands:\n\n"
        "/dashboard - Complete overview\n"
        "/attendance - View attendance\n"
        "/low - Subjects below 75%\n"
        "/required - Classes needed to reach 75%\n"
        "/today - Today's classes\n"
        "/tomorrow - Tomorrow's classes\n"
        "/schedule - Weekly timetable\n"
        "/all - Complete attendance information\n"
        "/morning - Test morning notification\n"
        "/help - Show commands"
    )


def format_start_message() -> str:
    """Returns the clean welcome message for /start."""
    return (
        "🎓 NIET College Assistant\n\n"
        "Your personal college assistant.\n\n"
        "Available commands:\n\n"
        "📊 /attendance\n"
        "📅 /today\n"
        "🗓 /schedule\n"
        "📋 /all\n"
        "❓ /help"
    )


# ==============================================================================
# TIMEZONE & TIME HELPERS
# ==============================================================================

def get_kolkata_timezone():
    """Returns Asia/Kolkata timezone with resilient fallback."""
    tz_name = os.getenv("TIMEZONE", "Asia/Kolkata").strip()
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo("Asia/Kolkata")
        except Exception:
            from datetime import timezone, timedelta
            return timezone(timedelta(hours=5, minutes=30), name="IST")


def format_time_24h(time_str: str) -> str:
    """Converts 12-hour time string ('09:10 AM', '01:30 PM') to 24-hour ('09:10', '13:30')."""
    if not time_str:
        return ""
    clean = time_str.strip()
    try:
        dt = datetime.strptime(clean, "%I:%M %p")
        return dt.strftime("%H:%M")
    except Exception:
        try:
            dt = datetime.strptime(clean, "%H:%M")
            return dt.strftime("%H:%M")
        except Exception:
            return clean.split()[0] if clean else ""


# ==============================================================================
# COMMAND DISPATCHER (Clean interface for WhatsApp & CLI)
# ==============================================================================

def cmd_attendance(force_refresh: bool = False) -> str:
    """Command: attendance -> returns overall & subject-wise attendance."""
    try:
        data = get_attendance_data(force_refresh=force_refresh)
        return format_attendance_message(data)
    except Exception as e:
        return f"❌ *Error fetching attendance:*\n{e}"


def cmd_today(force_refresh: bool = False) -> str:
    """Command: today -> returns today's classes."""
    try:
        data = get_schedule_data(force_refresh=force_refresh, fetch_weekly=False)
        today_data = data.get("today", {})
        return format_today_schedule_message(today_data)
    except Exception as e:
        return f"❌ *Error fetching today's schedule:*\n{e}"


def cmd_tomorrow(force_refresh: bool = False) -> str:
    """Command: tomorrow -> returns tomorrow's class schedule."""
    tz = get_kolkata_timezone()
    now_ist = datetime.now(tz)
    from datetime import timedelta
    tomorrow = now_ist + timedelta(days=1)
    tomorrow_day = tomorrow.strftime("%A")
    tomorrow_date = tomorrow.strftime("%b %d, %Y")

    lines = [
        "📅 TOMORROW'S CLASSES",
        f"🗓️ {tomorrow_day}, {tomorrow_date}",
        ""
    ]

    tomorrow_classes = []
    try:
        sched_data = get_schedule_data(force_refresh=force_refresh, fetch_weekly=True)
        w_sched = sched_data.get("weekly", {}).get("schedule_by_day", {})
        for day_key, c_list in w_sched.items():
            if day_key.strip().lower().startswith(tomorrow_day.lower()):
                tomorrow_classes = c_list
                break
    except Exception as e:
        return f"❌ Failed to retrieve tomorrow's schedule: {e}"

    if not tomorrow_classes:
        lines.append(f"🎉 No classes scheduled for tomorrow ({tomorrow_day}).")
        return "\n".join(lines).strip()

    def sort_key(c):
        st = c.get("start_time", "")
        try:
            return datetime.strptime(st.strip(), "%I:%M %p")
        except Exception:
            return datetime.min

    sorted_classes = sorted(tomorrow_classes, key=sort_key)
    for c in sorted_classes:
        time_slot = f"{c.get('start_time', '')} - {c.get('end_time', '')}" if c.get('start_time') else c.get('time', '')
        subj = c.get('course_name', 'Subject')
        faculty = c.get('faculty', 'Faculty')
        room = c.get('room') or "N/A"

        lines.extend([
            f"⏰ {time_slot}",
            f"📚 {subj}",
            f"👨‍🏫 {faculty}",
            f"🏫 {room}",
            ""
        ])

    return "\n".join(lines).strip()


def cmd_schedule(force_refresh: bool = False) -> str:
    """Command: schedule -> returns weekly Monday-Friday timetable."""
    try:
        data = get_schedule_data(force_refresh=force_refresh, fetch_weekly=True)
        weekly_data = data.get("weekly", {})
        return format_weekly_schedule_message(weekly_data)
    except Exception as e:
        return f"❌ *Error fetching weekly schedule:*\n{e}"


def cmd_all(force_refresh: bool = False) -> str:
    """Command: all -> returns attendance summary + today's classes."""
    try:
        att_data = get_attendance_data(force_refresh=force_refresh)
        sched_data = get_schedule_data(force_refresh=force_refresh, fetch_weekly=False)
        today_data = sched_data.get("today", {})
        return format_all_message(att_data, today_data)
    except Exception as e:
        return f"❌ *Error fetching overview:*\n{e}"


def cmd_morning(force_refresh: bool = False) -> str:
    """
    Command: morning -> generates the automatic morning notification message.
    Format:
    ☀️ GOOD MORNING MEHUL

    Today's Classes:
    09:00 → Subject 1
    10:00 → Subject 2

    📊 Current Attendance: 75.93%
    """
    student_name = os.getenv("STUDENT_NAME", "MEHUL").strip().upper()
    lines = [
        f"☀️ GOOD MORNING {student_name}",
        "",
        "Today's Classes:",
        ""
    ]

    tz = get_kolkata_timezone()
    now_ist = datetime.now(tz)
    weekday_name = now_ist.strftime("%A")  # e.g. "Monday", "Sunday"

    today_classes = []
    try:
        sched_data = get_schedule_data(force_refresh=force_refresh, fetch_weekly=True)
        # Check today's schedule file
        t_data = sched_data.get("today", {})
        if t_data.get("classes") and t_data.get("day", "").lower() == weekday_name.lower():
            today_classes = t_data.get("classes", [])

        # Fallback to weekly schedule for this weekday
        if not today_classes:
            w_sched = sched_data.get("weekly", {}).get("schedule_by_day", {})
            for day_key, c_list in w_sched.items():
                if day_key.strip().lower().startswith(weekday_name.lower()):
                    today_classes = c_list
                    break
    except Exception as e:
        print(f"[!] Warning: Failed to fetch schedule for morning briefing: {e}")

    if not today_classes:
        lines.append("📅 No classes scheduled today.")
    else:
        def sort_key(c):
            st = c.get("start_time", "")
            try:
                return datetime.strptime(st.strip(), "%I:%M %p")
            except Exception:
                return datetime.min

        sorted_classes = sorted(today_classes, key=sort_key)
        for c in sorted_classes:
            t = format_time_24h(c.get("start_time", ""))
            name = c.get("course_name", "Subject")
            lines.append(f"{t} → {name}")

    # Attendance percentage
    lines.append("")
    try:
        att_data = get_attendance_data(force_refresh=force_refresh)
        overall = att_data.get("overall_attendance", {})
        pct = overall.get("percentage")
        if pct is not None:
            lines.append(f"📊 Current Attendance: {pct:.2f}%")
        else:
            lines.append("⚠️ Attendance information is temporarily unavailable.")
    except Exception:
        lines.append("⚠️ Attendance information is temporarily unavailable.")

    return "\n".join(lines).strip()


def cmd_low(force_refresh: bool = False) -> str:
    """Command: low -> returns subjects with attendance below 75%."""
    try:
        data = get_attendance_data(force_refresh=force_refresh)
        overall = data.get("overall_attendance", {})
        subjects = data.get("subject_wise_attendance", [])

        low_subs = [s for s in subjects if s.get("percentage", 0.0) < 75.0]

        if not low_subs:
            return (
                "🎉 *EXCELLENT NEWS!*\n\n"
                f"Overall Attendance: {overall.get('percentage', 0.0):.2f}%\n"
                "All your subjects currently have attendance ≥ 75.0%!"
            )

        lines = [
            "⚠️ *LOW ATTENDANCE REPORT (< 75%)*",
            f"Overall Attendance: {overall.get('percentage', 0.0):.2f}%",
            f"Critical Subjects: {len(low_subs)}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            ""
        ]

        for s in low_subs:
            name = s.get("course_name", "Subject")
            pct = s.get("percentage", 0.0)
            att = s.get("classes_attended", 0)
            tot = s.get("total_classes", 0)
            icon = "🟡" if pct >= 65.0 else "🔴"
            needed = max(0, 3 * tot - 4 * att)

            lines.extend([
                f"{icon} *{name}*",
                f"   {pct:.1f}% ({att}/{tot} classes)",
                f"   ↳ Need next {needed} consecutive class(es) to reach 75%",
                ""
            ])

        lines.append("💡 _Tip: Attend consecutive classes to restore 75% threshold._")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"❌ Failed to fetch low attendance report: {e}"


def cmd_required(force_refresh: bool = False) -> str:
    """Command: required -> calculates classes needed to reach 75% or safe classes to miss."""
    try:
        data = get_attendance_data(force_refresh=force_refresh)
        subjects = data.get("subject_wise_attendance", [])

        lines = [
            "🎯 *ATTENDANCE TARGET CALCULATOR (75% GOAL)*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            ""
        ]

        for s in subjects:
            name = s.get("course_name", "Subject")
            pct = s.get("percentage", 0.0)
            att = s.get("classes_attended", 0)
            tot = s.get("total_classes", 0)

            needed = max(0, 3 * tot - 4 * att)
            if needed > 0:
                lines.extend([
                    f"🔴 *{name}* ({pct:.1f}%)",
                    f"   ↳ Must attend next *{needed}* class(es) consecutively.",
                    ""
                ])
            else:
                can_skip = max(0, (4 * att - 3 * tot) // 3)
                lines.extend([
                    f"🟢 *{name}* ({pct:.1f}%)",
                    f"   ↳ Safe! Can miss up to *{can_skip}* class(es).",
                    ""
                ])

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 _Formula: (3 × Total - 4 × Attended) for 75% target._")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"❌ Failed to calculate required attendance: {e}"


def cmd_dashboard(force_refresh: bool = False) -> str:
    """Command: dashboard -> provides complete high-level overview."""
    try:
        att_data = get_attendance_data(force_refresh=force_refresh)
        overall = att_data.get("overall_attendance", {})
        pct = overall.get("percentage", 0.0)
        attended = overall.get("classes_attended", 0)
        absent = overall.get("classes_absent", 0)
        total = overall.get("total_classes", 0)
        indicator = "🟢" if pct >= 75.0 else "🟡" if pct >= 65.0 else "🔴"

        subjects = att_data.get("subject_wise_attendance", [])
        low_subs = [s for s in subjects if s.get("percentage", 0.0) < 75.0]

        tz = get_kolkata_timezone()
        now_ist = datetime.now(tz)
        weekday_name = now_ist.strftime("%A")

        sched_data = get_schedule_data(force_refresh=force_refresh, fetch_weekly=False)
        today_classes = sched_data.get("today", {}).get("classes", [])

        lines = [
            "🎓 *NIET COLLEGE ASSISTANT DASHBOARD*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📊 *Attendance:* {pct:.2f}% {indicator}",
            f"   Present: {attended} | Absent: {absent} | Total: {total}",
            "",
            f"⚠️ *Subjects Below 75%:* {len(low_subs)} of {len(subjects)}",
        ]

        if low_subs:
            for s in low_subs[:3]:
                lines.append(f"   • {s.get('course_name')}: {s.get('percentage', 0.0):.1f}%")
            if len(low_subs) > 3:
                lines.append(f"   • ...and {len(low_subs) - 3} more (use /low)")
        else:
            lines.append("   • All subjects in good standing!")

        lines.extend([
            "",
            f"📅 *Today's Classes ({weekday_name}):* {len(today_classes)} scheduled",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💡 *Commands:* /today | /tomorrow | /low | /required | /morning"
        ])

        return "\n".join(lines).strip()
    except Exception as e:
        return f"❌ Failed to generate dashboard: {e}"


def cmd_refresh() -> str:
    """Command: refresh -> forces live fetch of all data from portal."""
    try:
        print("[*] Performing live sync with NIET Portal...")
        full_data = portal.get_full_portal_data(fetch_weekly=True)

        if "attendance" in full_data:
            save_json_file(ATTENDANCE_JSON_FILE, full_data["attendance"])
        sched = full_data.get("schedule", {})
        if "today" in sched:
            save_json_file(TODAYS_SCHEDULE_JSON_FILE, sched["today"])
        if "weekly" in sched:
            save_json_file(WEEKLY_SCHEDULE_JSON_FILE, sched["weekly"])

        return (
            "✅ *NIET Portal Data Refreshed Successfully!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Synced Attendance and Timetable records.\n"
            "Try 'attendance', 'today', or 'schedule'."
        )
    except Exception as e:
        return f"❌ *Failed to refresh portal data:*\n{e}"


def handle_command(message: str, force_refresh: bool = False) -> str:
    """
    Main message dispatcher.
    Takes user input text and returns the response string.
    """
    if not message:
        return format_help_message()

    normalized = message.strip().lower()
    if normalized.startswith("/"):
        normalized = normalized[1:].strip()

    # Route commands
    if normalized in ("attendance", "att", "attend"):
        return cmd_attendance(force_refresh=force_refresh)

    elif normalized in ("today", "classes", "today's classes", "todays classes"):
        return cmd_today(force_refresh=force_refresh)

    elif normalized in ("tomorrow", "tom", "next"):
        return cmd_tomorrow(force_refresh=force_refresh)

    elif normalized in ("schedule", "timetable", "time table", "weekly"):
        return cmd_schedule(force_refresh=force_refresh)

    elif normalized in ("all", "briefing", "overview", "summary"):
        return cmd_all(force_refresh=force_refresh)

    elif normalized in ("morning", "notify", "daily"):
        return cmd_morning(force_refresh=force_refresh)

    elif normalized in ("low", "shortage", "short"):
        return cmd_low(force_refresh=force_refresh)

    elif normalized in ("required", "target", "bunk", "calc"):
        return cmd_required(force_refresh=force_refresh)

    elif normalized in ("dashboard", "dash"):
        return cmd_dashboard(force_refresh=force_refresh)

    elif normalized in ("refresh", "sync", "reload", "update"):
        return cmd_refresh()

    elif normalized in ("start",):
        return format_start_message()

    elif normalized in ("help", "hi", "hello", "?"):
        return format_help_message()

    else:
        return (
            f"❓ Unknown command: *'{message}'*\n\n"
            f"{format_help_message()}"
        )


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(handle_command(cmd))

