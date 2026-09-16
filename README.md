# SIMTS — SAI INSTITUTE OF MANAGEMENT & TECHNOLOGY STUDIES

Flask + PostgreSQL application for the institute website, student admission verification, course management and protected certificate-image storage.

## Current features
- Public Home, About Us, Courses, Admission Verification and Contact pages
- Management and Engineering course categories
- Admin student management and bulk `.xlsx` import
- Bulk import columns: Student code, Name, Email, Course, Passing grade (optional), Passing year (optional)
- Protected certificate management using PNG/JPG/JPEG images
- Admission verification shows Student Name, Student Code, Course Name, Passing Grade and Passing Year
- A small certificate image is shown beside verified admission details with click-to-zoom/full-size viewing
- No public certificate-verification page and no public certificate download button
- No marksheet feature
- CSRF protection, admin authentication, rate limiting, audit logs and security headers
- Neon/PostgreSQL support and private Vercel Blob support

## Local run
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` with a PostgreSQL `DATABASE_URL` and a strong `SECRET_KEY`. For local certificate images, leave `BLOB_READ_WRITE_TOKEN` unset and use the local `uploads/` folder.

Run:
```powershell
python app.py
```
Then open `http://127.0.0.1:5000/admin/setup`.

## Vercel + Neon + private Blob
Set:
- `DATABASE_URL`
- `SECRET_KEY`
- `BLOB_READ_WRITE_TOKEN`

Certificate images are uploaded to a private Blob store and are fetched server-side with the token before being displayed inline. The Blob URL is not exposed as a public download link.

## Database migration
The application automatically adds the new `student.passing_grade`, `student.passing_year`, `course.category` and existing `course.fees` columns at startup.

Existing course rows with no category are assigned `management`.

For the old marksheet tables/columns from a previous deployment, see `NEON_CLEANUP.sql`. Run that SQL manually only after taking a database backup.

Existing old PDF certificate blobs are not converted automatically. Replace old certificate records through the admin panel with PNG/JPG/JPEG images, then remove obsolete old blobs from Vercel Blob storage if desired.
