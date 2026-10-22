import React, {useEffect, useRef} from 'react'
import {X} from 'lucide-react'

export function Modal({title,onClose,busy=false,className='',protectChanges=false,children}:{title:string,onClose:()=>void,busy?:boolean,className?:string,protectChanges?:boolean,children:React.ReactNode}){
 const ref=useRef<HTMLDialogElement>(null)
 // A full page reload (including a dev-server update) destroys React form state.
 // Keep drafts in memory and let the browser guard navigation, never persist secrets.
 useEffect(()=>{
  if(!protectChanges)return
  const preventReload=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=''}
  window.addEventListener('beforeunload',preventReload)
  return()=>window.removeEventListener('beforeunload',preventReload)
 },[protectChanges])
 useEffect(()=>{
  const dialog=ref.current!
  const previous=document.activeElement as HTMLElement|null
  const overflow=document.body.style.overflow
  dialog.showModal();document.body.style.overflow='hidden'
  return()=>{dialog.close();document.body.style.overflow=overflow;previous?.focus()}
 },[])
 return <dialog ref={ref} className={'modal app-modal '+className} aria-label={title} onKeyDown={e=>{
  // Enter in a text/number field must not implicitly save and close the form,
  // including Enter used to confirm an IME candidate. Buttons remain keyboard accessible.
  if(protectChanges&&e.key==='Enter'&&e.target instanceof HTMLInputElement&&!['submit','button','checkbox','radio'].includes(e.target.type))e.preventDefault()
 }} onCancel={e=>{e.preventDefault();if(!busy)onClose()}} onClick={e=>{if(!protectChanges&&e.target===e.currentTarget){const r=e.currentTarget.getBoundingClientRect();if(!busy&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom))onClose()}}}>
  <div className="drawer-head"><strong>{title}</strong><button type="button" disabled={busy} onClick={onClose} aria-label={'关闭'+title}><X size={18}/></button></div>
  {children}
 </dialog>
}
