// SKAI Health: the patient's personal health tracker.
//
// Patients never type their own vitals. They upload the document their clinic,
// pharmacy clinic or lab gave them; the API verifies it and reads the values
// (app/documents.py). The only things a patient answers themselves are the ones
// no clinic document records: smoking and family history.
SKAI.patient = (() => {
    const { state, api, t, toast, chart, pct, esc, featureLabel, grid, API_BASE, TIER_COLOUR, docLine,
            modal, closeModal, resizeImage, patientSession, authHeader, downloadReport } = SKAI.core;
    const $ = id => document.getElementById(id);
    const enc = encodeURIComponent;

    let data = null;            // the /dashboard response
    let pid = null;
    let mood = null;
    let editingCheckin = false;
    let whatifTimer = null;
    const shownPopups = new Set();

    const MOODS = ['😞', '🙁', '😐', '🙂', '😄'];
    const LEVEL = {
        urgent: ['fa-triangle-exclamation', 'text-red-400', 'border-red-500/50'],
        warning: ['fa-circle-exclamation', 'text-amber-300', 'border-amber-500/40'],
        tip: ['fa-lightbulb', 'text-sky-400', 'border-sky-500/30'],
        good: ['fa-circle-check', 'text-emerald-400', 'border-emerald-500/30'],
    };
    // Healthy-range checks for the vitals tiles (AHA BP stages, WHO BMI bands, common lab ranges)
    const STATUS = {
        bp: v => (v.sbp < 120 ? 'good' : v.sbp < 140 ? 'watch' : 'high'),
        heart_rate: x => (x >= 60 && x <= 100 ? 'good' : 'watch'),
        ldl: x => (x < 3 ? 'good' : x < 4.1 ? 'watch' : 'high'),
        total_cholesterol: x => (x < 5.2 ? 'good' : x < 6.2 ? 'watch' : 'high'),
        glucose: x => (x < 5.6 ? 'good' : x < 7 ? 'watch' : 'high'),
        bmi: x => (x >= 18.5 && x < 25 ? 'good' : x < 30 ? 'watch' : 'high'),
    };
    const fmtDate = d => new Date(d).toLocaleDateString(state.lang === 'en' ? 'en-ZA' : undefined,
                                                         { day: 'numeric', month: 'short', year: 'numeric' });

    // ---------- sign in / profile ----------
    async function open() {
        if (patientSession.token) {
            try {
                const me = await api('/api/patient-auth/me');
                if (await load(me.id)) return;
            } catch (e) { patientSession.clear(); }
        }
        showSignIn();
    }

    function showForm(which) {
        $('pt-create').classList.toggle('hidden', which !== 'create');
        $('pt-activate').classList.toggle('hidden', which !== 'activate');
        $('pt-tab-create').className = `py-2 rounded-xl ${which === 'create' ? 'tab-active' : 'bg-slate-800 border border-slate-700 text-slate-300'}`;
        $('pt-tab-activate').className = `py-2 rounded-xl ${which === 'activate' ? 'tab-active' : 'bg-slate-800 border border-slate-700 text-slate-300'}`;
    }

    function formError(id, message) {
        $(id).textContent = message;
        $(id).classList.toggle('hidden', !message);
    }

    const AUTH_ERRORS = { login_failed: 'pt_login_failed', locked: 'login_locked', id_taken: 'pt_id_taken', code_invalid: 'pt_code_invalid' };
    const authError = e => (AUTH_ERRORS[e.message] ? t(AUTH_ERRORS[e.message]) : `${t('error_generic')} (${e.message})`);

    async function startSession(r) {
        patientSession.set(r.token);
        ['pt-login-pass', 'pt-new-pass', 'pt-new-pass2', 'pt-act-pass', 'pt-act-pass2', 'pt-act-code'].forEach(id => { $(id).value = ''; });
        await load(r.profile.id);
    }

    function sessionExpired() {
        patientSession.clear();
        toast(t('session_expired'), 'warning');
        showSignIn();
    }

    function showSignIn() {
        data = null;
        $('pt-signin').classList.remove('hidden');
        $('pt-dash').classList.add('hidden');
        $('pt-logout').classList.add('hidden');
        SKAI.core.setResult(null);
    }

    async function signIn() {
        if (!$('pt-login-form').reportValidity()) return;
        formError('pt-login-error', '');
        try {
            await startSession(await api('/api/patient-auth/login',
                { patient_id: $('pt-login-id').value.trim(), password: $('pt-login-pass').value }));
        } catch (e) { formError('pt-login-error', authError(e)); }
    }

    async function create() {
        if (!$('pt-create').reportValidity()) return;
        if ($('pt-new-pass').value !== $('pt-new-pass2').value) { formError('pt-create-error', t('pt_password_mismatch')); return; }
        formError('pt-create-error', '');
        try {
            await startSession(await api('/api/patient-auth/register', {
                patient_id: $('pt-new-id').value.trim(), name: $('pt-new-name').value.trim(),
                dob: $('pt-new-dob').value, sex: $('pt-new-sex').value, password: $('pt-new-pass').value,
            }));
            toast(t('pt_created'), 'success');
        } catch (e) { formError('pt-create-error', authError(e)); }
    }

    async function activate() {
        if (!$('pt-activate').reportValidity()) return;
        if ($('pt-act-pass').value !== $('pt-act-pass2').value) { formError('pt-activate-error', t('pt_password_mismatch')); return; }
        formError('pt-activate-error', '');
        try {
            await startSession(await api('/api/patient-auth/activate', {
                patient_id: $('pt-act-id').value.trim(), code: $('pt-act-code').value.trim(), password: $('pt-act-pass').value,
            }));
            toast(t('pt_activated'), 'success');
        } catch (e) { formError('pt-activate-error', authError(e)); }
    }

    async function logout() {
        try { await api('/api/patient-auth/logout', {}); } catch (e) { /* already ended */ }
        patientSession.clear();
        $('advice-pop').classList.add('hidden');
        showSignIn();
    }

    async function load(id) {
        try { data = await api(`/api/patients/${enc(id)}/dashboard`); } catch (e) { return false; }
        pid = id;
        $('pt-signin').classList.add('hidden');
        $('pt-dash').classList.remove('hidden');
        $('pt-logout').classList.remove('hidden');
        render();
        showPopup();
        return true;
    }

    const reload = () => load(pid);

    // ---------- render ----------
    function render() {
        if (!data) return;
        renderHero();
        renderRisk();
        renderTips();
        renderVitals();
        renderTrend();
        renderCheckin();
        renderMeds();
        renderActivity();
        renderDocs();
        SKAI.core.setResult(data.result);
    }

    function avatarHtml(p) {
        if (p.photo) return `<img src="${esc(p.photo)}" alt="" class="avatar border-4 border-skai-secondary">`;
        const initials = p.name.split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
        return `<div class="avatar on-accent bg-gradient-to-br from-skai-secondary to-emerald-500 flex items-center justify-center text-3xl font-black text-white">${esc(initials)}</div>`;
    }

    function renderHero() {
        const p = data.profile;
        $('pt-avatar').innerHTML = avatarHtml(p);
        const hour = new Date().getHours();
        const greet = hour < 12 ? 'pt_morning' : hour < 18 ? 'pt_afternoon' : 'pt_evening';
        $('pt-greeting').textContent = t(greet, { name: p.name.split(/\s+/)[0] });
        $('pt-subtitle').textContent = `${new Date().toLocaleDateString(state.lang === 'en' ? 'en-ZA' : undefined,
            { weekday: 'long', day: 'numeric', month: 'long' })} · ${t('pt_age', { n: p.age })}`;

        const badges = [];
        if (data.streak) badges.push(`<span class="chip bg-orange-500/20 text-orange-300">🔥 ${t('pt_streak', { n: data.streak })}</span>`);
        if (data.next_checkup) badges.push(`<span class="chip bg-sky-500/20 text-sky-300"><i class="fa-regular fa-calendar mr-1"></i>${t('pt_next_checkup', { date: fmtDate(data.next_checkup) })}</span>`);
        if (data.vitals_source) {
            const ok = data.vitals_source.status !== 'pending_review';
            badges.push(`<span class="chip ${ok ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-300'}">
                <i class="fa-solid ${ok ? 'fa-shield-halved' : 'fa-hourglass-half'} mr-1"></i>${t(`doc_${data.vitals_source.status}`)}</span>`);
        }
        $('pt-badges').innerHTML = badges.join('');
    }

    function renderRisk() {
        const r = data.result;
        if (!r) {
            const hasDoc = !!data.vitals_source;
            $('pt-risk').innerHTML = `
                <div class="text-center py-8">
                    <i class="fa-solid ${hasDoc ? 'fa-clipboard-question' : 'fa-file-arrow-up'} text-4xl text-skai-secondary mb-3"></i>
                    <p class="text-sm text-slate-300 max-w-md mx-auto">${t(hasDoc ? 'pt_no_check' : 'pt_no_doc_risk')}</p>
                    <button onclick="SKAI.patient.${hasDoc ? 'openSelfCheck' : 'openUpload'}()" class="on-accent mt-4 bg-skai-secondary hover:bg-teal-500 text-white text-sm font-bold px-4 py-2.5 rounded-xl">
                        ${t(hasDoc ? 'pt_check_btn' : 'pt_upload_btn')}</button>
                </div>`;
            return;
        }
        const src = r.vitals_source;
        const sliders = [['sbp', 100, 200, 1], ['weight_kg', 40, 160, 1], ['cigarettes_per_day', 0, 40, 1]]
            .filter(([k]) => r.inputs[k] !== undefined);
        $('pt-risk').innerHTML = `
            <div class="grid md:grid-cols-5 gap-5">
                <div class="md:col-span-2 space-y-3 min-w-0">
                    <div class="rounded-2xl border p-5 text-center bg-tier-${r.tier}">
                        <p class="text-[10px] uppercase tracking-wider text-slate-300 font-bold">${t('risk_estimate')}</p>
                        <p class="text-5xl font-black mt-1 tier-${r.tier}">${pct(r.probability)}</p>
                        <p class="text-sm font-bold tier-${r.tier}">${t(`tier_${r.tier}`)}</p>
                        <p class="text-[11px] text-slate-400 mt-2">${t('pt_assessed_on', { date: fmtDate(r.created_at || Date.now()) })}</p>
                    </div>
                    ${src ? `<p class="text-[11px] text-slate-400"><i class="fa-solid fa-shield-halved text-emerald-400 mr-1"></i>${t('pt_source', { issuer: esc(src.issuer || src.filename), date: src.document_date ? fmtDate(src.document_date) : '?' })} · ${t(`doc_${src.status}`)}</p>` : ''}
                    <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                        <p class="text-xs font-bold text-white">${t('pt_explore')}</p>
                        <p class="text-[10px] text-slate-500 mb-2">${t('pt_whatif_note')}</p>
                        ${sliders.map(([k, min, max, step]) => `
                            <div class="mb-2"><div class="flex justify-between text-[11px] text-slate-300"><span>${featureLabel(k)}</span><span id="pw-v-${k}" class="font-bold text-skai-secondary">${r.inputs[k]}</span></div>
                            <input type="range" id="pw-${k}" min="${min}" max="${max}" step="${step}" value="${r.inputs[k]}" oninput="SKAI.patient.whatIf()"></div>`).join('')}
                        <div class="flex justify-between items-baseline text-xs border-t border-slate-700/50 pt-2">
                            <span class="text-slate-400">${t('whatif_result')}</span>
                            <span><b id="pw-value" class="text-lg">${pct(r.probability)}</b> <span id="pw-delta" class="font-bold"></span></span>
                        </div>
                    </div>
                </div>
                <div class="md:col-span-3 space-y-4 min-w-0">
                    <div><p class="text-xs font-bold text-slate-300 mb-1">${t('drivers_title')}</p><div class="relative h-44"><canvas id="pt-drivers-chart"></canvas></div></div>
                    <div><p class="text-xs font-bold text-slate-300 mb-1">${t('projection_title')}</p><div class="relative h-40"><canvas id="pt-projection-chart"></canvas></div></div>
                    <ul class="space-y-1.5 text-xs">${planItems(r)}</ul>
                    <div class="flex flex-wrap items-center gap-3">
                        <button onclick="SKAI.core.downloadReport(${r.assessment_id})" class="text-xs bg-slate-800 hover:bg-slate-700 border border-slate-600 font-bold px-3 py-2 rounded-xl"><i class="fa-solid fa-file-pdf text-red-400 mr-1"></i>${t('download_pdf')}</button>
                        <span class="text-[10px] text-slate-500">${t('lifestyle_disclaimer')}</span>
                    </div>
                </div>
            </div>`;

        const top = r.drivers.filter(d => Math.abs(d.impact) > 0.01).slice(0, 6);
        chart('pt-drivers-chart', {
            type: 'bar',
            data: { labels: top.map(d => featureLabel(d.feature)), datasets: [{ data: top.map(d => d.impact), borderRadius: 4,
                backgroundColor: top.map(d => (d.impact > 0 ? 'rgba(239,68,68,.8)' : 'rgba(20,184,166,.8)')) }] },
            options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: {
                label: c => (c.raw > 0 ? t('raises_risk') : t('lowers_risk')) } } },
                scales: { x: { grid }, y: { grid: { display: false }, ticks: { font: { size: 10 } } } } },
        });
        const labels = [t('now'), t('in_10y_unchanged'), ...r.plan.map(p => t(`action_${p.action}`))];
        const values = [r.probability, r.projection.probability_if_unchanged, ...r.plan.map(p => p.probability_in_10y)];
        chart('pt-projection-chart', {
            type: 'bar',
            data: { labels, datasets: [{ data: values.map(v => +(v * 100).toFixed(1)), borderRadius: 4,
                backgroundColor: values.map((v, i) => (i === 0 ? '#64748b' : i === 1 ? '#ef4444' : '#14b8a6')) }] },
            options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
                scales: { y: { min: 0, max: 100, grid, ticks: { callback: v => `${v}%` } },
                          x: { grid: { display: false }, ticks: { font: { size: 9 }, maxRotation: 30 } } } },
        });
    }

    function planItems(r) {
        const base = r.projection.probability_if_unchanged;
        let html = r.plan.map(p => `
            <li class="flex justify-between gap-2 bg-slate-900/60 border border-slate-800 rounded-lg px-3 py-1.5">
                <span><i class="fa-solid fa-circle-check text-skai-secondary mr-1"></i>${esc(t(`action_${p.action}`))}</span>
                <span class="font-bold whitespace-nowrap">${pct(p.probability_in_10y)} <span class="text-emerald-400">(${((p.probability_in_10y - base) * 100).toFixed(1)})</span></span>
            </li>`).join('');
        if (!r.plan.length) html = `<li class="text-slate-400">${t('plan_none')}</li>`;
        if (r.inputs.cigarettes_per_day > 0) html += `<li class="text-amber-300"><i class="fa-solid fa-smoking mr-1"></i>${t('tip_smoking')}</li>`;
        return html;
    }

    function whatIf() {
        const r = data.result;
        const inputs = { ...r.inputs };
        delete inputs.document_id;
        delete inputs.vitals_status;
        document.querySelectorAll('[id^="pw-"][type="range"]').forEach(el => {
            const key = el.id.slice(3);
            inputs[key] = Number(el.value);
            $(`pw-v-${key}`).textContent = el.value;
        });
        clearTimeout(whatifTimer);
        whatifTimer = setTimeout(async () => {
            try {
                const res = await api('/api/lifestyle/assess', { inputs });
                const delta = (res.probability - r.probability) * 100;
                $('pw-value').textContent = pct(res.probability);
                $('pw-value').className = `text-lg tier-${res.tier}`;
                $('pw-delta').textContent = `${delta > 0 ? '+' : ''}${delta.toFixed(1)}`;
                $('pw-delta').className = `font-bold ${delta > 0 ? 'text-red-400' : 'text-emerald-400'}`;
            } catch (e) { /* out-of-range combination; keep last value */ }
        }, 200);
    }

    // ---------- advice ----------
    function adviceText(a) {
        return t(`advice_${a.key}`, a.params || {});
    }

    function renderTips() {
        $('pt-tips').innerHTML = data.advice.map(a => {
            const [icon, colour] = LEVEL[a.level];
            return `<li class="flex gap-2 text-xs bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                <i class="fa-solid ${icon} ${colour} mt-0.5"></i><span class="text-slate-200">${esc(adviceText(a))}</span></li>`;
        }).join('') || `<li class="text-xs text-slate-500">-</li>`;
    }

    // Urgent advice opens a modal; otherwise the most important tip pops up once per visit
    function showPopup() {
        const urgent = data.advice.find(a => a.level === 'urgent');
        if (urgent && !shownPopups.has(`${pid}:urgent`)) {
            shownPopups.add(`${pid}:urgent`);
            urgentModal();
            return;
        }
        const tip = data.advice.find(a => a.level !== 'good' && !shownPopups.has(`${pid}:${a.key}`));
        if (!tip) return;
        shownPopups.add(`${pid}:${tip.key}`);
        const [icon, colour, border] = LEVEL[tip.level];
        setTimeout(() => {
            const pop = $('advice-pop');
            pop.innerHTML = `
                <div class="advice-pop glass-card rounded-2xl p-4 border ${border} flex gap-3 shadow-2xl">
                    <i class="fa-solid ${icon} ${colour} text-xl mt-0.5"></i>
                    <div class="flex-1"><p class="text-xs font-bold text-white mb-1">${t('pt_tips_title')}</p><p class="text-sm text-slate-200">${esc(adviceText(tip))}</p></div>
                    <button onclick="document.getElementById('advice-pop').classList.add('hidden')" class="text-slate-400 hover:text-white self-start" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
                </div>`;
            pop.classList.remove('hidden');
            setTimeout(() => pop.classList.add('hidden'), 12000);
        }, 900);
    }

    function urgentModal() {
        modal(`
            <div class="text-center">
                <div class="w-16 h-16 mx-auto rounded-full bg-red-600 flex items-center justify-center mb-3 on-accent"><i class="fa-solid fa-truck-medical text-2xl text-white"></i></div>
                <h3 class="text-xl font-extrabold text-red-400">${t('urgent_title')}</h3>
                <p class="text-sm text-slate-200 mt-2">${t('urgent_text')}</p>
                <p class="text-lg font-black text-white mt-3"><i class="fa-solid fa-phone mr-2"></i>10177 · 112</p>
                <button onclick="SKAI.patient.closeModal()" class="on-accent mt-5 w-full bg-red-600 hover:bg-red-500 text-white font-bold py-3 rounded-xl">${t('urgent_ok')}</button>
            </div>`);
    }

    // ---------- vitals ----------
    function tile(label, value, unit, status) {
        return `<div class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
            <p class="text-[10px] uppercase font-bold text-slate-400">${label}</p>
            <p class="text-xl font-black text-white">${value} <span class="text-[11px] font-semibold text-slate-400">${unit}</span></p>
            ${status ? `<p class="text-[10px] font-bold status-${status}">${t(`st_${status}`)}</p>` : ''}
        </div>`;
    }

    function renderVitals() {
        const v = data.vitals;
        if (!data.vitals_source) {
            $('pt-vitals').innerHTML = `<p class="text-xs text-slate-400">${t('pt_no_vitals')}</p>
                <button onclick="SKAI.patient.openUpload()" class="on-accent mt-3 bg-skai-secondary text-white text-xs font-bold px-3 py-2 rounded-lg">${t('pt_upload_btn')}</button>`;
            return;
        }
        const tiles = [];
        if (v.sbp) tiles.push(tile(t('v_bp'), `${v.sbp}${v.dbp ? '/' + v.dbp : ''}`, 'mmHg', STATUS.bp(v)));
        if (v.heart_rate) tiles.push(tile(t('v_heart_rate'), v.heart_rate, 'bpm', STATUS.heart_rate(v.heart_rate)));
        if (v.ldl) tiles.push(tile(t('v_ldl'), v.ldl, 'mmol/L', STATUS.ldl(v.ldl)));
        if (v.total_cholesterol) tiles.push(tile(t('v_total_cholesterol'), v.total_cholesterol, 'mmol/L', STATUS.total_cholesterol(v.total_cholesterol)));
        if (v.glucose) tiles.push(tile(t('v_glucose'), v.glucose, 'mmol/L', STATUS.glucose(v.glucose)));
        if (v.bmi) tiles.push(tile(t('v_bmi'), v.bmi, `${v.weight_kg} kg`, STATUS.bmi(v.bmi)));
        const src = data.vitals_source;
        $('pt-vitals').innerHTML = `<div class="grid grid-cols-2 gap-2">${tiles.join('')}</div>
            <p class="text-[11px] text-slate-400 mt-3"><i class="fa-solid fa-shield-halved text-emerald-400 mr-1"></i>${t('pt_source', { issuer: esc(src.issuer || src.filename), date: src.document_date ? fmtDate(src.document_date) : '?' })}</p>`;
    }

    function renderTrend() {
        const h = data.vitals_history.filter(x => x.sbp || x.weight_kg);
        $('pt-trend-empty').classList.toggle('hidden', h.length > 0);
        chart('pt-trend-chart', {
            type: 'line',
            data: {
                labels: h.map(x => fmtDate(x.date)),
                datasets: [
                    { label: t('v_sbp'), data: h.map(x => x.sbp ?? null), borderColor: '#ef4444', backgroundColor: '#ef4444', tension: 0.3, yAxisID: 'y' },
                    { label: t('v_weight_kg'), data: h.map(x => x.weight_kg ?? null), borderColor: '#14b8a6', backgroundColor: '#14b8a6', tension: 0.3, yAxisID: 'y1' },
                    { label: t('pt_risk_title'), data: data.history.length === h.length ? data.history.map(x => +(x.probability * 100).toFixed(1)) : [],
                      borderColor: '#0ea5e9', backgroundColor: '#0ea5e9', borderDash: [4, 4], tension: 0.3, yAxisID: 'y1' },
                ],
            },
            options: { maintainAspectRatio: false, plugins: { legend: { labels: { boxWidth: 10, font: { size: 10 } } } },
                scales: { y: { grid, position: 'left' }, y1: { grid: { display: false }, position: 'right' }, x: { grid: { display: false } } } },
        });
    }

    // ---------- daily check-in ----------
    function renderCheckin() {
        const c = data.today_checkin;
        if (c && !editingCheckin) {
            $('pt-checkin').innerHTML = `
                <div class="text-center">
                    <div class="text-5xl">${MOODS[(c.mood || 3) - 1]}</div>
                    <p class="text-sm font-bold text-emerald-400 mt-2"><i class="fa-solid fa-circle-check mr-1"></i>${t('pt_checkin_done')}</p>
                    <div class="flex justify-center gap-4 text-xs text-slate-300 mt-3">
                        <span><i class="fa-solid fa-person-walking text-emerald-400 mr-1"></i>${c.activity_minutes} min</span>
                        <span><i class="fa-solid fa-glass-water text-sky-400 mr-1"></i>${c.water_glasses}</span>
                    </div>
                    ${c.symptoms.length ? `<p class="text-[11px] text-amber-300 mt-2">${c.symptoms.map(s => t(`sym_${s}`)).join(', ')}</p>` : ''}
                    <button onclick="SKAI.patient.editCheckin()" class="mt-3 text-xs text-slate-400 hover:text-white underline">${t('pt_edit')}</button>
                </div>`;
            return;
        }
        const prev = c || { mood: null, activity_minutes: 0, water_glasses: 0, symptoms: [] };
        mood = prev.mood;
        $('pt-checkin').innerHTML = `
            <p class="field-label">${t('pt_mood')}</p>
            <div class="flex justify-between mb-3">${MOODS.map((m, i) => `<button type="button" class="mood-btn ${mood === i + 1 ? 'selected' : ''}" onclick="SKAI.patient.pickMood(${i + 1}, this)">${m}</button>`).join('')}</div>
            <div class="grid grid-cols-2 gap-2 mb-3">
                <div><label class="field-label" for="ci-min">${t('pt_minutes')}</label><input id="ci-min" type="number" min="0" max="600" value="${prev.activity_minutes}" class="field"></div>
                <div><label class="field-label" for="ci-water">${t('pt_water')}</label><input id="ci-water" type="number" min="0" max="30" value="${prev.water_glasses}" class="field"></div>
            </div>
            <p class="field-label">${t('pt_symptoms')}</p>
            <div class="flex flex-wrap gap-1.5 mb-3">${data.symptom_options.map(s => `
                <label class="chip bg-slate-800 border border-slate-600 text-slate-200 cursor-pointer flex items-center gap-1 !text-[11px] !py-1">
                    <input type="checkbox" class="ci-sym accent-red-500" value="${s}" ${prev.symptoms.includes(s) ? 'checked' : ''}>${t(`sym_${s}`)}</label>`).join('')}</div>
            <button onclick="SKAI.patient.saveCheckin()" class="on-accent w-full bg-skai-secondary hover:bg-teal-500 text-white font-bold py-2.5 rounded-xl text-sm">${t('pt_checkin_save')}</button>`;
    }

    function pickMood(n, el) {
        mood = n;
        el.parentElement.querySelectorAll('.mood-btn').forEach(b => b.classList.remove('selected'));
        el.classList.add('selected');
    }

    function editCheckin() { editingCheckin = true; renderCheckin(); }

    async function saveCheckin() {
        if (!mood) { toast(t('pt_mood'), 'warning'); return; }
        const body = {
            mood,
            activity_minutes: Math.max(0, Number($('ci-min').value) || 0),
            water_glasses: Math.max(0, Number($('ci-water').value) || 0),
            symptoms: [...document.querySelectorAll('.ci-sym:checked')].map(c => c.value),
        };
        try {
            const r = await api(`/api/patients/${enc(pid)}/checkins`, body);
            editingCheckin = false;
            toast(t('pt_checkin_saved'), 'success');
            if (r.urgent) urgentModal();
            await reload();
        } catch (e) { toast(`${t('error_generic')} (${e.message})`, 'error'); }
    }

    // ---------- medication planner ----------
    function renderMeds() {
        const m = data.medications;
        if (!m.medications.length) {
            $('pt-meds').innerHTML = `<p class="text-xs text-slate-400">${t('pt_no_meds')}</p>`;
            return;
        }
        const adherence = m.adherence_7d == null ? null : Math.round(m.adherence_7d * 100);
        $('pt-meds').innerHTML = `
            ${adherence == null ? '' : `
            <div class="mb-3"><div class="flex justify-between text-xs text-slate-300 mb-1"><span>${t('pt_adherence', { n: adherence })}</span></div>
                <div class="h-2 bg-slate-800 rounded-full overflow-hidden"><div class="h-full ${adherence >= 80 ? 'bg-emerald-500' : 'bg-amber-500'}" style="width:${adherence}%"></div></div></div>`}
            <ul class="grid sm:grid-cols-2 gap-2">${m.medications.map(med => `
                <li class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                    <div class="flex justify-between items-start">
                        <div><p class="font-bold text-sm text-white">${esc(med.name)}</p><p class="text-[11px] text-slate-400">${esc(med.dose || '')}</p></div>
                        <button onclick="SKAI.patient.removeMed(${med.id}, this.dataset.name)" data-name="${esc(med.name)}" class="text-slate-500 hover:text-red-400" aria-label="${t('pt_remove')}"><i class="fa-solid fa-trash-can text-xs"></i></button>
                    </div>
                    <div class="flex flex-wrap gap-1.5 mt-2">${med.doses.map(d => `
                        <button onclick="SKAI.patient.toggleDose(${med.id}, '${d.slot}', ${!d.taken})" class="dose-btn ${d.taken ? 'taken' : ''} text-xs border border-slate-600 rounded-lg px-2 py-1">
                            <i class="fa-${d.taken ? 'solid fa-circle-check text-emerald-400' : 'regular fa-circle text-slate-400'} mr-1"></i>${d.slot}</button>`).join('')}
                    </div>
                </li>`).join('')}
            </ul>`;
    }

    async function toggleDose(id, slot, taken) {
        try {
            data.medications = await api(`/api/patients/${enc(pid)}/medications/${id}/doses`, { slot, taken });
            renderMeds();
        } catch (e) { toast(`${t('error_generic')} (${e.message})`, 'error'); }
    }

    async function removeMed(id, name) {
        if (!confirm(t('pt_confirm_remove', { name }))) return;
        try {
            await api(`/api/patients/${enc(pid)}/medications/${id}`, null, 'DELETE');
            toast(t('pt_med_removed'), 'info');
            await reload();
        } catch (e) { toast(t('error_generic'), 'error'); }
    }

    function openAddMed() {
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-4"><i class="fa-solid fa-pills text-purple-400 mr-2"></i>${t('pt_add_med')}</h3>
            <form id="med-form" class="space-y-3" onsubmit="event.preventDefault(); SKAI.patient.saveMed();">
                <div><label class="field-label" for="med-name">${t('pt_med_name')}</label><input id="med-name" class="field" maxlength="60" required></div>
                <div><label class="field-label" for="med-dose">${t('pt_med_dose')}</label><input id="med-dose" class="field" maxlength="40"></div>
                <div><p class="field-label">${t('pt_med_times')}</p><div id="med-times" class="flex flex-wrap gap-2"><input type="time" class="field !w-32 med-time" value="08:00" required></div>
                    <button type="button" onclick="SKAI.patient.addTime()" class="text-xs text-skai-secondary mt-2"><i class="fa-solid fa-plus mr-1"></i>${t('pt_add_time')}</button></div>
                <div class="flex gap-2 pt-2">
                    <button type="button" onclick="SKAI.patient.closeModal()" class="flex-1 bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">${t('pt_cancel')}</button>
                    <button class="on-accent flex-1 bg-skai-secondary text-white font-bold py-2.5 rounded-xl text-sm">${t('pt_save')}</button>
                </div>
            </form>`);
        $('med-name').focus();
    }

    function addTime() {
        if (document.querySelectorAll('.med-time').length >= 4) return;
        $('med-times').insertAdjacentHTML('beforeend', '<input type="time" class="field !w-32 med-time" value="20:00">');
    }

    async function saveMed() {
        const times = [...document.querySelectorAll('.med-time')].map(i => i.value).filter(Boolean);
        try {
            await api(`/api/patients/${enc(pid)}/medications`, { name: $('med-name').value.trim(), dose: $('med-dose').value.trim() || null, times });
            closeModal();
            toast(t('pt_med_added'), 'success');
            await reload();
        } catch (e) { toast(`${t('error_generic')} (${e.message})`, 'error'); }
    }

    // ---------- activity ----------
    function renderActivity() {
        const days = [...Array(7)].map((_, i) => { const d = new Date(); d.setDate(d.getDate() - (6 - i)); return d; });
        const byDay = Object.fromEntries(data.checkins.map(c => [c.day, c.activity_minutes || 0]));
        const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        const minutes = days.map(d => byDay[iso(d)] || 0);
        const total = minutes.reduce((a, b) => a + b, 0);
        $('pt-activity-total').textContent = t('pt_activity_total', { n: total, goal: 150 });
        chart('pt-activity-chart', {
            type: 'bar',
            data: { labels: days.map(d => d.toLocaleDateString(state.lang === 'en' ? 'en-ZA' : undefined, { weekday: 'short' })),
                    datasets: [{ data: minutes, borderRadius: 4, backgroundColor: minutes.map(m => (m >= 22 ? '#10b981' : '#64748b')) }] },
            options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
                       scales: { y: { beginAtZero: true, grid }, x: { grid: { display: false } } } },
        });
    }

    // ---------- documents ----------
    function renderDocs() {
        $('pt-docs-list').innerHTML = data.documents.map(d => `
            <li class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">${docLine(d)}</li>`).join('')
            || `<li class="text-xs text-slate-500">${t('pt_no_docs')}</li>`;
    }

    function openUpload() {
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-2"><i class="fa-solid fa-file-shield text-skai-secondary mr-2"></i>${t('pt_upload_title')}</h3>
            <p class="text-sm text-slate-300 mb-4">${t('pt_upload_text')}</p>
            <label id="dropzone" class="dropzone flex flex-col items-center justify-center text-center p-8 cursor-pointer">
                <i class="fa-solid fa-cloud-arrow-up text-4xl text-skai-secondary mb-2"></i>
                <span class="text-sm font-bold text-white">${t('pt_upload_drop')}</span>
                <span class="text-[11px] text-slate-500 mt-1">PDF · max 5 MB</span>
                <input type="file" accept="application/pdf,.pdf" class="hidden" onchange="SKAI.patient.upload(this.files[0])">
            </label>
            <div id="upload-result" class="mt-4"></div>`);
        const dz = $('dropzone');
        ['dragenter', 'dragover'].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.add('drag'); }));
        ['dragleave', 'drop'].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.remove('drag'); }));
        dz.addEventListener('drop', ev => upload(ev.dataTransfer.files[0]));
    }

    async function upload(file) {
        if (!file) return;
        const box = $('upload-result');
        box.innerHTML = `<p class="text-sm text-slate-300"><i class="fa-solid fa-spinner fa-spin mr-2"></i>${t('pt_uploading')}</p>`;
        const form = new FormData();
        form.append('file', file);
        let doc;
        try {
            const res = await fetch(`${API_BASE}/api/patients/${enc(pid)}/documents`, { method: 'POST', body: form, headers: authHeader() });
            doc = await res.json();
            if (!res.ok) throw new Error(doc.detail || res.statusText);
        } catch (e) {
            box.innerHTML = `<p class="text-sm text-red-400">${esc(t('error_generic'))} (${esc(e.message)})</p>`;
            return;
        }
        const accepted = doc.status !== 'rejected';
        box.innerHTML = `
            <div class="rounded-2xl border p-4 ${accepted ? 'bg-tier-low' : 'bg-tier-critical'}">
                <p class="text-sm font-bold text-white mb-1">${t(`res_${doc.status}`)}</p>
                <div class="text-xs">${docLine(doc)}</div>
            </div>
            <div class="flex gap-2 mt-4">
                ${accepted ? `<button onclick="SKAI.patient.closeModal(); SKAI.patient.openSelfCheck();" class="on-accent flex-1 bg-skai-secondary text-white font-bold py-2.5 rounded-xl text-sm">${t('pt_run_check')}</button>` : ''}
                <button onclick="SKAI.patient.closeModal()" class="flex-1 bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">${t('pt_close')}</button>
            </div>`;
        reload();
    }

    // ---------- self-check (answers only; vitals from the document) ----------
    function openSelfCheck() {
        if (!data.vitals_source) {
            modal(`<p class="text-sm text-slate-200 mb-4">${t('pt_need_doc')}</p>
                <button onclick="SKAI.patient.openUpload()" class="on-accent w-full bg-skai-secondary text-white font-bold py-2.5 rounded-xl">${t('pt_upload_btn')}</button>`);
            return;
        }
        const prev = data.result ? data.result.inputs : { cigarettes_per_day: 0, years_smoked: 0, family_history: false };
        const v = data.vitals;
        const src = data.vitals_source;
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-3"><i class="fa-solid fa-heart-circle-check text-skai-secondary mr-2"></i>${t('pt_check_title')}</h3>
            <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-3 mb-4">
                <p class="text-xs text-slate-400 mb-1">${t('pt_check_from')}</p>
                <p class="text-sm text-white font-bold"><i class="fa-solid fa-shield-halved text-emerald-400 mr-1"></i>${esc(src.issuer || src.filename)} · ${src.document_date ? fmtDate(src.document_date) : '?'} · ${t(`doc_${src.status}`)}</p>
                <p class="text-xs text-slate-300 mt-1">${['sbp', 'ldl', 'height_cm', 'weight_kg'].filter(k => v[k]).map(k => `${t(`v_${k}`)} ${v[k]}`).join(' · ')}</p>
            </div>
            <p class="text-xs font-bold text-slate-300 mb-2">${t('pt_check_questions')}</p>
            <form id="sc-form" class="grid grid-cols-2 gap-3" onsubmit="event.preventDefault(); SKAI.patient.runSelfCheck();">
                <div><label class="field-label" for="sc-cigs">${featureLabel('cigarettes_per_day')}</label><input id="sc-cigs" type="number" min="0" max="80" value="${prev.cigarettes_per_day || 0}" class="field"></div>
                <div><label class="field-label" for="sc-years">${featureLabel('years_smoked')}</label><input id="sc-years" type="number" min="0" max="80" value="${prev.years_smoked || 0}" class="field"></div>
                <div class="col-span-2"><label class="field-label" for="sc-fam">${featureLabel('family_history')}</label>
                    <select id="sc-fam" class="field"><option value="0">${t('no')}</option><option value="1" ${prev.family_history ? 'selected' : ''}>${t('yes')}</option></select></div>
                <button class="on-accent col-span-2 bg-gradient-to-r from-skai-secondary to-emerald-500 text-white font-bold py-3 rounded-xl">${t('pt_check_run')}</button>
            </form>`);
    }

    async function runSelfCheck() {
        if (!$('sc-form').reportValidity()) return;
        try {
            await api(`/api/patients/${enc(pid)}/self-check`, {
                cigarettes_per_day: Number($('sc-cigs').value), years_smoked: Number($('sc-years').value),
                family_history: $('sc-fam').value === '1',
            });
            closeModal();
            toast(t('pt_check_done'), 'success');
            await reload();
        } catch (e) {
            const code = { document_missing_bp: 'pt_doc_no_bp', no_document: 'pt_need_doc' }[e.message];
            toast(code ? t(code) : `${t('error_generic')} (${e.message})`, 'error');
        }
    }

    // ---------- profile picture ----------
    async function photo(file) {
        try {
            const dataUrl = await resizeImage(file);
            await api(`/api/patients/${enc(pid)}/photo`, { data_url: dataUrl }, 'PUT');
            data.profile.photo = dataUrl;
            renderHero();
            toast(t('pt_photo_saved'), 'success');
        } catch (e) { toast(t('error_generic'), 'error'); }
    }

    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
    SKAI.core.onRerender(() => { if (data && !$('view-patient').classList.contains('hidden')) render(); });

    return { open, signIn, create, activate, showForm, sessionExpired, logout, whatIf, pickMood, editCheckin, saveCheckin, toggleDose, removeMed,
             openAddMed, addTime, saveMed, openUpload, upload, openSelfCheck, runSelfCheck, photo, closeModal };
})();
