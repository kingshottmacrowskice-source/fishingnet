import React, {useEffect, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {QRCodeSVG} from 'qrcode.react'
import { Activity, ArrowUpRight, BookOpen, Bookmark, Check, ChevronRight, CircleHelp, FileText, FolderOpen, Layers, LogOut, Network, Plus, Search, Settings, ShieldCheck, SlidersHorizontal, Upload, X, RefreshCw, Clock3, Sparkles, UserRound, Wallet, PanelLeftClose } from 'lucide-react'
import './style.css'
import {SimpleHome} from './simple'
import {Modal} from './modal'

type Row = Record<string, any>
const labels:Row={queued:'排队中',running:'处理中',succeeded:'已完成',needs_review:'待复核',failed:'失败',cancelled:'已取消',retry_wait:'等待重试',cancel_requested:'取消中',blocked_budget:'预算暂停',blocked_credentials:'凭证阻塞',blocked_upstream:'上游受限',insufficient:'证据不足',unchecked:'未核查',current:'当前有效',stale:'已失效',high:'重要',low:'一般',opinion:'观点',factual_assertion:'事实主张',condition:'条件',experience:'条件体验',manual:'手动',schedule:'定时',retry:'重试',enqueued:'已入队',refreshed:'已刷新',excluded_term:'排除词',keyword_miss:'未命中关键词',duplicate:'已采集',over_limit:'超过上限',skipped_deleted:'来源已删除',supports:'语义支持',partially_supports:'部分支持',unsupported:'不支持',unclear:'上下文不足',proposed:'待确认',accepted:'已接受',rejected:'已拒绝',pending:'待复核',corroborated:'得到佐证',conflicted:'存在冲突',refuted:'已被反驳',E0:'E0 不可用',E1:'E1 有限间接',E2:'E2 实质支持',E3:'E3 强支持',rule:'规则',human:'人工',support:'支持',refute:'反驳',context:'背景',direct:'直接',indirect:'间接',unknown:'未知',confirmed:'已确认',shared_origin:'同源转载',verified:'已核验',unverified:'未检查',suspected_manipulation:'疑似篡改',matched:'匹配',mismatched:'不匹配',EVIDENCE_TIER_INSUFFICIENT:'证据等级不足',IMPORTANT_FACT_REQUIRES_E3:'重要事实需 E3',REFUTATION_TIER_INSUFFICIENT:'反证等级不足',CONFLICTING_SUPPORT_PRESENT:'存在强支持材料',DISPUTE_WITHOUT_MATERIAL:'争议缺材料',expression_support:'表达复核',performed:'已执行',not_configured:'未配置',unavailable:'未完成'}
const date=(s:string)=>new Date(s).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})
type ApiFailure=Error&{status?:number,code?:string,retryable?:boolean,requestId?:string}
async function api(path:string,options:RequestInit={}) {
  const csrf=document.cookie.split('; ').find(s=>s.startsWith('fn_csrf='))?.split('=')[1]||''
  let r:Response
  try{
    r=await fetch('/api/v1'+path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...options.headers}})
  }catch{
    const err:ApiFailure=new Error('无法连接本地服务，请确认后端已启动')
    err.code='NETWORK_ERROR';err.retryable=true;throw err
  }
  const text=await r.text()
  let data:Row={}
  try{data=text?JSON.parse(text):{}}catch{data={}}
  if(!r.ok){
    if(r.status===401&&!path.startsWith('/auth/'))window.dispatchEvent(new Event('fn:unauthorized'))
    const err:ApiFailure=new Error(data.error?.message||data.detail?.[0]?.msg||('请求失败（HTTP '+r.status+'）'))
    err.status=r.status;err.code=data.error?.code;err.retryable=data.error?.retryable;err.requestId=data.request_id
    throw err
  }
  return data
}
function SourceParts({source,reload}:{source:Row,reload:()=>void}){
 const parts:Row[]=source.metadata?.parts||[]
 const [selected,setSelected]=useState<string[]>([]),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
 if(source.platform!=='bilibili'||!parts.length)return null
 const toggle=(page:number)=>{const key=String(page);setSelected(current=>current.includes(key)?current.filter(value=>value!==key):(current.length>=20?current:[...current,key]));setConfirmed(false)}
 async function queueOne(page:number){
  if(busy)return
  setBusy(true);setError('');setNotice('')
  try{await api('/sources/'+source.id+'/parts/'+page+'/import',{method:'POST'});setNotice('已排队第 '+page+'P 采集任务');reload()}
  catch(e:any){setError(e.message)}
  finally{setBusy(false)}
 }
 async function queueBatch(){
  if(busy||!confirmed||!selected.length)return
  setBusy(true);setError('');setNotice('')
  try{const r=await api('/sources/'+source.id+'/parts/import',{method:'POST',body:JSON.stringify({page_nos:selected.map(Number),confirmed:true})});setNotice('已排队 '+r.queued+' 个分P；预计最多 '+r.estimated_upstream_requests+' 次上游请求');setSelected([]);setConfirmed(false);reload()}
  catch(e:any){setError(e.message)}
  finally{setBusy(false)}
 }
 return <div className="source-parts">
  <div className="section-caption"><h4>分P（{parts.length}）</h4><span className="tag amber">批量需确认</span></div>
  <p className="small-note">这里只做定位与排队：所选分P各自成为一个采集任务，不会下载视频，也不会抓取未选分P。每个任务仍受 4 次上游请求上限与低频限速约束，可在任务页取消。</p>
  <div className="part-list">{parts.map((p:Row)=><div className="part-row" key={p.page_no}><label className="check"><input type="checkbox" checked={selected.includes(String(p.page_no))} onChange={()=>toggle(Number(p.page_no))}/> 第 {p.page_no}P · {p.part_title||'未命名'}{p.duration_seconds?' · '+Math.round(Number(p.duration_seconds)/60)+' 分钟':''}</label><button className="outline" type="button" disabled={busy} onClick={()=>queueOne(Number(p.page_no))}>只导入此P</button></div>)}</div>
  <label className="check"><input type="checkbox" checked={confirmed} disabled={!selected.length} onChange={e=>setConfirmed(e.target.checked)}/>我确认排队采集所选 {selected.length} 个分P（预计最多 {selected.length*3} 次真实上游请求；不会绕过登录或风控）</label>
  <div className="tile-actions"><button className="outline" type="button" disabled={busy||!parts.length} onClick={()=>{setSelected(parts.slice(0,20).map((p:Row)=>String(p.page_no)));setConfirmed(false)}}>全选前 20 个</button><button className="primary" type="button" disabled={busy||!confirmed||!selected.length} onClick={queueBatch}>{busy?'正在排队…':'批量导入所选分P'}</button></div>
  {error&&<div role="alert" className="error">{error}</div>}
  {notice&&<div role="status" className="notice">{notice}</div>}
 </div>
}
function ClaimEvidencePanel({claim,reload}:{claim:Row,reload:()=>void}){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
 const [sources,setSources]=useState<Row[]>([]),[sourceId,setSourceId]=useState(''),[segments,setSegments]=useState<Row[]>([])
 const [form,setForm]=useState<Row>({segment_id:'',quote:'',proposition_scope:'',relation:'support',directness:'direct',conditions:'unknown',artifact_integrity:'unverified',independence_status:'unknown',origin_group_id:'',note:''})
 const materials:Row[]=claim.fact_evidence||[]
 const records:Row[]=claim.verification_records||[]
 const pending:Row[]=claim.pending_verification_records||[]
 useEffect(()=>{api('/sources?limit=100').then(r=>setSources(r.items||[])).catch(()=>{})},[])
 async function loadSegments(id:string){
  setSourceId(id);setSegments([]);setForm((f:Row)=>({...f,segment_id:'',quote:''}))
  if(!id)return
  const source=sources.find(s=>String(s.id)===id)
  if(!source?.current_version_id){setError('该来源还没有可用版本');return}
  try{const r=await api('/source-versions/'+source.current_version_id+'/segments?limit=50');setSegments(r.items||[])}catch(e:any){setError(e.message)}
 }
 async function submit(e:React.FormEvent<HTMLFormElement>){
  e.preventDefault();if(busy)return
  const source=sources.find(s=>String(s.id)===sourceId)
  if(!source?.current_version_id){setError('请选择有可用版本的来源');return}
  setBusy(true);setError('');setNotice('')
  try{
   const applicability:Row={}
   ;['product_version','date','environment','region','method'].forEach(key=>{applicability[key]=form.conditions==='matched'?'matched':form.conditions==='mismatch'?'mismatched':'unknown'})
   await api('/claims/'+claim.id+'/evidence-links',{method:'POST',body:JSON.stringify({source_version_id:source.current_version_id,segment_id:form.segment_id,quote:form.quote,relation:form.relation,proposition_scope:form.proposition_scope,directness:form.directness,artifact_integrity:form.artifact_integrity,independence_status:form.independence_status,origin_group_id:form.origin_group_id||null,applicability,note:form.note||null})})
   setNotice('事实材料已保存为待确认关系；只有接受核查记录后才会生效')
   setForm((f:Row)=>({...f,quote:'',proposition_scope:'',note:''}))
   reload()
  }catch(e:any){setError(e.message)}finally{setBusy(false)}
 }
 async function queueCheck(){
  if(busy)return;setBusy(true);setError('');setNotice('')
  try{const r=await api('/claims/'+claim.id+'/fact-checks',{method:'POST',body:JSON.stringify({reason:'工作台手动发起'})});setNotice(r.deduplicated?'已有待处理的核查任务':'核查任务已入队；规则只使用库内已保存材料');reload()}
  catch(e:any){setError(e.message)}finally{setBusy(false)}
 }
 async function decide(record:Row,decision:'accept'|'reject'){
  const reason=window.prompt(decision==='accept'?'接受理由（写入追加的复核版本）':'拒绝理由（写入追加的复核版本）','')
  if(!reason||reason.trim().length<5){setError('理由至少需要 5 个字符');return}
  setBusy(true);setError('');setNotice('')
  try{
   const r=await api('/verification-records/'+record.id+'/review',{method:'POST',body:JSON.stringify({decision,reason:reason.trim(),expected_input_fingerprint:record.input_fingerprint})})
   setNotice(decision==='accept'?'已接受该评估；事实状态='+(labels[r.claim.fact_check_status]||r.claim.fact_check_status):'已拒绝该评估，事实状态未改变')
   reload()
  }catch(e:any){setError(e.message)}finally{setBusy(false)}
 }
 return <div className="claim-evidence">
  <h3>库内事实核查</h3>
  <p className="small-note">只检索库内已保存材料，不访问外部网页。核查记录只追加；接受后会一并接受该记录使用的事实材料，并交给服务端发布门槛判断。接受一份记录不等于外部查证。</p>
  {error&&<div role="alert" className="error"><span>{error}</span></div>}
  {notice&&<div role="status" className="notice">{notice}</div>}
  <div className="tile-actions"><button className="outline" disabled={busy} onClick={queueCheck}>发起库内核查</button><span className="status amber">{labels[claim.fact_check_status]||claim.fact_check_status}</span><span className="tag">{claim.importance==='high'?'重要事实':'一般事实'}</span></div>
  {materials.length>0&&<div className="evidence-block"><h4>事实材料（{materials.length}）</h4>{materials.map(m=><div key={m.id} className="fact-material">
   <div><span className={'tag '+(m.role==='refute'?'amber':'')}>{m.role==='support'?'支持':m.role==='refute'?'反驳':'背景'}</span><span className="tag">{m.tier}</span><span className={'tag '+(m.status==='accepted'?'green':'')}>{labels[m.status]||m.status}</span><small>{m.material_title} · {m.payload?.anchor?.segment_id}</small></div>
   <blockquote>{m.payload?.anchor?.quote}</blockquote>
   <p>{m.payload?.proposition_scope}</p>
   <small>{labels[m.payload?.directness]||m.payload?.directness}材料 · 独立性 {labels[m.payload?.independence_status]||m.payload?.independence_status} · 完整性 {labels[m.payload?.artifact_integrity]||m.payload?.artifact_integrity}</small>
   {m.payload?.note&&<small>用途说明：{m.payload.note}</small>}
   {m.payload?.assessment_record_id&&<small>已由核查记录 {String(m.payload.assessment_record_id).slice(0,8)} 接受</small>}
   {!m.displayable&&<small className="muted">来源已不可用或保留期已到</small>}
  </div>)}</div>}
  {pending.length>0&&<div className="evidence-block"><h4>待人工复核的核查记录</h4>{pending.map(r=><div key={r.id} className="fact-material">
   <div><span className="tag amber">v{r.version_no} · 待复核</span><strong>{labels[r.assessment?.status]||r.assessment?.status}</strong><small>支持 {r.assessment?.support_strength} / 反驳 {r.assessment?.refutation_strength}</small></div>
   <p>{(r.assessment?.gaps||[]).join('；')}</p>
   {r.model_runs?.length>0&&<small className="muted">模型建议：{r.model_runs[0].status==='performed'?((labels[r.model_runs[0].verdict]||r.model_runs[0].verdict)+' · '+(r.model_runs[0].agreement==='agrees'?'与规则分档一致':'与规则分档不一致')+(r.model_runs[0].truncated?' · 材料有截断':'')):('未完成（'+String(r.model_runs[0].error_code||r.model_runs[0].status)+'）')}；建议不改变分档、状态或发布门槛</small>}
   {(r.gate||[]).length>0?<small className="muted">硬门槛未满足：{(r.gate||[]).map((code:string)=>labels[code]||code).join('、')}；接受会被拒绝</small>:<small className="muted">接受前请确认材料范围、条件与缺口；重要事实需要 E3 且必须人工接受</small>}
   <div className="tile-actions"><button className="outline" disabled={busy} onClick={()=>decide(r,'accept')}>接受评估</button><button className="outline" disabled={busy} onClick={()=>decide(r,'reject')}>拒绝</button></div>
  </div>)}</div>}
  <form className="discovery-form" onSubmit={submit}>
   <div className="form-row"><label>来源<select value={sourceId} onChange={e=>loadSegments(e.target.value)} required><option value="">选择来源</option>{sources.map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</select></label>
   <label>原文片段<select value={form.segment_id} onChange={e=>setForm({...form,segment_id:e.target.value})} required><option value="">选择片段</option>{segments.map((s:Row)=><option key={s.segment_id} value={s.segment_id}>{s.segment_id} · {(s.text||'').slice(0,40)}</option>)}</select></label></div>
   <label>逐字引文（必须出现在所选片段中）<input value={form.quote} onChange={e=>setForm({...form,quote:e.target.value})} required minLength={2}/></label>
   <label>命题范围（这份材料支持/反驳主张的哪一部分）<input value={form.proposition_scope} onChange={e=>setForm({...form,proposition_scope:e.target.value})} required minLength={2}/></label>
   <div className="form-row">
    <label>作用<select value={form.relation} onChange={e=>setForm({...form,relation:e.target.value})}><option value="support">支持</option><option value="refute">反驳</option><option value="context">背景</option></select></label>
    <label>直接性<select value={form.directness} onChange={e=>setForm({...form,directness:e.target.value})}><option value="direct">直接</option><option value="indirect">间接</option></select></label>
    <label>条件适用<select value={form.conditions} onChange={e=>setForm({...form,conditions:e.target.value})}><option value="unknown">未知（不能算匹配）</option><option value="matched">五项条件均匹配</option><option value="mismatch">存在不匹配</option></select></label>
   </div>
   <div className="form-row">
    <label>材料完整性<select value={form.artifact_integrity} onChange={e=>setForm({...form,artifact_integrity:e.target.value})}><option value="unverified">未检查</option><option value="verified">已核验</option><option value="suspected_manipulation">疑似篡改</option></select></label>
    <label>独立性<select value={form.independence_status} onChange={e=>setForm({...form,independence_status:e.target.value})}><option value="unknown">未知</option><option value="confirmed">已确认独立</option><option value="shared_origin">同源转载</option></select></label>
    <label>上游来源组<input value={form.origin_group_id} onChange={e=>setForm({...form,origin_group_id:e.target.value})} placeholder="留空按来源区分"/></label>
   </div>
   <label>用途说明（同一来源同时作表达溯源与事实证据时必须填写）<input value={form.note} onChange={e=>setForm({...form,note:e.target.value})}/></label>
   <button className="primary" disabled={busy||!segments.length}>添加待确认材料</button>
  </form>
  <div className="section-caption"><h4>核查版本</h4><span className="tag">{records.length} 条</span></div>
  {records.map(r=><div className="status-row" key={r.id}><span>v{r.version_no} · {labels[r.checker_type]||r.checker_type} · {labels[r.review_status]||r.review_status}</span><strong>{labels[r.assessment?.status]||r.assessment?.status} · 支持 {r.assessment?.support_strength} / 反驳 {r.assessment?.refutation_strength}</strong>{r.model_runs?.[0]?.status==='performed'&&<small> · 模型建议 {({supports:'支持',refutes:'反驳',conflicts:'冲突',insufficient:'不足',unclear:'不确定'} as Row)[r.model_runs[0].verdict]||r.model_runs[0].verdict}</small>}</div>)} </div>
}
function App(){
 const [user,setUser]=useState<Row|null>(null),[initialized,setInitialized]=useState<boolean|null>(null),[page,setPageState]=useState(location.hash.slice(2)||'feed'),[items,setItems]=useState<Row[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[search,setSearch]=useState(''),[filter,setFilter]=useState('all'),[drawer,setDrawer]=useState<Row|null>(null),[importing,setImporting]=useState(false),[settings,setSettings]=useState<Row>({}),[capabilities,setCapabilities]=useState<Row>({}),[digest,setDigest]=useState<Row>({}),[refresh,setRefresh]=useState(0),[notice,setNotice]=useState(''),[cursor,setCursor]=useState<string|null>(null),[busyMore,setBusyMore]=useState(false),[acting,setActing]=useState(false),[scope,setScope]=useState<{kind:'topic'|'entity',id:string,name:string}|null>(null),[days,setDays]=useState('7')
 const setPage=(value:string)=>{setItems([]);setCursor(null);setError('');setNotice('');location.hash='/'+value;setPageState(value)}
 useEffect(()=>{const sync=()=>{setItems([]);setCursor(null);setError('');setPageState(location.hash.slice(2)||'feed')};window.addEventListener('hashchange',sync);return()=>window.removeEventListener('hashchange',sync)},[])
 useEffect(()=>{const onUnauthorized=()=>{setUser(null);setError('登录已过期，请重新登录')};window.addEventListener('fn:unauthorized',onUnauthorized);return()=>window.removeEventListener('fn:unauthorized',onUnauthorized)},[])
 const bump=()=>setRefresh(v=>v+1)
 const listPath=(id:string,next:string|null)=>{
  const cursorQuery=next?'&cursor='+encodeURIComponent(next):''
  if(id==='feed'){
   const since=days==='all'?'':new Date(Date.now()-Number(days)*86400000).toISOString()
   const scopeQuery=scope?('&'+scope.kind+'_id='+encodeURIComponent(scope.id)):''
   return '/updates?limit=20&q='+encodeURIComponent(search)+(filter==='saved'?'&bookmarked=true':filter==='unread'?'&unread=true':'')+scopeQuery+(since?'&since='+encodeURIComponent(since):'')+cursorQuery
  }
  if(id==='entities')return '/nodes?limit=20&kind=entity'+cursorQuery
  if(id==='topics')return '/nodes?limit=20&kind=topic'+cursorQuery
  if(id==='sources')return '/sources?limit=20&q='+encodeURIComponent(search)+cursorQuery
  if(id==='jobs')return '/jobs?limit=20'+cursorQuery
  if(id==='verification')return '/verification-queue?limit=20'+cursorQuery
  return ''
 }
 async function loadMore(){
  if(!cursor||busyMore)return
  setBusyMore(true);setError('')
  try{const r=await api(listPath(page,cursor));setItems(v=>[...v,...r.items]);setCursor(r.next_cursor)}
  catch(e:any){setError(e.message)}
  finally{setBusyMore(false)}
 }
 useEffect(()=>{api('/auth/status').then(r=>setInitialized(r.initialized)).catch(e=>setError(e.message));api('/auth/me').then(setUser).catch(()=>{})},[])
 useEffect(()=>{if(!user)return;api('/settings').then(setSettings).catch(e=>setError(e.message));api('/capabilities').then(setCapabilities).catch(e=>setError(e.message))},[user])
 useEffect(()=>{if(!user)return;let alive=true;setBusy(true);setError('');setItems([]);setCursor(null)
  if(page==='digests'){api('/digests/today').then(r=>{if(alive)setDigest(r)}).catch(e=>setError(e.message)).finally(()=>setBusy(false))}
  else if(page==='sessions')api('/auth/sessions').then(r=>{if(alive){setItems(r.items);setCursor(r.next_cursor)}}).catch(e=>{if(alive)setError(e.message)}).finally(()=>{if(alive)setBusy(false)})
  else if(page==='platforms')api('/platforms').then(r=>{if(alive){setItems(r.items);setCursor(null)}}).catch(e=>{if(alive)setError(e.message)}).finally(()=>{if(alive)setBusy(false)})
  else if(listPath(page,null))api(listPath(page,null)).then(r=>{if(alive){setItems(r.items);setCursor(r.next_cursor)}}).catch(e=>{if(alive)setError(e.message)}).finally(()=>{if(alive)setBusy(false)})
  else setBusy(false)
  return()=>{alive=false}
 },[user,page,refresh,search,filter,scope,days])
 useEffect(()=>{if(page!=='jobs'||!user||cursor)return;const t=setInterval(bump,5000);return()=>clearInterval(t)},[page,user,cursor])
 async function act(work:()=>Promise<any>,message='已保存'){if(acting)return;setActing(true);setError('');setNotice('');try{await work();setNotice(message);bump()}catch(e:any){setError(e.message)}finally{setActing(false)}}
 async function openDetail(kind:string,id:string){try{const value=await api('/'+kind+'/'+id);setDrawer({kind,...value})}catch(e:any){setError(e.message)}}
 const nav=[['feed','信息动态',Activity],['entities','关注对象',Network],['topics','主题',Layers],['sources','来源库',FolderOpen],['digests','日报',BookOpen]] as const
 const admin=[['verification','事实核查',ShieldCheck],['models','模型管理',SlidersHorizontal],['platforms','平台账号',UserRound],['schedule','采集与调度',Clock3],['jobs','任务与费用',Wallet],['settings','系统设置',Settings]] as const
 const titles:Row={feed:'信息动态',entities:'关注对象',topics:'主题',sources:'来源库',digests:'每日信息简报',verification:'事实核查工作台',models:'模型管理',platforms:'平台账号',schedule:'采集与调度',jobs:'任务与费用',settings:'系统设置',sessions:'登录会话'}
 if(initialized===null)return <div className="auth"><h1>FishingNet</h1><p>{error||'正在连接本地知识库…'}</p></div>
 if(!user)return <Auth initialized={initialized} onDone={()=>{setInitialized(true);api('/auth/me').then(setUser)}}/>
 return <><SimpleHome api={api} page={page} navigate={setPage} refresh={refresh} importSource={()=>setImporting(true)}
  schedule={<DiscoveryPanel bump={bump}/>}
  settings={<><BudgetPanel settings={settings} reload={()=>api('/settings').then(setSettings)}/><ProfileForm user={user} timezone={settings.timezone} done={()=>api('/auth/me').then(setUser)}/><ClearDataPanel/><button className="outline" onClick={()=>api('/auth/logout',{method:'POST'}).then(()=>setUser(null))}>退出登录</button></>}
  models={<ModelsPanel bump={bump}/>} platforms={<PlatformPanel items={items} bump={bump}/>}/>
  {importing&&<ImportModal close={()=>setImporting(false)} done={()=>{setImporting(false);bump()}}/>}</>

}
function sourceTag(s:Row){
 if(s.platform!=='bilibili')return <span className="tag green">原文已保存</span>
 const sub=s.metadata?.subtitle_status
 const map:Row={ready:sub==='available'?'字幕已采集':'已采集',missing:sub==='language_mismatch'?'字幕语言不匹配':'未取得字幕',permission_required:'需要登录',pending:'待采集',failed:'采集失败'}
 const tone=s.content_status==='ready'?'green':(s.content_status==='missing'||s.content_status==='permission_required')?'amber':''
 return <span className={'tag '+tone}>{map[s.content_status]||s.content_status}</span>
}
function Empty({title,text}:{title:string,text:string}){return <div className="empty"><div className="empty-icon"><Network size={30}/></div><h3>{title}</h3><p>{text}</p></div>}
function More({busy,onClick}:{busy:boolean,onClick:()=>void}){return <div className="more-row"><button className="outline" disabled={busy} onClick={onClick}>{busy?'正在加载…':'加载更多'}</button></div>}
function NodeCreate({kind,onDone}:{kind:'topic'|'entity',onDone:()=>void}){
 const [name,setName]=useState(''),[description,setDescription]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false)
 const label=kind==='topic'?'主题':'实体'
 async function submit(e:React.FormEvent<HTMLFormElement>){e.preventDefault();if(busy)return;setBusy(true);setError('');try{await api('/nodes',{method:'POST',body:JSON.stringify({kind,name,description})});setName('');setDescription('');onDone()}catch(err:any){setError(err.message)}finally{setBusy(false)}}
 return <div className="panel topic-create"><h3>新建{label}</h3><form className="inline-form" onSubmit={submit}><input aria-label={label+'名称'} required placeholder={label+'名称'} value={name} onChange={e=>setName(e.target.value)} maxLength={200}/><input aria-label={label+'简介'} placeholder="简介（可选）" value={description} onChange={e=>setDescription(e.target.value)} maxLength={2000}/><button className="primary" disabled={busy}>创建{label}</button></form>{error&&<div role="alert" className="error">{error}</div>}</div>
}
function NodeDetail({node,reload,onViewFeed,onOpenUpdate,onOpenClaim}:{node:Row,reload:()=>void,onViewFeed:(kind:'topic'|'entity',id:string,name:string)=>void,onOpenUpdate:(id:string)=>void,onOpenClaim:(id:string)=>void}){
 const [topics,setTopics]=useState<Row[]>([]),[entities,setEntities]=useState<Row[]>([]),[parentChoice,setParentChoice]=useState(''),[childChoice,setChildChoice]=useState(''),[entityChoice,setEntityChoice]=useState(''),[entityTopic,setEntityTopic]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false)
 useEffect(()=>{let alive=true;Promise.all([api('/nodes?kind=topic&limit=100'),api('/nodes?kind=entity&limit=100')]).then(([t,e])=>{if(alive){setTopics(t.items);setEntities(e.items)}}).catch((e:any)=>{if(alive)setError(e.message)});return()=>{alive=false}},[node.id])
 async function run(work:()=>Promise<any>){if(busy)return;setBusy(true);setError('');try{await work();reload()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 const link=(child:string,parent:string)=>api('/topic-links',{method:'POST',body:JSON.stringify({child_id:child,parent_id:parent})})
 const unlink=(id:string)=>api('/topic-links/'+id,{method:'DELETE'})
 const isTopic=node.node_kind==='topic'
 const parents=node.parents||[],children=node.children||[],members=node.entities||[],belongs=node.topics||[]
 return <div className="node-detail"><span className="tag">{isTopic?'主题':'实体'}</span><h2>{node.canonical_name}</h2>{node.description&&<p>{node.description}</p>}{node.overview&&<p className="callout">{node.overview}</p>}<button className="outline" onClick={()=>onViewFeed(node.node_kind,node.id,node.canonical_name)}>查看该{isTopic?'主题':'实体'}动态</button>
  {isTopic&&<><h3>父主题</h3>{parents.length===0&&<p className="muted">暂无父主题</p>}{parents.map((p:Row)=><div className="link-row" key={p.relation_id}><span>{p.canonical_name}</span><button disabled={busy} onClick={()=>run(()=>unlink(p.relation_id))}>移除</button></div>)}<div className="inline-form"><select aria-label="选择父主题" value={parentChoice} onChange={e=>setParentChoice(e.target.value)}><option value="">选择父主题…</option>{topics.filter((t:Row)=>t.id!==node.id).map((t:Row)=><option key={t.id} value={t.id}>{t.canonical_name}</option>)}</select><button className="outline" disabled={!parentChoice||busy} onClick={()=>run(()=>link(node.id,parentChoice))}>添加父主题</button></div>
   <h3>子主题</h3>{children.length===0&&<p className="muted">暂无子主题</p>}{children.map((c:Row)=><div className="link-row" key={c.relation_id}><span>{c.canonical_name}</span><button disabled={busy} onClick={()=>run(()=>unlink(c.relation_id))}>移除</button></div>)}<div className="inline-form"><select aria-label="选择子主题" value={childChoice} onChange={e=>setChildChoice(e.target.value)}><option value="">选择子主题…</option>{topics.filter((t:Row)=>t.id!==node.id).map((t:Row)=><option key={t.id} value={t.id}>{t.canonical_name}</option>)}</select><button className="outline" disabled={!childChoice||busy} onClick={()=>run(()=>link(childChoice,node.id))}>添加子主题</button></div>
   <h3>关联实体</h3>{members.length===0&&<p className="muted">暂无关联实体</p>}{members.map((m:Row)=><div className="link-row" key={m.relation_id}><span>{m.canonical_name}</span><button disabled={busy} onClick={()=>run(()=>unlink(m.relation_id))}>移除</button></div>)}<div className="inline-form"><select aria-label="选择实体" value={entityChoice} onChange={e=>setEntityChoice(e.target.value)}><option value="">选择实体…</option>{entities.map((e:Row)=><option key={e.id} value={e.id}>{e.canonical_name}</option>)}</select><button className="outline" disabled={!entityChoice||busy} onClick={()=>run(()=>link(entityChoice,node.id))}>关联实体</button></div></>}
  {!isTopic&&<><h3>所属主题</h3>{belongs.length===0&&<p className="muted">尚未加入主题</p>}{belongs.map((t:Row)=><div className="link-row" key={t.relation_id}><span>{t.canonical_name}</span><button disabled={busy} onClick={()=>run(()=>unlink(t.relation_id))}>移除</button></div>)}<div className="inline-form"><select aria-label="选择主题" value={entityTopic} onChange={e=>setEntityTopic(e.target.value)}><option value="">选择主题…</option>{topics.map((t:Row)=><option key={t.id} value={t.id}>{t.canonical_name}</option>)}</select><button className="outline" disabled={!entityTopic||busy} onClick={()=>run(()=>link(node.id,entityTopic))}>加入主题</button></div></>}
  {error&&<div role="alert" className="error">{error}</div>}
  {node.updates?.length>0&&<><h3>相关动态</h3>{node.updates.map((u:Row)=><button className="claim-text" key={u.id} onClick={()=>onOpenUpdate(u.id)}>{(u.title||'').replace('示例作者观点（非事实确认）：','').replace('【合成示例】','')}</button>)}</>}
  {node.claims?.length>0&&<><h3>来源主张</h3>{node.claims.map((c:Row)=><button className="claim-text" key={c.id} onClick={()=>onOpenClaim(c.id)}>{c.statement}</button>)}</>}
 </div>
}
function PlatformPanel({items,bump}:{items:Row[],bump:()=>void}){
 const [expandedPlatform,setExpandedPlatform]=useState<string|null>(null)
 const [cookie,setCookie]=useState(''),[alias,setAlias]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[check,setCheck]=useState<Row|null>(null)
 const [qr,setQr]=useState<Row|null>(null),[qrBusy,setQrBusy]=useState(false)
 const bili=items.find(x=>x.platform==='bilibili')
 const conn:Row=bili?.connection||{}
 const stateLabel:Row={not_configured:'未配置',unconfigured:'未配置',unverified:'已保存 · 未验证',connected:'登录态有效',expired:'登录态已失效',invalid:'凭证无效',unavailable:'平台暂不可用',risk_control:'风控冷却中'}
 const recovery:Row=bili?.recovery||{}
 const qrLabel:Row={waiting_scan:'等待手机扫码',scanned:'已扫码，请在手机上确认',connected:'扫码登录成功',expired:'二维码已过期',error:'扫码失败',cancelled:'已取消'}
 async function run(work:()=>Promise<any>,message:string){
  if(busy)return false
  setBusy(true);setError('');setNotice('')
  try{const r=await work();if(r&&r.status)setCheck(r);setNotice(message);bump();return true}
  catch(e:any){setError(e.message);return false}
  finally{setBusy(false)}
 }
 async function startQr(){
  if(qrBusy)return
  setQrBusy(true);setError('');setNotice('')
  try{const r=await api('/platforms/bilibili/qr',{method:'POST'});setQr(r)}
  catch(e:any){setError(e.message)}
  finally{setQrBusy(false)}
 }
 async function cancelQr(){
  if(!qr||qrBusy)return
  setQrBusy(true)
  try{await api('/platforms/bilibili/qr/'+qr.auth_id+'/cancel',{method:'POST'})}
  catch(e:any){setError(e.message)}
  finally{setQr(null);setQrBusy(false)}
 }
 useEffect(()=>{
  if(!qr||!['waiting_scan','scanned'].includes(qr.status))return
  const timer=setInterval(async()=>{
   try{
    const r=await api('/platforms/bilibili/qr/'+qr.auth_id)
    setQr((current:Row|null)=>current?{...current,...r}:current)
    if(r.status==='connected'){setNotice('扫码登录成功：凭证已加密保存，可点击“检查连接”确认账号');bump()}
   }catch(e:any){setError(e.message);setQr(null)}
  },2000)
  return()=>clearInterval(timer)
 },[qr?.auth_id,qr?.status])
 const checkLabel=check?(stateLabel[check.status]||check.status):''
 return <>
  <div className="section-caption"><h3>平台账号</h3><span>管理各个平台的登录信息</span></div>
  <article className="platform-card"><div className="platform-account-head"><div><h4>B站</h4><p className="muted">{stateLabel[conn.status]||'未配置'}</p></div><button className="outline" aria-expanded={expandedPlatform==='bilibili'} aria-controls="bilibili-account" onClick={()=>setExpandedPlatform(expandedPlatform==='bilibili'?null:'bilibili')}>{expandedPlatform==='bilibili'?'收起':'修改'}</button></div>
  <div id="bilibili-account" hidden={expandedPlatform!=='bilibili'} className="platform-account-body">
  <div className="status-row"><span>B站 连接状态</span><strong>{stateLabel[conn.status]||'未配置'}</strong></div>
  <div className="status-row"><span>凭证</span><strong>{conn.credential_configured?'已加密保存 SESSDATA（不可回读）':'未提供'}</strong></div>
  <div className="status-row"><span>最近检查</span><strong>{conn.last_checked_at?date(conn.last_checked_at):'尚未检查'}</strong></div>
  <div className="status-row"><span>风控冷却</span><strong>{conn.cooldown_until?'冷却至 '+date(conn.cooldown_until):'无'}</strong></div>
  <div className="status-row"><span>待恢复采集</span><strong>{recovery.blocked_jobs||0} 个凭证阻塞 · {recovery.failed_jobs||0} 个可恢复失败</strong></div>
  <div className="status-row"><span>采集范围</span><strong>单条 BV 链接 · 公开元数据 + 你有权访问的字幕</strong></div>
  <div className="status-row"><span>关键词发现 / 定时调度</span><strong>已实现（真实网络烟测未执行）</strong></div>
  <div className="status-row"><span>本地转写</span><strong>未实现</strong></div>
  <div className="status-row"><span>真实网络验收</span><strong>未执行（等待你在本机授权后低频烟测）</strong></div>
  <div className="qr-box">
   <div className="section-caption"><h4>扫码登录（实验）</h4><span className="tag amber">会发起真实网络请求</span></div>
   <p className="small-note">用 B站 App 扫描二维码并在手机上确认。这是网页登录流程，不是官方开放平台授权，也没有做真实网络验收；二维码内容与轮询都只在本机进行，凭证仍加密保存、不回显。</p>
   {!qr&&<button className="outline" disabled={qrBusy} onClick={startQr}>{qrBusy?'正在获取二维码…':'生成登录二维码'}</button>}
   {qr&&<div className="qr-panel">
    {['waiting_scan','scanned'].includes(qr.status)&&<QRCodeSVG value={qr.qr_url} size={168} level="M" marginSize={2}/>}
    <p role="status">{qrLabel[qr.status]||qr.status}{qr.message&&qr.status!=='connected'?' · '+qr.message:''}</p>
    {qr.status==='connected'&&<p className="small-note">凭证已加密保存。可以点击上方“检查连接”读取账号昵称与字幕能力。</p>}
    <div className="tile-actions">
     {['waiting_scan','scanned'].includes(qr.status)&&<button className="outline" onClick={cancelQr} disabled={qrBusy}>取消</button>}
     {['expired','error','cancelled'].includes(qr.status)&&<button className="outline" onClick={async()=>{setQr(null);await startQr()}} disabled={qrBusy}>刷新二维码</button>}
     {qr.status==='connected'&&<button className="outline" onClick={()=>setQr(null)}>完成</button>}
    </div>
   </div>}
  </div>
  <p className="muted">本应用登录不代表已登录内容平台。凭证仅用于读取字幕清单，不会发给模型供应商，也不会写入日志；导入与检查都由你手动触发。</p>
  <div className="profile-forms">
   <form onSubmit={async e=>{e.preventDefault();if(await run(()=>api('/platforms/bilibili/credential',{method:'PUT',body:JSON.stringify({cookie,alias})}),'凭证已加密保存，请点击“检查连接”验证')){setCookie('')}}}>
    <label>B站 Cookie（写入后不可读取）<input type="password" autoComplete="off" value={cookie} onChange={e=>setCookie(e.target.value)} placeholder="SESSDATA=… 或只粘贴 SESSDATA 的值" maxLength={4096}/></label>
    <label>备注名称（可选）<input value={alias} onChange={e=>setAlias(e.target.value)} maxLength={80} placeholder="例如：我的主账号"/></label>
    <button className="outline" disabled={busy||!cookie}>加密保存凭证</button>
   </form>
   <div className="tile-actions">
    <button className="outline" disabled={busy||!conn.credential_configured} onClick={()=>run(()=>api('/platforms/bilibili/check',{method:'POST'}),'已执行一次真实登录态检查')}>检查连接</button>
    <button className="outline" disabled={busy||!recovery.recoverable} onClick={()=>run(()=>api('/platforms/bilibili/recover',{method:'POST'}),'已重新排队被阻塞的采集任务')}>恢复被阻塞的采集</button>
    <button className="outline" disabled={busy||!conn.credential_configured} onClick={()=>run(()=>api('/platforms/bilibili/credential',{method:'DELETE'}),'已移除本地凭证')}>移除本地凭证</button>
   </div>
   <p className="small-note">登录态过期时必须重新提供 Cookie 或扫码；风控冷却期内不会自动重试。恢复只重新排队已有采集任务，不会修改来源、启用状态或联网授权。</p>
   {check&&<p role="status" className="small-note">检查结果：{checkLabel} · {check.live_check?'已发生一次真实网络请求':'未发起网络请求'}{check.detail?' · '+check.detail:''}</p>}
   {conn.last_check_detail&&<p className="small-note">上次检查：{conn.last_check_detail}</p>}
   {error&&<div role="alert" className="error">{error}</div>}
   {notice&&<div role="status" className="notice">{notice}</div>}
  </div>
  </div></article>
  {items.filter(x=>x.platform!=='bilibili').map(p=><article className="platform-card" key={p.platform}><div className="platform-account-head"><div><h4>{p.label}</h4><p className="muted">{p.implemented?'可配置':'暂未支持'}</p></div><button className="outline" aria-expanded={expandedPlatform===p.platform} aria-controls={'account-'+p.platform} onClick={()=>setExpandedPlatform(expandedPlatform===p.platform?null:p.platform)}>{expandedPlatform===p.platform?'收起':'修改'}</button></div>{expandedPlatform===p.platform&&<div id={'account-'+p.platform} className="platform-account-body"><p className="muted">{p.implemented?(p.requirements||[]).join('；'):'该平台暂未开放账号接入。'}</p></div>}</article>)}
  <p className="small-note">采集结果进入来源库与证据链，不生成视频推荐流；缺字幕、需要登录、风控与失败分别显示，不会伪装成分析成功。</p>
 </>
}
function DiscoveryPanel({bump}:{bump:()=>void}){
 const [list,setList]=useState<Row[]>([]),[meta,setMeta]=useState<Row>({}),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[detail,setDetail]=useState<Row|null>(null),[showCreate,setShowCreate]=useState(false)
 async function load(){setLoading(true);setError('');try{const r=await api('/discovery-sources');setList(r.items);setMeta(r)}catch(e:any){setError(e.message)}finally{setLoading(false)}}
 useEffect(()=>{load()},[])
 async function run(work:()=>Promise<any>,message:string|((result:any)=>string)){if(busy)return;setBusy(true);setError('');setNotice('');try{const result=await work();setNotice(typeof message==='function'?message(result):message);await load();bump()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 async function toggle(s:Row,field:'enabled'|'network_authorized'){await run(async()=>{
  const payload:Row={name:s.name,platform:s.platform,keywords:s.keywords,exclude_terms:s.exclude_terms,languages:s.languages,match_mode:s.match_mode,language_policy:s.language_policy,page_policy:s.page_policy,target_pages:s.target_pages,max_results_per_run:s.limits.max_results_per_run,max_pages_per_run:s.limits.max_pages_per_run,max_requests_per_run:s.limits.max_requests_per_run,rescan_after_hours:s.limits.rescan_after_hours,schedule_interval_minutes:s.schedule_interval_minutes,daily_time:s.daily_time,schedule_timezone:s.schedule_timezone,enabled:s.enabled,network_authorized:s.network_authorized}
  payload[field]=!s[field]
  await api('/discovery-sources/'+s.id,{method:'PATCH',headers:{'If-Match':String(s.revision)},body:JSON.stringify(payload)})
 },field==='enabled'?'已更新启用状态':'已更新联网授权')}
 async function runNow(s:Row){await run(()=>api('/discovery-sources/'+s.id+'/run',{method:'POST'}),'已排队一次关键词发现；下方运行记录会显示每个候选的处理决定')}
 async function openDetailPanel(s:Row){try{setDetail(await api('/discovery-sources/'+s.id))}catch(e:any){setError(e.message)}}
 async function removeSource(s:Row){if(!window.confirm('删除关键词来源「'+s.name+'」？已采集的来源与运行记录会保留。'))return;await run(()=>api('/discovery-sources/'+s.id,{method:'DELETE',headers:{'If-Match':String(s.revision)}}),'关键词来源已删除')}
 return <>
  <h3>关键词任务</h3>
  <p className="small-note">按关键词筛选最新视频，获取字幕并生成一条完整总结；本轮结束后汇总信息、更新实体和当日日报。已经总结的视频不会再次调用模型。</p>
  <button className="primary" onClick={()=>setShowCreate(true)}><Plus size={16}/>新建关键词来源</button>

  {showCreate&&<KeywordSourceModal close={()=>setShowCreate(false)} done={()=>{setShowCreate(false);load();bump()}}/>}

  {loading?<p className="muted">正在读取关键词来源…</p>:list.length===0?<Empty title="还没有关键词来源" text="先创建一个来源并保存；只有你明确勾选联网授权并启用后，调度器才会处理它。"/>:null}
  {list.map(s=><article className="discovery-card" key={s.id}>
   <div className="card-meta"><span className="tag green">{s.platform}</span>{s.enabled?<span className="tag">已启用</span>:<span className="tag">已停用</span>}{s.network_authorized?<span className="tag amber">已授权联网</span>:<span className="tag">未授权联网</span>}<span className="card-time">{s.last_run_at?'最近运行 '+date(s.last_run_at):'尚未运行'}</span></div>
   <h4>{s.name}</h4>
   <p className="muted">关键词：{(s.keywords||[]).join('、')}{s.exclude_terms?.length?' · 排除：'+s.exclude_terms.join('、'):''}</p>
   <div className="status-row"><span>每轮/页数/间隔</span><strong>{s.limits.max_results_per_run} 条 · {s.limits.max_pages_per_run} 页 · {s.daily_time?'每天 '+s.daily_time+'（'+s.schedule_timezone+'）':s.schedule_interval_minutes+' 分钟'}</strong></div>
   <div className="status-row"><span>累计运行 / 来源</span><strong>{s.run_count} 次 · {s.source_count} 个来源{s.last_run_state?' · 最近 '+labels[s.last_run_state]:''}</strong></div>
   <div className="status-row"><span>增量 watermark</span><strong>{s.watermark.seen_count} 条已入队{s.watermark.newest_published_at?' · 最新发布 '+date(s.watermark.newest_published_at):''}</strong></div>
   <div className="status-row"><span>下次调度</span><strong>{s.next_run_at?date(s.next_run_at):'未安排'}{s.failure_streak?' · 连续失败 '+s.failure_streak+' 次':''}</strong></div>
   {s.last_error_code&&<p className="small-note">最近错误：{s.last_error_code}（失败会退避重试，不会伪装成“没有新内容”）</p>}
   <div className="tile-actions">
    <button className="outline" disabled={busy||!s.network_authorized} onClick={()=>runNow(s)}>立即运行一次</button>
    <button className="outline" disabled={busy} onClick={()=>toggle(s,'enabled')}>{s.enabled?'停用':'启用'}</button>
    <button className="outline" disabled={busy} onClick={()=>toggle(s,'network_authorized')}>{s.network_authorized?'撤销联网授权':'授权联网'}</button>
    <button className="outline" onClick={()=>openDetailPanel(s)}>运行记录</button>
    <button className="outline" disabled={busy} onClick={()=>removeSource(s)}>删除</button>
   </div>
  </article>)}

  {detail&&<div className="discovery-detail">
   <div className="section-caption"><h4>{detail.name} · 运行记录</h4><button aria-label="关闭运行记录" onClick={()=>setDetail(null)}><X size={14}/></button></div>
   <p className="small-note">{detail.explanation}</p>
   {(detail.runs||[]).length===0?<p className="muted">还没有运行记录。</p>:(detail.runs||[]).map((r:Row)=><div className="list-row" key={r.id}>
    <Clock3/><div className="row-content"><strong>{labels[r.state]||r.state} · {labels[r.trigger]||r.trigger}</strong><small>{date(r.requested_at)} · 读取 {r.pages_visited}/{r.request_budget} 页 · 候选 {r.discovered} · 入队 {r.enqueued} · 重复 {r.duplicates} · 排除 {r.excluded}{r.error_code?' · '+r.error_code:''}</small>{r.detail&&<small>{r.detail}</small>}</div>
   </div>)}
   {(detail.recent_decisions||[]).length>0&&<><h4>最近候选决定</h4><div className="decision-list">{(detail.recent_decisions||[]).map((d:Row,i:number)=><div className="status-row" key={d.external_id+i}><span>{d.external_id}</span><strong>{labels[d.decision]||d.decision}</strong></div>)}</div></>}
  </div>}

  {error&&<div role="alert" className="error">{error}</div>}
  {notice&&<div role="status" className="notice">{notice}</div>}
  <p className="muted">说明：关键词发现的上游请求尚未完成真实网络烟测；本文档和界面都不会把它写成“真实采集已验证”。分P批量已支持，但必须逐次明确确认，且不会自动抓取未选分P。</p>
 </>
}
function KeywordSourceModal({close,done}:{close:()=>void,done:()=>void}){
 const [name,setName]=useState(''),[keywords,setKeywords]=useState(''),[terms,setTerms]=useState(''),[languages,setLanguages]=useState('zh-CN, zh-Hans')
 const [limit,setLimit]=useState('10'),[pages,setPages]=useState('2'),[intervalMinutes,setIntervalMinutes]=useState('1440')
 const [authorized,setAuthorized]=useState(false),[enabled,setEnabled]=useState(false),[dailyTime,setDailyTime]=useState('08:00'),[scheduleMode,setScheduleMode]=useState('daily')
 const [busy,setBusy]=useState(false),[error,setError]=useState('')
 const split=(value:string)=>value.split(/[,，、\n]/).map(v=>v.trim()).filter(Boolean)
 async function submit(e:React.FormEvent<HTMLFormElement>){
  e.preventDefault();if(busy)return
  setBusy(true);setError('')
  try{await api('/discovery-sources',{method:'POST',body:JSON.stringify({name,keywords:split(keywords),exclude_terms:split(terms),languages:split(languages),max_results_per_run:Number(limit),max_pages_per_run:Number(pages),schedule_interval_minutes:Number(intervalMinutes),daily_time:scheduleMode==='daily'?dailyTime:null,schedule_timezone:'Asia/Shanghai',enabled,network_authorized:authorized})});done()}
  catch(e:any){setError(e.message)}
  finally{setBusy(false)}
 }
 return <div className="overlay modal-overlay" role="dialog" aria-modal="true" aria-label="新建关键词来源" onClick={close}>
  <section className="modal" onClick={e=>e.stopPropagation()}>
   <div className="drawer-head"><strong>新建关键词来源</strong><button onClick={close} aria-label="关闭新建关键词来源"><X size={18}/></button></div>
   <form onSubmit={submit} className="discovery-form">
    <label>名称<input required value={name} maxLength={80} onChange={e=>setName(e.target.value)} placeholder="例如：AI 行业观察"/></label>
    <label>关键词（最多 5 个，逗号分隔）<input required value={keywords} onChange={e=>setKeywords(e.target.value)} placeholder="AI, 大模型"/></label>
    <label>排除词（可选，命中标题或作者即跳过）<input value={terms} onChange={e=>setTerms(e.target.value)} placeholder="广告, 搬运"/></label>
    <label>优先字幕语言（逗号分隔）<input value={languages} onChange={e=>setLanguages(e.target.value)} placeholder="zh-CN, zh-Hans"/></label>
    <div className="form-row">
     <label>每轮视频上限<input type="number" min={1} max={20} value={limit} onChange={e=>setLimit(e.target.value)}/></label>
     <label>每轮页数<input type="number" min={1} max={3} value={pages} onChange={e=>setPages(e.target.value)}/></label>
     <label>运行方式<select value={scheduleMode} onChange={e=>setScheduleMode(e.target.value)}><option value="daily">每天固定时间（北京时间）</option><option value="interval">按间隔运行</option></select></label>{scheduleMode==='daily'?<label>每天<input type="time" required value={dailyTime} onChange={e=>setDailyTime(e.target.value)}/></label>:<label>定时间隔（分钟）<input type="number" min={60} max={10080} value={intervalMinutes} onChange={e=>setIntervalMinutes(e.target.value)}/></label>}
    </div>
    <label className="check"><input type="checkbox" checked={authorized} onChange={e=>setAuthorized(e.target.checked)}/> 我授权本机在运行时低频访问 B站 公开搜索接口（可随时关闭；未勾选时不会发起任何请求）</label>
    <label className="check"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/> 创建后立即启用定时调度</label>
    {error&&<div role="alert" className="error">{error}</div>}
    <button className="primary" disabled={busy||!name||!split(keywords).length}>{busy?'正在保存…':'创建关键词来源'}</button>
   </form>
  </section>
 </div>
}
function StatusRow({name,value}:{name:string,value:string}){return <div className="status-row"><span>{name}</span><strong>{value}</strong></div>}
function ClearDataPanel(){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
 async function clear(){
  if(!window.confirm('确定清空所有信息、实体、日报和自动任务数据吗？登录信息、平台账号和模型配置会保留；此操作不可撤销。'))return
  setBusy(true);setError('');setNotice('')
  try{const r=await api('/simple/clear-content',{method:'POST'});setNotice('已清空内容数据（共 '+r.cleared+' 张表）')}
  catch(e:any){setError(e.message)}
  finally{setBusy(false)}
 }
 return <div className="budget-form">
  <div className="section-caption"><h4>清空内容数据</h4><span className="tag amber">不可撤销</span></div>
  <p className="small-note">清空信息、实体、日报与自动任务（关键词来源、@设置、运行记录、任务队列）。保留登录信息、平台账号与模型配置。</p>
  {error&&<div role="alert" className="error">{error}</div>}
  {notice&&<div role="status" className="notice">{notice}</div>}
  <button className="outline" disabled={busy} onClick={clear}>{busy?'正在清空…':'清空内容数据'}</button>
 </div>
}
function BudgetPanel({settings,reload}:{settings:Row,reload:()=>void}){
 const [daily,setDaily]=useState(String(settings.daily_budget??'0')),[monthly,setMonthly]=useState(String(settings.monthly_budget??'0'))
 const [currency,setCurrency]=useState(String(settings.budget_currency||'USD')),[policy,setPolicy]=useState(String(settings.data_policy||'local_only'))
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
 useEffect(()=>{setDaily(String(settings.daily_budget??'0'));setMonthly(String(settings.monthly_budget??'0'));setCurrency(String(settings.budget_currency||'USD'));setPolicy(String(settings.data_policy||'local_only'))},[settings.revision])
 async function saveBudget(e?:React.FormEvent){
  if(e)e.preventDefault()
  if(busy)return
  setBusy(true);setError('');setNotice('')
  try{await api('/settings/budget',{method:'PUT',headers:{'If-Match':String(settings.revision)},body:JSON.stringify({daily_budget:daily,monthly_budget:monthly,budget_currency:currency,data_policy:policy})});setNotice('预算与数据策略已保存');reload()}
  catch(e:any){setError(e.message)}
  finally{setBusy(false)}
 }
 return <div className="budget-form">
  <div className="section-caption"><h4>云调用预算与数据策略</h4><span className="tag amber">默认零预算</span></div>
  <p className="small-note">预算按 UTC 日/月统计；每次真实调用前按输入/输出上限预留最坏费用，调用后按供应商返回的 token 用量结算。费用是按本地保存单价估算，未经供应商账单确认。</p>
  <div className="form-row">
   <label>每日预算（{currency}）<input type="number" min="0" step="0.0001" value={daily} onChange={e=>setDaily(e.target.value)}/></label>
   <label>每月预算（{currency}）<input type="number" min="0" step="0.0001" value={monthly} onChange={e=>setMonthly(e.target.value)}/></label>
   <label>预算币种<select value={currency} onChange={e=>setCurrency(e.target.value)}><option value="USD">USD</option><option value="CNY">CNY</option></select></label>
  </div>
  <label>数据发送策略<select value={policy} onChange={e=>setPolicy(e.target.value)}><option value="local_only">仅本地：不向云端供应商发送任何材料</option><option value="approved_cloud">允许云端：仅在任务路由显式确认后才发送</option></select></label>
  <div className="status-row"><span>今日已用 / 预算</span><strong>{Number(settings.budget?.day_spent||0).toFixed(4)} / {Number(settings.daily_budget||0).toFixed(2)} {currency}</strong></div>
  <div className="status-row"><span>本月已用 / 预算</span><strong>{Number(settings.budget?.month_spent||0).toFixed(4)} / {Number(settings.monthly_budget||0).toFixed(2)} {currency}</strong></div>
  {error&&<div role="alert" className="error">{error}</div>}
  {notice&&<div role="status" className="notice">{notice}</div>}
  <button className="outline" disabled={busy} onClick={()=>saveBudget()}>{busy?'正在保存…':'保存预算与数据策略'}</button>
 </div>
}
// Mirrors the server's ready rule in ai/gateway.route_list so the page can explain
// exactly which switch is off instead of leaving the status stuck on 未就绪.
function routeCheck(r:Row,models:Row[]){
 const byId=new Map<string,Row>(models.map(m=>[String(m.id),m]))
 const reasons:string[]=[]
 if(r.mode!=='model'){
  if(!r.enabled)reasons.push('任务未启用：勾选「启用」后保存')
  return {ready:!!r.enabled,reasons,
    note:'当前是 Mock / 规则模式，不调用模型；要测试模型请改为「模型调用」'}
 }
 if(!r.enabled)reasons.push('任务未启用：勾选「启用」后保存')
 if(!r.primary_model_id)reasons.push('未选择主模型')
 else{
  const m=byId.get(String(r.primary_model_id))
  if(!m)reasons.push('主模型已被删除，请重新选择')
  else{
   if(!m.enabled)reasons.push('主模型「'+(m.display_name||m.model_id)+'」未启用：到「模型列表」启用')
   if(!m.provider_enabled)reasons.push('供应商「'+m.provider_name+'」已停用：到「供应商连接」启用')
  }
 }
 for(const id of (r.fallback_model_ids||[])){
  const m=byId.get(String(id))
  if(!m)reasons.push('备用模型已被删除，请移除')
  else if(!m.enabled)reasons.push('备用模型「'+(m.display_name||m.model_id)+'」未启用')
  else if(!m.provider_enabled)reasons.push('备用模型「'+(m.display_name||m.model_id)+'」的供应商已停用')
 }
 return {ready:reasons.length===0,reasons,note:''}
}
function ModelsPanel({bump}:{bump:()=>void}){
 const [tab,setTab]=useState<'providers'|'models'|'routes'|'calls'>('providers')
 const [providers,setProviders]=useState<Row[]>([]),[models,setModels]=useState<Row[]>([])
 const [routes,setRoutes]=useState<Row[]>([]),[routeRevision,setRouteRevision]=useState('0')
 const [savedRoutes,setSavedRoutes]=useState('')
 const [cost,setCost]=useState<Row>({}),[calls,setCalls]=useState<Row[]>([])
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
 const [cloudConfirm,setCloudConfirm]=useState(false)
 const [pf,setPf]=useState({name:'',protocol:'openai_compatible',base_url:'',api_key:'',is_local:false,enabled:false})
 const [mf,setMf]=useState({provider_id:'',model_id:'',display_name:'',tier:'standard',context_window:'8192',max_output_tokens:'1024',input_price_per_mtok:'0',output_price_per_mtok:'0',supports_structured_output:false,enabled:false})
 const [dialog,setDialog]=useState<'provider'|'model'|null>(null)
  const [editingModel,setEditingModel]=useState<Row|null>(null)
 async function load(){
  setError('')
  try{
   const [p,m,r,c,i]=await Promise.all([api('/model-providers'),api('/models'),api('/task-routes'),api('/costs/summary'),api('/ai/invocations?limit=10')])
   setProviders(p.items);setModels(m.items);setRoutes(r.items);setSavedRoutes(JSON.stringify(r.items));setRouteRevision(String(r.revision));setCost(c);setCalls(i.items)
   setMf(v=>({...v,provider_id:v.provider_id||(p.items[0]?.id||'')}))
  }catch(e:any){setError(e.message)}
 }
 useEffect(()=>{load()},[])
 async function run(work:()=>Promise<any>,message:string|((result:any)=>string)){if(busy)return;setBusy(true);setError('');setNotice('');try{const result=await work();setNotice(typeof message==='function'?message(result):message);await load();bump()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 async function createProvider(e:React.FormEvent<HTMLFormElement>){
  e.preventDefault()
  await run(async()=>{
   const payload:Row={name:pf.name,protocol:pf.protocol,base_url:pf.protocol==='mock'?'':pf.base_url,is_local:pf.protocol==='local'||pf.protocol==='mock'?true:pf.is_local,enabled:pf.enabled}
   if(pf.api_key)payload.api_key=pf.api_key
   await api('/model-providers',{method:'POST',body:JSON.stringify(payload)})
   setPf(v=>({...v,name:'',base_url:'',api_key:''}));setDialog(null)
  },'供应商已保存；密钥只加密保存，界面只显示掩码')
 }
 async function toggleProvider(p:Row){await run(()=>api('/model-providers/'+p.id,{method:'PATCH',headers:{'If-Match':String(p.revision)},body:JSON.stringify({enabled:!p.enabled})}),p.enabled?'供应商已停用':'供应商已启用')}
 async function testProvider(p:Row){await run(()=>api('/model-providers/'+p.id+'/test',{method:'POST'}),(r:Row)=>'连接测试：'+r.provider_status+' · '+(r.note||''))}
 async function deleteProvider(p:Row){if(!window.confirm('删除供应商「'+p.name+'」？本地密钥会一并删除；不会调用平台撤权接口。'))return;await run(()=>api('/model-providers/'+p.id,{method:'DELETE',headers:{'If-Match':String(p.revision)}}),'供应商已删除')}
 async function createModel(e:React.FormEvent<HTMLFormElement>){
  e.preventDefault()
  await run(async()=>{
   if(editingModel){
    await api('/models/'+editingModel.id,{method:'PATCH',headers:{'If-Match':String(editingModel.revision)},body:JSON.stringify({display_name:mf.display_name||editingModel.model_id,tier:mf.tier,context_window:Number(mf.context_window),max_output_tokens:Number(mf.max_output_tokens),input_price_per_mtok:mf.input_price_per_mtok,output_price_per_mtok:mf.output_price_per_mtok,supports_structured_output:mf.supports_structured_output,enabled:mf.enabled})})
    setEditingModel(null)
   }else{
    await api('/models',{method:'POST',body:JSON.stringify({provider_id:mf.provider_id,model_id:mf.model_id,display_name:mf.display_name||mf.model_id,tier:mf.tier,context_window:Number(mf.context_window),max_output_tokens:Number(mf.max_output_tokens),input_price_per_mtok:mf.input_price_per_mtok,output_price_per_mtok:mf.output_price_per_mtok,supports_structured_output:mf.supports_structured_output,enabled:mf.enabled})})
   }
   setMf(v=>({...v,model_id:'',display_name:''}));setDialog(null)
  },editingModel?'模型已更新；价格/上限/结构化输出改动立即生效':'模型已保存；默认不启用，也不会自动被任务路由使用')
 }
 function startEditModel(m:Row){
  setEditingModel(m);setError('');setDialog('model')
  setMf({provider_id:String(m.provider_id),model_id:String(m.model_id),display_name:String(m.display_name||''),tier:String(m.tier||'standard'),context_window:String(m.context_window||8192),max_output_tokens:String(m.max_output_tokens||1024),input_price_per_mtok:String(m.input_price_per_mtok??'0'),output_price_per_mtok:String(m.output_price_per_mtok??'0'),supports_structured_output:!!m.supports_structured_output,enabled:!!m.enabled})
 }
 function cancelEditModel(){setDialog(null);setError('');setEditingModel(null);setMf(v=>({...v,model_id:'',display_name:''}))}
 async function toggleModel(m:Row){await run(()=>api('/models/'+m.id,{method:'PATCH',headers:{'If-Match':String(m.revision)},body:JSON.stringify({enabled:!m.enabled})}),m.enabled?'模型已停用':'模型已启用')}
 async function deleteModel(m:Row){if(!window.confirm('删除模型「'+m.display_name+'」？被任务路由引用或已有调用账本时会被拒绝。'))return;await run(()=>api('/models/'+m.id,{method:'DELETE',headers:{'If-Match':String(m.revision)}}),'模型已删除')}
 function patchRoute(task:string,patch:Row){setRoutes(list=>list.map(r=>r.task===task?{...r,...patch}:r))}
 async function saveRoutes(){
  await run(async()=>{
   const payload=routes.map(r=>({task:r.task,mode:r.mode,enabled:r.enabled,primary_model_id:r.primary_model_id||null,fallback_model_ids:(r.fallback_model_ids||[]).slice(0,3),max_input_tokens:Number(r.max_input_tokens),max_output_tokens:Number(r.max_output_tokens),timeout_seconds:Number(r.timeout_seconds),max_attempts:Number(r.max_attempts),data_policy:r.data_policy}))
   const result=await api('/task-routes',{method:'PUT',headers:{'If-Match':routeRevision},body:JSON.stringify({routes:payload,confirm_cloud:cloudConfirm})})
   setRoutes(result.items);setSavedRoutes(JSON.stringify(result.items));setRouteRevision(String(result.revision))
  },'任务路由已整组保存；未启用的任务不会调用模型')
 }
 async function testRoute(r:Row){
  if(!window.confirm('会对「'+r.label+'」发送一条合成测试提示，可能产生真实费用。继续？'))return
  await run(()=>api('/ai/test-call',{method:'POST',body:JSON.stringify({task:r.task,confirm_spend:true})}),(result:Row)=>'测试调用：'+result.status+' · 费用 '+result.cost.status+' '+result.cost.amount+' '+result.cost.currency+' · 结果未写入知识库')
 }
 const enabledModels=models.filter(m=>m.enabled)
 const routesDirty=!!savedRoutes&&JSON.stringify(routes)!==savedRoutes
 const budget=cost.budget
 const budgetTitle=!budget?'正在读取预算…':`每日预算 ${budget.daily_budget} / 每月预算 ${budget.monthly_budget} ${cost.currency||''}`
 const budgetNote=!budget?'预算尚未读取成功，请稍后重试。':Number(budget.daily_budget)<=0||Number(budget.monthly_budget)<=0?'每日和每月预算均需大于 0，才能进行有费用的模型调用。':`今日剩余 ${budget.day_remaining} / 本月剩余 ${budget.month_remaining} ${cost.currency||''}。`
 const activeRoutes=routes.filter(r=>['update_generation','digest_polish'].includes(r.task)&&r.mode==='model'&&routeCheck(r,models).ready).length
 return <div className="models-panel">
  <div className="tabs model-tabs">
   {([['providers','供应商连接'],['models','模型列表'],['routes','任务分配'],['calls','调用记录与费用']] as const).map(([id,name])=><button className={tab===id?'active':''} key={id} onClick={()=>setTab(id)}>{name}</button>)}
  </div>
  {error&&!dialog&&<div role="alert" className="error"><span>{error}</span><button onClick={()=>load()}>重试</button></div>}
  {notice&&<div role="status" className="notice">{notice}<button onClick={()=>setNotice('')}><X size={16}/></button></div>}
  {tab==='providers'&&<>
   <div className="mode-banner"><span className="mode-icon"><SlidersHorizontal size={18}/></span><div><strong>{budgetTitle}</strong><p>{budgetNote}</p><p>模型任务配置就绪：{activeRoutes} / 2。预算不会自动启用模型；请在“任务分配”中选择模型、启用任务，并确认供应商和模型均已启用。使用云端模型还需在预算设置和任务分配中允许云端。保存配置不会立即调用模型。</p></div></div>
   <div className="section-caption"><h3>供应商连接</h3><button className="primary" onClick={()=>{setError('');setPf({name:'',protocol:'openai_compatible',base_url:'',api_key:'',is_local:false,enabled:false});setDialog('provider')}}><Plus size={16}/>添加供应商</button></div>
   {dialog==='provider'&&<Modal title="添加供应商" protectChanges busy={busy} onClose={()=>setDialog(null)}>{error&&<div role="alert" className="error modal-error">{error}</div>}   <form className="discovery-form" onSubmit={createProvider}>
    <div className="form-row">
     <label>供应商名称<input required value={pf.name} onChange={e=>setPf({...pf,name:e.target.value})}/></label>
     <label>协议<select value={pf.protocol} onChange={e=>setPf({...pf,protocol:e.target.value,is_local:e.target.value==='local'||e.target.value==='mock'})}><option value="openai">OpenAI 原生（Chat Completions）</option><option value="openai_compatible">OpenAI 兼容端点</option><option value="local">本地兼容端点</option><option value="mock">Mock（仅合成，不联网）</option></select></label>
     <label>端点<input value={pf.base_url} disabled={pf.protocol==='mock'} onChange={e=>setPf({...pf,base_url:e.target.value})} placeholder="https://api.openai.com/v1" autoComplete="off"/></label>
    </div>
    <label>API 密钥（只写入，不回显）<input type="password" value={pf.api_key} onChange={e=>setPf({...pf,api_key:e.target.value})} autoComplete="new-password" placeholder="保存后只显示掩码"/></label>
    <label className="check"><input type="checkbox" checked={pf.enabled} onChange={e=>setPf({...pf,enabled:e.target.checked})}/>保存后立即启用该供应商（仍需要模型与任务路由才可能调用）</label>
    <div className="modal-actions"><button className="outline" type="button" disabled={busy} onClick={()=>setDialog(null)}>取消</button><button className="primary" disabled={busy}>{busy?'正在保存…':'保存供应商'}</button></div>
   </form></Modal>}

   <p className="small-note">支持 OpenAI 原生、OpenAI 兼容与本地兼容端点；Anthropic/Gemini 原生协议未实现。协议或端点变更会把状态重置为“未测试”。</p>
   {providers.length===0?<Empty title="尚未配置模型供应商" text="没有供应商时系统保持 Mock 模式，不产生任何模型费用。"/>:providers.map(p=><article className="discovery-card" key={p.id}>
    <div className="section-caption"><h4>{p.name}</h4><span className={p.status==='ok'?'tag green':'tag amber'}>{p.status_label}</span></div>
    <div className="status-row"><span>协议 / 端点</span><strong>{p.protocol_label} · {p.base_url||'不联网'}</strong></div>
    <div className="status-row"><span>密钥</span><strong>{p.configured?'已配置 '+p.credential_hint:'未配置'} · 模型 {p.model_count} 个</strong></div>
    <div className="status-row"><span>最近测试 / 错误</span><strong>{p.last_tested_at?date(p.last_tested_at):'未测试'}{p.last_error_code?' · '+p.last_error_code:''}</strong></div>
    <div className="tile-actions">
     <button className="outline" disabled={busy||!p.model_count} onClick={()=>testProvider(p)}>测试连接</button>
     <button className="outline" disabled={busy} onClick={()=>toggleProvider(p)}>{p.enabled?'停用':'启用'}</button>
     <button className="outline" disabled={busy} onClick={()=>deleteProvider(p)}>删除</button>
    </div>
   </article>)}
  </>}
  {tab==='models'&&<>
   <div className="section-caption"><h3>模型列表</h3><button className="primary" disabled={!providers.length} onClick={()=>{setError('');setEditingModel(null);setMf({provider_id:providers[0]?.id||'',model_id:'',display_name:'',tier:'standard',context_window:'8192',max_output_tokens:'1024',input_price_per_mtok:'0',output_price_per_mtok:'0',supports_structured_output:false,enabled:false});setDialog('model')}}><Plus size={16}/>添加模型</button></div>
   {!providers.length&&<p className="muted">请先在“供应商连接”中添加供应商，再添加模型。</p>}
   {dialog==='model'&&<Modal title={editingModel?'编辑模型':'添加模型'} protectChanges busy={busy} onClose={cancelEditModel}>{error&&<div role="alert" className="error modal-error">{error}</div>}   <form className="discovery-form" onSubmit={createModel}>
    <div className="form-row">
     <label>供应商<select value={mf.provider_id} disabled={!!editingModel} onChange={e=>setMf({...mf,provider_id:e.target.value})} required>{providers.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
     <label>真实模型 ID<input required value={mf.model_id} disabled={!!editingModel} onChange={e=>setMf({...mf,model_id:e.target.value})} placeholder="gpt-4.1-mini / 自建模型名"/></label>
     <label>显示名称<input value={mf.display_name} onChange={e=>setMf({...mf,display_name:e.target.value})} placeholder="可留空"/></label>
    </div>
    <div className="form-row">
     <label>等级<select value={mf.tier} onChange={e=>setMf({...mf,tier:e.target.value})}><option value="economy">经济</option><option value="standard">标准</option><option value="high">高质量</option></select></label>
     <label>上下文窗口<input type="number" min="512" value={mf.context_window} onChange={e=>setMf({...mf,context_window:e.target.value})}/></label>
     <label>最大输出<input type="number" min="1" value={mf.max_output_tokens} onChange={e=>setMf({...mf,max_output_tokens:e.target.value})}/></label>
    </div>
    <div className="form-row">
     <label>输入单价 / 百万 token（USD）<input type="number" min="0" step="0.000001" value={mf.input_price_per_mtok} onChange={e=>setMf({...mf,input_price_per_mtok:e.target.value})}/></label>
     <label>输出单价 / 百万 token（USD）<input type="number" min="0" step="0.000001" value={mf.output_price_per_mtok} onChange={e=>setMf({...mf,output_price_per_mtok:e.target.value})}/></label>
     <label className="check"><input type="checkbox" checked={mf.supports_structured_output} onChange={e=>setMf({...mf,supports_structured_output:e.target.checked})}/>支持 JSON 结构化输出</label>
    </div>
    <label className="check"><input type="checkbox" checked={mf.enabled} onChange={e=>setMf({...mf,enabled:e.target.checked})}/>保存后启用（不会自动加入任何任务路由）</label>
    <div className="tile-actions">{editingModel?<><button className="primary" disabled={busy}>{busy?'正在保存…':'保存修改'}</button><button className="outline" type="button" disabled={busy} onClick={cancelEditModel}>取消编辑</button></>:<button className="primary" disabled={busy||!providers.length}>保存模型</button>}</div>
   </form></Modal>}

   {editingModel&&<p className="small-note">正在编辑「{editingModel.display_name||editingModel.model_id}」：真实模型 ID 与供应商不可改（已有调用账本时模型也只能停用、不能删除）。</p>}
   <p className="small-note">价格只用于本地费用估算；系统不会从供应商拉取价格，也不会自动切换计价版本。</p>
   {models.length===0?<Empty title="尚未添加模型" text="添加真实模型 ID 后才能配置任务路由；Mock 提供器的模型也不会产生真实分析。"/>:models.map(m=><div className="discovery-card" key={m.id}>
    <div className="section-caption"><h4>{m.display_name}</h4><span className={m.enabled?'tag green':'tag'}>{m.enabled?'已启用':'已停用'}</span></div>
    <div className="status-row"><span>供应商 / 真实 ID</span><strong>{m.provider_name} · {m.model_id}</strong></div>
    <div className="status-row"><span>价格（每百万 token）</span><strong>输入 {m.input_price_per_mtok} · 输出 {m.output_price_per_mtok} {m.price_currency}</strong></div>
    <div className="status-row"><span>限制 / 能力</span><strong>{m.context_window} 上下文 · {m.max_output_tokens} 输出 · {m.supports_structured_output?'JSON 输出':'无结构化输出'}</strong></div>
    <div className="status-row"><span>被任务引用</span><strong>{(m.used_by_tasks||[]).length?m.used_by_tasks.join('、'):'未被引用'}</strong></div>
    <div className="tile-actions"><button className="outline" disabled={busy} onClick={()=>startEditModel(m)}>编辑</button><button className="outline" disabled={busy} onClick={()=>toggleModel(m)}>{m.enabled?'停用':'启用'}</button><button className="outline" disabled={busy} onClick={()=>deleteModel(m)}>删除</button></div>
   </div>)}
  </>}
  {tab==='routes'&&<>
   <p className="small-note">第一版只需配置两项任务：视频总结、每轮汇总与实体更新。两项均选择“模型调用”模式并勾选「启用」；可以使用同一个模型。长视频分段处理，建议输入上限至少 16000 token、输出上限至少 2000 token。费用仍受日/月预算控制。<strong>供应商、模型默认都是停用状态</strong>，需先在各自页签启用；修改后必须点底部「保存全部任务路由」才会生效。</p>
   {routesDirty&&<p className="small-note">有未保存的修改：先点底部「保存全部任务路由」，状态与「测试调用」才会按新配置更新。</p>}
   {!enabledModels.length&&<p className="small-note">没有已启用的模型：供应商和模型默认停用，请先到「供应商连接」「模型列表」逐项启用。</p>}
   {routes.filter(r=>['update_generation','digest_polish'].includes(r.task)).map(r=>{const check=routeCheck(r,models);return <div className="model-route" key={r.task}>
    <div className="route-head">
     <strong>{r.task==='update_generation'?'视频总结':r.task==='digest_polish'?'每轮汇总与实体更新':r.label}</strong>
     <span className={check.ready?'tag green':'tag amber'}>{check.ready?(routesDirty?'就绪（待保存）':'就绪'):(r.configured?'未就绪':'未配置')}</span>
     <label className="check"><input type="checkbox" checked={!!r.enabled} onChange={e=>patchRoute(r.task,{enabled:e.target.checked})}/>启用</label>
     <select value={r.mode} onChange={e=>patchRoute(r.task,{mode:e.target.value})}><option value="mock">Mock / 不调用模型</option><option value="rule">规则（不调用模型）</option><option value="model">模型调用</option></select>
     <button className="outline" type="button" disabled={busy||routesDirty||!check.ready||r.mode!=='model'}
       title={routesDirty?'有未保存修改，请先保存':r.mode!=='model'?'只有「模型调用」模式可以测试调用':'发送一条合成测试提示，可能产生真实费用'}
       onClick={()=>testRoute(r)}>测试调用</button>
    </div>
    {r.mode==='model'&&<div className="route-grid">
     <label>主模型<select value={r.primary_model_id||''} onChange={e=>patchRoute(r.task,{primary_model_id:e.target.value||null,fallback_model_ids:((r.fallback_model_ids||[]) as string[]).filter((id:string)=>String(id)!==e.target.value)})}><option value="">未选择</option>{enabledModels.map(m=><option key={m.id} value={m.id}>{m.display_name}（{m.provider_name}）</option>)}</select></label>
     <label>备用模型（可多选，最多 3 个）<select multiple value={r.fallback_model_ids||[]} onChange={e=>patchRoute(r.task,{fallback_model_ids:Array.from(e.target.selectedOptions).map(o=>o.value).slice(0,3)})}>{enabledModels.filter(m=>String(m.id)!==String(r.primary_model_id)).map(m=><option key={m.id} value={m.id}>{m.display_name}</option>)}</select></label>
     <label>输入上限（token）<input type="number" min="1" value={r.max_input_tokens} onChange={e=>patchRoute(r.task,{max_input_tokens:e.target.value})}/></label>
     <label>输出上限（token）<input type="number" min="1" value={r.max_output_tokens} onChange={e=>patchRoute(r.task,{max_output_tokens:e.target.value})}/></label>
     <label>超时（秒）<input type="number" min="1" max="300" value={r.timeout_seconds} onChange={e=>patchRoute(r.task,{timeout_seconds:e.target.value})}/></label>
     <label>尝试次数<input type="number" min="1" max="3" value={r.max_attempts} onChange={e=>patchRoute(r.task,{max_attempts:e.target.value})}/></label>
     <label>数据策略<select value={r.data_policy} onChange={e=>patchRoute(r.task,{data_policy:e.target.value})}><option value="local_only">仅本地</option><option value="approved_cloud">允许云端</option></select></label>
    </div>}
    {check.reasons.length>0&&<small className="muted">未就绪原因：{check.reasons.join('；')}。</small>}
    {check.note&&<small className="muted">{check.note}</small>}
    {r.primary_model_label&&<small className="muted">当前：{r.primary_model_label}{(r.fallback_model_labels||[]).length?' · 备用 '+r.fallback_model_labels.join('、'):''}</small>}
   </div>})}
   <label className="check"><input type="checkbox" checked={cloudConfirm} onChange={e=>setCloudConfirm(e.target.checked)}/>我确认：所选路由若包含云端模型，材料（含合成测试文本）会发送给该供应商；费用按 token 用量估算</label>
   <div className="tile-actions"><button className="primary" disabled={busy} onClick={saveRoutes}>{busy?'正在保存…':(routesDirty?'保存全部任务路由（有未保存修改）':'保存全部任务路由')}</button><button className="outline" disabled={busy} onClick={()=>load()}>放弃修改并刷新</button></div>
  </>}
  {tab==='calls'&&<>
   <div className="stats model-stats">
    <div><small>本窗口调用</small><strong>{cost.totals?.calls||0}</strong></div>
    <div><small>估算费用（{cost.currency||'USD'}）</small><strong>{Number(cost.totals?.settled||0).toFixed(4)}</strong></div>
    <div><small>未知费用（按最坏情况）</small><strong>{Number(cost.totals?.unknown||0).toFixed(4)}</strong></div>
    <div><small>今日剩余预算</small><strong>{Number(cost.budget?.day_remaining||0).toFixed(2)}</strong></div>
   </div>
   <p className="small-note">{cost.note||''} 预算按 UTC 日/月统计；开放中的预留 {cost.budget?.open_reservations||0} 笔。</p>
   {(cost.groups||[]).length>0&&<div className="cost-groups">
    {(cost.groups||[]).map((g:Row,i:number)=><div className="status-row" key={i}><span>{g.day} · {labels[g.task]||g.task||'连接测试'} · {g.provider_name||'—'} / {g.model_label||'—'}</span><strong>{g.calls} 次 · {Number(g.input_tokens)+Number(g.output_tokens)} token · {g.cost_status==='unknown'?'未知':'估算'} {Number(g.settled_total).toFixed(4)}</strong></div>)}
   </div>}
   <div className="section-caption"><h4>最近调用</h4><span className="tag">{calls.length} 条</span></div>
   {calls.length===0?<p className="muted">还没有模型调用。Mock 调用也会记录在此。</p>:calls.map(c=><div className="call-row" key={c.id}>
    <div className="status-row">
     <span>{c.task_label} · {c.provider_name||'—'} / {c.model_label||'—'} · {date(c.created_at)}</span>
     <strong>{labels[c.status]||c.status} · {c.cost_status==='unknown'?'费用未知':'费用 '+Number(c.cost_settled||0).toFixed(4)+' '+c.currency}{c.error_code?' · '+c.error_code:''}</strong>
    </div>
    {c.response_content&&<details className="raw-output"><summary>查看模型原始输出</summary><pre>{c.response_content}</pre></details>}
   </div>)}
  </>}
 </div>
}
function Auth({initialized,onDone}:{initialized:boolean,onDone:()=>void}){
 const [error,setError]=useState(''),[busy,setBusy]=useState(false)
 async function submit(e:React.FormEvent<HTMLFormElement>){e.preventDefault();setBusy(true);setError('');const values=Object.fromEntries(new FormData(e.currentTarget));try{await api(initialized?'/auth/login':'/auth/setup',{method:'POST',body:JSON.stringify(values)});onDone()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 return <div className="auth"><div className="auth-card"><span className="brand"><span className="brand-icon"><Network/></span>FishingNet</span><div className="eyebrow">CONNECT INFORMATION WITH EVIDENCE</div><h1>{initialized?'欢迎回到知识库':'建立你的信息知识库'}</h1><p>{initialized?'登录后继续关注变化、核查主张与阅读证据。':'先创建本应用管理员。不会自动登录平台或启用付费模型。'}</p><form onSubmit={submit}>{!initialized&&<><label>部署端一次性初始化口令<input required name="setup_token" type="password" autoComplete="off"/></label><small className="muted">口令保存在项目 .env 的 SETUP_TOKEN 中。</small><label>工作区名称<input name="workspace_name" required defaultValue="我的知识库"/></label><label>时区<input name="timezone" required defaultValue="Asia/Shanghai"/></label></>}<label>用户名<input name="username" required minLength={3} autoComplete="username"/></label><label>密码<input name="password" type="password" required minLength={initialized?1:12} autoComplete={initialized?'current-password':'new-password'}/></label>{error&&<div role="alert" className="error">{error}</div>}<button className="primary" disabled={busy}>{busy?'正在验证…':initialized?'登录知识库':'创建并进入'}<ArrowUpRight size={17}/></button></form><div className="auth-foot"><ShieldCheck size={16}/>数据默认仅在本地处理 · 云调用预算为零</div></div></div>
}
function ImportModal({close,done}:{close:()=>void,done:()=>void}){
 const attempt=useRef<{key:string,body:string}>({key:crypto.randomUUID(),body:''})
 const urlAttempt=useRef<{key:string,body:string}>({key:crypto.randomUUID(),body:''})
 const [mode,setMode]=useState<'document'|'bilibili'>('document')
 const [content,setContent]=useState(''),[format,setFormat]=useState('text'),[title,setTitle]=useState(''),[rights,setRights]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false),[url,setUrl]=useState('')
 async function submitDocument(e:React.FormEvent<HTMLFormElement>){
  e.preventDefault();if(busy)return
  setBusy(true);setError('')
  const payload={title,content,format,rights_confirmed:rights}
  const body=JSON.stringify(payload)
  // Reuse the key only while the payload is unchanged, so a corrected retry is a new request.
  if(attempt.current.body!==body)attempt.current={key:crypto.randomUUID(),body}
  try{await api('/sources/import',{method:'POST',headers:{'Idempotency-Key':attempt.current.key},body});done()}
  catch(e:any){setError(e.message)}
  finally{setBusy(false)}
 }
 async function submitUrl(e:React.FormEvent<HTMLFormElement>){
  e.preventDefault();if(busy)return
  setBusy(true);setError('')
  const payload={url:url.trim(),rights_confirmed:rights}
  const body=JSON.stringify(payload)
  if(urlAttempt.current.body!==body)urlAttempt.current={key:crypto.randomUUID(),body}
  try{await api('/sources/import-url',{method:'POST',headers:{'Idempotency-Key':urlAttempt.current.key},body});done()}
  catch(e:any){setError(e.message)}
  finally{setBusy(false)}
 }
 return <div className="overlay modal-overlay"><section className="modal" role="dialog" aria-modal="true" aria-label="导入来源"><div className="drawer-head"><strong>导入一份来源</strong><button onClick={close} aria-label="关闭导入"><X/></button></div>
  <div className="tabs modal-tabs" role="tablist"><button type="button" role="tab" aria-selected={mode==='document'} className={mode==='document'?'active':''} onClick={()=>{setMode('document');setError('')}}>文件 / 文本</button><button type="button" role="tab" aria-selected={mode==='bilibili'} className={mode==='bilibili'?'active':''} onClick={()=>{setMode('bilibili');setError('')}}>B站链接</button></div>
  {mode==='document'?<form onSubmit={submitDocument}><p className="muted">支持文本、Markdown、SRT、VTT。保存后使用已配置的视频总结模型处理。</p><label className="upload-zone"><Upload/><strong>选择文件，或在下方粘贴文本</strong><small>单文件不超过 5 MiB · UTF-8</small><input type="file" accept=".txt,.md,.srt,.vtt" onChange={async e=>{const f=e.target.files?.[0];if(!f)return;if(f.size>5*1024*1024){setError('文件超过 5 MiB');return}const ext=f.name.split('.').pop()?.toLowerCase();if(!['txt','md','srt','vtt'].includes(ext||'')){setError('不支持该文件类型');return}setTitle(f.name);setFormat(ext==='txt'?'text':ext||'text');setContent(await f.text())}}/></label><label>来源标题<input required value={title} onChange={e=>setTitle(e.target.value)} maxLength={200}/></label><label>材料正文<textarea required value={content} onChange={e=>setContent(e.target.value)} rows={7} placeholder="保留原始上下文、版本与使用条件…"/></label><label className="checkbox"><input type="checkbox" checked={rights} onChange={e=>setRights(e.target.checked)}/>我有权提供及处理这份材料，并同意在本地保留 30 天。</label><p className="small-note">每份资料生成一条总结，使用模型设置中的“视频总结”任务。</p>{error&&<div role="alert" className="error">{error}</div>}<button className="primary" disabled={busy||!rights}>{busy?'正在保存…':'保存并创建任务'}<ArrowUpRight size={16}/></button></form>
  :<form onSubmit={submitUrl}><p className="muted">粘贴单条 B站 视频链接（包含 BV 号）。保存后由独立 worker 低频采集公开元数据与你有权访问的字幕，不做 AI 分析。</p><label>视频链接<input required value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://www.bilibili.com/video/BV…" maxLength={500} inputMode="url"/></label><p className="small-note">暂不支持 b23.tv 短链、番剧、合集与多P批量；字幕需要在“平台账号”页提供本机 Cookie。</p><label className="checkbox"><input type="checkbox" checked={rights} onChange={e=>setRights(e.target.checked)}/>我有权提供并处理该链接对应的内容；元数据与字幕仅在本机保留 30 天。</label><p className="small-note">不会绕过登录、付费、验证码或风控；元数据、缺字幕、需要登录与失败会分别显示。</p>{error&&<div role="alert" className="error">{error}</div>}<button className="primary" disabled={busy||!rights}>{busy?'正在创建任务…':'创建采集任务'}<ArrowUpRight size={16}/></button></form>}
 </section></div>
}
function ProfileForm({user,timezone,done}:{user:Row,timezone:string,done:()=>void}){
 const [message,setMessage]=useState(''),[busy,setBusy]=useState(false)
 const save=async(e:React.FormEvent<HTMLFormElement>,password=false)=>{
  e.preventDefault();if(busy)return
  setBusy(true);setMessage('');const form=e.currentTarget
  try{await api(password?'/auth/password':'/auth/profile',{method:password?'POST':'PATCH',headers:{'If-Match':String(user.revision)},body:JSON.stringify(Object.fromEntries(new FormData(form)))});if(password)form.reset();setMessage(password?'密码已修改，其他会话已注销':'个人设置已保存');done()}
  catch(err:any){
   if(err.status===409||err.status===428){setMessage('资料已在其他窗口更新，已刷新，请重试');done()}
   else setMessage(err.message)
  }finally{setBusy(false)}
 }
 return <div className="profile-forms"><h3>个人设置</h3><form onSubmit={e=>save(e)}><label>显示名称<input name="display_name" defaultValue={user.display_name} required maxLength={80}/></label><label>时区<input name="timezone" defaultValue={timezone} required/></label><button className="outline" disabled={busy}>保存个人设置</button></form><details><summary>修改密码</summary><form onSubmit={e=>save(e,true)}><label>当前密码<input name="current_password" type="password" autoComplete="current-password" required/></label><label>新密码（至少12位）<input name="new_password" type="password" minLength={12} autoComplete="new-password" required/></label><button className="outline" disabled={busy}>修改并注销其他会话</button></form></details>{message&&<p role="status">{message}</p>}</div>
}
createRoot(document.getElementById('root')!).render(<App/> )
