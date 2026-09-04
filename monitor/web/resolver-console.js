(()=>{
'use strict';

let snapshot=null;
let resolverActive=false;
let renderQueued=false;

const $=(s)=>document.querySelector(s);
const esc=(v)=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nice=(v)=>String(v??'unknown').replaceAll('_',' ').replace(/\b\w/g,m=>m.toUpperCase());
const tone=(v)=>{const s=String(v??'').toUpperCase();if(/QUALIFIED|READY|VALID|CERT/.test(s)&&!s.includes('NOT_'))return'good';if(/DEFER|RETIRED|DEFINED|PENDING|BLOCK/.test(s))return'warn';if(/ERROR|INVALID|FAILED/.test(s))return'bad';return'muted';};
const badge=(v)=>`<span class="lab-badge ${tone(v)}">${esc(nice(v))}</span>`;
const horizon=(ns)=>{const n=Number(ns||0);if(n>=60e9&&n%60e9===0)return `${n/60e9} min`;if(n>=1e9&&n%1e9===0)return `${n/1e9} sec`;return `${n} ns`;};
const shortHash=(v)=>v?`${String(v).slice(0,12)}…${String(v).slice(-8)}`:'—';

function registry(){return snapshot?.question_registry||{};}
function activeQuestions(){return (registry().questions||[]).filter(q=>q.lifecycle_state==='RESOLVER_READY');}

function ensureNavButton(){
  const nav=$('#nav');
  if(!nav||nav.querySelector('[data-resolver-registry]'))return;
  const button=document.createElement('button');
  button.className=`nav-item ${resolverActive?'active':''}`;
  button.dataset.resolverRegistry='true';
  button.innerHTML='<span class="nav-icon">⌗</span><span>Question Registry</span>';
  const forecast=nav.querySelector('[data-page="forecasts"]');
  if(forecast?.nextSibling)nav.insertBefore(button,forecast.nextSibling);else nav.appendChild(button);
  button.addEventListener('click',(event)=>{
    event.preventDefault();
    event.stopPropagation();
    resolverActive=true;
    nav.querySelectorAll('.nav-item').forEach(item=>item.classList.remove('active'));
    button.classList.add('active');
    renderRegistry();
  });
}

function familyOrder(a,b){
  const order=['DIRECTION','MAGNITUDE','VOLATILITY','FRAGILITY','LIQUIDITY','BASIS','RELATIVE_VALUE','REGIME','PERSISTENCE','REVERSAL'];
  return order.indexOf(a)-order.indexOf(b);
}

function questionCard(q){
  const params=Object.entries(q.parameters||{}).map(([k,v])=>`<div><span>${esc(nice(k))}</span><strong>${esc(String(v))}</strong></div>`).join('');
  return `<details class="resolver-card ${tone(q.lifecycle_state)}">
    <summary>
      <div><span class="resolver-family">${esc(nice(q.family))}</span><h3>${esc(q.asks)}</h3><small>${esc(q.question_ref)}</small></div>
      <div class="resolver-summary-meta"><b>${esc(horizon(q.horizon_ns))}</b>${badge(q.lifecycle_state)}</div>
    </summary>
    <div class="resolver-detail-grid">
      <div><span>Scope</span><strong>${esc(nice(q.scope))}</strong></div>
      <div><span>Answer</span><strong>${esc(nice(q.answer_kind))}</strong></div>
      <div><span>Outcome metric</span><strong>${esc(q.outcome_metric_id)}</strong></div>
      <div><span>Evidence cutoff</span><strong>${esc(nice(q.evidence_cutoff_policy))}</strong></div>
    </div>
    <div class="resolver-rule"><span>Resolver policy</span><code>${esc(q.resolver_policy_id)}</code><span>Implementation</span><code>${esc(q.resolver_implementation_ref||'NONE')}</code></div>
    <div class="resolver-evidence"><div><span>Required artifacts</span><p>${(q.required_artifact_types||[]).map(x=>`<i>${esc(nice(x))}</i>`).join('')}</p></div><div><span>Required feature families</span><p>${(q.required_feature_families||[]).map(x=>`<i>${esc(nice(x))}</i>`).join('')}</p></div></div>
    ${params?`<div class="resolver-parameters"><span>Preregistered parameters</span>${params}</div>`:''}
    <div class="resolver-hash"><span>Definition hash</span><code>${esc(q.definition_hash)}</code></div>
  </details>`;
}

function renderRegistry(){
  ensureNavButton();
  const view=$('#view');
  const title=$('#page-title');
  if(!view)return;
  if(title)title.textContent='Question Registry';
  document.querySelectorAll('#nav .nav-item').forEach(item=>item.classList.toggle('active',item.hasAttribute('data-resolver-registry')));
  const r=registry();
  if(!r.status){
    view.innerHTML='<div class="loading-state"><div class="orbital-loader"><span></span><span></span><span></span></div><p>Resolving question registry contract…</p></div>';
    return;
  }
  const summary=r.summary||{};
  const cert=r.certificate||{};
  const reg=r.registry||{};
  const guarantees=r.guarantees||{};
  const questions=(r.questions||[]).slice().sort((a,b)=>familyOrder(a.family,b.family)||a.question_ref.localeCompare(b.question_ref));
  const active=questions.filter(q=>q.lifecycle_state==='RESOLVER_READY');
  const historical=questions.filter(q=>q.lifecycle_state!=='RESOLVER_READY');
  const guaranteeItems=Object.entries(guarantees).map(([k,v])=>`<div><span>${v?'✓':'✕'}</span><b>${esc(nice(k))}</b></div>`).join('');
  view.innerHTML=`<div class="lab-stack resolver-owner-view" data-resolver-view="true">
    <section class="lab-stage-hero resolver-hero"><div class="lab-orb">⌗</div><div><span class="lab-kicker">TRUTH SYSTEM / EXAM BOARD</span><h2>Question Registry</h2><p>The fixed examination rules ZLJ uses to determine what actually happened. Experts may answer these questions; they may not redefine them after seeing the future.</p></div>${badge(r.status)}</section>
    <section class="resolver-status-grid">
      <article><span>Registry</span><strong>${esc(reg.registry_id)}</strong><small>${esc(reg.version)}</small></article>
      <article><span>Active resolvers</span><strong>${esc(summary.active_resolver_ready??active.length)}</strong><small>ready to score future experts</small></article>
      <article><span>Historical definitions</span><strong>${esc((summary.retired_historical||0)+(summary.defined_historical||0))}</strong><small>preserved, not rewritten</small></article>
      <article><span>Execution suitability</span><strong class="resolver-deferred">DEFERRED</strong><small>until Hand shadow-execution truth exists</small></article>
    </section>
    <section class="lab-panel resolver-cert-panel"><header><div><span class="lab-kicker">FROZEN CERTIFICATE</span><h3>${esc(cert.certification_id||r.status)}</h3></div>${badge('QUALIFIED')}</header>
      <div class="resolver-certificate-line"><span>Registry hash</span><code>${esc(shortHash(reg.content_hash))}</code><span>Certificate hash</span><code>${esc(shortHash(cert.integrity?.content_hash))}</code></div>
      <div class="resolver-guarantees">${guaranteeItems}</div>
      <div class="lab-boundary"><span>Defines examination truth</span><strong>YES</strong><span>Selects models</span><strong>NO</strong><span>Claims competence</span><strong>NO</strong><span>Capital decision</span><strong>NO — BENJAMIN</strong><span>Risk authorization</span><strong>NO — WATCHMAN</strong><span>External execution</span><strong>NO — THE HAND</strong></div>
    </section>
    <section class="lab-panel"><header><div><span class="lab-kicker">ACTIVE EXAMINATION SURFACE</span><h3>What ZLJ can objectively resolve</h3></div><small>Click any question to inspect its exact truth contract.</small></header><div class="resolver-list">${active.map(questionCard).join('')}</div></section>
    <section class="lab-panel"><header><div><span class="lab-kicker">HISTORICAL SEMANTICS</span><h3>Preserved rather than rewritten</h3></div><small>Old definitions remain visible for replay and audit.</small></header><div class="resolver-list historical">${historical.map(questionCard).join('')||'<div class="lab-empty">No historical definitions.</div>'}</div></section>
    <section class="lab-panel resolver-deferred-panel"><span class="lab-kicker">INTENTIONALLY OUTSIDE THIS REGISTRY</span><h3>Execution Suitability</h3><p>ZLJ cannot claim an opportunity was executable merely because a market signal existed. This family stays deferred until The Hand can produce qualified shadow-execution truth for fills, slippage, fees, latency, partial fills, venue constraints and rejection paths.</p>${badge((summary.deferred_families||['EXECUTION_SUITABILITY'])[0])}</section>
  </div>`;
}

function ownerStatusStrip(){
  if(resolverActive||$('#page-title')?.textContent!=='Overview')return;
  const host=$('#view .lab-stack');
  if(!host||host.querySelector('.resolver-owner-strip'))return;
  const r=registry();
  if(!r.status)return;
  const models=Number((snapshot?.stages||[]).find(s=>s.id==='Z4')?.metrics?.registered_models||0);
  const strip=document.createElement('section');
  strip.className='resolver-owner-strip';
  strip.innerHTML=`<article><span>Market perception</span><strong>${((snapshot?.stages||[]).some(s=>s.id==='Z1'&&s.availability==='AVAILABLE'))?'ACTIVE':'CONSTRUCTED'}</strong><small>observation + experience</small></article><article><span>Truth system</span><strong>QUALIFIED</strong><small>${esc(registry().summary?.active_resolver_ready||0)} active resolvers</small></article><article><span>Expert school</span><strong>${models?'CONSTRUCTED':'NOT STARTED'}</strong><small>${esc(models)} registered experts/models</small></article><article><span>Benjamin handoff</span><strong>NOT BUILT</strong><small>capital authority remains none</small></article>`;
  host.insertBefore(strip,host.firstChild);
}

function certificationRegistryCard(){
  if(resolverActive||$('#page-title')?.textContent!=='Certification')return;
  const stack=$('#view .lab-stack');
  if(!stack||stack.querySelector('.resolver-cert-injection')||!registry().status)return;
  const r=registry();
  const panel=document.createElement('section');
  panel.className='lab-panel resolver-cert-injection';
  panel.innerHTML=`<header><div><span class="lab-kicker">QUESTION REGISTRY V1</span><h3>Examination truth is frozen</h3></div>${badge(r.status)}</header><div class="resolver-cert-kpis"><div><span>Active resolvers</span><b>${esc(r.summary?.active_resolver_ready||0)}</b></div><div><span>Replay</span><b>${r.guarantees?.replay_required?'REQUIRED':'—'}</b></div><div><span>Leakage guard</span><b>${r.guarantees?.leakage_protected?'ACTIVE':'—'}</b></div><div><span>Execution suitability</span><b>DEFERRED</b></div></div><button class="resolver-open-button" type="button">Open Question Registry</button>`;
  panel.querySelector('button').onclick=()=>{resolverActive=true;renderRegistry();};
  const hero=stack.firstElementChild;
  if(hero?.nextSibling)stack.insertBefore(panel,hero.nextSibling);else stack.appendChild(panel);
}

function decorate(){
  if(renderQueued)return;
  renderQueued=true;
  requestAnimationFrame(()=>{
    renderQueued=false;
    ensureNavButton();
    if(resolverActive){
      if(!$('#view [data-resolver-view]'))renderRegistry();
      return;
    }
    ownerStatusStrip();
    certificationRegistryCard();
  });
}

function bindNavigation(){
  document.addEventListener('click',(event)=>{
    const normal=event.target.closest?.('#nav [data-page]');
    if(normal)resolverActive=false;
  },true);
  new MutationObserver(decorate).observe(document.body,{childList:true,subtree:true});
}

function acceptSnapshot(value){
  if(value?.contract?.name!=='zlj-operator-console')return;
  snapshot=value;
  if(resolverActive)renderRegistry();else decorate();
}

async function initial(){
  try{const response=await fetch('/api/operator',{cache:'no-store'});if(response.ok)acceptSnapshot(await response.json());}catch(_){/* base console owns connection errors */}
  try{
    const stream=new EventSource('/api/events');
    stream.addEventListener('snapshot',(event)=>{try{acceptSnapshot(JSON.parse(event.data));}catch(_){}});
  }catch(_){/* manual refresh still works */}
}

bindNavigation();
decorate();
initial();
})();
