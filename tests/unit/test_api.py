import pytest
import io
from main import normalize_course, get_grade_weight, term_sort_key, students_db

# --- UNIT TESTS (Logic) ---


def test_normalize_course():
    assert normalize_course("CS-101!") == "CS101"
    assert normalize_course("  math 200  ") == "MATH200"


def test_get_grade_weight():
    assert get_grade_weight("4.0") == 3
    assert get_grade_weight("A") == 2
    assert get_grade_weight("P") == 1


def test_term_sort_key():
    assert term_sort_key("24F") == (24, 4)
    assert term_sort_key("24SP") == (24, 2)
    assert term_sort_key("invalid") == (99, 99)


# --- INTEGRATION TESTS (API Endpoints) ---


def test_catalog_import(client):
    html_content = b"""
    <table>
        <tr><th>Course Code</th><th>Title</th><th>Credits</th></tr>
        <tr><td>CS101</td><td>Intro to CS</td><td>3</td></tr>
    </table>
    """
    file = io.BytesIO(html_content)
    response = client.post(
        "/api/v1/admin/catalog/import", files={"file": ("test.html", file, "text/html")}
    )
    assert response.status_code == 201
    assert response.json()["courses_imported"] == 1


def test_student_history_and_audit(client):
    # 1. Setup Catalog
    html_cat = b"""
    <table>
        <tr>
            <th>Course Code</th>
            <th>Title</th>
            <th>Credits</th>
            <th>Prerequisites</th>
            <th>Cross-listed</th>
        </tr>
        <tr><td>CS101</td><td>Intro</td><td>3</td><td></td><td></td></tr>
        <tr><td>CS102</td><td>Advanced</td><td>3</td><td>CS101</td><td></td></tr>
        <tr><td>MATH101</td><td>Math</td><td>3</td><td></td><td>STAT101</td></tr>
        <tr><td>STAT101</td><td>Stats</td><td>3</td><td></td><td>MATH101</td></tr>
    </table>
    """
    res_cat = client.post(
        "/api/v1/admin/catalog/import",
        files={"file": ("cat.html", io.BytesIO(html_cat), "text/html")},
    )
    assert res_cat.status_code == 201

    # 2.Import HTML Transcript to initialize the student
    html_transcript = b"""
    <table>
        <tr>
            <th>Status</th>
            <th>Course</th>
            <th>Grade</th>
            <th>Term</th>
            <th>Credits</th>
        </tr>
        <tr><td>Completed</td><td>CS101</td><td>A</td><td>24F</td><td>3</td></tr>
        <tr><td>Completed</td><td>MATH101</td><td>B</td><td>24F</td><td>3</td></tr>
        <!-- Triggers the deduplication / weight replacement logic for extra coverage -->
        <tr><td>Completed</td><td>MATH101</td><td>A</td><td>24F</td><td>3</td></tr> 
        <!-- Triggers the pass/blank grade logic -->
        <tr><td>In-Progress</td><td>ENG101</td><td>P</td><td>25SP</td><td>3</td></tr>
    </table>
    """
    res_hist_import = client.post(
        "/api/v1/students/STUDENT1/history/import",
        files={"file": ("transcript.html", io.BytesIO(html_transcript), "text/html")},
    )
    assert res_hist_import.status_code == 201

    #  PUT request to ensure the update endpoint is covered
    payload = {
        "history": [
            {
                "course_code": "CS101",
                "term": "24F",
                "credits_earned": 3,
                "status": "Completed",
            },
            {
                "course_code": "MATH101",
                "term": "24F",
                "credits_earned": 3,
                "status": "Completed",
            },
        ]
    }
    res_hist = client.put("/api/v1/students/STUDENT1/history", json=payload)
    assert res_hist.status_code == 200

    # 3. Add Plan
    plan_payload = {
        "planned_courses": [
            {"course_code": "CS101", "term": "25SP"},  # Triggers DUPLICATE
            {"course_code": "CS102", "term": "24F"},  # Triggers PREREQUISITE
            {"course_code": "STAT101", "term": "25SP"},  # Triggers CROSS-LIST
        ]
    }
    res_plan = client.post("/api/v1/students/STUDENT1/plan", json=plan_payload)
    assert res_plan.status_code in [200, 201]

    # 4. Audit
    response = client.get("/api/v1/students/STUDENT1/audit-report")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "warning"
    assert data["credit_summary"]["total_earned"] == 6
    assert len(data["cross_list_violations"]) == 1
    assert data["cross_list_violations"][0]["type"] == "CROSS_LIST_CONFLICT"


def test_audit_missing_student(client):
    response = client.get("/api/v1/students/NONEXISTENT/audit-report")
    assert response.status_code == 404
