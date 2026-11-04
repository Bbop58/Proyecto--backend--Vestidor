// Disposable sandbox acceptance harness. Buyer credentials arrive over stdin,
// remain in memory, and are never written to reports, screenshots or logs.
const {chromium}=require('../../web/node_modules/@playwright/test');
const {createInterface}=require('node:readline');
const {writeFile}=require('node:fs/promises');
const {randomUUID,createHash}=require('node:crypto');
const path=require('node:path');
const base='https://proyecto-backend-vestidor-production.up.railway.app/api/v1';
if(!process.argv.includes('--sandbox-acceptance')||!process.stdin.isTTY)throw Error('Use an interactive terminal and --sandbox-acceptance for the disposable demo only');
const filename=process.argv.includes('--after-fix')?'FASE_8_PAYPAL_COMPRADOR_CORREGIDO.json':'FASE_8_PAYPAL_COMPRADOR.json';
const output=path.resolve(__dirname,'../../docs/auditoria_software',filename);
let browser,page,login,order,details,branch,quote,stockBefore;
const evidence={environment:'sandbox',real_payment:false,journey:'Sandbox buyer browser approval plus real API capture, not APK capture button',checks:[]};
if(process.stdin.isTTY)process.stdin.setRawMode(true);
async function api(route,method='GET',payload,token=login?.access_token){
 const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(payload?{body:JSON.stringify(payload)}:{})});
 return {status:response.status,body:await response.json()};
}
function safeHost(){return new URL(page.url()).hostname;}
function requireSandbox(){if(!['www.sandbox.paypal.com','sandbox.paypal.com'].includes(safeHost()))throw Error('Not a sandbox PayPal page');}
async function state(){
 const elements=await page.locator('input,button,[role=button],a').evaluateAll(nodes=>nodes.filter(n=>n.getBoundingClientRect().width&&n.getBoundingClientRect().height).map(n=>({tag:n.tagName,id:n.id,name:n.getAttribute('name'),type:n.getAttribute('type'),label:n.tagName==='INPUT'?n.getAttribute('placeholder'):(n.innerText||n.getAttribute('aria-label')||'').trim().slice(0,100)})));
 console.log(JSON.stringify({host:safeHost(),path:new URL(page.url()).pathname,elements}));
}
async function setup(){
 const {body:companies}=await api('/subscriptions/public/tenants');
 const company=companies.find(c=>c.slug==='urbanstyle');
 const config=await api('/payments/paypal/config?tenant_id='+company.id);if(config.body.mode!=='sandbox')throw Error('Refusing non-sandbox config');
 const auth=await api('/auth/login','POST',{email:'cliente@ficttstore.com',password:'Password123!',tenant_id:company.id});if(auth.status!==200)throw Error('Demo client login failed');login=auth.body;
 const products=(await api('/productos')).body;
 const product=products.find(p=>p.activo&&p.variantes?.some(v=>v.activo));
 const variant=product.variantes.find(v=>v.activo);
 quote='/inventario/disponibilidad?'+new URLSearchParams({producto_id:product.id,talla:variant.talla,color:variant.color});
 const stock=(await api(quote)).body;const available=stock.find(s=>s.stock_disponible>0);
 if(!available)throw Error('No stock');branch=available.sucursal_id;stockBefore=available.stock_disponible;
 details=[{variante_id:variant.id,cantidad:1}];
 const created=await api('/payments/paypal/create-order','POST',{monto_bob:Number(available.precio),sucursal_id:branch,detalles:details,request_id:randomUUID()});
 if(created.status!==201)throw Error('Order creation failed '+created.status);order=created.body;
 const url=new URL(order.approve_url);if(!['www.sandbox.paypal.com','sandbox.paypal.com'].includes(url.hostname))throw Error('Refusing non-sandbox approval URL');
 evidence.order_id=order.order_id;evidence.amount_bob=order.monto_bob;evidence.amount_usd=order.monto_usd;evidence.created_utc=new Date().toISOString();
 await writeFile(output,JSON.stringify(evidence,null,2));
 browser=await chromium.launch({channel:'chrome',headless:true});page=await browser.newPage();page.setDefaultTimeout(20000);
 await page.goto(url.href,{waitUntil:'domcontentloaded'});await state();
}
async function capture(){
 // The provider must have returned to our configured page after approval.
 const url=new URL(page.url());if(url.hostname!=='proyecto-backend-vestidor-production.up.railway.app'||!url.pathname.endsWith('/paypal/return')||url.searchParams.get('token')!==order.order_id)throw Error('Approval return not observed');
 const payload={order_id:order.order_id,sucursal_id:branch,detalles:details,nota:'Aceptacion externa sandbox fase 8'};
 const first=await api('/payments/paypal/capture-order','POST',payload);
 evidence.capture_attempts??=[];evidence.capture_attempts.push(first.status);
 evidence.capture_http=first.status;if(first.status!==200){evidence.error=first.body.detail;await writeFile(output,JSON.stringify(evidence,null,2));console.log(JSON.stringify({capture_http:first.status,error:first.body.detail}));return;}
 delete evidence.error;
 const sale=first.body.venta;
 evidence.receipt=sale.numero_recibo;evidence.sale_id=sale.id;evidence.capture_fingerprint=createHash('sha256').update(first.body.paypal_capture_id).digest('hex');
 evidence.checks.push({name:'Provider captured and sale completed',pass:first.body.paypal_status==='COMPLETED'&&sale.estado==='COMPLETADA'&&sale.metodo_pago==='PAYPAL'});
 const repeated=await api('/payments/paypal/capture-order','POST',payload);
 evidence.checks.push({name:'Same-order retry returns same sale and capture',pass:repeated.status===200&&repeated.body.venta.id===sale.id&&repeated.body.paypal_capture_id===first.body.paypal_capture_id});
 const history=await api('/ventas/mis-compras');
 evidence.checks.push({name:'Exactly one receipt in own customer history',pass:history.status===200&&history.body.filter(s=>s.id===sale.id||s.numero_recibo===sale.numero_recibo).length===1});
 const after=(await api(quote)).body.find(s=>s.sucursal_id===branch);
 evidence.checks.push({name:'Stock decremented exactly once after capture and retry',pass:after.stock_disponible===stockBefore-1});
 await writeFile(output,JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence));
}
const rl=createInterface({input:process.stdin,terminal:false,crlfDelay:Infinity});
(async()=>{
 try {
  await setup();
  for await(const line of rl){
   try {
    const action=JSON.parse(line);
    if(action.action==='state')await state();
    else if(action.action==='fill'){requireSandbox();await page.locator(action.selector).fill(action.value);console.log('Field filled; value not logged.');}
    else if(action.action==='click'){requireSandbox();await page.locator(action.selector).click();await page.waitForLoadState('domcontentloaded').catch(()=>{});await state();}
    else if(action.action==='capture')await capture();
    else if(action.action==='close')break;
   }catch(error){console.log(JSON.stringify({action_failed:true,type:error.name}));}
  }
 }catch(error){console.log(JSON.stringify({setup_failed:true,type:error.name,reason:error.message.replace(/https?:\/\/\S+/g,'[url]')}));}
 finally {
  if(login)await api('/auth/logout','POST',{refresh_token:login.refresh_token}).catch(()=>{});
  if(browser)await browser.close();rl.close();
 }
})();
