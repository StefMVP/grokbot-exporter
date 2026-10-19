#!/usr/bin/env node
// Protocol twin for playwright-cli. All state and files belong to the test.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');

const stateFile = process.env.GROKBOT_TWIN_STATE;
assert.ok(stateFile, 'The source twin needs a test state file');
const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
const args = process.argv.slice(2);
assert.equal(args[0], '-s=digital-twin');
const calls = state.calls ??= [];
const read = (name, value) => { calls.push(name); return value; };
const gateway = {
  getAgentWorkflows: ({id}) => read('skills', state.setup[id].skills.value),
  getAgentAutomations: ({id}) => read('routines', state.setup[id].routines.value),
  getAgentTasks: () => [], getAgentChannels: () => [],
  readMainAgentContext: () => ({}), getConversationOutline: () => ({}),
  getBotTemplateForSourceAgent: () => ({}),
  getAgentTranscriptTail: ({id, beforeSeq}) => {
    calls.push('page');
    if (state.failBeforeSeq === beforeSeq && beforeSeq !== undefined) {
      delete state.failBeforeSeq;
      throw new Error('Twin connection interrupted');
    }
    let entries = state.entries[id].filter(item => state.stall || beforeSeq === undefined || item.seq < beforeSeq);
    return {entries: entries.slice(-2).reverse()};
  },
};
const app = {
  roster: {snapshots: {get: () => ({hasCompleteRoster: true, agents: {rows: state.roster.agents}})}},
  transcript: {loadOlder() {}}, workflows: {refresh() {}}, accountSlot: 'test',
  automations: {setEnabled: async (id, routineId, enabled) => {
    calls.push('setEnabled');
    state.setup[id].routines.value.find(item => item.id === routineId).isEnabled = enabled;
  }},
};
function remoteFile(value) {
  assert.ok(value.startsWith('/workspace/.grokbot-export/'));
  const relative = value.slice('/workspace/.grokbot-export/'.length);
  assert.ok(!relative.includes('..'));
  return path.join(state.remoteRoot, relative);
}
const desktop = {
  listGrokBotAgentSessions: () => [], listGrokBotUserBotMemories: () => [],
  listGrokBotAgentFiles: () => [], getGrokBotAgentPlugins: () => [], listLibraryDocs: () => [],
  readAttachmentBytes: ({path: value}) => {
    calls.push('attachment');
    if (value.startsWith('/workspace/.grokbot-export/')) {
      const bytes = fs.readFileSync(remoteFile(value));
      return {kind: 'bytes', bytes: new Uint8Array(bytes), size: bytes.length};
    }
    if (value.endsWith('.png')) return null;
    return null;
  },
  readAttachmentText: ({path: value}) => ({text: fs.readFileSync(remoteFile(value), 'utf8'), truncated: false}),
  resolveAttachmentMedia: ({source}) => ({src: 'data:image/png;base64,' + state.files[source]}),
};
const window = {desktop: {main: desktop}};
if (state.discovered) window.__grokbotExport = app;
if (state.attached) window.__grokbotExportReadGateway = gateway;
const element = {__reactFiberTest: {memoizedProps: {value: app}}};
const sandbox = {window, document: {querySelector: () => element}, location: {href: 'file:///digital-twin/index.html'},
  TextDecoder, btoa: value => Buffer.from(value, 'binary').toString('base64')};
const cdp = {
  send: async (method, params) => {
    if (method === 'Runtime.evaluate') {
      assert.equal(typeof vm.runInNewContext(params.expression, sandbox), 'function');
      return {result: {objectId: 'refresh'}};
    }
    if (method === 'Runtime.getProperties') {
      if (params.objectId === 'refresh') return {result: [], internalProperties: [{name: '[[Scopes]]', value: {objectId: 'scopes'}}]};
      if (params.objectId === 'scopes') return {result: [{name: '0', value: {objectId: 'closure', description: 'Closure'}}]};
      if (params.objectId === 'closure') return {result: [{name: 'client', value: {type: 'object', objectId: 'gateway'}}]};
      if (params.objectId === 'gateway') return {result: Object.keys(gateway).map(name => ({name, value: {type: 'function'}}))};
      throw new Error('Unknown CDP object');
    }
    if (method === 'Runtime.callFunctionOn') {
      vm.runInNewContext('(' + params.functionDeclaration + ')', sandbox).call(gateway);
      return {};
    }
    throw new Error('Unexpected CDP command: ' + method);
  },
  detach: async () => {},
};
(async () => {
  try {
    let result;
    if (args[1] === 'run-code') {
      const code = fs.readFileSync(args[args.indexOf('--filename') + 1], 'utf8');
      result = await vm.runInNewContext('(' + code + ')', sandbox)({context: () => ({newCDPSession: async () => cdp})});
    } else {
      assert.equal(args[1], 'eval');
      result = await vm.runInNewContext('(' + args[2] + ')', sandbox)();
    }
    state.discovered = !!window.__grokbotExport;
    state.attached = !!window.__grokbotExportReadGateway;
    process.stdout.write('### Result\n' + JSON.stringify(result) + '\n### Ran code\n');
  } catch (error) {
    process.stdout.write('### Error\n' + error.message + '\n');
    process.stderr.write(error.message);
    process.exitCode = 1;
  } finally {
    fs.writeFileSync(stateFile, JSON.stringify(state));
  }
})();
