// SKAI web app. Every number shown comes from the API (app/api.py), which runs
// the trained models; nothing here is simulated.
//
// Normally the page is served by the API itself (python run_app.py) at "/", so
// API calls are same-origin. When it is opened from the project folder instead
// (VS Code Live Server serves it as /app/web/index.html) or as a file, the
// calls go to the API on its default port 8000.
const API_BASE = (location.protocol === 'file:' || location.pathname.includes('/app/web/'))
    ? 'http://127.0.0.1:8000' : '';

const SKAI = (() => {
    const state = {
        role: null, mode: 'clinical', lang: 'en',
        result: null, inputs: null, assessmentId: null,
        charts: {}, whatifTimer: null, idTimer: null, modelInfo: null, queue: [],
    };

    // ---------- form definitions ----------
    const codes = n => Array.from({ length: n }, (_, i) => [i, String(i)]);
    const yesNo = [[0, 'no'], [1, 'yes']];

    const FORMS = {
        clinical: [
            { section: 'section_vitals' },
            { key: 'age', min: 18, max: 100, value: 55 },
            { key: 'sex', options: [[1, 'male'], [0, 'female']], value: 1 },
            { key: 'trestbps', min: 70, max: 250, value: 130 },
            { key: 'chol', min: 100, max: 600, value: 240 },
            { key: 'thalach', min: 60, max: 220, value: 150 },
            { key: 'fbs', options: yesNo, value: 0 },
            { section: 'section_tests' },
            { key: 'cp', options: codes(4), value: 0, raw: true },
            { key: 'restecg', options: codes(3), value: 1, raw: true },
            { key: 'exang', options: yesNo, value: 0 },
            { key: 'oldpeak', min: 0, max: 10, step: 0.1, value: 1.0 },
            { key: 'slope', options: codes(3), value: 1, raw: true },
            { key: 'ca', options: codes(5), value: 0, raw: true },
            { key: 'thal', options: codes(4), value: 2, raw: true },
        ],
        lifestyle: [
            { section: 'section_about_you' },
            { key: 'age', min: 18, max: 100, value: 45 },
            { key: 'sbp', min: 80, max: 250, value: 130 },
            { key: 'cigarettes_per_day', min: 0, max: 80, value: 0 },
            { key: 'years_smoked', min: 0, max: 80, value: 0 },
            { key: 'family_history', options: yesNo, value: 0 },
            { section: 'section_optional' },
            { key: 'height_cm', min: 100, max: 230, optional: true },
            { key: 'weight_kg', min: 30, max: 300, optional: true },
            { key: 'ldl', min: 0.5, max: 20, step: 0.1, optional: true },
        ],
    };

    // What-if sliders: [feature, min, max, step]
    const WHATIF = {
        clinical: [['trestbps', 90, 200, 1], ['chol', 120, 400, 1], ['thalach', 70, 200, 1], ['oldpeak', 0, 6, 0.1]],
        lifestyle: [['sbp', 100, 200, 1], ['cigarettes_per_day', 0, 40, 1], ['weight_kg', 40, 160, 1], ['ldl', 1, 10, 0.1]],
    };

    const TIER_COLOUR = { low: '#10b981', moderate: '#eab308', high: '#f97316', critical: '#ef4444' };

    // ---------- helpers ----------
    const $ = id => document.getElementById(id);
    const pct = p => `${(p * 100).toFixed(1)}%`;
    const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function t(key, vars = {}) {
        const dict = window.SKAI_I18N;
        let s = (dict[state.lang] && dict[state.lang][key]) || dict.en[key] || (window.SKAI_STAFF_TEXT || {})[key] || key;
        for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
        return s;
    }
    const featureLabel = f => t(`f_${f}`);

    function toast(message, type = 'info') {
        const colours = { success: 'border-emerald-500', info: 'border-sky-500', warning: 'border-amber-500', error: 'border-red-500' };
        const el = document.createElement('div');
        el.className = `bg-slate-900/95 border border-slate-700 border-l-4 ${colours[type]} px-4 py-3 rounded-xl shadow-2xl text-sm text-white fade-in`;
        el.textContent = message;
        $('toast-container').appendChild(el);
        setTimeout(() => el.remove(), 4000);
    }

    // ---------- clinician session ----------
    // The token lives in this browser only; the server keeps just its hash and expires it after 8 hours.
    const session = {
        get token() { try { return localStorage.getItem('skai-clinician-token'); } catch (e) { return null; } },
        set(token, clinician) {
            try { localStorage.setItem('skai-clinician-token', token); } catch (e) { /* private mode */ }
            state.clinician = clinician;
        },
        clear() {
            try { localStorage.removeItem('skai-clinician-token'); } catch (e) { /* private mode */ }
            state.clinician = null;
        },
    };

    // The patient app has its own session, separate from any clinician signed in on this browser
    const patientSession = {
        get token() { try { return localStorage.getItem('skai-patient-token'); } catch (e) { return null; } },
        set(token) { try { localStorage.setItem('skai-patient-token', token); } catch (e) { /* private mode */ } },
        clear() { try { localStorage.removeItem('skai-patient-token'); } catch (e) { /* private mode */ } },
    };
    const activeToken = () => (state.role === 'patient' ? patientSession.token : session.token);
    const authHeader = () => (activeToken() ? { Authorization: `Bearer ${activeToken()}` } : {});

    async function api(path, body, method) {
        const headers = { ...(body ? { 'Content-Type': 'application/json' } : {}), ...authHeader() };
        const res = await fetch(API_BASE + path, {
            method: method || (body ? 'POST' : 'GET'), headers, body: body ? JSON.stringify(body) : undefined,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            // FastAPI returns a list (validation), a string, or {code} for app errors
            const detail = Array.isArray(err.detail) ? err.detail.map(d => d.msg).join('; ')
                : (err.detail && err.detail.code) || err.detail;
            if (detail === 'not_signed_in' && state.role === 'clinician') sessionExpired();
            if (detail === 'patient_not_signed_in' && state.role === 'patient' && SKAI.patient) SKAI.patient.sessionExpired();
            throw new Error(detail || res.statusText);
        }
        return res.json();
    }

    function sessionExpired() {
        session.clear();
        toast(t('session_expired'), 'warning');
        showLogin();
    }

    // ---------- sign-in ----------
    const DEMO_DOCTORS = ['samuel.olajide', 'sfiso.kekana', 'mpho.mutengwe', 'tshiamo.bokaba', 'kamogelo', 'neontle'];

    function showLogin() {
        moveAdvisor(false);
        showView('view-login');
        $('login-error').classList.add('hidden');
        $('login-pass').value = '';
        $('login-demo').innerHTML = DEMO_DOCTORS.map(u =>
            `<button type="button" onclick="SKAI.fillLogin('${u}')" class="chip bg-slate-800 border border-slate-600 text-slate-200 hover:border-skai-primary !text-[11px] !py-1">${u}</button>`).join('');
        setTimeout(() => ($('login-user').value ? $('login-pass') : $('login-user')).focus(), 50);
    }

    function fillLogin(username) {
        $('login-user').value = username;
        $('login-pass').focus();
    }

    function togglePassword() {
        const show = $('login-pass').type === 'password';
        $('login-pass').type = show ? 'text' : 'password';
        $('login-eye').className = `fa-solid ${show ? 'fa-eye-slash' : 'fa-eye'} text-xs`;
    }

    async function login() {
        if (!$('login-form').reportValidity()) return;
        $('login-btn').disabled = true;
        $('login-error').classList.add('hidden');
        try {
            const r = await api('/api/auth/login', { username: $('login-user').value.trim(), password: $('login-pass').value });
            session.set(r.token, r.clinician);
            $('login-pass').value = '';
            toast(t('signed_in_as', { name: r.clinician.name }), 'success');
            openWorkspace('clinician', r.clinician.role === 'nurse' ? 'triage' : 'assess');
        } catch (e) {
            const key = { locked: 'login_locked', login_failed: 'login_failed' }[e.message];
            $('login-error').textContent = key ? t(key) : `${t('error_generic')} (${e.message})`;
            $('login-error').classList.remove('hidden');
        } finally {
            $('login-btn').disabled = false;
        }
    }

    async function logout() {
        try { await api('/api/auth/logout', {}); } catch (e) { /* already expired */ }
        session.clear();
        closeDoctorMenu();
        toast(t('signed_out'), 'info');
        home();
    }

    function avatarHtml(person, size = 'w-7 h-7 text-[10px]') {
        if (person.photo) return `<img src="${esc(person.photo)}" alt="" class="${size} rounded-full object-cover">`;
        const initials = person.name.replace(/^Dr\.?\s+/i, '').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
        return `<span class="on-accent ${size} rounded-full bg-gradient-to-br from-skai-primary to-blue-600 text-white font-black flex items-center justify-center shrink-0">${esc(initials)}</span>`;
    }

    function renderDoctor() {
        const c = state.clinician;
        $('doctor-menu').classList.toggle('hidden', !c || state.role !== 'clinician');
        if (!c) return;
        $('doctor-avatar').innerHTML = avatarHtml(c);
        $('doctor-name').textContent = c.name;
        $('doctor-dd-name').textContent = c.name;
        $('doctor-dd-title').textContent = [c.title, c.facility_name].filter(Boolean).join(' · ');
    }

    function toggleDoctorMenu() { $('doctor-dropdown').classList.toggle('hidden'); }
    function closeDoctorMenu() { $('doctor-dropdown').classList.add('hidden'); }
    document.addEventListener('click', e => { if (!e.target.closest('#doctor-menu')) closeDoctorMenu(); });

    // Shared by the doctor and patient profile pictures: centre-crop to a small square JPEG
    function resizeImage(file, size = 256) {
        return new Promise((resolve, reject) => {
            if (!file || !file.type.startsWith('image/')) { reject(new Error('not an image')); return; }
            const img = new Image();
            img.onload = () => {
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = size;
                const side = Math.min(img.width, img.height);
                canvas.getContext('2d').drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, size, size);
                URL.revokeObjectURL(img.src);
                resolve(canvas.toDataURL('image/jpeg', 0.85));
            };
            img.onerror = reject;
            img.src = URL.createObjectURL(file);
        });
    }

    async function doctorPhoto(file) {
        closeDoctorMenu();
        try {
            const dataUrl = await resizeImage(file);
            await api('/api/clinicians/me/photo', { data_url: dataUrl }, 'PUT');
            state.clinician.photo = dataUrl;
            renderDoctor();
            toast(t('photo_saved'), 'success');
        } catch (e) { toast(t('error_generic'), 'error'); }
    }

    // ---------- shared modal ----------
    function modal(html) {
        $('modal-body').innerHTML = html;
        $('modal').classList.remove('hidden');
        $('modal').classList.add('flex');
    }

    function closeModal() {
        $('modal').classList.add('hidden');
        $('modal').classList.remove('flex');
    }

    function openPasswordChange() {
        closeDoctorMenu();
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-4"><i class="fa-solid fa-key text-skai-primary mr-2"></i>${t('pw_title')}</h3>
            <form id="pw-form" class="space-y-3" onsubmit="event.preventDefault(); SKAI.changePassword();">
                <div><label class="field-label" for="pw-current">${t('pw_current')}</label><input id="pw-current" type="password" class="field" autocomplete="current-password" required></div>
                <div><label class="field-label" for="pw-new">${t('pw_new')}</label><input id="pw-new" type="password" class="field" autocomplete="new-password" minlength="8" required></div>
                <p id="pw-error" class="hidden text-xs font-bold text-red-400"></p>
                <div class="flex gap-2 pt-2">
                    <button type="button" onclick="SKAI.closeModal()" class="flex-1 bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">${t('pt_cancel')}</button>
                    <button class="on-accent flex-1 bg-skai-primary text-white font-bold py-2.5 rounded-xl text-sm">${t('pt_save')}</button>
                </div>
            </form>`);
        $('pw-current').focus();
    }

    async function changePassword() {
        if (!$('pw-form').reportValidity()) return;
        try {
            await api('/api/clinicians/me/password', { current: $('pw-current').value, new: $('pw-new').value }, 'PUT');
            closeModal();
            toast(t('pw_changed'), 'success');
        } catch (e) {
            $('pw-error').textContent = e.message === 'pw_wrong' ? t('pw_wrong') : `${t('error_generic')} (${e.message})`;
            $('pw-error').classList.remove('hidden');
        }
    }

    async function openActivity() {
        closeDoctorMenu();
        let log = [];
        try { log = await api('/api/audit?limit=60'); } catch (e) { return; }
        const when = iso => new Date(iso).toLocaleString(state.lang === 'en' ? 'en-ZA' : undefined,
            { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-1"><i class="fa-solid fa-clock-rotate-left text-skai-primary mr-2"></i>${t('activity_title')}</h3>
            <p class="text-xs text-slate-400 mb-4">${t('activity_sub')}</p>
            <ul class="space-y-2">${log.map(a => `
                <li class="flex items-center gap-3 bg-slate-900/60 border border-slate-800 rounded-xl px-3 py-2">
                    ${avatarHtml({ name: a.clinician_name, photo: a.clinician_photo })}
                    <div class="flex-1 min-w-0 text-xs"><span class="font-bold text-white">${esc(a.clinician_name)}</span>
                        <span class="text-slate-300">${t(`act_${a.action}`, { target: esc(a.target || '') })}</span></div>
                    <span class="text-[10px] text-slate-500 whitespace-nowrap">${when(a.at)}</span>
                </li>`).join('')}</ul>
            <button onclick="SKAI.closeModal()" class="mt-4 w-full bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">${t('pt_close')}</button>`);
    }

    // Documents need the clinician's session, so they are fetched with it rather than linked directly
    async function viewDocument(id) {
        const res = await fetch(`${API_BASE}/api/documents/${id}/file`, { headers: { Authorization: `Bearer ${session.token}` } });
        if (res.status === 401) { sessionExpired(); return; }
        if (!res.ok) { toast(t('error_generic'), 'error'); return; }
        window.open(URL.createObjectURL(await res.blob()), '_blank', 'noopener');
    }

    function chart(id, config) {
        if (state.charts[id]) state.charts[id].destroy();
        state.charts[id] = new Chart($(id), config);
    }
    Chart.defaults.font.family = '"Plus Jakarta Sans", sans-serif';
    const grid = { color: 'rgba(51, 65, 85, 0.35)' };   // updated by applyTheme

    // Things to re-draw when the language or theme changes (patient.js registers too)
    const rerenderHooks = [];

    // ---------- theme ----------
    function applyTheme(theme) {
        document.documentElement.dataset.theme = theme;
        const light = theme === 'light';
        Chart.defaults.color = light ? '#475569' : '#94a3b8';
        grid.color = light ? 'rgba(148, 163, 184, 0.3)' : 'rgba(51, 65, 85, 0.35)';
        document.querySelectorAll('.theme-toggle i').forEach(i => { i.className = `fa-solid ${light ? 'fa-sun' : 'fa-moon'}`; });
    }

    function toggleTheme() {
        const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
        try { localStorage.setItem('skai-theme', next); } catch (e) { /* private mode */ }
        applyTheme(next);
        rerender();
    }

    function rerender() {
        if (state.role && state.role !== 'patient') {
            renderForm(readInputs());
            if (state.result) { renderResult(); advisorIntro(); } else clearResult();
            renderQueue();
            renderDocs();
            if (!$('panel-models').classList.contains('hidden')) renderModels();
        }
        rerenderHooks.forEach(fn => fn());
    }

    // ---------- navigation ----------
    function showView(id) {
        for (const v of ['view-home', 'view-login', 'view-work', 'view-patient']) {
            $(v).classList.toggle('hidden', v !== id);
        }
        $('view-work').classList.toggle('flex', id === 'view-work');
    }

    // The advisor card is shared: it sits in the clinician results or the patient dashboard
    function moveAdvisor(toPatient) {
        (toPatient ? $('pt-advisor-slot') : $('advisor-home')).appendChild($('advisor-card'));
    }

    async function enter(role, tab = 'assess') {
        state.role = role;      // set first: it decides which session (clinician or patient) API calls carry
        if (role === 'clinician') {
            if (!session.token) { showLogin(); return; }
            try { state.clinician = await api('/api/auth/me'); } catch (e) { session.clear(); showLogin(); return; }
        }
        openWorkspace(role, tab);
    }

    function openWorkspace(role, tab = 'assess') {
        state.role = role;
        state.result = null;
        if (role === 'patient') {
            showView('view-patient');
            moveAdvisor(true);
            clearResult();
            SKAI.patient.open();
            return;
        }
        moveAdvisor(false);
        showView('view-work');
        renderDoctor();
        const clinician = role === 'clinician';
        $('queue-panel').classList.toggle('hidden', !clinician);
        $('mode-toggle').classList.toggle('hidden', !clinician);
        $('patient-brand').classList.toggle('hidden', clinician);
        $('patient-brand').classList.toggle('flex', !clinician);
        // Never carry one person's details into the other role's session
        $('p-name').value = '';
        $('p-id').value = '';
        state.charts['history-chart']?.destroy();
        delete state.charts['history-chart'];
        $('history-empty').classList.remove('hidden');
        state.patient = null;
        renderDocs();
        setMode(clinician ? 'clinical' : 'lifestyle');
        if (clinician) refreshQueue();
        showTab(tab);
    }

    function home() {
        moveAdvisor(false);
        showView('view-home');
    }

    // Staff-only tabs (waiting room, bookings, referrals, outbox) live in staff.js
    const STAFF_TABS = ['triage', 'bookings', 'referrals', 'outbox', 'monitoring'];
    function showTab(tab) {
        const staff = state.role === 'clinician';
        const monitoring = staff && SKAI.staff && SKAI.staff.can('dashboard');
        if ((!staff && STAFF_TABS.includes(tab)) || (tab === 'monitoring' && !monitoring)) tab = 'assess';
        for (const name of ['assess', 'models', ...STAFF_TABS]) {
            $(`panel-${name}`).classList.toggle('hidden', tab !== name);
            const btn = $(`tab-${name}`);
            btn.classList.toggle('hidden', (STAFF_TABS.includes(name) && !staff) || (name === 'monitoring' && !monitoring));
            btn.className = `px-3 py-1.5 rounded-lg ${tab === name ? 'tab-active' : 'text-slate-300'}${btn.classList.contains('hidden') ? ' hidden' : ''}`;
        }
        $('mode-toggle').classList.toggle('hidden', !staff || tab !== 'assess');
        if (tab === 'models') renderModels();
        if (SKAI.staff) SKAI.staff.render(tab);
    }

    function setMode(mode) {
        state.mode = mode;
        $('mode-clinical').className = `px-3 py-1.5 rounded-lg ${mode === 'clinical' ? 'tab-active' : 'text-slate-300'}`;
        $('mode-lifestyle').className = `px-3 py-1.5 rounded-lg ${mode === 'lifestyle' ? 'tab-active' : 'text-slate-300'}`;
        $('lifestyle-optional').classList.toggle('hidden', mode !== 'lifestyle');
        renderForm();
        clearResult();
    }

    // ---------- form ----------
    function renderForm(values = {}) {
        let html = '';
        let open = false;
        for (const f of FORMS[state.mode]) {
            if (f.section) {
                if (open) html += '</div>';
                html += `<h4 class="text-[10px] font-bold text-skai-primary uppercase tracking-wider mt-4 mb-2 first:mt-0" data-i18n="${f.section}">${t(f.section)}</h4><div class="grid grid-cols-2 gap-3">`;
                open = true;
                continue;
            }
            const v = values[f.key] ?? f.value ?? '';
            const label = `<label class="field-label" for="in-${f.key}">${featureLabel(f.key)}</label>`;
            if (f.options) {
                const opts = f.options.map(([val, lab]) =>
                    `<option value="${val}" ${Number(v) === val ? 'selected' : ''}>${f.raw ? lab : t(lab)}</option>`).join('');
                html += `<div>${label}<select id="in-${f.key}" class="field">${opts}</select></div>`;
            } else {
                html += `<div>${label}<input id="in-${f.key}" type="number" class="field" min="${f.min}" max="${f.max}" step="${f.step || 1}" value="${v}" ${f.optional ? '' : 'required'}></div>`;
            }
        }
        $('form-fields').innerHTML = html + (open ? '</div>' : '');
    }

    function readInputs() {
        const inputs = {};
        for (const f of FORMS[state.mode]) {
            if (f.section) continue;
            const raw = $(`in-${f.key}`).value;
            if (raw === '') continue;               // optional and left blank
            inputs[f.key] = Number(raw);
        }
        if ('family_history' in inputs) inputs.family_history = inputs.family_history === 1;
        return inputs;
    }

    // ---------- assessment ----------
    async function assess({ save = $('p-save').checked } = {}) {
        if (!$('assess-form').reportValidity()) return;
        const inputs = readInputs();
        const body = { inputs };
        if (save) {
            const id = $('p-id').value.trim(), name = $('p-name').value.trim();
            if (!id || !name) { toast(t('need_patient'), 'warning'); return; }
            body.patient = { id, name };
            if (state.mode === 'clinical') body.patient.sex = inputs.sex === 1 ? 'M' : 'F';
        }
        const btn = $('assess-btn');
        btn.disabled = true;
        try {
            const result = await api(`/api/${state.mode}/assess`, body);
            state.result = result;
            state.inputs = inputs;
            state.assessmentId = result.assessment_id || null;
            renderResult();
            if (save) {
                toast(t('saved'), 'success');
                loadHistory(body.patient.id);
                if (state.role === 'clinician') refreshQueue();
            }
            advisorIntro();
        } catch (e) {
            toast(`${t('error_generic')} (${e.message})`, 'error');
        } finally {
            btn.disabled = false;
        }
    }

    function clearResult() {
        state.result = null;
        state.assessmentId = null;
        $('result').classList.add('hidden');
        $('result-empty').classList.remove('hidden');
        $('chat').innerHTML = '';
        addMsg(t('advisor_hello'), 'ai');
        renderChips();
    }

    function renderResult() {
        const r = state.result;
        if (!r) return;
        $('result-empty').classList.add('hidden');
        $('result').classList.remove('hidden');

        $('tier-box').className = `rounded-2xl border p-5 text-center bg-tier-${r.tier}`;
        $('risk-value').textContent = pct(r.probability);
        $('risk-value').className = `text-5xl font-black mt-1 tier-${r.tier}`;
        $('risk-tier').textContent = t(`tier_${r.tier}`);
        $('risk-tier').className = `text-sm font-bold mt-1 tier-${r.tier}`;

        const top = r.drivers.slice(0, 7);
        chart('drivers-chart', {
            type: 'bar',
            data: {
                labels: top.map(d => featureLabel(d.feature)),
                datasets: [{
                    data: top.map(d => d.impact),
                    backgroundColor: top.map(d => d.impact > 0 ? 'rgba(239,68,68,.8)' : 'rgba(14,165,233,.8)'),
                    borderRadius: 4,
                }],
            },
            options: {
                indexAxis: 'y', maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { callbacks: {
                    label: c => `${c.raw > 0 ? t('raises_risk') : t('lowers_risk')} (${c.raw > 0 ? '+' : ''}${c.raw.toFixed(3)})`,
                } } },
                scales: { x: { grid }, y: { grid: { display: false }, ticks: { font: { size: 10 } } } },
            },
        });

        const notes = [];
        if (r.estimated && r.estimated.length) notes.push(t('estimated_note', { list: r.estimated.map(featureLabel).join(', ') }));
        if (r.clipped && r.clipped.length) notes.push(t('clipped_note', { list: r.clipped.map(featureLabel).join(', ') }));
        if (r.model === 'lifestyle') notes.push(t('lifestyle_disclaimer'));
        $('result-notes').textContent = notes.join(' ');

        renderPlan();
        renderWhatIf();
        updatePdfButton();
    }

    function updatePdfButton() {
        $('pdf-btn').disabled = !state.assessmentId;
        $('pdf-hint').classList.toggle('hidden', !!state.assessmentId);
    }

    function renderPlan() {
        const r = state.result;
        const items = [];
        if (r.model === 'lifestyle') {
            $('plan-title').textContent = t('projection_title');
            $('projection').classList.remove('hidden');
            const labels = [t('now'), t('in_10y_unchanged'), ...r.plan.map(p => t(`action_${p.action}`))];
            const values = [r.probability, r.projection.probability_if_unchanged, ...r.plan.map(p => p.probability_in_10y)];
            chart('projection-chart', {
                type: 'bar',
                data: { labels, datasets: [{ data: values.map(v => +(v * 100).toFixed(1)), borderRadius: 4,
                    backgroundColor: values.map((v, i) => i === 0 ? '#64748b' : i === 1 ? '#ef4444' : '#14b8a6') }] },
                options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
                    scales: { y: { min: 0, max: 100, grid, ticks: { callback: v => `${v}%` } },
                              x: { grid: { display: false }, ticks: { font: { size: 9 }, maxRotation: 30 } } } },
            });
            r.plan.forEach(p => items.push([t(`action_${p.action}`), p.probability_in_10y, r.projection.probability_if_unchanged]));
        } else {
            $('plan-title').textContent = t('plan_title');
            $('projection').classList.add('hidden');
            r.plan.forEach(p => items.push([t(`action_${p.action}`), p.probability, r.probability]));
        }
        let html = items.map(([label, p, base]) => `
            <li class="flex items-center justify-between gap-3 bg-slate-900/60 border border-slate-800 rounded-xl px-3 py-2">
                <span class="text-slate-200"><i class="fa-solid fa-circle-check text-skai-secondary mr-2"></i>${esc(label)}</span>
                <span class="font-bold whitespace-nowrap">${pct(p)} <span class="text-emerald-400 text-xs">(${((p - base) * 100).toFixed(1)})</span></span>
            </li>`).join('');
        if (!items.length) html = `<li class="text-slate-400 text-xs">${t('plan_none')}</li>`;
        if (state.inputs && state.inputs.cigarettes_per_day > 0) html += `<li class="text-amber-300 text-xs mt-2"><i class="fa-solid fa-smoking mr-1"></i>${t('tip_smoking')}</li>`;
        $('plan').innerHTML = html;
    }

    // ---------- what-if ----------
    function renderWhatIf() {
        const sliders = WHATIF[state.mode].filter(([key]) => state.inputs[key] !== undefined);
        $('whatif-sliders').innerHTML = sliders.map(([key, min, max, step]) => `
            <div>
                <div class="flex justify-between text-xs text-slate-300 mb-1"><span>${featureLabel(key)}</span>
                    <span id="wv-${key}" class="font-bold text-skai-primary">${state.inputs[key]}</span></div>
                <input type="range" id="ws-${key}" min="${min}" max="${max}" step="${step}" value="${state.inputs[key]}" oninput="SKAI.whatIf()">
            </div>`).join('');
        $('whatif-value').textContent = pct(state.result.probability);
        $('whatif-delta').textContent = '';
    }

    function whatIf() {
        const changed = { ...state.inputs };
        for (const [key] of WHATIF[state.mode]) {
            const el = $(`ws-${key}`);
            if (!el) continue;
            changed[key] = Number(el.value);
            $(`wv-${key}`).textContent = el.value;
        }
        clearTimeout(state.whatifTimer);
        state.whatifTimer = setTimeout(async () => {
            try {
                const r = await api(`/api/${state.mode}/assess`, { inputs: changed });
                const delta = (r.probability - state.result.probability) * 100;
                $('whatif-value').textContent = pct(r.probability);
                $('whatif-value').className = `text-2xl font-black tier-${r.tier}`;
                $('whatif-delta').textContent = `${delta > 0 ? '+' : ''}${delta.toFixed(1)}`;
                $('whatif-delta').className = `text-xs font-bold ${delta > 0 ? 'text-red-400' : 'text-emerald-400'}`;
            } catch (e) { /* slider moved into an invalid combination; keep the last value */ }
        }, 200);
    }

    // ---------- patients, history, queue ----------
    async function loadHistory(patientId) {
        let patient;
        try { patient = await api(`/api/patients/${encodeURIComponent(patientId)}`); } catch { return null; }
        const points = patient.assessments.filter(a => a.kind === state.mode);
        $('history-empty').classList.toggle('hidden', points.length > 0);
        chart('history-chart', {
            type: 'line',
            data: {
                labels: points.map(a => new Date(a.created_at).toLocaleDateString(state.lang === 'en' ? 'en-ZA' : undefined)),
                datasets: [{ data: points.map(a => +(a.probability * 100).toFixed(1)), tension: 0.3, fill: true,
                    borderColor: '#0ea5e9', backgroundColor: 'rgba(14,165,233,.15)',
                    pointBackgroundColor: points.map(a => TIER_COLOUR[a.tier]), pointRadius: 5 }],
            },
            options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
                scales: { y: { min: 0, max: 100, grid, ticks: { callback: v => `${v}%` } }, x: { grid: { display: false } } } },
        });
        return patient;
    }

    async function refreshQueue() {
        try { state.queue = await api('/api/patients'); } catch { return; }
        renderQueue();
    }

    function renderQueue() {
        const flagged = state.queue.filter(q => q.flags.length).length;
        $('queue-alerts').classList.toggle('hidden', !flagged);
        $('queue-alerts').textContent = t('alerts_count', { n: flagged });
        if (!state.queue.length) { $('queue').innerHTML = `<p class="text-xs text-slate-500">${t('no_patients')}</p>`; return; }
        // Live search by name or ID
        const query = $('queue-search').value.trim().toLowerCase();
        const shown = state.queue.filter(q => !query || `${q.name} ${q.id}`.toLowerCase().includes(query));
        if (!shown.length) { $('queue').innerHTML = `<p class="text-xs text-slate-500">${t('no_match')}</p>`; return; }
        const flagStyle = { high_risk: 'bg-red-500/20 text-red-300', rising: 'bg-orange-500/20 text-orange-300',
                            follow_up_due: 'bg-amber-500/20 text-amber-300', needs_verification: 'bg-sky-500/20 text-sky-300' };
        $('queue').innerHTML = shown.map(q => {
            const change = q.change == null ? '' : `<span class="${q.change > 0 ? 'text-red-400' : 'text-emerald-400'} text-[10px] font-bold">${q.change > 0 ? '▲' : '▼'} ${Math.abs(q.change * 100).toFixed(0)}</span>`;
            const when = q.days_since === 0 ? t('today') : t('days_ago', { n: q.days_since });
            return `
            <button data-id="${esc(q.id)}" onclick="SKAI.selectPatient(this.dataset.id)" class="w-full text-left glass-card rounded-xl p-3 border hover:border-skai-primary transition ${q.flags.includes('high_risk') ? 'border-red-500/30' : 'border-slate-800'}">
                <div class="flex justify-between items-start">
                    <div><div class="font-bold text-sm text-white">${esc(q.name)}</div>
                         <div class="text-[11px] text-slate-400">${esc(q.id)} • ${t(`kind_${q.latest.kind}`)} • ${when}</div>
                         ${q.latest.created_by_name ? `<div class="text-[10px] text-slate-500"><i class="fa-solid fa-user-doctor mr-1"></i>${esc(q.latest.created_by_name)}</div>` : ''}</div>
                    <div class="text-right"><div class="font-black tier-${q.latest.tier}">${pct(q.latest.probability)}</div>${change}</div>
                </div>
                ${q.flags.length ? `<div class="flex flex-wrap gap-1 mt-2">${q.flags.map(f => `<span class="chip ${flagStyle[f]}">${t(`flag_${f}`)}</span>`).join('')}</div>` : ''}
            </button>`;
        }).join('');
    }

    async function selectPatient(id) {
        const patient = await api(`/api/patients/${encodeURIComponent(id)}`);
        const latest = patient.assessments[patient.assessments.length - 1];
        state.patient = patient;
        renderDocs();
        renderAppAccess();
        $('p-name').value = patient.name;
        $('p-id').value = patient.id;
        if (latest.kind !== state.mode) setMode(latest.kind);
        renderForm(latest.inputs);
        showTab('assess');
        await assess({ save: false });          // re-score the stored inputs to show the explanation
        state.assessmentId = latest.id;          // ...and point the PDF at the saved assessment
        updatePdfButton();
        loadHistory(id);
    }

    // ---------- clinical documents (clinician review) ----------
    const DOC_STYLE = {
        verified: ['fa-shield-halved', 'text-emerald-400'], approved: ['fa-user-check', 'text-emerald-400'],
        pending_review: ['fa-hourglass-half', 'text-amber-300'], rejected: ['fa-ban', 'text-red-400'],
    };

    function docLine(d) {
        const notes = [...d.reasons.map(r => t(`reason_${r}`)), ...d.warnings.map(w => t(`warn_${w}`))];
        const vitals = Object.entries(d.vitals).map(([k, v]) => `${t(`v_${k}`)} ${v}`).join(' · ');
        const [icon, colour] = DOC_STYLE[d.status];
        return `
            <div class="flex items-center gap-2 text-sm">
                <i class="fa-solid ${icon} ${colour}"></i>
                <span class="font-bold ${colour}">${t(`doc_${d.status}`)}</span>
                <span class="text-xs text-slate-400 truncate">${esc(d.issuer || d.filename)}${d.document_date ? ' · ' + esc(d.document_date) : ''}</span>
            </div>
            ${vitals ? `<div class="text-[11px] text-slate-300 mt-1">${esc(vitals)}</div>` : ''}
            ${notes.length ? `<div class="text-[11px] text-slate-500 mt-1">${notes.map(esc).join(' ')}</div>` : ''}`;
    }

    function renderDocs() {
        const docs = state.patient?.documents || [];
        $('docs-card').classList.toggle('hidden', !docs.length || state.role !== 'clinician');
        $('docs-list').innerHTML = docs.map(d => `
            <li class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                ${docLine(d)}
                <div class="flex flex-wrap gap-2 mt-2 text-xs">
                    <button onclick="SKAI.viewDocument(${d.id})" class="px-2 py-1 rounded-lg bg-slate-800 border border-slate-600 hover:border-skai-primary"><i class="fa-solid fa-eye mr-1"></i>${t('doc_view')}</button>
                    ${d.status === 'pending_review' ? `
                    <button onclick="SKAI.reviewDocument(${d.id}, true)" class="on-accent px-2 py-1 rounded-lg bg-emerald-600 text-white font-bold"><i class="fa-solid fa-check mr-1"></i>${t('doc_approve')}</button>
                    <button onclick="SKAI.reviewDocument(${d.id}, false)" class="on-accent px-2 py-1 rounded-lg bg-red-600 text-white font-bold"><i class="fa-solid fa-xmark mr-1"></i>${t('doc_reject')}</button>` : ''}
                </div>
            </li>`).join('');
    }

    async function reviewDocument(id, approve) {
        try {
            await api(`/api/documents/${id}/review`, { approve });
            toast(t('doc_reviewed'), 'success');
            state.patient = await api(`/api/patients/${encodeURIComponent(state.patient.id)}`);
            renderDocs();
            refreshQueue();
        } catch (e) { toast(`${t('error_generic')} (${e.message})`, 'error'); }
    }

    function newPatient() {
        state.patient = null;
        renderDocs();
        renderAppAccess();
        $('p-name').value = '';
        $('p-id').value = `P-${Math.floor(1000 + Math.random() * 9000)}`;
        setMode(state.mode);
        showTab('assess');
        $('p-name').focus();
    }

    // When a known patient ID is typed, fill in the name and show their history
    function watchPatientId() {
        $('p-id').addEventListener('input', () => {
            clearTimeout(state.idTimer);
            state.idTimer = setTimeout(async () => {
                const id = $('p-id').value.trim();
                if (!id) return;
                const p = await loadHistory(id);
                if (p && !$('p-name').value) $('p-name').value = p.name;
            }, 400);
        });
    }

    function downloadPdf() {
        if (state.assessmentId) downloadReport(state.assessmentId);
    }

    // Reports are private: fetch with the current session, then save the file
    async function downloadReport(assessmentId) {
        const res = await fetch(`${API_BASE}/api/assessments/${assessmentId}/report.pdf`, { headers: authHeader() });
        if (!res.ok) { toast(t('error_generic'), 'error'); return; }
        const name = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
        const link = document.createElement('a');
        link.href = URL.createObjectURL(await res.blob());
        link.download = name ? name[1] : 'SKAI_report.pdf';
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 5000);
    }

    // ---------- patient app access (clinic-registered patients) ----------
    function renderAppAccess() {
        const p = state.patient;
        $('app-access').classList.toggle('hidden', !p || state.role !== 'clinician');
        if (!p) return;
        $('app-access-status').className = `chip ${p.has_account ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-800 text-slate-400'}`;
        $('app-access-status').textContent = t(p.has_account ? 'code_has_account' : 'code_no_account');
        $('btn-book').classList.toggle('hidden', !SKAI.staff.can('book'));
        $('btn-refer').classList.toggle('hidden', !SKAI.staff.can('refer'));
        $('btn-outcome').classList.toggle('hidden', !SKAI.staff.can('record_outcome'));
        $('btn-labs').classList.toggle('hidden', !SKAI.staff.can('assess'));
    }

    function patientAction(action) {
        const p = state.patient;
        ({ outcome: () => SKAI.staff.openOutcome(p.id, p.name), fhir: () => SKAI.staff.exportFhir(p.id),
           labs: () => SKAI.staff.openFhirImport(p.id, p.name) })[action]();
    }

    function bookPatient() { SKAI.staff.openBook(state.patient.id, state.patient.name); }
    function referPatient() {
        const latest = state.patient.assessments[state.patient.assessments.length - 1];
        SKAI.staff.openRefer(state.patient.id, state.patient.name, latest ? `${pct(latest.probability)} (${latest.tier})` : '');
    }

    async function issueActivationCode() {
        if (!state.patient) return;
        try {
            const r = await api(`/api/patients/${encodeURIComponent(state.patient.id)}/activation-code`, {});
            const expires = new Date(r.expires_at).toLocaleString(state.lang === 'en' ? 'en-ZA' : undefined,
                { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
            modal(`
                <div class="text-center">
                    <i class="fa-solid fa-mobile-screen-button text-3xl text-skai-secondary mb-2"></i>
                    <h3 class="text-lg font-extrabold text-white">${t('code_title', { name: esc(state.patient.name) })}</h3>
                    <p class="text-sm text-slate-300 mt-2">${t('code_text')}</p>
                    <p class="text-4xl font-black tracking-[0.35em] text-white my-4">${r.code}</p>
                    <p class="text-xs text-slate-400">${t('patient_id')}: <b>${esc(state.patient.id)}</b> · ${t('code_expires', { date: expires })}</p>
                    <button onclick="SKAI.closeModal()" class="mt-5 w-full bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">${t('pt_close')}</button>
                </div>`);
        } catch (e) { toast(`${t('error_generic')} (${e.message})`, 'error'); }
    }

    // ---------- offline advisor ----------
    function addMsg(html, who) {
        const el = document.createElement('div');
        el.className = who === 'user'
            ? 'ml-auto max-w-[85%] bg-slate-800 border border-slate-700 rounded-xl rounded-tr-none px-3 py-2'
            : 'max-w-[90%] bg-teal-900/30 border border-teal-800/50 rounded-xl rounded-tl-none px-3 py-2 text-slate-200';
        el.innerHTML = html;
        $('chat').appendChild(el);
        $('chat').scrollTop = $('chat').scrollHeight;
    }

    function renderChips() {
        $('chat-chips').innerHTML = ['why', 'lower', 'doctor'].map(q =>
            `<button onclick="SKAI.ask('${q}')" class="chip bg-slate-800 border border-slate-600 text-slate-200 hover:border-skai-secondary">${t(`q_${q}`)}</button>`).join('');
    }

    function advisorIntro() {
        $('chat').innerHTML = '';
        addMsg(answer('why'), 'ai');
        renderChips();
    }

    function answer(intent) {
        const r = state.result;
        if (!r) return t('a_no_result');
        const tier = t(`tier_${r.tier}`);
        if (intent === 'why') {
            const up = r.drivers.filter(d => d.impact > 0).slice(0, 3).map(d => `<b>${featureLabel(d.feature)}</b>`);
            const down = r.drivers.filter(d => d.impact < 0).slice(0, 2).map(d => `<b>${featureLabel(d.feature)}</b>`);
            let s = t('a_why', { p: pct(r.probability), tier, up: up.join(', ') || '-' });
            if (down.length) s += ' ' + t('a_why_down', { down: down.join(', ') });
            return s;
        }
        if (intent === 'lower') {
            if (!r.plan.length) return t('plan_none');
            const key = r.model === 'lifestyle' ? 'probability_in_10y' : 'probability';
            return t(r.model === 'lifestyle' ? 'a_lower_10y' : 'a_lower', {
                plan: r.plan.map(p => `<b>${t(`action_${p.action}`)}</b> → ${pct(p[key])}`).join('; '),
            });
        }
        return ['high', 'critical'].includes(r.tier) ? t('a_doctor_urgent', { tier }) : t('a_doctor_routine', { tier });
    }

    function detectIntent(text) {
        const s = text.toLowerCase();
        const matches = (key, words) => words.some(w => s.includes(w)) || s.includes(t(`q_${key}`).toLowerCase().slice(0, 6));
        if (matches('lower', ['lower', 'reduce', 'improve', 'plan', 'better', 'prevent'])) return 'lower';
        if (matches('doctor', ['doctor', 'clinic', 'hospital', 'urgent', 'when', 'nurse'])) return 'doctor';
        if (matches('why', ['why', 'reason', 'cause', 'explain', 'driv', 'mean'])) return 'why';
        return null;
    }

    function ask(intent) {
        const input = $('chat-input');
        const text = intent ? t(`q_${intent}`) : input.value.trim();
        if (!text) return;
        addMsg(esc(text), 'user');
        input.value = '';
        const found = intent || detectIntent(text);
        setTimeout(() => addMsg(found ? answer(found) : t('a_fallback'), 'ai'), 250);
    }

    // ---------- voice input (Web Speech API; Chrome/Edge) ----------
    const SPEECH_LANG = {
        en: 'en-ZA', af: 'af-ZA', zu: 'zu-ZA', xh: 'xh-ZA', nr: 'nr-ZA', nso: 'nso-ZA',
        st: 'st-ZA', tn: 'tn-ZA', ss: 'ss-ZA', ve: 've-ZA', ts: 'ts-ZA',
    };
    let recognizer = null;

    function toggleVoice() {
        const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!Recognition) { toast(t('voice_unsupported'), 'warning'); return; }
        if (recognizer) { recognizer.stop(); return; }

        recognizer = new Recognition();
        recognizer.lang = SPEECH_LANG[state.lang] || 'en-ZA';
        recognizer.interimResults = false;
        recognizer.maxAlternatives = 1;
        recognizer.onstart = () => { $('mic-btn').classList.add('listening'); toast(t('voice_listening'), 'info'); };
        recognizer.onresult = e => {
            $('chat-input').value = e.results[0][0].transcript;
            ask();                                   // send the spoken question straight to the advisor
        };
        recognizer.onerror = e => {
            if (e.error === 'not-allowed' || e.error === 'service-not-allowed') toast(t('voice_denied'), 'error');
            else if (e.error === 'language-not-supported') toast(t('voice_lang_unsupported'), 'warning');
            else if (e.error !== 'no-speech' && e.error !== 'aborted') toast(t('voice_error'), 'error');
        };
        recognizer.onend = () => { $('mic-btn').classList.remove('listening'); recognizer = null; };
        recognizer.start();
    }

    // ---------- model & bias page ----------
    async function renderModels() {
        if (!state.modelInfo) {
            try { state.modelInfo = await api('/api/model-info'); } catch (e) { toast(t('error_generic'), 'error'); return; }
        }
        const m = state.modelInfo;
        const metric = (label, v) => `<div class="bg-slate-900/60 rounded-xl p-3 border border-slate-800"><div class="text-[10px] text-slate-400 uppercase font-bold">${label}</div><div class="text-2xl font-black text-white">${(v * 100).toFixed(1)}%</div></div>`;
        const modelCard = (key, icon, colour) => {
            const x = m[key].test_metrics;
            return `<div class="glass-card rounded-2xl p-5">
                <h3 class="font-bold text-white"><i class="fa-solid ${icon} ${colour} mr-2"></i>${t(`model_${key}`)}</h3>
                <p class="text-[11px] text-slate-400 mb-3">${esc(m[key].model)} · ${esc(m[key].dataset)}</p>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">${metric(t('metric_recall'), x.recall)}${metric(t('metric_precision'), x.precision)}${metric(t('metric_auc'), x.roc_auc)}${metric(t('metric_accuracy'), x.accuracy)}</div>
            </div>`;
        };
        const groups = [['uci_sex', 'bias_by_sex'], ['uci_age', 'bias_by_age_clinical'], ['sa_age', 'bias_by_age_lifestyle'], ['sa_family_history', 'bias_by_family']];
        $('panel-models').innerHTML = `
            <div><h2 class="text-2xl font-extrabold text-white">${t('models_title')}</h2><p class="text-sm text-slate-400">${t('models_intro')}</p></div>
            <div class="grid lg:grid-cols-2 gap-5">${modelCard('clinical', 'fa-stethoscope', 'text-skai-primary')}${modelCard('lifestyle', 'fa-leaf', 'text-skai-secondary')}</div>
            <div class="glass-card rounded-2xl p-5">
                <h3 class="font-bold text-white mb-1">${t('bias_title')}</h3>
                <div class="grid sm:grid-cols-2 xl:grid-cols-4 gap-4 mt-3">
                    ${groups.map(([g, label]) => `<div><p class="text-xs font-bold text-slate-300 mb-1">${t(label)}</p><div class="relative h-44"><canvas id="bias-${g}"></canvas></div></div>`).join('')}
                </div>
            </div>
            <div class="glass-card rounded-2xl p-5">
                <h3 class="font-bold text-white mb-2">${t('bias_findings_title')}</h3>
                <ul class="space-y-2 text-sm text-slate-300 list-disc pl-5">${[1, 2, 3, 4, 5].map(i => `<li>${t(`bias_f${i}`)}</li>`).join('')}</ul>
            </div>`;
        for (const [g] of groups) {
            const rows = m.subgroups[g] || [];
            chart(`bias-${g}`, {
                type: 'bar',
                data: { labels: rows.map(r => r.group), datasets: [
                    { label: t('bias_recall'), data: rows.map(r => r.recall), backgroundColor: '#0ea5e9', borderRadius: 3 },
                    { label: t('bias_fpr'), data: rows.map(r => r.fpr), backgroundColor: '#f59e0b', borderRadius: 3 },
                ] },
                options: { maintainAspectRatio: false, plugins: { legend: { labels: { boxWidth: 10, font: { size: 9 } } } },
                    scales: { y: { min: 0, max: 1, grid }, x: { grid: { display: false } } } },
            });
        }
    }

    // ---------- language ----------
    function setLang(lang) {
        state.lang = window.SKAI_I18N[lang] ? lang : 'en';
        document.documentElement.lang = state.lang;
        document.querySelectorAll('.lang-select').forEach(s => { s.value = state.lang; });
        document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
        document.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
        try { localStorage.setItem('skai-lang', state.lang); } catch (e) { /* private mode */ }
        // Re-render everything built in JS so it picks up the new language
        rerender();
    }

    function init() {
        const options = window.SKAI_LANGUAGES.map(l => `<option value="${l.code}">${l.label}</option>`).join('');
        document.querySelectorAll('.lang-select').forEach(sel => {
            sel.innerHTML = options;
            sel.addEventListener('change', () => { setLang(sel.value); toast(t('lang_changed'), 'info'); });
        });
        applyTheme(document.documentElement.dataset.theme || 'dark');
        let saved = 'en';
        try { saved = localStorage.getItem('skai-lang') || 'en'; } catch (e) { /* private mode */ }
        setLang(saved);
        watchPatientId();
        checkServer();
    }

    // Without the API nothing works, so say so plainly instead of failing silently
    async function checkServer() {
        try {
            const res = await fetch(`${API_BASE}/api/health`);
            if (res.ok) return;
        } catch (e) { /* not running */ }
        const banner = document.createElement('div');
        banner.className = 'fixed bottom-4 left-1/2 -translate-x-1/2 z-[100] bg-red-600 text-white text-sm font-bold px-4 py-3 rounded-xl shadow-2xl max-w-[92vw]';
        banner.innerHTML = '<i class="fa-solid fa-plug-circle-xmark mr-2"></i>The SKAI server is not running. In a terminal, run <code class="bg-red-800 px-1 rounded">python run_app.py</code> and then refresh this page.';
        document.body.appendChild(banner);
    }

    document.addEventListener('DOMContentLoaded', init);

    // Shared helpers for patient.js
    const core = {
        state, api, t, toast, chart, pct, esc, featureLabel, grid, API_BASE, TIER_COLOUR, docLine,
        modal, closeModal, resizeImage, patientSession, authHeader, downloadReport,
        onRerender: fn => rerenderHooks.push(fn),
        // Point the shared advisor at a patient's result
        setResult(result) { state.result = result; state.inputs = result ? result.inputs : null; if (result) advisorIntro(); else clearResult(); },
    };

    return { core, enter, home, showTab, bookPatient, referPatient, patientAction, setMode, assess, whatIf, selectPatient, newPatient, downloadPdf, ask, toggleVoice,
             toggleTheme, reviewDocument, filterQueue: renderQueue,
             login, logout, fillLogin, togglePassword, toggleDoctorMenu, doctorPhoto, openPasswordChange,
             changePassword, openActivity, viewDocument, closeModal, issueActivationCode,
             showModels: () => enter('viewer', 'models') };
})();
