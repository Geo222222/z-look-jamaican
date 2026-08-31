from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .adapters import RepositoryReader, classify, first, iso, parse_ts, public_wallet, record_id, record_timestamp, safe_text, score_for, status_for, title_for, utcnow

ROOT = Path(os.getenv('ZLOOK_SOURCE_ROOT', '/zlook')).resolve()
WEB = Path(__file__).resolve().parents[1] / 'web'
REFRESH_SECONDS = max(1, int(os.getenv('ZLOOK_MONITOR_REFRESH_SECONDS', '3')))

app = FastAPI(title='Z Look Jamaican Read-Only Command Center', version='1.0.0')
app.mount('/assets', StaticFiles(directory=WEB), name='assets')

@app.middleware('http')
async def read_only_guard(request: Request, call_next):
    if request.method.upper() not in {'GET', 'HEAD', 'OPTIONS'}:
        return JSONResponse({'error': 'read_only_monitor', 'message': 'Mutation methods are disabled.'}, status_code=405)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-ZLook-Monitor-Mode'] = 'read-only'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'"
    return response

def snapshot(): return RepositoryReader(ROOT).scan()

def serialize_record(rec: dict[str, Any]) -> dict[str, Any]:
    dt = record_timestamp(rec)
    return {'id':record_id(rec),'kind':classify(rec),'title':title_for(rec),'status':status_for(rec),'timestamp':iso(dt),'score':score_for(rec),'source':rec.get('_source',''),'summary':safe_text(first(rec,'summary','description','message','conclusion','result','hypothesis'),700),'raw':rec}

def ordered(records): return sorted(records,key=lambda r:record_timestamp(r) or datetime.min.replace(tzinfo=timezone.utc),reverse=True)

def experiments_from(snap):
    seen={}
    for rec in ordered(snap.records):
        if classify(rec)!='experiment': continue
        rid=record_id(rec); key=rid or f"{rec.get('_source','')}:{title_for(rec)}"
        if key not in seen:
            item=serialize_record(rec); item['mode']=safe_text(first(rec,'mode','execution_mode','environment'),80) or 'Unknown'; item['next_observation']=first(rec,'next_observation','next_run_at','next_sample_at'); item['cadence']=first(rec,'cadence','interval_seconds','frequency'); item['evidence_count']=0; seen[key]=item
    for rec in snap.records:
        exp=safe_text(first(rec,'experiment_id'),120)
        if exp and exp in seen and classify(rec) in {'evidence','heartbeat','reflection'}: seen[exp]['evidence_count']+=1
    return list(seen.values())

def opportunities_from(snap):
    out=[]
    for rec in snap.records:
        if classify(rec)!='opportunity': continue
        item=serialize_record(rec); item['mechanism']=safe_text(first(rec,'mechanism','hypothesis','description'),600); item['capital_required_usd']=first(rec,'capital_required_usd','capital_requirement_usd','capital_required'); item['confidence']=first(rec,'confidence','evidence_confidence'); out.append(item)
    return sorted(out,key=lambda x:(x['score'] is not None,x['score'] or -1),reverse=True)

def evidence_from(snap):
    out=[]
    for rec in snap.records:
        if classify(rec) not in {'evidence','heartbeat','reflection'}: continue
        item=serialize_record(rec); item['experiment_id']=safe_text(first(rec,'experiment_id'),120); out.append(item)
    return sorted(out,key=lambda x:parse_ts(x['timestamp']) or datetime.min.replace(tzinfo=timezone.utc),reverse=True)

def economic_values(snap):
    retained=exposure=None
    for rec in ordered(snap.records):
        if retained is None:
            v=first(rec,'retained_revenue_usd','retained_realized_revenue_usd','realized_revenue_usd','retained_profit_usd')
            try:
                if v is not None: retained=float(v)
            except (TypeError,ValueError): pass
        if exposure is None:
            v=first(rec,'live_exposure_usd','concurrent_exposure_usd','financial_exposure_usd')
            try:
                if v is not None: exposure=float(v)
            except (TypeError,ValueError): pass
        if retained is not None and exposure is not None: break
    return retained,exposure

def latest_heartbeat(snap):
    candidates=[]
    for rec in snap.records:
        if classify(rec)=='heartbeat':
            dt=record_timestamp(rec)
            if dt: candidates.append((dt,rec))
    if not candidates:
        for rec in snap.records:
            dt=record_timestamp(rec)
            if dt: candidates.append((dt,rec))
    return max(candidates,key=lambda x:x[0]) if candidates else None

def overview_payload(snap):
    exps=experiments_from(snap); opps=opportunities_from(snap); evidence=evidence_from(snap); retained,exposure=economic_values(snap); hb=latest_heartbeat(snap); age=heartbeat_kind=None
    if hb:
        dt,rec=hb; age=max(0,int((utcnow()-dt).total_seconds())); heartbeat_kind='heartbeat' if classify(rec)=='heartbeat' else 'latest-observation'
    health='healthy'; reasons=[]
    if not ROOT.exists(): health='offline'; reasons.append('source root missing')
    if snap.scan_errors: health='degraded'; reasons.append(f'{len(snap.scan_errors)} scan error(s)')
    if not snap.source_files: health='degraded'; reasons.append('no authoritative source files discovered')
    if age is not None and age>600: health='degraded'; reasons.append('latest observed timestamp is stale')
    top_score=opps[0]['score'] if opps and opps[0]['score'] is not None else None
    active=[e for e in exps if e['status'] in {'ACTIVE','RUNNING','MONITORING','SHADOW','OBSERVING'}]; active_exp=active[0] if active else (exps[0] if exps else None)
    return {'generated_at':snap.generated_at,'mode':'READ_ONLY','source_root':str(ROOT),'health':health,'health_reasons':reasons,'heartbeat':{'age_seconds':age,'kind':heartbeat_kind,'timestamp':iso(hb[0]) if hb else None},'metrics':{'active_experiments':len(active),'total_experiments':len(exps),'evidence_events':len(evidence),'opportunity_score':top_score,'retained_revenue_usd':retained,'live_exposure_usd':exposure,'source_files':len(snap.source_files),'scan_errors':len(snap.scan_errors)},'active_experiment':active_exp,'recent_evidence':evidence[:8],'top_opportunities':opps[:8]}

@app.get('/')
def index(): return FileResponse(WEB/'index.html')
@app.get('/api/health')
def health():
    p=overview_payload(snapshot()); return {'status':p['health'],'mode':'read-only','generated_at':p['generated_at'],'source_root':str(ROOT)}
@app.get('/api/overview')
def overview(): return overview_payload(snapshot())
@app.get('/api/experiments')
def experiments(): return {'items':experiments_from(snapshot())}
@app.get('/api/opportunities')
def opportunities(): return {'items':opportunities_from(snapshot())}
@app.get('/api/evidence')
def evidence(limit:int=Query(200,ge=1,le=2000)): return {'items':evidence_from(snapshot())[:limit]}
@app.get('/api/wallets')
def wallets():
    snap=snapshot(); items=[]; seen=set()
    for rec in snap.records:
        if classify(rec)!='wallet' and not any(k in rec for k in ('public_address','wallet_address')): continue
        item=public_wallet(rec)
        if not item: continue
        key=(item['network'],item['address'])
        if key not in seen: seen.add(key); items.append(item)
    return {'items':items,'note':'Only public wallet metadata is exposed. Secret-shaped records are excluded.'}
@app.get('/api/treasury')
def treasury():
    p=snapshot().treasury or {}; d=p.get('destinations',[]) if isinstance(p,dict) else []
    return {'purpose':p.get('purpose') if isinstance(p,dict) else None,'policy':p.get('policy',{}) if isinstance(p,dict) else {},'destinations':d if isinstance(d,list) else [],'sweep_defaults':p.get('sweep_defaults',{}) if isinstance(p,dict) else {},'source':'config/treasury_destinations.yaml' if p else None}
@app.get('/api/governor')
def governor():
    snap=snapshot(); return {'records':[serialize_record(r) for r in snap.records if classify(r)=='governor'],'document':snap.governor_text,'source':'docs/GOVERNOR.md' if snap.governor_text else None}
@app.get('/api/deployments')
def deployments():
    snap=snapshot(); return {'items':[serialize_record(r) for r in snap.records if classify(r)=='deployment']}
@app.get('/api/logs')
def logs(limit:int=Query(300,ge=1,le=2000)):
    snap=snapshot(); return {'items':snap.logs[-limit:],'redaction':'Known secret-shaped log lines are redacted before response.'}
@app.get('/api/provenance')
def provenance():
    snap=snapshot(); return {'generated_at':snap.generated_at,'sources':snap.provenance(),'scan_errors':snap.scan_errors}
@app.get('/api/events')
async def events(request:Request):
    async def stream():
        last=None
        while True:
            if await request.is_disconnected(): break
            p=overview_payload(snapshot()); encoded=json.dumps(p,sort_keys=True,default=str); digest=hashlib.sha256(encoded.encode()).hexdigest()
            if digest!=last: yield f"event: snapshot\ndata: {encoded}\n\n"; last=digest
            await asyncio.sleep(REFRESH_SECONDS)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
