// Staff tools: SATS waiting room, priority bookings, referrals and the message outbox.
// Staff-facing text is English-first (see SKAI_STAFF_TEXT in i18n.js).
SKAI.staff = (() => {
    const { state, api, t, toast, esc, modal, closeModal, pct, authHeader, API_BASE } = SKAI.core;
    const $ = id => document.getElementById(id);
    const COLOUR = {
        red: ['bg-red-600', 'border-red-500', 'text-red-400', 'Red - immediately'],
        orange: ['bg-orange-500', 'border-orange-500', 'text-orange-300', 'Orange - within 10 min'],
        yellow: ['bg-yellow-400', 'border-yellow-400', 'text-amber-300', 'Yellow - within 60 min'],
        green: ['bg-emerald-500', 'border-emerald-500', 'text-emerald-400', 'Green - within 4 hours'],
    };
    const label = s => s.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
    const role = () => (state.clinician && state.clinician.role) || '';
    const can = action => ({
        triage: ['nurse', 'doctor'], book: ['nurse', 'doctor', 'facility_manager'], refer: ['nurse', 'doctor'],
        assess: ['nurse', 'doctor'], record_outcome: ['doctor'], dashboard: ['doctor', 'facility_manager', 'district_manager'],
    }[action] || []).includes(role());
    const fmt = iso => new Date(iso).toLocaleString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    let options = null;
    let refreshTimer = null;

    function render(tab) {
        clearInterval(refreshTimer);
        if (tab === 'triage') { renderTriage(); refreshTimer = setInterval(renderTriage, 30000); }
        if (tab === 'bookings') renderBookings();
        if (tab === 'referrals') renderReferrals();
        if (tab === 'outbox') renderOutbox();
        if (tab === 'monitoring') renderMonitoring();
    }

    const card = (title, icon, body, extra = '') =>
        `<div class="glass-card rounded-2xl p-5"><div class="flex flex-wrap items-center justify-between gap-2 mb-3">
            <h3 class="text-sm font-bold text-white"><i class="fa-solid ${icon} text-skai-primary mr-2"></i>${title}</h3>${extra}</div>${body}</div>`;

    // ---------- SATS waiting room ----------
    async function renderTriage() {
        if (!options) options = await api('/api/triage/options');
        let visits = [];
        try { visits = await api('/api/triage/queue'); } catch (e) { return; }
        const counts = Object.fromEntries(Object.keys(COLOUR).map(c => [c, visits.filter(v => v.colour === c && v.status === 'waiting').length]));
        const overdue = visits.filter(v => v.overdue).length;
        $('panel-triage').innerHTML = `
            <div class="flex flex-wrap items-end justify-between gap-3">
                <div><h2 class="text-2xl font-extrabold text-white">Waiting room</h2>
                    <p class="text-sm text-slate-400">${esc(state.clinician.facility_name || 'All facilities')} · South African Triage Scale (SATS). SATS sets the colour; within a colour, SKAI moves patients with high cardiac risk forward.</p></div>
                ${can('triage') ? `<button onclick="SKAI.staff.openArrival()" class="on-accent bg-skai-primary hover:bg-sky-500 text-white text-sm font-bold px-4 py-2.5 rounded-xl"><i class="fa-solid fa-user-plus mr-2"></i>New arrival</button>` : ''}
            </div>
            <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
                ${Object.entries(COLOUR).map(([c, [bg, , text, name]]) => `
                    <div class="glass-card rounded-2xl p-4 border-l-4 ${COLOUR[c][1]}"><p class="text-[10px] font-bold uppercase text-slate-400">${name}</p>
                    <p class="text-3xl font-black ${text}">${counts[c]}</p></div>`).join('')}
                <div class="glass-card rounded-2xl p-4 border-l-4 ${overdue ? 'border-red-500' : 'border-slate-700'}"><p class="text-[10px] font-bold uppercase text-slate-400">Past target time</p>
                    <p class="text-3xl font-black ${overdue ? 'text-red-400' : 'text-white'}">${overdue}</p></div>
            </div>
            <div class="space-y-2">${visits.map(visitCard).join('') || '<p class="text-sm text-slate-400">Nobody is waiting.</p>'}</div>`;
    }

    function visitCard(v) {
        const [bg, border, text] = COLOUR[v.colour];
        const waited = v.waited_minutes < 60 ? `${v.waited_minutes} min` : `${Math.floor(v.waited_minutes / 60)} h ${v.waited_minutes % 60} min`;
        const actions = can('triage') ? (v.status === 'waiting'
            ? `<button onclick="SKAI.staff.visitStatus(${v.id}, 'in_consult')" class="on-accent bg-skai-primary text-white font-bold px-3 py-1.5 rounded-lg">Call in</button>
               <button onclick="SKAI.staff.visitStatus(${v.id}, 'left')" class="bg-slate-800 border border-slate-600 px-3 py-1.5 rounded-lg">Left</button>`
            : `<button onclick="SKAI.staff.visitStatus(${v.id}, 'done')" class="on-accent bg-emerald-600 text-white font-bold px-3 py-1.5 rounded-lg">Done</button>`) : '';
        return `
            <div class="glass-card rounded-2xl p-4 border-l-8 ${border} flex flex-wrap items-center gap-4 ${v.status === 'in_consult' ? 'opacity-70' : ''}">
                <div class="w-14 text-center"><span class="on-accent inline-block ${bg} text-white text-[10px] font-black uppercase px-2 py-1 rounded-lg">${v.colour}</span>
                    <p class="text-[10px] text-slate-400 mt-1">TEWS ${v.tews}</p></div>
                <div class="flex-1 min-w-[180px]">
                    <p class="font-bold text-white">${esc(v.name)} ${v.patient_id ? `<span class="text-[11px] text-slate-400 font-normal">· ${esc(v.patient_id)}</span>` : ''}
                        ${v.cardiac_flag ? '<span class="chip bg-red-500/20 text-red-300 ml-1"><i class="fa-solid fa-heart-pulse mr-1"></i>SKAI high cardiac risk</span>' : ''}
                        ${v.status === 'in_consult' ? '<span class="chip bg-sky-500/20 text-sky-300 ml-1">In consultation</span>' : ''}</p>
                    <p class="text-xs text-slate-400">${v.discriminators.map(label).join(', ') || 'No discriminators'} · BP ${v.vitals.sbp}${v.vitals.dbp ? '/' + v.vitals.dbp : ''}${v.vitals.bp_source === 'device' ? ' <i class="fa-brands fa-bluetooth-b text-sky-400" title="From BP monitor"></i>' : ''} · HR ${v.vitals.hr} · RR ${v.vitals.rr} · ${v.vitals.temp}°C</p>
                </div>
                <div class="text-right text-xs"><p class="${v.overdue ? 'text-red-400 font-bold' : 'text-slate-300'}">${v.overdue ? '<i class="fa-solid fa-triangle-exclamation mr-1"></i>' : ''}Waited ${waited}</p>
                    <p class="text-slate-500">${v.target_minutes ? `target ${v.target_minutes} min` : 'see immediately'}</p></div>
                <div class="flex gap-2 text-xs">${v.patient_id ? `<button onclick="SKAI.staff.openRecord('${esc(v.patient_id)}')" class="bg-slate-800 border border-slate-600 px-3 py-1.5 rounded-lg">Record</button>` : ''}${actions}</div>
            </div>`;
    }

    async function visitStatus(id, status) {
        try { await api(`/api/triage/${id}/status`, { status }); renderTriage(); } catch (e) { toast(e.message, 'error'); }
    }

    function openRecord(patientId) {
        SKAI.showTab('assess');
        SKAI.selectPatient(patientId);
    }

    function openArrival() {
        const discs = Object.entries(options.discriminators).map(([c, list]) => `
            <div class="mb-2"><p class="text-[10px] font-bold uppercase ${COLOUR[c][2]} mb-1">${c} signs</p>
            <div class="flex flex-wrap gap-1.5">${list.map(d => `<label class="chip bg-slate-800 border border-slate-600 text-slate-200 cursor-pointer flex items-center gap-1 !text-[11px] !py-1">
                <input type="checkbox" class="tr-disc" value="${d}" onchange="SKAI.staff.previewArrival()">${label(d)}</label>`).join('')}</div></div>`).join('');
        const num = (id, lab, min, max, value, step = 1) => `<div><label class="field-label" for="${id}">${lab}</label>
            <input id="${id}" type="number" class="field" min="${min}" max="${max}" step="${step}" value="${value}" required oninput="SKAI.staff.previewArrival()"></div>`;
        bpReading = {};
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-1"><i class="fa-solid fa-user-plus text-skai-primary mr-2"></i>New arrival (SATS)</h3>
            <p class="text-xs text-slate-400 mb-4">Record arrival vitals. The colour updates as you type.</p>
            <form id="tr-form" class="space-y-3" onsubmit="event.preventDefault(); SKAI.staff.saveArrival();">
                <div class="grid grid-cols-2 gap-3">
                    <div><label class="field-label" for="tr-name">Name</label><input id="tr-name" class="field" maxlength="80" required></div>
                    <div><label class="field-label" for="tr-pid">Patient ID (if registered)</label><input id="tr-pid" class="field" maxlength="40" onchange="SKAI.staff.previewArrival()"></div>
                    <div><label class="field-label" for="tr-mob">Mobility</label><select id="tr-mob" class="field" onchange="SKAI.staff.previewArrival()">
                        <option value="walking">Walking</option><option value="with_help">With help</option><option value="stretcher">Stretcher / immobile</option></select></div>
                    <div><label class="field-label" for="tr-avpu">Alertness (AVPU)</label><select id="tr-avpu" class="field" onchange="SKAI.staff.previewArrival()">
                        <option value="alert">Alert</option><option value="voice">Reacts to voice</option><option value="pain">Reacts to pain</option><option value="unresponsive">Unresponsive</option></select></div>
                    ${num('tr-rr', 'Respiratory rate', 0, 80, 16)}${num('tr-hr', 'Heart rate', 0, 250, 80)}
                    ${num('tr-sbp', 'Systolic BP', 0, 300, 120)}${num('tr-temp', 'Temperature °C', 25, 45, 36.8, 0.1)}
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <button type="button" onclick="SKAI.staff.readBp()" class="text-xs bg-slate-800 hover:bg-slate-700 border border-slate-600 font-bold px-3 py-1.5 rounded-lg"><i class="fa-brands fa-bluetooth-b text-sky-400 mr-1"></i>Read from BP monitor</button>
                    <span id="tr-bp-status" class="text-[11px] text-slate-400">Or type the reading.</span>
                </div>
                <label class="flex items-center gap-2 text-xs text-slate-300"><input id="tr-trauma" type="checkbox" onchange="SKAI.staff.previewArrival()"> Trauma</label>
                ${discs}
                <div id="tr-preview" class="rounded-2xl border border-slate-700 p-3 text-sm text-slate-300">Enter vitals to see the SATS colour.</div>
                <div class="flex gap-2">
                    <button type="button" onclick="SKAI.closeModal()" class="flex-1 bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">Cancel</button>
                    <button class="on-accent flex-1 bg-skai-primary text-white font-bold py-2.5 rounded-xl text-sm">Add to waiting room</button>
                </div>
            </form>`);
        previewArrival();
        $('tr-name').focus();
    }

    function arrivalBody() {
        return {
            name: $('tr-name').value.trim() || 'Unnamed', patient_id: $('tr-pid').value.trim() || null,
            vitals: { mobility: $('tr-mob').value, rr: +$('tr-rr').value, hr: +$('tr-hr').value, sbp: +$('tr-sbp').value,
                      temp: +$('tr-temp').value, avpu: $('tr-avpu').value, trauma: $('tr-trauma').checked, ...bpReading },
            discriminators: [...document.querySelectorAll('.tr-disc:checked')].map(c => c.value),
        };
    }

    // ---------- clinic BP monitor ----------
    // Standard Bluetooth Blood Pressure service (0x1810), measurement characteristic 0x2A35.
    // Browsers without Web Bluetooth (or no monitor nearby) use a clearly labelled simulator.
    let bpReading = {};
    const sfloat = v => { let m = v & 0x0fff, e = v >> 12; if (m >= 0x800) m -= 0x1000; if (e >= 0x8) e -= 0x10; return m * 10 ** e; };

    function parseBp(view) {
        const flags = view.getUint8(0);
        const kpa = flags & 1;
        const unit = x => Math.round(kpa ? x * 7.50062 : x);
        const offset = 7 + (flags & 2 ? 7 : 0);
        const reading = { sys: unit(sfloat(view.getUint16(1, true))), dia: unit(sfloat(view.getUint16(3, true))) };
        if (flags & 4) reading.pulse = Math.round(sfloat(view.getUint16(offset, true)));
        return reading;
    }

    async function readBluetooth() {
        const device = await navigator.bluetooth.requestDevice({ filters: [{ services: [0x1810] }] });
        $('tr-bp-status').textContent = `Connecting to ${device.name || 'monitor'}... start a measurement on the cuff.`;
        const server = await device.gatt.connect();
        const ch = await (await server.getPrimaryService(0x1810)).getCharacteristic(0x2a35);
        return new Promise(resolve => {
            ch.addEventListener('characteristicvaluechanged', e => { resolve({ ...parseBp(e.target.value), device: device.name || 'Bluetooth BP monitor' }); server.disconnect(); });
            ch.startNotifications();
        });
    }

    async function simulateBp() {
        $('tr-bp-status').innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i>Simulated monitor measuring...';
        await new Promise(r => setTimeout(r, 1800));
        const sys = 110 + Math.round(Math.random() * 60);
        return { sys, dia: Math.round(sys * 0.62 + Math.random() * 8), pulse: 64 + Math.round(Math.random() * 40), device: 'Simulated monitor (demo)' };
    }

    async function readBp() {
        let r;
        try {
            r = navigator.bluetooth && await navigator.bluetooth.getAvailability() ? await readBluetooth() : await simulateBp();
        } catch (e) {
            if (e.name !== 'NotFoundError') toast(`Monitor: ${e.message}`, 'error');
            r = await simulateBp();        // picker cancelled or no monitor: fall back to the simulator
        }
        $('tr-sbp').value = r.sys;
        if (r.pulse) $('tr-hr').value = r.pulse;
        bpReading = { dbp: r.dia, bp_source: 'device', bp_device: r.device };
        $('tr-bp-status').innerHTML = `<i class="fa-solid fa-circle-check text-emerald-400 mr-1"></i><b class="text-white">${r.sys}/${r.dia}</b>${r.pulse ? ` · pulse ${r.pulse}` : ''} from ${esc(r.device)}`;
        ['tr-sbp', 'tr-hr'].forEach(id => $(id).addEventListener('input', () => { bpReading = {}; $('tr-bp-status').textContent = 'Typed by hand.'; }, { once: true }));
        previewArrival();
    }

    let previewTimer = null;
    function previewArrival() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(async () => {
            try {
                const r = await api('/api/triage/preview', arrivalBody());
                const [bg, border, , name] = COLOUR[r.colour];
                $('tr-preview').className = `rounded-2xl border-2 ${border} p-3 text-sm`;
                $('tr-preview').innerHTML = `<span class="on-accent ${bg} text-white text-xs font-black uppercase px-2 py-1 rounded-lg mr-2">${r.colour}</span>
                    <b class="text-white">TEWS ${r.tews}</b> · ${name}${r.cardiac_flag ? ' · <span class="text-red-300">SKAI high cardiac risk</span>' : ''}`;
            } catch (e) { /* incomplete form */ }
        }, 250);
    }

    async function saveArrival() {
        if (!$('tr-form').reportValidity()) return;
        try {
            const r = await api('/api/triage', arrivalBody());
            closeModal();
            toast(`Added: ${r.colour.toUpperCase()} (TEWS ${r.tews})`, 'success');
            renderTriage();
        } catch (e) { toast(e.message, 'error'); }
    }

    // ---------- priority booking ----------
    async function renderBookings() {
        let priority = [], upcoming = [];
        try { [priority, upcoming] = await Promise.all([api('/api/bookings/priority'), api('/api/appointments')]); } catch (e) { return; }
        $('panel-bookings').innerHTML = `
            <div><h2 class="text-2xl font-extrabold text-white">Bookings</h2>
                <p class="text-sm text-slate-400">Most at risk first, in the appointment book rather than the waiting room: high-risk patients without an upcoming appointment, with the date they should be seen by.</p></div>
            <div class="grid lg:grid-cols-2 gap-5">
                ${card('Needs an appointment', 'fa-list-ol', priority.map(p => `
                    <div class="flex items-center gap-3 bg-slate-900/60 border border-slate-800 rounded-xl p-3 mb-2">
                        <div class="flex-1"><p class="font-bold text-white">${esc(p.name)} <span class="text-[11px] text-slate-400 font-normal">${esc(p.id)}</span></p>
                            <p class="text-xs text-slate-400">Risk <b class="tier-${p.tier}">${pct(p.probability)}</b> · book by <b>${p.book_by}</b></p></div>
                        <span class="chip ${p.priority === 'urgent' ? 'bg-red-500/20 text-red-300' : 'bg-amber-500/20 text-amber-300'}">${p.priority}</span>
                        ${can('book') ? `<button onclick="SKAI.staff.openBook('${esc(p.id)}', '${esc(p.name)}', '${p.priority}', '${p.book_by}')" class="on-accent bg-skai-primary text-white text-xs font-bold px-3 py-1.5 rounded-lg">Book</button>` : ''}
                    </div>`).join('') || '<p class="text-sm text-slate-400">Every high-risk patient has an appointment.</p>')}
                ${card('Upcoming (14 days)', 'fa-calendar-days', upcoming.map(a => `
                    <div class="flex items-center gap-3 bg-slate-900/60 border border-slate-800 rounded-xl p-3 mb-2">
                        <div class="flex-1"><p class="font-bold text-white">${esc(a.patient_name)}</p>
                            <p class="text-xs text-slate-400">${fmt(a.starts_at)} · ${esc(a.reason)}</p></div>
                        <span class="chip bg-slate-800 text-slate-300">${a.status}</span>
                        ${a.status === 'booked' && can('book') ? `
                            <button onclick="SKAI.staff.apptStatus(${a.id}, 'attended')" class="text-xs bg-slate-800 border border-slate-600 px-2 py-1 rounded-lg">Attended</button>
                            <button onclick="SKAI.staff.apptStatus(${a.id}, 'missed')" class="text-xs bg-slate-800 border border-slate-600 px-2 py-1 rounded-lg">Missed</button>` : ''}
                    </div>`).join('') || '<p class="text-sm text-slate-400">No appointments booked.</p>')}
            </div>`;
    }

    function openBook(patientId, name, priority = 'routine', bookBy = null) {
        const d = bookBy ? new Date(`${bookBy}T09:00`) : new Date(Date.now() + 7 * 864e5);
        d.setHours(9, 0, 0, 0);
        if (d.getDay() === 0) d.setDate(d.getDate() + 1);
        if (d.getDay() === 6) d.setDate(d.getDate() + 2);
        const local = new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16);
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-4"><i class="fa-solid fa-calendar-plus text-skai-primary mr-2"></i>Book ${esc(name)}</h3>
            <form id="bk-form" class="space-y-3" onsubmit="event.preventDefault(); SKAI.staff.saveBook('${esc(patientId)}');">
                <div><label class="field-label" for="bk-when">Date and time</label><input id="bk-when" type="datetime-local" class="field" value="${local}" required></div>
                <div><label class="field-label" for="bk-reason">Reason</label><input id="bk-reason" class="field" maxlength="200" value="Cardiovascular risk follow-up" required></div>
                <div><label class="field-label" for="bk-priority">Priority</label><select id="bk-priority" class="field">
                    ${['urgent', 'soon', 'routine'].map(p => `<option ${p === priority ? 'selected' : ''}>${p}</option>`).join('')}</select></div>
                <p class="text-[11px] text-slate-500">If the patient opted in to SMS, a confirmation and a reminder the day before are sent automatically.</p>
                <div class="flex gap-2"><button type="button" onclick="SKAI.closeModal()" class="flex-1 bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">Cancel</button>
                    <button class="on-accent flex-1 bg-skai-primary text-white font-bold py-2.5 rounded-xl text-sm">Book</button></div>
            </form>`);
    }

    async function saveBook(patientId) {
        if (!$('bk-form').reportValidity()) return;
        try {
            await api('/api/appointments', { patient_id: patientId, starts_at: new Date($('bk-when').value).toISOString(),
                                             reason: $('bk-reason').value.trim(), priority: $('bk-priority').value });
            closeModal();
            toast('Appointment booked', 'success');
            if (!$('panel-bookings').classList.contains('hidden')) renderBookings();
        } catch (e) { toast(e.message, 'error'); }
    }

    async function apptStatus(id, status) {
        try { await api(`/api/appointments/${id}/status`, { status }); renderBookings(); } catch (e) { toast(e.message, 'error'); }
    }

    // ---------- referrals ----------
    async function renderReferrals() {
        let data;
        try { data = await api('/api/referrals'); } catch (e) { return; }
        const urgencyChip = u => `<span class="chip ${u === 'emergency' ? 'bg-red-500/20 text-red-300' : u === 'urgent' ? 'bg-orange-500/20 text-orange-300' : 'bg-slate-800 text-slate-300'}">${u}</span>`;
        const item = (r, incoming) => `
            <div class="bg-slate-900/60 border ${r.overdue ? 'border-red-500/60' : 'border-slate-800'} rounded-xl p-3 mb-2">
                <div class="flex flex-wrap items-center gap-2"><p class="font-bold text-white flex-1">${esc(r.patient_name)} <span class="text-[11px] text-slate-400 font-normal">${esc(r.patient_id)}</span></p>
                    ${urgencyChip(r.urgency)}<span class="chip bg-sky-500/20 text-sky-300">${r.status}</span>
                    ${r.overdue ? '<span class="chip bg-red-500/20 text-red-300"><i class="fa-solid fa-triangle-exclamation mr-1"></i>overdue</span>' : ''}</div>
                <p class="text-xs text-slate-400 mt-1">${esc(r.from_name)} → ${esc(r.to_name)} · due ${fmt(r.due_by)} · by ${esc(r.created_by_name)}</p>
                <p class="text-xs text-slate-300 mt-1">${esc(r.reason)}</p>
                <div class="flex flex-wrap gap-2 mt-2 text-xs">
                    <button onclick="SKAI.staff.letter(${r.id})" class="bg-slate-800 border border-slate-600 px-2 py-1 rounded-lg"><i class="fa-solid fa-file-pdf text-red-400 mr-1"></i>Letter</button>
                    ${can('refer') && incoming && r.status === 'sent' ? `<button onclick="SKAI.staff.refStatus(${r.id}, 'arrived')" class="on-accent bg-emerald-600 text-white font-bold px-2 py-1 rounded-lg">Arrived</button>` : ''}
                    ${can('refer') && incoming && r.status === 'arrived' ? `<button onclick="SKAI.staff.refStatus(${r.id}, 'seen')" class="on-accent bg-emerald-600 text-white font-bold px-2 py-1 rounded-lg">Seen</button>` : ''}
                    ${can('refer') && !incoming && !['closed', 'cancelled'].includes(r.status) ? `
                        <button onclick="SKAI.staff.refStatus(${r.id}, 'closed')" class="bg-slate-800 border border-slate-600 px-2 py-1 rounded-lg">Close</button>
                        <button onclick="SKAI.staff.refStatus(${r.id}, 'cancelled')" class="bg-slate-800 border border-slate-600 px-2 py-1 rounded-lg">Cancel</button>` : ''}
                </div>
            </div>`;
        $('panel-referrals').innerHTML = `
            <div><h2 class="text-2xl font-extrabold text-white">Referrals</h2>
                <p class="text-sm text-slate-400">Closed loop: the receiving facility confirms arrival, and a referral not completed by its due date is flagged here and in the triage queue.</p></div>
            <div class="grid lg:grid-cols-2 gap-5">
                ${card('Incoming', 'fa-right-to-bracket', data.incoming.map(r => item(r, true)).join('') || '<p class="text-sm text-slate-400">No incoming referrals.</p>')}
                ${card('Outgoing', 'fa-share-from-square', data.outgoing.map(r => item(r, false)).join('') || '<p class="text-sm text-slate-400">No outgoing referrals.</p>')}
            </div>`;
    }

    async function refStatus(id, status) {
        try { await api(`/api/referrals/${id}/status`, { status }); renderReferrals(); } catch (e) { toast(e.message, 'error'); }
    }

    async function letter(id) {
        const res = await fetch(`${API_BASE}/api/referrals/${id}/letter.pdf`, { headers: authHeader() });
        if (!res.ok) { toast('Could not create the letter', 'error'); return; }
        const link = document.createElement('a');
        link.href = URL.createObjectURL(await res.blob());
        link.download = `Referral_${id}.pdf`;
        link.click();
    }

    async function openRefer(patientId, name, risk) {
        const facilities = (await api('/api/facilities')).filter(f => f.id !== state.clinician.facility_id);
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-4"><i class="fa-solid fa-share-from-square text-skai-primary mr-2"></i>Refer ${esc(name)}</h3>
            <form id="rf-form" class="space-y-3" onsubmit="event.preventDefault(); SKAI.staff.saveRefer('${esc(patientId)}');">
                <div><label class="field-label" for="rf-to">Refer to</label><select id="rf-to" class="field">${facilities.map(f => `<option value="${f.id}">${esc(f.name)} (${f.kind})</option>`).join('')}</select></div>
                <div><label class="field-label" for="rf-urgency">Urgency</label><select id="rf-urgency" class="field">
                    <option value="urgent">Urgent (within 2 days)</option><option value="routine">Routine (within 14 days)</option><option value="emergency">Emergency (within 6 hours)</option></select></div>
                <div><label class="field-label" for="rf-reason">Reason</label><textarea id="rf-reason" class="field" rows="3" maxlength="500" required>${risk ? `SKAI cardiovascular risk ${risk}. ` : ''}</textarea></div>
                <div class="flex gap-2"><button type="button" onclick="SKAI.closeModal()" class="flex-1 bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">Cancel</button>
                    <button class="on-accent flex-1 bg-skai-primary text-white font-bold py-2.5 rounded-xl text-sm">Send referral</button></div>
            </form>`);
    }

    async function saveRefer(patientId) {
        if (!$('rf-form').reportValidity()) return;
        try {
            const r = await api('/api/referrals', { patient_id: patientId, to_facility: $('rf-to').value,
                                                    urgency: $('rf-urgency').value, reason: $('rf-reason').value.trim() });
            closeModal();
            toast('Referral sent', 'success');
            letter(r.id);
        } catch (e) { toast(e.message, 'error'); }
    }

    // ---------- message outbox ----------
    async function renderOutbox() {
        let msgs = [];
        try { msgs = await api('/api/messages'); } catch (e) { return; }
        const chip = s => `<span class="chip ${s === 'sent' ? 'bg-emerald-500/20 text-emerald-400' : s === 'queued' ? 'bg-sky-500/20 text-sky-300' : 'bg-red-500/20 text-red-300'}">${s}</span>`;
        $('panel-outbox').innerHTML = `
            <div class="flex flex-wrap items-end justify-between gap-3">
                <div><h2 class="text-2xl font-extrabold text-white">Reminders outbox</h2>
                    <p class="text-sm text-slate-400">SMS / WhatsApp to patients who opted in: appointment confirmations and reminders, referral instructions, medication doses. Demo mode records messages without texting anyone; a real deployment plugs in an SMS gateway.</p></div>
                <button onclick="SKAI.staff.dispatch()" class="on-accent bg-skai-primary text-white text-sm font-bold px-4 py-2.5 rounded-xl"><i class="fa-solid fa-paper-plane mr-2"></i>Send due now</button>
            </div>
            ${card(`${msgs.length} messages`, 'fa-comment-sms', `<div class="space-y-2">${msgs.map(m => `
                <div class="flex flex-wrap items-center gap-3 bg-slate-900/60 border border-slate-800 rounded-xl p-3 text-xs">
                    <i class="fa-${m.channel === 'whatsapp' ? 'brands fa-whatsapp text-emerald-400' : 'solid fa-comment-sms text-sky-400'} text-lg"></i>
                    <div class="flex-1 min-w-[200px]"><p class="font-bold text-white">${esc(m.patient_name)} <span class="text-slate-400 font-normal">${esc(m.to_number)}</span></p>
                        <p class="text-slate-300">${esc(m.body)}</p></div>
                    <span class="text-slate-500">${fmt(m.scheduled_for)}</span>${chip(m.status)}
                </div>`).join('') || '<p class="text-sm text-slate-400">No messages yet.</p>'}</div>`)}`;
    }

    async function dispatch() {
        try { const r = await api('/api/messages/dispatch', {}); toast(`${r.sent} sent (${r.provider})`, 'success'); renderOutbox(); }
        catch (e) { toast(e.message, 'error'); }
    }

    // ---------- confirmed outcomes (pilot study) ----------
    function openOutcome(patientId, name) {
        const sources = ['echocardiogram', 'angiogram', 'ecg', 'stress_test', 'clinical_diagnosis', 'hospital_admission'];
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-1"><i class="fa-solid fa-clipboard-check text-skai-primary mr-2"></i>Confirmed outcome: ${esc(name)}</h3>
            <p class="text-xs text-slate-400 mb-4">Record what tests later confirmed. SKAI compares this with its prediction to monitor accuracy and bias on local patients.</p>
            <form id="oc-form" class="space-y-3" onsubmit="event.preventDefault(); SKAI.staff.saveOutcome('${esc(patientId)}');">
                <div><label class="field-label" for="oc-cvd">Cardiovascular disease</label><select id="oc-cvd" class="field">
                    <option value="1">Confirmed</option><option value="0">Ruled out</option></select></div>
                <div><label class="field-label" for="oc-src">Confirmed by</label><select id="oc-src" class="field">${sources.map(x => `<option value="${x}">${label(x)}</option>`).join('')}</select></div>
                <div class="flex gap-2"><button type="button" onclick="SKAI.closeModal()" class="flex-1 bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">Cancel</button>
                    <button class="on-accent flex-1 bg-skai-primary text-white font-bold py-2.5 rounded-xl text-sm">Save outcome</button></div>
            </form>`);
    }

    async function saveOutcome(patientId) {
        try {
            await api(`/api/patients/${encodeURIComponent(patientId)}/outcome`, { has_cvd: $('oc-cvd').value === '1', source: $('oc-src').value });
            closeModal();
            toast('Outcome recorded', 'success');
        } catch (e) { toast(e.message, 'error'); }
    }

    // ---------- HL7 FHIR ----------
    async function exportFhir(patientId) {
        try {
            const bundle = await api(`/api/patients/${encodeURIComponent(patientId)}/fhir`);
            const link = document.createElement('a');
            link.href = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/fhir+json' }));
            link.download = `SKAI_${patientId}_FHIR.json`;
            link.click();
            toast(`FHIR bundle: ${bundle.entry.length} resources`, 'success');
        } catch (e) { toast(e.message, 'error'); }
    }

    function sampleLabBundle(patientId) {
        const today = new Date().toISOString().slice(0, 10);
        const obs = (code, display, value, unit) => ({ resource: { resourceType: 'Observation', status: 'final',
            code: { coding: [{ system: 'http://loinc.org', code, display }] }, subject: { reference: `Patient/${patientId}` },
            effectiveDateTime: today, performer: [{ display: 'NHLS Chemical Pathology (demo)' }],
            valueQuantity: { value, unit } } });
        return { resourceType: 'Bundle', type: 'collection', entry: [
            obs('2093-3', 'Total cholesterol', 5.8, 'mmol/L'), obs('2085-9', 'HDL cholesterol', 1.1, 'mmol/L'),
            obs('13457-7', 'LDL cholesterol', 3.9, 'mmol/L'), obs('2345-7', 'Glucose', 6.1, 'mmol/L')] };
    }

    function openFhirImport(patientId, name) {
        modal(`
            <h3 class="text-lg font-extrabold text-white mb-1"><i class="fa-solid fa-flask-vial text-skai-primary mr-2"></i>Import lab results: ${esc(name)}</h3>
            <p class="text-xs text-slate-400 mb-3">Paste an HL7 FHIR R4 Bundle of LOINC-coded Observations (for example from the NHLS or a private lab).
                Imported values are marked as approved by you. A lab system can also send results directly with its integration key; those count as verified.</p>
            <textarea id="fh-json" class="field font-mono text-[11px]" rows="12" spellcheck="false">${esc(JSON.stringify(sampleLabBundle(patientId), null, 2))}</textarea>
            <div class="flex gap-2 mt-3"><button type="button" onclick="SKAI.closeModal()" class="flex-1 bg-slate-800 border border-slate-600 font-bold py-2.5 rounded-xl text-sm">Cancel</button>
                <button onclick="SKAI.staff.saveFhirImport()" class="on-accent flex-1 bg-skai-primary text-white font-bold py-2.5 rounded-xl text-sm">Import</button></div>`);
    }

    async function saveFhirImport() {
        let bundle;
        try { bundle = JSON.parse($('fh-json').value); } catch (e) { toast('That is not valid JSON', 'error'); return; }
        try {
            const r = await api('/api/fhir/lab-results', bundle);
            closeModal();
            const vitals = r.imported.map(i => Object.entries(i.vitals).map(([k, v]) => `${t(`v_${k}`)} ${v}`).join(', ')).join('; ');
            toast(`Imported: ${vitals}`, 'success');
            SKAI.selectPatient(r.imported[0].patient_id);
        } catch (e) { toast(e.message, 'error'); }
    }

    // ---------- monitoring, district view and governance ----------
    async function renderMonitoring() {
        let mon, dist;
        try { [mon, dist] = await Promise.all([api('/api/monitoring'), api('/api/district')]); } catch (e) { return; }
        const p = x => (x == null ? '–' : `${Math.round(x * 100)}%`);
        const alertStyle = { danger: 'border-red-500/50 text-red-300', warning: 'border-amber-500/50 text-amber-300',
                             info: 'border-sky-500/40 text-sky-300', good: 'border-emerald-500/40 text-emerald-400' };
        const statRow = (name, s) => `<tr class="border-t border-slate-800 ${s.enough ? '' : 'text-slate-500'}">
            <td class="py-1.5 pr-3 font-bold">${name}</td><td>${s.n}</td><td>${s.with_cvd}</td><td>${p(s.recall)}</td><td>${p(s.false_alarm_rate)}</td>
            <td>${p(s.mean_predicted)} / ${p(s.observed_rate)}</td><td>${s.enough ? '' : '<span class="chip bg-slate-800 text-slate-400">too few</span>'}</td></tr>`;
        const num = (v, bad) => `<td class="py-1.5 ${bad && v ? 'text-red-400 font-bold' : ''}">${v}</td>`;
        $('panel-monitoring').innerHTML = `
            <div><h2 class="text-2xl font-extrabold text-white">Monitoring</h2>
                <p class="text-sm text-slate-400">How SKAI performs on the patients it is used on, checked against outcomes doctors confirm, and how each facility is doing.</p></div>
            ${card(state.clinician.role === 'district_manager' ? 'District overview' : 'Facility overview', 'fa-map-location-dot', `
                <div class="overflow-x-auto"><table class="w-full text-xs text-slate-300 text-left"><thead class="text-slate-400"><tr>
                    <th class="py-1.5 pr-3">Facility</th><th>Patients</th><th>High risk</th><th>High risk, no booking</th><th>Waiting now</th>
                    <th>Referrals in</th><th>Overdue referrals out</th><th>Attended / missed (30 d)</th></tr></thead><tbody>
                    ${dist.map(f => `<tr class="border-t border-slate-800"><td class="py-1.5 pr-3 font-bold text-white">${esc(f.name)}</td>
                        ${num(f.patients)}${num(f.high_risk)}${num(f.high_risk_unbooked, true)}${num(f.waiting)}${num(f.open_referrals_in)}${num(f.overdue_referrals_out, true)}
                        <td>${f.attended_30d} / <span class="${f.missed_30d ? 'text-amber-300 font-bold' : ''}">${f.missed_30d}</span></td></tr>`).join('')}
                </tbody></table></div>`)}
            <div class="grid lg:grid-cols-5 gap-5">
                <div class="lg:col-span-3">${card('Live accuracy and bias (confirmed outcomes)', 'fa-scale-balanced', `
                    <div class="space-y-2 mb-3">${mon.alerts.map(a => `<p class="text-xs border rounded-xl px-3 py-2 ${alertStyle[a.level]}">${esc(a.text)}</p>`).join('')}</div>
                    <div class="overflow-x-auto"><table class="w-full text-xs text-slate-300 text-left"><thead class="text-slate-400"><tr>
                        <th class="py-1.5 pr-3">Group</th><th>Outcomes</th><th>With CVD</th><th>Recall</th><th>False alarms</th><th>Predicted / observed</th><th></th></tr></thead><tbody>
                        ${statRow('Everyone', mon.overall)}
                        ${Object.values(mon.groups).flatMap(g => Object.entries(g).map(([n, s]) => statRow(n, s))).join('')}
                    </tbody></table></div>
                    <p class="text-[11px] text-slate-500 mt-3">Recall: share of people with confirmed heart disease whom SKAI placed in the high or critical tier (target ${p(mon.thresholds.recall_target)}).
                        A group needs ${mon.thresholds.min_n} outcomes before it is judged. Doctors record outcomes from the patient record.</p>`)}</div>
                <div class="lg:col-span-2">${card('Governance', 'fa-shield-halved', `
                    <ul class="text-xs text-slate-300 space-y-2">
                        <li><b class="text-white">Intended use:</b> helps clinics decide who to see and follow up first. A screening aid, never a diagnosis; SATS and the clinician always decide.</li>
                        <li><b class="text-white">Validated anchor:</b> patient results also show the Framingham 2008 score, which is published and externally validated.</li>
                        <li><b class="text-white">Regulation:</b> software that informs clinical decisions is a medical device (SaMD) under SAHPRA; registration and a local validation study are needed before real clinical use.</li>
                        <li><b class="text-white">Privacy (POPIA):</b> patients consent when they sign up, can download all their data, and every staff access is logged.</li>
                        <li><b class="text-white">Access:</b> staff see only their facility's patients, plus patients referred to them while the referral is open.</li>
                        <li><b class="text-white">Known limits:</b> trained on US (UCI) and South African (SAHeart, men only) data; recalibrate when local outcomes show drift.</li>
                    </ul>`)}</div>
            </div>`;
    }

    return { render, readBp, openOutcome, saveOutcome, exportFhir, openFhirImport, saveFhirImport, openArrival, previewArrival, saveArrival, visitStatus, openRecord, openBook, saveBook, apptStatus,
             refStatus, letter, openRefer, saveRefer, dispatch, can };
})();
