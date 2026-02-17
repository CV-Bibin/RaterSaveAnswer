import sqlite3
import re
import json
from flask import Flask, render_template, request, jsonify
from collections import OrderedDict # Imported for grouping tasks

app = Flask(__name__)

# --- Database Configuration ---
def get_db_connection():
    conn = sqlite3.connect('tasks.db', timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS answers 
                 (id INTEGER PRIMARY KEY, task_id TEXT, query TEXT, 
                  header_info TEXT, rating_results TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- Helper Functions (Unchanged) ---

def clean_value(val):
    """
    Cleans up rating values. 
    Returns 'n/a' if explicitly stated, otherwise '-' for missing/garbage.
    """
    if not val: return "-"
    if val.strip().lower() == "n/a": return "n/a"

    # List of headers that might accidentally get captured as values
    garbage_headers = [
        "Street Number", "Unit/Apt", "Street Name", "Sub-Locality", 
        "Locality", "Region/State", "Postal Code", "Country", 
        "Address does not exist", "Language/Script issue", "Other Issue",
        "Name Issue", "Category Issue", "User intent issue", "Distance/Prominence issue"
    ]
    
    if val.strip() in garbage_headers: return "-"
    return val.strip()

def is_valid_meta(val):
    """
    Checks if a metadata value (Distance/Lat,Lng) is valid.
    It must contain digits. If it's a status message, return False.
    """
    if not val: return False
    # If the value is a known error message or status, ignore it
    garbage_starts = ["Result name", "Business/POI", "Relevance", "Type", "Category", "Status"]
    if any(val.startswith(k) for k in garbage_starts):
        return False
    
    # Must contain at least one digit to be a distance or coordinate
    if not re.search(r'\d', val):
        return False
        
    return True

def process_text(text):
    # --- 1. Header Extraction ---
    headers = {}
    
    # helper to find value by key
    def get_header(key):
        # Matches "Key Name" followed by newline and then the Value
        m = re.search(rf'{key}\s*\n(.+)', text)
        return m.group(1).strip() if m else "Unknown"

    headers["Task ID"] = get_header("Task ID")
    headers["Task Type"] = get_header("Task Type")
    headers["Viewport Age"] = get_header("Viewport Age")
    headers["Locale"] = get_header("Locale")
    headers["Lat, Lng"] = get_header("Lat, Lng")

    # Extract Query (Special case: often has double newlines)
    q_match = re.search(r'Query\s*\n\s*\n(.+)', text)
    if not q_match: 
        q_match = re.search(r'Query\s*\n(.+)', text)
    headers["Query"] = q_match.group(1).strip() if q_match else "Unknown Query"

    # --- 2. Result Extraction ---
    raw_results = re.split(r'\n(\d+)\.\s*\n', text)
    processed_results = []
    
    system_keywords = ["Category", "Type", "Status", "Distance", "Relevance", "Lat, Lng", "Result name"]

    for i in range(1, len(raw_results), 2):
        res_num = raw_results[i]
        content = raw_results[i+1].strip()
        
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        title = ""
        subtitle = ""

        if lines:
            title = lines[0]
            if len(lines) > 1:
                candidate = lines[1]
                is_keyword = any(candidate.startswith(k) for k in system_keywords)
                if not is_keyword:
                    subtitle = candidate

        # Meta Data
        meta_table = []
        target_keys = ["Category", "Type", "Distance to User", "Distance to Viewport", "Lat, Lng"]
        
        for key in target_keys:
            m = re.search(rf'{key}\s*\n(.+)', content)
            if m:
                val = m.group(1).strip()
                if is_valid_meta(val):
                     if not any(d['value'] == val for d in meta_table):
                        meta_table.append({"label": key, "value": val})

        # Ratings
        def get_val(pattern):
            m = re.search(pattern, content, re.IGNORECASE)
            return clean_value(m.group(1).strip()) if m else "-"

        rel_m = re.search(r'Relevance\s*\n(.+)', content)
        rel_val = rel_m.group(1).strip() if rel_m else "Not Rated"

        name_val = get_val(r'Name Accuracy\s*\n(.+)')
        addr_val = get_val(r'Address Accuracy\s*\n(.+)')
        pin_val  = get_val(r'(?:Pin Accuracy|Pin/Zip Accuracy)\s*\n(.+)')

        ratings_table = [
            {"label": "Relevance", "value": rel_val},
            {"label": "Name Acc", "value": name_val},
            {"label": "Address Acc", "value": addr_val},
            {"label": "Pin Acc", "value": pin_val}
        ]

        processed_results.append({
            "num": res_num,
            "title": title,
            "subtitle": subtitle,
            "meta": meta_table,
            "ratings": ratings_table,
            "upvotes": 0,
            "downvotes": 0
        })

    return headers.get("Task ID", ""), headers.get("Query", ""), headers, processed_results

# --- Routes ---

@app.route('/', methods=['GET', 'POST'])
def home():
    message = ""
    search_results = [] # This will be a list of grouped tasks
    conn = get_db_connection()
    
    if request.method == 'POST':
        # --- 1. SAVE NEW TASK LOGIC (UPDATED FOR VERSIONS) ---
        if 'raw_text' in request.form:
            raw = request.form['raw_text']
            if not raw.strip():
                message = "Error: Text was empty."
            else:
                try:
                    t_id, q, headers, results = process_text(raw)
                    if results:
                        # NEW LOGIC: Check if this specific version exists
                        existing_rows = conn.execute("SELECT rating_results FROM answers WHERE task_id = ?", (t_id,)).fetchall()
                        
                        is_duplicate = False
                        new_results_json = json.dumps(results)
                        
                        # Compare new results with every existing version for this Task ID
                        for row in existing_rows:
                            if row['rating_results'] == new_results_json:
                                is_duplicate = True
                                break
                        
                        if is_duplicate:
                            message = f"Skipped: This exact version already exists for {q}."
                        else:
                            # Insert as NEW row (Do not delete old ones)
                            conn.execute("INSERT INTO answers (task_id, query, header_info, rating_results) VALUES (?, ?, ?, ?)", 
                                         (t_id, q, json.dumps(headers), new_results_json))
                            conn.commit()
                            message = f"Saved: New version added for {q}!"
                    else:
                        message = "Error: Could not parse results."
                except Exception as e:
                    message = f"Parsing Error: {str(e)}"
        
        # --- 2. SEARCH LOGIC ---
        if 'search_query' in request.form:
             q = request.form['search_query'].strip()
             rows = conn.execute("SELECT * FROM answers WHERE task_id LIKE ? OR query LIKE ? ORDER BY id DESC", 
                                ('%'+q+'%', '%'+q+'%')).fetchall()
        else:
             # Just load some recent ones if we just saved something
             rows = conn.execute("SELECT * FROM answers ORDER BY id DESC LIMIT 50").fetchall()

    else:
        # --- 3. DEFAULT LOAD (GET REQUEST) ---
        rows = conn.execute("SELECT * FROM answers ORDER BY id DESC LIMIT 50").fetchall()

    # --- 4. GROUPING LOGIC (The Key Update) ---
    # We transform the flat list of rows into a grouped structure:
    # { 'TaskID_1': { headers:..., versions: [ {db_id:1, results:...}, {db_id:2, results:...} ] } }
    
    tasks_map = OrderedDict()

    for row in rows:
        tid = row['task_id']
        
        if tid not in tasks_map:
            tasks_map[tid] = {
                'task_id': tid,
                'headers': json.loads(row['header_info']),
                'versions': []
            }
        
        tasks_map[tid]['versions'].append({
            'db_id': row['id'],
            'results': json.loads(row['rating_results'])
        })

    search_results = list(tasks_map.values())
            
    conn.close()
    return render_template('home.html', message=message, search_results=search_results)

@app.route('/vote', methods=['POST'])
def vote():
    data = request.json
    db_id = data.get('id')
    res_idx = data.get('idx')
    v_type = data.get('type')

    conn = get_db_connection()
    try:
        row = conn.execute("SELECT rating_results FROM answers WHERE id = ?", (db_id,)).fetchone()
        if row:
            results = json.loads(row['rating_results'])
            if v_type == 'up': results[res_idx]['upvotes'] += 1
            else: results[res_idx]['downvotes'] += 1
            conn.execute("UPDATE answers SET rating_results = ? WHERE id = ?", (json.dumps(results), db_id))
            conn.commit()
            return jsonify({"success": True, "up": results[res_idx]['upvotes'], "down": results[res_idx]['downvotes']})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
    finally:
        conn.close()
    return jsonify({"success": False})

if __name__ == '__main__':
    app.run(debug=True, port=5000)