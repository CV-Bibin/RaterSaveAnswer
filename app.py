import sqlite3
import re
import json
from flask import Flask, render_template, request

app = Flask(__name__)

def get_db_connection():
    conn = sqlite3.connect('tasks.db', timeout=20)
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

def process_text(text):
    # --- 1. Header Extraction ---
    patterns = {
        "Task Type": r'Task Type\s*\n(.+)',
        "Task ID": r'Task ID\s*\n(.+)',
        "Estimated Rating Time": r'Estimated Rating Time\s*\n(.+)',
        "Lat, Lng": r'Lat, Lng\s*\n(\d+\.\d+,\s*\d+\.\d+\s*:\s*\d+)',
        "Query": r'Query\s*\n\s*\n(.+)',
        "Viewport Age": r'Viewport Age\s*\n(.+)',
        "Locale": r'Locale\s*\n(.+)',
        "Country": r'Country\s*\n(.+)'
    }
    header_data = {k: (re.search(v, text).group(1).strip() if re.search(v, text) else "") for k, v in patterns.items()}

    # --- 2. Result Extraction ---
    sections = re.split(r'\n(\d+)\.\s*\n', text)
    processed_results = []
    
    # These are the 4 mandatory rows you want to see
    required_ratings = ["Relevance", "Name Accuracy", "Address Accuracy", "Pin Accuracy"]

    for i in range(1, len(sections), 2):
        res_num = sections[i]
        res_content = sections[i+1].strip()
        
        # Trigger: We only process the result if "Relevance" has a valid rating word
        rel_match = re.search(r'Relevance\s*\n\s*(Navigational|Excellent|Good|Acceptable|Bad)', res_content, re.IGNORECASE)
        
        if rel_match:
            lines = res_content.split('\n')
            title = lines[0].strip() if len(lines) > 0 else ""
            subtitle = lines[1].strip() if len(lines) > 1 else ""
            
            # --- Info Table (Category, Distance, etc.) ---
            meta_table = []
            for key in ["Category", "Type", "Status", "Distance to User", "Distance to Viewport", "Lat, Lng"]:
                m = re.search(rf'{key}\s*\n(.+)', res_content)
                if m: meta_table.append({"label": key, "value": m.group(1).strip()})

            # --- Forced 4-Row Logic (Blank vs NA) ---
            ratings_table = []
            for key in required_ratings:
                # Special handle for Pin Accuracy vs Pin/Zip Accuracy
                if key == "Pin Accuracy":
                    m = re.search(r'(Pin Accuracy|Pin/Zip Accuracy)\s*\n(.+)', res_content)
                    val = m.group(2).strip() if m else ""
                else:
                    m = re.search(rf'{key}\s*\n(.+)', res_content)
                    val = m.group(1).strip() if m else ""
                
                # If the value found is just another field name (regex error), make it empty
                if val in ["Name Accuracy", "Address Accuracy", "Pin Accuracy", "Pin/Zip Accuracy", "Comment and Link"]:
                    val = ""

                ratings_table.append({"label": key, "value": val})

            processed_results.append({
                "num": res_num,
                "title": title,
                "subtitle": subtitle,
                "meta": meta_table,
                "ratings": ratings_table
            })

    return header_data.get("Task ID", ""), header_data.get("Query", ""), header_data, processed_results

@app.route('/', methods=['GET', 'POST'])
def home():
    message = ""
    search_results = []
    if request.method == 'POST':
        if 'search_query' in request.form:
            q = request.form['search_query'].strip()
            conn = get_db_connection()
            rows = conn.execute("SELECT * FROM answers WHERE task_id LIKE ? OR query LIKE ?", ('%'+q+'%', '%'+q+'%')).fetchall()
            for row in rows:
                search_results.append({"headers": json.loads(row['header_info']), "results": json.loads(row['rating_results'])})
            conn.close()
        elif 'raw_text' in request.form:
            t_id, q, headers, results = process_text(request.form['raw_text'])
            if results:
                conn = get_db_connection()
                conn.execute("DELETE FROM answers WHERE task_id = ?", (t_id,))
                conn.execute("INSERT INTO answers (task_id, query, header_info, rating_results) VALUES (?, ?, ?, ?)", 
                             (t_id, q, json.dumps(headers), json.dumps(results)))
                conn.commit()
                conn.close()
                message = f"Task {t_id} Saved!"
            else:
                message = "Could not find a rated answer. Please try again!"
    return render_template('home.html', message=message, search_results=search_results)

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)