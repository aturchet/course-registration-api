from bs4 import BeautifulSoup
from typing import List
import re
import uvicorn
import os
from typing import List, Dict, Optional, Tuple
from typing_extensions import Literal
from fastapi import FastAPI, UploadFile, File, HTTPException, status
from pydantic import BaseModel

app = FastAPI(title="Course Registration API", version="3.0.0")

# IN-MEMORY DATA STORES
catalog_db: Dict[str, dict] = {}

students_db: Dict[str, dict] = {}


# PYDANTIC MODELS
class CourseCatalogRecord(BaseModel):
    course_code: str
    title: Optional[str] = ""
    credits: Optional[int] = 0
    prerequisites: Optional[str] = ""
    cross_listed: Optional[str] = ""


class HistoryRecord(BaseModel):
    course_code: str
    term: str
    credits_earned: int
    status: str


class HistoryUpdatePayload(BaseModel):
    history: List[HistoryRecord]


class PlanRecord(BaseModel):
    course_code: str
    term: str


class PlanUpdatePayload(BaseModel):
    planned_courses: List[PlanRecord]


class StudentProfile(BaseModel):
    student_id: str
    history: List[HistoryRecord]
    plan: List[PlanRecord]


class CourseError(BaseModel):
    course_code: str
    type: str
    message: str


class TermValidation(BaseModel):
    term: str
    errors: List[CourseError]


class CreditSummary(BaseModel):
    total_earned: int
    total_planned: int
    total_remaining_for_graduation: int


class AuditReport(BaseModel):
    student_id: str
    status: Literal["ok", "warning", "failed"]
    timeline_validation: List[TermValidation]
    cross_list_violations: List[CourseError]
    credit_summary: CreditSummary


# UTILITY PARSING FUNCTIONS
def normalize_course(code: str) -> str:
    """Removes all whitespace and non-alphanumeric characters, returning uppercase."""
    return re.sub(r"[^a-zA-Z0-9]", "", code).upper()


def get_grade_weight(grade_str: str) -> int:
    """Returns a weight used for deduplicating grades: Numeric > Letter > P/Blank."""
    g = grade_str.strip()
    if not g:
        return 1
    try:
        float(g)
        return 3  # Numeric grade
    except ValueError:
        pass

    if g.upper() in ["P", "CR", "S", "PASS"]:
        return 1  # Pass/Credit/Blank
    return 2  # Standard Letter Grade (A, B+, C-, etc.)


def check_student_exists(student_id: str):
    """Dependency helper to ensure student state exists."""
    if student_id not in students_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found. Must perform history import first.",
        )


def term_sort_key(term: str) -> Tuple[int, int]:
    """
    Parses a term string like '24F' or '26SP' into a sortable tuple: (year, season_rank).
    Season order: W (1) < SP (2) < S (3) < F (4).
    """
    match = re.match(r"^(\d{2})(W|SP|S|F)$", term.strip().upper())
    if not match:
        return (99, 99)

    year = int(match.group(1))
    season_str = match.group(2)

    season_ranks = {"W": 1, "SP": 2, "S": 3, "F": 4}
    return (year, season_ranks[season_str])


def parse_prerequisites(prereq_string: str) -> List[str]:
    """Parses a comma-separated prerequisite string into normalized course codes."""
    if not prereq_string:
        return []
    return [normalize_course(code) for code in prereq_string.split(",")]


# COURSE ENDPOINTS
@app.post(
    "/api/v1/admin/catalog/import",
    status_code=status.HTTP_201_CREATED,
    summary="Import university courses from an HTML table file",
)
async def import_catalog(file: UploadFile = File(...)):
    """Parses the Catalog HTML and stores it in memory."""
    contents = await file.read()
    soup = BeautifulSoup(contents, "html.parser")
    imported_count = 0

    # Iterate through tables dynamically looking for catalog headers
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        headers = [
            th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])
        ]

        # Look for standard catalog column identifiers
        if "course code" in headers:
            idx_code = headers.index("course code")
            idx_title = headers.index("title") if "title" in headers else -1
            idx_cred = headers.index("credits") if "credits" in headers else -1
            idx_prereq = (
                headers.index("prerequisites") if "prerequisites" in headers else -1
            )
            idx_cross = (
                headers.index("cross-listed") if "cross-listed" in headers else -1
            )

            for row in rows[1:]:
                cols = row.find_all("td")
                if len(cols) <= idx_code:
                    continue

                original_code = cols[idx_code].get_text(strip=True)
                if not original_code:
                    continue

                title = (
                    cols[idx_title].get_text(strip=True)
                    if idx_title != -1 and len(cols) > idx_title
                    else ""
                )
                cred_str = (
                    cols[idx_cred].get_text(strip=True)
                    if idx_cred != -1 and len(cols) > idx_cred
                    else "0"
                )
                prereq = (
                    cols[idx_prereq].get_text(strip=True)
                    if idx_prereq != -1 and len(cols) > idx_prereq
                    else ""
                )
                cross = (
                    cols[idx_cross].get_text(strip=True)
                    if idx_cross != -1 and len(cols) > idx_cross
                    else ""
                )

                credits_val = int(cred_str) if cred_str.isdigit() else 0

                # Key the database using the normalized code, but preserve original in the payload
                norm_code = normalize_course(original_code)

                catalog_db[norm_code] = {
                    "course_code": original_code,
                    "title": title,
                    "credits": credits_val,
                    "prerequisites": prereq,
                    "cross_listed": cross,
                }
                imported_count += 1

    imported = imported_count

    return {"status": "success", "courses_imported": imported}


@app.get(
    "/api/v1/catalog/courses/{course_code}",
    status_code=status.HTTP_200_OK,
    summary="Get a structured course by its code",
    response_model=CourseCatalogRecord,
)
async def get_course(course_code: str):
    # Apply the exact normalization requested before looking up in the db
    lookup_key = normalize_course(course_code)

    if lookup_key not in catalog_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found"
        )

    return catalog_db[lookup_key]


# STUDENT ENDPOINTS
@app.post(
    "/api/v1/students/{student_id}/history/import", status_code=status.HTTP_201_CREATED
)
async def import_student_history(student_id: str, file: UploadFile = File(...)):
    """Parses Transcript HTML, applying the deduplication logic across all tables."""
    contents = await file.read()
    soup = BeautifulSoup(contents, "html.parser")
    valid_statuses = {"Completed", "In-Progress", "Attempted"}

    # dict to handle deduplication: key -> (record_dict, grade_weight, credits_earned)
    dedup_map = {}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        headers = [
            th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])
        ]

        # Check if this table contains the required transcript columns
        if all(
            req in headers for req in ["status", "course", "grade", "term", "credits"]
        ):
            idx_status = headers.index("status")
            idx_course = headers.index("course")
            idx_grade = headers.index("grade")
            idx_term = headers.index("term")
            idx_credits = headers.index("credits")

            for row in rows[1:]:
                cols = row.find_all(["td", "th"])
                # Ensure row has enough columns
                if len(cols) <= max(
                    idx_status, idx_course, idx_grade, idx_term, idx_credits
                ):
                    continue

                status_val = cols[idx_status].get_text(strip=True)
                course_val = cols[idx_course].get_text(strip=True)
                grade_val = cols[idx_grade].get_text(strip=True)
                term_val = cols[idx_term].get_text(strip=True)
                credits_val_str = cols[idx_credits].get_text(strip=True)

                # Rules for valid row
                if status_val not in valid_statuses:
                    continue
                if not term_val:
                    continue

                credits_earned = (
                    int(credits_val_str) if credits_val_str.isdigit() else 0
                )
                weight = get_grade_weight(grade_val)

                dedup_key = (course_val, term_val)
                record_dict = {
                    "course_code": course_val,
                    "term": term_val,
                    "credits_earned": credits_earned,
                    "status": status_val,
                }

                # Deduplication / Conflict Resolution
                if dedup_key not in dedup_map:
                    dedup_map[dedup_key] = (record_dict, weight, credits_earned)
                else:
                    _, existing_weight, existing_credits = dedup_map[dedup_key]
                    if weight > existing_weight:
                        dedup_map[dedup_key] = (record_dict, weight, credits_earned)
                    elif (
                        weight == existing_weight and credits_earned > existing_credits
                    ):
                        dedup_map[dedup_key] = (record_dict, weight, credits_earned)

    records = [item[0] for item in dedup_map.values()]

    # Initialize or update student in memory
    if student_id not in students_db:
        students_db[student_id] = {"history": [], "plan": []}

    students_db[student_id]["history"] = records

    return {"status": "success", "past_courses_imported": len(records)}


@app.put("/api/v1/students/{student_id}/history")
async def update_student_history(student_id: str, payload: HistoryUpdatePayload):
    check_student_exists(student_id)
    # Serialize Pydantic objects to dicts for clean memory storage
    students_db[student_id]["history"] = [
        record.model_dump() for record in payload.history
    ]
    return {"status": "success", "message": "Academic history updated successfully"}


@app.delete(
    "/api/v1/students/{student_id}/history", status_code=status.HTTP_204_NO_CONTENT
)
async def clear_student_history(student_id: str):
    check_student_exists(student_id)
    students_db[student_id]["history"] = []
    return None


# PLAN ENDPOINTS
@app.post("/api/v1/students/{student_id}/plan")
async def add_student_plan(student_id: str, payload: PlanUpdatePayload):
    check_student_exists(student_id)
    # Append to existing plan logic or store standard list
    new_plans = [record.model_dump() for record in payload.planned_courses]
    students_db[student_id]["plan"].extend(new_plans)

    return {"status": "success", "planned_courses_saved": len(new_plans)}


@app.put("/api/v1/students/{student_id}/plan")
async def replace_student_plan(student_id: str, payload: PlanUpdatePayload):
    check_student_exists(student_id)
    # Entirely overwrite
    students_db[student_id]["plan"] = [
        record.model_dump() for record in payload.planned_courses
    ]

    return {
        "status": "success",
        "message": "Academic plan completely replaced",
        "planned_courses_saved": len(students_db[student_id]["plan"]),
    }


@app.delete(
    "/api/v1/students/{student_id}/plan", status_code=status.HTTP_204_NO_CONTENT
)
async def clear_student_plan(student_id: str):
    check_student_exists(student_id)
    students_db[student_id]["plan"] = []
    return None


# PROFILE ENDPOINT
@app.get("/api/v1/students/{student_id}/profile", response_model=StudentProfile)
async def get_student_profile(student_id: str):
    check_student_exists(student_id)
    return {
        "student_id": student_id,
        "history": students_db[student_id]["history"],
        "plan": students_db[student_id]["plan"],
    }


# AUDIT ENDPOINT
@app.get("/api/v1/students/{student_id}/audit-report", response_model=AuditReport)
async def generate_audit_report(student_id: str, strict: bool = False):
    check_student_exists(student_id)
    student = students_db[student_id]

    # Sort history and plan chronologically using the custom key
    history = sorted(
        student.get("history", []), key=lambda x: term_sort_key(x.get("term", ""))
    )
    plan = sorted(
        student.get("plan", []), key=lambda x: term_sort_key(x.get("term", ""))
    )

    completed_terms: Dict[str, str] = {}
    earned_credits_map: Dict[str, int] = {}

    # New structures for the updated schema
    timeline_errors_by_term: Dict[str, List[dict]] = {}
    cross_list_violations: List[dict] = []

    def add_timeline_error(term: str, code: str, err_type: str, msg: str):
        if term not in timeline_errors_by_term:
            timeline_errors_by_term[term] = []
        timeline_errors_by_term[term].append(
            {"course_code": code, "type": err_type, "message": msg}
        )

    # process historyy
    for record in history:
        course_code = normalize_course(record["course_code"])
        term = record["term"]
        record_status = record["status"].lower()
        original_code = record[
            "course_code"
        ]  # Preserve original casing/hyphens for output

        catalog_entry = catalog_db.get(course_code, {})
        cross_listed_with = normalize_course(catalog_entry.get("cross_listed", ""))

        if record_status == "completed":
            if cross_listed_with and cross_listed_with in completed_terms:
                continue

            completed_terms[course_code] = term
            earned_credits_map[course_code] = record.get("credits_earned", 0)
        else:
            if course_code not in completed_terms:
                earned_credits_map[course_code] = 0

    total_earned = sum(earned_credits_map.values())
    total_planned = 0

    # plan
    for record in plan:
        course_code = normalize_course(record["course_code"])
        term_planned = record["term"]
        original_code = record["course_code"]  # Preserve casing/hyphens for output

        # 1. Check if course exists in catalog FIRST
        catalog_entry = catalog_db.get(course_code)
        if not catalog_entry:
            add_timeline_error(
                term=term_planned,
                code=original_code,
                err_type="UNKNOWN_COURSE",
                msg="Course is unknown in catalog.",
            )
            continue

        # 2. Add credits before checks
        total_planned += catalog_entry.get("credits", 0)

        # 3. Deduplication/Retake Check
        if course_code in completed_terms:
            add_timeline_error(
                term=term_planned,
                code=original_code,
                err_type="DUPLICATE_COURSE",
                msg="Course is already completed.",
            )
            continue

        # 4. Cross-listing Check
        cross_listed_with = normalize_course(catalog_entry.get("cross_listed", ""))
        if cross_listed_with and cross_listed_with in completed_terms:
            original_cross = catalog_entry.get("cross_listed", cross_listed_with)
            cross_list_violations.append(
                {
                    "course_code": original_code,
                    "type": "CROSS_LIST_CONFLICT",
                    "message": f"Cross-listed with completed course {original_cross}",
                }
            )
            continue

        # Prerequisite Evaluation
        reqs = parse_prerequisites(catalog_entry.get("prerequisites", ""))
        missing_reqs = []
        for req in reqs:
            if req not in completed_terms:
                missing_reqs.append(req)
            else:
                req_term = completed_terms[req]
                if term_sort_key(req_term) >= term_sort_key(term_planned):
                    missing_reqs.append(req)

        if missing_reqs:
            add_timeline_error(
                term=term_planned,
                code=original_code,
                err_type="MISSING_PREREQUISITE",
                msg=f"Missing prerequisite: {', '.join(missing_reqs)}",
            )

        completed_terms[course_code] = term_planned

    # determine path to graduation
    total_remaining_for_graduation = max(0, 120 - total_earned - total_planned)

    # Format the timeline errors into the List[TermValidation] structure
    timeline_validation = [
        {"term": term, "errors": errs} for term, errs in timeline_errors_by_term.items()
    ]

    has_issues = len(timeline_validation) > 0 or len(cross_list_violations) > 0

    # Evaluate strict parameter
    if has_issues:
        final_status = "failed" if strict else "warning"
    else:
        final_status = "ok"

    return {
        "student_id": student_id,
        "status": final_status,
        "timeline_validation": timeline_validation,
        "cross_list_violations": cross_list_violations,
        "credit_summary": {
            "total_earned": total_earned,
            "total_planned": total_planned,
            "total_remaining_for_graduation": total_remaining_for_graduation,
        },
    }


# ENTRY POINT
if __name__ == "__main__":
    # Fetch the PORT environment variable.
    # Fallback to 8000 if it isn't set, and cast to an integer.
    port = int(os.getenv("PORT", 8000))

    # Run the FastAPI app using Uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
