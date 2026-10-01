"""
NIET Cloud Portal Automation - Attendance & Timetable Module
Automates login to NIET Cloud (https://nietcloud.niet.co.in) and extracts student attendance,
today's class schedule, and the weekly timetable using Playwright.
"""

import os
import sys
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PlaywrightTimeoutError

# Base Directory & Paths
BASE_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

# Portal URLs
PORTAL_LOGIN_URL = "https://nietcloud.niet.co.in/login.htm"
PORTAL_HOME_URL = "https://nietcloud.niet.co.in/home.htm"
PORTAL_ATTENDANCE_URL = "https://nietcloud.niet.co.in/studentCourses.htm?shwA=%2700A%27"
PORTAL_TIMETABLE_URL = "https://nietcloud.niet.co.in/studentCourses.htm?shwA=%2700TimeTable%27"

# Form Selectors
SELECTOR_USERNAME = "#j_username"
SELECTOR_PASSWORD = "#password-1"
SELECTOR_LOGIN_BTN = 'button[type="submit"]'

# Fallback Selectors
FALLBACK_USERNAME_SELECTORS = ["#j_username", "input[name='j_username']", "input[placeholder*='username' i]"]
FALLBACK_PASSWORD_SELECTORS = ["#password-1", "input[name='j_password']", "input[type='password']"]
FALLBACK_BTN_SELECTORS = ['button[type="submit"]', 'button:has-text("Login")', '.btn-primary']

# Potential Security & Challenge Selectors (CAPTCHA, OTP, MFA)
CHALLENGE_SELECTORS = [
    "input[name*='captcha' i]",
    "input[id*='captcha' i]",
    "img[src*='captcha' i]",
    "#captcha",
    "input[name*='otp' i]",
    "input[id*='otp' i]",
    "input[placeholder*='otp' i]",
    "#otp",
    "input[name*='mfa' i]",
    "input[id*='mfa' i]",
]

# Error message containers on login page
ERROR_SELECTORS = [
    ".warningMsg",
    ".text-danger",
    ".alert-danger",
    ".alert",
    "#warningMsg",
]


class PortalAutomationError(Exception):
    """Base exception for portal automation errors."""
    pass


class CredentialsMissingError(PortalAutomationError):
    """Raised when required credentials are missing from environment."""
    pass


class SecurityChallengeError(PortalAutomationError):
    """Raised when CAPTCHA, OTP, MFA, or other security challenges are detected."""
    pass


class PortalLoginError(PortalAutomationError):
    """Raised when portal login fails."""
    pass


class PortalElementNotFoundError(PortalAutomationError):
    """Raised when expected page elements are not found."""
    pass


def load_credentials() -> Tuple[str, str]:
    """
    Loads COLLEGE_ID and COLLEGE_PASSWORD from .env file.
    Never prints or logs the credentials.
    """
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        load_dotenv(dotenv_path=env_file)
    else:
        load_dotenv()

    college_id = os.getenv("COLLEGE_ID", "").strip()
    college_password = os.getenv("COLLEGE_PASSWORD", "").strip()

    if not college_id or not college_password:
        missing = []
        if not college_id:
            missing.append("COLLEGE_ID")
        if not college_password:
            missing.append("COLLEGE_PASSWORD")
        raise CredentialsMissingError(
            f"Missing required credential(s) in .env file: {', '.join(missing)}.\n"
            f"Please update your .env file at '{env_file}' with valid credentials."
        )

    return college_id, college_password


def mask_identifier(identifier: str) -> str:
    """Masks username/ID for safe console output without revealing identity."""
    if len(identifier) <= 4:
        return "****"
    return f"{identifier[:2]}***{identifier[-2:]}"


def find_first_element(page: Page, selectors: List[str]):
    """Attempts to find the first matching selector from a list of fallbacks."""
    for selector in selectors:
        element = page.query_selector(selector)
        if element and element.is_visible():
            return selector, element
    return None, None


def check_for_security_challenges(page: Page) -> Optional[str]:
    """
    Checks if CAPTCHA, OTP, MFA, or other security protection challenges are present.
    Returns a description if found, otherwise None.
    Strictly follows requirement: Do not attempt to bypass CAPTCHA, OTP, MFA.
    """
    try:
        captcha_val = page.eval_on_selector("#validateCaptcha", "el => el.value")
        if captcha_val and captcha_val.lower() == "true":
            return "Server-side CAPTCHA flag is activated"
    except Exception:
        pass

    for selector in CHALLENGE_SELECTORS:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                return f"Security challenge element detected ({selector})"
        except Exception:
            continue

    try:
        body_text = page.inner_text("body").lower()
        if "enter captcha" in body_text:
            return "CAPTCHA prompt detected"
        if "enter otp" in body_text or "enter one-time password" in body_text:
            return "OTP prompt detected"
        if "two-factor authentication" in body_text:
            return "Two-factor authentication prompt detected"
    except Exception:
        pass

    return None


def extract_error_message(page: Page) -> str:
    """Extracts any visible error messages displayed on the login page."""
    for selector in ERROR_SELECTORS:
        try:
            elements = page.query_selector_all(selector)
            for el in elements:
                text = el.inner_text().strip()
                if text:
                    return text
        except Exception:
            continue

    if "failure=true" in page.url:
        return "Invalid User Name or Password."

    return "Unknown login error occurred."


def perform_login(page: Page, college_id: str, college_password: str, timeout: int = 30000) -> None:
    """
    Executes the login sequence on the given page.
    Automatically handles username domain formatting (e.g. appending @niet.co.in if needed).
    """
    username_variants = [
        college_id if "@" in college_id else f"{college_id}@niet.co.in",
        college_id
    ]

    for idx, user_candidate in enumerate(username_variants):
        print(f"[*] Navigating to login page: {PORTAL_LOGIN_URL}")
        page.goto(PORTAL_LOGIN_URL, wait_until="domcontentloaded", timeout=timeout)

        user_sel, user_el = find_first_element(page, FALLBACK_USERNAME_SELECTORS)
        pwd_sel, pwd_el = find_first_element(page, FALLBACK_PASSWORD_SELECTORS)
        btn_sel, btn_el = find_first_element(page, FALLBACK_BTN_SELECTORS)

        if not user_el or not pwd_el or not btn_el:
            missing_fields = []
            if not user_el:
                missing_fields.append("College ID / Username field")
            if not pwd_el:
                missing_fields.append("Password field")
            if not btn_el:
                missing_fields.append("Login submit button")
            raise PortalElementNotFoundError(
                f"Could not find required login element(s): {', '.join(missing_fields)}"
            )

        challenge = check_for_security_challenges(page)
        if challenge:
            raise SecurityChallengeError(
                f"Security challenge detected: {challenge}. "
                "Halting automation in compliance with security guidelines."
            )

        page.fill(user_sel, user_candidate)
        page.fill(pwd_sel, college_password)
        page.click(btn_sel)

        try:
            page.wait_for_url(
                lambda url: "login.htm" not in url.lower() or "failure=true" in url.lower(),
                timeout=10000
            )
            page.wait_for_load_state("domcontentloaded", timeout=5000) 
        except PlaywrightTimeoutError:
            pass

        current_url = page.url
        if "login.htm" not in current_url.lower() and "failure=true" not in current_url.lower():
            print(f"[+] Successfully authenticated into NIET Portal ({current_url})")
            return

        if idx == len(username_variants) - 1:
            error_msg = extract_error_message(page)
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            failure_screenshot = SCREENSHOTS_DIR / "login_failure.png"
            page.screenshot(path=str(failure_screenshot), full_page=True)
            raise PortalLoginError(
                f"Login failed! Error: '{error_msg}'. "
                f"Please verify your credentials in .env. "
                f"Screenshot saved to '{failure_screenshot}'."
            )


def extract_student_profile(page: Page) -> Dict[str, Any]:
    """Extracts student profile information from the home dashboard."""
    return page.evaluate("""() => {
        const bodyText = document.body.innerText;
        const rollMatch = bodyText.match(/Roll\\s*No\\.?\\s*:\\s*([0-9a-zA-Z]+)/i);
        const idMatch = bodyText.match(/Student\\s*Unique\\s*Id\\s*:\\s*([0-9a-zA-Z]+)/i);
        const semMatch = bodyText.match(/(SEM-[IVX]+)/i);
        const branchMatch = bodyText.match(/B\\.Tech[^\\n]+/i);
        const secMatch = bodyText.match(/(AI-[A-Z]|CSE-[A-Z]|[A-Z]-[0-9]+)/i);

        return {
            roll_no: rollMatch ? rollMatch[1] : null,
            student_id: idMatch ? idMatch[1] : null,
            semester: semMatch ? semMatch[1] : null,
            branch: branchMatch ? branchMatch[0].trim() : null,
            section: secMatch ? secMatch[1] : null
        };
    }""")


def safe_int(val: Any, default: int = 0) -> int:
    """Safely converts string or number to integer."""
    if val is None:
        return default
    val_str = str(val).replace(",", "").strip()
    match = re.search(r"[-+]?\d+", val_str)
    if match:
        try:
            return int(match.group(0))
        except ValueError:
            pass
    return default


def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely converts string or number to float."""
    if val is None:
        return default
    val_str = str(val).replace("%", "").replace(",", "").strip()
    match = re.search(r"[-+]?\d*\.?\d+", val_str)
    if match:
        try:
            return float(match.group(0))
        except ValueError:
            pass
    return default


# ==============================================================================
# ATTENDANCE EXTRACTION
# ==============================================================================

def extract_attendance_from_page(page: Page) -> Dict[str, Any]:
    """
    Extracts structured attendance data from the active attendance page.
    Pure read-only operation; does not modify any element or state.
    """
    table_data = page.evaluate("""() => {
        const tables = Array.from(document.querySelectorAll('table'));
        const attTable = tables.find(t => {
            const text = (t.innerText || '').toLowerCase();
            return text.includes('present count') && text.includes('percentage');
        });

        if (!attTable) return null;

        const rows = Array.from(attTable.querySelectorAll('tr')).map(tr => 
            Array.from(tr.querySelectorAll('th, td')).map(c => c.innerText.trim())
        ).filter(r => r.length > 0);

        return rows;
    }""")

    if not table_data or len(table_data) < 2:
        raise PortalAutomationError("Attendance data table could not be found or was empty on the page.")

    raw_rows = table_data[1:]
    subject_records: List[Dict[str, Any]] = []
    overall_attendance: Dict[str, Any] = {
        "percentage": 0.0,
        "classes_attended": 0,
        "classes_absent": 0,
        "total_classes": 0,
        "leaves_applied": 0,
        "attendance_not_entered": 0
    }

    for row in raw_rows:
        if len(row) >= 7 and (row[0] == "" or "total" in row[0].lower() or len(row) < 11):
            overall_attendance = {
                "classes_attended": safe_int(row[1]),
                "classes_absent": safe_int(row[2]),
                "leaves_applied": safe_int(row[3]),
                "attendance_not_entered": safe_int(row[4]),
                "total_classes": safe_int(row[5]),
                "percentage": safe_float(row[6])
            }
            continue

        if len(row) >= 11:
            subject_records.append({
                "sr_no": safe_int(row[0]),
                "course_code": row[1],
                "course_name": row[2],
                "total_sessions": safe_int(row[3]),
                "faculty_name": row[4],
                "classes_attended": safe_int(row[5]),
                "classes_absent": safe_int(row[6]),
                "leaves_applied": safe_int(row[7]),
                "attendance_not_entered": safe_int(row[8]),
                "total_classes": safe_int(row[9]),
                "percentage": safe_float(row[10])
            })
        elif len(row) >= 7:
            subject_records.append({
                "course_code": row[1] if len(row) > 1 else "",
                "course_name": row[2] if len(row) > 2 else "",
                "classes_attended": safe_int(row[-6]),
                "classes_absent": safe_int(row[-5]),
                "total_classes": safe_int(row[-2]),
                "percentage": safe_float(row[-1])
            })

    if overall_attendance["total_classes"] == 0 and subject_records:
        tot_attended = sum(s["classes_attended"] for s in subject_records)
        tot_absent = sum(s["classes_absent"] for s in subject_records)
        tot_classes = sum(s["total_classes"] for s in subject_records)
        calc_pct = round((tot_attended / tot_classes * 100), 2) if tot_classes > 0 else 0.0
        overall_attendance = {
            "classes_attended": tot_attended,
            "classes_absent": tot_absent,
            "total_classes": tot_classes,
            "leaves_applied": sum(s.get("leaves_applied", 0) for s in subject_records),
            "attendance_not_entered": sum(s.get("attendance_not_entered", 0) for s in subject_records),
            "percentage": calc_pct
        }

    return {
        "overall_attendance": overall_attendance,
        "subject_wise_attendance": subject_records,
        "total_subjects": len(subject_records)
    }


def get_student_attendance(
    headless: Optional[bool] = None,
    timeout: int = 30000,
    screenshot_name: str = "attendance.png"
) -> Dict[str, Any]:
    """
    Automates the attendance extraction workflow.
    """
    college_id, college_password = load_credentials()

    if headless is None:
        headless_env = os.getenv("HEADLESS", "true").strip().lower()
        headless = headless_env not in ("false", "0", "no")

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = SCREENSHOTS_DIR / screenshot_name

    masked_id = mask_identifier(college_id)
    print(f"[*] Starting NIET Attendance automation for ID: {masked_id}")

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page: Page = context.new_page()

        try:
            perform_login(page, college_id, college_password, timeout=timeout)
            student_profile = extract_student_profile(page)

            print("[*] Navigating to Attendance section...")
            attendance_card = page.locator("text=Attendance").first
            if attendance_card and attendance_card.is_visible():
                attendance_card.click()
                page.wait_for_url(lambda u: "studentCourses" in u or "attendance" in u.lower(), timeout=15000)
            else:
                page.goto(PORTAL_LOGIN_URL, wait_until="domcontentloaded", timeout=10000)

            page.wait_for_load_state("networkidle", timeout=5000) 

            #page.screenshot(path=str(screenshot_path), full_page=True)
            #print(f"[+] Attendance screenshot captured at: {screenshot_path}")

            print("[*] Extracting attendance data...")
            result = extract_attendance_from_page(page)
            result["student_info"] = student_profile
            result["screenshot_path"] = str(screenshot_path)
            return result

        except PlaywrightTimeoutError as e:
            timeout_screenshot = SCREENSHOTS_DIR / "timeout_error.png"
            try:
                page.screenshot(path=str(timeout_screenshot), full_page=True)
            except Exception:
                pass
            raise PortalAutomationError(f"Navigation timed out: {e}") from e
        finally:
            context.close()
            browser.close()


# ==============================================================================
# SCHEDULE & TIMETABLE EXTRACTION
# ==============================================================================

def parse_schedule_table_rows(raw_rows: List[List[str]], section: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses raw DOM table rows into clean, structured class records.
    Columns: Sr. No., Date, Time, Session, Course Code, Course Name, Faculty Name, Room Name, Docs
    """
    parsed: List[Dict[str, Any]] = []
    for row in raw_rows:
        if len(row) < 5:
            continue

        first_cell = row[0].lower()
        if "course code" in first_cell or "sr. no." in first_cell or "no records found" in first_cell:
            continue

        date_str = row[1] if len(row) > 1 else ""
        time_str = row[2] if len(row) > 2 else ""

        # Parse start and end time
        start_time = ""
        end_time = ""
        if "-" in time_str:
            parts = time_str.split("-")
            start_time = parts[0].strip()
            end_time = parts[1].strip()
        else:
            start_time = time_str.strip()

        # Parse day of week from date
        day_of_week = ""
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%b %d, %Y")
                day_of_week = dt.strftime("%A")
            except Exception:
                pass

        try:
            sr_no = int(row[0])
        except ValueError:
            sr_no = len(parsed) + 1

        session_str = row[3] if len(row) > 3 else ""
        course_code = row[4] if len(row) > 4 else ""
        course_name = row[5] if len(row) > 5 else ""
        faculty_name = row[6] if len(row) > 6 else ""
        room_name = row[7].strip() if len(row) > 7 else ""

        parsed.append({
            "sr_no": sr_no,
            "date": date_str,
            "day": day_of_week,
            "start_time": start_time,
            "end_time": end_time,
            "time": time_str,
            "session": session_str,
            "course_code": course_code,
            "course_name": course_name,
            "faculty": faculty_name,
            "room": room_name if room_name else None,
            "section": section
        })

    return parsed


def extract_schedule_from_page(
    page: Page,
    student_profile: Optional[Dict[str, Any]] = None,
    fetch_weekly: bool = True
) -> Dict[str, Any]:
    """
    Extracts today's schedule and the weekly timetable from the active studentCourses page.
    """
    section = student_profile.get("section") if student_profile else None
    today_now = datetime.now()
    today_formatted = today_now.strftime("%b %d, %Y")
    today_day = today_now.strftime("%A")

    # 1. Read classes in active view (default is Today view)
    today_raw = page.evaluate("""() => {
        const tables = Array.from(document.querySelectorAll('table'));
        const schedTable = tables.find(t => {
            const txt = (t.innerText || '').toLowerCase();
            return txt.includes('course code') && (txt.includes('time') || txt.includes('room name'));
        });
        if (!schedTable) return [];
        return Array.from(schedTable.querySelectorAll('tbody tr, tr')).map(tr => 
            Array.from(tr.querySelectorAll('td')).map(c => c.innerText.trim())
        ).filter(r => r.length > 0);
    }""")

    today_classes = parse_schedule_table_rows(today_raw, section=section)

    weekly_classes: List[Dict[str, Any]] = []
    schedule_by_day: Dict[str, List[Dict[str, Any]]] = {}

    # 2. Extract Weekly Schedule if requested
    if fetch_weekly:
        week_tab = page.locator("#weekTab")
        if week_tab and week_tab.is_visible():
            print("[*] Clicking 'Weeks' tab to load Monday-Friday timetable...")
            week_tab.click()
           # page.wait_for_timeout(3000)
            page.wait_for_load_state("networkidle")

            week_raw = page.evaluate("""() => {
                const tables = Array.from(document.querySelectorAll('table'));
                const schedTable = tables.find(t => {
                    const txt = (t.innerText || '').toLowerCase();
                    return txt.includes('course code') && (txt.includes('time') || txt.includes('room name'));
                });
                if (!schedTable) return [];
                return Array.from(schedTable.querySelectorAll('tbody tr, tr')).map(tr => 
                    Array.from(tr.querySelectorAll('td')).map(c => c.innerText.trim())
                ).filter(r => r.length > 0);
            }""")

            weekly_classes = parse_schedule_table_rows(week_raw, section=section)

            # Group classes by Day / Date
            for c in weekly_classes:
                group_key = f"{c.get('day', 'Unknown')} ({c.get('date', '')})"
                if group_key not in schedule_by_day:
                    schedule_by_day[group_key] = []
                schedule_by_day[group_key].append(c)

            # If todayTab showed 0 classes, cross-check weekly classes against today's date
            matched_today = [c for c in weekly_classes if c.get("date") == today_formatted]
            if matched_today and not today_classes:
                today_classes = matched_today

    today_schedule_data = {
        "date": today_formatted,
        "day": today_day,
        "student_info": student_profile,
        "total_classes_today": len(today_classes),
        "status": f"{len(today_classes)} class(es) scheduled for today" if today_classes else f"No classes scheduled for today ({today_day})",
        "classes": today_classes
    }

    weekly_schedule_data = {
        "student_info": student_profile,
        "total_classes_this_week": len(weekly_classes),
        "total_days_with_classes": len(schedule_by_day),
        "schedule_by_day": schedule_by_day,
        "all_classes": weekly_classes
    }

    return {
        "today": today_schedule_data,
        "weekly": weekly_schedule_data if fetch_weekly else None
    }


def get_class_schedule(
    fetch_weekly: bool = True,
    headless: Optional[bool] = None,
    timeout: int = 30000,
    screenshot_name: str = "schedule_today.png"
) -> Dict[str, Any]:
    """
    Automates the class schedule extraction workflow:
    1. Authenticates into NIET Cloud.
    2. Navigates to Timetable section.
    3. Extracts today's class schedule (date, times, subject, faculty, room, section).
    4. If fetch_weekly is True, extracts the full Monday-Friday weekly schedule.
    5. Captures a debug screenshot.
    6. Returns structured JSON-serializable dictionary.
    """
    college_id, college_password = load_credentials()

    if headless is None:
        headless_env = os.getenv("HEADLESS", "true").strip().lower()
        headless = headless_env not in ("false", "0", "no")

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = SCREENSHOTS_DIR / screenshot_name

    masked_id = mask_identifier(college_id)
    print(f"[*] Starting NIET Schedule automation for ID: {masked_id}")

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page: Page = context.new_page()

        try:
            perform_login(page, college_id, college_password, timeout=timeout)
            student_profile = extract_student_profile(page)

            print("[*] Navigating to Timetable section...")
            timetable_link = page.locator("#a_timeTable")
            if timetable_link.count() > 0 and timetable_link.first.is_visible():
                timetable_link.first.click()
                page.wait_for_url(lambda u: "studentCourses" in u or "timetable" in u.lower(), timeout=15000)
            else:
                page.goto(PORTAL_TIMETABLE_URL, wait_until="networkidle", timeout=timeout)


            page.wait_for_load_state("networkidle", timeout=15000)
           #page.wait_for_timeout(2000)

            p# page.screenshot(path=str(screenshot_path), full_page=True)
# print(f"[+] Schedule screenshot captured at: {screenshot_path}")

            print("[*] Extracting schedule data...")
            schedule_result = extract_schedule_from_page(
                page,
                student_profile=student_profile,
                fetch_weekly=fetch_weekly
            )
            schedule_result["screenshot_path"] = str(screenshot_path)
            return schedule_result

        except PlaywrightTimeoutError as e:
            timeout_screenshot = SCREENSHOTS_DIR / "timeout_error.png"
            try:
                page.screenshot(path=str(timeout_screenshot), full_page=True)
            except Exception:
                pass
            raise PortalAutomationError(f"Navigation timed out: {e}") from e
        finally:
            context.close()
            browser.close()


def get_full_portal_data(
    fetch_weekly: bool = True,
    headless: Optional[bool] = None,
    timeout: int = 30000
) -> Dict[str, Any]:
    """
    Combined extractor: Authenticates once and extracts both Attendance and Schedule data.
    Maximizes efficiency and eliminates duplicate logins.
    """
    college_id, college_password = load_credentials()

    if headless is None:
        headless_env = os.getenv("HEADLESS", "true").strip().lower()
        headless = headless_env not in ("false", "0", "no")

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    masked_id = mask_identifier(college_id)
    print(f"[*] Starting complete NIET Portal data extraction for ID: {masked_id}")

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page: Page = context.new_page()

        try:
            # Login
            perform_login(page, college_id, college_password, timeout=timeout)
            student_profile = extract_student_profile(page)

            # 1. Attendance Extraction
            print("[*] Navigating to Attendance section...")
            att_card = page.locator("text=Attendance").first
            if att_card and att_card.is_visible():
                att_card.click()
                page.wait_for_url(lambda u: "studentCourses" in u or "attendance" in u.lower(), timeout=15000)
            else:
                page.goto(PORTAL_ATTENDANCE_URL, wait_until="networkidle", timeout=timeout)

            page.wait_for_load_state("networkidle", timeout=15000)
            #page.wait_for_timeout(2000)

            att_screenshot = SCREENSHOTS_DIR / "attendance.png"
            #page.screenshot(path=str(att_screenshot), full_page=True)
            attendance_data = extract_attendance_from_page(page)
            attendance_data["student_info"] = student_profile
            attendance_data["screenshot_path"] = str(att_screenshot)

            # 2. Timetable Extraction
            print("[*] Navigating to Timetable section...")
            page.goto(PORTAL_HOME_URL, wait_until="networkidle", timeout=timeout)
            tt_link = page.locator("#a_timeTable")
            if tt_link.count() > 0 and tt_link.first.is_visible():
                tt_link.first.click()
                page.wait_for_url(lambda u: "studentCourses" in u or "timetable" in u.lower(), timeout=15000)
            else:
                page.goto(PORTAL_TIMETABLE_URL, wait_until="networkidle", timeout=timeout)

            #page.wait_for_timeout(2000)


            sched_screenshot = SCREENSHOTS_DIR / "schedule_today.png"
            page.screenshot(path=str(sched_screenshot), full_page=True)

            schedule_data = extract_schedule_from_page(
                page,
                student_profile=student_profile,
                fetch_weekly=fetch_weekly
            )
            schedule_data["screenshot_path"] = str(sched_screenshot)

            return {
                "student_info": student_profile,
                "attendance": attendance_data,
                "schedule": schedule_data
            }

        except PlaywrightTimeoutError as e:
            timeout_screenshot = SCREENSHOTS_DIR / "timeout_error.png"
            try:
                page.screenshot(path=str(timeout_screenshot), full_page=True)
            except Exception:
                pass
            raise PortalAutomationError(f"Navigation timed out: {e}") from e
        finally:
            context.close()
            browser.close()


def login_to_portal(
    headless: Optional[bool] = None,
    timeout: int = 30000,
    screenshot_name: str = "dashboard_success.png"
) -> Dict[str, Any]:
    """
    Automates login and captures dashboard screenshot.
    """
    college_id, college_password = load_credentials()

    if headless is None:
        headless_env = os.getenv("HEADLESS", "true").strip().lower()
        headless = headless_env not in ("false", "0", "no")

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = SCREENSHOTS_DIR / screenshot_name

    masked_id = mask_identifier(college_id)
    print(f"[*] Starting NIET Portal login for ID: {masked_id}")

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page: Page = context.new_page()

        try:
            perform_login(page, college_id, college_password, timeout=timeout)
            page.screenshot(path=str(screenshot_path), full_page=True)
            return {
                "success": True,
                "url": page.url,
                "title": page.title(),
                "screenshot_path": str(screenshot_path)
            }
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        data = get_full_portal_data()
        print("\n=== Extracted Portal Data Summary ===")
        print(f"Overall Attendance: {data['attendance']['overall_attendance']['percentage']}%")
        print(f"Classes Today: {data['schedule']['today']['total_classes_today']}")
        if data['schedule'].get('weekly'):
            print(f"Weekly Classes: {data['schedule']['weekly']['total_classes_this_week']}")
    except PortalAutomationError as err:
        print(f"\n[!] Automation Error: {err}", file=sys.stderr)
        sys.exit(1)
    except Exception as err:
        print(f"\n[!] Unexpected Error: {err}", file=sys.stderr)
        sys.exit(1)
