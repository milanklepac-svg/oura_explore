import React, {useEffect, useState} from 'react';

const number = value => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: 2}) : 'Not recorded';
const strings = value => Array.isArray(value) ? value.filter(item => typeof item === 'string') : [];
const reconciliation = {
 matched_suunto_preferred: 'Matched Oura record · Suunto preferred',
 oura_only: 'Oura only · fallback',
 suunto_only: 'Suunto only',
 ambiguous_overlap: 'Ambiguous overlap · review separately',
};
async function read(path, options = {}){
 const response = await fetch(path, {cache:'no-store', ...options});
 if(!response.ok) throw Error('Request failed');
 return response.json();
}

export default function RunPanel(){
 const [context, setContext] = useState(null), [status, setStatus] = useState(null);
 const [loading, setLoading] = useState(true), [queueing, setQueueing] = useState(false);
 const [contextError, setContextError] = useState(''), [syncError, setSyncError] = useState('');
 async function refresh(signal){
  setLoading(true);
  const results = await Promise.allSettled([
   read('/api/running/context?include_today=true', {signal}),
   read('/api/suunto/status', {signal}),
  ]);
  if(signal?.aborted) return;
  const [runs, sync] = results;
  if(runs.status==='fulfilled' && Array.isArray(runs.value?.runs) && runs.value.summary){
   setContext(runs.value); setContextError('');
  }else{setContext(null); setContextError('Running records are unavailable. Please refresh to try again.');}
  if(sync.status==='fulfilled' && typeof sync.value?.configured==='boolean'){
   setStatus(sync.value); setSyncError('');
  }else{setStatus(null); setSyncError('Suunto status is unavailable. Please refresh before requesting a sync.');}
  setLoading(false);
 }
 useEffect(()=>{
  const controller = new AbortController();
  refresh(controller.signal);
  return ()=>controller.abort();
 },[]);
 async function queue(){
  setQueueing(true); setSyncError('');
  try{
   const job = await read('/api/suunto/sync', {method:'POST'});
   setStatus(previous=>({...previous, sync:job.sync || job}));
  }catch(e){setSyncError('Could not request the Suunto sync. Refresh its status before trying again.'); setStatus(null);}
  finally{setQueueing(false);}
 }
 const pending = ['queued','running'].includes(status?.sync?.status);
 const quota = status?.quota;
 return <section className="panel running" aria-labelledby="running-title" aria-busy={loading}>
  <div className="section-head"><div><p className="eyebrow">RECORDED RUNNING</p><h2 id="running-title">Running and Suunto</h2></div><button className="secondary running-refresh" type="button" disabled={loading||queueing} onClick={()=>refresh()}>{loading?'Refreshing running…':'Refresh running'}</button></div>
  <p className="form-note">Suunto provides running distance and duration; Oura provides sleep and recovery context. Oura-only runs are labelled as fallback records.</p>
  <p className="running-caution">This view includes the current day, which may still be partial. Recorded totals are incomplete observations, not planning totals or a reason to increase training load. No recorded run does not prove a rest day.</p>
  <div className="suunto-status" aria-live="polite">
   <h3>Suunto sync</h3>
   {status&&<>
    <p>{status.configured?'Suunto connection configured.':'Suunto connection is not configured.'}</p>
    <p>Last successful Suunto sync: {status.last_successful_sync_at || 'None recorded'}</p>
    <p className="running-sync-state">{status.sync?`Sync: ${status.sync.status}${status.sync.detail?` · ${status.sync.detail}`:''}`:'No sync job recorded.'}</p>
    {quota&&<p className="form-note">Request quota: {number(quota.requests_used)} used · {number(quota.requests_remaining)} remaining{quota.blocked_until?` · Blocked until ${quota.blocked_until}`:''}</p>}
   </>}
   <button className="secondary suunto-sync" type="button" disabled={loading||queueing||pending||!status?.configured} onClick={queue}>{queueing?'Requesting Suunto sync…':pending?'Suunto sync pending':'Sync Suunto'}</button>
   <p className="form-note">Sync requests are queued for the worker. Use Refresh running to check progress and load new records.</p>
   {syncError&&<p className="error" role="alert">{syncError}</p>}
  </div>
  {loading&&<p role="status">Loading running records…</p>}
  {contextError&&<p className="error" role="alert">{contextError}</p>}
  {context&&!loading&&<>
   <p className="form-note">Observed period: {context.start} to {context.end} · includes partial current day</p>
   <dl className="running-summary">
    {[[context.summary.recorded_runs,'Recorded runs'],[context.summary.recorded_km,'Recorded km'],[context.summary.missing_distance,'Runs missing distance'],[context.summary.missing_suunto_distance,'Suunto runs missing distance'],[context.summary.ambiguous_overlap_records,'Ambiguous overlap records']].map(([value,label])=><div key={label}><dt>{label}</dt><dd>{number(value)}</dd></div>)}
   </dl>
   <p className="form-note">Missing distances stay missing and are excluded from recorded kilometres. Ambiguous overlaps remain visible for review and are excluded from distance totals.</p>
   {!context.runs.length?<p className="empty" role="status">No recorded runs are available in this period.</p>:<ol className="running-list">{context.runs.map((run,index)=><li className="running-item" key={`${run.day}-${index}`}>
    <h3><time dateTime={run.day}>{run.day}</time> · {run.source==='suunto'?'Suunto':run.source==='oura'?'Oura':'Source not recorded'}{run.source_fallback?' · Oura fallback':''}</h3>
    <p>{reconciliation[run.reconciliation] || 'Reconciliation not recorded'}</p>
    <p>Distance: {Number.isFinite(run.km)?`${number(run.km)} km`:'Not recorded'} · Duration: {Number.isFinite(run.minutes)?`${number(run.minutes)} min`:'Not recorded'}</p>
    <p className="form-note">Duration basis: {typeof run.duration_basis==='string'?run.duration_basis.replaceAll('_',' '):'Not recorded'} · Elapsed pace: {Number.isFinite(run.elapsed_pace_minutes_per_km)?`${number(run.elapsed_pace_minutes_per_km)} min/km`:'Not recorded'}</p>
    {Array.isArray(run.provenance)&&<p className="form-note">Provenance: {strings(run.provenance).join(', ')}</p>}{run.excluded_from_totals&&<p className="form-note">Excluded from distance totals pending review.</p>}{typeof run.provenance==='string'&&<p className="form-note">Provenance: {run.provenance}</p>}
   </li>)}</ol>}
   {strings(context.limitations).length>0&&<div className="running-limitations"><h3>Record limits</h3><ul>{strings(context.limitations).map((item,index)=><li key={index}>{item}</li>)}</ul></div>}
  </>}
 </section>;
}
