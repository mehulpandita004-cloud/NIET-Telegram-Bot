"""
NIET Cloud Portal Automation - Personal College Assistant CLI
Supports interactive terminal mode, direct commands (attendance, today, schedule, all),
and CLI flags for easy testing and future WhatsApp connectivity.
"""

import sys
import json
import argparse
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import assistant
import portal

BASE_DIR = Path(__file__).resolve().parent
ATTENDANCE_JSON_FILE = BASE_DIR / "attendance.json"
TODAYS_SCHEDULE_JSON_FILE = BASE_DIR / "today's_schedule.json"
WEEKLY_SCHEDULE_JSON_FILE = BASE_DIR / "weekly_schedule.json"


def interactive_mode():
    """Runs a conversational interactive loop in the terminal."""
    print("=" * 65)
    print("      NIET College Assistant (Interactive Terminal Mode)")
    print("=" * 65)
    print("Available Commands:")
    print("  • attendance : Overall & subject-wise attendance")
    print("  • today      : Today's class schedule")
    print("  • schedule   : Complete Monday-Friday timetable")
    print("  • all        : Daily briefing (attendance + today's classes)")
    print("  • refresh    : Fetch latest data directly from NIET Cloud")
    print("  • exit / q   : Exit the assistant")
    print("=" * 65 + "\n")

    while True:
        try:
            user_input = input("NIET Assistant > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q", "bye"):
                print("\nGoodbye! Have a great day.\n")
                break

            response = assistant.handle_command(user_input)
            print("\n" + response + "\n")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting assistant. Goodbye!\n")
            break
        except Exception as e:
            print(f"\n[!] Error: {e}\n", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="NIET College Assistant - Attendance & Timetable",
        add_help=True
    )
    # Positional command argument (e.g., 'python main.py today', 'python main.py attendance')
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        help="Command to run: 'attendance', 'today', 'schedule', 'all', 'refresh', 'telegram', or 'interactive'"
    )
    # Legacy / specific flags
    parser.add_argument(
        "--telegram",
        dest="run_telegram",
        action="store_true",
        help="Start the Telegram Bot service"
    )
    parser.add_argument(
        "--today", "--schedule-today",
        dest="schedule_today",
        action="store_true",
        help="Show today's class schedule"
    )
    parser.add_argument(
        "--weekly", "--timetable",
        dest="schedule_weekly",
        action="store_true",
        help="Show complete weekly timetable"
    )
    parser.add_argument(
        "--attendance",
        dest="attendance_only",
        action="store_true",
        help="Show student attendance report"
    )
    parser.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="Show attendance summary + today's classes"
    )
    parser.add_argument(
        "--refresh",
        dest="force_refresh",
        action="store_true",
        help="Force fresh data extraction from NIET portal"
    )
    parser.add_argument(
        "-i", "--interactive",
        dest="interactive",
        action="store_true",
        help="Start interactive conversational terminal mode"
    )

    args = parser.parse_args()

    # Route based on arguments
    cmd = (args.command or "").strip().lower()

    if args.run_telegram or cmd in ("telegram", "bot"):
        import telegram_bot
        telegram_bot.run_bot()
        return 0

    if args.interactive or cmd in ("interactive", "-i"):
        interactive_mode()
        return 0

    if args.attendance_only or cmd == "attendance":
        print(assistant.handle_command("attendance", force_refresh=args.force_refresh))
        return 0

    if args.schedule_today or cmd == "today":
        print(assistant.handle_command("today", force_refresh=args.force_refresh))
        return 0

    if args.schedule_weekly or cmd == "schedule":
        print(assistant.handle_command("schedule", force_refresh=args.force_refresh))
        return 0

    if args.show_all or cmd == "all":
        print(assistant.handle_command("all", force_refresh=args.force_refresh))
        return 0

    if cmd == "refresh" or args.force_refresh:
        print(assistant.handle_command("refresh"))
        return 0

    if cmd:
        # Pass any custom text to the assistant dispatcher
        print(assistant.handle_command(cmd, force_refresh=args.force_refresh))
        return 0

    # Default if no arguments given: start interactive mode
    interactive_mode()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except portal.CredentialsMissingError as e:
        print("\n" + "!" * 65, file=sys.stderr)
        print("CONFIGURATION ERROR: Credentials Missing", file=sys.stderr)
        print("!" * 65, file=sys.stderr)
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)
    except portal.SecurityChallengeError as e:
        print(f"\n[!] Security Protection Error: {e}", file=sys.stderr)
        sys.exit(2)
    except portal.PortalLoginError as e:
        print(f"\n[!] Authentication Failed: {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"\n[!] Error: {e}", file=sys.stderr)
        sys.exit(4)
