let currentUserEmail = ""; 
let userSanitizedKey = "";

function initApp(email, sanitizedKey, activeTaskIds) {
    currentUserEmail = email;
    userSanitizedKey = sanitizedKey;

    document.querySelectorAll('.author-btn').forEach(btn => {
        if(btn.dataset.author && btn.dataset.author.toLowerCase() === currentUserEmail) {
            btn.style.display = 'inline-block';
        }
    });

    if (activeTaskIds && activeTaskIds.length > 0) {
        startLiveSync(activeTaskIds);
    }
}

function startLiveSync(taskIds) {
    const dbRef = firebase.database();
    console.log("📡 Live Sync attempting connection...");

    taskIds.forEach(taskId => {
        // We added a specific error callback here to catch Permission Denied errors
        dbRef.ref('tasks/' + taskId).on('value', (snapshot) => {
            const versions = snapshot.val();
            if (!versions) return;

            Object.entries(versions).forEach(([verId, verData]) => {
                const resultsObj = verData.rating_results || {};
                
                Object.keys(resultsObj).forEach(idx => {
                    const res = resultsObj[idx];
                    if(!res) return;

                    // Update Likes
                    const upCount = document.getElementById(`up-${verId}-${idx}`);
                    const downCount = document.getElementById(`down-${verId}-${idx}`);
                    const btnUp = document.getElementById(`btn-up-${verId}-${idx}`);
                    const btnDown = document.getElementById(`btn-down-${verId}-${idx}`);
                    const resContainer = document.getElementById(`res-${verId}-${idx}`);
                    
                    if (upCount) upCount.innerText = res.upvotes || 0;
                    if (downCount) downCount.innerText = res.downvotes || 0;
                    
                    if (btnUp && btnDown) {
                        btnUp.classList.remove('vote-active-up');
                        btnDown.classList.remove('vote-active-down');
                        if (res.voters && res.voters[userSanitizedKey]) {
                            if (res.voters[userSanitizedKey] === 'up') btnUp.classList.add('vote-active-up');
                            else btnDown.classList.add('vote-active-down');
                        }
                    }

                    if (resContainer) {
                        resContainer.classList.toggle('res-caution', (res.downvotes || 0) > (res.upvotes || 0));
                    }

                    // Update Notes (With extreme Null protection)
                    const notesSection = document.getElementById(`notes-${verId}-${idx}`);
                    if (notesSection) {
                        let newHtml = ''; 
                        if (res.notes) {
                            Object.entries(res.notes).forEach(([nid, note]) => {
                                if (!note) return; // Stop a corrupted note from crashing the app
                                
                                const safeUser = note.user || "unknown@user";
                                const username = safeUser.split('@')[0];
                                const isMe = (safeUser.toLowerCase() === currentUserEmail);
                                
                                newHtml += `
                                    <div class="note-item">
                                        <div><span class="note-user">${username}:</span> <span>${note.text}</span></div>
                                        ${isMe ? `<span class="del-note-btn" onclick="deleteNote('${taskId}','${verId}',${idx},'${nid}')" title="Delete">×</span>` : ''}
                                    </div>`;
                            });
                        }
                        notesSection.innerHTML = newHtml;
                    }
                });
            });
        }, (error) => {
            // IF FIREBASE RULES BLOCK YOU, THIS WILL PRINT IN RED
            console.error("🔥 FIREBASE BLOCKED LIVE SYNC:", error);
        });
    });
}

function vote(t, v, i, type) { 
    fetch('/vote', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task_id:t, ver_id:v, idx:i, type:type, user_email:currentUserEmail}) }); 
}

function deleteNote(t, v, i, nid) {
    if(!confirm("Delete this comment?")) return;
    fetch('/delete_note', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task_id:t, ver_id:v, idx:i, note_id:nid, user_email:currentUserEmail}) })
    .then(r => r.json()).then(d => { 
        if(!d.success) alert(d.error); 
        // We removed the location.reload() here! Live Sync handles it instantly.
    });
}

function saveNote(t, v, i) {
    const inp = document.getElementById(`txt-note-${v}-${i}`);
    if(!inp.value.trim()) return;
    
    const noteText = inp.value;
    inp.value = ''; // Instantly clears the box
    
    fetch('/add_note', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task_id:t, ver_id:v, idx:i, note_text:noteText, user_email:currentUserEmail}) });
    // We removed the setTimeout reload here! Live Sync will just pop the note onto the screen.
}

function openTab(evt, tabId, group) {
    document.querySelectorAll(`.group-${group}`).forEach(c => c.classList.remove("active"));
    evt.currentTarget.parentElement.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.getElementById(tabId).classList.add("active");
    evt.currentTarget.classList.add("active");
}

function toggleNoteBox(v, i) { 
    const b = document.getElementById(`note-input-${v}-${i}`); 
    if(b) b.style.display = (b.style.display === 'none' || b.style.display === '') ? 'flex' : 'none'; 
}

function enableEdit(taskId, verId, idx) {
    const container = document.getElementById(`ratings-${verId}-${idx}`);
    if(!container || container.classList.contains('edit-mode')) return;
    container.classList.add('edit-mode');
    container.querySelectorAll('.rating-row').forEach(row => {
        const valSpan = row.querySelector('.r-val');
        const labelText = row.children[0].innerText;
        row.innerHTML = `<span class="r-label">${labelText}</span> <input type="text" value="${valSpan.innerText}" class="edit-input">`;
    });
    const b = document.createElement('div');
    b.innerHTML = `<button class="btn-mini" style="background:#22c55e; color:white; width:100%; margin-top:8px;" onclick="saveEdit('${taskId}','${verId}',${idx})">Save Changes</button>`;
    container.appendChild(b);
}

function saveEdit(t, v, i) {
    const nr = [];
    document.getElementById(`ratings-${v}-${i}`).querySelectorAll('.rating-row').forEach(row => {
        nr.push({ label: row.querySelector('.r-label').innerText, value: row.querySelector('.edit-input').value });
    });
    fetch('/edit_result', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task_id:t, ver_id:v, idx:i, new_ratings:nr, user_email:currentUserEmail}) }).then(() => location.reload());
}