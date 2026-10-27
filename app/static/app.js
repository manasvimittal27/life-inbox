let isSavingNote = false;
let isAnalyzingInbox = false;
let lastTasks = [];
let editingTaskId = null;
const pendingNoteActions = new Set();
const pendingTaskActions = new Set();
const itemFilterFields = {
  q: 'itemSearch',
  item_type: 'itemTypeFilter',
  category: 'categoryFilter',
  priority: 'priorityFilter',
  processing_status: 'processingStatusFilter'
};

function setStatus(elementId, message, isError = false) {
  const element = document.getElementById(elementId);
  element.textContent = message;
  element.classList.toggle('error', isError);
}

function setButtonBusy(buttonId, busy, busyLabel, idleLabel) {
  const button = document.getElementById(buttonId);
  button.disabled = busy;
  button.textContent = busy ? busyLabel : idleLabel;
  button.setAttribute('aria-busy', String(busy));
}

function itemFilterParams() {
  const params = new URLSearchParams();
  for (const [name, elementId] of Object.entries(itemFilterFields)) {
    const value = document.getElementById(elementId).value.trim();
    if (value) params.set(name, value);
  }
  return params;
}

function hasItemFilters() {
  return itemFilterParams().size > 0;
}

function updateUrl(updates) {
  const url = new URL(window.location.href);
  for (const [name, value] of Object.entries(updates)) {
    if (value) {
      url.searchParams.set(name, value);
    } else {
      url.searchParams.delete(name);
    }
  }
  window.history.pushState({}, '', url);
}

function syncControlsFromUrl() {
  const params = new URLSearchParams(window.location.search);
  for (const [name, elementId] of Object.entries(itemFilterFields)) {
    const element = document.getElementById(elementId);
    const value = params.get(name) || '';
    if (
      name === 'category'
      && value
      && !Array.from(element.options).some(option => option.value === value)
    ) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      element.appendChild(option);
    }
    element.value = value;
  }

  const taskStatus = params.get('task_status');
  document.getElementById('taskStatusFilter').value =
    ['open', 'completed', 'dismissed'].includes(taskStatus)
      ? taskStatus
      : 'open';
}

function populateCategoryFilter(data) {
  const select = document.getElementById('categoryFilter');
  const selected =
    new URLSearchParams(window.location.search).get('category')
    || select.value;
  const categories = Array.from(
    new Set((data.buckets || []).map(bucket => bucket.name))
  ).sort((first, second) => first.localeCompare(second));

  select.replaceChildren();
  const allOption = document.createElement('option');
  allOption.value = '';
  allOption.textContent = 'All';
  select.appendChild(allOption);

  if (selected && !categories.includes(selected)) {
    categories.push(selected);
    categories.sort((first, second) => first.localeCompare(second));
  }
  for (const category of categories) {
    const option = document.createElement('option');
    option.value = category;
    option.textContent = category;
    select.appendChild(option);
  }
  select.value = selected;
}

function applyItemFilters(event) {
  event.preventDefault();
  const values = {};
  const params = itemFilterParams();
  for (const name of Object.keys(itemFilterFields)) {
    values[name] = params.get(name) || '';
  }
  updateUrl(values);
  loadNotes();
}

function clearItemFilters() {
  for (const elementId of Object.values(itemFilterFields)) {
    document.getElementById(elementId).value = '';
  }
  updateUrl(
    Object.fromEntries(
      Object.keys(itemFilterFields).map(name => [name, ''])
    )
  );
  loadNotes();
}

function changeTaskStatusFilter() {
  const status = document.getElementById('taskStatusFilter').value;
  updateUrl({task_status: status});
  loadTasks();
}

function changeTaskView() {
  renderTaskView();
}

async function fetchJson(url, options, friendlyError) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error(friendlyError);
  }

  if (!response.ok) {
    throw new Error(friendlyError);
  }

  if (response.status === 204) {
    return null;
  }

  try {
    return await response.json();
  } catch {
    throw new Error(friendlyError);
  }
}

async function addNote() {
  if (isSavingNote) return;

  const textarea = document.getElementById('noteText');
  const text = textarea.value.trim();
  if (!text) {
    setStatus('noteStatus', 'Write a note before saving.', true);
    textarea.focus();
    return;
  }

  isSavingNote = true;
  setButtonBusy('addNoteButton', true, 'Saving…', 'Add Note');
  setStatus('noteStatus', 'Saving your note…');

  try {
    await fetchJson(
      '/api/items',
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({raw_text: text})
      },
      'Unable to save note.'
    );

    if (textarea.value.trim() === text) {
      textarea.value = '';
    }
    setStatus('noteStatus', 'Note saved.');
    await loadNotes(
      'Your note was saved, but the list could not refresh. Reload to try again.'
    );
  } catch {
    setStatus(
      'noteStatus',
      'We could not save your note. Your text is still here—please try again.',
      true
    );
  } finally {
    isSavingNote = false;
    setButtonBusy('addNoteButton', false, 'Saving…', 'Add Note');
  }
}

function esc(str) {
  return String(str ?? '').replace(/[&<>'"]/g, tag => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;'
  }[tag]));
}

function actionButton(label, className, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.className = className;
  button.addEventListener('click', onClick);
  return button;
}

function renderNote(note) {
  const root = document.createElement('div');
  root.className = 'note';
  root.dataset.noteId = String(note.id);

  const text = document.createElement('div');
  text.className = 'note-text';
  text.textContent = note.raw_text;

  const time = document.createElement('div');
  time.className = 'time';
  time.textContent = `#${note.id} · ${note.created_at}`;

  const actions = document.createElement('div');
  actions.className = 'note-actions';

  const editButton = actionButton('Edit', 'secondary', () => {
    showEditNote(note, root);
  });
  const deleteButton = actionButton('Delete', 'danger', () => {
    deleteExistingNote(note, root, editButton, deleteButton);
  });

  actions.append(editButton, deleteButton);
  root.append(text, time, actions);
  return root;
}

function showEditNote(note, root) {
  if (pendingNoteActions.has(note.id)) return;

  const textarea = document.createElement('textarea');
  textarea.className = 'note-edit-input';
  textarea.maxLength = 2000;
  textarea.value = note.raw_text;

  const actions = document.createElement('div');
  actions.className = 'note-actions';

  const saveButton = actionButton('Save', '', async () => {
    const text = textarea.value.trim();
    if (!text) {
      setStatus('noteStatus', 'Edited note text cannot be empty.', true);
      textarea.focus();
      return;
    }
    if (pendingNoteActions.has(note.id)) return;

    pendingNoteActions.add(note.id);
    saveButton.disabled = true;
    cancelButton.disabled = true;
    saveButton.textContent = 'Saving…';
    setStatus('noteStatus', 'Saving your changes…');

    try {
      const updatedNote = await fetchJson(
        `/api/items/${note.id}`,
        {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({raw_text: text})
        },
        'Unable to update note.'
      );
      root.replaceWith(renderNote(updatedNote));
      setStatus('noteStatus', 'Note updated.');
    } catch {
      setStatus(
        'noteStatus',
        'We could not update this note. Your changes are still here—please try again.',
        true
      );
      saveButton.disabled = false;
      cancelButton.disabled = false;
      saveButton.textContent = 'Save';
    } finally {
      pendingNoteActions.delete(note.id);
    }
  });

  const cancelButton = actionButton('Cancel', 'secondary', () => {
    if (pendingNoteActions.has(note.id)) return;
    root.replaceWith(renderNote(note));
    setStatus('noteStatus', 'Edit cancelled.');
  });

  actions.append(saveButton, cancelButton);
  root.replaceChildren(textarea, actions);
  textarea.focus();
}

async function deleteExistingNote(note, root, editButton, deleteButton) {
  if (pendingNoteActions.has(note.id)) return;

  const confirmed = window.confirm(
    'Delete this note? This action cannot be undone.'
  );
  if (!confirmed) return;

  pendingNoteActions.add(note.id);
  editButton.disabled = true;
  deleteButton.disabled = true;
  deleteButton.textContent = 'Deleting…';
  setStatus('noteStatus', 'Deleting note…');

  try {
    await fetchJson(
      `/api/items/${note.id}`,
      {method: 'DELETE'},
      'Unable to delete note.'
    );
    root.remove();
    setStatus('noteStatus', 'Note deleted.');
  } catch {
    setStatus(
      'noteStatus',
      'We could not delete this note. It is still here—please try again.',
      true
    );
    editButton.disabled = false;
    deleteButton.disabled = false;
    deleteButton.textContent = 'Delete';
  } finally {
    pendingNoteActions.delete(note.id);
  }
}

function formatDueDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  if (!match) return iso || '';
  const d = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return d.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short'
  });
}

function scheduleLabel(item) {
  if (item.task_type === 'recurring' || item.recurrence) {
    return `🔁 ${item.recurrence || 'recurring'}`;
  }
  if (item.due_date) return formatDueDate(item.due_date);
  if (item.when) return item.when;
  if (item.timing_text) return item.timing_text;
  return '';
}

function bucketPillHtml(item) {
  if (!item.bucket) {
    return `<span class="pill">${esc(item.priority || '')}</span>`;
  }
  const star = item.importance === 'high' ? ' ★' : '';
  return `<span class="pill bucket-${esc(item.bucket)}">${esc(item.bucket)}${star}</span>`;
}

function bucketPillNode(item) {
  const span = document.createElement('span');
  if (!item.bucket) {
    span.className = 'pill';
    span.textContent = item.priority || '';
    return span;
  }
  span.className = `pill bucket-${item.bucket}`;
  span.textContent = item.importance === 'high'
    ? `${item.bucket} ★`
    : item.bucket;
  return span;
}

function renderTask(task) {
  const root = document.createElement('div');
  root.className = 'task';
  root.dataset.taskId = String(task.id);

  const text = document.createElement('strong');
  text.textContent = task.text;

  const priority = bucketPillNode(task);

  const content = document.createElement('div');
  content.append(text, priority);

  const meta = document.createElement('div');
  meta.className = 'task-meta';
  const sched = scheduleLabel(task);
  const schedText = sched ? ` · ${sched}` : '';
  const effort = task.effort_min ? ` · ${task.effort_min}m` : '';
  const sources = task.source_item_ids
    .map(itemId => `#${itemId}`)
    .join(', ');
  meta.textContent = `${task.status}${schedText}${effort} · Sources: ${sources}`;

  const actions = document.createElement('div');
  actions.className = 'task-actions';

  actions.append(
    actionButton('Edit', 'secondary', () => {
      openTaskModal(task.id);
    })
  );
  if (task.status === 'open') {
    actions.append(
      actionButton('Complete', '', () => {
        transitionTask(task.id, 'complete', actions);
      }),
      actionButton('Dismiss', 'secondary', () => {
        transitionTask(task.id, 'dismiss', actions);
      })
    );
  } else {
    actions.append(
      actionButton('Reopen', '', () => {
        transitionTask(task.id, 'reopen', actions);
      })
    );
  }
  actions.append(
    actionButton('Delete', 'danger', () => {
      deleteTask(task.id);
    })
  );

  root.append(content, meta, actions);
  return root;
}

async function transitionTask(taskId, action, actions) {
  if (pendingTaskActions.has(taskId)) return;

  pendingTaskActions.add(taskId);
  const buttons = actions.querySelectorAll('button');
  buttons.forEach(button => {
    button.disabled = true;
  });
  setStatus('taskStatus', 'Updating task…');

  try {
    await fetchJson(
      `/api/tasks/${taskId}/${action}`,
      {method: 'POST'},
      'Unable to update task.'
    );
    const messages = {
      complete: 'Task completed.',
      dismiss: 'Task dismissed.',
      reopen: 'Task reopened.'
    };
    setStatus('taskStatus', messages[action]);
    await loadTasks();
  } catch {
    setStatus(
      'taskStatus',
      'We could not update this task. Please try again.',
      true
    );
    buttons.forEach(button => {
      button.disabled = false;
    });
  } finally {
    pendingTaskActions.delete(taskId);
  }
}

async function loadTasks() {
  const filter = document.getElementById('taskStatusFilter').value;
  try {
    const tasks = await fetchJson(
      `/api/tasks?status=${encodeURIComponent(filter)}`,
      undefined,
      'Unable to load tasks.'
    );
    lastTasks = tasks;
    renderTaskView();
    return true;
  } catch {
    setStatus(
      'taskStatus',
      'We could not load tasks. Reload to try again.',
      true
    );
    return false;
  }
}

function renderTaskView() {
  const mode = document.getElementById('taskViewMode').value;
  const weekRoot = document.getElementById('weekView');
  const listRoot = document.getElementById('tasks');
  if (mode === 'week') {
    listRoot.style.display = 'none';
    weekRoot.style.display = '';
    renderWeek(lastTasks);
  } else {
    weekRoot.style.display = 'none';
    weekRoot.innerHTML = '';
    listRoot.style.display = '';
    renderList(lastTasks);
  }
}

function renderList(tasks) {
  const root = document.getElementById('tasks');
  root.innerHTML = '';
  if (!tasks.length) {
    root.innerHTML = '<div class="panel muted">No tasks in this view.</div>';
    return;
  }
  for (const task of tasks) {
    root.appendChild(renderTask(task));
  }
}

// --- Week view ---------------------------------------------------------------

const BUCKET_ORDER = {P0: 0, P1: 1, P2: 2};

function startOfWeek(d) {
  const day = d.getDay();            // 0 Sun .. 6 Sat
  const diff = day === 0 ? -6 : 1 - day;  // back to Monday
  const monday = new Date(d);
  monday.setDate(d.getDate() + diff);
  monday.setHours(0, 0, 0, 0);
  return monday;
}

function localIso(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function compareTasks(a, b) {
  const ba = BUCKET_ORDER[a.bucket] ?? 3;
  const bb = BUCKET_ORDER[b.bucket] ?? 3;
  if (ba !== bb) return ba - bb;
  const ia = a.importance === 'high' ? 0 : 1;
  const ib = b.importance === 'high' ? 0 : 1;
  if (ia !== ib) return ia - ib;
  return a.id - b.id;
}

function weekTaskHtml(task, showDate) {
  let sched = '';
  if (showDate) {
    sched = scheduleLabel(task);
  } else if (task.task_type === 'recurring') {
    sched = `🔁 ${task.recurrence || 'recurring'}`;
  }
  const schedText = sched ? ` · ${esc(sched)}` : '';
  const effort = task.effort_min ? ` · ${task.effort_min}m` : '';
  const doneClass = task.status === 'completed' ? ' week-task-done' : '';
  return `<div class="week-task${doneClass}">${bucketPillHtml(task)}` +
    `<span class="week-task-text">${esc(task.text)}</span>` +
    `<span class="week-task-meta">${schedText}${effort}</span>` +
    taskHoverActionsHtml(task.id) +
    `</div>`;
}

function taskHoverActionsHtml(taskId) {
  return `<span class="task-hover-actions">` +
    `<button type="button" title="Edit" aria-label="Edit task" onclick="openTaskModal(${taskId})">✎</button>` +
    `<button type="button" title="Complete" aria-label="Complete task" onclick="completeTask(${taskId})">✓</button>` +
    `<button type="button" title="Delete" aria-label="Delete task" onclick="deleteTask(${taskId})">🗑</button>` +
    `</span>`;
}

function renderWeek(tasks) {
  const root = document.getElementById('weekView');

  if (!tasks.length) {
    root.innerHTML = '<div class="panel muted">No tasks in this view.</div>';
    return;
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayIso = localIso(today);
  const monday = startOfWeek(today);
  const days = [];
  for (let i = 0; i < 7; i += 1) {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    days.push(d);
  }
  const weekIsos = days.map(localIso);

  const recurring = tasks.filter(t => t.task_type === 'recurring');
  const dated = tasks.filter(
    t => t.task_type !== 'recurring'
      && t.due_date
      && weekIsos.includes(t.due_date)
  );
  const backlog = tasks.filter(
    t => t.task_type !== 'recurring'
      && (!t.due_date || !weekIsos.includes(t.due_date))
  );

  const dayCards = days.map((d, idx) => {
    const iso = weekIsos[idx];
    const dayTasks = [
      ...recurring,
      ...dated.filter(t => t.due_date === iso)
    ].sort(compareTasks);

    const label = d.toLocaleDateString(undefined, {weekday: 'short'});
    const dateLabel = d.toLocaleDateString(undefined, {
      day: 'numeric',
      month: 'short'
    });
    const isToday = iso === todayIso;
    const body = dayTasks.length
      ? dayTasks.map(t => weekTaskHtml(t, false)).join('')
      : '<div class="muted week-empty">—</div>';

    return `
      <div class="day-card${isToday ? ' day-today' : ''}">
        <div class="day-head">
          <span class="day-name">${esc(label)}</span>
          <span class="day-date">${esc(dateLabel)}</span>
        </div>
        ${body}
      </div>
    `;
  }).join('');

  let backlogHtml = '';
  if (backlog.length) {
    const sorted = [...backlog].sort(compareTasks);
    backlogHtml = `
      <div class="panel backlog">
        <h3>Backlog &amp; undated (${backlog.length})</h3>
        ${sorted.map(t => {
          const overdue = t.due_date && t.due_date < todayIso
            ? ' <span class="overdue">overdue</span>'
            : '';
          return weekTaskHtml(t, true).replace(
            '</div>',
            `${overdue}</div>`
          );
        }).join('')}
      </div>
    `;
  }

  root.innerHTML = `<div class="week-grid">${dayCards}</div>${backlogHtml}`;
}

// --- Task actions & edit modal ----------------------------------------------

async function completeTask(taskId) {
  if (pendingTaskActions.has(taskId)) return;
  pendingTaskActions.add(taskId);
  setStatus('taskStatus', 'Updating task…');
  try {
    await fetchJson(
      `/api/tasks/${taskId}/complete`,
      {method: 'POST'},
      'Unable to complete task.'
    );
    setStatus('taskStatus', 'Task completed.');
    await loadTasks();
  } catch {
    setStatus('taskStatus', 'We could not complete this task.', true);
  } finally {
    pendingTaskActions.delete(taskId);
  }
}

async function deleteTask(taskId) {
  if (!window.confirm('Delete this task? This cannot be undone.')) return;
  if (pendingTaskActions.has(taskId)) return;
  pendingTaskActions.add(taskId);
  setStatus('taskStatus', 'Deleting task…');
  try {
    await fetchJson(
      `/api/tasks/${taskId}`,
      {method: 'DELETE'},
      'Unable to delete task.'
    );
    setStatus('taskStatus', 'Task deleted.');
    await loadTasks();
  } catch {
    setStatus('taskStatus', 'We could not delete this task.', true);
  } finally {
    pendingTaskActions.delete(taskId);
  }
}

function onEditTypeChange() {
  const type = document.getElementById('editType').value;
  const recurring = type === 'recurring';
  document.getElementById('editDueWrap').style.display = recurring ? 'none' : '';
  document.getElementById('editRecurrenceWrap').style.display =
    recurring ? '' : 'none';
}

function openTaskModal(taskId) {
  const task = lastTasks.find(t => t.id === taskId);
  if (!task) return;
  editingTaskId = taskId;
  document.getElementById('editText').value = task.text || '';
  document.getElementById('editBucket').value = task.bucket || 'P1';
  document.getElementById('editType').value = task.task_type || 'one_off';
  document.getElementById('editDue').value = task.due_date || '';
  document.getElementById('editRecurrence').value = task.recurrence || 'daily';
  document.getElementById('editImportance').checked = task.importance === 'high';
  document.getElementById('editEffort').value =
    task.effort_min != null ? task.effort_min : '';
  onEditTypeChange();
  setStatus('editStatus', '');
  document.getElementById('taskModalBackdrop').hidden = false;
}

function closeTaskModal() {
  document.getElementById('taskModalBackdrop').hidden = true;
  editingTaskId = null;
}

function onModalBackdropClick(event) {
  if (event.target.id === 'taskModalBackdrop') closeTaskModal();
}

async function saveTaskEdit() {
  if (editingTaskId == null) return;
  const text = document.getElementById('editText').value.trim();
  if (!text) {
    setStatus('editStatus', 'Task text is required.', true);
    return;
  }
  const type = document.getElementById('editType').value;
  const payload = {
    text,
    bucket: document.getElementById('editBucket').value,
    task_type: type,
    importance: document.getElementById('editImportance').checked
      ? 'high'
      : 'normal'
  };
  if (type === 'recurring') {
    payload.recurrence = document.getElementById('editRecurrence').value;
    payload.due_date = null;
  } else {
    payload.recurrence = null;
    payload.due_date = document.getElementById('editDue').value || null;
  }
  const effortRaw = document.getElementById('editEffort').value;
  if (effortRaw !== '') payload.effort_min = Number(effortRaw);

  try {
    await fetchJson(
      `/api/tasks/${editingTaskId}`,
      {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      },
      'Unable to save task.'
    );
    closeTaskModal();
    setStatus('taskStatus', 'Task updated.');
    await loadTasks();
  } catch {
    setStatus('editStatus', 'Could not save changes. Please try again.', true);
  }
}

function renderAnalysis(data) {
  const root = document.getElementById('aiOutput');
  const generatedAt = data.generated_at
    ? new Date(data.generated_at).toLocaleString()
    : null;

  const bucketHtml = (data.buckets || []).map(bucket => `
    <div class="panel">
      <h3>${esc(bucket.name)}</h3>
      <ul>
        ${(bucket.notes || []).map(n =>
          `<li>${esc(n.title)} <span class="pill">${esc(n.priority)}</span></li>`
        ).join('')}
      </ul>
    </div>
  `).join('');

  const todoHtml = (data.todos || []).map(t => {
    const sched = scheduleLabel(t);
    const schedText = sched ? ` · ${esc(sched)}` : '';
    const effort = t.effort_min ? ` · ${t.effort_min}m` : '';
    return `<li><strong>${esc(t.task)}</strong> ${bucketPillHtml(t)}${schedText}${effort}</li>`;
  }).join('');

  const pv = data.priority_view || {High: [], Medium: [], Low: []};
  const priorityHtml = ['High', 'Medium', 'Low'].map(level => `
    <div class="panel">
      <h3>${level} priority (${(pv[level] || []).length})</h3>
      <ul>
        ${(pv[level] || []).map(n => `<li>${esc(n.title)}</li>`).join('') || '<li class="muted">None</li>'}
      </ul>
    </div>
  `).join('');

  root.innerHTML = `
    ${generatedAt ? `<p class="muted"><strong>Generated:</strong> ${esc(generatedAt)}</p>` : ''}

    <h3>Buckets</h3>
    ${bucketHtml || '<p class="muted">No notes yet.</p>'}

    <h3>ToDos</h3>
    <ul>${todoHtml || '<li class="muted">No concrete ToDos detected.</li>'}</ul>

    <h3>Priority view</h3>
    ${priorityHtml}

    <p class="muted">Mode: ${esc(data.mode || 'unknown')}</p>
  `;
}

async function loadLatestAnalysis() {
  try {
    const data = await fetchJson(
      '/api/analyze/latest',
      undefined,
      'Unable to load saved analysis.'
    );
    if (!data) return;

    populateCategoryFilter(data);
    renderAnalysis(data);
    const generatedAt = new Date(data.generated_at).toLocaleString();
    setStatus(
      'analysisStatus',
      `Showing saved analysis generated ${generatedAt}.`
    );
  } catch {
    setStatus(
      'analysisStatus',
      'We could not load the saved analysis. You can try analyzing again.',
      true
    );
  }
}

async function analyzeInbox() {
  if (isAnalyzingInbox) return;

  isAnalyzingInbox = true;
  setButtonBusy(
    'analyzeButton',
    true,
    'Analyzing…',
    'Analyze Entire Inbox with AI'
  );
  setStatus('analysisStatus', 'Analyzing your saved notes…');

  try {
    const data = await fetchJson(
      '/api/analyze',
      {method: 'POST'},
      'Unable to analyze inbox.'
    );
    populateCategoryFilter(data);
    renderAnalysis(data);
    await loadTasks();
    setStatus('analysisStatus', 'Analysis complete.');
  } catch {
    setStatus(
      'analysisStatus',
      'We could not analyze your inbox. Your notes are still here—please try again.',
      true
    );
  } finally {
    isAnalyzingInbox = false;
    setButtonBusy(
      'analyzeButton',
      false,
      'Analyzing…',
      'Analyze Entire Inbox with AI'
    );
  }
}

async function loadNotes(
  failureMessage = 'We could not load your notes. Reload to try again.'
) {
  const params = itemFilterParams();
  const filtered = params.size > 0;
  const query = params.toString();
  try {
    const notes = await fetchJson(
      `/api/items${query ? `?${query}` : ''}`,
      undefined,
      'Unable to load notes.'
    );
    const root = document.getElementById('notes');
    root.innerHTML = '';

    if (!notes.length) {
      root.innerHTML = filtered
        ? '<div class="panel muted">No inbox items match these filters.</div>'
        : '<div class="panel muted">No saved notes yet.</div>';
      setStatus(
        'searchStatus',
        filtered ? 'No results found.' : ''
      );
      return true;
    }

    for (const note of notes.filter(item => item.item_type === 'text')) {
      root.appendChild(renderNote(note));
    }
    setStatus(
      'searchStatus',
      filtered
        ? `${notes.length} matching item${notes.length === 1 ? '' : 's'}.`
        : ''
    );
    return true;
  } catch {
    if (filtered) {
      setStatus(
        'searchStatus',
        'Search failed. Please try again.',
        true
      );
    } else {
      setStatus('noteStatus', failureMessage, true);
    }
    return false;
  }
}

syncControlsFromUrl();
loadNotes();
loadLatestAnalysis();
loadTasks();

window.addEventListener('popstate', () => {
  syncControlsFromUrl();
  loadNotes();
  loadTasks();
});
