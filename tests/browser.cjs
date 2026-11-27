/* Run with Playwright and Chromium. Set GRAVITY_APP to validate a built zipapp. */
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const {spawn,spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const readline = require('node:readline');
const root = path.resolve(__dirname,'..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(),'gravity-ui-'));
const child = spawn(process.env.GRAVITY_EXE || process.env.PYTHON || 'python',
  [...(process.env.GRAVITY_EXE ? [] : process.env.GRAVITY_APP ? [process.env.GRAVITY_APP] : ['-m','gravity_memory']), '--db',path.join(tmp,'workspace.sqlite'),'studio','--no-browser'],
  {cwd:root,env:{...process.env, GRAVITY_MODEL_DIR:path.join(tmp,'models')}, stdio:['ignore','pipe','inherit']});
async function run(){
  let browser;
  try {
    const lines = readline.createInterface({input:child.stdout});
    let url;
    for await(const line of lines){if(line.startsWith('http:')){url=line;break}}
    assert.ok(url, 'launch URL');
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined});
    const page=await browser.newPage({viewport:{width:1440,height:960},deviceScaleFactor:1});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
    await page.goto(url);
    await page.getByRole('heading',{name:'Memory field'}).waitFor();
    const shots=process.env.GRAVITY_SCREENSHOTS;
    if(shots){fs.mkdirSync(shots,{recursive:true});await page.screenshot({path:path.join(shots,'overview.png')})}
    assert.equal(await page.locator('#workspace').inputValue(),'sample');
    const nav=name=>page.locator('#navigation').getByRole('button',{name});
    await nav('Model library').click();
    await page.getByRole('heading',{name:'SmolLM2'}).waitFor();
    if(shots)await page.screenshot({path:path.join(shots,'models.png')});
    await page.locator('#preferred').selectOption('smol-backup');
    await page.locator('#fallback').uncheck();
    await page.getByRole('button',{name:'Save routing'}).click();
    await page.getByText('Local routing saved',{exact:true}).waitFor();
    await nav('Overview').click();
    await page.getByRole('heading',{name:'Memory field'}).waitFor();
    await page.locator('#workspace').selectOption('personal');
    await page.getByText('Add a source to start your archive.').waitFor();
    const capture=async(body,date)=>{
      await page.locator('#origin').fill('Customer handoff');
      await page.locator('#body').fill(body);
      if(date)await page.locator('#effective').fill(date);
      await page.getByRole('button',{name:'Save source'}).click();
      await page.locator('dialog').waitFor({state:'hidden'});
    };
    await page.locator('#add').click();
    await capture('I live in Dublin.','2024-04-05T12:00');
    await page.locator('button.row[data-key="home.city"]').click();
    await page.getByText('Dublin',{exact:true}).first().waitFor();
    await page.locator('[data-proof]').click();
    assert.match(await page.locator('#original mark').innerText(),/Dublin/);
    await page.keyboard.press('Escape');
    await page.locator('button.row[data-key="home.city"]').click();
    await page.getByRole('button',{name:'Record correction'}).click();
    await page.locator('#fixed-value').fill('Ghent');
    await capture('We confirmed the move to Ghent.','2024-06-12T12:00');
    await page.locator('button.row[data-key="home.city"]').click();
    await page.locator('.big').getByText('Ghent',{exact:true}).waitFor();
    await page.locator('#asof').fill('2024-05-01');
    await page.getByRole('button',{name:'Inspect'}).click();
    await page.locator('.big').getByText('Dublin',{exact:true}).waitFor({timeout:6000});
    await page.keyboard.press('Escape');
    await page.locator('#add').click();
    const delivery='🎙️ Friday morning was tentative. The delivery window is Friday morning.';
    await capture(delivery);
    await nav(/Review inbox/).click();
    await page.locator('[data-review]').first().click();
    await page.locator('#fieldkey').fill('delivery.window');
    await page.locator('#propose').click();
    await page.getByText(/no eligible local model/).waitFor();
    await page.locator('#fieldvalue').fill('Friday morning');
    await page.locator('#original').evaluate(node=>{
      const text=node.firstChild,range=document.createRange(),start=text.textContent.lastIndexOf('Friday morning');
      range.setStart(text,start);range.setEnd(text,start+'Friday morning'.length);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
    });
    await page.locator('#use-selection').click();
    await page.locator('#confirm-review').click();
    await page.getByText(/All caught up/).waitFor();
    await nav('Memory library').click();
    await page.locator('button.row[data-key="delivery.window"]').click();
    const citation=page.locator('button[data-proof]').first();
    await citation.waitFor();
    assert.equal(Number(await citation.getAttribute('data-start')),Array.from(delivery.slice(0,delivery.lastIndexOf('Friday morning'))).length);
    await page.keyboard.press('Escape');
    await nav('Source archive').click();
    await page.getByRole('textbox',{name:'Search source content'}).fill('Friday');
    await page.locator('#searchmode').selectOption('auto_archive');
    await page.locator('#searchform button').first().click();
    await page.getByText(/full_text/).waitFor();
    await page.locator('.listitem').first().click();
    assert.match(await page.locator('#original mark').innerText({timeout:4000}),/Friday/);
    await page.keyboard.press('Escape');
    await nav('Model library').click();
    await page.getByRole('heading',{name:'SmolLM2'}).waitFor();
    assert.equal(await page.locator('#preferred').inputValue(),'smol-backup');
    assert.equal(await page.locator('#fallback').isChecked(),false);
    if(process.env.GRAVITY_TEST_PACK){
      await page.locator('#packfile').setInputFiles(process.env.GRAVITY_TEST_PACK);
      const exportLink=page.getByRole('link',{name:'Export pack'});
      await exportLink.waitFor();
      const download=page.waitForEvent('download');
      await exportLink.click();
      const pack=await download;
      assert.equal(pack.suggestedFilename(),'minilm.gmpack');
      const copy=path.join(tmp,'transfer.gmpack');
      await pack.saveAs(copy);
      assert.equal(fs.statSync(copy).size, fs.statSync(process.env.GRAVITY_TEST_PACK).size);
      await page.getByRole('button',{name:'Index sources'}).click();
      await page.getByText(/Indexed .* new passages/).waitFor();
      await nav('Source archive').click();
      await page.getByRole('textbox',{name:'Search source content'}).fill('delivery');
      await page.locator('#searchmode').selectOption('auto_archive');
      await page.locator('#searchform button').first().click();
      await page.getByText(/hybrid · matching embedding index/).waitFor();
    }
    await page.locator('#workspace').selectOption('sample');
    await page.getByRole('heading',{name:'Memory field'}).waitFor();
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'mobile width');
    await page.locator('#mobile-menu').click();
    await nav('Memory library').click();
    await page.locator('.row').first().click();
    await page.locator('#asof').waitFor();
    await page.keyboard.press('Escape');
    if(shots){await page.locator('#message.show').waitFor({state:'hidden'});await page.locator('#mobile-menu').click();await nav('Overview').click();await page.getByRole('heading',{name:'Memory field'}).waitFor();await page.screenshot({path:path.join(shots,'mobile.png')})}
    assert.deepEqual(errors,[]);
    console.log('Desktop and mobile customer journeys passed. No browser errors.');
  } finally {
    if(browser)await browser.close();
    if(child.exitCode===null){const ended=new Promise(r=>child.once('close',r));if(process.env.GRAVITY_EXE && process.platform==='win32')spawnSync('taskkill',['/pid',String(child.pid),'/t','/f']);else child.kill();await ended}
    fs.rmSync(tmp,{recursive:true,force:true});
  }
}
run().catch(e=>{console.error(e);process.exitCode=1});
