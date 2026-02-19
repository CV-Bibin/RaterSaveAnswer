import os
import json
import re
from flask import Flask, render_template, request, jsonify
import firebase_admin
from firebase_admin import credentials, db
from dotenv import load_dotenv
from collections import OrderedDict

# 1. LOAD ENVIRONMENT VARIABLES
load_dotenv() 

# 2. CREATE THE FLASK APP
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "secure_rater_key_2026")

# 3. INITIALIZE FIREBASE
def initialize_firebase():
    if not firebase_admin._apps:
        cred_json = os.getenv('FIREBASE_SERVICE_ACCOUNT')
        db_url = os.getenv('DATABASE_URL')
        
        if cred_json:
            cred_dict = json.loads(cred_json)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred, {'databaseURL': db_url})
            print("Firebase Realtime DB connected.")

initialize_firebase()

# --- Helper Functions ---

def clean_value(val):
    if not val: return "-"
    if val.strip().lower() == "n/a": return "n/a"
    
    garbage_headers = [
        "Category", "Type", 
        "Street Number", "Unit/Apt", "Street Name", "Sub-Locality", 
        "Locality", "Region/State", "Postal Code", "Country", 
        "Address does not exist", "Language/Script issue", "Other Issue",
        "Name Issue", "Category Issue", "User intent issue", "Distance/Prominence issue"
    ]
    
    if val.strip() in garbage_headers: return "-"
    return val.strip()

def is_valid_meta(val):
    if not val: return False
    garbage_starts = [
        "Result name", "Business/POI", "Relevance", "Type", "Category", "Status",
        "Distance to User", "Distance to Viewport", "Lat, Lng"
    ]
    if any(val.startswith(k) for k in garbage_starts): return False
    return True

def process_text(text):
    headers = {}
    def get_header(key):
        m = re.search(rf'{key}\s*\n(.+)', text)
        return m.group(1).strip() if m else "Unknown"

    headers["Task ID"] = get_header("Task ID")
    headers["Task Type"] = get_header("Task Type")
    headers["Viewport Age"] = get_header("Viewport Age")
    headers["Locale"] = get_header("Locale")
    headers["Lat, Lng"] = get_header("Lat, Lng")
    
    q_match = re.search(r'Query\s*\n\s*\n(.+)', text)
    if not q_match: q_match = re.search(r'Query\s*\n(.+)', text)
    headers["Query"] = q_match.group(1).strip() if q_match else "Unknown Query"

    raw_results = re.split(r'\n(\d+)\.\s*\n', text)
    processed_results = []
    
    system_keywords = ["Category", "Type", "Status", "Distance", "Relevance", "Lat, Lng", "Result name"]

    for i in range(1, len(raw_results), 2):
        res_num = raw_results[i]
        content = raw_results[i+1].strip()
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        title = lines[0] if lines else ""
        subtitle = ""
        if len(lines) > 1:
            candidate = lines[1]
            if not any(candidate.startswith(k) for k in system_keywords):
                subtitle = candidate

        meta_table = []
        target_keys = ["Category", "Type", "Distance to User", "Distance to Viewport", "Lat, Lng"]
        for key in target_keys:
            m = re.search(rf'{key}\s*\n(.+)', content)
            if m:
                val = m.group(1).strip()
                if is_valid_meta(val):
                     if not any(d['value'] == val for d in meta_table):
                        meta_table.append({"label": key, "value": val})

        def get_val(pattern):
            m = re.search(pattern, content, re.IGNORECASE)
            return clean_value(m.group(1).strip()) if m else "-"

        rel_m = re.search(r'Relevance\s*\n(.+)', content)
        rel_val = rel_m.group(1).strip() if rel_m else "Not Rated"

        ratings_table = [
            {"label": "Relevance", "value": rel_val},
            {"label": "Name Acc", "value": get_val(r'Name Accuracy\s*\n(.+)')},
            {"label": "Address Acc", "value": get_val(r'Address Accuracy\s*\n(.+)')},
            {"label": "Pin Acc", "value": get_val(r'(?:Pin Accuracy|Pin/Zip Accuracy)\s*\n(.+)')}
        ]

        processed_results.append({
            "num": res_num,
            "title": title,
            "subtitle": subtitle,
            "meta": meta_table,
            "ratings": ratings_table,
            "upvotes": 0,
            "downvotes": 0,
            "voters": {},
            "notes": {} # New: Storage for comments
        })

    return headers.get("Task ID", ""), headers.get("Query", ""), headers, processed_results

# --- ROUTES ---

def get_fb_config():
    return {
        "apiKey": os.getenv("FIREBASE_API_KEY"),
        "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
        "projectId": os.getenv("FIREBASE_PROJECT_ID"),
        "databaseURL": os.getenv("DATABASE_URL"),
        "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
        "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
        "appId": os.getenv("FIREBASE_APP_ID")
    }

@app.route('/login')
def login_page():
    return render_template('login.html', firebase_config=get_fb_config())

@app.route('/', methods=['GET', 'POST'])
def home():
    message = ""
    current_user_email = request.args.get('u', "").strip().lower()
    sanitized_user = current_user_email.replace('.', ',')

    if request.method == 'POST' and 'raw_text' in request.form:
        user_email = request.form.get('user_email', "").strip().lower()
        if not user_email:
             message = "⚠️ Error: You must be logged in to save tasks."
        else:
            try:
                t_id, q, headers, results = process_text(request.form['raw_text'])
                if results:
                    ref = db.reference('tasks')
                    task_ref = ref.child(t_id)
                    snapshot = task_ref.get()
                    
                    is_duplicate = False
                    new_results_json = json.dumps(results)
                    
                    if snapshot:
                        for key, val in snapshot.items():
                            if json.dumps(val.get('rating_results')) == new_results_json:
                                is_duplicate = True
                                break
                    
                    task_ref.push().set({
                        'task_id': t_id, 'query': q, 'header_info': headers,
                        'rating_results': results, 'submitted_by': user_email,
                        'timestamp': {'.sv': 'timestamp'}
                    })
                    message = "✅ Version saved successfully!"
            except Exception as e:
                message = f"Error: {str(e)}"

    ref = db.reference('tasks')
    snapshot = ref.order_by_key().limit_to_last(40).get()
    
    all_tasks = []
    if snapshot:
        for tid, versions in reversed(list(snapshot.items())):
            v_list = []
            h_data, q_str = {}, ""
            for vid, vdata in versions.items():
                h_data, q_str = vdata.get('header_info', {}), vdata.get('query', "")
                v_list.append({
                    'db_id': vid, 
                    'results': vdata.get('rating_results', []),
                    'author': vdata.get('submitted_by'),
                    'voters': vdata.get('voters', {}),
                    'notes': vdata.get('notes', {}) # Pass notes to template
                })
            all_tasks.append({'task_id': tid, 'headers': h_data, 'query': q_str, 'versions': v_list})

    search_results = all_tasks
    if request.method == 'POST' and 'search_query' in request.form:
        sq = request.form['search_query'].strip().lower()
        search_results = [t for t in all_tasks if sq in t['task_id'].lower() or sq in t['query'].lower()]

    return render_template('home.html', search_results=search_results, message=message, firebase_config=get_fb_config(), sanitized_user=sanitized_user)

# --- NEW: EDIT RESULT (AUTHOR ONLY) ---
@app.route('/edit_result', methods=['POST'])
def edit_result():
    data = request.json
    # Path to the specific result's ratings list
    path = f"tasks/{data['task_id']}/{data['ver_id']}/rating_results/{data['idx']}/ratings"
    ver_ref = db.reference(f"tasks/{data['task_id']}/{data['ver_id']}")
    
    # Check if user is the author
    if ver_ref.get().get('submitted_by') == data['user_email']:
        # Update the ratings list with new data
        db.reference(path).set(data['new_ratings'])
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Unauthorized"})

# --- NEW: ADD NOTE (ALL USERS) ---
@app.route('/add_note', methods=['POST'])
def add_note():
    data = request.json
    if not data.get('note_text'): return jsonify({"success": False})
    
    # Path to notes for this specific result
    path = f"tasks/{data['task_id']}/{data['ver_id']}/rating_results/{data['idx']}/notes"
    
    note_obj = {
        "user": data['user_email'],
        "text": data['note_text'],
        "timestamp": {'.sv': 'timestamp'}
    }
    
    db.reference(path).push().set(note_obj)
    return jsonify({"success": True})


# --- NEW: DELETE NOTE ---
@app.route('/delete_note', methods=['POST'])
def delete_note():
    data = request.json
    # Path: tasks/{tid}/{vid}/rating_results/{idx}/notes/{note_id}
    path = f"tasks/{data['task_id']}/{data['ver_id']}/rating_results/{data['idx']}/notes/{data['note_id']}"
    
    note_ref = db.reference(path)
    note_data = note_ref.get()
    
    # Check if the user trying to delete is the one who wrote it
    if note_data and note_data.get('user') == data['user_email']:
        note_ref.delete()
        return jsonify({"success": True})
    
    return jsonify({"success": False, "error": "Unauthorized: You can only delete your own notes."})



@app.route('/vote', methods=['POST'])
def vote():
    data = request.json
    task_id = data.get('task_id')
    ver_id = data.get('ver_id')
    res_idx = data.get('idx')
    vote_type = data.get('type')
    user_email = data.get('user_email')

    if not user_email: return jsonify({"success": False, "error": "Login required"})

    user_key = user_email.replace('.', ',')
    path = f"tasks/{task_id}/{ver_id}/rating_results/{res_idx}"
    item_ref = db.reference(path)

    def toggle_vote(current_data):
        if not current_data: return current_data
        
        if 'upvotes' not in current_data: current_data['upvotes'] = 0
        if 'downvotes' not in current_data: current_data['downvotes'] = 0
        if 'voters' not in current_data: current_data['voters'] = {}
        
        voters = current_data['voters']
        previous_vote = voters.get(user_key)

        if previous_vote == vote_type:
            if vote_type == 'up': current_data['upvotes'] -= 1
            else: current_data['downvotes'] -= 1
            del voters[user_key]
        else:
            if previous_vote == 'up': current_data['upvotes'] -= 1
            elif previous_vote == 'down': current_data['downvotes'] -= 1
            
            if vote_type == 'up': current_data['upvotes'] += 1
            else: current_data['downvotes'] += 1
            voters[user_key] = vote_type

        current_data['voters'] = voters
        return current_data

    try:
        updated = item_ref.transaction(toggle_vote)
        new_status = updated.get('voters', {}).get(user_key, None)
        return jsonify({
            "success": True, "up": updated['upvotes'], "down": updated['downvotes'], "user_status": new_status
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    app.run(debug=True, port=5000)