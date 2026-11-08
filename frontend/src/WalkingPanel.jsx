import React, {useEffect, useState} from 'react';

const classifications = [
 ['recovery_compatible', 'Recovery-compatible'],
 ['additional_load', 'Additional activity load'],
 ['ordinary_activity', 'Ordinary activity'],
 ['uncertain', 'Uncertain'],
];
const labels = Object.fromEntries(classifications);
const strings = value => Array.isArray(value) ? value.filter(item => typeof item === 'string') : [];

export default function WalkingPanel(){
 const [context, setContext] = useState(null);
 const [loading, setLoading] = useState(true);
 const [error, setError] = useState('');

 async function refresh(signal){
  setLoading(true);
  setError('');
  try{
   const response = await fetch('/api/walking/context', {cache:'no-store', signal});
   if(!response.ok) throw Error('request failed');
   const result = await response.json();
   if(!result || !Array.isArray(result.walks) || !result.summary || !result.period) throw Error('invalid response');
   if(!signal?.aborted) setContext(result);
  }catch(e){
   if(!signal?.aborted){
    setContext(null);
    setError('Walking context is unavailable. Please try refreshing.');
   }
  }finally{
   if(!signal?.aborted) setLoading(false);
  }
 }
 useEffect(()=>{
  const controller = new AbortController();
  refresh(controller.signal);
  return ()=>controller.abort();
 },[]);

 return <section className="panel walking" aria-labelledby="walking-title" aria-busy={loading}>
  <div className="section-head"><div><p className="eyebrow">RECORDED MOVEMENT</p><h2 id="walking-title">Walking and recovery</h2></div><button className="secondary" type="button" disabled={loading} onClick={()=>refresh()}>{loading?'Refreshing walking…':'Refresh walking'}</button></div>
  <p className="form-note">Walks are assessed automatically from their recorded activity and available recovery context.</p>
  <p className="walking-caution">Recovery-compatible means a walk may fit a recovery day. It is not proof of recovery and does not earn fatigue credit or cancel other activity load.</p>
  <div role="status" aria-live="polite">{loading&&<p className="form-note">Loading walking context…</p>}</div>
  {error&&<p className="error" role="alert">{error}</p>}
  {context&&!loading&&<>
   <p className="form-note walking-period">Assessment period: {context.period.start}{context.period.end!==context.period.start?` to ${context.period.end}`:''}</p>
   <dl className="walking-summary">{classifications.map(([key,label])=><div key={key}><dt>{label}</dt><dd>{Number.isFinite(context.summary[key])?context.summary[key]:0}</dd></div>)}</dl>
   {context.summary.previous_day_additional_load&&<p className="walking-caution">The previous day includes additional walking load. Keep it in view when considering today's activity.</p>}
   {!context.walks.length?<p className="empty" role="status">No recorded walks are available in this period.</p>:<ol className="walking-list">{context.walks.map((walk,index)=>{
    const classification = Object.hasOwn(labels,walk.classification)?walk.classification:'uncertain';
    const reasons = strings(walk.reasons), gaps = strings(walk.data_gaps);
    return <li className="walking-item" key={`${walk.day}-${index}`}>
     <div className="walking-item-head"><h3><time dateTime={walk.day}>{walk.day}</time></h3><span className={`walking-label ${classification}`}>{labels[classification]}</span></div>
     <p className="walking-details">Duration: {Number.isFinite(walk.duration_minutes)?`${walk.duration_minutes} min`:'Not recorded'} · Intensity: {walk.intensity||'Not recorded'}</p>
     <h4>Why this assessment</h4>{reasons.length?<ul>{reasons.map((reason,i)=><li key={i}>{reason}</li>)}</ul>:<p>No assessment reasons provided.</p>}
     {(gaps.length>0||classification==='uncertain')&&<div className="walking-gaps"><h4>Missing context and uncertainty</h4>{gaps.length?<ul>{gaps.map((gap,i)=><li key={i}>{gap}</li>)}</ul>:<p>The available record does not support a confident assessment.</p>}</div>}
    </li>;
   })}</ol>}
   {strings(context.limitations).length>0&&<div className="walking-limitations"><h3>Assessment limits</h3><ul>{strings(context.limitations).map((limitation,index)=><li key={index}>{limitation}</li>)}</ul></div>}
  </>}
 </section>;
}
