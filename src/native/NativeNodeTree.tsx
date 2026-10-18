import { useI18n } from './i18n.ts';
import { useEffect, useRef, useState, type PointerEvent } from 'react';
import { formatNativeTick, type NativeReplayProject } from '../domain/nativeReplay.ts';

export default function NativeNodeTree({project,selectedId,disabled,onSelect,onMove,onSelectEdge,activeEdge,speed=1}: {
  project:NativeReplayProject; selectedId:string|null; disabled:boolean;
  onSelect:(id:string)=>void; onMove:(id:string,position:{x:number;y:number})=>void;
  onSelectEdge:(childId:string)=>void;
  activeEdge?:{childId:string;progress:number;playing:boolean}|null;
  speed?:number;
}) {
  const {t} = useI18n();
  const viewport=useRef<HTMLDivElement>(null);
  const links=useRef<SVGSVGElement>(null);
  const [view,setView]=useState({x:20,y:25,scale:1});
  const [dragPosition,setDragPosition]=useState<{id:string;x:number;y:number}|null>(null);
  const gesture=useRef<{id:string|null;x:number;y:number;originX:number;originY:number;moved:boolean}|null>(null);
  const nodes=Object.values(project.nodes);
  const position=(id:string)=>dragPosition?.id===id?dragPosition:project.layout[id]??{x:0,y:0};
  function point(event:{clientX:number;clientY:number}):{x:number;y:number} {
    const element=viewport.current!,bounds=element.getBoundingClientRect();
    return {x:(event.clientX-bounds.left)*element.offsetWidth/bounds.width,
      y:(event.clientY-bounds.top)*element.offsetHeight/bounds.height};
  }
  function fit():void {
    const element=viewport.current;if(!element)return;
    const bounds={width:element.clientWidth,height:element.clientHeight};
    const points=nodes.map(node=>project.layout[node.id]??{x:0,y:0});
    const minX=Math.min(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y));
    const width=Math.max(...points.map(p=>p.x))+160-minX,height=Math.max(...points.map(p=>p.y))+64-minY;
    const scale=Math.min(1,(bounds.width-24)/width,(bounds.height-24)/height);
    setView({x:(bounds.width-width*scale)/2-minX*scale,y:20-minY*scale,scale:Math.max(.15,scale)});
  }
  useEffect(()=>{
    const element=viewport.current,selected=project.layout[selectedId??project.rootNodeId]??{x:0,y:0};
    if(element)setView({x:element.clientWidth/2-selected.x-80,y:25-selected.y,scale:1});
  },[project.projectId]);
  useEffect(()=>{links.current?.setCurrentTime(0);},[activeEdge?.childId]);
  useEffect(()=>{
    if(activeEdge?.playing)links.current?.unpauseAnimations();
    else links.current?.pauseAnimations();
  },[activeEdge?.childId,activeEdge?.playing]);
  function start(event:PointerEvent<HTMLDivElement>,id:string|null):void {
    if(event.button!==0 || disabled)return;
    event.preventDefault();event.stopPropagation();event.currentTarget.setPointerCapture(event.pointerId);
    const origin=id?position(id):view;
    const local=point(event);
    gesture.current={id,...local,originX:origin.x,originY:origin.y,moved:false};
  }
  function move(event:PointerEvent<HTMLDivElement>):void {
    const active=gesture.current;if(!active)return;
    const local=point(event),dx=local.x-active.x,dy=local.y-active.y;
    if(Math.abs(dx)+Math.abs(dy)>4)active.moved=true;
    if(!active.moved)return;
    if(active.id)setDragPosition({id:active.id,x:active.originX+dx/view.scale,y:active.originY+dy/view.scale});
    else setView(v=>({...v,x:active.originX+dx,y:active.originY+dy}));
  }
  function finish():void {
    const active=gesture.current;gesture.current=null;
    if(active?.id) {
      if(active.moved && dragPosition)onMove(active.id,{x:dragPosition.x,y:dragPosition.y});
      else if(!active.moved)onSelect(active.id);
    }
    setDragPosition(null);
  }
  return <>
    <div className="tree-tools"><button onClick={fit}>{t("fit")}</button><button aria-label={t("zoomOut")} onClick={()=>setView(v=>({...v,scale:Math.max(.15,v.scale/1.2)}))}>−</button><span>{Math.round(view.scale*100)}%</span><button aria-label={t("zoomIn")} onClick={()=>setView(v=>({...v,scale:Math.min(2,v.scale*1.2)}))}>＋</button></div>
    <div className="native-tree" ref={viewport} onPointerDown={event=>start(event,null)} onPointerMove={move} onPointerUp={finish} onPointerCancel={()=>{gesture.current=null;setDragPosition(null);}}
      onWheel={event=>{const {x,y}=point(event);setView(v=>{const scale=Math.min(2,Math.max(.15,v.scale*Math.exp(-event.deltaY*.0015)));return {scale,x:x-(x-v.x)*scale/v.scale,y:y-(y-v.y)*scale/v.scale};});}}>
      <div className="tree-world" style={{transform:`translate(${view.x}px,${view.y}px) scale(${view.scale})`}}>
        <svg className="tree-links" ref={links}>{nodes.filter(node=>node.parentId).map(node=>{
          const parent=position(node.parentId!),child=position(node.id),x=parent.x+80,y=parent.y+64,endX=child.x+80,endY=child.y,mid=(y+endY)/2;
          const path=`M ${x} ${y} C ${x} ${mid}, ${endX} ${mid}, ${endX} ${endY}`;
          const active=activeEdge?.childId===node.id,progress=active?activeEdge.progress:0;
          const label=`${t('playEdge')} ${project.nodes[node.parentId!].title} → ${node.title} · ${formatNativeTick(node.finalTick-project.nodes[node.parentId!].finalTick)}`;
          return <g key={node.id} className={`tree-edge ${active?'active':''}`} data-edge-id={node.id} role="button" aria-label={label} aria-disabled={disabled}
            tabIndex={disabled?-1:0} onPointerDown={event=>event.stopPropagation()} onClick={()=>{if(!disabled)onSelectEdge(node.id);}}
            onKeyDown={event=>{if(!disabled&&(event.key==='Enter'||event.key===' ')){event.preventDefault();onSelectEdge(node.id);}}}>
            <title>{label}</title><path className="tree-edge-hit" d={path}/><path className="tree-edge-line" d={path}/>
            {active&&<><path className="tree-edge-progress" d={path} pathLength={1} strokeDasharray={`${progress} 1`}/>
              <text className="tree-edge-progress-label" x={(x+endX)/2+10} y={mid}>{Math.round(progress*100)}%</text>
              <circle className={`tree-flow-ball${activeEdge.playing?'':' paused'}`} r={5}>
                <animateMotion dur={`${1.4/speed}s`} repeatCount="indefinite" path={path}/>
              </circle></>}
          </g>;
        })}</svg>
        {nodes.map(node=>{const p=position(node.id);return <div key={node.id} data-node-id={node.id} className={`tree-node ${selectedId===node.id?'active':''}`} style={{left:p.x,top:p.y}} onPointerDown={event=>start(event,node.id)}>
          <button disabled={disabled} onClick={event=>{if(event.detail===0)onSelect(node.id);}} title={`${node.title}\n${node.note}`}><span className="tree-node-line">{node.title}</span><small>{formatNativeTick(node.finalTick)} · {node.actionsFromParent.length} {t("actions")}{node.editableCopy?` · ${t("editable")}`:''}</small></button>
        </div>;})}
      </div>
    </div>
    <div className="tree-hint">{t("treeHint")}</div>
  </>;
}
