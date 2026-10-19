async (page) => {
  // Locate the app's read client in a loaded function scope. Do not read auth values.
  // CDP is bound to the private headless process started for this export.
  const cdp = await page.context().newCDPSession(page);
  const root = await cdp.send('Runtime.evaluate', { expression: 'window.__grokbotExport.workflows.refresh' });
  const seen = new Set();
  async function find(objectId, depth) {
    if (!objectId || depth > 4 || seen.has(objectId)) return false;
    seen.add(objectId);
    const properties = await cdp.send('Runtime.getProperties', { objectId, ownProperties: true });
    if (properties.result.some(p => p.name === 'readMainAgentContext' && p.value?.type === 'function')) {
      await cdp.send('Runtime.callFunctionOn', { objectId,
        functionDeclaration: `function() { const source = this; const names = ['getAgentWorkflows', 'getAgentAutomations', 'getAgentTasks', 'getAgentChannels', 'getAgentTranscriptTail', 'getConversationOutline', 'readMainAgentContext', 'getBotTemplateForSourceAgent', 'getHostStatus', 'getForeverBoxStatus']; window.__grokbotExportReadGateway = Object.fromEntries(names.filter(name => typeof source[name] === 'function').map(name => [name, source[name].bind(source)])); return true; }`, returnByValue: true });
      return true;
    }
    const scopesId = properties.internalProperties?.find(p => p.name === '[[Scopes]]')?.value?.objectId;
    if (!scopesId) return false;
    const scopes = await cdp.send('Runtime.getProperties', { objectId: scopesId, ownProperties: true });
    for (const scope of scopes.result.filter(p => /^\d+$/.test(p.name) && !/Module|Global|Script/.test(p.value?.description ?? ''))) {
      const variables = await cdp.send('Runtime.getProperties', { objectId: scope.value.objectId, ownProperties: true });
      for (const variable of variables.result) {
        const value = variable.value;
        if (value?.type === 'object' && value.objectId && value.subtype !== 'map') {
          if (await find(value.objectId, depth + 1)) return true;
        }
        if (value?.type === 'function' && /getAgentWorkflows/.test(value.description ?? '')) {
          if (await find(value.objectId, depth + 1)) return true;
        }
      }
    }
    return false;
  }
  const found = await find(root.result.objectId, 0);
  await cdp.detach();
  if (!found) throw new Error('This Grok version does not expose a supported read client');
  return { attached: true, writesAllowed: false };
}
