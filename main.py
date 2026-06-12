from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.responses import JSONResponse
from bs4 import BeautifulSoup
from typing import List
import re

app = FastAPI(title="University Catalog Ingestion API (No Pydantic)", version="1.0.0")

# In-memory database to store parsed courses
# Key: course_code (string, uppercase), Value: dict
COURSE_DB = {}

def normalize_code(code: str) -> str:
    """Removes all whitespace and non-alphanumeric characters, returning uppercase.
    
    Transforms 'CS 101', 'cs101', and 'CS-101' all into 'CS101'.
    """
    if not code:
        return ""
    return re.sub(r'[^a-zA-Z0-9]', '', code).upper()


# --- Helper Utilities ---
def clean_text(element) -> str:
    """Removes leading/trailing whitespaces and replaces non-breaking spaces."""
    if not element:
        return ""
    return element.get_text(strip=True).replace("\xa0", " ")


def parse_comma_separated(text: str) -> List[str]:
    """Splits a comma-separated string into a list of cleaned strings."""
    if not text or text.lower() in ["none", "n/a", "nil", "", "-"]:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


# --- Endpoints ---

@app.post(
    "/api/v1/admin/catalog/import",
    status_code=status.HTTP_201_CREATED,
    summary="Import university courses from an HTML table file"
)
async def import_catalog(file: UploadFile = File(...)):
    # Validate file type extension
    if not file.filename.endswith((".html", ".htm")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Please upload an HTML file."
        )

    try:
        # Read file contents
        content = await file.read()
        soup = BeautifulSoup(content, "html.parser")
        
        # Find the table element
        table = soup.find("table")
        if not table:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No HTML table element found in the uploaded file."
            )

        # Parse table rows
        rows = table.find_all("tr")
        imported_count = 0

        for row in rows:
            cells = row.find_all(["td", "th"])
            
            # Skip empty rows, header tags, or rows containing the literal label "course code"
            if not cells or cells[0].name == "th" or "course code" in cells[0].get_text().lower():
                continue
                
            # Ensure the row has at least 5 columns matching the schema requirements
            if len(cells) < 5:
                continue

            # Extract fields via cell positions
            raw_code = clean_text(cells[0])
            raw_title = clean_text(cells[1])
            raw_credits = clean_text(cells[2])
            raw_prereqs = clean_text(cells[3])
            raw_cross_listed = clean_text(cells[4])

            # Validation fallback for missing core identifiers
            if not raw_code or not raw_title:
                continue

            # Parse numeric string safely to a float
            try:
                credits_val = float(raw_credits) if raw_credits else 0.0
            except ValueError:
                credits_val = 0.0

            # Generate regular Python dictionaries
            course_data = {
                "course_code": raw_code,
                "title": raw_title,
                "credits": credits_val,
                "prerequisites": parse_comma_separated(raw_prereqs),
                "cross_listed": parse_comma_separated(raw_cross_listed)
            }

            # Save data to dictionary memory store using uppercase keys
            lookup_key = normalize_code(raw_code)
            COURSE_DB[lookup_key] = course_data
            imported_count += 1

        return {
            "status": "success",
            "message": f"Successfully parsed and imported {imported_count} courses."
        }

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while parsing the file: {str(e)}"
        )


@app.get(
    "/api/v1/catalog/courses/{course_code}",
    status_code=status.HTTP_200_OK,
    summary="Get a structured course by its code"
)
async def get_course(course_code: str):
    # Normalize lookup key to uppercase
    lookup_key = course_code.upper()
    
    if lookup_key not in COURSE_DB:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Course with code '{course_code}' not found."
        )
        
    # Return standard dictionary; FastAPI transforms this directly to JSON serialization
    return COURSE_DB[lookup_key]