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

// --- Voice capture (browser Web Speech API) ---------------------------------

let recognition = null;
let isListening = false;

function setMicState(listening) {
  const btn = document.getElementById('micButton');
  if (!btn) return;
  btn.textContent = listening ? '⏹ Stop' : '🎤 Speak';
  btn.classList.toggle('listening', listening);
  btn.setAttribute('aria-pressed', String(listening));
}

function toggleVoiceCapture() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    setStatus(
      'noteStatus',
      'Voice input is not supported in this browser. Try Chrome or Safari.',
      true
    );
    return;
  }
  if (isListening) {
    stopVoiceCapture();
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = 'en-IN';
  recognition.interimResults = true;
  recognition.continuous = true;

  const textarea = document.getElementById('noteText');
  // Keep whatever was already typed; append spoken words after it.
  const baseText = textarea.value ? textarea.value.replace(/\s*$/, '') + ' ' : '';
  let finalText = '';

  recognition.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalText += transcript + ' ';
      } else {
        interim += transcript;
      }
    }
    textarea.value = baseText + finalText + interim;
  };

  recognition.onerror = (event) => {
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      setStatus(
        'noteStatus',
        'Microphone access was blocked. Allow the mic for this site and try again.',
        true
      );
    } else if (event.error !== 'aborted' && event.error !== 'no-speech') {
      setStatus('noteStatus', `Voice error: ${event.error}.`, true);
    }
  };

  recognition.onend = () => {
    isListening = false;
    setMicState(false);
  };

  try {
    recognition.start();
    isListening = true;
    setMicState(true);
    setStatus('noteStatus', 'Listening… speak now, then press Stop.');
  } catch (err) {
    setStatus('noteStatus', 'Could not start voice input. Try again.', true);
  }
}

function stopVoiceCapture() {
  if (recognition) {
    try {
      recognition.stop();
    } catch (err) { /* ignore */ }
  }
  isListening = false;
  setMicState(false);
  setStatus(
    'noteStatus',
    'Captured your words. Edit if needed, then “Add Note” — or keep talking.'
  );
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
  setButtonBusy('addNoteButton', true, 'Saving…', 'Add & organize');
  setStatus('noteStatus', 'Saving your note…');

  let savedItem = null;
  try {
    savedItem = await fetchJson(
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
    return savedItem;
  } catch {
    setStatus(
      'noteStatus',
      'We could not save your note. Your text is still here—please try again.',
      true
    );
  } finally {
    isSavingNote = false;
    setButtonBusy('addNoteButton', false, 'Saving…', 'Add & organize');
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

async function uploadFile() {
  const input = document.getElementById('fileInput');
  const file = input.files && input.files[0];
  if (!file) {
    setStatus('noteStatus', 'Choose an image or PDF first.', true);
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  setButtonBusy('uploadButton', true, 'Reading…', 'Read file & add');
  setStatus('noteStatus', `Reading ${file.name}…`);
  try {
    const items = await fetchJson(
      '/api/items/upload',
      {method: 'POST', body: formData},
      'Unable to read the file.'
    );
    input.value = '';
    const count = Array.isArray(items) ? items.length : 1;
    setStatus(
      'noteStatus',
      `Added ${count} item${count === 1 ? '' : 's'} from ${file.name}. ` +
      'Click “Analyze Entire Inbox with AI” to organize them.'
    );
    await loadNotes();
  } catch {
    setStatus(
      'noteStatus',
      'We could not read that file. Please try another image or PDF.',
      true
    );
  } finally {
    setButtonBusy('uploadButton', false, 'Reading…', 'Read file & add');
  }
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
  const source = note.original_filename
    ? ` · 📎 ${note.original_filename}`
    : '';
  time.textContent = `#${note.id} · ${note.created_at}${source}`;

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

  let progress = null;
  if (task.progress_pct) {
    progress = document.createElement('div');
    progress.className = 'progress';
    progress.innerHTML =
      `<span class="progress-bar"><span class="progress-fill" ` +
      `style="width:${task.progress_pct}%"></span></span>` +
      `<span class="progress-pct">${task.progress_pct}%</span>`;
  }

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

  if (progress) {
    root.append(content, meta, progress, actions);
  } else {
    root.append(content, meta, actions);
  }
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
  try {
    const tasks = await fetchJson(
      '/api/tasks?status=all',
      undefined,
      'Unable to load tasks.'
    );
    lastTasks = tasks;
    updateEmptyState();
    renderDay();
    renderTimePie();
    renderWeekSummary();
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

// --- Day view ----------------------------------------------------------------

// First-run: when there's nothing yet, show only the centered capture bar.
function updateEmptyState() {
  document.body.classList.toggle('empty-state', lastTasks.length === 0);
}

const BUCKET_ORDER = {P0: 0, P1: 1, P2: 2};
const BUCKET_LABELS = {
  P0: 'P0 · Urgent',
  P1: 'P1 · Important',
  P2: 'P2 · Operational'
};

let selectedDate = new Date();
selectedDate.setHours(0, 0, 0, 0);

function changeDay(delta) {
  selectedDate = new Date(selectedDate);
  selectedDate.setDate(selectedDate.getDate() + delta);
  selectedDate.setHours(0, 0, 0, 0);
  updateDayLabel();
  renderDay();
  renderTimePie();
}

function updateDayLabel() {
  const label = document.getElementById('dayLabel');
  if (!label) return;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((selectedDate - today) / 86400000);
  let prefix = selectedDate.toLocaleDateString(undefined, {weekday: 'long'});
  if (diffDays === 0) prefix = 'Today';
  else if (diffDays === 1) prefix = 'Tomorrow';
  else if (diffDays === -1) prefix = 'Yesterday';
  const dateStr = selectedDate.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short'
  });
  label.textContent = `${prefix} · ${dateStr}`;
}

// Tasks that belong on a given day: recurring every day, one-offs on their due
// date, plus (for today only) open past-due tasks carried forward.
function dayTasksFor(dateIso) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayIso = localIso(today);
  const out = [];
  for (const t of lastTasks) {
    if (t.status === 'dismissed') continue;
    if (t.task_type === 'recurring') {
      // A recurring task runs from the day it was created through its deadline
      // (due_date), inclusive. No deadline = an open-ended habit that repeats
      // forever. Past the deadline it disappears — you don't study for an exam
      // after it's over.
      const startIso = t.created_at ? localIso(new Date(t.created_at)) : null;
      const afterStart = !startIso || dateIso >= startIso;
      const beforeDeadline = !t.due_date || dateIso <= t.due_date;
      if (afterStart && beforeDeadline) out.push(t);
    } else if (t.due_date === dateIso) {
      out.push(t);
    } else if (dateIso === todayIso && t.status === 'open' && !t.due_date) {
      // Undated tasks surface on today so new thoughts show up immediately.
      out.push({...t, _anytime: true});
    } else if (
      dateIso === todayIso
      && t.status === 'open'
      && t.due_date
      && t.due_date < todayIso
    ) {
      out.push({...t, _overdue: true});
    }
  }
  return out.sort(compareTasks);
}

function renderDay() {
  const root = document.getElementById('dayView');
  if (!root) return;
  const dateIso = localIso(selectedDate);
  const tasks = dayTasksFor(dateIso);

  if (!tasks.length) {
    root.innerHTML = '<div class="panel muted">Nothing scheduled for this day. '
      + 'Enter a thought above and I’ll sort it in.</div>';
    return;
  }

  const sections = ['P0', 'P1', 'P2'].map(bucket => {
    const inBucket = tasks.filter(t => (t.bucket || 'P2') === bucket);
    if (!inBucket.length) return '';
    return `
      <div class="bucket-section bucket-${bucket}">
        <h3 class="bucket-head">${esc(BUCKET_LABELS[bucket])} <span class="muted">(${inBucket.length})</span></h3>
        ${inBucket.map(renderDayTaskHtml).join('')}
      </div>
    `;
  }).join('');

  root.innerHTML = sections;
}

function linkifyText(text) {
  const urlRe = /(https?:\/\/[^\s]+)/g;
  let out = '';
  let last = 0;
  let match;
  while ((match = urlRe.exec(text)) !== null) {
    out += esc(text.slice(last, match.index));
    const url = match[0].replace(/[).,]+$/, '');
    out += `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`;
    last = match.index + match[0].length;
  }
  out += esc(text.slice(last));
  return out;
}

function meetingJoinHtml(task) {
  const url = task.link
    || (task.text.match(/https?:\/\/[^\s]+/) || [])[0];
  if (!url) return '';
  const clean = url.replace(/[).,]+$/, '');
  let label = '🔗 Join';
  if (/zoom\.us/i.test(clean)) label = '🎥 Join Zoom';
  else if (/meet\.google/i.test(clean)) label = '🎥 Join Meet';
  else if (/teams\.microsoft/i.test(clean)) label = '🎥 Join Teams';
  return `<a class="join-link" href="${esc(clean)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

function renderDayTaskHtml(task) {
  const doneClass = task.status === 'completed' ? ' task-row-done' : '';
  const sched = task.task_type === 'recurring'
    ? `🔁 ${task.recurrence || 'recurring'}`
    : scheduleLabel(task);
  const schedText = sched ? `<span class="task-sched">${esc(sched)}</span>` : '';
  const effort = task.effort_min ? `<span class="task-effort">${task.effort_min}m</span>` : '';
  const star = task.importance === 'high' ? ' <span class="star">★</span>' : '';
  const overdue = task._overdue ? ' <span class="overdue">overdue</span>' : '';
  const anytime = task._anytime ? ' <span class="anytime">anytime</span>' : '';
  const join = meetingJoinHtml(task);
  const progress = task.progress_pct ? progressBarHtml(task.progress_pct) : '';
  return `<div class="task-row${doneClass}" data-task-id="${task.id}">
    <div class="task-row-main">
      <span class="task-row-text">${linkifyText(task.text)}${star}${overdue}${anytime}</span>
      ${join}
    </div>
    <div class="task-row-meta">${schedText}${effort}${progress}</div>
    ${taskHoverActionsHtml(task.id)}
  </div>`;
}

function renderWeekSummary() {
  const root = document.getElementById('weekSummary');
  if (!root) return;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const weekAgo = new Date(today);
  weekAgo.setDate(today.getDate() - 7);
  const weekAgoIso = localIso(weekAgo);
  const todayIso = localIso(today);

  const completed = lastTasks.filter(t =>
    t.status === 'completed'
    && (t.updated_at || '').slice(0, 10) >= weekAgoIso
  );
  const missed = lastTasks.filter(t =>
    t.status === 'open'
    && t.task_type !== 'recurring'
    && t.due_date
    && t.due_date >= weekAgoIso
    && t.due_date < todayIso
  );

  const listOf = (items) => items.length
    ? `<ul class="summary-list">${items.slice(0, 6).map(t =>
        `<li>${esc(t.text)}</li>`).join('')}</ul>`
    : '<p class="muted small">None</p>';

  root.innerHTML = `
    <div class="summary-block">
      <p class="summary-count done">✓ Completed (${completed.length})</p>
      ${listOf(completed)}
    </div>
    <div class="summary-block">
      <p class="summary-count miss">✗ Missed (${missed.length})</p>
      ${listOf(missed)}
    </div>
  `;
}

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

function taskHoverActionsHtml(taskId) {
  return `<span class="task-hover-actions">` +
    `<button type="button" title="Edit" aria-label="Edit task" onclick="openTaskModal(${taskId})">✎</button>` +
    `<button type="button" title="Complete" aria-label="Complete task" onclick="completeTask(${taskId})">✓</button>` +
    `<button type="button" title="Delete" aria-label="Delete task" onclick="deleteTask(${taskId})">🗑</button>` +
    `</span>`;
}

// --- Today's time pie --------------------------------------------------------

function fmtMins(mins) {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

function selectedDayOccupancy() {
  // Open tasks on the selected day, summed by bucket.
  const totals = {P0: 0, P1: 0, P2: 0};
  for (const t of dayTasksFor(localIso(selectedDate))) {
    if (t.status !== 'open') continue;
    const bucket = t.bucket || 'P2';
    totals[bucket] = (totals[bucket] || 0) + (t.effort_min || 0);
  }
  return totals;
}

function buildDonut(slices, ringTotal) {
  const size = 150;
  const stroke = 26;
  const r = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circ = 2 * Math.PI * r;
  let acc = 0;
  const segs = [];
  for (const s of slices) {
    if (s.value <= 0) continue;
    const len = (s.value / ringTotal) * circ;
    segs.push(
      `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" ` +
      `stroke="${s.color}" stroke-width="${stroke}" ` +
      `stroke-dasharray="${len.toFixed(2)} ${(circ - len).toFixed(2)}" ` +
      `stroke-dashoffset="${(-acc).toFixed(2)}"></circle>`
    );
    acc += len;
  }
  return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" class="donut" aria-hidden="true">` +
    `<g transform="rotate(-90 ${cx} ${cy})">` +
    `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#efece5" stroke-width="${stroke}"></circle>` +
    segs.join('') +
    `</g></svg>`;
}

function renderTimePie() {
  const el = document.getElementById('timePie');
  if (!el) return;
  const budgetH = parseFloat(document.getElementById('dayBudget').value) || 16;
  try {
    localStorage.setItem('dayBudgetH', String(budgetH));
  } catch (e) { /* ignore */ }

  const budget = Math.round(budgetH * 60);
  const totals = selectedDayOccupancy();
  const occupied = totals.P0 + totals.P1 + totals.P2;

  if (occupied === 0) {
    el.innerHTML = '<p class="muted small">No time committed for this day yet.</p>';
    return;
  }

  const free = Math.max(0, budget - occupied);
  const over = Math.max(0, occupied - budget);
  const ring = Math.max(budget, occupied) || 1;
  const slices = [
    {label: 'P0 urgent', value: totals.P0, color: '#a8432f'},
    {label: 'P1 deep work', value: totals.P1, color: '#c08a3e'},
    {label: 'P2 operational', value: totals.P2, color: '#3f6b78'},
    {label: 'Free', value: free, color: '#b9cdb0'}
  ];
  const legend = slices
    .filter(s => s.value > 0)
    .map(s =>
      `<li><span class="swatch" style="background:${s.color}"></span>` +
      `${s.label} — ${fmtMins(s.value)}</li>`
    )
    .join('');
  const pct = budget ? Math.round((occupied / budget) * 100) : 0;
  const overMsg = over > 0
    ? `<p class="over">Overbooked by ${fmtMins(over)}</p>`
    : '';

  el.innerHTML = `
    <div class="time-layout">
      ${buildDonut(slices, ring)}
      <div class="time-info">
        <p><strong>${fmtMins(occupied)}</strong> committed of ${fmtMins(budget)} (${pct}%)</p>
        <ul class="time-legend">${legend}</ul>
        ${overMsg}
      </div>
    </div>
  `;
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
  document.getElementById('editProgressNote').value = task.progress_note || '';
  document.getElementById('editProgressPct').value =
    task.progress_pct != null ? task.progress_pct : '';
  onEditTypeChange();
  setStatus('editStatus', '');
  document.getElementById('taskModalBackdrop').hidden = false;
}

async function logProgress() {
  if (editingTaskId == null) return;
  const note = document.getElementById('editProgressNote').value.trim();
  const pctRaw = document.getElementById('editProgressPct').value;
  const payload = {};
  if (note) payload.note = note;
  if (pctRaw !== '') payload.pct = Number(pctRaw);
  if (payload.pct == null && !payload.note) {
    setStatus('editStatus', 'Add a progress note or a %.', true);
    return;
  }
  setStatus('editStatus', 'Logging progress…');
  try {
    const updated = await fetchJson(
      `/api/tasks/${editingTaskId}/progress`,
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      },
      'Unable to log progress.'
    );
    document.getElementById('editProgressPct').value = updated.progress_pct;
    setStatus('editStatus', `Logged: ${updated.progress_pct}% complete today.`);
    await loadTasks();
  } catch {
    setStatus('editStatus', 'Could not log progress. Please try again.', true);
  }
}

function progressBarHtml(pct) {
  return `<span class="progress">` +
    `<span class="progress-bar"><span class="progress-fill" style="width:${pct}%"></span></span>` +
    `<span class="progress-pct">${pct}%</span></span>`;
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

async function analyzeInbox() {
  if (isAnalyzingInbox) return;
  isAnalyzingInbox = true;
  startAnalyzing();
  try {
    await fetchJson('/api/analyze', {method: 'POST'}, 'Unable to analyze inbox.');
    await loadTasks();
  } catch {
    setStatus(
      'noteStatus',
      'We could not organize your inbox. Your notes are safe — please try again.',
      true
    );
  } finally {
    isAnalyzingInbox = false;
    stopAnalyzing();
  }
}

// One-tap flow: save the thought, organize it, and show where it landed.
let isCapturing = false;
let lastCapture = {text: '', at: 0};

async function captureAndOrganize() {
  const text = document.getElementById('noteText').value.trim();
  if (!text) {
    setStatus('noteStatus', 'Enter a thought first — or press 🎤 Speak.', true);
    return;
  }
  // Double-submit guard: ignore a second trigger while one is in flight, and
  // swallow an identical note re-sent within 10s (rapid double-tap or voice+click).
  if (isCapturing) return;
  if (text === lastCapture.text && Date.now() - lastCapture.at < 10000) {
    setStatus('noteStatus', 'Already added that one just now.');
    document.getElementById('noteText').value = '';
    return;
  }
  isCapturing = true;
  lastCapture = {text, at: Date.now()};
  try {
    await runCapture(text);
  } finally {
    isCapturing = false;
  }
}

const ANALYZING_MESSAGES = [
  'Reading your thoughts',
  'Analyzing and prioritizing for you',
  'Sorting into P0 · P1 · P2',
  'Almost there',
];
let analyzingTimer = null;

function startAnalyzing() {
  const panel = document.getElementById('thinking');
  const msg = document.getElementById('analyzingMsg');
  if (!panel) return;
  panel.hidden = false;
  if (!msg) return;
  let i = 0;
  msg.classList.remove('fade');
  msg.textContent = ANALYZING_MESSAGES[0];
  clearInterval(analyzingTimer);
  analyzingTimer = setInterval(() => {
    msg.classList.add('fade');               // fade out
    setTimeout(() => {
      i = (i + 1) % ANALYZING_MESSAGES.length;
      msg.textContent = ANALYZING_MESSAGES[i];
      msg.classList.remove('fade');          // fade back in
    }, 250);
  }, 1500);
}

function stopAnalyzing() {
  clearInterval(analyzingTimer);
  analyzingTimer = null;
  const panel = document.getElementById('thinking');
  if (panel) panel.hidden = true;
}

async function runCapture(text) {
  const item = await addNote();  // saves + clears the box, returns the item
  if (!item || document.getElementById('noteText').value.trim()) return;

  // Fast path: analyze ONLY this new note (not the whole inbox).
  startAnalyzing();
  let created = [];
  try {
    created = await fetchJson(
      `/api/items/${item.id}/organize`,
      {method: 'POST'},
      'Unable to organize this note.'
    ) || [];
    await loadTasks();
  } catch {
    setStatus('noteStatus', 'Saved, but could not organize it. Try again.', true);
    return;
  } finally {
    stopAnalyzing();
  }
  if (!created.length) {
    setStatus('noteStatus', 'Saved to your inbox.');
    return;
  }

  const whenOf = (t) => {
    if (t.task_type === 'recurring') return t.recurrence || 'recurring';
    if (t.due_date) return formatDueDate(t.due_date);
    return 'today';
  };
  const summary = created
    .map(t => `${t.text} → ${t.bucket} · ${whenOf(t)}`)
    .join('   ·   ');
  setStatus('noteStatus', `Sorted: ${summary}`);

  // If a new task isn't on the day you're viewing, jump so you actually see it.
  const onView = dayTasksFor(localIso(selectedDate)).some(
    d => created.some(c => c.id === d.id)
  );
  if (!onView) {
    const dated = created.find(t => t.due_date);
    if (dated) {
      selectedDate = new Date(`${dated.due_date}T00:00:00`);
      selectedDate.setHours(0, 0, 0, 0);
      updateDayLabel();
      renderDay();
      renderTimePie();
    }
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

    for (const note of notes.filter(item => item.raw_text)) {
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

function restoreDayBudget() {
  try {
    const saved = localStorage.getItem('dayBudgetH');
    if (saved) document.getElementById('dayBudget').value = saved;
  } catch (e) { /* ignore */ }
}

// --- Gmail inbound sync -----------------------------------------------------

let gmailConnected = false;

function setGmailButton(connected) {
  gmailConnected = connected;
  const btn = document.getElementById('gmailButton');
  if (!btn) return;
  btn.textContent = connected ? 'Sync Gmail' : 'Connect Gmail';
  btn.title = connected
    ? 'Pull recent emails and turn them into tasks'
    : 'Connect your Gmail (read-only) to turn emails into tasks';
}

async function refreshGmailStatus() {
  try {
    const status = await fetchJson('/api/gmail/status', undefined, '');
    setGmailButton(Boolean(status && status.connected));
  } catch {
    setGmailButton(false);
  }
}

function gmailAction() {
  if (gmailConnected) {
    syncGmail();
  } else {
    // Hand off to Google's consent screen; the callback returns us to "/".
    window.location.href = '/api/gmail/auth';
  }
}

async function syncGmail() {
  setButtonBusy('gmailButton', true, 'Syncing…', 'Sync Gmail');
  setStatus('noteStatus', 'Checking your inbox…');
  try {
    const result = await fetchJson(
      '/api/gmail/sync',
      {method: 'POST'},
      'Unable to sync Gmail.'
    );
    const created = (result && result.created_tasks) || [];
    const newEmails = (result && result.new_emails) || 0;
    if (!newEmails) {
      setStatus('noteStatus', 'No new emails to turn into tasks.');
    } else {
      const n = created.length;
      setStatus(
        'noteStatus',
        `Pulled ${newEmails} new email${newEmails === 1 ? '' : 's'} → ` +
        `${n} actionable${n === 1 ? '' : 's'} added.`
      );
    }
    await loadNotes();
    await loadTasks();
  } catch {
    setStatus('noteStatus', 'Could not sync Gmail. Please try again.', true);
  } finally {
    setButtonBusy('gmailButton', false, 'Syncing…', 'Sync Gmail');
  }
}

function handleGmailReturn() {
  const params = new URLSearchParams(window.location.search);
  const flag = params.get('gmail');
  if (!flag) return;
  if (flag === 'connected') {
    setStatus('noteStatus', 'Gmail connected. Click “Sync Gmail” to pull your emails.');
  } else if (flag === 'error') {
    setStatus('noteStatus', 'Gmail connection was cancelled or failed.', true);
  }
  // Clean the flag out of the URL so a refresh doesn't repeat the message.
  params.delete('gmail');
  const qs = params.toString();
  window.history.replaceState(
    {}, '', window.location.pathname + (qs ? `?${qs}` : '')
  );
}

syncControlsFromUrl();
restoreDayBudget();
handleGmailReturn();
refreshGmailStatus();
updateDayLabel();
loadNotes();
loadTasks();

window.addEventListener('popstate', () => {
  syncControlsFromUrl();
  loadNotes();
  loadTasks();
});
