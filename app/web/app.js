// SKAI web app. Every number shown comes from the API (app/api.py), which runs
// the trained models; nothing here is simulated.
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
        let s = (dict[state.lang] && dict[state.lang][key]) || dict.en[key] || key;
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

    async function api(path, body) {
        const res = await fetch(path, body ? {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        } : undefined);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            const detail = Array.isArray(err.detail) ? err.detail.map(d => d.msg).join('; ') : err.detail;
            throw new Error(detail || res.statusText);
        }
        return res.json();
    }

    function chart(id, config) {
        if (state.charts[id]) state.charts[id].destroy();
        state.charts[id] = new Chart($(id), config);
    }
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.font.family = '"Plus Jakarta Sans", sans-serif';
    const grid = { color: 'rgba(51, 65, 85, 0.35)' };

    // ---------- navigation ----------
    function enter(role, tab = 'assess') {
        state.role = role;
        $('view-home').classList.add('hidden');
        $('view-work').classList.remove('hidden');
        $('view-work').classList.add('flex');
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
        setMode(clinician ? 'clinical' : 'lifestyle');
        if (clinician) refreshQueue();
        showTab(tab);
    }

    function home() {
        $('view-work').classList.add('hidden');
        $('view-work').classList.remove('flex');
        $('view-home').classList.remove('hidden');
    }

    function showTab(tab) {
        $('panel-assess').classList.toggle('hidden', tab !== 'assess');
        $('panel-models').classList.toggle('hidden', tab !== 'models');
        $('tab-assess').className = `px-3 py-1.5 rounded-lg ${tab === 'assess' ? 'tab-active' : 'text-slate-300'}`;
        $('tab-models').className = `px-3 py-1.5 rounded-lg ${tab === 'models' ? 'tab-active' : 'text-slate-300'}`;
        if (tab === 'models') renderModels();
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
        const flagStyle = { high_risk: 'bg-red-500/20 text-red-300', rising: 'bg-orange-500/20 text-orange-300', follow_up_due: 'bg-amber-500/20 text-amber-300' };
        $('queue').innerHTML = shown.map(q => {
            const change = q.change == null ? '' : `<span class="${q.change > 0 ? 'text-red-400' : 'text-emerald-400'} text-[10px] font-bold">${q.change > 0 ? '▲' : '▼'} ${Math.abs(q.change * 100).toFixed(0)}</span>`;
            const when = q.days_since === 0 ? t('today') : t('days_ago', { n: q.days_since });
            return `
            <button data-id="${esc(q.id)}" onclick="SKAI.selectPatient(this.dataset.id)" class="w-full text-left glass-card rounded-xl p-3 border hover:border-skai-primary transition ${q.flags.includes('high_risk') ? 'border-red-500/30' : 'border-slate-800'}">
                <div class="flex justify-between items-start">
                    <div><div class="font-bold text-sm text-white">${esc(q.name)}</div>
                         <div class="text-[11px] text-slate-400">${esc(q.id)} • ${t(`kind_${q.latest.kind}`)} • ${when}</div></div>
                    <div class="text-right"><div class="font-black tier-${q.latest.tier}">${pct(q.latest.probability)}</div>${change}</div>
                </div>
                ${q.flags.length ? `<div class="flex flex-wrap gap-1 mt-2">${q.flags.map(f => `<span class="chip ${flagStyle[f]}">${t(`flag_${f}`)}</span>`).join('')}</div>` : ''}
            </button>`;
        }).join('');
    }

    async function selectPatient(id) {
        const patient = await api(`/api/patients/${encodeURIComponent(id)}`);
        const latest = patient.assessments[patient.assessments.length - 1];
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

    function newPatient() {
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
        if (state.assessmentId) window.location.href = `/api/assessments/${state.assessmentId}/report.pdf`;
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
                    ${groups.map(([g, label]) => `<div><p class="text-xs font-bold text-slate-300 mb-1">${t(label)}</p><div class="h-44"><canvas id="bias-${g}"></canvas></div></div>`).join('')}
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
        try { localStorage.setItem('skai-lang', state.lang); } catch (e) { /* private mode */ }
        // Re-render everything built in JS so it picks up the new language
        if (state.role) {
            const values = readInputs();
            renderForm(values);
            if (state.result) { renderResult(); advisorIntro(); } else clearResult();
            renderQueue();
            if (!$('panel-models').classList.contains('hidden')) renderModels();
        }
    }

    function init() {
        const options = window.SKAI_LANGUAGES.map(l => `<option value="${l.code}">${l.label}</option>`).join('');
        document.querySelectorAll('.lang-select').forEach(sel => {
            sel.innerHTML = options;
            sel.addEventListener('change', () => { setLang(sel.value); toast(t('lang_changed'), 'info'); });
        });
        let saved = 'en';
        try { saved = localStorage.getItem('skai-lang') || 'en'; } catch (e) { /* private mode */ }
        setLang(saved);
        watchPatientId();
    }

    document.addEventListener('DOMContentLoaded', init);

    return { enter, home, showTab, setMode, assess, whatIf, selectPatient, newPatient, downloadPdf, ask, toggleVoice,
             filterQueue: renderQueue,
             showModels: () => enter('patient', 'models') };
})();
