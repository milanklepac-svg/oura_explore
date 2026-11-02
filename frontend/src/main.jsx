import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import TrainingPanel from './TrainingPanel.jsx';
const fmt=(value,digits=0)=>value==null?'—':Number(value).toFixed(digits);
function App(){
 const [data,setData]=useState({records:[],sync:null}),[error,setError]=useState(''),[days,setDays]=useState(30),[busy,setBusy]=useState(false);
 async function load(){try{const r=await fetch('/api/dashboard');if(!r.ok)throw Error('Dashboard unavailable');setData(await r.json());setError('');}catch(e){setError(e.message);}}
 useEffect(()=>{load();const t=setInterval(load,10000);return()=>clearInterval(t)},[]);
 async function sync(){setBusy(true);try{const r=await fetch('/api/sync',{method:'POST'});if(!r.ok)throw Error('Could not request sync');await load()}catch(e){setError(e.message)}finally{setBusy(false)}}
 const cut=new Date();cut.setDate(cut.getDate()-days);const cutoff=cut.toLocaleDateString('en-CA');
 const all=data.records, rows=all.filter(r=>r.day>=cutoff);
 const latest=(kind)=>all.filter(r=>r.kind===kind).at(-1);
 const sleep=latest('daily_sleep'),ready=latest('daily_readiness'),activity=latest('daily_activity');
 const nights=all.filter(r=>r.kind==='sleep'&&r.data.type!=='short_sleep').sort((a,b)=>a.day.localeCompare(b.day)||(a.data.total_sleep_duration||0)-(b.data.total_sleep_duration||0));
 const night=nights.at(-1);
 const dates=[...new Set(rows.map(r=>r.day))].sort().reverse();
 const get=(day,kind)=>rows.filter(r=>r.day===day&&r.kind===kind).at(-1)?.data;
 const workouts=rows.filter(r=>r.kind==='workout').sort((a,b)=>b.day.localeCompare(a.day));
 const working=['queued','running'].includes(data.sync?.status);
 return <main><header><div className="brand">PERSONAL COACH <span>LOCAL / OURA</span></div><div className="top"><div><p className="eyebrow">YOUR DAILY PICTURE</p><h1>Recovery meets movement.</h1><p className="intro">Your sleep, recovery and training, together in one place.</p></div><button disabled={busy||working} onClick={sync}>{working?'Syncing…':busy?'Requesting…':'↻ Sync now'}</button></div><div className="status"><i className={data.sync?.status==='failed'?'bad':''}/>{data.sync?`${data.sync.status} · ${data.sync.detail||'Waiting for the worker'}`:'Preparing your first sync'}<span>{data.sync?.finished_at?`Last finished ${new Date(data.sync.finished_at).toLocaleString()}`:'Data appears after your ring syncs to Oura.'}</span></div></header>
 {error&&<div role="alert" className="error">{error}</div>}
 <section className="cards">{[['Sleep score',fmt(sleep?.data.score),'out of 100',sleep?.day],['Readiness',fmt(ready?.data.score),'out of 100',ready?.day],['Sleep duration',night?fmt(night.data.total_sleep_duration/3600,1):'—','hours',night?.day],['Nightly HRV',fmt(night?.data.average_hrv),'ms',night?.day],['Resting heart rate',fmt(night?.data.lowest_heart_rate),'lowest overnight bpm',night?.day],['Daily steps',activity?.data.steps?.toLocaleString()??'—','steps',activity?.day]].map(([label,value,unit,date])=><article key={label}><p>{label}</p><strong>{value}</strong><span>{unit}</span><small>{date||'No synced data yet'}</small></article>)}</section>
 <TrainingPanel/>
 <section className="panel"><div className="section-head"><div><p className="eyebrow">LOOKING BACK</p><h2>Recovery history</h2></div><div className="range">{[7,30,90].map(n=><button className={days===n?'active':''} onClick={()=>setDays(n)} key={n}>{n} days</button>)}</div></div><div className="chart">{rows.filter(r=>r.kind==='daily_readiness').map(r=><div className="bar-col" key={r.day} title={`${r.day}: readiness ${r.data.score??'missing'}`}><div className="bar" style={{height:`${r.data.score??0}%`}}/><small>{r.day.slice(5)}</small></div>)}{!rows.length&&<p className="empty">Your first sync will load up to 90 days of available Oura history.</p>}</div>
 <div className="table-wrap"><table><thead><tr><th>Date</th><th>Sleep score</th><th>Readiness</th><th>HRV · ms</th><th>Steps</th></tr></thead><tbody>{dates.map(day=>{const n=rows.filter(r=>r.day===day&&r.kind==='sleep'&&r.data.type!=='short_sleep').sort((a,b)=>(b.data.total_sleep_duration||0)-(a.data.total_sleep_duration||0))[0]?.data;return <tr key={day}><td>{day}</td><td>{fmt(get(day,'daily_sleep')?.score)}</td><td>{fmt(get(day,'daily_readiness')?.score)}</td><td>{fmt(n?.average_hrv)}</td><td>{get(day,'daily_activity')?.steps?.toLocaleString()??'—'}</td></tr>})}</tbody></table></div></section>
 <section className="panel"><p className="eyebrow">MOVEMENT</p><h2>Recent workouts</h2>{!workouts.length?<p className="empty">No workouts available in this period.</p>:<div className="workouts">{workouts.map(r=><div className="workout" key={r.data.id}><div><strong>{r.data.label||r.data.activity||'Workout'}</strong><small>{r.day} · {r.data.source||'Oura'}</small></div><span>{r.data.start_datetime&&r.data.end_datetime?fmt((new Date(r.data.end_datetime)-new Date(r.data.start_datetime))/60000)+' min':'—'}</span><span>{r.data.distance!=null?fmt(r.data.distance/1000,1)+' km':'—'}</span></div>)}</div>}</section><footer>Oura provides the latest synced records. Missing values stay empty. Suunto connection is awaiting approval.</footer></main>
}
createRoot(document.getElementById('root')).render(<App/>);
