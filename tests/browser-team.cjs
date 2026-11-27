/* Exercise the team product through a real browser, server and TypeScript package. */
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
const {spawn,spawnSync}=require('node:child_process');
const {randomUUID}=require('node:crypto');
const {once}=require('node:events');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const readline=require('node:readline');
const assert=require('node:assert/strict');

async function run(){
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'gravity-team-ui-'));
  const root=path.resolve(__dirname,'..'),ownerPassword=randomUUID(),viewerPassword=randomUUID();
  const cli=process.env.GRAVITY_EXE||process.env.PYTHON||'python';
  const cliArgs=process.env.GRAVITY_EXE?[]:process.env.GRAVITY_APP?[process.env.GRAVITY_APP]:['-m','gravity_memory'];
  const child=spawn(cli,[...cliArgs,'team','serve','--directory',tmp,'--port','0'],{cwd:root,stdio:['ignore','pipe','inherit'],env:{...process.env,GRAVITY_OWNER:'owner',GRAVITY_OWNER_PASSWORD:ownerPassword,GRAVITY_PUBLIC_ORIGIN:''}});
  let browser;
  try{
    const lines=readline.createInterface({input:child.stdout});
    let url;
    for await(const s of lines){if(s.startsWith('http://')){url=s;break}}
    assert.ok(url,'Team service printed a listening address');
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
    const page=await browser.newPage({viewport:{width:1380,height:960}});
    const frontendErrors=[];
    page.on('pageerror',e=>frontendErrors.push(e.message));
    page.setDefaultTimeout(20000);
    const nav=name=>page.locator('#navigation').getByRole('button',{name,exact:true});
    const button=name=>page.getByRole('button',{name,exact:true});
    const openAccount=()=>page.getByLabel('Account settings',{exact:true}).click();
    const modal=page.locator('#detail');
    const signIn=async(user,password)=>{await page.getByLabel('Username',{exact:true}).fill(user);await page.getByLabel('Password',{exact:true}).fill(password);await button('Sign in').click()};
    await page.goto(url);await signIn('owner',ownerPassword);
    await page.getByRole('heading',{name:'Request activity'}).waitFor();
    await nav('Namespaces').click();await button('Create namespace').click();
    await modal.getByLabel('Name',{exact:true}).fill('Engineering');
    await modal.getByLabel('Namespace ID').fill('eng');
    await modal.getByRole('button',{name:'Create namespace'}).click();
    await page.getByRole('heading',{name:'Request activity'}).waitFor();
    assert.equal(await page.locator('#workspace').inputValue(),'eng');
    await nav('Namespaces').click();await button('Create account').click();
    await modal.getByLabel('Username').fill('auditor');await modal.getByLabel('Initial password').fill(viewerPassword);
    await modal.getByRole('button',{name:'Create account'}).click();await modal.waitFor({state:'hidden'});
    await button('Change access').click();await modal.getByLabel('Account username').fill('auditor');
    await modal.getByLabel('Role').selectOption('viewer');await button('Save access').click();
    await page.locator('.plain-row').filter({hasText:'auditor'}).waitFor();
    await page.getByLabel('Retention in days').fill('90');await button('Save policy').click();
    await page.getByText('90 days',{exact:true}).waitFor();
    await nav('Connectors').click();
    await button('Connect Google Drive').click();
    await modal.getByText('Engineering',{exact:true}).waitFor();
    await modal.getByLabel('Server credential alias').waitFor();
    await page.keyboard.press('Escape');
    await nav('API keys').click();await button('Create API key').click();
    await modal.getByLabel('Name',{exact:true}).fill('handoff-agent');
    await modal.getByLabel('Write',{exact:true}).check();
    await modal.getByLabel('Feedback',{exact:true}).check();
    await modal.getByRole('button',{name:'Create key',exact:true}).click();
    const key=await modal.getByLabel('Agent key').inputValue();
    await page.keyboard.press('Escape');
    const {GravityClient}=await import('../sdk/typescript/index.js');
    const memory=new GravityClient({url,namespace:'eng',key});
    async function remember(id,value){
      const text='The release environment is '+value+'.';
      return memory.call('remember',{request_id:id,text,source:'Release runbook',facts:[{key:'release.environment',value,quote:text}]});
    }
    await remember('original','staging');
    const run=await memory.call('start_run',{request_id:'first-run',title:'Release handoff'});
    const recorded=await memory.call('recall',{request_id:'check-runtime',run_id:run.run_id,query:'release environment',budget_bytes:2048});
    assert.ok(recorded.items.some(i=>i.value==='staging'));
    await memory.call('handoff',{request_id:'handoff',run_id:run.run_id,summary:'Deployment ready for review'});
    await remember('correction','production');
    await nav('Agent runs').click();
    await button(/Release handoff/).click();
    await page.getByText('Deployment ready for review').waitFor();
    await page.locator('[data-trace]').filter({hasText:'Recall'}).click();
    await modal.getByRole('heading',{name:'Returned evidence'}).waitFor();
    await button('Compare with current recall').click();
    const cmp=page.locator('#comparison');
    await cmp.getByRole('heading',{name:'Current',exact:true}).waitFor();
    await cmp.locator('section').first().getByText('staging',{exact:true}).waitFor();
    await cmp.locator('section').last().getByText('production',{exact:true}).waitFor();
    await page.keyboard.press('Escape');
    await nav('API keys').click();await button('Revoke').click();await modal.getByRole('button',{name:'Revoke key'}).click();await modal.waitFor({state:'hidden'});
    await assert.rejects(()=>memory.call('status'),/401|unauthorized|credential/i);
    await openAccount();await button('Sign out').click();
    await signIn('auditor',viewerPassword);
    await page.getByRole('heading',{name:'Request activity'}).waitFor();
    assert.deepEqual(await page.locator('#workspace option').evaluateAll(xs=>xs.map(o=>o.value)),['eng']);
    assert.equal(await page.locator('#add').isVisible(),false);
    await nav('Agents & MCP').click();assert.equal(await button('Run verification').count(),0);
    await nav('API keys').click();await page.getByText('Administrator access required').waitFor();
    await openAccount();await button('Change password').click();
    await modal.getByLabel('Current password').fill(viewerPassword);const newPassword=randomUUID();
    await modal.getByLabel('New password',{exact:true}).fill(newPassword);
    await modal.getByLabel('Confirm new password').fill(newPassword);
    await button('Save new password').click();await modal.waitFor({state:'hidden'});
    await openAccount();await button('Sign out').click();await signIn('auditor',newPassword);
    await page.getByRole('heading',{name:'Request activity'}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.locator('#mobile-menu').click();await nav('Agent runs').click();
    await button(/Release handoff/).click();
    await page.getByRole('heading',{name:'Request timeline'}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'mobile inspector fits');
    assert.deepEqual(frontendErrors,[]);
    console.log('Shared console passed account, password rotation, grants, scoped key, real SDK recall, historical comparison, revocation, read-only and mobile checks.');
  }finally{
    if(browser)await browser.close();
    if(child.exitCode===null){const ended=once(child,'close');if(process.env.GRAVITY_EXE&&process.platform==='win32')spawnSync('taskkill',['/pid',String(child.pid),'/t','/f']);else child.kill();await ended}
    fs.rmSync(tmp,{recursive:true,force:true});
  }
}
run().catch(error=>{console.error(error);process.exitCode=1});
