"""Pause source schedules after a complete target proof. Keep source webhook backups."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from ...archive import digest, write_private
from ...capture import Browser, parse_cli_result


def cutover(session: str, bundle_file: Path, proof_file: Path, receipt_file: Path, restore: bool = False) -> dict:
    bundle = json.loads(bundle_file.read_text())
    proof = json.loads(proof_file.read_text())
    if not proof.get('ok') or not proof.get('complete') or proof.get('bundleHash') != digest(bundle):
        raise ValueError('Cutover requires a complete matching target proof')
    browser = Browser(session)
    browser.read('roster')
    browser.attach_reader()
    receipt = json.loads(receipt_file.read_text()) if receipt_file.exists() else dict(bundleHash=digest(bundle), routines={})
    if receipt['bundleHash'] != digest(bundle):
        raise ValueError('The source cutover receipt belongs to a different bundle')
    changed = 0
    for bot in bundle['bots']:
        current = browser.read('routines', id=bot['key'])
        if not current['ok']:
            raise ValueError('Cannot read current source routines: ' + bot['name'])
        current = {item['id']: item for item in current['value']}
        for routine in bot['routines']:
            if routine['sourceTrigger']['type'] == 'webhook':
                continue
            key = bot['key'] + ':' + routine['sourceId']
            original = receipt['routines'].get(key)
            present = current.get(routine['sourceId'])
            if not present or present['prompt'] != routine['prompt'] or present['trigger'] != routine['sourceTrigger']:
                raise ValueError('The source routine changed since capture: ' + routine['name'])
            if original is None:
                if restore:
                    continue
                original = dict(botId=bot['key'], name=routine['name'], sourceId=routine['sourceId'], wasEnabled=present['isEnabled'])
                receipt['routines'][key] = original
                write_private(receipt_file, receipt)
            wanted = original['wasEnabled'] if restore else False
            if present['isEnabled'] == wanted:
                continue
            code = 'async () => { const a = window.__grokbotExport; if (!a?.automations?.setEnabled) throw new Error("Unsupported source routine client"); await a.automations.setEnabled(' + json.dumps(bot['key']) + ',' + json.dumps(routine['sourceId']) + ',' + json.dumps(wanted) + '); return {updated:true}; }'
            result = subprocess.run(['playwright-cli', f'-s={session}', 'eval', code], capture_output=True, text=True, timeout=45)
            if result.returncode or not parse_cli_result(result.stdout).get('updated'):
                raise RuntimeError('Source routine update failed: ' + routine['name'])
            state = browser.read('routines', id=bot['key'])
            actual = next((item for item in state.get('value', []) if item['id'] == routine['sourceId']), None)
            if not state['ok'] or not actual or actual['isEnabled'] != wanted:
                raise ValueError('Source routine update did not persist: ' + routine['name'])
            original['currentEnabled'] = wanted
            write_private(receipt_file, receipt)
            changed += 1
    receipt['state'] = 'restored' if restore else 'source-schedules-paused'
    write_private(receipt_file, receipt)
    return dict(state=receipt['state'], changed=changed, sourceWebhookBackups='preserved')
