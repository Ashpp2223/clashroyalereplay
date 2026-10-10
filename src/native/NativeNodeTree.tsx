import { useEffect, useRef, useState, type PointerEvent } from 'react';
import { formatNativeTick, type NativeReplayProject } from '../domain/nativeReplay.ts';

export default function NativeNodeTree({project,selectedId,disabled,onSelect,onMove}: {
  project:NativeReplayProject; selectedId:string; disabled:boolean;
  onSelect:(id:string)=>void; onMove:(id:string,position:{x:number;y:number})=>void;
}) {
  const viewport=useRef<HTMLDivElement>(null);
  const [view,setView]=useState({x:20,y:25,scale:1});
  const [dragPosition,setDragPosition]=useState<{id:string;x:number;y:number}|null>(null);
  const gesture=useRef<{id:string|null;x:number;y:number;originX:number;originY:number;moved:boolean}|null>(null);
  const nodes=Object.values(project.nodes);
  const position=(id:string)=>dragPosition?.id===id?dragPosition:project.layout[id]??{x:0,y:0};
  function fit():void {
    const bounds=viewport.current?.getBoundingClientRect(); if(!bounds)return;
    const points=nodes.map(node=>project.layout[node.id]??{x:0,y:0});
    const minX=Math.min(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y));
    const width=Math.max(...points.map(p=>p.x))+160-minX,height=Math.max(...points.map(p=>p.y))+64-minY;
    const scale=Math.min(1,(bounds.width-24)/width,(bounds.height-24)/height);
    setView({x:(bounds.width-width*scale)/2-minX*scale,y:20-minY*scale,scale:Math.max(.15,scale)});
  }
  useEffect(()=>{
    const bounds=viewport.current?.getBoundingClientRect(),point=project.layout[selectedId]??{x:0,y:0};
    if(bounds)setView({x:bounds.width/2-point.x-80,y:25-point.y,scale:1});
  },[project.projectId]);
  function start(event:PointerEvent<HTMLDivElement>,id:string|null):void {
    if(event.button!==0 || disabled)return;
    event.preventDefault();event.stopPropagation();event.currentTarget.setPointerCapture(event.pointerId);
    const origin=id?position(id):view;
    gesture.current={id,x:event.clientX,y:event.clientY,originX:origin.x,originY:origin.y,moved:false};
  }
  function move(event:PointerEvent<HTMLDivElement>):void {
    const active=gesture.current;if(!active)return;
    const dx=event.clientX-active.x,dy=event.clientY-active.y;
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
    <div className="tree-tools"><button onClick={fit}>适应视图</button><button aria-label="缩小节点树" onClick={()=>setView(v=>({...v,scale:Math.max(.15,v.scale/1.2)}))}>−</button><span>{Math.round(view.scale*100)}%</span><button aria-label="放大节点树" onClick={()=>setView(v=>({...v,scale:Math.min(2,v.scale*1.2)}))}>＋</button></div>
    <div className="native-tree" ref={viewport} onPointerDown={event=>start(event,null)} onPointerMove={move} onPointerUp={finish} onPointerCancel={()=>{gesture.current=null;setDragPosition(null);}}
      onWheel={event=>{const bounds=event.currentTarget.getBoundingClientRect(),x=event.clientX-bounds.left,y=event.clientY-bounds.top;setView(v=>{const scale=Math.min(2,Math.max(.15,v.scale*Math.exp(-event.deltaY*.0015)));return {scale,x:x-(x-v.x)*scale/v.scale,y:y-(y-v.y)*scale/v.scale};});}}>
      <div className="tree-world" style={{transform:`translate(${view.x}px,${view.y}px) scale(${view.scale})`}}>
        <svg className="tree-links">{nodes.filter(node=>node.parentId).map(node=>{const parent=position(node.parentId!),child=position(node.id),x=parent.x+80,y=parent.y+64,endX=child.x+80,endY=child.y;return <path key={node.id} d={`M ${x} ${y} C ${x} ${(y+endY)/2}, ${endX} ${(y+endY)/2}, ${endX} ${endY}`} />;})}</svg>
        {nodes.map(node=>{const p=position(node.id);return <div key={node.id} className={`tree-node ${selectedId===node.id?'active':''}`} style={{left:p.x,top:p.y}} onPointerDown={event=>start(event,node.id)}>
          <button disabled={disabled} onClick={event=>{if(event.detail===0)onSelect(node.id);}} title={`${node.title}\n${node.note}`}><span className="tree-node-line">{node.title}</span><small>{formatNativeTick(node.finalTick)} · {node.actionsFromParent.length} 动作{node.editableCopy?' · 可编辑':''}</small></button>
        </div>;})}
      </div>
    </div>
    <div className="tree-hint">点击切换 · 拖动节点或空白 · 滚轮缩放</div>
  </>;
}
