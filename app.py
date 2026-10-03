"""
Flask Web Application for eCourts Judicial Intelligence & Document Explorer
Production API and static web server.
Strictly serves confirmed metadata from PostgreSQL and local PDFs.
No fake statistics, no PDF-content extraction.
"""

import os
import sys
import threading
import subprocess
from datetime import datetime
from flask import Flask, jsonify, request, send_file, send_from_directory, abort, redirect

import data_service

app = Flask(__name__, static_folder="static", static_url_path="")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(BASE_DIR, "downloaded_pdfs")

# Live scraper worker tracking
CURRENT_SCRAPER_JOB = {
    "is_running": False,
    "started_at": None,
    "court_info": None,
    "log_lines": [],
    "error": None
}

# -------------------------------------------------------------
# Frontend Page Routes
# -------------------------------------------------------------
@app.route("/")
def serve_index():
    return send_from_directory("static", "index.html")

@app.route("/judges")
@app.route("/judges.html")
def serve_judges_page():
    return send_from_directory("static", "judges.html")

@app.route("/supreme-court-judges")
@app.route("/supreme-court-judges.html")
def serve_supreme_court_judges_page():
    return redirect("/judges.html?court=supreme", code=302)

@app.route("/judge")
@app.route("/judge.html")
def serve_judge_detail_page():
    return send_from_directory("static", "judge.html")

@app.route("/supreme-court-judge")
@app.route("/supreme-court-judge.html")
def serve_supreme_court_judge_detail_page():
    return send_from_directory("static", "supreme-court-judge.html")

@app.route("/pdfs")
@app.route("/pdfs.html")
def serve_pdfs_page():
    return send_from_directory("static", "pdfs.html")

@app.route("/pdf-viewer")
@app.route("/pdf-viewer.html")
def serve_pdf_viewer_page():
    return send_from_directory("static", "pdf-viewer.html")

@app.route("/runs")
@app.route("/runs.html")
def serve_runs_page():
    return send_from_directory("static", "runs.html")


# -------------------------------------------------------------
# REST API Endpoints
# -------------------------------------------------------------
@app.route("/api/overview")
def api_overview():
    try:
        stats = data_service.get_overview_stats()
        return jsonify({"success": True, "data": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/judges")
def api_judges():
    try:
        q = request.args.get("q") or request.args.get("query")
        sort_by = request.args.get("sort", "total_pdfs")
        order = request.args.get("order", "desc")
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 20))

        result = data_service.get_judges(
            query=q,
            sort_by=sort_by,
            sort_order=order,
            page=page,
            limit=limit
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/judge-directory")
def api_judge_directory():
    try:
        result = data_service.get_judge_directory(
            query=request.args.get("q") or request.args.get("query"),
            court=request.args.get("court", "all"),
            sort_by=request.args.get("sort", "record_count"),
            sort_order=request.args.get("order", "desc"),
            page=int(request.args.get("page", 1)),
            limit=int(request.args.get("limit", 20))
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/judges/<path:judge_name>")
def api_judge_detail_path(judge_name):
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        judge = data_service.get_judge_detail(judge_name, start_date=start_date, end_date=end_date)
        if not judge:
            return jsonify({"success": False, "error": "Judge not found"}), 404
        return jsonify({"success": True, "data": judge})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/judge")
def api_judge_detail_param():
    try:
        judge_name = request.args.get("name")
        if not judge_name:
            return jsonify({"success": False, "error": "Judge name required"}), 400
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        judge = data_service.get_judge_detail(judge_name, start_date=start_date, end_date=end_date)
        if not judge:
            return jsonify({"success": False, "error": "Judge not found"}), 404
        return jsonify({"success": True, "data": judge})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/pdfs")
def api_pdfs():
    try:
        q = request.args.get("q") or request.args.get("query")
        judge = request.args.get("judge")
        date_filter = request.args.get("date")
        court_code = request.args.get("court")
        state_code = request.args.get("state")
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 25))

        result = data_service.get_pdfs(
            query=q,
            judge=judge,
            date_filter=date_filter,
            court_code=court_code,
            state_code=state_code,
            page=page,
            limit=limit
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/pdf/metadata")
def api_pdf_metadata():
    try:
        filename = request.args.get("file") or request.args.get("filename")
        pdf_id = request.args.get("id")
        doc = data_service.get_pdf_details(filename=filename, pdf_id=pdf_id)
        if not doc:
            return jsonify({"success": False, "error": "Document metadata not found"}), 404
        return jsonify({"success": True, "data": doc})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/pdf/<path:filename>")
def serve_pdf(filename):
    """
    Safely streams a cause list PDF directly to browser.
    Supports inline rendering for viewer and attachment for downloads.
    Guards against path traversal attacks.
    """
    safe_path = data_service.get_safe_pdf_path(filename)
    if not safe_path or not os.path.isfile(safe_path):
        abort(404, description="PDF file not found on disk")

    download = request.args.get("download", "").lower() in ("1", "true", "yes")
    download_name = os.path.basename(safe_path)

    return send_file(
        safe_path,
        mimetype="application/pdf",
        as_attachment=download,
        download_name=download_name
    )

# -------------------------------------------------------------
# Supreme Court API Endpoints
# -------------------------------------------------------------
@app.route("/api/supreme-court/overview")
def api_supreme_court_overview():
    try:
        stats = data_service.get_supreme_court_overview()
        if not stats:
            return jsonify({"success": True, "data": None, "message": "No Supreme Court data available. Run import_supreme_court.py first."})
        return jsonify({"success": True, "data": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/supreme-court/judges")
def api_supreme_court_judges():
    try:
        q = request.args.get("q") or request.args.get("query")
        sort_by = request.args.get("sort", "total_cases")
        order = request.args.get("order", "desc")
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 20))

        result = data_service.get_supreme_court_judges(
            query=q,
            sort_by=sort_by,
            sort_order=order,
            page=page,
            limit=limit
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/supreme-court/judges/<path:judge_name>")
def api_supreme_court_judge_detail(judge_name):
    try:
        detail = data_service.get_supreme_court_judge_detail(judge_name)
        if not detail:
            return jsonify({"success": False, "error": "Supreme Court judge not found"}), 404
        return jsonify({"success": True, "data": detail})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/scraper/status")
def api_scraper_status():
    """Returns current live job status and PostgreSQL scraper runs history"""
    try:
        recent_runs = data_service.get_scraper_runs()
        return jsonify({
            "success": True,
            "live_job": CURRENT_SCRAPER_JOB,
            "recent_runs": recent_runs
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def _run_scraper_worker(state_arg, court_arg, days_arg):
    global CURRENT_SCRAPER_JOB
    CURRENT_SCRAPER_JOB["is_running"] = True
    CURRENT_SCRAPER_JOB["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    CURRENT_SCRAPER_JOB["court_info"] = f"State: {state_arg}, Court: {court_arg}, Days: {days_arg}"
    CURRENT_SCRAPER_JOB["log_lines"] = [f"Starting scraper for State {state_arg}, Court {court_arg}, Days {days_arg}..."]
    CURRENT_SCRAPER_JOB["error"] = None

    try:
        # Launch scraper/1.py without altering its code
        cmd = [
            sys.executable,
            os.path.join(BASE_DIR, "scraper", "1.py"),
            "--state", str(state_arg),
            "--court", str(court_arg) if court_arg else "1",
            "--days", str(days_arg)
        ]

        proc = subprocess.Popen(
            cmd,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        for line in iter(proc.stdout.readline, ''):
            if line:
                CURRENT_SCRAPER_JOB["log_lines"].append(line.strip())
                if len(CURRENT_SCRAPER_JOB["log_lines"]) > 100:
                    CURRENT_SCRAPER_JOB["log_lines"].pop(0)

        proc.wait()
        CURRENT_SCRAPER_JOB["log_lines"].append(f"Scraper process completed with exit code: {proc.returncode}")
        # Refresh disk cache after download
        data_service.get_disk_pdf_index(force=True)
    except Exception as e:
        CURRENT_SCRAPER_JOB["error"] = str(e)
        CURRENT_SCRAPER_JOB["log_lines"].append(f"Error executing scraper: {e}")
    finally:
        CURRENT_SCRAPER_JOB["is_running"] = False

@app.route("/api/scraper/trigger", methods=["POST"])
def api_scraper_trigger():
    global CURRENT_SCRAPER_JOB
    if CURRENT_SCRAPER_JOB["is_running"]:
        return jsonify({"success": False, "error": "A scraper job is already active"}), 400

    payload = request.get_json(silent=True) or {}
    state_code = payload.get("state_code", "1")
    court_code = payload.get("court_code", "1")
    days = payload.get("days", 3)

    t = threading.Thread(target=_run_scraper_worker, args=(state_code, court_code, days), daemon=True)
    t.start()

    return jsonify({"success": True, "message": "Scraper job started in background"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting eCourts Judicial Intelligence Platform on http://127.0.0.1:{port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False)
