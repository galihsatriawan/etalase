"""The look shared by every page a buyer can open: the store, the buy page, the order page, the return forms, the return
policy and the UCP hosted payment page. The seller dashboard reuses the same tokens, buttons, fields and icons (BASE_CSS and
sprite()), so the whole product looks like one app.

It is server-rendered HTML with one inline stylesheet, one self-hosted font (Plus Jakarta Sans, about 27 KB, SIL Open Font
License) and one small inline script. The Content-Security-Policy pins that script by hash, so nothing else can run, and
nothing is loaded from another site. Without JavaScript every page still works: links are links, forms post normally and the
order page refreshes itself.

Design decisions (kept here so a change is made once):
  * It should look like a shopping app people already know, not like a template. On a phone the page is a stack of full-width
    white bands separated by thin grey gaps (the way marketplace apps are built); from 32rem up the same bands become rounded
    cards. One confident green for the main action and for "done", near-black ink, amber and red only for a status or an error.
  * A store has an identity: a gradient cover and a round avatar in a colour taken from its name, a three-number stats row built
    only from real data (products, return window, completed orders or where it ships from), search and sort.
  * A product has its own page: a swipeable photo gallery, a big price, option chips that switch in place, a payment-protection
    band, the seller, more from the same store and a sticky "Buy now" bar. Checkout is a separate short page: order, contact,
    delivery, payment method, summary and a sticky "Place order" bar. An error appears at the top and the field is marked.
  * One typeface, Plus Jakarta Sans, in a few weights. It was drawn in Jakarta and reads well at small sizes on cheap phones.
    Prices and countdowns use tabular figures so they do not jitter.
  * Radii are 12px for controls, 20px for cards and a pill for chips and search: three values, not ten.
  * Every control is at least 44px high and has a pressed, focus and busy state. Hover effects exist only where there is a real
    hover (`@media(hover:hover)`), so nothing sticks after a tap. The text size never goes below 12px for captions and 13px for
    anything a person must read, and inputs are 16px so phones do not zoom the form.
  * Moving between pages never blanks the screen: links and forms are fetched and swapped in place with a short cross-fade
    (View Transitions where the browser has them), the sort reorders cards with a move animation, the order page swaps its
    status itself, photos fade in once loaded, and a thin progress line shows while the server works. Entrance animation runs
    once, on the first paint. All motion is off for people who ask for reduced motion.
"""

from __future__ import annotations

import base64
import hashlib
import io
from datetime import timedelta
from html import escape
from urllib.parse import quote

FONT_FILES = {"plus-jakarta-sans.woff2": "font/woff2"}

# The only script the pages run (one hash in the CSP). Everything here is an enhancement; the pages work without it.
#   1. photos fade in once they have loaded; a quantity field gets + / - buttons and a live subtotal;
#   2. links and forms inside the store, the product page, checkout, the order page and the pay page are fetched and swapped in
#      place (no white flash), with a progress line, history and scroll position kept (typed values too), and a pointer-over
#      prefetch for store links; after a swap the first invalid field is focused; a page brought back from the history gets its
#      forms unstuck and its cart count refreshed, and the cart and its checkout form are always fetched fresh;
#   3. a second tap on a submit button does nothing, and the button says what is happening;
#   4. a page marked data-live asks the server for itself every few seconds and swaps in the new state (the no-JavaScript
#      fallback is a meta refresh inside <noscript>); [data-left] counts the seconds left to pay, from the server's number, and
#      dims the QR code at zero;
#   5. the store page filters (search) and sorts its cards; the product page switches option chips in place (price, label,
#      stock tag and the Buy now link) and tracks the gallery position; share, copy and "save the QR as an image" buttons.
SCRIPT = r"""(function(){
var d=document,root=d.documentElement,main=d.getElementById('main'),bar=d.getElementById('bar'),foot=d.getElementById('foot'),
prog=d.getElementById('prog'),toastEl=d.getElementById('toast'),t0=performance.now(),pt,tt,cache={},snaps={},busy=false,seq=0;
root.classList.add('js');
function keep(){main.querySelectorAll('input,textarea').forEach(function(i){
if(i.type==='checkbox'||i.type==='radio'){if(i.checked)i.setAttribute('checked','');else i.removeAttribute('checked')}
else if(i.tagName==='TEXTAREA'){i.textContent=i.value}else{i.setAttribute('value',i.value)}})}
if('scrollRestoration' in history)history.scrollRestoration='manual';
history.replaceState({y:0},'');
function seen(i){i.classList.add('in')}
function sweep(s){(s||d).querySelectorAll('img').forEach(function(i){if(i.complete)seen(i)})}
function badge(){var c=bar.querySelector('.cartlink');if(!c)return;var h=c.getAttribute('href');
fetch(h+'/badge',{credentials:'same-origin',cache:'no-store'}).then(function(r){return r.ok?r.text():''}).then(function(t){
var n=bar.querySelector('.cartlink');if(t&&n&&n.getAttribute('href')===h)n.outerHTML=t}).catch(function(){})}
d.addEventListener('load',function(e){if(e.target.tagName==='IMG')seen(e.target)},true);
d.addEventListener('error',function(e){if(e.target.tagName==='IMG')seen(e.target)},true);
sweep();
function toast(m){toastEl.textContent=m;toastEl.classList.add('on');clearTimeout(tt);tt=setTimeout(function(){toastEl.classList.remove('on')},4500)}
function money(n){return 'Rp'+n.toLocaleString('id-ID')}
function subtotal(f){var q=f.querySelector('[name=qty]');if(!q)return;
var n=Math.min(+q.max||99,Math.max(1,parseInt(q.value,10)||1));
f.querySelectorAll('[data-subtotal]').forEach(function(el){el.textContent=money(n*(+f.dataset.unit||0))})}
d.addEventListener('input',function(e){if(e.target.name==='qty'&&e.target.form)subtotal(e.target.form);
if(e.target.id==='q')filter()});
function filter(){var q=d.getElementById('q'),t=q.value.trim().toLowerCase(),n=0;
d.querySelectorAll('.pc[data-name]').forEach(function(c){var hit=!t||c.dataset.name.indexOf(t)>-1;c.hidden=!hit;if(hit)n++});
var none=d.getElementById('none');if(none)none.hidden=n>0}
d.addEventListener('click',function(e){var b=e.target.closest('[data-sort]');if(!b)return;
b.parentNode.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false')});
var g=d.querySelector('.grid'),k=b.dataset.sort,cards=[].slice.call(g.querySelectorAll('.pc'));
var order=cards.slice().sort(function(a,c){return k==='asc'?a.dataset.price-c.dataset.price:k==='desc'?c.dataset.price-a.dataset.price:a.dataset.i-c.dataset.i});
function apply(){order.forEach(function(c){g.appendChild(c)})}
var calm=matchMedia('(prefers-reduced-motion:reduce)').matches;
if(d.startViewTransition&&!calm&&cards.length<40){cards.forEach(function(c){c.style.viewTransitionName='pc'+c.dataset.i});
d.startViewTransition(apply).finished.then(function(){cards.forEach(function(c){c.style.viewTransitionName=''})},function(){cards.forEach(function(c){c.style.viewTransitionName=''})})}
else apply()});
d.addEventListener('click',function(e){var c=e.target.closest('.chip[data-id]');if(!c)return;
var box=c.closest('[data-variants]');if(!box||e.metaKey||e.ctrlKey||e.shiftKey)return;e.preventDefault();
if(c.classList.contains('on'))return;
box.querySelectorAll('.chip').forEach(function(x){x.classList.toggle('on',x===c);if(x===c)x.setAttribute('aria-current','true');else x.removeAttribute('aria-current')});
var root=c.closest('main');
root.querySelectorAll('[data-price-text]').forEach(function(el){el.textContent=money(+c.dataset.vprice)});
root.querySelectorAll('[data-label]').forEach(function(el){el.textContent=c.dataset.vlabel});
root.querySelectorAll('[data-avail]').forEach(function(el){var w=c.dataset.vavail==='confirm_with_seller';el.className='tag'+(w?' wait':'');el.textContent=w?'Seller confirms stock':'Ready stock'});
root.querySelectorAll('[data-go]').forEach(function(a){a.setAttribute('href',c.dataset.buy)});
root.querySelectorAll('[data-variant-input]').forEach(function(i){i.value=c.dataset.id});
try{history.replaceState(history.state,'',c.getAttribute('href'))}catch(x){}});
d.addEventListener('click',function(e){var b=e.target.closest('[data-share]');if(!b)return;
var u=location.href.split('#')[0];
if(navigator.share){navigator.share({title:d.title,url:u}).catch(function(){})}
else if(navigator.clipboard){navigator.clipboard.writeText(u).then(function(){toast('Link copied')},function(){toast(u)})}
else toast(u)});
d.addEventListener('scroll',function(e){var s=e.target;if(!s.classList||!s.classList.contains('slides'))return;
var i=Math.round(s.scrollLeft/s.clientWidth),g=s.parentNode,n=g.querySelector('.cnt');
if(n)n.textContent=(i+1)+'/'+s.children.length;
g.querySelectorAll('.dotsnav i').forEach(function(x,j){x.classList.toggle('on',j===i)})},true);
d.addEventListener('click',function(e){var b=e.target.closest('[data-copy]');if(!b)return;
var v=b.dataset.copy;
if(navigator.clipboard)navigator.clipboard.writeText(v).then(function(){toast('Copied')},function(){toast(v)});else toast(v)});
d.addEventListener('click',function(e){var b=e.target.closest('[data-saveqr]');if(!b)return;
var svg=d.querySelector('.qr svg');if(!svg)return;
var x=new XMLSerializer().serializeToString(svg).replace('<svg','<svg width="640" height="640"'),img=new Image();
img.onload=function(){var c=d.createElement('canvas');c.width=c.height=720;var g=c.getContext('2d');g.fillStyle='#fff';g.fillRect(0,0,720,720);g.drawImage(img,40,40,640,640);
c.toBlob(function(bl){if(!bl)return;var f=new File([bl],'etalase-qris.png',{type:'image/png'});
if(navigator.canShare&&navigator.canShare({files:[f]})){navigator.share({files:[f]}).catch(function(){})}
else{var a=d.createElement('a');a.href=URL.createObjectURL(bl);a.download='etalase-qris.png';d.body.appendChild(a);a.click();a.remove();toast('QR image saved')}},'image/png')};
img.onerror=function(){toast('Could not save the image. Take a screenshot instead.')};
img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(x)});
d.addEventListener('click',function(e){var b=e.target.closest('[data-step]');if(!b||!b.form)return;
var q=b.form.querySelector('[name=qty]'),n=(parseInt(q.value,10)||1)+(+b.dataset.step);
q.value=Math.min(+q.max||99,Math.max(+q.min||1,n));subtotal(b.form)});
function count(){d.querySelectorAll('[data-left]').forEach(function(el){
var s=Math.max(0,Math.round(el.dataset.left-(performance.now()-t0)/1000));
el.textContent=Math.floor(s/60)+':'+('0'+s%60).slice(-2);
if(s===0){var p=el.closest('.pay');if(p)p.classList.add('dead')}})}
setInterval(count,1000);count();
function progress(on){clearTimeout(pt);
if(on){pt=setTimeout(function(){prog.className='prog on'},120)}
else if(prog.classList.contains('on')){prog.className='prog done';pt=setTimeout(function(){prog.className='prog'},700)}}
function label(f,on){var b=f.querySelector('button[type=submit]');if(!b)return;
if(on){b.dataset.label=b.textContent;b.setAttribute('aria-busy','true');b.textContent=b.dataset.busy||'One moment…'}
else{delete f.dataset.sent;if(b.dataset.label){b.textContent=b.dataset.label}b.removeAttribute('aria-busy')}}
function snap(){keep();var k=Object.keys(snaps);if(k.length>20)delete snaps[k[0]];
snaps[location.href]={main:main.innerHTML,cls:main.className,live:main.getAttribute('data-live'),bar:bar.innerHTML,foot:foot?foot.innerHTML:'',title:d.title}}
function restore(s,y){var my=++seq;function apply(){if(my!==seq)return;bar.innerHTML=s.bar;main.innerHTML=s.main;main.className=s.cls;main.removeAttribute('data-live');
main.querySelectorAll('form').forEach(function(f){label(f,false)});badge();
if(foot)foot.innerHTML=s.foot;d.title=s.title;root.classList.add('swapped');t0=performance.now();count();sweep(main);scrollTo({top:y||0,behavior:'instant'})}
if(d.startViewTransition)d.startViewTransition(apply);else apply()}
function show(html,url,push,y,my){
var next=new DOMParser().parseFromString(html,'text/html'),nm=next.getElementById('main'),nb=next.getElementById('bar');
if(!nm||!nb){location.href=url;return}
function apply(){
if(my!==seq)return;
if(push){snap();history.replaceState({y:scrollY},'');history.pushState({y:0},'',url)}else if(url!==location.href)history.replaceState({y:scrollY},'',url);
bar.innerHTML=nb.innerHTML;main.innerHTML=nm.innerHTML;main.className=nm.className;
if(nm.hasAttribute('data-live'))main.setAttribute('data-live',nm.getAttribute('data-live'));else main.removeAttribute('data-live');
var nf=next.getElementById('foot');if(nf&&foot){foot.innerHTML=nf.innerHTML}
d.title=next.title;root.classList.add('swapped');t0=performance.now();count();sweep(main);scrollTo({top:y||0,behavior:'instant'});
var bad=main.querySelector('[aria-invalid=true]');
if(bad){bad.focus({preventScroll:true});bad.scrollIntoView({block:'center',behavior:'instant'})}else main.focus({preventScroll:true})}
if(d.startViewTransition)d.startViewTransition(apply);else apply()}
function go(url,o){o=o||{};progress(true);var my=++seq;
var hit=!o.body&&cache[url]&&Date.now()-cache[url].t<20000?cache[url].p:null;
var req=hit||fetch(url,{method:o.body?'POST':'GET',body:o.body,credentials:'same-origin',cache:'no-store',
headers:o.body?{'content-type':'application/x-www-form-urlencoded'}:{}});
return Promise.resolve(req).then(function(r){
if(o.body)cache={};
if((r.headers.get('content-type')||'').indexOf('text/html')!==0){
if(o.body)throw new Error('response');location.href=url;return}
return r.clone().text().then(function(h){show(h,r.url||url,!o.pop&&(r.url||url)!==location.href,o.y,my)})
}).then(function(){progress(false)},function(err){progress(false);throw err})}
var NAV=/^\/(stores|pay|checkout)\//,FRESH=/\/cart(\/checkout)?$/;
d.addEventListener('click',function(e){
if(e.defaultPrevented||e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
var a=e.target.closest('a[href]');
if(!a||a.target||a.hasAttribute('download')||a.origin!==location.origin||!NAV.test(a.pathname)||/\.jsonld$/.test(a.pathname))return;
if(a.pathname===location.pathname&&a.search===location.search&&a.hash)return;
e.preventDefault();go(a.href).catch(function(){location.href=a.href})});
function warm(e){var a=e.target.closest&&e.target.closest('a[href^="/stores/"]');
if(!a||cache[a.href]||/\.jsonld$/.test(a.pathname)||a.target)return;
var p=fetch(a.href,{credentials:'same-origin',cache:'no-store'});cache[a.href]={t:Date.now(),p:p};p.catch(function(){delete cache[a.href]})}
d.addEventListener('pointerover',function(e){if(e.pointerType==='mouse')warm(e)});
d.addEventListener('touchstart',warm,{passive:true});
addEventListener('popstate',function(e){var y=(e.state||{}).y||0,s=snaps[location.href];
if(s&&!s.live&&!FRESH.test(location.pathname)){restore(s,y);return}
go(location.href,{pop:true,y:y}).catch(function(){location.reload()})});
d.addEventListener('submit',function(e){var f=e.target;
if(f.dataset.sent){e.preventDefault();return}
f.dataset.sent='1';label(f,true);
if((f.method||'').toLowerCase()!=='post'||e.defaultPrevented||!f.action)return;
e.preventDefault();
go(f.action,{body:new URLSearchParams(new FormData(f)).toString()}).catch(function(){label(f,false);toast('That did not go through. Check your connection and try again.')})});
addEventListener('pageshow',function(e){if(e.persisted)d.querySelectorAll('form').forEach(function(f){label(f,false)})});
function poll(){if(d.hidden||busy||!main.hasAttribute('data-live'))return;busy=true;
fetch(location.href,{cache:'no-store',credentials:'same-origin'}).then(function(r){return r.ok?r.text():''}).then(function(h){
var next=h&&new DOMParser().parseFromString(h,'text/html').getElementById('main');
if(next&&next.getAttribute('data-live')!==main.getAttribute('data-live')){
var swap=function(){main.innerHTML=next.innerHTML;if(next.hasAttribute('data-live'))main.setAttribute('data-live',next.getAttribute('data-live'));else main.removeAttribute('data-live');t0=performance.now();count();sweep(main)};
if(d.startViewTransition)d.startViewTransition(swap);else swap()}
}).catch(function(){}).then(function(){busy=false})}
setInterval(poll,4000);d.addEventListener('visibilitychange',poll);
})();"""
SCRIPT_HASH = "'sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode() + "'"


def csp(images: str = "data:", forms: bool = False) -> str:
    """The Content-Security-Policy for a buyer page: its own font and script, its own fetch, nothing from anywhere else."""
    return ("default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; "
            f"img-src {images}; script-src {SCRIPT_HASH}; connect-src 'self'; "
            + ("form-action 'self'; " if forms else "") + "base-uri 'none'; frame-ancestors 'none'")


# ------------------------------------------------------------------ icons
# One set, one stroke width. Drawn on a 24px grid; used through <use href="#i-name"> from the sprite below.
ICONS = {
    "arrow-left": '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    "pin": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "qr": '<rect x="3" y="3" width="5" height="5" rx="1"/><rect x="16" y="3" width="5" height="5" rx="1"/><rect x="3" y="16" width="5" height="5" rx="1"/><path d="M21 16h-3a2 2 0 0 0-2 2v3M21 21v.01M12 7v3a2 2 0 0 1-2 2H7M3 12h.01M12 3h.01M12 16v.01M16 12h1M21 12v.01M12 21v-1"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "truck": '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2M15 18H9M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "package": '<path d="M21 8 12 3 3 8v8l9 5 9-5Z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "copy": '<rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "chevron": '<path d="m6 9 6 6 6-6"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/>',
    "minus": '<path d="M5 12h14"/>',
    "plus": '<path d="M5 12h14M12 5v14"/>',
    "undo": '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "code": '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
    "bag": '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18M16 10a4 4 0 0 1-8 0"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
    "external": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "wallet": '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
    "out": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "settings": '<path d="M20 7h-9M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="8" cy="7" r="3"/>',
    "share": '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.59 13.51 6.83 3.98M15.41 6.51l-6.82 3.98"/>',
    "store": '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/><path d="M2 7h20"/><path d="M22 7v3a2 2 0 0 1-2 2a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 16 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 12 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 8 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 4 12a2 2 0 0 1-2-2V7"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "message": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
}


def ico(name: str, cls: str = "") -> str:
    return f'<svg class="ico{" " + cls if cls else ""}" aria-hidden="true"><use href="#i-{name}"/></svg>'


def sprite() -> str:
    symbols = "".join(f'<symbol id="i-{k}" viewBox="0 0 24 24">{v}</symbol>' for k, v in ICONS.items())
    return f'<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">{symbols}</svg>'


_LOGO_INNER = ('<rect width="32" height="32" rx="9" fill="#08815a"/>'
               '<path d="M9.5 7.5h13l3.5 6.5a3.5 3.5 0 0 1-7 0 3.5 3.5 0 0 1-7 0 3.5 3.5 0 0 1-7 0z" fill="#fff"/>'
               '<rect x="8" y="17.5" width="16" height="7.5" rx="1.6" fill="#fff" fill-opacity=".92"/>')
LOGO = f'<svg viewBox="0 0 32 32" aria-hidden="true">{_LOGO_INNER}</svg>'
FAVICON_SVG = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{_LOGO_INNER}</svg>'

# ------------------------------------------------------------------ styles
BASE_CSS = """
@font-face{font-family:"Plus Jakarta Sans";font-style:normal;font-weight:200 800;font-display:swap;src:url(/static/plus-jakarta-sans.woff2) format("woff2")}
:root{--bg:#f4f6f5;--surface:#fff;--ink:#0e1a14;--muted:#4f5e57;--soft:#6b7a73;--line:#e6ebe8;--line-2:#d3dbd7;
--brand:#08815a;--brand-strong:#066b4a;--brand-ink:#04503a;--brand-soft:#e3f3ec;--brand-tint:#f0f8f4;--deep:#073b2b;
--amber:#955500;--amber-bg:#fff0d2;--amber-dot:#e19a0b;--red:#b3261e;--red-bg:#fde8e6;
--r:12px;--r-sm:12px;--r-lg:20px;
--shadow-1:0 0 0 1px rgba(14,26,20,.05),0 1px 2px rgba(14,26,20,.04);
--shadow-2:0 0 0 1px rgba(14,26,20,.04),0 2px 4px rgba(14,26,20,.05),0 18px 34px -14px rgba(14,26,20,.28);
--sans:"Plus Jakarta Sans",system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",sans-serif;
--ease:cubic-bezier(.22,.8,.2,1);color-scheme:light}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%;scroll-padding-top:4.5rem;scroll-padding-bottom:6rem}
body{margin:0;min-height:100dvh;display:flex;flex-direction:column;background:var(--bg);color:var(--ink);font:400 1rem/1.55 var(--sans);
-webkit-font-smoothing:antialiased;-webkit-tap-highlight-color:transparent;text-rendering:optimizeLegibility}
a,button,input,select,textarea,summary,label{touch-action:manipulation}
h1,h2,h3,h4,p,ul,ol,dl,dd,figure{margin:0}
h1{font:800 clamp(1.5rem,5.2vw,2rem)/1.15 var(--sans);letter-spacing:-.025em;text-wrap:balance}
h2{font:700 1.125rem/1.3 var(--sans);letter-spacing:-.012em;text-wrap:balance}
h3{font:650 1rem/1.35 var(--sans)}
p{text-wrap:pretty}
a{color:var(--brand);text-underline-offset:.2em;transition:color .15s}
@media(hover:hover){a:hover{color:var(--brand-strong)}}
:focus-visible{outline:2px solid var(--brand);outline-offset:3px;border-radius:8px}
main:focus{outline:none}
.skip{position:absolute;left:.75rem;top:-4rem;z-index:60;background:var(--ink);color:#fff;padding:.7rem 1rem;border-radius:var(--r)}
.skip:focus{top:.75rem}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.ico{width:1.25rem;height:1.25rem;flex:none;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.ico.sm{width:1rem;height:1rem}.ico.lg{width:1.5rem;height:1.5rem}
.note{color:var(--muted)}.fine{font-size:.875rem;color:var(--muted)}
.card{background:var(--surface);border-radius:var(--r-lg);box-shadow:var(--shadow-1);padding:1.25rem}
.cap{font:700 1rem/1.3 var(--sans);letter-spacing:-.01em;color:var(--ink);margin:0 0 .85rem}
.eyebrow{font:700 .75rem/1.3 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.bar{position:sticky;top:0;z-index:30;background:#fff;box-shadow:0 1px 0 var(--line)}
.bar .in{display:flex;align-items:center;gap:.75rem;min-height:3.5rem}
.brand{display:inline-flex;align-items:center;gap:.55rem;font:800 1.1875rem/1 var(--sans);letter-spacing:-.03em;color:var(--ink)}
.brand svg,.mark svg{width:1.75rem;height:1.75rem;display:block}
.mark{margin-left:auto;display:block}
.iconbtn{display:grid;place-items:center;width:2.75rem;height:2.75rem;border:0;border-radius:50%;background:none;color:var(--ink);flex:none;text-decoration:none;cursor:pointer;
transition:background-color .15s,transform .15s var(--ease)}
.iconbtn:active{transform:scale(.92);background:var(--bg)}
@media(hover:hover){.iconbtn:hover{background:var(--bg);color:var(--ink)}}
.bar .iconbtn:first-child{margin-left:-.6rem}
.bar .iconbtn.end{margin-left:auto;margin-right:-.6rem}
.bartitle{font:650 1rem/1.2 var(--sans);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.btn{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:.55rem;min-height:3rem;padding:0 1.35rem;border:0;border-radius:var(--r);
background:var(--brand);color:#fff;font:700 1rem/1.2 var(--sans);text-decoration:none;text-align:center;cursor:pointer;
transition:background-color .2s,transform .16s var(--ease),color .2s,box-shadow .2s}
.btn:active{transform:scale(.975);background:var(--brand-strong)}
@media(hover:hover){.btn:hover{background:var(--brand-strong);color:#fff}}
.btn.alt{background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 1.5px var(--line-2)}
.btn.alt:active{background:var(--bg)}
@media(hover:hover){.btn.alt:hover{background:var(--bg);color:var(--ink);box-shadow:inset 0 0 0 1.5px var(--soft)}}
.btn.light{background:#fff;color:var(--deep)}
@media(hover:hover){.btn.light:hover{background:var(--brand-soft);color:var(--deep)}}
.btn.quiet{background:none;color:var(--muted);min-height:2.75rem;padding:0 .75rem;font-weight:600}
.btn.quiet:active{background:var(--bg)}
@media(hover:hover){.btn.quiet:hover{color:var(--ink);background:var(--bg)}}
.btn.sm{min-height:2.75rem;padding:0 1.1rem;font-size:.9375rem;border-radius:999px}
.btn.block{display:flex;width:100%;min-height:3.25rem;font-size:1.0625rem}
.btn[aria-busy=true],.btn:disabled{pointer-events:none;opacity:.7}
.btn[aria-busy=true]::before,.btn.spin::before{content:"";width:1.1em;height:1.1em;flex:none;border-radius:50%;border:2.5px solid rgba(255,255,255,.35);border-top-color:#fff;animation:spin .7s linear infinite}
.btn.alt[aria-busy=true]::before{border-color:var(--line-2);border-top-color:var(--ink)}
@keyframes spin{to{transform:rotate(360deg)}}
label{display:block;margin:0 0 .4rem;font:650 .875rem/1.3 var(--sans);color:var(--ink)}
.hint{display:block;margin:-.2rem 0 .45rem;font:400 .8125rem/1.4 var(--sans);color:var(--muted)}
input,textarea,select{display:block;width:100%;min-height:3rem;padding:.7rem .9rem;font:500 1rem/1.4 var(--sans);color:var(--ink);background:var(--surface);
border:0;border-radius:var(--r);box-shadow:inset 0 0 0 1.5px var(--line-2);transition:box-shadow .18s,background-color .18s;appearance:none;-webkit-appearance:none}
select{background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23566660' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
background-repeat:no-repeat;background-position:right .8rem center;background-size:1.15rem;padding-right:2.6rem}
input::placeholder,textarea::placeholder{color:var(--soft);font-weight:400}
@media(hover:hover){input:hover,textarea:hover,select:hover{box-shadow:inset 0 0 0 1.5px var(--soft)}}
input:focus,textarea:focus,select:focus{outline:none;box-shadow:inset 0 0 0 2px var(--brand),0 0 0 4px rgba(8,129,90,.16)}
input:user-invalid,textarea:user-invalid,.field.bad input,.field.bad textarea{box-shadow:inset 0 0 0 2px var(--red),0 0 0 4px rgba(179,38,30,.12)}
textarea{min-height:5.75rem;resize:none}
.field{margin:0 0 1rem}.field:last-child{margin-bottom:0}
.alert,.callout{display:flex;gap:.7rem;align-items:flex-start;border-radius:var(--r);padding:.85rem 1rem;font-size:.9375rem;line-height:1.45}
.alert{background:var(--red-bg);color:var(--red)}.alert b{display:block;font-weight:700}
.callout{background:var(--amber-bg);color:var(--amber)}
.alert .ico,.callout .ico{margin-top:.1rem}
.sep{height:1px;background:var(--line);margin:1.35rem 0}
.checks,.num{list-style:none;margin:0;padding:0;display:grid;gap:.75rem}
.checks li{display:flex;gap:.75rem;align-items:flex-start}.checks .ico{color:var(--brand);margin-top:.15rem}
.num{counter-reset:s}.num li{display:flex;gap:.85rem;align-items:flex-start;counter-increment:s}
.num li::before{content:counter(s);flex:none;display:grid;place-items:center;width:1.65rem;height:1.65rem;border-radius:50%;background:var(--brand-soft);color:var(--brand-ink);font:800 .8125rem/1 var(--sans);margin-top:.05rem}
.pill{display:inline-flex;align-items:center;gap:.4rem;padding:.28rem .7rem;border-radius:999px;font:650 .8125rem/1.3 var(--sans);background:var(--bg);color:var(--muted);white-space:nowrap}
.pill i{width:.45rem;height:.45rem;border-radius:50%;background:currentColor;flex:none}
.pill.ok{background:var(--brand-soft);color:var(--brand-ink)}.pill.wait{background:var(--amber-bg);color:var(--amber)}.pill.bad{background:var(--red-bg);color:var(--red)}
.tag{display:inline-flex;align-items:center;gap:.35rem;padding:.25rem .65rem;border-radius:999px;background:var(--brand-soft);color:var(--brand-ink);font:700 .75rem/1.3 var(--sans)}
.tag.wait{background:var(--amber-bg);color:var(--amber)}
.money{font-variant-numeric:tabular-nums;white-space:nowrap}
.prog{position:fixed;inset:0 0 auto 0;height:3px;z-index:60;background:var(--brand);transform:scaleX(0);transform-origin:left;opacity:0;pointer-events:none}
.prog.on{opacity:1;transform:scaleX(.88);transition:transform 9s cubic-bezier(.08,.7,.12,1)}
.prog.done{opacity:0;transform:scaleX(1);transition:transform .2s,opacity .45s .2s}
.toast{position:fixed;left:50%;bottom:calc(1.25rem + env(safe-area-inset-bottom));z-index:70;max-width:calc(100% - 2rem);padding:.85rem 1.1rem;border-radius:var(--r);background:var(--ink);color:#fff;
font:600 .9375rem/1.4 var(--sans);box-shadow:var(--shadow-2);opacity:0;transform:translate(-50%,1rem);pointer-events:none;transition:opacity .25s,transform .35s var(--ease)}
.toast.on{opacity:1;transform:translate(-50%,0)}
.skel{border-radius:var(--r);background:linear-gradient(100deg,#e9eeeb 30%,#f6f9f7 50%,#e9eeeb 70%);background-size:200% 100%}
@media(prefers-reduced-motion:no-preference){
.skel{animation:shimmer 1.3s linear infinite}@keyframes shimmer{to{background-position:-200% 0}}
html:not(.swapped) .rise{animation:rise .5s var(--ease) both;animation-delay:calc(var(--i,0)*45ms)}
@keyframes rise{from{opacity:0;transform:translateY(8px)}}
}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation-duration:.01ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important}}
"""

PAGE_CSS = """
@view-transition{navigation:auto}
#bar{view-transition-name:bar}#main{view-transition-name:main}
.wrap{width:100%;max-width:66rem;margin:0 auto;padding:0 1rem}.narrow{max-width:32rem}
#main{flex:1;padding-top:1rem;padding-bottom:2rem}#main.flush{padding-top:0}
@media(min-width:32rem){#main.flush{padding-top:1rem}}
.pagehead{margin:.25rem 0 1rem}.pagehead .lead{margin-top:.35rem;color:var(--muted)}
.stack{display:grid;gap:.5rem}
.band{background:var(--surface);margin:0 -1rem;padding:1.15rem 1rem}
@media(min-width:32rem){.stack{gap:.75rem}.band{margin:0;padding:1.25rem;border-radius:var(--r-lg);box-shadow:var(--shadow-1)}}
.band>.cap:first-child,.band>h2:first-child{margin-top:0}
/* store */
.cover{height:7.25rem;margin:0 -1rem;border-radius:0 0 var(--r-lg) var(--r-lg);
background:radial-gradient(90% 150% at 100% 0%,rgba(255,255,255,.3),transparent 62%),linear-gradient(135deg,var(--c1,#0a5c42),var(--c2,#1fb384))}
@media(min-width:40rem){.cover{height:9.5rem;margin:0;border-radius:var(--r-lg)}}
.shop{--ov:2.5rem;position:relative;display:flex;gap:1rem;align-items:flex-start;margin:calc(var(--ov)*-1) .25rem 0}
@media(min-width:40rem){.shop{--ov:2.75rem;margin-left:1.5rem;margin-right:1.5rem}}
.avatar{width:4.75rem;height:4.75rem;flex:none;border-radius:50%;display:grid;place-items:center;font:800 1.875rem/1 var(--sans);color:#fff;
background:linear-gradient(135deg,var(--c1,#0a5c42),var(--c2,#1fb384));box-shadow:0 0 0 4px var(--bg)}
.avatar.sm{width:3rem;height:3rem;font-size:1.25rem;box-shadow:none}
.shop .id{min-width:0;padding-top:calc(var(--ov) + .45rem)}
.shop h1{font-size:1.375rem;line-height:1.2}
.where{display:flex;align-items:center;gap:.3rem;margin-top:.2rem;color:var(--muted);font-size:.9375rem}
.policyline{display:flex;align-items:center;gap:.5rem;margin:.9rem .25rem 0;font-size:.875rem;color:var(--muted)}
.policyline .ico{color:var(--brand)}.policyline a{font-weight:650}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin:1rem 0 0;padding:.95rem 0;background:var(--surface);border-radius:var(--r-lg);box-shadow:var(--shadow-1)}
.stats div{text-align:center;padding:0 .5rem;min-width:0}.stats div+div{border-left:1px solid var(--line)}
.stats b{display:block;font:800 1.25rem/1.2 var(--sans);letter-spacing:-.02em;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stats span{font:600 .75rem/1.3 var(--sans);color:var(--muted)}
.sec{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;margin:1.75rem 0 .75rem}
.sec .count{color:var(--muted);font:650 .875rem/1 var(--sans)}
.tools{display:grid;gap:.6rem;margin:0 0 1rem}
html:not(.js) .tools{display:none}
.search{position:relative}
.search .ico{position:absolute;left:1rem;top:50%;transform:translateY(-50%);color:var(--soft);pointer-events:none}
.search input{padding-left:2.9rem;border-radius:999px}
.sorts{display:flex;gap:.5rem;overflow-x:auto;scrollbar-width:none;margin:0 -1rem;padding:.1rem 1rem}.sorts::-webkit-scrollbar{display:none}
@media(min-width:66rem){.sorts{margin:0;padding:.1rem 0}}
.sorts button{flex:none;min-height:2.75rem;padding:0 1.1rem;border:0;border-radius:999px;background:var(--surface);color:var(--ink);font:650 .875rem/1 var(--sans);
box-shadow:inset 0 0 0 1.5px var(--line-2);cursor:pointer;transition:background-color .2s,color .2s,box-shadow .2s,transform .12s var(--ease)}
.sorts button:active{transform:scale(.96)}
.sorts button[aria-pressed=true]{background:var(--ink);color:#fff;box-shadow:none}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem}
@media(min-width:40rem){.grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem}}
@media(min-width:62rem){.grid{grid-template-columns:repeat(auto-fill,minmax(11rem,1fr))}}
.pc{display:flex;flex-direction:column;background:var(--surface);border-radius:var(--r-lg);overflow:hidden;color:var(--ink);text-decoration:none;box-shadow:var(--shadow-1);
transition:transform .28s var(--ease),box-shadow .28s}
.pc:active{transform:scale(.98)}
@media(hover:hover){.pc:hover{color:var(--ink);transform:translateY(-3px);box-shadow:var(--shadow-2)}.pc:hover .media img{transform:scale(1.04)}}
.media{position:relative;aspect-ratio:4/5;overflow:hidden;background:var(--tone,#e9eeec)}
.media img{display:block;width:100%;height:100%;object-fit:cover;transition:transform .6s var(--ease),opacity .4s var(--ease)}
html.js .media img{opacity:0}html.js .media img.in{opacity:1}
.ph{height:100%;display:grid;place-items:center;background:var(--tone,#e9edeb);color:var(--tone-ink,#04503a);font:800 3.25rem/1 var(--sans);opacity:.9}
.flag{position:absolute;left:.6rem;top:.6rem;padding:.3rem .6rem;border-radius:999px;background:rgba(14,26,20,.82);color:#fff;font:700 .75rem/1 var(--sans)}
.pc .body{display:flex;flex-direction:column;gap:.3rem;padding:.75rem .85rem .9rem;flex:1}
.pc .name{font:650 .9375rem/1.3 var(--sans);min-height:2.6em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pc .opts{font-size:.8125rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sw{display:flex;align-items:center;gap:.3rem;font-size:.75rem;color:var(--muted);font-weight:650}
.sw i{width:.85rem;height:.85rem;border-radius:50%;box-shadow:inset 0 0 0 1px rgba(14,26,20,.2)}
.price{font:800 1.0625rem/1.3 var(--sans);letter-spacing:-.015em;font-variant-numeric:tabular-nums;margin-top:auto;padding-top:.15rem}
.price small{font:600 .75rem var(--sans);color:var(--muted);margin-right:.3rem;letter-spacing:0}
.pc .hold{font-size:.75rem;font-weight:650;color:var(--amber)}
.rail{display:flex;gap:.75rem;overflow-x:auto;scroll-snap-type:x proximity;margin:0 -1rem;padding:.25rem 1rem .5rem;scrollbar-width:none}
.rail::-webkit-scrollbar{display:none}
.rail .pc{flex:0 0 9.75rem;scroll-snap-align:start}
@media(min-width:32rem){.rail{margin:0 -1.25rem;padding:.25rem 1.25rem .5rem}}
.devnote{margin:2rem 0 0;font-size:.875rem;color:var(--muted)}
.devnote summary{display:inline-flex;align-items:center;gap:.5rem;cursor:pointer;list-style:none;min-height:2.75rem;font-weight:650}
.devnote summary::-webkit-details-marker{display:none}
.devnote summary .ico:last-child{transition:transform .25s var(--ease)}.devnote[open] summary .ico:last-child{transform:rotate(180deg)}
.devnote dl{display:grid;gap:.25rem;padding:.25rem 0 .5rem}.devnote dt{font:650 .8125rem/1.3 var(--sans);color:var(--soft);margin-top:.5rem}
code{background:var(--bg);padding:.12rem .45rem;border-radius:8px;font:500 .85em ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
/* product page */
.pdp{display:grid;gap:.5rem}
.gallery{position:relative;margin:0 -1rem}
.slides{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none;overscroll-behavior-x:contain;height:min(100vw,46vh);height:min(100vw,46svh);min-height:15rem;
border-radius:0 0 var(--r-lg) var(--r-lg);background:var(--tone,#e9eeec)}
.slides::-webkit-scrollbar{display:none}
.slide{flex:0 0 100%;scroll-snap-align:center;height:100%}
.slide .media{height:100%;aspect-ratio:auto;border-radius:0}
.cnt{position:absolute;right:.9rem;bottom:.9rem;padding:.3rem .7rem;border-radius:999px;background:rgba(14,26,20,.72);color:#fff;font:700 .75rem/1.2 var(--sans);font-variant-numeric:tabular-nums}
.dotsnav{position:absolute;left:0;right:0;bottom:1rem;display:flex;justify-content:center;gap:.35rem;pointer-events:none}
.dotsnav i{width:.4rem;height:.4rem;border-radius:999px;background:rgba(255,255,255,.6);box-shadow:0 0 0 1px rgba(14,26,20,.12);transition:width .25s var(--ease),background-color .25s}
.dotsnav i.on{width:1.1rem;background:#fff}
.info{display:flex;flex-direction:column;gap:.5rem}
@media(min-width:32rem){.gallery{margin:0}.slides{border-radius:var(--r-lg)}.info{gap:.75rem}}
@media(min-width:56rem){.pdp{grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:2rem;align-items:start}.gallery{position:sticky;top:4.75rem}
.slides{height:auto;aspect-ratio:1;max-height:42rem}}
.pricebig{font:800 1.75rem/1.1 var(--sans);letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.pdp h1{font:650 1.125rem/1.4 var(--sans);letter-spacing:-.01em;margin-top:.55rem}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-top:.75rem}
.opt .cap{display:flex;gap:.4rem;align-items:baseline;margin-bottom:.7rem}.opt .cap span{font-weight:500;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:.5rem}
.chip{display:inline-flex;align-items:center;gap:.4rem;min-height:2.75rem;padding:0 1.1rem;border-radius:var(--r);background:var(--surface);color:var(--ink);font:650 .9375rem/1 var(--sans);
text-decoration:none;box-shadow:inset 0 0 0 1.5px var(--line-2);transition:box-shadow .18s,background-color .18s,color .18s,transform .15s var(--ease)}
.chip:active{transform:scale(.96)}
@media(hover:hover){.chip:hover{color:var(--ink);box-shadow:inset 0 0 0 1.5px var(--soft)}}
.chip.on{background:var(--brand-tint);color:var(--brand-ink);box-shadow:inset 0 0 0 2px var(--brand)}
.chip small{font-weight:500;color:var(--muted)}
.trust{display:flex;gap:.85rem;align-items:flex-start}
.trust .badge{display:grid;place-items:center;width:2.5rem;height:2.5rem;flex:none;border-radius:50%;background:var(--brand-soft);color:var(--brand)}
.trust b{display:block;font-weight:700}.trust p{font-size:.875rem;color:var(--muted);line-height:1.5;margin-top:.1rem}.trust a{font-weight:650;font-size:.875rem}
.sellerrow{display:flex;align-items:center;gap:.85rem}.sellerrow .who{min-width:0;flex:1}
.sellerrow b{display:block;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sellerrow small{display:flex;align-items:center;gap:.3rem;color:var(--muted);font-size:.875rem}
.about{color:var(--muted);line-height:1.6;white-space:pre-line}
.dock{position:sticky;bottom:0;z-index:20;order:99;display:flex;align-items:center;gap:1rem;margin:0 -1rem;padding:.75rem 1rem calc(.75rem + env(safe-area-inset-bottom));
background:#fff;box-shadow:0 -1px 0 var(--line),0 -10px 24px -16px rgba(14,26,20,.28)}
.dock .tot{display:grid;line-height:1.2;min-width:0}.dock .tot small{font:600 .75rem var(--sans);color:var(--muted)}.dock .tot b{font:800 1.25rem/1.2 var(--sans);letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.dock .btn{flex:1}
@media(min-width:32rem){.dock{margin:0;border-radius:var(--r-lg);box-shadow:var(--shadow-1)}}
@media(min-width:56rem){.pdp .dock{position:static;order:0}}
/* checkout */
.item{display:grid;grid-template-columns:4.5rem minmax(0,1fr);gap:.9rem;align-items:center}
.item .media{width:4.5rem;height:4.5rem;aspect-ratio:auto;border-radius:var(--r)}
.item .ph{font-size:1.5rem}
.item b{display:block;font-weight:650;line-height:1.35;overflow-wrap:anywhere}.item small{display:block;color:var(--muted);font-size:.875rem}
.item a{font-weight:650;font-size:.875rem;display:inline-flex;align-items:center;min-height:2rem}
.item .price{font-size:1rem;margin:.1rem 0 0;padding:0}
/* cart */
.barend{margin-left:auto;margin-right:-.6rem;display:flex;align-items:center}.bar .barend .iconbtn.end,.bar .barend .iconbtn:first-child{margin:0}
.cartlink{position:relative}.cartn{position:absolute;top:.15rem;right:.1rem;min-width:1.15rem;height:1.15rem;padding:0 .3rem;border-radius:999px;background:var(--brand);color:#fff;font:800 .6875rem/1.15rem var(--sans);text-align:center}
.cartline+.cartline{margin-top:1.1rem;padding-top:1.1rem;border-top:1px solid var(--line)}
.cartline .ops{display:flex;align-items:center;flex-wrap:wrap;gap:.6rem;margin-top:.6rem}
.cartline form{display:flex;align-items:center;gap:.5rem;margin:0}
.cartline input[type=number]{width:4.25rem;min-height:2.5rem;padding:.4rem .5rem;text-align:center}
.cartline .btn{min-height:2.5rem;padding:0 1rem;font-size:.9375rem}
.cartline.gone .item{opacity:.55}
.addform{display:contents}
.qtyrow{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-top:1rem;padding-top:1rem;border-top:1px solid var(--line)}
.qtyrow label{margin:0}
.stepper{display:flex;align-items:center;gap:.25rem;background:var(--surface);border-radius:var(--r);box-shadow:inset 0 0 0 1.5px var(--line-2);padding:.25rem}
.stepper input{width:3rem;min-height:2.75rem;padding:0;text-align:center;box-shadow:none;background:none;font-weight:700;-moz-appearance:textfield}
.stepper input::-webkit-inner-spin-button,.stepper input::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
.stepper input:focus{box-shadow:none}.stepper:focus-within{box-shadow:inset 0 0 0 2px var(--brand),0 0 0 4px rgba(8,129,90,.16)}
.st{display:none;place-items:center;width:2.75rem;height:2.75rem;border:0;border-radius:10px;background:var(--bg);color:var(--ink);cursor:pointer;transition:background-color .15s,transform .12s var(--ease)}
html.js .st{display:grid}.st:active{transform:scale(.88)}
@media(hover:hover){.st:hover{background:var(--line)}}
.pair{display:grid;grid-template-columns:3fr 2fr;gap:0 .75rem}
.pm{display:flex;gap:.85rem;align-items:center;padding:.9rem 1rem;border-radius:var(--r);background:var(--brand-tint);box-shadow:inset 0 0 0 2px var(--brand)}
.pm .ico{color:var(--brand)}.pm b{display:block;font-weight:700}.pm small{display:block;color:var(--muted);font-size:.8125rem;line-height:1.4}
.pm .tick{margin-left:auto;display:grid;place-items:center;width:1.5rem;height:1.5rem;flex:none;border-radius:50%;background:var(--brand);color:#fff}
.pm .tick .ico{width:.9rem;height:.9rem;color:#fff;stroke-width:2.6}
/* order, pay, return */
.track{display:grid;grid-template-columns:repeat(var(--n,4),1fr);list-style:none;margin-top:0;margin-bottom:0;padding:1rem .5rem .85rem}
.track li{position:relative;display:grid;justify-items:center;align-content:start;gap:.4rem;font:650 .75rem/1.25 var(--sans);color:var(--soft);text-align:center}
.track .dotc{position:relative;z-index:1;display:grid;place-items:center;width:2.25rem;height:2.25rem;border-radius:50%;background:var(--bg);color:var(--soft);transition:background-color .5s var(--ease),color .5s,box-shadow .5s}
.track .dotc .ico{width:1.05rem;height:1.05rem}
.track li::before{content:"";position:absolute;top:1.0625rem;right:50%;width:100%;height:2px;background:var(--line);transition:background-color .5s var(--ease)}
.track li:first-child::before{display:none}
.track li.done,.track li.cur{color:var(--ink)}
.track li.done .dotc{background:var(--brand);color:#fff}.track li.done::before,.track li.cur::before{background:var(--brand)}
.track li.cur .dotc{background:var(--surface);color:var(--brand);box-shadow:0 0 0 2px var(--brand),0 0 0 6px rgba(8,129,90,.14)}
.stat{position:relative;display:grid;grid-template-columns:auto 1fr;gap:.4rem .9rem;align-items:center;padding:1.25rem 1.15rem;border-radius:var(--r-lg);background:var(--surface);box-shadow:var(--shadow-1)}
.stat h2{font-size:1.25rem}.stat .sub{grid-column:1/-1;font-size:.9375rem;line-height:1.5;color:var(--muted)}
.stat .sub p+p{margin-top:.5rem}.stat .sub .btn{width:100%;margin-top:1rem}
.bubble{display:grid;place-items:center;width:2.75rem;height:2.75rem;border-radius:50%;background:var(--brand-soft);color:var(--brand)}
.bubble .ico{width:1.35rem;height:1.35rem}
.bubble.wait{background:var(--amber-bg);color:var(--amber)}.bubble.bad{background:var(--red-bg);color:var(--red)}.bubble.info{background:var(--bg);color:var(--muted)}
.stat.ok{background:var(--deep);color:#fff;box-shadow:none}.stat.ok .bubble{background:rgba(255,255,255,.14);color:#fff}
.stat.ok .sub{color:rgba(255,255,255,.86)}.stat.ok .sub a:not(.btn){color:#fff}.stat.ok code{background:rgba(255,255,255,.14);color:#fff}
.stat.ok .btn{background:#fff;color:var(--deep);box-shadow:none}.stat.ok .btn.quiet{background:rgba(255,255,255,.12);color:#fff}
.stat.wait{background:var(--amber-bg);color:#4d3000;box-shadow:none}.stat.wait .bubble{background:rgba(149,85,0,.14)}.stat.wait .sub{color:#6a4300}
.stat.bad{background:var(--red-bg);color:#6f1712;box-shadow:none}.stat.bad .bubble{background:rgba(179,38,30,.12)}.stat.bad .sub{color:#8a2a24}
.way{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-top:.5rem}.way .btn.sm{min-height:2.5rem;margin:0;width:auto}
.pay{text-align:center}
.timer{display:inline-flex;align-items:center;gap:.4rem;padding:.4rem .85rem;border-radius:999px;background:var(--amber-bg);color:var(--amber);font:700 .875rem/1 var(--sans);font-variant-numeric:tabular-nums}
.timer .ico{width:1rem;height:1rem}
.pay .lbl{margin-top:.85rem;font:600 .8125rem/1 var(--sans);color:var(--muted)}
.total{font:800 clamp(2.25rem,11vw,2.75rem)/1.1 var(--sans);letter-spacing:-.035em;font-variant-numeric:tabular-nums;margin:.3rem 0 0}
.qr{display:grid;justify-items:center;background:#fff;border-radius:var(--r-lg);padding:.85rem;margin:1rem auto 0;width:min(100%,15rem);box-shadow:0 0 0 1.5px var(--line);transition:opacity .4s}
.qr svg{width:100%;height:auto;display:block}
.pay .savebtn{margin-top:1rem;min-height:2.75rem;border-radius:999px}
.pay .gone{display:none;margin-top:.85rem;font-weight:700;color:var(--red)}
html:not(.js) [data-saveqr],html:not(.js) [data-share]{display:none}
.pay.dead .gone{display:block}.pay.dead .qr{opacity:.12}.pay.dead .savebtn{display:none}
.pay.dead .timer{background:var(--red-bg);color:var(--red)}
.how{margin:.85rem auto 0;max-width:24rem;color:var(--muted);font-size:.875rem;text-wrap:balance}
.lines{list-style:none;margin:0;padding:0}
.lines li{display:flex;justify-content:space-between;gap:1rem;padding:.6rem 0;color:var(--muted)}
.lines li+li{border-top:1px solid var(--line)}
.lines li span:first-child{min-width:0;overflow-wrap:anywhere}
.lines li span:last-child{font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--ink);font-weight:600}
.lines .sum{color:var(--ink);font-weight:800;font-size:1.0625rem}.lines .sum span:last-child{font-weight:800}
.links{display:flex;flex-wrap:wrap;gap:.5rem 1.25rem;margin:1rem 0 0}.links a{display:inline-flex;align-items:center;gap:.4rem;min-height:2.75rem;font-weight:650;text-decoration:none}
@media(hover:hover){.links a:hover{text-decoration:underline}}
.sellernote{border-radius:var(--r-lg);background:var(--amber-bg);color:#4d3000;padding:1rem 1.15rem}.sellernote b{display:block;font-weight:700;margin-bottom:.25rem}
.foot{color:var(--muted);font-size:.8125rem;padding:1rem 0 1.75rem}
.foot p{display:flex;gap:.6rem;align-items:center}.foot .mark{margin:0}.foot .mark svg{width:1.25rem;height:1.25rem}
.foot b{color:var(--ink);font-weight:800;letter-spacing:-.02em}
@media(prefers-reduced-motion:no-preference){
::view-transition-old(main){animation:.12s ease both vt-out}
::view-transition-new(main){animation:.3s var(--ease) both vt-in}
::view-transition-old(root),::view-transition-new(root){animation-duration:.01ms}
@keyframes vt-in{from{opacity:0}}@keyframes vt-out{to{opacity:0}}
}
@media(prefers-reduced-motion:reduce){::view-transition-group(*),::view-transition-old(*),::view-transition-new(*){animation:none!important}}
"""
CSS = BASE_CSS + PAGE_CSS

# Pairs of (soft background, ink) for a store avatar and for a product without a photo. A name always gets the same pair.
TONES = (("#e3f3ec", "#04503a"), ("#fdebd3", "#7a4300"), ("#e5eafb", "#2c3b95"), ("#fbe3ea", "#8f1f40"), ("#ece6f7", "#4d3187"), ("#e2f0f4", "#0b5568"))
COVERS = (("#0a5c42", "#1fb384"), ("#9a4a00", "#f0a73a"), ("#2c3b95", "#6a82f0"), ("#98234a", "#ec6a8e"), ("#4d3187", "#9a76e0"), ("#0b5568", "#35b3d1"))
FOOTER = "Sold through Etalase. The seller is paid only after delivery and the return window."


def initial(text: str) -> str:
    """The first letter or digit of a name, for an icon."""
    return next((ch.upper() for ch in text if ch.isalnum()), "E")


def tone(key: str) -> tuple[str, str]:
    return TONES[sum(map(ord, key)) % len(TONES)]


def tone_style(key: str) -> str:
    bg, ink = tone(key)
    c1, c2 = COVERS[sum(map(ord, key)) % len(COVERS)]
    return f"--tone:{bg};--tone-ink:{ink};--c1:{c1};--c2:{c2}"


def favicon() -> str:
    return f'<link rel="icon" href="data:image/svg+xml,{quote(FAVICON_SVG)}">'


def layout(title: str, body: str, *, h1: str | None = None, lead: str = "", back: tuple[str, str] | None = None,
           head: str = "", index: bool = False, wide: bool = False, live: str = "", heading: bool = True,
           action: str = "", flush: bool = False) -> str:
    """One page. `title` goes in <title>; `h1` defaults to it. `back` is (href, label) for the app bar's way back.
    `live` marks a page whose state changes by itself: the script swaps in the server's newer version in place, and
    without JavaScript a meta refresh does the same. `wide` gives the store and the buy page the full width, and
    `heading=False` lets a page draw its own h1 (the store name, the product name)."""
    robots = "index,follow" if index else "noindex,nofollow"
    meta = "<noscript><meta http-equiv=refresh content=5></noscript>" if live else ""
    width = "wrap" if wide else "wrap narrow"
    main_cls = width + (" flush" if flush else "")
    if back:
        left = (f'<a class=iconbtn href="{escape(back[0])}" aria-label="Back to {escape(back[1])}">{ico("arrow-left")}</a>'
                f'<span class=bartitle>{escape(back[1])}</span>' + (action or f'<span class=mark>{LOGO}</span>'))
    else:
        left = f'<span class=brand>{LOGO}Etalase</span>' + action
    intro = ""
    if heading:
        sub = f'<p class=lead>{escape(lead)}</p>' if lead else ""
        intro = f'<div class=pagehead><h1>{escape(h1 or title)}</h1>{sub}</div>'
    live_attr = f' data-live="{escape(live)}"' if live else ""
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name=theme-color content="#ffffff">'
            f'<meta name=robots content="{robots}">{meta}<title>{escape(title)}</title>{favicon()}'
            '<link rel=preload href="/static/plus-jakarta-sans.woff2" as=font type="font/woff2" crossorigin>'
            f"{head}<style>{CSS}</style></head><body>{sprite()}<a class=skip href=#main>Skip to content</a><div id=prog class=prog aria-hidden=true></div>"
            f'<header id=bar class=bar><div class="{width} in">{left}</div></header>'
            f'<main id=main class="{main_cls}" tabindex=-1{live_attr}>{intro}{body}</main>'
            f'<footer id=foot class=foot><div class="{width}"><p><span class=mark>{LOGO}</span><span><b>Etalase</b> · {escape(FOOTER)}</span></p></div></footer>'
            f'<div id=toast class=toast role=status aria-live=polite></div><script>{SCRIPT}</script></body></html>')


def qr_block(payload: str) -> str:
    import segno
    buf = io.BytesIO()
    # No width or height, so the viewBox lets CSS scale the code to the card.
    segno.make(payload or "-", error="m").save(buf, kind="svg", scale=5, border=1, xmldecl=False, svgns=True, omitsize=True)
    return f'<div class=qr role=img aria-label="QRIS payment code">{buf.getvalue().decode()}</div>'


def window(hours: int) -> str:
    """72 -> '3 days', 36 -> '36 hours'."""
    if hours and hours % 24 == 0:
        days = hours // 24
        return f"{days} day" + ("" if days == 1 else "s")
    return f"{hours} hours"


def clock(dt) -> str:
    """A time of day in Indonesian western time (WIB, UTC+7)."""
    return (dt + timedelta(hours=7)).strftime("%H:%M") + " WIB"


def stamp(dt) -> str:
    """A date and time in WIB, for example '18 Oct 2026, 14:32 WIB'."""
    return (dt + timedelta(hours=7)).strftime("%d %b %Y, %H:%M") + " WIB"


STEP_ICONS = ("user", "qr", "truck", "package")


def track(steps: list[str], current: int) -> str:
    """An order progress tracker. Steps before `current` are done; `current` == len(steps) means all done."""
    items = []
    for i, label in enumerate(steps):
        name = STEP_ICONS[i % len(STEP_ICONS)]
        if i < current:
            items.append(f'<li class=done><span class=dotc>{ico("check")}</span>{escape(label)}<span class=sr> (done)</span></li>')
        elif i == current:
            items.append(f'<li class=cur aria-current=step><span class=dotc>{ico(name)}</span>{escape(label)}<span class=sr> (current step)</span></li>')
        else:
            items.append(f'<li><span class=dotc>{ico(name)}</span>{escape(label)}</li>')
    return f'<ol class="band track" style="--n:{len(steps)}" aria-label="Order progress">' + "".join(items) + "</ol>"


_STATUS_ICON = {"ok": "check", "wait": "clock", "bad": "x", "info": "info"}


def status(kind: str, headline: str, detail: str = "", icon: str | None = None) -> str:
    """The state of an order as a banner: green when it went well, amber when something is pending, red when it did not
    work out. A coloured icon, a clear headline, then what happens next (detail is trusted HTML)."""
    return (f'<section class="stat {escape(kind)}"><span class="bubble {escape(kind)}">{ico(icon or _STATUS_ICON.get(kind, "info"))}</span>'
            f'<h2>{escape(headline)}</h2>' + (f'<div class=sub>{detail}</div>' if detail else "") + "</section>")


def summary(lines: list[tuple[str, str]], total: tuple[str, str] | None = None) -> str:
    """An order summary card. Each line is (label, amount), both plain text that is escaped here."""
    rows = "".join(f"<li><span>{escape(a)}</span><span>{escape(b)}</span></li>" for a, b in lines)
    if total:
        rows += f"<li class=sum><span>{escape(total[0])}</span><span>{escape(total[1])}</span></li>"
    return f'<section class=band><h2 class=cap>Order summary</h2><ul class=lines>{rows}</ul></section>'


def pay_card(total: str, qr_string: str, left: int | None = None, until: str = "") -> str:
    """The QRIS payment block shared by the order page and the UCP pay page: countdown, total, code, a way to save the code,
    and one line on how to pay. When the countdown reaches zero the script dims the code and shows the expired line."""
    timer = (f'<span class=timer>{ico("clock")}<span><span data-left="{left}">{left // 60}:{left % 60:02d}</span> left to pay</span></span>'
             if left is not None else "")
    return (f'<section class="band pay"><h2 class=sr>Pay with QRIS</h2>{timer}<p class=lbl>Total to pay</p><p class=total>{escape(total)}</p>'
            + qr_block(qr_string)
            + '<p class=gone role=status>This code has expired. Nothing was charged.</p>'
            + f'<button class="btn alt savebtn" type=button data-saveqr>{ico("image")}Save QR image</button>'
            + "<p class=how>Open your e-wallet or banking app and choose Scan. Paying on this phone? Save the image, then pick it from your gallery in the app."
            + (f" Pay before {escape(until)}; after that the item goes back on sale." if until else "") + "</p></section>")
