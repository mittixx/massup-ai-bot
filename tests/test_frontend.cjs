// DOM-level unit tests without Telegram, browser automation or external requests.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const start = source.indexOf('$("weightSave").onclick=');
const end = source.indexOf('const adminEventNames', start);
assert.ok(start >= 0 && end > start);

function setup(weights = []) {
  const nodes = {
    weightInput: {value: '71.2'}, weightSave: {disabled: false},
    weightCurrent: {textContent: '', innerHTML: ''}, weightHistory: {innerHTML: ''},
    profileForm: {elements: {weight_kg: {value: ''}}},
  };
  const calls = [], messages = [];
  const context = vm.createContext({
    $: id => nodes[id], userId: 1, toast: text => messages.push(text),
    loadProfile: async () => {},
    api: async (url, options) => {
      calls.push({url, options});
      if (url === '/api/weight') {
        weights = [{measured_on: '2026-09-08', weight_kg: JSON.parse(options.body).weight_kg}];
        return {ok: true};
      }
      return {weights};
    },
  });
  vm.runInContext(source.slice(start, end), context);
  return {nodes, calls, messages, context};
}

test('weight save renders value and date, resets input and releases button', async () => {
  const {nodes, calls} = setup();
  const pending = nodes.weightSave.onclick();
  assert.equal(nodes.weightSave.disabled, true);
  await pending;
  assert.equal(nodes.weightSave.disabled, false);
  assert.equal(nodes.weightInput.value, '');
  assert.match(nodes.weightCurrent.innerHTML, /71.2 кг/);
  assert.match(nodes.weightCurrent.innerHTML, /8 сентября/);
  assert.match(nodes.weightHistory.innerHTML, /08.09/);
  assert.match(nodes.weightHistory.innerHTML, /height:52.5px/);
  assert.equal(calls.filter(x => x.url === '/api/weight').length, 1);
});

test('empty progress clears old graph', async () => {
  const {nodes, context} = setup();
  nodes.weightHistory.innerHTML = 'old';
  await vm.runInContext('loadProgress()', context);
  assert.equal(nodes.weightHistory.innerHTML, '');
  assert.match(nodes.weightCurrent.textContent, /пуста/);
});

test('invalid weight does not call server', async () => {
  const {nodes, calls, messages} = setup();
  nodes.weightInput.value = '5';
  await nodes.weightSave.onclick();
  assert.equal(calls.length, 0);
  assert.match(messages[0], /35 до 300/);
});

test('failed save keeps typed weight and enables retry', async () => {
  const {nodes, context, messages} = setup();
  context.api = async () => {throw new Error('Сервер временно недоступен');};
  await nodes.weightSave.onclick();
  assert.equal(nodes.weightSave.disabled, false);
  assert.equal(nodes.weightInput.value, '71.2');
  assert.match(messages[0], /недоступен/);
});

test('weight bars use bounded non-growing width', () => {
  const css = fs.readFileSync(path.join(__dirname, '../web/weight.css'), 'utf8');
  assert.match(css, /flex:\s*0 0 42px/);
  assert.match(css, /overflow-x:\s*auto/);
});

test('AI coach exposes all requested tools and reminders', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  for (const id of ['coachView', 'adviceMode', 'labelInput', 'reminderForm', 'forecastText', 'achievementList']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const mode of ['top_up', 'review', 'recipe', 'swap', 'portion', 'coach']) {
    assert.match(html, new RegExp(`value="${mode}"`));
  }
  assert.match(source, /\/api\/insights/);
  assert.match(source, /\/api\/advice/);
  assert.match(source, /\/api\/label/);
  assert.match(source, /\/api\/reminders/);
});

test('owner admin panel is hidden by default and loads protected APIs', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  for (const id of ['adminView', 'adminNav', 'adminMetrics', 'adminUsers', 'adminEvents', 'adminTrend']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /id="adminNav"[^>]*class="hidden"|class="hidden"[^>]*id="adminNav"/);
  assert.match(source, /\/api\/admin\/session/);
  assert.match(source, /\/api\/admin\/overview/);
  assert.match(source, /\/api\/admin\/users/);
  const css = fs.readFileSync(path.join(__dirname, '../web/admin.css'), 'utf8');
  assert.match(css, /nav\.admin-nav-enabled/);
});
