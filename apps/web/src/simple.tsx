import React, {useEffect, useState} from 'react'
import {Activity, Clock3, Network, BookOpen, Settings, RefreshCw, Plus, ArrowUpRight, X} from 'lucide-react'

type Row = Record<string, any>
type Props = {api:(path:string, options?:RequestInit)=>Promise<any>, page:string, navigate:(page:string)=>void,
  schedule:React.ReactNode, settings:React.ReactNode, models:React.ReactNode, platforms:React.ReactNode,
  importSource:()=>void, refresh:number}
const labels:Row={collecting:'收集与总结中',queued:'等待汇总',running:'汇总中',succeeded:'已完成',partial:'部分完成',failed:'未完成',keywords:'关键词',mentions:'收到 @',manual:'手动导入'}
const date=(s:string)=>s?new Date(s).toLocaleString('zh-CN'):''

export function SimpleHome(p:Props){
 const page=['feed','schedule','entities','digests','settings','models','platforms','jobs'].includes(p.page)?p.page:'feed'
 const [data,setData]=useState<Row>({}),[error,setError]=useState(''),[loading,setLoading]=useState(false),[revision,setRevision]=useState(0),[q,setQ]=useState(''),[next,setNext]=useState<number|null>(null)
 const [monitor,setMonitor]=useState<Row>({enabled:false,network_authorized:false,interval_minutes:30}),[saving,setSaving]=useState(false)
 const [expanded,setExpanded]=useState<string|null>(null),[entity,setEntity]=useState<Row|null>(null),[scheduleTab,setScheduleTab]=useState<'keywords'|'mentions'|'rounds'>('keywords')
 const reload=()=>setRevision(v=>v+1)
 // Reset data synchronously on page change (during render). The old
 // useEffect-based reset ran after paint, so for one frame a stale list (feed
 // items) rendered under another page's shape (rounds read .items, entities read
 // .sources) and threw, unmounting the whole tree -> white screen until refresh.
 const [lastPage,setLastPage]=useState(page)
 if(lastPage!==page){setLastPage(page);setData({});setNext(null);setExpanded(null);setScheduleTab('keywords')}
 useEffect(()=>{let live=true;setError('');setLoading(true)
  const path=page==='feed'?'/simple/feed?q='+encodeURIComponent(q):page==='entities'?'/simple/entities':page==='digests'?'/simple/digest':page==='schedule'||page==='jobs'?'/simple/rounds':null
  if(path)p.api(path).then(r=>{if(live){setData(r);setNext(r.next_offset??null)}}).catch(e=>{if(live)setError(e.message)}).finally(()=>{if(live)setLoading(false)})
  else{setData({});setLoading(false)}
  return()=>{live=false}
 },[page,revision,p.refresh,q])
 useEffect(()=>{let live=true;if(page==='schedule')p.api('/simple/mentions').then(r=>{if(live)setMonitor(r.monitor)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[page])
 useEffect(()=>{if(!['schedule','jobs','digests'].includes(page))return;const timer=setInterval(reload,15000);return()=>clearInterval(timer)},[page])
 async function saveMonitor(){setSaving(true);setError('');try{await p.api('/simple/mentions',{method:'PUT',body:JSON.stringify({enabled:monitor.enabled,network_authorized:monitor.network_authorized,interval_minutes:Number(monitor.interval_minutes)})});reload()}catch(e:any){setError(e.message)}finally{setSaving(false)}}
 async function retry(id:string){setError('');try{await p.api('/simple/rounds/'+id+'/retry',{method:'POST'});reload()}catch(e:any){setError(e.message)}}
 async function more(){if(next===null)return;setLoading(true);try{const r=await p.api('/simple/feed?q='+encodeURIComponent(q)+'&offset='+next);setData(d=>({...d,items:[...d.items,...r.items]}));setNext(r.next_offset)}catch(e:any){setError(e.message)}finally{setLoading(false)}}
 const nav=[['feed','信息动态',Activity],['schedule','自动任务',Clock3],['entities','实体',Network],['digests','日报',BookOpen],['settings','设置',Settings]] as const
 const title=({feed:'信息动态',schedule:'自动任务',entities:'实体',digests:'今日日报',jobs:'运行记录',settings:'设置',models:'设置',platforms:'设置'} as Row)[page]
 const mentionsPanel=<section className="panel"><h3>收到 @ 的视频</h3><p className="muted">定期检查已登录 B站账号收到的 @；识别到视频后自动加入总结。多个入口共用去重记录。</p><label>检查间隔（分钟）<input type="number" min={5} max={10080} value={monitor.interval_minutes} onChange={e=>setMonitor({...monitor,interval_minutes:e.target.value})}/></label><label className="check"><input type="checkbox" checked={monitor.network_authorized} onChange={e=>setMonitor({...monitor,network_authorized:e.target.checked})}/>允许读取此账号收到的 @ 通知</label><label className="check"><input type="checkbox" checked={monitor.enabled} onChange={e=>setMonitor({...monitor,enabled:e.target.checked})}/>启用定期检查</label><button className="primary" disabled={saving} onClick={saveMonitor}>保存 @ 设置</button><p className="small-note">最近检查：{date(monitor.last_checked_at)||'尚未检查'}{monitor.error_code?' · '+monitor.error_code:''}</p></section>
 const roundsPanel=<section className="panel"><h3>每轮运行记录</h3>{!data.items?.length&&<p className="muted">运行后，这里会显示视频处理结果和本轮总结。</p>}{data.items?.map((run:Row)=><article className="discovery-card" key={run.id}><div className="card-meta"><span className="tag">{labels[run.origin]}</span><strong>{labels[run.state]}</strong><span className="card-time">{date(run.created_at)}</span></div><p>{(run.items||[]).filter((i:Row)=>i.summary_id).length} / {(run.items||[]).length} 个视频已总结</p>{run.error_code&&<p className="error">{run.error_code}</p>}<button className="outline" onClick={()=>setExpanded(expanded===run.id?null:run.id)}>查看本轮</button>{['failed','partial'].includes(run.state)&&<button className="outline" onClick={()=>retry(run.id)}>重试未完成项</button>}{expanded===run.id&&<><p className="v1-summary">{run.report||'等待本轮处理完成'}</p>{(run.items||[]).map((item:Row)=><div className="status-row" key={item.video_key}><span>{item.title}</span><strong>{item.summary_id?'已总结':item.process_error||item.collect_error||item.process_state||item.collect_state||'无字幕'}</strong></div>)}</>}</article>)}</section>
 return <div className="shell"><aside className="sidebar"><a className="brand" href="#/feed"><span className="brand-icon"><Network/></span>FishingNet<span className="beta">V1</span></a><div className="workspace"><div><strong>把视频变成可读的信息</strong><small>关键词发现 · 字幕总结 · 每日汇总</small></div></div>
 <nav>{nav.map(([id,label,Icon])=><button key={id} className={(page===id||(id==='settings'&&['models','platforms'].includes(page)))?'selected':''} onClick={()=>p.navigate(id)}><Icon size={18}/>{label}</button>)}</nav>
 <div className="sidebar-bottom"><p className="small-note">每个视频一条动态。相同视频自动复用已有总结。</p></div></aside>
 <section className="main"><header><strong>{title}</strong><div className="top-actions"><button className="outline" onClick={reload}><RefreshCw size={16}/>刷新</button><button className="primary" onClick={p.importSource}><Plus size={16}/>添加视频 / 资料</button></div></header><main>
 <div className="page-heading"><div><div className="eyebrow">FISHINGNET · DAILY READING</div><h1>{title}</h1><p>{page==='feed'?'一份视频，一条总结。阅读要点，按需回到原视频。':page==='schedule'?'设置关键词和频率，自动收集、总结并更新日报。':page==='entities'?'每轮根据新内容更新对象简介，保留关联来源。':'把每天值得关注的信息集中起来。'}</p></div></div>
 {error&&<div role="alert" className="error">{error}</div>}
 {page==='feed'&&<><label className="search"><input aria-label="搜索总结" placeholder="搜索视频标题或总结…" value={q} onChange={e=>setQ(e.target.value)}/></label><div className="v1-feed">{data.items?.map((item:Row)=><article className="update-card" key={item.id}><div className="card-meta"><span className="tag green">视频 / 资料总结</span><span className="card-time">{date(item.created_at)}</span></div><h2>{item.title}</h2><p className="v1-summary">{item.summary}</p><ul>{item.points?.map((point:string,index:number)=><li key={index}>{point}</li>)}</ul><div className="card-footer"><span className="muted">{item.author_name||'导入资料'}</span>{item.canonical_url&&<a className="evidence-link" href={item.canonical_url} target="_blank" rel="noreferrer">查看原视频<ArrowUpRight size={15}/></a>}</div><small className="muted">根据来源内容总结</small></article>)}</div>{!loading&&!data.items?.length&&<div className="empty"><BookOpen/><h3>还没有视频总结</h3><p>先配置模型与 B站账号，然后创建一个关键词任务。</p><button className="primary" onClick={()=>p.navigate('schedule')}>设置自动任务</button></div>}{next!==null&&<button className="outline" disabled={loading} onClick={more}>加载更多</button>}</>}
 {page==='schedule'&&<><div className="tabs">{[['keywords','关键词任务'],['mentions','收到的 @'],['rounds','运行记录']].map(([id,label])=><button key={id} className={scheduleTab===id?'active':''} onClick={()=>setScheduleTab(id)}>{label}</button>)}</div>{scheduleTab==='keywords'&&p.schedule}{scheduleTab==='mentions'&&mentionsPanel}{scheduleTab==='rounds'&&roundsPanel}</>}
 {page==='jobs'&&roundsPanel}
 {page==='entities'&&<div className="tile-grid">{data.items?.map((entity:Row)=><article className="tile" key={entity.id} role="button" tabIndex={0} onClick={()=>setEntity(entity)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setEntity(entity)}}}><Network/><h2>{entity.canonical_name}{entity.metadata?.entity_type&&<span className="tag">{entity.metadata.entity_type}</span>}</h2><p className="v1-summary">{entity.description}</p><span className="muted">{entity.summaries?.length||0} 条信息动态 · {entity.sources?.length||0} 份视频资源 · 点击查看详情</span></article>)}{!loading&&!data.items?.length&&<p className="muted">第一轮汇总完成后，相关实体会出现在这里。</p>}</div>}
 {page==='digests'&&<section className="panel"><div className="section-caption"><h3>{data.date}</h3><a className="outline" href="/api/v1/simple/digest?download=true">下载日报</a></div><div className="v1-summary">{data.markdown||'正在读取日报…'}</div></section>}
 {['settings','models','platforms'].includes(page)&&<><div className="tabs">{[['settings','预算与账户'],['models','模型'],['platforms','B站账号']].map(([id,label])=><button key={id} className={id===page?'active':''} onClick={()=>p.navigate(id)}>{label}</button>)}</div>{page==='settings'?p.settings:page==='models'?p.models:p.platforms}</>}
 {loading&&<p role="status" className="muted">正在读取…</p>}
 {entity&&<EntityModal entity={entity} onClose={()=>setEntity(null)}/>}
 </main></section></div>
}

function EntityModal({entity,onClose}:{entity:Row,onClose:()=>void}){
 const sources:Row[]=entity.sources||[]
 const summaries:Row[]=entity.summaries||[]
 return <div className="overlay modal-overlay" role="dialog" aria-modal="true" aria-label="实体详情" onClick={onClose}>
  <section className="modal entity-modal" onClick={e=>e.stopPropagation()}>
   <div className="drawer-head"><strong>{entity.canonical_name}</strong><button onClick={onClose} aria-label="关闭实体详情"><X size={18}/></button></div>
   {entity.metadata?.entity_type&&<div className="card-meta"><span className="tag">{entity.metadata.entity_type}</span></div>}
   <p className="v1-summary">{entity.description||'暂无简介。'}</p>
   <h4>视频资源（{sources.length}）</h4>
   {sources.length===0?<p className="muted">暂无视频资源。</p>:sources.map((s:Row)=><p key={s.id}>{s.canonical_url?<a href={s.canonical_url} target="_blank" rel="noreferrer">{s.title}</a>:s.title}{s.platform&&<span className="tag">{s.platform}</span>}</p>)}
   <h4>信息动态（{summaries.length}）</h4>
   {summaries.length===0?<p className="muted">暂无相关信息动态。</p>:summaries.map((v:Row)=><div className="update-card" key={v.id}><strong>{v.title}</strong><p className="v1-summary">{v.summary}</p></div>)}
  </section>
 </div>
}
