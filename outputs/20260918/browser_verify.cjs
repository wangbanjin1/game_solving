const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--in-process-gpu','--disable-gpu','--disable-software-rasterizer'],timeout:20000});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const kind of ['exact','qoe']){
   await page.goto('file:///D:/coding/game_solving/outputs/20260918/browser_check_'+kind+'.html',{waitUntil:'load',timeout:30000});
   const status=await page.locator('body').getAttribute('data-replay-test');
   if(!status?.startsWith('PASS'))throw Error(kind+' '+status+' '+errors);
   console.log(kind,status);
   if(kind==='exact'){
    await page.locator('#strategy-detail').scrollIntoViewIfNeeded();
    await page.screenshot({path:'D:/coding/game_solving/outputs/20260918/strategy_preview.png'});
   }
  }
  if(errors.length)throw Error(errors.join('\n'));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
