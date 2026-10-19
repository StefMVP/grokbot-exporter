(async function capture(request) {
  const element = document.querySelector('main');
  if (!element) throw new Error('Open the signed-in Grok Bot conversation screen first');
  let fiber = element[Object.keys(element).find(k => k.startsWith('__reactFiber'))];
  while (fiber) {
    const value = fiber.memoizedProps?.value;
    if (value?.roster?.snapshots && value?.transcript?.loadOlder && value?.workflows?.refresh) {
      window.__grokbotExport = value;
      break;
    }
    fiber = fiber.return;
  }
  const app = window.__grokbotExport;
  if (!app) throw new Error('Unsupported Grok renderer: the read-only data store was not found');
  if (request.operation === 'roster') {
    const state = app.roster.snapshots.get();
    if (!state.hasCompleteRoster) throw new Error('Grok roster is incomplete; reconnect and retry');
    return { agents: state.agents.rows, capturedAt: new Date().toISOString(), url: location.href };
  }
  const id = request.id;
  const read = async (operation) => {
    try { return { ok: true, value: await operation() }; }
    catch (error) { return { ok: false, error: String(error.message ?? error) }; }
  };
  const gateway = window.__grokbotExportReadGateway;
  if (request.operation === 'routines') {
    if (!gateway) throw new Error('Attach the read client before capture');
    return read(() => gateway.getAgentAutomations({ id }));
  }
  if (request.operation === 'setup') {
    if (!gateway) throw new Error('Attach the read client before capture');
    return {
      skills: await read(() => gateway.getAgentWorkflows({ id })),
      routines: await read(() => gateway.getAgentAutomations({ id })),
      tasks: await read(() => gateway.getAgentTasks({ agentId: id })),
      channels: await read(() => gateway.getAgentChannels({ id })),
      context: await read(() => gateway.readMainAgentContext({ id })),
      outline: await read(() => gateway.getConversationOutline({ id })),
      template: await read(() => gateway.getBotTemplateForSourceAgent({ sourceAgentId: id })),
      sessions: await read(() => window.desktop.main.listGrokBotAgentSessions({ agentId: id })),
      memories: await read(() => window.desktop.main.listGrokBotUserBotMemories({ agentId: id })),
      files: await read(() => window.desktop.main.listGrokBotAgentFiles({ agentId: id })),
      plugins: await read(() => window.desktop.main.getGrokBotAgentPlugins({ agentId: id })),
      documents: await read(() => window.desktop.main.listLibraryDocs({ agentId: id })),
    };
  }
  if (request.operation === 'page') {
    if (!gateway) throw new Error('Attach the read client before capture');
    return gateway.getAgentTranscriptTail({ id, limit: 500,
      ...(request.beforeSeq === undefined ? {} : { beforeSeq: request.beforeSeq }) });
  }
  if (request.operation === 'texts') {
    return Promise.all(request.paths.map(async path => {
      try {
        const result = await window.desktop.main.readAttachmentBytes({ path, maxBytes: 8 * 1024 * 1024, agentId: id, viewSlot: app.accountSlot });
        const value = result?.kind === 'bytes' ? { kind: 'text', text: new TextDecoder().decode(result.bytes), truncated: result.bytes.length !== result.size } : null;
        return { path, value };
      } catch (error) { return { path, error: String(error.message ?? error) }; }
    }));
  }
  if (request.operation === 'box') return gateway.getForeverBoxStatus({ id });
  if (request.operation === 'image') return window.desktop.main.resolveAttachmentMedia({ source: request.path, inline: true, rendition: 'original', viewSlot: app.accountSlot });
  if (request.operation === 'attachment') {
    const result = await window.desktop.main.readAttachmentBytes({ path: request.path,
      maxBytes: 32 * 1024 * 1024, agentId: id, viewSlot: app.accountSlot });
    if (result?.kind === 'bytes') {
      let binary = '';
      for (let offset = 0; offset < result.bytes.length; offset += 8192) binary += String.fromCharCode(...result.bytes.subarray(offset, offset + 8192));
      return { kind: 'bytes', base64: btoa(binary), size: result.size, resolvedName: result.resolvedName };
    }
    return result;
  }
  if (request.operation === 'text') {
    return window.desktop.main.readAttachmentText({ path: request.path, agentId: id, viewSlot: app.accountSlot });
  }
  throw new Error('Unsupported capture operation');
})
