/* Shadow Intelligence Monitor v1.
 * Loaded after product-app.js so it replaces only the Live Market renderer.
 * It never derives market truth in the browser; every displayed claim comes
 * from the read-only operator snapshot.
 */
(function(){
  const nsTime=(v)=>{if(v===null||v===undefined)return'—';const n=Number(v);if(!Number.isFinite(n))return String(v);return new Date(n/1e6).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit'});};
  const shortHash=(v)=>{const s=String(v||'');return s?`${s.slice(0,12)}…${s.slice(-8)}`:'—';};
  const storyRows=(rows=[])=>rows.length?rows.map(r=>`<div class="shadow-story-row"><div><span>${esc(r.label)}</span><strong>${esc(r.statement)}</strong>${r.reason?`<small>${esc(r.reason)}</small>`:''}</div>${badge(r.status||'UNKNOWN')}</div>`).join(''):'<div class="lab-empty">No durable evidence yet.</div>';
  const subjectCard=(s)=>`<article class="lab-panel shadow-story-card"><header><div><span class="lab-kicker">SUBJECT</span><h3>${esc(s.label||s.subject_id||'Unknown')}</h3></div>${badge('EVIDENCE_BOUND')}</header>${storyRows(s.rows||[])}</article>`;
  const questionCard=(q)=>`<article class="shadow-question-card"><div class="shadow-question-head"><div><span>${esc(q.subject_id||'—')}</span><strong>${esc(q.question_ref||'—')}</strong></div>${badge(q.status||'UNKNOWN')}</div><div class="shadow-answer"><span>Prediction</span><code>${esc(JSON.stringify(q.answer||{}))}</code></div><div class="shadow-question-meta"><span>Models ${esc((q.model_refs||[]).join(', ')||'—')}</span><span>Resolves ${esc(nsTime(q.resolves_at_ns))}</span></div>${q.realized_answer!==null&&q.realized_answer!==undefined?`<div class="shadow-realized"><span>Resolved truth</span><code>${esc(JSON.stringify(q.realized_answer))}</code></div>`:''}<details><summary>Evidence</summary><dl class="shadow-evidence-dl"><dt>Prediction</dt><dd>${esc(q.prediction_id||'—')}</dd><dt>Content hash</dt><dd>${esc(q.prediction_content_hash||'—')}</dd><dt>Journal hash</dt><dd>${esc(q.prediction_journal_entry_hash||'—')}</dd><dt>Outcome</dt><dd>${esc(q.outcome_id||'—')}</dd><dt>Resolver</dt><dd>${esc(q.resolver_implementation_ref||'—')}</dd></dl></details></article>`;
  const expertRow=(e)=>{const c=e.competence||{};const score=c.value===null||c.value===undefined?badge(c.status||'COLLECTING'):`<strong class="shadow-score">${esc(c.value)}</strong>`;return `<tr><td><strong>${esc(e.expert_id||e.expert_ref||'—')}</strong><small>${esc(e.family||'—')}</small></td><td>${badge(e.lifecycle||'UNKNOWN')}</td><td>${score}<small>${esc(c.metric||'metric not qualified')}</small></td><td>${esc(fmt(c.sample_count||0))}</td></tr>`;};
  const registryRow=(q)=>`<tr><td><strong>${esc(q.family)}</strong><small>${esc(q.question_ref)}</small></td><td>${esc(q.asks)}</td><td>${esc((Number(q.horizon_ns||0)/1e9).toFixed(0))}s</td><td>${badge(q.lifecycle)}</td></tr>`;
  const traceItem=(label,value)=>`<div><span>${esc(label)}</span><code title="${esc(value||'')}">${esc(shortHash(value))}</code></div>`;

  live=function(){
    const si=state.snapshot?.shadow_intelligence||{};
    const story=si.market_story||{};
    const hist=si.historical_context||{};
    const qs=si.questions||{};
    const ex=si.experts||{};
    const learning=si.learning||{};
    const evidence=si.evidence||{};
    const active=qs.active||[];
    const registry=qs.registry||[];
    const experts=ex.items||[];
    const ctx=evidence.latest_market_context||{};
    const contract=si.contract||{};
    const comparable=hist.comparable_experiences===null||hist.comparable_experiences===undefined?'NOT YET QUALIFIED':fmt(hist.comparable_experiences);
    return `<div class="lab-stack shadow-monitor">
      <section class="shadow-banner"><div><span class="status-dot cyan"></span><strong>PROSPECTIVE SHADOW INTELLIGENCE</strong></div><span>NO CAPITAL AUTHORITY</span><span>NO EXECUTION</span><span>READ ONLY</span></section>
      <section class="lab-stage-hero"><div class="lab-orb">⌁</div><div><span class="lab-kicker">LIVE MARKET EXPERIENCE</span><h2>What ZLJ can prove it sees right now</h2><p>Market facts, questions, expert participation, outcomes and evidence are projected from durable kernel state. Missing evidence remains unavailable; model competence remains collecting until a qualified scoring contract earns it.</p></div>${badge(story.context_status||'UNAVAILABLE')}</section>

      <section class="shadow-grid">${(story.subjects||[]).map(subjectCard).join('')}${subjectCard({label:'MARKET',rows:story.market?.rows||[]})}</section>

      <section class="lab-grid-2">
        <article class="lab-panel shadow-history"><header><div><span class="lab-kicker">HISTORICAL CONTEXT</span><h3>Comparable experience memory</h3></div>${badge(hist.status||'NOT_QUALIFIED')}</header><div class="shadow-history-number"><strong>${esc(comparable)}</strong><span>comparable experiences</span></div><div class="shadow-history-foot"><div><span>Eligible stored experiences</span><b>${esc(fmt(hist.eligible_experience_records||0))}</b></div><p>${esc(hist.reason||'')}</p></div></article>
        <article class="lab-panel"><header><div><span class="lab-kicker">LEARNING LOOP</span><h3>Prospective shadow evidence</h3></div></header><div class="shadow-learning-grid"><div><span>Predictions</span><strong>${esc(fmt(learning.prediction_count||0))}</strong>${badge(learning.prediction_journal_status||'UNKNOWN')}</div><div><span>Resolved</span><strong>${esc(fmt(learning.resolved_outcome_count||0))}</strong></div><div><span>Awaiting</span><strong>${esc(fmt(learning.awaiting_outcome_count||0))}</strong></div><div><span>Unresolvable</span><strong>${esc(fmt(learning.unresolvable_outcome_count||0))}</strong>${badge(learning.outcome_journal_status||'UNKNOWN')}</div></div></article>
      </section>

      <section class="lab-panel"><header><div><span class="lab-kicker">QUESTION BOARD</span><h3>Active falsifiable claims</h3></div><small>${active.length} active / journaled predictions shown</small></header>${active.length?`<div class="shadow-question-grid">${active.map(questionCard).join('')}</div>`:'<div class="lab-empty">No question-bound shadow predictions have been journaled yet. The registry is ready; the prospective shadow loop still needs to produce claims.</div>'}</section>

      <section class="lab-panel"><header><div><span class="lab-kicker">EXPERT COUNCIL</span><h3>Who is answering which questions?</h3></div>${badge(ex.status||'COLLECTING')}</header>${experts.length?`<div class="shadow-table-wrap"><table class="shadow-table"><thead><tr><th>Expert</th><th>Lifecycle</th><th>Competence</th><th>Resolved samples</th></tr></thead><tbody>${experts.map(expertRow).join('')}</tbody></table></div>`:`<div class="lab-empty">${esc(ex.registry_reason||'No activated question experts yet.')}</div>`}<p class="shadow-policy">${esc(ex.competence_policy||'')}</p></section>

      <section class="lab-panel"><header><div><span class="lab-kicker">QUESTION REGISTRY</span><h3>What ZLJ is allowed to ask</h3></div><small>Resolver readiness is not model qualification.</small></header><div class="shadow-table-wrap"><table class="shadow-table"><thead><tr><th>Family</th><th>Question</th><th>Horizon</th><th>Truth resolver</th></tr></thead><tbody>${registry.map(registryRow).join('')}</tbody></table></div></section>

      <section class="lab-panel"><header><div><span class="lab-kicker">EVIDENCE TRACE</span><h3>Why the screen says what it says</h3></div>${badge(contract.truth_policy?'EVIDENCE_BOUND':'UNAVAILABLE')}</header><div class="shadow-evidence-trace">${traceItem('Market Context',ctx.artifact_id)}${traceItem('Context hash',ctx.content_hash)}${traceItem('Prediction journal tip',evidence.prediction_journal_last_entry_hash)}${traceItem('Outcome journal tip',evidence.outcome_journal_last_entry_hash)}</div><details class="shadow-raw"><summary>Raw shadow intelligence projection</summary><pre class="lab-json">${esc(JSON.stringify(si,null,2))}</pre></details></section>
    </div>`;
  };
})();
