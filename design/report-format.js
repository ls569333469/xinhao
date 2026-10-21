/* Safe report text formatter. Raw HTML is always escaped; no external renderer. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.ReportFormat=api;})(typeof window!=='undefined'?window:globalThis,function(){
 'use strict';
 const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function inline(s){return String(s).split(/(`[^`]+`)/g).map(part=>part.startsWith('`')&&part.endsWith('`')?'<code>'+escape(part.slice(1,-1))+'</code>':escape(part).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')).join('');}
 const cells=s=>s.trim().replace(/^\|/,'').replace(/\|$/,'').split(/(?<!\\)\|/).map(c=>c.trim().replace(/\\\|/g,'|'));
 const separator=s=>s.includes('|')&&cells(s).every(c=>/^:?-{3,}:?$/.test(c));
 function markdown(text){
  const lines=String(text??'').replace(/\r\n?/g,'\n').split('\n'),html=[];
  let list=null;
  const close=()=>{if(list){html.push('</'+list+'>');list=null;}};
  for(let i=0;i<lines.length;i++){
   const line=lines[i],fence=line.match(/^\s*(`{3,}|~{3,})(.*)$/);
   if(fence){close();const code=[],mark=fence[1][0],length=fence[1].length;while(++i<lines.length){if(new RegExp('^\\s*'+mark+'{'+length+',}\\s*$').test(lines[i]))break;code.push(lines[i]);}html.push('<pre><code>'+escape(code.join('\n'))+'</code></pre>');continue;}
   if(i+1<lines.length&&line.includes('|')&&separator(lines[i+1])){
    close();const head=cells(line);i++;let rows='';
    while(i+1<lines.length&&lines[i+1].trim()&&lines[i+1].includes('|')){const row=cells(lines[++i]);rows+='<tr>'+head.map((_,n)=>'<td>'+inline(row[n]??'')+'</td>').join('')+'</tr>';}
    html.push('<div class="markdown-table"><table><thead><tr>'+head.map(c=>'<th>'+inline(c)+'</th>').join('')+'</tr></thead><tbody>'+rows+'</tbody></table></div>');continue;
   }
   const item=line.match(/^\s*(?:([-*])\s+|(\d+)[.)]\s+)(.*)$/);
   if(item){const type=item[1]?'ul':'ol';if(list!==type){close();list=type;html.push('<'+type+'>');}html.push('<li>'+inline(item[3])+'</li>');continue;}
   close();const h=line.match(/^(#{1,6})\s+(.*)$/);
   if(h){const n=Math.min(6,h[1].length+1);html.push('<h'+n+'>'+inline(h[2])+'</h'+n+'>');}
   else if(line.trim())html.push('<p>'+inline(line)+'</p>');
  }
  close();return html.join('\n');
 }
 return Object.freeze({markdown,looksMarkdown:t=>typeof t==='string'&&(/^(?:#{1,6}\s|\s*```|\s*~~~|\s*[-*]\s|\s*\d+[.)]\s)/m.test(t)||/\*\*[^*]+\*\*/.test(t)||t.split(/\r?\n/).some(separator))});
});
