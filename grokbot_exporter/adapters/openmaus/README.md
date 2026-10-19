# Import into OpenMaus

This optional adapter converts a saved Grok export to `openmaus.backup` and
imports it into a local OpenMaus server. The export core does not need OpenMaus.

## Check the server

The adapter needs `POST /api/migrations/bots`, plus the native bot, memory,
skill, routine, file, and attachment APIs. The migration route must support
source namespaces, stable source IDs, optional existing bot bindings, reactions,
and refusal to replace local edits. It needs loopback access and admin scope.

This route was added in a private OpenMaus-derived app. A public compatible
server release has not been verified. A standard OpenMaus installation can lack
it. The local protocol twin tests the client contract; it does not establish
compatibility with a server release.

## Build and import

Copy [config.example.json](../../../config.example.json) to a private folder. Set the
model instance and model to values supported by your server. Optional bindings
map source bot names to existing target bot IDs. `chiefOfStaff` lists source bot
IDs or names. `runtimeNotes` adds your host instructions.

```sh
grokbot-exporter openmaus build --source "$HOME/grok-export-private/export" \
  --output "$HOME/grok-export-private/openmaus.json" --host-root "$HOME/Work" \
  --policy "$HOME/grok-export-private/config.json"
grokbot-exporter openmaus apply --bundle "$HOME/grok-export-private/openmaus.json" \
  --config "$HOME/grok-export-private/config.json" --url http://127.0.0.1:8799 \
  --receipt "$HOME/grok-export-private/receipt.json"
grokbot-exporter openmaus verify --bundle "$HOME/grok-export-private/openmaus.json" \
  --url http://127.0.0.1:8799 --receipt "$HOME/grok-export-private/receipt.json" --complete
```

`build` reads the export and writes a target-specific bundle. `apply` saves a
partial native team backup, then imports. Keep the export on the target host:
archived report links use its local paths. Build a new bundle if that path changes.
Use the same receipt for an interrupted retry. Local edits stop the retry.
A full rollback needs a complete native workspace backup.

## Map source behavior

The adapter seeds short memory, maps mascot shapes and colors, and converts
history cards to text. Raw source records stay in the export. Past approvals
and tool calls do not run again. Personal Grok memory and live account sessions
are not transferred.

User workflow skills are enabled by default. Managed wrappers remain available
but disabled. `skillSourcesEnabled` selects other source classes. Target routines
retain source enabled states and time zones. Jitter stays in the raw trigger;
the target uses nominal cron times. Webhooks need an explicit replacement in
`routineReplacements` before import. A replacement records a handoff; verification
does not check the external replacement service.

## Pause or restore source schedules

After complete verification, the command below pauses source schedules. It
keeps webhooks. The source browser session must still be running.

```sh
grokbot-exporter openmaus cutover --session grok-export \
  --bundle "$HOME/grok-export-private/openmaus.json" \
  --proof "$HOME/grok-export-private/receipt-proof.json" \
  --receipt "$HOME/grok-export-private/cutover.json"
```

Add `--restore` to restore the saved enabled states. Keep all receipts private.
