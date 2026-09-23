(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  // --- goal ranking and same-page fragments (pure; unit-tested via tests/test_agent.py) ---
  // browser.py rewrites the GOAL_TEXT line from the run's goal before evaluating this file.
  const GOAL_TEXT="";
  const jevTokens = s => (s||'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim().split(' ').filter(Boolean);
  const jevGoalWords = text => new Set(jevTokens(text));
  const jevGoalFlat = text => ' '+jevTokens(text).join(' ')+' ';
  // [score, matched] for a candidate's accessible name. The score is normalised token overlap
  // with the goal plus 1 when the whole name appears in the goal verbatim; `matched` is how
  // many of its words the goal contains, which separates "Bicycle wheel" from "Bicycle" when
  // both are wholly inside the goal and so both score the same. Deterministic, no model call:
  // a named hop ("1972", "Bicycle wheel") is nearly always a substring of the goal, and that
  // alone lifts it over the neighbours that crowded it out of a distance-ordered budget.
  const jevGoalRank = (label, goalWords, goalFlat) => {
    const words = jevTokens(label);
    if (!words.length || !goalWords.size) return [0,0];
    let matched = 0;
    for (const w of words) if (goalWords.has(w)) matched++;
    return [matched/words.length + (goalFlat.includes(' '+words.join(' ')+' ') ? 1 : 0), matched];
  };
  // True when the href differs from the current page only in its fragment, so clicking it
  // moves within this page rather than to another article.
  const jevSamePageFragment = (href, here) => {
    if (href === null || href === undefined || href === '') return false;
    let target, current;
    try { target = new URL(href, here); current = new URL(here); } catch { return false; }
    return Boolean(target.hash) && target.origin === current.origin &&
      target.pathname === current.pathname && target.search === current.search;
  };
  // --- end goal ranking and same-page fragments ---
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  const LIMIT=250;
  // Off-viewport links get a budget of their own inside the 250: every row
  // lengthens the table the chooser reads, and filling all 250 with them cost
  // about a third of the latency of every decision. browser.py rewrites this
  // line from JEV_OFFSCREEN_MAX; 0 is upstream's viewport-only behaviour and
  // -1 fills the budget, which is what the first version of this did.
  const OFFSCREEN_LIMIT=100;
  const actions=[], offscreen=[];
  const goalWords=jevGoalWords(GOAL_TEXT), goalFlat=jevGoalFlat(GOAL_TEXT);
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0) continue;
    // A link that only changes the fragment goes nowhere new; say so, so a table-of-contents
    // entry is not mistaken for the article link with the same words in it.
    const fragment=e.tagName==='A' && jevSamePageFragment(e.getAttribute('href'), location.href)
      ? ' (section of this page)' : '';
    if (x<0 || y<0 || x>=innerWidth || y>=innerHeight) {
      // A long page keeps almost every link out of the viewport. Offer the nearest links and
      // buttons anyway, clearly labelled; Browser.act scrolls one into view before clicking it,
      // and geometry is still re-resolved and hit-tested there. Fields are deliberately excluded:
      // typing into something nobody has seen is not the same kind of safe.
      if (rname!=='link' && rname!=='button') continue;
      // A link inside a collapsed box (a Wikipedia navbox, a folded accordion) passes the CSS
      // visibility check but is clipped to nothing by an ancestor, and no scroll can bring it
      // into view: the click is refused as covered, every time. Skip anything an overflow
      // ancestor clips away entirely.
      let clipped=false;
      for (let a=e.parentElement; a && a!==document.body && !clipped; a=a.parentElement) {
        const st=getComputedStyle(a);
        if (st.overflowX==='visible' && st.overflowY==='visible') continue;
        const c=a.getBoundingClientRect();
        clipped=r.right<=c.left || r.left>=c.right || r.bottom<=c.top || r.top>=c.bottom;
      }
      if (clipped) continue;
      const where=y<0 ? 'above' : y>=innerHeight ? 'below' : 'offscreen';
      const plain=(name(e)||rname)+fragment;
      offscreen.push({node:identity(e),role:rname,label:plain+' ('+where+')',
        rect:{x:r.x,y:r.y,w:r.width,h:r.height},kind:'click',value:'',offscreen:where,
        rank:jevGoalRank(name(e)||rname,goalWords,goalFlat),
        distance:y<0 ? -y : y>=innerHeight ? y-innerHeight+1 : 0});
      continue;
    }
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:(name(e)||rname)+fragment,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  // In-viewport actions first, then the off-viewport ones within the one budget: most like the
  // goal first, nearest second. Distance alone put the link a named hop asks for outside a
  // hundred-row budget on an article-length page, and the run gave up rather than scroll.
  offscreen.sort((a,b)=>b.rank[0]-a.rank[0] || b.rank[1]-a.rank[1] ||
    a.distance-b.distance || a.node-b.node);
  let room=Math.max(0,LIMIT-actions.length);
  if (OFFSCREEN_LIMIT>=0) room=Math.min(room,OFFSCREEN_LIMIT);
  const omitted_actions=Math.max(0,actions.length-LIMIT)+Math.max(0,offscreen.length-room);
  actions.splice(LIMIT);
  for (const a of offscreen.slice(0,room)) { delete a.distance; delete a.rank; actions.push(a); }
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6]];
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
