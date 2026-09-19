"""Bulk student import rules for SIMTS.

This module has no Flask or database code, so it can be tested on its own.
The admin route reads the spreadsheet, calls ``build_import_plan()`` and then
saves the returned plan to the database in a single transaction.

Import rules
------------
* Required columns: Student code, Name, Course.
* Email, Passing grade and Passing year are optional. A blank or invalid value
  never causes a row to be skipped.
* A row is skipped only when it has no Student code or no Name, or when that
  Student code already exists in the database.
* The same Student code repeated inside the file is merged into one student.
* Courses are matched to existing courses ignoring case, spacing, the
  "POST GRADUATE" / "POSTGRADUATE" spelling and the short form in brackets.
  Only courses that cannot be matched are created.
"""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

REQUIRED_MESSAGE = (
    'Required columns: Student code, Name, Course. '
    'Optional: Email, Passing grade, Passing year.'
)

HEADER_ALIASES = {
    'student_id': 'student_code',
    'full_name': 'name',
    'e_mail': 'email',
    'course_name': 'course',
    'grade': 'passing_grade',
    'year': 'passing_year',
}
REQUIRED_HEADERS = ('student_code', 'name', 'course')


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _text(value, max_length):
    """Return a trimmed string. Whole-number floats (2021.0) lose the '.0'."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()[:max_length]


def _year(value):
    """Return (year, invalid_text). Blank -> (None, ''); invalid -> (None, raw)."""
    raw = re.sub(r'\.0+$', '', _text(value, 10))
    if raw.isdigit() and 1900 <= int(raw) <= 2200:
        return raw, ''
    return None, raw


def normalize_headers(first_row):
    def slug(value):
        return re.sub(r'[^a-z0-9]+', '_', _text(value, 80).lower()).strip('_')

    headers = [slug(h).replace('_if', '') for h in first_row]
    return [HEADER_ALIASES.get(h, h) for h in headers]


# ---------------------------------------------------------------------------
# Course names
# ---------------------------------------------------------------------------

_LEGACY_KNOWN = {
    'GBA': 'POSTGRADUATE IN BUSINESS ADMINISTRATION',
    'GDBA': 'POSTGRADUATE DIPLOMA IN BUSINESS ADMINISTRATION',
    'PDBA': 'PROFESSIONAL DIPLOMA IN BUSINESS ADMINISTRATION',
    'MBA': 'MASTER IN BUSINESS ADMINISTRATION',
    'MPBA': 'MASTER PROGRAMME IN BUSINESS ADMINISTRATION',
    'EMBA': 'EXECUTIVE MASTER IN BUSINESS ADMINISTRATION',
    'PDM': 'PROFESSIONAL DOCTRATE IN MANAGEMENT',
    'DBA': 'DIPLOMA IN BUSINESS ADMINISTRATION',
    'DCA': 'DIPLOMA IN COMPUTER APPLICATION',
    'GCA': 'POSTGRADUATE IN COMPUTER APPLICATION',
    'PGDCA': 'POSTGRADUATE DIPLOMA IN COMPUTER APPLICATION',
    'MCA': 'MASTER IN COMPUTER APPLICATION',
    'GHM': 'POSTGRADUATE IN HOTEL MANAGEMENT',
    'DME': 'DIPLOMA IN MECHANICAL ENGINEERING',
    'GDME': 'POSTGRADUATE DIPLOMA IN MECHANICAL ENGINEERING',
    'GME': 'POSTGRADUATE IN MECHANICAL ENGINEERING',
    'DCE': 'DIPLOMA IN CIVIL ENGINEERING',
    'DAE': 'DIPLOMA IN ARCHITECTURE ENGINEERING',
    'DEE': 'DIPLOMA IN ELECTRICAL ENGINEERING',
    'GEE': 'POSTGRADUATE IN ELECTRICAL ENGINEERING',
    'GAM': 'POSTGRADUATE IN AUTOMOBILE ENGINEERING',
    'CDCN': 'CERTIFIED DIPLOMA IN COMPUTER NETWORKING',
    'DECE': 'DIPLOMA IN ELECTRONICS & COMMUNICATION ENGINEERING',
    'GDECE': 'POSTGRADUATE DIPLOMA IN ELECTRONICS & COMMUNICATION ENGINEERING',
    'GECE': 'POSTGRADUATE IN ELECTRONICS & COMMUNICATION ENGINEERING',
}

_TYPOS = (
    ('GRADUTE', 'GRADUATE'),
    ('MEHCHANICAL', 'MECHANICAL'),
    ('MEHCANICAL', 'MECHANICAL'),
    ('ARTITECTURE', 'ARCHITECTURE'),
    ('DIPOMA', 'DIPLOMA'),
)


def normalize_course_name(value):
    """Tidy a course name from the spreadsheet.

    "TITLE (CODE)" and plain titles are kept exactly as written (upper-cased,
    spacing tidied, common typos fixed, a bare GRADUATE becomes POST GRADUATE).

    The older "CODE (Title)" layout keeps its previous behaviour: the code is
    looked up in the website course list and the official name is used.
    """
    raw = re.sub(r'\s+', ' ', _text(value, 200)).strip()
    upper = raw.upper()
    legacy = re.match(r'^\s*([A-Z0-9]+)\s*\(\s*(.*?)\s*\)\s*$', upper)

    if not legacy:
        name = upper
        for wrong, right in _TYPOS:
            name = re.sub(rf'\b{wrong}\b', right, name)
        name = re.sub(r'(?<!POST )\bGRADUATE\b', 'POST GRADUATE', name)
        name = re.sub(r'\s*\(\s*([^()]*?)\s*\)', r' (\1)', name)
        return re.sub(r'\s+', ' ', name).strip()

    code, title = legacy.group(1), legacy.group(2)
    title = re.sub(r'\bPOST\s+GRADUATE\b', 'POSTGRADUATE', title)
    title = re.sub(r'\bGRADUTE\b', 'GRADUATE', title)
    title = re.sub(r'\bGRADUATE\b', 'POSTGRADUATE', title)
    title = re.sub(r'\bMEHCHANICAL\b', 'MECHANICAL', title)
    title = re.sub(r'\bMEHCANICAL\b', 'MECHANICAL', title)
    title = re.sub(r'\bARTITECTURE\b', 'ARCHITECTURE', title)
    title = re.sub(r'\bDIPOMA\b', 'DIPLOMA', title)
    title = re.sub(r'\s+', ' ', title).strip()
    if code in _LEGACY_KNOWN:
        title = _LEGACY_KNOWN[code]
    return f'{title} ({code})'


def course_category(course_name):
    u = course_name.upper()
    engineering_words = (
        'ENGINEERING', 'MECHANICAL', 'CIVIL', 'ELECTRICAL', 'ELECTRONICS',
        'AUTOMOBILE', 'CHEMICAL', 'ARCHITECTURE', 'COMPUTER NETWORKING',
    )
    return 'engineering' if any(w in u for w in engineering_words) else 'management'


def engineering_section_for(course_name):
    u = course_name.upper()
    if 'MECHANICAL' in u:
        return 'mechanical'
    if 'CHEMICAL' in u:
        return 'chemical'
    if 'CIVIL' in u:
        return 'civil'
    if 'ELECTRICAL' in u and 'ELECTRONICS' not in u:
        return 'electrical'
    if 'ELECTRONICS' in u:
        return 'electronics'
    if 'AUTOMOBILE' in u:
        return 'automobile'
    if 'COMPUTER' in u:
        return 'computer'
    return 'other'


def _canon(text):
    """Comparison form: ignores case, spaces, punctuation, POST GRADUATE spelling, AND/&."""
    t = re.sub(r'\bPOST[\s-]*GRADUATE\b', 'POSTGRADUATE', (text or '').upper())
    t = re.sub(r'\bAND\b', '&', t)
    return re.sub(r'[^A-Z0-9&]+', '', t)


def course_keys(name):
    """Return (full_key, title_key). title_key ignores the short form in brackets."""
    without_code = re.sub(r'\s*\([^()]*\)\s*$', '', name or '')
    return _canon(name), _canon(without_code)


# ---------------------------------------------------------------------------
# Import plan
# ---------------------------------------------------------------------------

@dataclass
class ImportPlan:
    error: str = ''
    # Students to create. Each is a dict with the Student fields plus
    # 'row', 'course_id' (existing course) or 'new_course_key' (course to create).
    students: list = field(default_factory=list)
    # Courses to create: dicts with key, name, category, section, students.
    new_courses: list = field(default_factory=list)
    # Existing courses that students were linked to: {course_id: {name, students}}.
    matched_courses: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)   # (row, student_code, reason)
    merged: list = field(default_factory=list)    # (row, student_code, first_row)
    notes: list = field(default_factory=list)     # (row, student_code, message)

    @property
    def without_email(self):
        return sum(1 for s in self.students if not s['email'])

    @property
    def without_year(self):
        return sum(1 for s in self.students if not s['passing_year'])


def build_import_plan(rows, existing_student_ids, existing_courses):
    """Turn spreadsheet rows into an ImportPlan without touching the database.

    rows                 -- all sheet rows (first row = headers), as tuples
    existing_student_ids -- Student codes already in the database
    existing_courses     -- iterable of (course_id, course_name) already in the database
    """
    plan = ImportPlan()
    rows = list(rows)
    if not rows:
        plan.error = 'Spreadsheet is empty.'
        return plan

    headers = normalize_headers(rows[0])
    if not all(h in headers for h in REQUIRED_HEADERS):
        plan.error = REQUIRED_MESSAGE
        return plan

    existing_ids = set(existing_student_ids)
    first_seen = {}   # student code -> student dict created from its first row

    for row_no, row in enumerate(rows[1:], start=2):
        if all(_text(v, 5) == '' for v in row):
            continue  # completely empty line

        data = dict(zip(headers, row))
        code = _text(data.get('student_code'), 80)
        name = _text(data.get('name'), 200)
        if not code or not name:
            plan.skipped.append((row_no, code, 'missing Student code' if not code else 'missing Name'))
            continue

        course_cell = _text(data.get('course'), 200)
        year, bad_year = _year(data.get('passing_year'))
        values = {
            'email': _text(data.get('email'), 200),
            'passing_grade': re.sub(r'\s+', ' ', _text(data.get('passing_grade'), 50)),
            'passing_year': year,
            'course_name': normalize_course_name(course_cell) if course_cell else '',
        }

        if code in first_seen:
            # Same student listed twice: keep one record, fill any blanks from this row.
            first = first_seen[code]
            for key, val in values.items():
                if val and not first[key]:
                    first[key] = val
            if bad_year and not first['bad_year']:
                first['bad_year'] = bad_year
            if name.upper() != first['full_name'].upper():
                plan.notes.append((row_no, code, f'repeated Student code with a different name ("{name}"); kept "{first["full_name"]}"'))
            if values['course_name'] and first['course_name'] and values['course_name'] != first['course_name']:
                plan.notes.append((row_no, code, f'repeated Student code with a different course; kept "{first["course_name"]}"'))
            plan.merged.append((row_no, code, first['row']))
            continue

        if code in existing_ids:
            plan.skipped.append((row_no, code, 'already in the database'))
            continue

        student = {
            'student_id': code,
            'full_name': name,
            'bad_year': bad_year,
            'row': row_no,
            'course_id': None,
            'new_course_key': None,
            **values,
        }
        first_seen[code] = student
        plan.students.append(student)

    _resolve_courses(plan, existing_courses)

    for s in plan.students:
        if not s['course_name']:
            plan.notes.append((s['row'], s['student_id'], 'no course given; imported without a course'))
        if s['bad_year'] and not s['passing_year']:
            plan.notes.append((s['row'], s['student_id'], f'passing year "{s["bad_year"]}" is not a valid 4-digit year; left blank'))
    plan.notes.sort(key=lambda n: n[0])
    return plan


def _resolve_courses(plan, existing_courses):
    by_full, by_title = {}, {}
    for course_id, course_name in sorted(existing_courses, key=lambda c: c[0]):
        full, title = course_keys(course_name)
        by_full.setdefault(full, (course_id, course_name))
        by_title.setdefault(title, (course_id, course_name))

    # If several spellings/short forms of one course appear in the file, the most
    # common one is used as the name when the course has to be created.
    votes = defaultdict(Counter)
    for s in plan.students:
        if s['course_name']:
            votes[course_keys(s['course_name'])[1]][s['course_name']] += 1

    created = {}
    for s in plan.students:
        if not s['course_name']:
            continue
        full, title = course_keys(s['course_name'])
        hit = by_full.get(full) or by_title.get(title)
        if hit:
            s['course_id'] = hit[0]
            entry = plan.matched_courses.setdefault(hit[0], {'name': hit[1], 'students': 0})
            entry['students'] += 1
            continue
        if title not in created:
            best = votes[title].most_common(1)[0][0]
            category = course_category(best)
            created[title] = {
                'key': title,
                'name': best,
                'category': category,
                'section': engineering_section_for(best) if category == 'engineering' else None,
                'students': 0,
            }
            plan.new_courses.append(created[title])
        s['new_course_key'] = title
        created[title]['students'] += 1
