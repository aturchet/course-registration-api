import pytest
import io

def test_full_academic_lifecycle(client):
    """
    Test integration flow: 
    -Import Catalog 
    -Import History 
    -Add Plan 
    -Audit for Graduation Progress
    """
    
    # 1. Setup: Import Catalog
    catalog_html = b"""
    <table>
        <tr><th>Course Code</th><th>Title</th><th>Credits</th><th>Prerequisites</th></tr>
        <tr><td>CS101</td><td>Intro</td><td>3</td><td></td></tr>
        <tr><td>CS102</td><td>Advanced</td><td>3</td><td>CS101</td></tr>
    </table>
    """
    response_cat = client.post(
        "/api/v1/admin/catalog/import", 
        files={"file": ("catalog.html", io.BytesIO(catalog_html), "text/html")}
    )
    assert response_cat.status_code == 201

    # 2. Setup: Import Student History
    student_id = "STU_99"
    history_payload = {
        "history": [
            {"course_code": "CS101", "term": "24F", "credits_earned": 3, "status": "Completed"}
        ]
    }
    client.put(f"/api/v1/students/{student_id}/history", json=history_payload)

    # 3. Action: Add Planned Course (Dependent on CS101)
    plan_payload = {
        "planned_courses": [
            {"course_code": "CS102", "term": "25SP"}
        ]
    }
    client.post(f"/api/v1/students/{student_id}/plan", json=plan_payload)

    # 4. Verification: Audit Report
    audit_resp = client.get(f"/api/v1/students/{student_id}/audit-report")
    assert audit_resp.status_code == 200
    
    data = audit_resp.json()
    assert data["status"] == "ok"
    assert data["credit_summary"]["total_earned"] == 3
    assert data["credit_summary"]["total_planned"] == 3
    # 120 - 3 - 3 = 114
    assert data["credit_summary"]["total_remaining_for_graduation"] == 114

def test_integration_missing_prerequisite(client):
    """Verifies that the audit correctly flags a missing prerequisite."""
    # Setup Catalog
    catalog_html = b"""
    <table>
        <tr><th>Course Code</th><th>Prerequisites</th></tr>
        <tr><td>CS102</td><td>CS101</td></tr>
    </table>
    """
    client.post("/api/v1/admin/catalog/import", files={"file": ("c.html", io.BytesIO(catalog_html), "text/html")})

    # Plan CS102 without ever taking CS101
    client.put("/api/v1/students/S1/history", json={"history": []})
    client.post("/api/v1/students/S1/plan", json={"planned_courses": [{"course_code": "CS102", "term": "25SP"}]})

    # Audit
    audit_resp = client.get("/api/v1/students/S1/audit-report")
    data = audit_resp.json()
    
    # Check for the specific error type
    errors = data["timeline_validation"][0]["errors"]
    assert any(err["type"] == "MISSING_PREREQUISITE" for err in errors)