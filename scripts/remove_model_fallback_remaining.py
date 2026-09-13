from pathlib import Path
import ast
import re


def rw(path: str, fn):
    p = Path(path)
    p.write_text(fn(p.read_text()))


def remove_functions(path: str, names: set[str]):
    p = Path(path)
    s = p.read_text()
    tree = ast.parse(s)
    lines = s.splitlines(True)
    spans = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names:
            a = sum(map(len, lines[: n.lineno - 1]))
            b = sum(map(len, lines[: n.end_lineno]))
            while b < len(s) and s[b:b+1] == "\n":
                b += 1
            spans.append((a, b))
    for a, b in sorted(spans, reverse=True):
        s = s[:a] + s[b:]
    p.write_text(s)


rw('nanobot/model_settings.py', lambda s: re.sub(
    r'\n\ndef _model_call_order_state\(.*?\n\ndef _validate_configured_provider',
    '\n\ndef _validate_configured_provider', s, flags=re.S
).replace(
    'activate_as_primary = not config.model_presets and not _legacy_model_configuration_migratable(config, oauth_status)',
    'activate_as_primary = not config.model_presets',
))

p = Path('nanobot/webui/settings_models.py'); s = p.read_text()
s = s.replace('    migrate_models: SettingsOperation\n    update_call_order: SettingsOperation\n', '')
s = s.replace('    model_call_order: list[str]\n    model_call_order_editable: bool\n    model_configuration_migratable: bool\n', '')
s = re.sub(r'\n\ndef _unique_model_configuration_name\(.*?\n\ndef _validate_configured_provider', '\n\ndef _validate_configured_provider', s, flags=re.S)
s = s.replace('    model_call_order, model_call_order_editable = _model_call_order_state(config)\n', '')
s = s.replace('        "model_call_order": model_call_order,\n', '')
s = s.replace('        "model_call_order_editable": model_call_order_editable,\n        "model_configuration_migratable": _legacy_model_configuration_migratable(\n            config,\n            oauth_status,\n        ),\n', '')
s = s.replace('    activate_as_primary = not config.model_presets and not _legacy_model_configuration_migratable(\n        config, oauth_status\n    )', '    activate_as_primary = not config.model_presets')
s = re.sub(r'\n\ndef update_model_call_order\(.*?\n\ndef update_model_prompt_overrides', '\n\ndef update_model_prompt_overrides', s, flags=re.S)
s = re.sub(r'\n\ndef migrate_model_configurations\(.*?\n\ndef delete_model_configuration', '\n\ndef delete_model_configuration', s, flags=re.S)
s = s.replace('                "models-migrate": operations.migrate_models,\n                "call-order-update": operations.update_call_order,\n', '')
p.write_text(s)

rw('nanobot/webui/settings_api.py', lambda s: re.sub(
    r'\n\ndef migrate_model_configurations\(.*?\n\ndef delete_model_configuration', '\n\ndef delete_model_configuration',
    re.sub(r'\n\ndef update_model_call_order\(.*?\n\ndef update_model_prompt_overrides', '\n\ndef update_model_prompt_overrides', s, flags=re.S), flags=re.S
))

p = Path('nanobot/webui/settings_routes.py'); s = p.read_text()
for x in ('    migrate_model_configurations,\n', '    update_model_call_order,\n', '    "/api/settings/model-configurations/migrate": "models-migrate",\n', '    "/api/settings/model-call-order/update": "call-order-update",\n', '    "/api/settings/model-configurations/migrate",\n', '    "/api/settings/model-call-order/update",\n', '            migrate_models=migrate_model_configurations,\n            update_call_order=update_model_call_order,\n'):
    s = s.replace(x, '')
p.write_text(s)
rw('nanobot/webui/ws_http.py', lambda s: s.replace('    "settings.model_configuration.migrate": "/api/settings/model-configurations/migrate",\n','').replace('    "settings.model_call_order.update": "/api/settings/model-call-order/update",\n',''))

rw('nanobot/session/webui_turns.py', lambda s: s.replace('from nanobot.agent.tools.context import current_request_context\n',''))
rw('tests/utils/test_webui_turn_helpers.py', lambda s: s.replace('from nanobot.agent.tools.context import RequestContext, request_context\n',''))
p = Path('tests/webui/test_settings_api.py'); s = p.read_text()
s = s.replace('from nanobot.session.manager import SessionManager\n','').replace('from nanobot.session.model_selection import SESSION_MODEL_PRESET_METADATA_KEY\n','')
s = s.replace('    migrate_model_configurations,\n','').replace('    update_model_call_order,\n','')
p.write_text(s)
remove_functions('tests/webui/test_settings_api.py', {'test_schema_default_is_not_exposed_or_materialized_as_legacy_configuration','test_update_model_call_order_selects_exactly_one_preset','test_update_model_call_order_rejects_multiple_presets'})
remove_functions('tests/agent/test_onboard_logic.py', {'test_fallback_models_field_add'})
remove_functions('tests/providers/test_custom_thinking_style.py', {'test_provider_signature_tracks_dynamic_fallback_thinking_style'})
rw('tests/agent/test_model_runtime_resolver.py', lambda s: s.replace('    assert "include_fallbacks" not in call.kwargs\n',''))
p=Path('tests/webui/test_settings_routes.py'); s=p.read_text()
s=re.sub(r'        \(\n            "/api/settings/model-configurations/migrate",.*?        \),\n','',s,flags=re.S)
s=re.sub(r'        \(\n            "/api/settings/model-call-order/update",.*?        \),\n','',s,flags=re.S)
p.write_text(s)

p=Path('webui/src/lib/api.ts'); s=p.read_text()
s=re.sub(r'\nexport async function migrateModelConfigurations\(.*?\n}\n\nexport async function updateModelCallOrder\(.*?\n}\n','\n',s,flags=re.S); p.write_text(s)
rw('webui/src/lib/types.ts', lambda s: s.replace('  model_call_order: string[];\n  model_call_order_editable: boolean;\n','').replace('  /** Whether an actual legacy model configuration is available to convert. */\n  model_configuration_migratable?: boolean;\n',''))

p=Path('webui/src/components/settings/models/useModelSettingsState.ts'); s=p.read_text()
s=s.replace('  const [modelCallOrderSaving, setModelCallOrderSaving] = useState(false);\n  const [modelMigrationSaving, setModelMigrationSaving] = useState(false);\n','  const [modelPresetSelecting, setModelPresetSelecting] = useState(false);\n')
s=re.sub(r'  const \[modelCallOrder, setModelCallOrder\] = useState<string\[]>\(\n    \(\) => initialSettings\?\.model_call_order \?\? \[],\n  \);\n','',s)
for x in ('    modelCallOrder,\n','    modelCallOrderSaving,\n','    modelMigrationSaving,\n','    setModelCallOrder,\n','    setModelCallOrderSaving,\n','    setModelMigrationSaving,\n'):
    s=s.replace(x,'')
s=s.replace('    modelPresetPendingDelete,\n','    modelPresetPendingDelete,\n    modelPresetSelecting,\n').replace('    setModelPresetPendingDelete,\n','    setModelPresetPendingDelete,\n    setModelPresetSelecting,\n'); p.write_text(s)

p=Path('webui/src/components/settings/models/useModelSettingsActions.ts'); s=p.read_text()
s=s.replace('  migrateModelConfigurations,\n','').replace('  updateModelCallOrder,\n','')
s=s.replace('    modelCallOrder,\n    modelCallOrderSaving,\n','    modelPresetSelecting,\n').replace('    modelMigrationSaving,\n','')
s=s.replace('    setModelCallOrder,\n    setModelCallOrderSaving,\n','    setModelPresetSelecting,\n').replace('    setModelMigrationSaving,\n','')
s=s.replace('      modelCallOrderSaving ||\n','      modelPresetSelecting ||\n').replace('if (!settings || saving || modelCallOrderSaving || modelConfigurationSaving) return;','if (!settings || saving || modelPresetSelecting || modelConfigurationSaving) return;')
s=s.replace('      (preset) => !preset.is_default && preset.name === settings.model_call_order?.[0],','      (preset) => !preset.is_default && preset.active,')
s=s.replace('        const nextOrder = createdPreset ? [createdPreset] : null;\n        applyPayload(payload);','        applyPayload(payload);')
s=re.sub(r'\n        let finalPayload = payload;\n        if \(nextOrder\) \{.*?\n        \}', '\n        let finalPayload = payload;\n        if (createdPreset && payload.agent.model_preset !== createdPreset) {\n          finalPayload = await updateSettings(client, { modelPreset: createdPreset });\n          applyPayload(finalPayload);\n        }', s, flags=re.S)
s=re.sub(r'\n  const changeModelCallOrder = async \(nextOrder: string\[]\) => \{.*?\n  \};\n\n  const savePromptOverrides', '\n  const selectActiveModelPreset = async (name: string) => {\n    if (!settings || saving || modelPresetSelecting || modelConfigurationSaving) return;\n    if (settings.agent.model_preset === name) return;\n    setModelPresetSelecting(true);\n    try {\n      const payload = await updateSettings(client, { modelPreset: name });\n      applyPayload(payload, { preserveAgentForm: true });\n      onModelNameChange(payload.agent.model || null);\n      setError(null);\n    } catch (err) {\n      setError((err as Error).message);\n    } finally {\n      setModelPresetSelecting(false);\n    }\n  };\n\n  const savePromptOverrides', s, flags=re.S)
s=re.sub(r'\n  const handleMigrateModelConfigurations = async \(\) => \{.*?\n  \};\n','\n',s,flags=re.S)
s=s.replace('    changeModelCallOrder,\n','    selectActiveModelPreset,\n').replace('    handleMigrateModelConfigurations,\n','')
p.write_text(s)

p=Path('webui/src/components/settings/useSettingsController.ts'); s=p.read_text()
s=s.replace('editingProviderKeys, expandedProvider, form, modelCallOrder, modelCallOrderSaving,\n    imageAnalysisSaving, modelConfigurationSaving, modelMigrationSaving, modelPresetBeforeCreateRef,','editingProviderKeys, expandedProvider, form, modelPresetSelecting,\n    imageAnalysisSaving, modelConfigurationSaving, modelPresetBeforeCreateRef,')
s=s.replace('    setModelCallOrder, setModelPresetCreating,','    setModelPresetCreating,').replace('      setModelCallOrder(payload.model_call_order ?? []);\n','')
s=s.replace('    changeModelCallOrder,\n','    selectActiveModelPreset,\n').replace('    handleMigrateModelConfigurations,\n','')
s=s.replace('    modelCallOrder,\n    modelCallOrderSaving,\n','    modelPresetSelecting,\n').replace('    modelMigrationSaving,\n',''); p.write_text(s)

p=Path('webui/src/components/settings/SettingsPage.tsx'); s=p.read_text()
s=s.replace('    changeModelCallOrder,\n','    selectActiveModelPreset,\n').replace('    handleMigrateModelConfigurations,\n','').replace('    modelCallOrder,\n    modelCallOrderSaving,\n','    modelPresetSelecting,\n').replace('    modelMigrationSaving,\n','')
s=s.replace('              callOrder={modelCallOrder}\n','').replace('              orderSaving={modelCallOrderSaving || modelConfigurationSaving}\n','              selectionSaving={modelPresetSelecting || modelConfigurationSaving}\n').replace('              migrationSaving={modelMigrationSaving}\n','').replace('              onChangeCallOrder={changeModelCallOrder}\n','              onSelectActivePreset={selectActiveModelPreset}\n').replace('              onMigrate={handleMigrateModelConfigurations}\n',''); p.write_text(s)

p=Path('webui/src/components/settings/models/ModelsSettings.tsx'); s=p.read_text()
s=s.replace('  GripVertical,\n  ListOrdered,\n','')
s=s.replace('''function modelPresetValue(payload: SettingsPayload): string {\n  return (\n    payload.model_call_order?.[0] ??\n    payload.model_presets.find((preset) => !preset.is_default)?.name ??\n    ""\n  );\n}\n''','''function modelPresetValue(payload: SettingsPayload): string {\n  return (\n    payload.model_presets.find((preset) => preset.active && !preset.is_default)?.name ??\n    payload.model_presets.find((preset) => !preset.is_default)?.name ??\n    ""\n  );\n}\n''')
s=s.replace('  callOrder,\n','').replace('  orderSaving,\n','  selectionSaving,\n').replace('  migrationSaving,\n','').replace('  onChangeCallOrder,\n','  onSelectActivePreset,\n').replace('  onMigrate,\n','')
s=s.replace('  callOrder: string[];\n','').replace('  orderSaving: boolean;\n','  selectionSaving: boolean;\n').replace('  migrationSaving: boolean;\n','').replace('  onChangeCallOrder: (order: string[]) => void;\n','  onSelectActivePreset: (name: string) => void;\n').replace('  onMigrate: () => void;\n','')
s=re.sub(r'  const \[draggedCallOrderIndex.*?\n  const \[dragOverCallOrderIndex.*?\n','',s)
a=s.index('  const namedPresetsByName = new Map'); b=s.index('  const selectedPreset = ',a)
s=s[:a]+'''  const namedPresetsByName = new Map(namedPresets.map((preset) => [preset.name, preset]));\n  const presetRows = namedPresets.map((preset) => ({\n    key: `preset:${preset.name}`,\n    name: preset.name,\n    preset,\n  }));\n'''+s[b:]
s=re.sub(r'  const selectedPresetReferenced = Boolean\(\n    selectedPreset && callOrder\.includes\(selectedPreset\.name\),\n  \);\n  const callOrderBusy = orderSaving \|\| saving;','  const selectedPresetReferenced = selectedPreset?.active === true;\n  const selectionBusy = selectionSaving || saving;',s)
s=re.sub(r'\n  const moveCallOrderItem = .*?\n  const renderPresetEditor =','\n  const renderPresetEditor =',s,flags=re.S)
s=s.replace('selectedPresetReferenced || saving || orderSaving','selectedPresetReferenced || saving || selectionSaving')
start=s.index('        <SettingsGroup>\n          {!settings.model_call_order_editable')
end_marker='''        </SettingsGroup>\n      </section>\n      <section>\n        <SettingsSectionTitle>\n          {tx("settings.models.imageAnalysis.title", "Image analysis fallback")}'''
end=s.index(end_marker,start)
new='''        <SettingsGroup>\n          <div role="list" className="divide-y divide-border/45">\n            {presetRows.map(({ key, name, preset }) => {\n              const provider = modelPresetProviderKey(preset, settings);\n              const presetConfigured = settingsProviderConfigured(settings, preset.provider, preset.resolved_provider);\n              const isSelected = editorOpen && !creating && activeEditorRowKey === key && selectedPreset?.name === name;\n              return (\n                <div key={key} role="listitem">\n                  <div data-testid={`model-preset-row-${name}`} className={cn("group flex min-h-[76px] items-center gap-3 px-4 py-3 transition-colors sm:px-5", "hover:bg-muted/25", isSelected && "bg-muted/45 hover:bg-muted/45")}>\n                    <button type="button" aria-pressed={selectedPreset?.name === name} aria-expanded={isSelected} aria-controls={isSelected ? "model-preset-editor" : undefined} onClick={() => selectPreset(preset, key)} className="flex min-w-0 flex-1 items-center gap-3 rounded-control text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">\n                      <ProviderPickerIcon provider={provider} showBrandLogos={showBrandLogos} unconfigured={!presetConfigured} />\n                      <span className="min-w-0 flex-1"><span className="flex min-w-0 flex-wrap items-center gap-2"><span className="truncate text-[14px] font-medium text-foreground">{name}</span>{preset.active ? (<StatusPill tone="success">{tx("settings.models.active", "Active")}</StatusPill>) : null}{!presetConfigured ? (<span className="text-[11px] font-medium text-amber-700 dark:text-amber-300">{tx("settings.models.providerSetupRequired", "Provider setup required")}</span>) : null}</span><span className="mt-0.5 block truncate text-[12px] text-muted-foreground">{preset.model}</span></span>\n                      <ChevronRight className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", isSelected && "rotate-90")} aria-hidden />\n                    </button>\n                    <Button type="button" size="sm" variant={preset.active ? "secondary" : "outline"} className="shrink-0 rounded-full" disabled={selectionBusy || preset.active || !presetConfigured} onClick={() => onSelectActivePreset(preset.name)}>{preset.active ? tx("settings.models.active", "Active") : tx("settings.models.usePreset", "Use")}</Button>\n                  </div>\n                  {isSelected ? renderPresetEditor() : null}\n                </div>\n              );\n            })}\n          </div>\n          {!creating ? (<button type="button" className="flex min-h-[58px] w-full items-center justify-between gap-3 px-4 py-3 text-left outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 sm:px-5" disabled={selectionBusy} onClick={() => { setEditorRowKey(null); setEditorOpen(true); onBeginCreate(); }}><span className="inline-flex items-center text-[13px] font-medium"><Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />{tx("settings.models.newPreset", "New model preset")}</span>{selectionSaving ? (<SettingsStatusMessage><span className="inline-flex items-center gap-1.5"><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />{tx("settings.actions.saving", "Saving...")}</span></SettingsStatusMessage>) : null}</button>) : null}\n          {creating && editorOpen ? renderPresetEditor() : null}\n'''
s=s[:start]+new+s[end:]
p.write_text(s)

rw('webui/src/components/thread/ThreadShell.tsx', lambda s: s.replace('''  const order = new Map(\n    (settings.model_call_order ?? []).map((name, index) => [name.trim(), index]),\n  );\n  return settings.model_presets\n    .filter((preset) => !preset.is_default && preset.name.trim())\n    .sort((a, b) => (\n      (order.get(a.name.trim()) ?? Number.POSITIVE_INFINITY)\n      - (order.get(b.name.trim()) ?? Number.POSITIVE_INFINITY)\n    ))\n''','''  return settings.model_presets\n    .filter((preset) => !preset.is_default && preset.name.trim())\n    .sort((a, b) => Number(b.active) - Number(a.active))\n''').replace('''  useEffect(() => {\n    if (!chatId) {\n      setFallbackModelName(null);\n      return;\n    }\n    setFallbackModelName(null);\n    return client.onChat(chatId, (event) => {\n      if (event.event !== "turn_model_updated" || event.fallback !== true) return;\n      setFallbackModelName(event.model_name);\n    });\n  }, [chatId, client]);\n\n''','').replace('    setFallbackModelName(null);\n',''))


def remove_it_titles(path: str, titles: set[str]):
    p=Path(path); s=p.read_text(); starts=[]; off=0
    while True:
        i=s.find('  it("',off)
        if i<0: break
        starts.append(i); off=i+1
    targets=[]
    for title in titles:
        pos=s.find(f'  it("{title}"')
        if pos>=0: targets.append(pos)
    for pos in sorted(targets, reverse=True):
        nxt=next((x for x in starts if x>pos),None)
        end=nxt if nxt is not None else s.rfind('\n});')
        s=s[:pos]+s[end:]
    p.write_text(s)

remove_it_titles('webui/src/tests/settings-models.test.tsx', {'drags model presets to reorder and saves the model call order immediately','keeps repeated fallback preset rows stable when changing the primary preset','restores the model call order when immediate persistence fails','shows presets outside the call order in the unified list and adds them directly','appends a new model preset to the call order immediately','converts legacy model settings into presets before editing call order'})
remove_it_titles('webui/src/tests/api.test.ts', {'serializes model call order as an ordered JSON array'})
remove_it_titles('webui/src/tests/thread-shell.test.tsx', {'shows the effective fallback model in the composer badge'})

p=Path('webui/src/tests/api.test.ts'); s=p.read_text().replace('  migrateModelConfigurations,\n','').replace('  updateModelCallOrder,\n','').replace('it("serializes model preset deletion and migration"','it("serializes model preset deletion"').replace('    await migrateModelConfigurations(mutationTransport);\n\n','')
s=s.replace('''    expect(requestMutation).toHaveBeenNthCalledWith(\n      1,\n      "settings.model_configuration.delete",\n      { name: "spare" },\n      20_000,\n    );\n    expect(requestMutation).toHaveBeenNthCalledWith(\n      2,\n      "settings.model_configuration.migrate",\n      {},\n      20_000,\n    );''','''    expect(requestMutation).toHaveBeenCalledWith(\n      "settings.model_configuration.delete",\n      { name: "spare" },\n      20_000,\n    );'''); p.write_text(s)
for path in Path('webui/src/tests').glob('*.ts*'):
    s=path.read_text(); s=''.join(line for line in s.splitlines(True) if not any(k in line for k in ('model_call_order:', 'model_call_order_editable:', 'model_configuration_migratable:', '.model_call_order ='))); path.write_text(s)
rw('webui/src/tests/settings-models.test.tsx', lambda s: s.replace('model-call-order-row-', 'model-preset-row-'))

print('final fallback cleanup applied')
